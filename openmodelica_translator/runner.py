"""runner.py — one study end to end: validate, translate, build (or reuse), run, read, check, write the result.
Version: 2026.10.21.2

run_study(name, study, folder, solver, timeout, cancel) -> the result dict, also written atomically to
<folder>/<name>.json. solver: "omc" (OpenModelica) or "ref" (the reference implementation only: linear
networks, no OpenModelica; for tests and machines without it).
Milestone 2: transient and steady runs (outputs read from the result file, every applied value read back) and
linearisation (A, B, C, D, modes, Bode). Derived quantities, the energy balance, the reference comparison,
converge and sweeps come with milestones 3 and 5.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path

import numpy as np

from . import __version__


def _atomic(path: Path, data: dict):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=_json))
    os.replace(tmp, path)


def _json(x):
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, complex):
        return [x.real, x.imag]
    return str(x)


def execution(procs_requested=1, procs_used=1, limits=None) -> dict:
    kind = os.environ.get("OMCT_EXECUTION") or ("slurm" if os.environ.get("SLURM_JOB_ID") else "local")
    return {"executor": kind, "slurm_job": os.environ.get("SLURM_JOB_ID"), "procs_requested": procs_requested,
            "procs_used": procs_used, "mpi": None, "limits": limits or {}}


def _times(an: dict):
    kind = an["kind"]
    t0 = float(an.get("start_time", 0.0))
    if kind == "linearize":
        return t0, float(an.get("at", 0.0)), 1, float(an.get("tolerance", 1e-6)), "dassl"
    t1 = float(an["stop_time"] if kind == "transient" else an["max_time"])
    return t0, t1, int(an.get("intervals", 500)), float(an.get("tolerance", 1e-6)), an.get("method", "dassl")


def run_study(name: str, study: dict, folder, solver: str = "omc", timeout: float = 600, cancel=None) -> dict:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cancel = Path(cancel) if cancel else folder / "cancel"
    t_start = time.perf_counter()
    try:
        res = _run(name, study, folder, solver, timeout, cancel)
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "why": f"internal error: {type(exc).__name__}: {exc}",
               "trace": traceback.format_exc()[-3000:]}
    res.setdefault("name", name)
    res["translator"] = __version__
    res.setdefault("seconds", {})
    res["seconds"]["total"] = round(time.perf_counter() - t_start, 3)
    res.setdefault("execution", execution(limits={"timeout_s": timeout,
                                                  "from_study": sorted((study.get("run") or {}).keys())}))
    if res.get("ok") is False:
        res.setdefault("reliable", False)
        res.setdefault("unreliable_because", [res.get("why", "failed")])
    _atomic(folder / f"{name}.json", res)
    return res


def _run(name, study, folder, solver, timeout, cancel) -> dict:
    from . import modelica as M
    from .format import validate_study

    v = validate_study(study)
    if not v["ok"]:
        return {"ok": False, "why": "invalid study", "errors": v["errors"], "warnings": v["warnings"]}
    warnings = list(v["warnings"])
    if study.get("sweep"):
        return {"ok": False, "why": "sweeps are not implemented yet in this version (milestone 5)", "warnings": warnings}
    an = study["analysis"]
    seconds = {}
    t = time.perf_counter()
    tr = M.escape_translation(study) if "modelica" in study else M.translate(study, name)
    seconds["translate"] = round(time.perf_counter() - t, 3)
    (folder / "model.mo").write_text(tr.text)
    files = {"model": str(folder / "model.mo")}
    out = {"ok": True, "warnings": warnings, "files": files, "seconds": seconds, "solver": solver,
           "inputs": tr.net.inputs if tr.net is not None else {}, "fixed_initial_values": tr.fixed}
    units = {k: u for k, (_, u) in tr.outputs.items()}
    if solver == "ref":
        return _run_ref(study, tr, out, units)
    from . import omc, simrun
    from .matfile import Result

    b = omc.build(tr, timeout=timeout, cancel=cancel)
    seconds["build"] = b.get("seconds")
    out["model_info"] = {"hash": b.get("hash"), "cache_hit": b.get("cache_hit"), "omc": b.get("omc"),
                         "msl": b.get("msl"), **(b.get("check") or {})}
    warnings += b.get("warnings", [])
    if not b.get("ok"):
        out.update({"ok": False, "why": "the model did not build", "errors": b.get("errors", [])})
        out["files"].update(b.get("files", {}))
        return out
    # values the compiled model cannot take (isValueChangeable false in _init.xml)
    ch = b.get("changeable") or {}
    refused = sorted(k for k in tr.overrides if ch.get(k) is False)
    if refused:
        out.update({"ok": False, "why": "values the compiled model cannot change",
                    "errors": [f"{k} is fixed in the compiled model (structural, final or bound)" for k in refused]})
        return out
    t0, t1, n, tol, method = _times(an)
    lin = an["kind"] == "linearize"
    filt = None if lin else M.variable_filter(tr)
    sim = simrun.simulate(b["dir"], folder, tr.overrides, start=t0, stop=t1 if not lin else max(t1, t0),
                          intervals=n, tolerance=tol, method=method, variable_filter=filt,
                          timeout=timeout, linearize_at=t1 if lin else None, cancel=cancel)
    seconds["simulate"] = sim["seconds"]
    out["files"].update(sim["files"])
    warnings += sim["warnings"]
    out["checks"] = {"balance": {"ok": True, **(b.get("check") or {})},
                     "initialization": {"ok": sim["ok"] or sim["init_homotopy_steps"] is not None,
                                        "homotopy_steps": sim["init_homotopy_steps"]},
                     "solver": {"ok": sim["ok"], **sim["stats"]}}
    if sim["chattering"]:
        out["checks"]["events"] = {"ok": False, "chattering": sim["chattering"]}
    elif sim["stats"]:
        ev = sim["stats"].get("state_events", 0)
        out["checks"]["events"] = {"ok": ev <= 1000, "state_events": ev, "time_events": sim["stats"].get("time_events")}
    if not sim["ok"]:
        out.update({"ok": False, "why": "the simulation failed", "errors": sim["errors"]})
        return out
    if "result" in sim:
        r = Result(sim["result"])
        rb = simrun.read_back(r, tr.overrides)
        out["checks"]["overrides"] = rb
        if not rb["ok"]:
            out["ok"] = False
            out["why"] = "values were not applied as asked"
            out["errors"] = [f"{m['name']}: set {m['set']!r}, the run used {m['got']!r}" for m in rb["mismatch"]] + \
                            [f"{n_} is not in the result file" for n_ in rb["not_recorded"]]
            return out
        if not lin:
            from .post import combine, outputs
            vals = {}
            for oname, (terms, _) in tr.outputs.items():
                try:
                    vals[oname] = combine(r, terms)
                except KeyError as exc:
                    out.update({"ok": False, "why": f"output {oname}: {exc} is not in the result file"})
                    return out
            out["outputs"] = outputs(r.time, vals, units)
            fin = all(o["finite"] for o in out["outputs"].values())
            out["checks"]["finite"] = {"ok": fin}
            if not fin:
                out["ok"] = False
                out["why"] = "an output is not finite"
            if an["kind"] == "steady":
                out["checks"]["solver"]["steady_state_time"] = sim["steady_state_time"]
    if lin:
        from .linear import analyse, bode_freqs, read
        L = read(sim["linear"])
        out["linearization"] = analyse(L, bode_freqs(an.get("bode")))
    out["reliable"] = out["ok"] and all(c.get("ok", True) for c in out["checks"].values())
    if not out["reliable"]:
        out["unreliable_because"] = [k for k, c in out["checks"].items() if not c.get("ok", True)]
    return out


def _run_ref(study, tr, out, units) -> dict:
    """The reference implementation alone (no OpenModelica)."""
    from .post import outputs

    r = tr.ref
    if r is None:
        out.update({"ok": False, "why": "the escape hatch needs OpenModelica (no reference)"})
        return out
    if not r.exact:
        out.update({"ok": False, "why": "the reference implementation covers linear networks only: " + "; ".join(r.not_exact)})
        return out
    an = study["analysis"]
    t0, t1, n, tol, method = _times(an)
    if an["kind"] == "linearize":
        from .linear import bode_freqs
        ev = r.eigenvalues()
        lin = {"eigenvalues": [[float(x.real), float(x.imag)] for x in ev], "outputs": list(tr.outputs),
               "inputs": sorted(tr.net.free_inputs)}
        fr = bode_freqs(an.get("bode"))
        if fr:
            bode = {}
            for i in lin["inputs"]:
                for o, (terms, _) in tr.outputs.items():
                    g = r.freq_response(i, {p: c for c, p in terms}, fr)
                    bode[f"{i}->{o}"] = {"f_Hz": fr, "gain_dB": [float(20 * np.log10(abs(x))) if abs(x) > 0 else None for x in g],
                                         "phase_deg": [float(x) for x in np.degrees(np.unwrap(np.angle(g)))]}
            lin["bode"] = bode
        out["linearization"] = lin
    else:
        t = np.linspace(t0, t1, n + 1)
        vals = r.simulate(t)
        series = {}
        for oname, (terms, _) in tr.outputs.items():
            y = np.zeros_like(t)
            for c, p in terms:
                y = y + c * vals[p]
            series[oname] = y
        out["outputs"] = outputs(t, series, units)
    out["reliable"] = True
    out["checks"] = {"reference_only": {"ok": True, "note": "solver ref: no OpenModelica run"}}
    return out
