"""api.py — what the CLI (and, from milestone 4, the MCP server) calls. JSON in, JSON out.
Version: 2026.10.21.4

    tool_status()            versions; omc, MSL, the C compiler, the build cache; the executor and its caps
    study_format()           the study format, with status
    validate(studies)        static checks
    run(studies, solver)     runs every study now, one after the other, into a request folder under OMCT_OUT
                             (milestone 2; the worker pool, SLURM and pending results come with milestones 4 and 6)
    model(study, name)       the generated model, build script and override file, without running
    bench(names, solver)     the benchmark studies with closed-form answers (milestone 3: run now, one by one)
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from . import __version__
from .format import caps
from .format import study_format as _format
from .format import validate as _validate


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
    t = ((study or {}).get("run") or {}).get("timeout_s")
    return float(t) if t else float(os.environ.get("OMCT_TIMEOUT", "600"))


def executor_status() -> dict:
    return {"kind": "local", "workers": int(os.environ.get("OMCT_WORKERS", "2")),
            "procs": int(os.environ.get("OMCT_PROCS", "1") or 1), "timeout_s": timeout_s(), "caps": caps()}


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


def _request_dir(prefix: str) -> Path:
    d = out_root() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(studies: dict, solver: str | None = None) -> dict:
    """Runs every study now (synchronously) into <OMCT_OUT>/run_<date>_<time>_<id>/<name>/<name>.json."""
    from .runner import run_study
    folder = _request_dir("run")
    s = _solver(solver)
    (folder / "studies.json").write_text(json.dumps(studies, indent=1))
    (folder / "request.json").write_text(json.dumps({"names": list(studies), "solver": s, "started": time.time(),
                                                     "studies": studies, "executor": "local"}))
    out = {}
    for name, st in studies.items():
        out[name] = run_study(name, st, folder / name, s, timeout_s(st))
        out[name].pop("trace", None)
    res = {"request": str(folder), "translator": __version__, "solver": s, "done": True, "pending": [],
           "studies": out}
    (folder / "results.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def model(study: dict, name: str = "study") -> dict:
    from .format import validate_study
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
    import base64
    p = Path(path).expanduser().resolve()
    root = out_root().resolve()
    if root not in p.parents:
        return {"error": f"only files under {root} can be read"}
    if not p.is_file():
        return {"error": f"no file {p}"}
    if p.stat().st_size > max_mb * 1e6:
        return {"error": f"{p.stat().st_size / 1e6:.1f} MB is more than max_mb"}
    return {"name": p.name, "bytes": p.stat().st_size, "base64": base64.b64encode(p.read_bytes()).decode()}


def bench(names=None, solver: str | None = None) -> dict:
    from . import bench as B
    s = _solver(solver)
    folder = _request_dir("bench")
    r = B.run(s, names, folder)
    out = {"bench": str(folder), "solver": s, "translator": __version__,
           "passed": [k for k, v in r.items() if v.get("passed")],
           "failed": [k for k, v in r.items() if "skipped" not in v and not v.get("passed")],
           "skipped": [k for k, v in r.items() if "skipped" in v], "cases": r}
    (folder / "bench.json").write_text(json.dumps(out, indent=1, default=float))
    return out
