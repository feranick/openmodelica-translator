"""api.py — what the CLI and the MCP server call. JSON in, JSON out.
Version: 2026.11.19.8

    tool_status()                  versions; omc, MSL, the C compiler, the build cache; the executor and its caps
    study_format()                 the study format, with status
    validate(studies)              static checks
    run(studies, solver, wait)     runs the studies in a request folder under OMCT_OUT: with wait (seconds),
                                   returns then with the rest pending; without, waits for all
    start(studies, solver)         the same in the background: the request folder
    status(request, wait)          finished studies with results, the others pending (state, stage, elapsed)
    jobs(limit, active)            recent requests and their studies' states
    cancel(request, names)         a cancel flag for the request's studies (the running one stops within a second)
    model(study, name)             the generated model, build script and override file, without running
    bench(names, solver, wait_s)   the benchmarks; with wait_s they run in the pool and bench_status collects
    bench_status(bench, wait_s)    the verdicts of the benchmarks finished since
    render_images(folder, images)  new pictures of a finished study; image_path(path) a checked PNG path
    read_file(path, max_mb)        a written file as base64 (only under OMCT_OUT)

Studies run in worker processes (OMCT_WORKERS, default 2) that live across calls of one server process, or as
SLURM jobs (OMCT_EXECUTOR=slurm, executor.py: one job per study). Every state is read from the files
(request.json, <name>/<name>.json, job.json, model.mo, sim.log) and the queue, so another server process sees
the same results; a local study still running in a process that ended is reported failed. From 2026.11.09.2 the
SLURM states and the queue's CPUs behind jobs() are llmt-runtime's (through executor.slurm_states and
executor.queue_summary), and cancel()'s flags and scancel are its cancel_request; the answers keep omct's shapes.
From 2026.11.09.3 status() ends within an MCP call's deadline (3 s kept for the answer) and _close_pool is
llmt-runtime's stop_pool.
"""
from __future__ import annotations

import atexit
import base64
import json
import multiprocessing as mp
import os
import shutil
import time
import uuid
from pathlib import Path

from . import __version__
from .format import study_format as _format
from .format import validate as _validate
from .format import validate_study


def out_root() -> Path:
    return Path(os.environ.get("OMCT_OUT", "~/omct_runs")).expanduser()


def _solver(solver: str | None) -> str:
    s = (solver or os.environ.get("OMCT_SOLVER", "auto")).lower()
    if s == "auto":
        from .omc import find_omc
        return "omc" if "error" not in find_omc() else "ref"
    if s not in ("omc", "ref"):
        raise ValueError("solver: auto, omc or ref")
    return s


def timeout_s(study: dict | None = None) -> float:
    from .executor import timeout_s as t
    return t(study)


def executor_status() -> dict:
    from .executor import status as st
    return st()


def tool_status() -> dict:
    from .omc import cache_status, find_omc, msl_status
    o = find_omc()
    out = {"translator": __version__, "omc": o, "msl": msl_status() if "error" not in o else {"error": "no omc"},
           "cc": shutil.which("gcc") or shutil.which("clang"), "cache": cache_status(),
           "default_solver": _solver(None), "compile_procs": int(os.environ.get("OMCT_COMPILE_PROCS", "4")),
           "omp_threads": int(os.environ.get("OMCT_OMP_THREADS", "1")), "executor": executor_status()}
    for mod in ("fea_translator", "build123d_translator", "cfd_translator"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "yes")
        except ImportError:
            out[mod] = None
    return out


def study_format() -> dict:
    return {**_format(), "translator": __version__, "status": tool_status()}


def _after_problems(request, name: str, study: dict, studies: dict) -> list[str]:
    """run.after / run.after_mode (llmt-runtime's after_problems): the runs waited for exist on this machine, no
    cycle, not itself. Only for a study that has them (as cfdt's)."""
    r = study.get("run") if isinstance(study, dict) else None
    if not isinstance(r, dict) or not {"after", "after_mode"} & set(r):
        return []
    from .runtime import RT
    return RT.jobs.after_problems(request, name, study, studies)


def validate(studies: dict) -> dict:
    """Static checks; each valid study also gets "estimate": {minutes, procs, mem_mb} (the router's placement)."""
    from .executor import estimate
    out = _validate(studies)
    for n, st in (studies.items() if isinstance(studies, dict) else []):
        rec = out.get("studies", {}).get(n)
        if rec is None or not rec.get("ok"):
            continue
        ap = _after_problems(None, n, st, studies)
        if ap:
            rec["ok"] = False
            rec["errors"] = ap + rec.get("errors", [])
            out["ok"] = False
    try:
        solver = _solver(None)
    except ValueError:
        solver = "omc"
    for name, r in out.get("studies", {}).items():
        if r.get("ok"):
            try:
                r["estimate"] = estimate(studies[name], solver)
            except Exception:  # noqa: BLE001 - an estimate is optional; validation stands without it
                pass
            if studies[name].get("fit"):
                try:
                    from .executor import fit_time
                    ft = fit_time(studies[name], solver)
                    if ft["expected_s"] > ft["timeout_s"]:
                        r.setdefault("warnings", []).append(
                            f"fit: expected to need about {ft['expected_s'] / 60:.0f} min ({ft['run_s']:.2g} s a run), "
                            f"more than run.timeout_s ({ft['timeout_s']:g} s), which limits the whole fit: raise "
                            "run.timeout_s (within the caps)")
                except Exception:  # noqa: BLE001
                    pass
    return out


# ---- the worker pool ---------------------------------------------------------------------------------
def _run_one(job: dict) -> dict:
    from .runner import run_study
    r = run_study(job["name"], job["study"], Path(job["folder"]) / job["name"], job["solver"], job["timeout"])
    r.pop("trace", None)
    return {"ok": r.get("ok")}


class _Pool:
    def __init__(self):
        self.pool = None
        self.jobs: dict[str, dict] = {}

    def ensure(self) -> None:
        """The pool, created once. llmt-runtime's make_pool raises WorkersNotPermitted where the MCP client's sandbox
        refuses worker processes (Gemini's Local MCP: SemLock EPERM); it reaches the caller as the tool's error."""
        if self.pool is None:
            from llmt_runtime.workers import make_pool

            from .workers import watch_parent
            # each worker ends when this server is gone (crash, kill -9): llm-translators "Worker pools never
            # outlive the server"
            self.pool = make_pool(int(os.environ.get("OMCT_WORKERS", "2")), maxtasksperchild=20,
                                  initializer=watch_parent, initargs=(os.getpid(),))

    def submit(self, folder: Path, jobs: list[dict]) -> None:
        self.ensure()
        self.jobs.setdefault(str(folder), {}).update(
            {j["name"]: self.pool.apply_async(_run_one, (j,)) for j in jobs})


_POOL = _Pool()


def _close_pool():
    """Stops the study pool (at exit, on a signal, on the stale-server exit). From 2026.11.09.3 with llmt-runtime's
    stop_pool: SIGTERM to each worker (its handler ends its omc build or simulation), SIGKILL after 3 s to one inside a
    long native call, then the pool; Pool.terminate() alone could wait on such a worker."""
    pool, _POOL.pool = _POOL.pool, None
    if pool is not None:
        from llmt_runtime.workers import stop_pool
        stop_pool(pool)                  # workers are daemons; quiet at exit (no Pool.__del__ noise)


atexit.register(_close_pool)


def _request_dir(prefix: str) -> Path:
    d = out_root() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


from llmt_runtime.requests import REQUEST_NAME  # noqa: E402,F401 (run_<id>: the router names requests)


def start(studies: dict, solver: str | None = None, prefix: str = "run", inline: bool = False,
          request_name: str | None = None) -> Path:
    """The request folder; the valid studies go to the pool (inline: run here, one after the other).
    request_name: the folder's name, chosen by the caller (llm-translators' router) so it knows the path first."""
    if request_name:
        folder = out_root() / request_name
        folder.mkdir(parents=True, exist_ok=False)
    else:
        folder = _request_dir(prefix)
    s = _solver(solver)
    (folder / "studies.json").write_text(json.dumps(studies, indent=1))
    invalid, jobs = {}, []
    for n, st in studies.items():
        v = validate_study(st)
        ap = _after_problems(folder, n, st, studies) if v["ok"] else []
        if ap:                                     # run.after: checked with the request folder, before any submission
            v = {"ok": False, "errors": ap, "warnings": v["warnings"]}
        if not v["ok"]:
            invalid[n] = {"ok": False, "why": "invalid study", "errors": v["errors"], "warnings": v["warnings"],
                          "reliable": False, "unreliable_because": ["invalid study"]}
        else:
            jobs.append({"name": n, "study": st, "folder": str(folder), "solver": s, "timeout": timeout_s(st)})
    from llmt_runtime import requests as R

    from . import executor as X
    from .runtime import RT
    queued = [not inline and X.use_slurm(j["study"], s) for j in jobs]
    local = [j for j, q in zip(jobs, queued) if not q]
    if local and not inline:
        # the pool before anything is recorded or submitted: where workers are not permitted, the call fails with
        # that error and leaves no request that would look pending for ever
        try:
            _POOL.ensure()
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    # with the versions it is submitted with ("translator", llmt-runtime's "runtime"): status says what it ran with
    (folder / "request.json").write_text(json.dumps(R.request_meta(
        RT, names=[j["name"] for j in jobs], invalid=invalid, solver=s, started=time.time(),
        executor=X.kind(), pid=os.getpid(), studies={j["name"]: j["study"] for j in jobs}), default=float))
    for j, q in zip(jobs, queued):
        if q:
            X.submit(folder, j["name"], j["study"], s)
    if inline:
        for j in local:
            _run_one(j)
    elif local:
        _POOL.submit(folder, local)
    return folder


def _progress(d: Path, timeout: float) -> dict:
    from llmt_runtime.jobs import extended_s
    if not d.exists():
        return {"state": "queued", "stage": "waiting for a worker (OMCT_WORKERS)"}
    timeout = timeout + extended_s(d)               # what extend added (each run's deadline moved by it)
    t0 = min((p.stat().st_mtime for p in d.iterdir()), default=time.time())
    el = max(time.time() - t0, 0.0)
    if (d / "fit_progress.json").exists():
        try:
            fp = json.loads((d / "fit_progress.json").read_text())
        except (OSError, ValueError):
            fp = {}
        if (d / "final").exists():
            stage = "fit: the final run at the best values (with every check)"
        else:
            c = fp.get("best_cost")
            stage = (f"fit: start {fp.get('start', 0) + 1} of {fp.get('starts', 1)}, {fp.get('evaluations', 0)} model "
                     f"runs ({fp.get('failed', 0)} failed), best cost " + ("-" if c is None else f"{c:.3g}"))
        return {"state": "running", "stage": stage, "evaluations": fp.get("evaluations", 0),
                "elapsed_min": round(el / 60, 2), "eta_min_max": round(max(timeout - el, 0) / 60, 1),
                "note": "a fit stops at run.timeout_s (extend adds time) with its best point so far"}
    if (d / "sweep.json").exists():
        n = json.loads((d / "sweep.json").read_text())["points"]
        done = sum(1 for p in (d / "points").glob("p*/p*.json")) if (d / "points").exists() else 0
        per = el / done if done else None
        return {"state": "running", "stage": f"sweep: {done} of {n} points done", "points_done": done, "points": n,
                "elapsed_min": round(el / 60, 2),
                "eta_min_max": round(min(per * (n - done), max(timeout - el, 0)) / 60, 1) if per else round(max(timeout - el, 0) / 60, 1)}
    if (d / "sim_converge.log").exists():
        stage = "second run at tolerance/10 (converge)"
    elif (d / "sim.log").exists():
        stage = "simulating" if not (d / "result.mat").exists() and not (d / "linearized_model.mo").exists() else "checking results"
    elif (d / "model.mo").exists():
        stage = "building (omc) or reusing the cached build"
    else:
        stage = "validating and translating"
    return {"state": "running", "stage": stage, "elapsed_min": round(el / 60, 2),
            "eta_min_max": round(max(timeout - el, 0) / 60, 1),
            "note": "eta_min_max is the time limit left; most studies take seconds"}


def job_progress(folder: Path, study: dict) -> dict:
    """Where a running SLURM study is (llmt-runtime's hooks.running_progress, for its list_jobs): stage, points_done,
    points, elapsed_min, eta_min_max from _progress, with the timeout its job was sized with (job.json)."""
    from llmt_runtime import jobs as J
    pr = _progress(Path(folder), J.job_timeout_s(Path(folder), timeout_s(study or {})))
    return {k: pr[k] for k in ("stage", "points_done", "points", "evaluations", "elapsed_min", "eta_min_max") if k in pr}


def _collect(folder: Path) -> dict:
    from . import executor as X
    meta = json.loads((folder / "request.json").read_text())
    handles = _POOL.jobs.get(str(folder), {})
    slurm = X.slurm_states(folder, meta["names"], meta.get("studies", {}))
    out, pending, progress, job_ids = dict(meta.get("invalid", {})), [], {}, {}
    for name in meta["names"]:
        f = folder / name / f"{name}.json"
        h = handles.get(name)
        if f.is_file() and (h is None or h.ready()):
            try:
                out[name] = json.loads(f.read_text())
                out[name].pop("trace", None)
                continue
            except json.JSONDecodeError:
                pass
        if name in slurm:
            st = slurm[name]
            job_ids[name] = st.get("job_id")
            if st["state"] in X.FINAL:
                out[name] = st
            else:
                pending.append(name)
                progress[name] = st
            continue
        if (folder / name / "job.json").is_file():       # a job that finished between the reads
            pending.append(name)
            progress[name] = {"state": "running", "stage": "finishing"}
            continue
        if h is not None and h.ready():
            try:
                h.get()
                out[name] = {"ok": False, "why": "the worker ended without a result"}
            except Exception as exc:  # noqa: BLE001
                out[name] = {"ok": False, "why": "worker failed", "error": str(exc)[:500]}
        elif h is None and meta.get("pid") == os.getpid() and not f.is_file():
            pending.append(name)
            progress[name] = _progress(folder / name, timeout_s(meta["studies"].get(name)))
        elif h is None:
            if _alive(meta.get("pid")) and not (folder / name / "cancel").exists():
                pending.append(name)
                progress[name] = {**_progress(folder / name, timeout_s(meta["studies"].get(name))),
                                  "note": f"run by another server process (pid {meta.get('pid')})"}
            else:
                out[name] = {"ok": False, "state": "failed",
                             "why": "not finished and no longer running (the server that ran it stopped?); run it again"}
        else:
            pending.append(name)
            progress[name] = _progress(folder / name, timeout_s(meta["studies"].get(name)))
    # the version the studies ran with (their results', else request.json's), and translator_now when this server
    # runs another (llmt-runtime's ran_with; before 2026.11.09.1 the installed version, whatever the run used)
    from llmt_runtime import requests as R

    from .runtime import RT
    res = {"request": str(folder), **R.ran_with(RT, meta, out), "solver": meta["solver"], "done": not pending,
           "pending": pending, "elapsed_s": round(time.time() - meta.get("started", time.time()), 1), "studies": out}
    if pending:
        res["progress"] = progress
        sl = [job_ids[n] for n in pending if n in job_ids]
        if sl:
            etas = [p.get("eta_min_max") for p in progress.values() if p.get("eta_min_max") is not None]
            res["next"] = (f"The studies are SLURM jobs on this machine (ids {', '.join(map(str, sl))}). They keep running "
                           f"without this conversation. Tell the user when they should finish (at most "
                           f"{max(etas) if etas else '?'} min once running) and stop; collect them later with "
                           f"study_status(request='{folder}') or find them with jobs().")
        else:
            res["next"] = (f"call study_status(request='{folder}') to collect the rest. Polling costs tokens: if eta_min_max "
                           f"is more than about 5 min, tell the user when to come back and stop polling")
    else:
        (folder / "results.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def status(request: str, wait: float = 0.0) -> dict:
    folder = Path(request).expanduser()
    if not (folder / "request.json").is_file():
        return {"error": f"no request {folder}"}
    from llmt_runtime.requests import until
    t0 = time.time()
    t_end = until(wait)          # within an MCP call's deadline, 3 s kept for the answer (llmt-runtime's)
    while True:
        tc = time.time()
        res = _collect(folder)
        took = time.time() - tc                  # another collect must also end by t_end (squeue can be slow)
        if res["done"] or time.time() + took + 0.5 >= t_end:
            return res
        # SLURM jobs do not need this process: give quick ones up to 15 s, then return them pending
        prog = res.get("progress", {})
        if prog and all("job_id" in v for v in prog.values()) and time.time() - t0 >= 15:
            return res
        time.sleep(0.5 if time.time() - t0 < 5 else 2.0)


def run(studies: dict, solver: str | None = None, wait: float | None = None, request_name: str | None = None) -> dict:
    """wait None: run here, one after the other, and return everything (the CLI); otherwise in the pool.
    request_name ('run_<id>'): the request folder's name, so a caller that loses the answer (llm-translators' router:
    'submitted_unconfirmed') knows where to look; a repeated call with the same name returns that request's status
    and submits nothing again (ROUTER.md, "Requests named by the router"; as cfdt 2026.11.06.1)."""
    from llmt_runtime import requests as R

    from .runtime import RT
    kind_, found = R.check_request_name(RT, request_name)     # llmt-runtime: new | repeat (its status) | error
    if kind_ == "error":
        return found
    if kind_ == "repeat":
        res = status(str(found), 0.0 if wait is None else min(wait, 15.0))
        return dict(res, note=R.REPEAT_NOTE)
    t0 = time.time()
    try:
        folder = start(studies, solver, inline=wait is None, request_name=request_name)
    except FileExistsError:                                   # another call took the name between the look and now
        return {"error": f"request_name {request_name!r}: the folder exists but holds no request (still being "
                         "submitted by an earlier call? call again in a few seconds)",
                "request": str(out_root() / request_name)}
    if wait is None:
        return status(str(folder), 0)
    return status(str(folder), max(0.0, wait - (time.time() - t0)))     # submitting counts against the wait


def jobs(limit: int = 20, active: bool = False) -> dict:
    """The jobs() tool: llmt-runtime's list_jobs over every request (all_requests, from runtime 2026.11.19.2: local-pool
    studies too, and run_benchmarks' bench_* requests), in omct's shape through the hooks below."""
    from llmt_runtime import jobs as J

    from .runtime import RT
    return J.list_jobs(RT, limit, active, all_requests=True)


_PROGRESS_KEYS = ("state", "stage", "job_id", "reason", "ahead", "expected_start", "eta_min_max", "job_elapsed_min",
                  "points_done", "points")


def jobs_states(folder: Path, names: list, meta: dict) -> dict:
    """{study: state record} of one request for jobs() (the runtime's hooks.jobs_states): omct's _collect, which
    knows local-pool studies, SLURM jobs and invalid studies alike. A request that cannot be read gives {}."""
    try:
        res = _collect(Path(folder))
    except (OSError, json.JSONDecodeError, KeyError):
        return {}
    out = {n: {"state": "done" if s.get("ok") else s.get("state", "failed"), "ok": s.get("ok"),
               "reliable": s.get("reliable")} for n, s in res["studies"].items()}
    for n in res["pending"]:
        out[n] = dict(res["progress"].get(n) or {"state": "running"})
    return out


def jobs_row(folder: Path, name: str, rec: dict, meta: dict) -> dict:
    """One study's row in jobs() (the runtime's hooks.jobs_row): state, ok, reliable and job_id when it has ended;
    state, stage, job_id and the progress keys while it is queued or running."""
    if "ok" in rec:
        jf = Path(folder) / name / "job.json"
        try:
            jid = json.loads(jf.read_text()).get("job_id") if jf.is_file() else None
        except (OSError, json.JSONDecodeError):
            jid = None
        return {"state": rec["state"], "ok": rec["ok"], "reliable": rec.get("reliable"), "job_id": jid}
    return {k: rec[k] for k in _PROGRESS_KEYS if rec.get(k) is not None}


def jobs_answer(ans: dict) -> dict:
    """The whole jobs() answer (the runtime's hooks.jobs_answer): omct's executor status, benchmark requests marked
    (kind, the bench_status call that collects them), requests that could not be read left out."""
    rows = []
    for row in ans.get("requests", []):
        if not row.get("studies") and not row.get("note"):
            continue
        if Path(row["request"]).name.startswith("bench_"):
            row = dict(row, kind="benchmarks", collect=f"bench_status(bench='{row['request']}')")
        rows.append(row)
    return {**ans, "translator": __version__, "executor": executor_status(), "requests": rows}


def solver_left_s(folder: Path, study: dict) -> float:
    """Seconds a running study may still take before omct's own deadline (llmt-runtime's hooks.solver_left_s, which
    extend uses to decide whether the SLURM job's limit must be raised): its progress's eta_min_max (with what extend
    added so far), 0 when unknown."""
    from llmt_runtime import jobs as J
    try:
        pr = _progress(Path(folder), J.job_timeout_s(Path(folder), timeout_s(study or {})))
        return float(pr.get("eta_min_max") or 0.0) * 60.0
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return 0.0


def extend(request: str, hours: float, names: list[str] | None = None) -> dict:
    """More time for a request's unfinished studies (or the named ones): each run's deadline (omc build, simulation,
    every sweep point) moves back by `hours` within about 2 s (an 'extend' file in the study's folder, which the run
    consumes; extended.json keeps the total), and later runs of the study get it too. Within OMCT_MAX_TIMEOUT when set
    (run.timeout_s plus what was added). A SLURM job's limit is raised only when its time left does not cover the new
    deadline (SLURM may refuse a user's raise: then an admin has to). llmt-runtime's extend_request."""
    from .runtime import RT
    folder = Path(request).expanduser()
    try:
        meta = json.loads((folder / "request.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"error": f"no request {folder}"}
    out = RT.jobs.extend_request(folder, meta, meta.get("studies") or {}, hours, names)
    for rec in out.get("studies", {}).values():
        if "note" in rec:
            rec["note"] = "the running build or simulation takes it within about 2 s (extended.json records it)"
    return out


def cancel(request: str, names: list[str] | None = None, at: str | None = None) -> dict:
    """Stops the request's studies (or the named ones): a cancel flag in each unfinished study's folder (the runner
    checks it in whatever process runs the study) and scancel for SLURM jobs, queued or running: llmt-runtime's
    cancel_request (2026.11.09.2); 'error' when scancel failed. Then their states. at: 'now' (default) or 'job_end'
    (the current jobs finish, nothing after them starts: llmt-runtime 2026.11.18.1)."""
    from .runtime import RT
    folder = Path(request).expanduser()
    if not (folder / "request.json").is_file():
        return {"error": f"no request {folder}"}
    meta = json.loads((folder / "request.json").read_text())
    c = RT.jobs.cancel_request(folder, meta, names, at=at)
    if "want" not in c:                      # unknown names
        return c
    names, todo, ids = c["want"], c["todo"], c["cancelled_jobs"]
    time.sleep(1.0 if ids else 0.0)
    res = status(str(folder), 3.0 if todo else 0.0)
    out = {"request": str(folder), "cancelled": todo, "cancelled_jobs": ids,
           "already_finished": c["already_finished"],
           "states": {n: ("done" if n in res["studies"] and res["studies"][n].get("ok") else
                          res["studies"].get(n, {}).get("state") or res["studies"].get(n, {}).get("why") or
                          res.get("progress", {}).get(n, {}).get("state")) for n in names},
           "note": "a queued study is skipped when a worker reaches it; a running one stops within a second"}
    if c.get("error"):
        out["error"] = c["error"]
    return out


def model(study: dict, name: str = "study") -> dict:
    from .modelica import build_mos, escape_translation, override_file, translate
    v = validate_study(study)
    if not v["ok"]:
        return {"error": "invalid study", **v}
    t = escape_translation(study) if "modelica" in study else translate(study, name)
    return {"model.mo": t.text, "build.mos": build_mos("model.mo", os.environ.get("OMCT_MSL_VERSION", "4.0.0"),
                                                      model=t.model),
            "params.txt": override_file(t.overrides), "fixed": t.fixed,
            "outputs": {k: {"terms": v_[0], "unit": v_[1]} for k, v_ in t.outputs.items()}, "warnings": v["warnings"]}


def read_file(path: str, max_mb: float = 20) -> dict:
    p = Path(path).expanduser().resolve()
    root = out_root().resolve()
    if root not in p.parents:
        return {"error": f"only files under {root} can be read"}
    if not p.is_file():
        return {"error": f"no file {p}"}
    if p.stat().st_size > max_mb * 1e6:
        return {"error": f"{p.stat().st_size / 1e6:.1f} MB is more than max_mb"}
    return {"name": p.name, "bytes": p.stat().st_size, "base64": base64.b64encode(p.read_bytes()).decode()}


def render_images(study_folder: str, images=True) -> dict:
    from .render import render, spec_errors
    d = Path(study_folder).expanduser().resolve()
    root = out_root().resolve()
    if root not in d.parents:
        return {"error": f"only study folders under {root}"}
    res_f = d / f"{d.name}.json"
    if not res_f.is_file():
        return {"error": f"no finished study in {d} (a study's folder: <request>/<name>)"}
    res = json.loads(res_f.read_text())
    errs = spec_errors(images, list(res.get("outputs") or {}))
    if errs:
        return {"error": "invalid images", "errors": errs}
    out = render(d, res, images)
    if res.get("fit") and images is True:                 # a fit: data against the model too
        from .fit import plot
        out.update(plot(d, res))
    return {"images": out}


def image_path(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    root = out_root().resolve()
    if root not in p.parents or p.suffix.lower() != ".png" or not p.is_file():
        raise ValueError(f"no image {p} under {root}")
    return p


# ---- benchmarks ---------------------------------------------------------------------------------------
def bench(names=None, solver: str | None = None, wait_s: float | None = None) -> dict:
    """wait_s None: run here and return the verdicts; otherwise in the pool (or SLURM), returning after wait_s.
    Chain cases (chain_*) run their other translator first, here, and only when named."""
    from . import bench as B
    names = list(names) if names else list(B.cases())
    studies, checks, skipped = B.prepare(names)
    if (solver or os.environ.get("OMCT_SOLVER", "")) == "ref":
        for n in [n for n in studies if n in B.omc_only()]:
            studies.pop(n)
            checks.pop(n, None)
            skipped[n] = "needs OpenModelica (not covered by the reference yet)"
    t0 = time.time()
    folder = start(studies, solver, prefix="bench", inline=wait_s is None)
    (folder / "bench_request.json").write_text(json.dumps({"names": list(studies), "skipped": skipped,
                                                           "checks": checks, "translator": __version__}, default=float))
    # starting 37 studies counts against the wait (2026-10-31: the first call after the OpenModelica 1.27.2 update
    # ran past the router's limit)
    return bench_status(str(folder), 0 if wait_s is None else max(0.0, wait_s - (time.time() - t0)))


def bench_status(bench_folder: str, wait_s: float = 0) -> dict:
    from . import bench as B
    folder = Path(bench_folder).expanduser()
    if not (folder / "bench_request.json").is_file():
        return {"error": f"no benchmark request {folder}"}
    meta = json.loads((folder / "bench_request.json").read_text())
    st = status(str(folder), wait_s)
    res = {n: B.evaluate(n, r, checks=meta["checks"][n]) for n, r in st["studies"].items() if n in meta["checks"]}
    for n, r in st["studies"].items():
        if n in res:
            res[n]["checks_run"] = {k: v.get("ok") for k, v in (r.get("checks") or {}).items()}
    sk = meta.get("skipped", {})
    ver = {k: st[k] for k in ("translator", "translator_now", "translator_note") if k in st}   # what the cases ran with
    out = {"bench": str(folder), "solver": st["solver"], **(ver or {"translator": __version__}), "done": st["done"],
           "passed": [n for n, v in res.items() if v.get("passed")],
           "failed": [n for n, v in res.items() if not v.get("passed")], "pending": st.get("pending", []),
           "skipped": sk if isinstance(sk, dict) else {n: "unknown" for n in sk}, "cases": res}
    if not st["done"]:
        out["progress"] = st.get("progress")
        out["next"] = f"call bench_status(bench='{folder}') in a little while"
    else:
        (folder / "bench.json").write_text(json.dumps(out, indent=1, default=float))
    return out
