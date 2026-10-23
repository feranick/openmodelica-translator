"""executor.py — where studies run: the local worker pool, or one SLURM job per study (kind: threads).
Version: 2026.10.22.1

Implements llm-translators' docs/EXECUTION.md (from cfd-translator's executor). Settings (environment):
    OMCT_EXECUTOR          local | slurm (local)
    OMCT_SLURM_MIN_MINUTES studies estimated below this run locally even with slurm (1)
    OMCT_SLURM_ARGS        extra sbatch arguments, split like a shell does
    OMCT_SLURM_MEM         auto | a size | none (auto: 1 GB + 256 MB per CPU)
    OMCT_SLURM_TIME        timeout | estimate (timeout); OMCT_SLURM_TIME_MIN (20), OMCT_SLURM_TIME_FACTOR (3)
    OMCT_PROCS             sweep points at once per study (1); a study's run.procs overrides it
    OMCT_COMPILE_PROCS     the C compile's parallelism (4): a job that must build asks for at least these CPUs
    OMCT_TIMEOUT           seconds per study; the job's time limit is ceil(timeout / 60) + 10 minutes
A study's run.queue true (always SLURM) or false (always local) overrides the minimum, not OMCT_EXECUTOR=local.

One job per study with --ntasks=1 --cpus-per-task=N: N = the sweep points it can run at once (at most the
points, OMCT_PROCS or run.procs), and at least OMCT_COMPILE_PROCS when the model is not in the build cache yet.
Inside the job, SLURM_CPUS_PER_TASK sets the sweep's threads; each simulation stays single-threaded
(OMP_NUM_THREADS=1, measured on mochi). Estimates before submitting: the build (0 on a cache hit, else ~0.1 min),
and every point from the cache's runs.json: a whole study's measured time on this build (its checks and the
reference included), else the simulation's seconds per output interval x 3 plus 2 s, else 2e-5 s per interval
plus 2 s. Measured on mochi: a 20000-interval PI-loop point costs ~1.5 s with its checks.

Every SLURM state is computed from the files and the queue (never from in-process handles):
    done           <name>.json exists
    queued         squeue: PENDING (reason, expected_start, ahead)
    running        squeue: RUNNING / COMPLETING (the study's progress, job_elapsed_min)
    timed_out, out_of_memory, cancelled, failed
                   gone from squeue without a result: sacct, or job.log when sacct knows nothing; these are
                   written as the study's result ({"ok": false, "state", "why"}) so they stay final
"""
from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .format import caps, run_errors  # noqa: F401  (run_errors: validation, re-exported)

CLI = "omct"
PKG_ROOT = str(Path(__file__).resolve().parents[1])
FINAL = ("timed_out", "out_of_memory", "cancelled", "failed")
S_PER_INTERVAL = 2e-5


# ---- settings ----------------------------------------------------------------------------------
def kind() -> str:
    k = os.environ.get("OMCT_EXECUTOR", "local").strip().lower() or "local"
    return k if k in ("local", "slurm") else "local"


def _float(env, default):
    try:
        return float(os.environ.get(env, default))
    except ValueError:
        return float(default)


def min_minutes() -> float:
    return _float("OMCT_SLURM_MIN_MINUTES", "1")


def timeout_s(study: dict | None = None) -> float:
    t = ((study or {}).get("run") or {}).get("timeout_s")
    return float(t) if t else _float("OMCT_TIMEOUT", "600")


def time_limit_min(study: dict | None = None, est_min: float | None = None) -> int:
    full = int(math.ceil(timeout_s(study) / 60.0)) + 10
    if os.environ.get("OMCT_SLURM_TIME", "timeout").strip().lower() == "estimate" and est_min is not None:
        t = max(_float("OMCT_SLURM_TIME_MIN", "20"), math.ceil(_float("OMCT_SLURM_TIME_FACTOR", "3") * est_min) + 10)
        return int(min(t, full))
    return full


def procs_of(study: dict) -> int:
    p = (study.get("run") or {}).get("procs")
    return int(p) if p else max(1, int(os.environ.get("OMCT_PROCS", "1") or 1))


def queue_of(study: dict):
    return (study.get("run") or {}).get("queue")


# ---- sizing --------------------------------------------------------------------------------------
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
    runs = _points(study) * (2 if study.get("converge") else 1)
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
    n = min(procs_of(study), _points(study))
    if solver != "ref" and not cache_hit(study):
        n = max(n, int(os.environ.get("OMCT_COMPILE_PROCS", "4") or 4))
    cap = caps().get("procs")
    return max(1, min(n, int(cap)) if cap else n)


def mem_for_job(study: dict, cpus: int) -> str | None:
    m = (study.get("run") or {}).get("mem")
    if m:
        return m
    s = os.environ.get("OMCT_SLURM_MEM", "auto").strip().lower()
    if s == "none" or any(a.startswith("--mem") for a in shlex.split(os.environ.get("OMCT_SLURM_ARGS", ""))):
        return None
    if s == "auto":
        return f"{1024 + 256 * cpus}M"
    return os.environ["OMCT_SLURM_MEM"].strip()


def mem_mb_for_job(study: dict, cpus: int) -> int:
    """The memory a job reserves, in MB: the study's run.mem, else --mem from OMCT_SLURM_ARGS, else
    OMCT_SLURM_MEM (auto: 1024 + 256 per CPU, as submit asks for)."""
    from .format import mem_mb
    m = mem_for_job(study, cpus)
    if m is None:
        for a in shlex.split(os.environ.get("OMCT_SLURM_ARGS", "")):
            if a.startswith("--mem="):
                m = a.split("=", 1)[1]
    v = mem_mb(m) if m else None
    return int(math.ceil(v)) if v else 1024 + 256 * cpus


def estimate(study: dict, solver: str = "omc") -> dict:
    """{minutes, procs, mem_mb}: what the job would ask for (llm-translators' router places runs with it;
    docs/EXECUTION.md "Estimates in validation"). The same functions size SLURM jobs: procs after the size cap
    (compile threads on a cache miss), memory as for --mem, minutes the upper estimate of the run time."""
    cpus = cpus_for_job(study, solver)
    return {"minutes": round(estimate_minutes(study, solver), 3), "procs": cpus, "mem_mb": mem_mb_for_job(study, cpus)}


def use_slurm(study: dict, solver: str) -> bool:
    if kind() != "slurm":
        return False
    q = queue_of(study)
    if q is True:
        return True
    if q is False:
        return False
    return estimate_minutes(study, solver) >= min_minutes()


def status() -> dict:
    """The 'executor' key of tool_status (read by llmt status)."""
    base = {"procs": procs_of({}), "timeout_s": timeout_s(), "caps": caps(),
            "compile_procs": int(os.environ.get("OMCT_COMPILE_PROCS", "4") or 4)}
    if kind() == "slurm":
        return {"kind": "slurm", "sbatch": shutil.which("sbatch"), "min_minutes": min_minutes(),
                "time_limit_min": time_limit_min(), "time": os.environ.get("OMCT_SLURM_TIME", "timeout"),
                "mem": os.environ.get("OMCT_SLURM_MEM", "auto"), "args": os.environ.get("OMCT_SLURM_ARGS", ""), **base}
    return {"kind": "local", "workers": int(os.environ.get("OMCT_WORKERS", "2")), **base}


# ---- submitting --------------------------------------------------------------------------------
def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def sbatch_args(study: dict, mem: str | None) -> list[str]:
    args = shlex.split(os.environ.get("OMCT_SLURM_ARGS", ""))
    if (study.get("run") or {}).get("mem"):
        out, skip = [], False
        for a in args:
            if skip:
                skip = False
                continue
            if a in ("--mem", "--mem-per-cpu"):
                skip = True
                continue
            if a.startswith(("--mem=", "--mem-per-cpu=")):
                continue
            out.append(a)
        args = out
    return args + ([f"--mem={mem}"] if mem else [])


def job_script(request: Path, name: str, cpus: int, tmin: int) -> str:
    d = request / name
    return "\n".join([
        "#!/bin/bash",
        f"#SBATCH --job-name={CLI}:{request.name}:{name}",
        f"#SBATCH --chdir={d}",
        f"#SBATCH --output={d / 'job.log'}",
        "#SBATCH --ntasks=1",
        f"#SBATCH --cpus-per-task={cpus}",
        f"#SBATCH --time={tmin}",
        "#SBATCH --export=ALL",
        # the package's folder first: the job finds this translator even when it is not installed in the venv
        f"export PYTHONPATH={shlex.quote(PKG_ROOT)}${{PYTHONPATH:+:$PYTHONPATH}}",
        f"exec {shlex.quote(sys.executable)} -m openmodelica_translator.cli run-study {shlex.quote(str(request))} "
        f"{shlex.quote(name)}",
        ""])


def submit(request: Path, name: str, study: dict, solver: str = "omc") -> dict:
    """Writes <request>/<name>/job.sh, submits it, writes job.json. On failure writes the study's result
    {"ok": false, "why": "could not submit: ..."} (never falls back to local)."""
    d = request / name
    d.mkdir(parents=True, exist_ok=True)
    cpus = cpus_for_job(study, solver)
    est = estimate_minutes(study, solver)
    tmin = time_limit_min(study, est)
    mem = mem_for_job(study, cpus)
    script = d / "job.sh"
    script.write_text(job_script(request, name, cpus, tmin))
    script.chmod(0o755)
    cmd = ["sbatch", "--parsable"] + sbatch_args(study, mem) + [str(script)]
    try:
        r = subprocess.run(cmd, cwd=str(d), capture_output=True, text=True, timeout=60)
        ok, msg = r.returncode == 0 and r.stdout.strip(), (r.stderr or r.stdout).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok, msg = False, str(exc)
    if not ok:
        res = {"name": name, "ok": False, "state": "failed", "why": f"could not submit: {msg[-500:] or 'sbatch failed'}",
               "reliable": False, "unreliable_because": ["could not submit"],
               "execution": {"executor": "slurm", "slurm_job": None}}
        write_atomic(d / f"{name}.json", json.dumps(res, indent=1))
        return res
    job_id = r.stdout.strip().splitlines()[-1].split(";")[0].strip()
    info = {"executor": "slurm", "job_id": job_id, "submitted": time.time(), "procs": cpus, "time_min": tmin,
            "time_from": os.environ.get("OMCT_SLURM_TIME", "timeout"), "timeout_s": timeout_s(study), "mem": mem,
            "estimate_min": round(est, 3), "command": cmd, "run": dict(study.get("run") or {})}
    write_atomic(d / "job.json", json.dumps(info, indent=1))
    return info


def run_study_job(request: str, name: str) -> int:
    """`omct run-study REQUEST NAME`: what a SLURM job (or a person) runs. Exit 0 when a result was written."""
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
    run_study(name, study, req / name, meta.get("solver", "omc"), timeout_s(study))
    return 0


# ---- the queue ---------------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _minutes(t: str) -> float | None:
    t = t.strip()
    if not t or t in ("N/A", "INVALID"):
        return None
    days = 0
    if "-" in t:
        dd, t = t.split("-", 1)
        days = int(dd)
    parts = [int(x) for x in t.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return round(days * 1440 + h * 60 + m + s / 60, 1)


def squeue(ids: list[str]) -> dict | None:
    if not ids:
        return {}
    rc, out, err = _run(["squeue", "-h", "-j", ",".join(ids), "-o", "%i|%T|%r|%S|%M"])
    if rc != 0 and "Invalid job id" not in err:
        return None
    q = {}
    for line in out.splitlines():
        f = (line.split("|") + [""] * 5)[:5]
        q[f[0].strip()] = {"state": f[1].strip(), "reason": f[2].strip(), "start": f[3].strip(),
                           "elapsed_min": _minutes(f[4])}
    return q


def pending_ahead() -> dict:
    rc, out, _ = _run(["squeue", "-h", "-t", "PD", "-o", "%i|%Q"])
    jobs = []
    for line in out.splitlines() if rc == 0 else []:
        i, _, p = line.partition("|")
        try:
            jobs.append((float(p or 0), int(i)))
        except ValueError:
            pass
    return {str(i): sum(1 for pp, ii in jobs if pp > p or (pp == p and ii < i)) for p, i in jobs}


def sacct(ids: list[str]) -> dict | None:
    if not ids:
        return {}
    rc, out, _ = _run(["sacct", "-n", "-P", "-X", "-j", ",".join(ids), "-o", "JobID,State"])
    if rc != 0:
        return None
    st = {}
    for line in out.splitlines():
        i, _, s = line.partition("|")
        if i.strip() and "." not in i:
            st[i.strip()] = s.strip()
    return st or None


def _tail(path: Path, n: int = 30) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no job.log)"


def final_state(acct: str | None, log: str) -> tuple[str, str]:
    a = (acct or "").upper()
    if a.startswith("TIMEOUT") or (not acct and "DUE TO TIME LIMIT" in log):
        return "timed_out", ("the SLURM time limit ended the job: give the study a longer run.timeout_s (the job asks "
                             "for it plus 10 minutes), or with OMCT_SLURM_TIME=estimate rerun with "
                             "OMCT_SLURM_TIME=timeout; or shorten the stop time / the sweep")
    if a.startswith("OUT_OF_MEMORY") or (not acct and ("oom-kill" in log.lower() or "out of memory" in log.lower())):
        return "out_of_memory", "out of memory: give the study run.mem (e.g. '4G'), or set OMCT_SLURM_MEM"
    if a.startswith("CANCELLED") or (not acct and "CANCELLED" in log):
        who = a.split("BY", 1)[1].strip() if "BY" in a else None
        return "cancelled", "cancelled" + (f" by user {who}" if who else "")
    if not acct and log == "(no job.log)":
        return "cancelled", ("left the queue without starting (no job.log): cancelled while queued (scancel), or "
                             "SLURM dropped it; submit it again if it was not cancelled on purpose")
    return "failed", f"the job ended without a result ({acct or 'no accounting'}); the end of job.log:\n{log}"


def slurm_states(request: Path, names: list[str], studies: dict | None = None, write: bool = True) -> dict:
    """{name: {"state", ...}} for the request's studies that have job.json and no result. Finished
    failures become the study's result file (when write)."""
    from .api import _progress

    jobs = {}
    for n in names:
        jf = request / n / "job.json"
        if jf.is_file() and not (request / n / f"{n}.json").is_file():
            try:
                jobs[n] = json.loads(jf.read_text())
            except json.JSONDecodeError:
                pass
    if not jobs:
        return {}
    ids = [str(j["job_id"]) for j in jobs.values()]
    q = squeue(ids)
    gone = [n for n, j in jobs.items() if q is not None and str(j["job_id"]) not in q]
    acct = sacct([str(jobs[n]["job_id"]) for n in gone]) if gone else {}
    ahead = pending_ahead() if q and any(v["state"] == "PENDING" for v in q.values()) else {}
    out = {}
    for n, j in jobs.items():
        jid = str(j["job_id"])
        base = {"job_id": jid, "procs": j.get("procs"), "estimate_min": j.get("estimate_min")}
        if (request / n / f"{n}.json").is_file():
            continue
        if q is None:
            out[n] = {"state": "unknown", **base, "why": "squeue failed (is slurmctld running?)"}
            continue
        if jid in q:
            s = q[jid]
            if s["state"] in ("PENDING", "CONFIGURING", "REQUEUED", "SUSPENDED"):
                st = {"state": "queued", **base, "reason": s["reason"], "ahead": ahead.get(jid),
                      "eta_min_max": round(j.get("time_min", 0), 1)}
                if s["start"] and s["start"] not in ("N/A", "Unknown"):
                    st["expected_start"] = s["start"]
            else:
                prog = _progress(request / n, float(j.get("timeout_s") or timeout_s((studies or {}).get(n))))
                st = {**prog, "state": "running", **base, "job_elapsed_min": s["elapsed_min"]}
            out[n] = st
            continue
        if (request / n / f"{n}.json").is_file():           # finished between the calls
            continue
        state, why = final_state((acct or {}).get(jid), _tail(request / n / "job.log"))
        if state == "failed" and (request / n / "cancel").exists():
            state, why = "cancelled", "cancelled with cancel(request) (it left the queue before running)"
        res = {"name": n, "ok": False, "state": state, "why": why, "job_id": jid, "reliable": False,
               "unreliable_because": [state], "execution": {"executor": "slurm", "slurm_job": jid}}
        if write:
            write_atomic(request / n / f"{n}.json", json.dumps(res, indent=1))
        out[n] = res
    return out


# ---- jobs and cancel ---------------------------------------------------------------------------
def queue_summary() -> dict:
    rc, out, _ = _run(["squeue", "-h", "-u", os.environ.get("USER", ""), "-o", "%i|%j|%T|%C"])
    run = qd = cpus = 0
    for line in out.splitlines() if rc == 0 else []:
        f = (line.split("|") + [""] * 4)[:4]
        if not f[1].startswith(f"{CLI}:"):
            continue
        if f[2] in ("RUNNING", "COMPLETING"):
            run += 1
            cpus += int(f[3]) if f[3].isdigit() else 0
        else:
            qd += 1
    machine = {}
    rc, out, _ = _run(["sinfo", "-h", "-o", "%C"])
    if rc == 0 and out.strip():
        a, i, o, t = (out.strip().splitlines()[0].split("/") + ["0"] * 4)[:4]
        try:
            machine = {"cpus": int(t), "cpus_allocated": int(a)}
        except ValueError:
            pass
    return {"queue": {"running": run, "queued": qd, "cpus_in_use": cpus}, "machine": machine}


def cancel_slurm(request: Path, names: list[str]) -> list[str]:
    ids = []
    for n in names:
        jf = request / n / "job.json"
        if jf.is_file() and not (request / n / f"{n}.json").is_file():
            ids.append(str(json.loads(jf.read_text())["job_id"]))
    if ids:
        _run(["scancel"] + ids)
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
