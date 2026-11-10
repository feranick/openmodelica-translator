"""executor.py — where studies run: the local worker pool, or one SLURM job per study (kind: threads).
Version: 2026.11.09.2

From 2026.11.09.2 the SLURM layer is llmt-runtime's (llmt_runtime.slurm and llmt_runtime.jobs, configured by
runtime.RT): the settings, the machine (the cluster's nodes from `scontrol -o show node`, this node, pinning with
OMCT_SLURM_NODE), the node's memory, the time limit (OMCT_SLURM_TIME), job.sh and job.json, submission (with its sbatch
breaker and the adoption of a job a slow controller took), squeue/sacct and the states of jobs (ended jobs squeue still
lists, the queue position by priority, the lost-node diagnosis), the queue's CPUs, the cancel flags and scancel. The
names below stay as thin wrappers with omct's signatures (api, runner, cli and the tests call them). What stays omct's:
the sizing (cpus_for_job: the sweep points it can run at once, at least OMCT_COMPILE_PROCS on a build-cache miss, capped
at the node's CPUs; mem_for_job's auto: 1 GB + 256 MB per CPU; sweep_rounds: the rounds of a sweep's points, which set
its job's time limit; estimate_minutes, from the cache's measured runs), validation (format.run_errors and caps), the
progress of a running study (api._progress), the sweep pools, job.sh's PYTHONPATH line, the run-study entry point, and
writing a finished failure as the study's result (slurm_states). They reach the runtime as hooks on runtime.RT (procs,
mem_auto, solves, script_lines, job_extra: estimate_min and time_min, running_progress, oom_advice, timeout_hint).
2026.11.09.1 (phase 1): final_state and the runtime's version.

Implements llm-translators' docs/EXECUTION.md. Settings (environment):
    OMCT_EXECUTOR          local | slurm (local)
    OMCT_SLURM_MIN_MINUTES studies estimated below this run locally even with slurm (1)
    OMCT_SLURM_ARGS        extra sbatch arguments, split like a shell does
    OMCT_SLURM_MEM         auto | a size | none (auto: 1 GB + 256 MB per CPU)
    OMCT_SLURM_TIME        timeout (default) | estimate | max | minutes (llmt-runtime's meanings): timeout = the runs'
                           timeouts + 10 min, a sweep's rounds of points each with its timeout; estimate =
                           max(OMCT_SLURM_TIME_MIN 20, OMCT_SLURM_TIME_FACTOR 3 x the estimate + 10), at most timeout
                           (each run's own timeout then fits in it: job.json timeout_s); max = the partition's limit
    OMCT_PROCS             sweep points at once per study (1); a study's run.procs overrides it
    OMCT_COMPILE_PROCS     the C compile's parallelism (4): a job that must build asks for at least these CPUs
    OMCT_TIMEOUT           seconds per run (600)
    OMCT_SLURM_NODE        local | any | a node list (local): where jobs may run in a cluster of several nodes
                           (llm-translators docs/EXECUTION.md, "Clusters of several nodes"); a --nodelist / -w in
                           OMCT_SLURM_ARGS wins
A study's run.queue true (always SLURM) or false (always local) overrides the minimum, not OMCT_EXECUTOR=local.

One job per study with --ntasks=1 --cpus-per-task=N: N = the sweep points it can run at once (at most the
points, OMCT_PROCS or run.procs), and at least OMCT_COMPILE_PROCS when the model is not in the build cache yet.
Inside the job, SLURM_CPUS_PER_TASK sets the sweep's threads; each simulation stays single-threaded
(OMP_NUM_THREADS=1, measured on mochi). A sweep runs its first point alone (it builds the model), then the others N at
a time, each with the full timeout: its job's limit counts those rounds (until 2026.11.09.1 it was one timeout + 10
min, so SLURM could stop a big sweep before omct's own timeouts). Estimates before submitting: the build (0 on a cache
hit, else ~0.1 min), and every point from the cache's runs.json: a whole study's measured time on this build (its
checks and the reference included), else the simulation's seconds per output interval x 3 plus 2 s, else 2e-5 s per
interval plus 2 s. Measured on mochi: a 20000-interval PI-loop point costs ~1.5 s with its checks.

Every SLURM state is computed from the files and the queue (never from in-process handles):
    done           <name>.json exists
    queued         squeue: PENDING (reason, expected_start, ahead: by priority)
    running        squeue: RUNNING / COMPLETING (the study's progress, job_elapsed_min)
    timed_out, out_of_memory, cancelled, failed
                   gone from squeue without a result (or listed in a final state: MinJobAge): sacct, or job.log when
                   sacct knows nothing; these are written as the study's result ({"ok": false, "state", "why"}) so
                   they stay final
"""
from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from llmt_runtime import jobs as J
from llmt_runtime import slurm as S

from .format import caps, run_errors  # noqa: F401  (run_errors: validation, re-exported)
from .runtime import RT

CLI = "omct"
PKG_ROOT = str(Path(__file__).resolve().parents[1])
FINAL = J.FINAL            # ("timed_out", "out_of_memory", "cancelled", "failed")
S_PER_INTERVAL = 2e-5
SBATCH_TIMEOUT = 15.0      # omct's: sbatch gets this long (then the request's other studies fail at once for 30 s)


# ---- settings (llmt-runtime's) ----------------------------------------------------------------------------
def kind() -> str:
    return S.kind(RT)


def min_minutes() -> float:
    return S.min_minutes(RT)


def timeout_s(study: dict | None = None) -> float:
    """Seconds per run: the study's run.timeout_s, else OMCT_TIMEOUT (600)."""
    return S.timeout_s(RT, study)


def procs_of(study: dict) -> int:
    """Sweep points at once asked for: run.procs, else OMCT_PROCS (before the size caps)."""
    return S.procs_of(RT, study)


def queue_of(study: dict):
    """run.queue: True (always SLURM), False (always local), None (by the estimate)."""
    return (study.get("run") or {}).get("queue")


def compile_procs() -> int:
    try:
        return max(1, int(float(os.environ.get("OMCT_COMPILE_PROCS", "4") or 4)))
    except ValueError:
        return 4


def time_limit_min(study: dict | None = None, est_min: float | None = None) -> int:
    """The job's limit in minutes. Without est_min: what the study's runs may take (llmt-runtime's time_need_min with
    omct's solves: ceil(rounds x timeout / 60) + 10, one round for a study without a sweep). With est_min: the limit
    OMCT_SLURM_TIME gives (llmt-runtime's job_time; 'estimate': max(OMCT_SLURM_TIME_MIN, 3 x est_min + 10), at most
    the former); the former when that is UNLIMITED."""
    if est_min is None:
        return S.time_need_min(RT, study)
    t, _ = S.job_time(RT, study, est_min)
    return int(t) if t.isdigit() else S.time_need_min(RT, study)


# ---- the sizing of one job (omct's; the runtime's hooks) ----------------------------------------------------
_CTX = threading.local()


@contextmanager
def _sizing(**keys):
    """What one submission (or one estimate) already knows (solver, cpus, est), for the hooks the runtime calls during
    it: the CPUs are sized once and the hooks agree with them."""
    prev = getattr(_CTX, "v", None)
    _CTX.v = {**(prev or {}), **{k: v for k, v in keys.items() if v is not None}}
    try:
        yield
    finally:
        _CTX.v = prev


def _ctx(key: str):
    return (getattr(_CTX, "v", None) or {}).get(key)


def _solver_now() -> str:
    """The solver of the submission under way, else OMCT_SOLVER (omc unless 'ref')."""
    s = str(_ctx("solver") or os.environ.get("OMCT_SOLVER", "")).strip().lower()
    return s if s in ("omc", "ref") else "omc"


def _points(study: dict) -> int:
    if not study.get("sweep"):
        return 1
    from .format import sweep_points
    try:
        return max(1, len(sweep_points(study["sweep"])))
    except Exception:  # noqa: BLE001
        return 1


def _cache_dir(study: dict) -> Path | None:
    """The build cache folder of the study's model (existing or not), or None when it cannot be told."""
    try:
        from . import modelica as M
        from .omc import cache_root, structure_key
        st = dict(study)
        st.pop("sweep", None)
        tr = M.escape_translation(st) if "modelica" in st else M.translate(st, "estimate")
        return cache_root() / structure_key(tr)
    except Exception:  # noqa: BLE001
        return None


def cache_hit(study: dict) -> bool:
    d = _cache_dir(study)
    return bool(d and (d / "meta.json").is_file())


def _intervals(study: dict) -> int:
    an = study.get("analysis") or {}
    return 1 if an.get("kind") == "linearize" else int(an.get("intervals", 500))


AC_TRANSIENT = {"induction_machine", "pmsm", "pmsm_damper"}


def estimate_minutes(study: dict, solver: str = "omc") -> float:
    """An upper estimate of the study's time: the build, then every point (convergence runs twice)."""
    if solver == "ref":
        return 0.05 * _points(study)
    d = _cache_dir(study)
    hit = bool(d and (d / "meta.json").is_file())
    rec = _runs(d) if d else {}
    per = max(float(rec["s_per_interval"]) * 3, 1e-7) if "s_per_interval" in rec else S_PER_INTERVAL
    runs = _points(study) * (1 + bool(study.get("converge")) + ("modelica" in study and study.get("cross_check", True)))
    if "study_s" in rec:                                 # measured: a whole study on this build, with its checks
        one = 1.5 * float(rec["study_s"]) * (_intervals(study) / max(int(rec.get("intervals") or 1), 1)) ** 0.5
    else:
        one = per * _intervals(study) + 2.0 + 1e-4 * _intervals(study)   # start-up, reading, checks, reference
        kinds = {str(c.get("kind")) for c in (study.get("components") or {}).values() if isinstance(c, dict)}
        if kinds & AC_TRANSIENT:
            one += 0.01 * _intervals(study)       # the nonlinear reference of a transient AC machine (12-40 s at 5000)
    secs = (0 if hit else 6.0) + runs * one / max(1, min(procs_of(study), _points(study)))
    return secs / 60.0


def cpus_for_job(study: dict, solver: str = "omc") -> int:
    """The CPUs a study's job asks for: the sweep points it can run at once (run.procs or OMCT_PROCS, at most the
    points), at least OMCT_COMPILE_PROCS when the model must be built, within OMCT_MAX_PROCS and the node's CPUs."""
    n = min(procs_of(study), _points(study))
    if solver != "ref" and not cache_hit(study):
        n = max(n, compile_procs())
    cap = caps().get("procs")
    try:
        n = min(n, int(float(cap))) if cap else n
    except ValueError:
        pass
    nc = node_cpus() if kind() == "slurm" else None     # a job cannot have more CPUs than its node
    return max(1, min(n, nc) if nc else n)


def job_cpus(study: dict) -> int:
    """The runtime's hooks.procs: the CPUs of the submission under way, else cpus_for_job with OMCT_SOLVER."""
    c = _ctx("cpus")
    return int(c) if c else cpus_for_job(study, _solver_now())


def mem_auto(study: dict, cpus: int | None = None) -> str:
    """OMCT_SLURM_MEM=auto (the runtime's hooks.mem_auto): 1 GB + 256 MB per CPU of the job."""
    return f"{1024 + 256 * int(cpus if cpus is not None else job_cpus(study))}M"


def sweep_rounds(study: dict, cpus: int | None = None) -> int:
    """Runs one after the other in a study's job, each with the full timeout (the runtime's hooks.solves, so the job's
    limit is ceil(rounds x timeout / 60) + 10 minutes): 1 without a sweep; a sweep's first point alone (it builds the
    model), then the others in rounds of the job's CPUs (run-study gives the sweep min(CPUs, points) at once)."""
    p = _points(study)
    if p <= 1:
        return 1
    n = cpus if cpus is not None else job_cpus(study)
    w = max(1, min(int(n), p - 1))
    return 1 + math.ceil((p - 1) / w)


def script_lines(request: Path, name: str, study: dict) -> list[str]:
    """job.sh's lines before its exec (the runtime's hooks.script_lines): the package's folder first on PYTHONPATH,
    so the job finds this translator even when it is not installed in the venv (mochi, 2026.10.21.8)."""
    return [f"export PYTHONPATH={shlex.quote(PKG_ROOT)}${{PYTHONPATH:+:$PYTHONPATH}}"]


def job_extra(study: dict) -> dict:
    """omct's keys in job.json (the runtime's hooks.job_extra): estimate_min (to 0.001 min) and time_min, the job's
    limit in minutes as omct recorded it (the runtime's time_min is what the runs may take; with
    OMCT_SLURM_TIME=estimate the limit is shorter, and a queued study's eta_min_max shows it)."""
    est = _ctx("est")
    if est is None:
        est = estimate_minutes(study, _solver_now())
    t, _ = S.job_time(RT, study, est)
    return {"estimate_min": round(float(est), 3),
            "time_min": int(t) if t.isdigit() else S.time_need_min(RT, study)}


def mem_for_job(study: dict, cpus: int) -> str | None:
    """The --mem in a study's job.sh (llmt-runtime's mem_plan): run.mem; nothing when OMCT_SLURM_ARGS has a --mem or
    --mem-per-cpu (sbatch takes it from there); else OMCT_SLURM_MEM none | a size | auto (1 GB + 256 MB per CPU, at
    most the node's memory). Sizes as sbatch takes them ('8GB' and '1.5G' in MB)."""
    with _sizing(cpus=cpus):
        return S.mem_for(RT, study)


def mem_mb_for_job(study: dict, cpus: int) -> int:
    """The memory a job reserves, in MB: the study's run.mem, else --mem from OMCT_SLURM_ARGS (--mem-per-cpu times
    the CPUs), else OMCT_SLURM_MEM (auto: 1024 + 256 per CPU, as submit asks for)."""
    m = mem_for_job(study, cpus)
    per_cpu = False
    if m is None:
        args = S.slurm_args(RT)
        a = S.opt_value(args, ("--mem",))
        if a:
            m = a
        else:
            a = S.opt_value(args, ("--mem-per-cpu",))
            if a:
                m, per_cpu = a, True
    v = S.mem_mb(m) if m else None
    if v and per_cpu:
        v *= cpus
    return int(math.ceil(v)) if v else 1024 + 256 * cpus


def estimate(study: dict, solver: str = "omc") -> dict:
    """{minutes, procs, mem_mb}: what the job would ask for (llm-translators' router places runs with it;
    docs/EXECUTION.md "Estimates in validation"). The same functions size SLURM jobs: procs after the size cap
    (compile threads on a cache miss), memory as for --mem, minutes the upper estimate of the run time."""
    cpus = cpus_for_job(study, solver)
    return {"minutes": round(estimate_minutes(study, solver), 3), "procs": cpus, "mem_mb": mem_mb_for_job(study, cpus)}


def use_slurm(study: dict, solver: str) -> bool:
    """Whether a study becomes a SLURM job: only with OMCT_EXECUTOR=slurm; then run.queue decides if given, else the
    estimate against OMCT_SLURM_MIN_MINUTES (llmt-runtime's use_slurm; the estimate is made only when it decides)."""
    if kind() != "slurm":
        return False
    if queue_of(study) is not None:
        return bool(queue_of(study))
    return S.use_slurm(RT, study, estimate_minutes(study, solver))


# ---- clusters of several nodes (llmt-runtime's; llm-translators docs/EXECUTION.md) ---------------------------
_JOB_NODE: dict = {}


def forget() -> None:
    """Drops what llmt-runtime learnt about the cluster (its nodes, the partition's limit)."""
    S.forget()


def _node_rec(n: dict) -> dict:
    mem = S.node_real_mb(n)
    return {"name": n.get("NodeName") or "", "host": n.get("NodeHostName") or n.get("NodeName") or "",
            "addr": n.get("NodeAddr") or "", "cpus": S.node_cpus(n) or None, "mem_mb": int(mem) if mem else None}


def slurm_nodes(all_nodes: bool = False) -> list[dict]:
    """The SLURM nodes this machine's jobs may go to (all of the cluster's with all_nodes): [{name, host, addr, cpus,
    mem_mb}] from llmt-runtime's `scontrol -o show node`; [] without SLURM or when it does not answer."""
    if kind() != "slurm":
        return []
    nodes = S.cluster_nodes() or []
    if nodes and not all_nodes:
        nodes = S.job_nodes(RT)
    return [_node_rec(n) for n in nodes]


def _cluster() -> list[dict]:
    """The cluster's nodes (slurm_nodes(all_nodes=True))."""
    return slurm_nodes(all_nodes=True)


def node_mem_mb() -> float | None:
    """The most memory one job can ask for (MB): the largest RealMemory among the nodes this machine's jobs go to
    (this node with OMCT_SLURM_NODE=local), or None without SLURM or when it is unknown (llmt-runtime's). A larger
    --mem fails only at submission ('Memory specification can not be satisfied': cfdt on mochi, 90G)."""
    return S.node_mem_mb(RT)


def node_cpus() -> int | None:
    """The most CPUs one job can get: the largest allowed node's (this node's with local), not the cluster's."""
    c = [n["cpus"] for n in slurm_nodes() if n.get("cpus")]
    return max(c) if c else None


def _my_addresses() -> set:
    """This machine's addresses (llmt-runtime's: `hostname -I`, the host name's own)."""
    return S.addresses()


def local_node() -> str | None:
    """This machine's node in the SLURM cluster (llmt-runtime's this_node: NodeName or NodeHostName equal to the host
    name, else NodeAddr among this machine's addresses: the sparks use their Tailscale addresses)."""
    if kind() != "slurm":
        return None
    me = S.this_node(S.cluster_nodes())
    return me.get("NodeName") if me else None


def _args_nodelist() -> bool:
    return S.args_nodelist(RT) is not None


def node_setting() -> str:
    """OMCT_SLURM_NODE: local (default) | any | a node list."""
    return S.node_setting(RT)


def node_list() -> str | None:
    """The --nodelist omct adds (llmt-runtime's node_pin): with OMCT_SLURM_NODE=local (the default) this machine's
    node when the cluster has several (the sparks: spark1 is spark-5da5, spark2 spark-0808; a job sent to the other
    node cannot see its run folder unless the home folders are shared); 'any': none (SLURM chooses); a node list:
    those. A --nodelist / -w in OMCT_SLURM_ARGS wins (then omct adds none). One-node clusters: none."""
    return S.node_pin(RT)[0]


def status() -> dict:
    """The 'executor' key of tool_status (read by llmt status and the router): omct's keys; node.warning when this
    machine is none of the cluster's nodes."""
    base = {"procs": procs_of({}), "timeout_s": timeout_s(), "caps": caps(), "compile_procs": compile_procs(),
            "runtime": RT.runtime_version}      # llmt-runtime's version (machines() and llmt status show it)
    if kind() == "slurm":
        _, _, info = S.node_pin(RT)
        node = {"setting": node_setting(), "nodelist": node_list(), "cluster_nodes": len(S.cluster_nodes() or [])}
        if info.get("warning"):
            node["warning"] = info["warning"]
        return {"kind": "slurm", "sbatch": shutil.which("sbatch"), "min_minutes": min_minutes(),
                "time_limit_min": time_limit_min(), "time": S.time_setting(RT),
                "mem": RT.env("SLURM_MEM", "auto"), "args": RT.env("SLURM_ARGS", ""),
                "nodes": [{k: n[k] for k in ("name", "cpus")} for n in slurm_nodes()], "node": node, **base}
    return {"kind": "local", "workers": int(os.environ.get("OMCT_WORKERS", "2")), **base}


# ---- submitting (llmt-runtime's) ---------------------------------------------------------------------------
write_atomic = J.write_atomic      # a file written whole or not at all (a temporary file, then rename)


def sbatch_args(study: dict, mem: str | None = None) -> list[str]:
    """OMCT_SLURM_ARGS for this study's sbatch, split as a shell does (unbalanced quotes: the plain words); with a
    study's run.mem, its --mem/--mem-per-cpu are dropped (llmt-runtime's sbatch_args). From 2026.11.09.2 the job's
    --mem is in job.sh (mem_for_job), no longer appended here: `sbatch job.sh` by hand lost it. (mem: unused, kept
    for the signature.)"""
    return S.sbatch_args(RT, study)


def job_script(request: Path, name: str, cpus: int, tmin, study: dict | None = None, mem: str | None = None) -> str:
    """job.sh (llmt-runtime's job_script): the job's name omct:<request>:<name>, folder, log, --ntasks=1
    --cpus-per-task=N, --time, --mem (mem, else the study's mem_for_job; in job.sh from 2026.11.09.2), --nodelist
    (node_list), --export=ALL, the PYTHONPATH line, then `python -m openmodelica_translator.cli run-study REQUEST NAME`."""
    if mem is None and study is not None:
        mem = mem_for_job(study, cpus)
    return J.job_script(RT, Path(request), name, study or {}, int(cpus), str(tmin), mem, node_list())


def submit(request: Path, name: str, study: dict, solver: str = "omc") -> dict:
    """Writes <request>/<name>/job.sh, submits it, writes job.json (llmt-runtime's submit, with omct's hooks). On
    failure writes the study's result {"ok": false, "why": "could not submit: ..."} (never falls back to local).
    sbatch gets SBATCH_TIMEOUT s (and the look-up of a job a slow controller took, the runtime's FIND_JOB_S); one
    that does not answer fails the request's other studies at once for 30 s (the runtime's breaker)."""
    request = Path(request)
    cpus = cpus_for_job(study, solver)
    est = estimate_minutes(study, solver)
    with _sizing(solver=solver, cpus=cpus, est=est), S.deadline(SBATCH_TIMEOUT + J.FIND_JOB_S + 1):
        info = J.submit(RT, request, name, study, estimate_min=est)
    if info.get("job_id"):
        return info
    return J.failed_submission(RT, request, name, info, reliable=False, unreliable_because=["could not submit"])


def run_study_job(request: str, name: str) -> int:
    """`omct run-study REQUEST NAME`: what a SLURM job (or a person) runs. Exit 0 when a result was written. Each
    run's timeout is the one its job was sized with (job.json timeout_s: shorter with OMCT_SLURM_TIME=estimate)."""
    from .runner import run_study

    req = Path(request).expanduser().resolve()
    meta = json.loads((req / "request.json").read_text())
    study = json.loads(json.dumps(meta["studies"][name]))
    n = os.environ.get("SLURM_CPUS_PER_TASK")
    cpus = int(n) if n and n.isdigit() else procs_of(study)
    if study.get("sweep"):
        study.setdefault("run", {})["procs"] = max(1, min(cpus, _points(study)))
    if not os.environ.get("SLURM_JOB_ID"):
        os.environ["OMCT_EXECUTION"] = "by hand"
    os.environ["OMCT_PROCS_REQUESTED"] = str(cpus)
    run_study(name, study, req / name, meta.get("solver", "omc"), J.job_timeout_s(req / name, timeout_s(study)))
    return 0


# ---- the queue (llmt-runtime's) ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 10):
    """(returncode, stdout, stderr) of a SLURM (or system) command, through llmt-runtime's slurm.run (within an MCP
    call's deadline when one is set); (1, '', why) when the command is missing or does not answer."""
    p = S.run(cmd, timeout)
    if p is None:
        return 1, "", f"{cmd[0]}: not found or no answer"
    return p.returncode, p.stdout or "", p.stderr or ""


_minutes = S.elapsed_minutes   # squeue's elapsed time ([D-]HH:MM:SS, MM:SS) in minutes


def squeue(ids: list[str]) -> dict | None:
    """One squeue call for these job ids: {id: {state, reason, start, elapsed_min, node}}; None when SLURM does not
    answer. Ended jobs squeue still lists (MinJobAge) are among them, in their final state."""
    return J.squeue([str(i) for i in ids])


def pending_ahead() -> dict:
    """{job id: pending jobs SLURM considers before it} for every pending job: higher priority, or equal and submitted
    earlier (llmt-runtime's pending_jobs / ahead_of)."""
    pend = J.pending_jobs()
    if not pend:
        return {}
    return {str(i): J.ahead_of(str(i), pend) for _, i in pend}


def sacct(ids: list[str]) -> dict | None:
    """{id: state} from accounting, or None when sacct fails or knows nothing (llmt-runtime's sacct_rows; the node
    each job ran on is kept in _JOB_NODE)."""
    if not ids:
        return {}
    rows = J.sacct_rows([str(i) for i in ids])
    for i, r in rows.items():
        if r.get("node"):
            _JOB_NODE[i] = r["node"]
    return {i: r["state"] for i, r in rows.items()} or None


def _tail(path: Path, n: int = 30) -> str:
    return J.tail(path, n) if Path(path).is_file() else "(no job.log)"


def final_state(acct: str | None, log: str, folder: Path | None = None) -> tuple[str, str]:
    """(state, why) of a job that left the queue without a result, from sacct's state and the end of job.log ('(no
    job.log)' when there is none): llmt-runtime's rules (jobs.classify_end), with omct's out-of-memory advice and
    time-limit hint. SLURM's out-of-memory wording 'Detected N oom_kill events' counts whatever sacct says (before
    2026.11.09.1 only 'oom-kill', and only without accounting: such jobs came back as 'failed')."""
    has_log = log != "(no job.log)"
    job = J.read_job(Path(folder)) if folder is not None else {}
    return J.classify_end(RT, acct, log if has_log else "", has_log=has_log, folder=folder, job=job)


def slurm_states(request: Path, names: list[str], studies: dict | None = None, write: bool = True) -> dict:
    """{name: {"state", ...}} for the request's studies that have job.json and no result: llmt-runtime's job_states
    (one squeue call; ahead by priority; sacct, else job.log, for jobs that left the queue or that squeue lists in a
    final state; a job lost on another node), in omct's shapes: queued with eta_min_max (the job's limit), running with
    the study's progress. Finished failures become the study's result file (when write)."""
    from .api import _progress

    request = Path(request)
    jobs = {}
    for n in names:
        if (request / n / "job.json").is_file() and not (request / n / f"{n}.json").is_file():
            j = J.read_job(request / n)
            if j.get("job_id"):
                jobs[n] = j
    if not jobs:
        return {}
    states = J.job_states(RT, request, list(jobs))
    out = {}
    for n, j in jobs.items():
        s = states.get(n) or {}
        jid = str(j["job_id"])
        base = {"job_id": jid, "procs": j.get("procs"), "estimate_min": j.get("estimate_min")}
        state = s.get("state")
        if state == "done" or (request / n / f"{n}.json").is_file():       # finished between the calls
            continue
        if state == "unknown":
            out[n] = {"state": "unknown", **base, "why": s.get("why")}
            continue
        if state == "queued":
            st = {"state": "queued", **base, "reason": s.get("reason"), "ahead": s.get("ahead"),
                  "eta_min_max": round(float(j.get("time_min") or 0), 1)}
            if s.get("expected_start"):
                st["expected_start"] = s["expected_start"]
            out[n] = st
            continue
        if state == "running":
            prog = _progress(request / n, float(j.get("timeout_s") or timeout_s((studies or {}).get(n))))
            st = {**prog, "state": "running", **base, "job_elapsed_min": s.get("job_elapsed_min")}
            if s.get("node"):
                st["node"] = s["node"]
            out[n] = st
            continue
        why = s.get("why") or "the job left the queue without a result"
        if state not in FINAL:
            state = "failed"
        res = {"name": n, "ok": False, "state": state, "why": why, "job_id": jid, "reliable": False,
               "unreliable_because": [state], "execution": {"executor": "slurm", "slurm_job": jid}}
        if write:
            write_atomic(request / n / f"{n}.json", json.dumps(res, indent=1))
        out[n] = res
    return out


# ---- jobs and cancel ---------------------------------------------------------------------------
def queue_summary() -> dict:
    """{queue: {running, queued, cpus_in_use}, machine: {cpus, cpus_allocated, cpus_idle}}: llmt-runtime's (the
    machine's CPUs from `sinfo -N`, each node once), with 'queue' counting this user's omct jobs as omct did (ended jobs
    squeue still lists are not in the queue)."""
    base = J.queue_summary(RT)
    user = os.environ.get("USER", "")
    if user:
        rc, out, _ = _run(["squeue", "-h", "-u", user, "-o", "%i|%j|%T|%C"])
        if rc == 0:
            q = {"running": 0, "queued": 0, "cpus_in_use": 0}
            for line in out.splitlines():
                f = (line.split("|") + [""] * 4)[:4]
                if not f[1].startswith(f"{CLI}:"):
                    continue
                if f[2] in J.ACTIVE:
                    q["running"] += 1
                    q["cpus_in_use"] += int(f[3]) if f[3].isdigit() else 0
                elif f[2] in J.QUEUED:
                    q["queued"] += 1
            base["queue"] = q
    return base


def cancel_slurm(request: Path, names: list[str]) -> list[str]:
    """scancel for the SLURM jobs of these studies that have no result yet; returns their ids."""
    request = Path(request)
    ids = []
    for n in names:
        if (request / n / "job.json").is_file() and not (request / n / f"{n}.json").is_file():
            jid = J.read_job(request / n).get("job_id")
            if jid:
                ids.append(str(jid))
    if ids:
        J.scancel(ids)
    return ids


def _runs(cache_dir: Path) -> dict:
    try:
        return json.loads((Path(cache_dir) / "runs.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def record_run(cache_dir: Path, seconds: float, intervals: int) -> None:
    """The cache's measured seconds per output interval of the simulation (for the next estimate)."""
    try:
        r = _runs(cache_dir)
        r.update({"s_per_interval": seconds / max(intervals, 1), "seconds": seconds, "intervals": intervals,
                  "when": time.time()})
        write_atomic(Path(cache_dir) / "runs.json", json.dumps(r))
    except OSError:
        pass


def record_study(key: str, seconds: float) -> None:
    """A whole study's time on this build (simulation, reading, checks, reference): what a sweep point costs."""
    from .omc import cache_root
    d = cache_root() / key
    if not d.is_dir():
        return
    try:
        r = _runs(d)
        r.update({"study_s": seconds, "study_when": time.time()})
        write_atomic(d / "runs.json", json.dumps(r))
    except OSError:
        pass
