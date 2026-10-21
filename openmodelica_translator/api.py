"""api.py — what the CLI and the MCP server call. JSON in, JSON out.
Version: 2026.10.21.7

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
the same results; a local study still running in a process that ended is reported failed.
"""
from __future__ import annotations

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


def validate(studies: dict) -> dict:
    return _validate(studies)


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

    def submit(self, folder: Path, jobs: list[dict]) -> None:
        if self.pool is None:
            self.pool = mp.get_context("spawn").Pool(int(os.environ.get("OMCT_WORKERS", "2")), maxtasksperchild=20)
        self.jobs.setdefault(str(folder), {}).update(
            {j["name"]: self.pool.apply_async(_run_one, (j,)) for j in jobs})


_POOL = _Pool()


def _request_dir(prefix: str) -> Path:
    d = out_root() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def start(studies: dict, solver: str | None = None, prefix: str = "run", inline: bool = False) -> Path:
    """The request folder; the valid studies go to the pool (inline: run here, one after the other)."""
    folder = _request_dir(prefix)
    s = _solver(solver)
    (folder / "studies.json").write_text(json.dumps(studies, indent=1))
    invalid, jobs = {}, []
    for n, st in studies.items():
        v = validate_study(st)
        if not v["ok"]:
            invalid[n] = {"ok": False, "why": "invalid study", "errors": v["errors"], "warnings": v["warnings"],
                          "reliable": False, "unreliable_because": ["invalid study"]}
        else:
            jobs.append({"name": n, "study": st, "folder": str(folder), "solver": s, "timeout": timeout_s(st)})
    from . import executor as X
    (folder / "request.json").write_text(json.dumps({
        "names": [j["name"] for j in jobs], "invalid": invalid, "solver": s, "started": time.time(),
        "executor": X.kind(), "pid": os.getpid(), "studies": {j["name"]: j["study"] for j in jobs}}, default=float))
    local = []
    for j in jobs:
        if not inline and X.use_slurm(j["study"], s):
            X.submit(folder, j["name"], j["study"], s)
        else:
            local.append(j)
    if inline:
        for j in local:
            _run_one(j)
    elif local:
        _POOL.submit(folder, local)
    return folder


def _progress(d: Path, timeout: float) -> dict:
    if not d.exists():
        return {"state": "queued", "stage": "waiting for a worker (OMCT_WORKERS)"}
    t0 = min((p.stat().st_mtime for p in d.iterdir()), default=time.time())
    el = max(time.time() - t0, 0.0)
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
    res = {"request": str(folder), "translator": __version__, "solver": meta["solver"], "done": not pending,
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
    t0 = time.time()
    t_end = t0 + max(0.0, float(wait))
    while True:
        res = _collect(folder)
        if res["done"] or time.time() >= t_end:
            return res
        # SLURM jobs do not need this process: give quick ones up to 15 s, then return them pending
        prog = res.get("progress", {})
        if prog and all("job_id" in v for v in prog.values()) and time.time() - t0 >= 15:
            return res
        time.sleep(0.5 if time.time() - t0 < 5 else 2.0)


def run(studies: dict, solver: str | None = None, wait: float | None = None) -> dict:
    """wait None: run here, one after the other, and return everything (the CLI); otherwise in the pool."""
    if wait is None:
        folder = start(studies, solver, inline=True)
        return status(str(folder), 0)
    folder = start(studies, solver)
    return status(str(folder), wait)


def _requests(limit: int, prefix: str = "run"):
    root = out_root()
    if not root.is_dir():
        return []
    reqs = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(prefix + "_") and (p / "request.json").is_file()]
    return sorted(reqs, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def jobs(limit: int = 20, active: bool = False) -> dict:
    out = []
    for req in _requests(max(limit * 3, limit)):
        try:
            res = _collect(req)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        studies = {}
        for n, s in res["studies"].items():
            jf = req / n / "job.json"
            studies[n] = {"state": "done" if s.get("ok") else s.get("state", "failed"), "ok": s.get("ok"),
                          "reliable": s.get("reliable"),
                          "job_id": json.loads(jf.read_text()).get("job_id") if jf.is_file() else None}
        for n in res["pending"]:
            p = res["progress"][n]
            studies[n] = {k: p.get(k) for k in ("state", "stage", "job_id", "reason", "ahead", "expected_start",
                                                "eta_min_max", "job_elapsed_min", "points_done", "points")
                          if p.get(k) is not None}
        live = bool(res["pending"])
        if active and not live:
            continue
        out.append({"request": str(req), "started": json.loads((req / "request.json").read_text()).get("started"),
                    "active": live, "studies": studies})
        if len(out) >= limit:
            break
    from . import executor as X
    return {"translator": __version__, "executor": executor_status(), **X.queue_summary(), "requests": out}


def cancel(request: str, names: list[str] | None = None) -> dict:
    folder = Path(request).expanduser()
    if not (folder / "request.json").is_file():
        return {"error": f"no request {folder}"}
    meta = json.loads((folder / "request.json").read_text())
    names = names or meta["names"]
    unknown = [n for n in names if n not in meta["names"]]
    if unknown:
        return {"error": f"no studies {unknown} in this request ({meta['names']})"}
    from . import executor as X
    todo = [n for n in names if not (folder / n / f"{n}.json").is_file()]
    for n in todo:
        (folder / n).mkdir(parents=True, exist_ok=True)
        (folder / n / "cancel").write_text(str(time.time()))
    ids = X.cancel_slurm(folder, todo)
    time.sleep(1.0 if ids else 0.0)
    res = status(str(folder), 3.0 if todo else 0.0)
    return {"request": str(folder), "cancelled": todo, "cancelled_jobs": ids,
            "already_finished": [n for n in names if n not in todo],
            "states": {n: ("done" if n in res["studies"] and res["studies"][n].get("ok") else
                           res["studies"].get(n, {}).get("state") or res["studies"].get(n, {}).get("why") or
                           res.get("progress", {}).get(n, {}).get("state")) for n in names},
            "note": "a queued study is skipped when a worker reaches it; a running one stops within a second"}


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
    return {"images": render(d, res, images)}


def image_path(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    root = out_root().resolve()
    if root not in p.parents or p.suffix.lower() != ".png" or not p.is_file():
        raise ValueError(f"no image {p} under {root}")
    return p


# ---- benchmarks ---------------------------------------------------------------------------------------
def bench(names=None, solver: str | None = None, wait_s: float | None = None) -> dict:
    """wait_s None: run here and return the verdicts; otherwise in the pool, returning after wait_s."""
    from . import bench as B
    cs = B.cases()
    names = list(names) if names else list(cs)
    unknown = [n for n in names if n not in cs]
    studies = {n: cs[n]["study"] for n in names if n in cs}
    folder = start(studies, solver, prefix="bench", inline=wait_s is None)
    (folder / "bench_request.json").write_text(json.dumps({"names": list(studies), "skipped": unknown,
                                                           "translator": __version__}))
    return bench_status(str(folder), 0 if wait_s is None else wait_s)


def bench_status(bench_folder: str, wait_s: float = 0) -> dict:
    from . import bench as B
    folder = Path(bench_folder).expanduser()
    if not (folder / "bench_request.json").is_file():
        return {"error": f"no benchmark request {folder}"}
    meta = json.loads((folder / "bench_request.json").read_text())
    st = status(str(folder), wait_s)
    cs = B.cases()
    res = {n: B.evaluate(n, r, cs) for n, r in st["studies"].items()}
    for n, r in st["studies"].items():
        res[n]["checks_run"] = {k: v.get("ok") for k, v in (r.get("checks") or {}).items()}
    out = {"bench": str(folder), "solver": st["solver"], "translator": __version__, "done": st["done"],
           "passed": [n for n, v in res.items() if v.get("passed")],
           "failed": [n for n, v in res.items() if not v.get("passed")], "pending": st.get("pending", []),
           "skipped": meta.get("skipped", []), "cases": res}
    if not st["done"]:
        out["progress"] = st.get("progress")
        out["next"] = f"call bench_status(bench='{folder}') in a little while"
    else:
        (folder / "bench.json").write_text(json.dumps(out, indent=1, default=float))
    return out
