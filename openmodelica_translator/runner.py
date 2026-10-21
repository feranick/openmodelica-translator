"""runner.py — one study end to end: validate, translate, build (or reuse), run, read, check, write the result.
Version: 2026.10.21.18

run_study(name, study, folder, solver, timeout, cancel) -> the result dict, also written atomically to
<folder>/<name>.json. solver: "omc" (OpenModelica) or "ref" (the reference implementation only: linear
networks, no OpenModelica; for tests and machines without it).
Every run: outputs (from the result file), derived quantities, steady values (steady analysis), and the checks:
balance, initialisation, solver, events, every applied value read back, finite outputs, the energy balance,
the reference implementation on the same time points (linear networks; eigenvalues for linearize), and with
converge: true a second run at tolerance / 10. reliable is false when any check fails; expect only sets passed.
A study with a sweep runs every point (sweep.py) and reports a table.
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


def execution(procs_requested=None, procs_used=1, limits=None) -> dict:
    kind = os.environ.get("OMCT_EXECUTION") or ("slurm" if os.environ.get("SLURM_JOB_ID") else "local")
    if procs_requested is None:
        procs_requested = int(os.environ.get("OMCT_PROCS_REQUESTED") or os.environ.get("SLURM_CPUS_PER_TASK") or 1)
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
        if cancel.exists():
            res = {"ok": False, "state": "cancelled", "why": "cancelled before it started"}
        else:
            res = _run(name, study, folder, solver, timeout, cancel)
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "why": f"internal error: {type(exc).__name__}: {exc}",
               "trace": traceback.format_exc()[-3000:]}
    res.setdefault("name", name)
    res["translator"] = __version__
    res.setdefault("seconds", {})
    res["seconds"]["total"] = round(time.perf_counter() - t_start, 3)
    used = (res.get("sweep") or {}).get("procs", 1)
    res.setdefault("execution", execution(procs_used=used, limits={
        "timeout_s": timeout, "mem": (study.get("run") or {}).get("mem") or os.environ.get("OMCT_SLURM_MEM"),
        "from_study": sorted((study.get("run") or {}).keys())}))
    if res.get("ok") is False:
        res.setdefault("reliable", False)
        res.setdefault("unreliable_because", [res.get("why", "failed")])
    elif study.get("images"):
        try:
            if res.get("sweep"):
                from .sweep import plot
                res["images"] = plot(folder, res)
            else:
                from .render import render
                res["images"] = render(folder, res, True)
        except Exception as exc:  # noqa: BLE001
            res.setdefault("warnings", []).append(f"pictures failed: {type(exc).__name__}: {exc}")
    _atomic(folder / f"{name}.json", res)
    if res.get("ok") and not res.get("sweep") and (res.get("model_info") or {}).get("hash"):
        from .executor import record_study
        record_study(res["model_info"]["hash"], res["seconds"]["total"])
    return res


def _level_si(units):
    from .units import to_si

    def f(d):
        u = units.get(d["signal"], "1") or "1"
        return to_si(d["level"], u, absolute_temperature=(u == "K"))[0]
    return f


def _post(out, study, tr, t, get, units, solver, folder=None):
    """Outputs, derived, steady, energy; get(path) -> series of a Modelica variable. The full output series go to
    <folder>/series.npz (for pictures made later)."""
    from . import energy as E
    from .post import derived, outputs, steady

    an = study["analysis"]
    vals = {}
    for oname, (terms, _) in tr.outputs.items():
        y = None
        for c, path in terms:
            y = c * get(path) if y is None else y + c * get(path)
        vals[oname] = np.asarray(y, float)
    out["outputs"] = outputs(t, vals, units)
    if folder is not None:
        from .render import save_state
        save_state(Path(folder), t, vals, units)
        out["files"]["series"] = str(Path(folder) / "series.npz")
    fin = all(o["finite"] for o in out["outputs"].values())
    out["checks"]["finite"] = {"ok": fin}
    if not fin:
        out["ok"] = False
        out["why"] = "an output is not finite"
        return vals
    if study.get("derived"):
        out["derived"] = derived(t, vals, study["derived"], float(an.get("start_time", 0.0)), _level_si(units))
    if an["kind"] == "steady":
        band = float(an.get("band", 1e-4))
        out["steady"] = {k: steady(t, y, band) for k, y in vals.items()}
        late = [k for k, v in out["steady"].items() if not v["reached"]]
        if late:
            out["warnings"].append(f"not steady by max_time (band {band:g}): {', '.join(late)}; raise analysis.max_time")
    try:
        out["checks"]["energy"] = E.balance(E.plan(tr.net), t, get)
    except KeyError as exc:
        out["checks"]["energy"] = {"ok": True, "covered": False, "why": f"not recorded: {exc}"}
    return vals


def allowed(y, lim, tol) -> float:
    """The allowed error relative to an output's range: lim, or what the solver's relative tolerance allows on
    the output's size (10 tol max|y|): an absolute temperature of 300 K rising by 1 K is only accurate to ~3e-4 K
    at tolerance 1e-6, 3e-4 of its range."""
    y = np.asarray(y, float)
    if y.size == 0:
        return lim
    rng = float(np.max(y) - np.min(y))
    mag = float(np.max(np.abs(y)))
    return float(max(lim, 10 * tol * mag / rng)) if rng > 0 else lim


def _reference(out, study, tr, t, vals, tol):
    """The reference implementation on the same (non-event) time points."""
    from .post import compare, not_events
    from .ref import RefError

    r = tr.ref
    if r is None or not r.exact:
        out["checks"]["reference"] = {"ok": True, "covered": False,
                                      "why": "nonlinear: " + "; ".join(r.not_exact) if r is not None else "escape hatch"}
        return
    m = not_events(t)
    tarr = np.asarray(t, float)
    for b in r.breaks(float(tarr[0]), float(tarr[-1]), ends=True):     # where sources step: either side is right
        m &= np.abs(tarr - b) > 1e-9 * max(1.0, abs(b))
    idx = np.nonzero(m)[0]
    if idx.size > 1000:                      # at most 1000 points: the reference's cost grows with them
        keep = np.unique(np.linspace(0, idx.size - 1, 1000).round().astype(int))
        m = np.zeros_like(m)
        m[idx[keep]] = True
    tt = tarr[m]
    try:
        rv = r.simulate(tt, t0=float(tarr[0]))
    except (RefError, ValueError, np.linalg.LinAlgError) as exc:
        out["checks"]["reference"] = {"ok": True, "covered": False, "why": f"reference failed: {exc}"}
        return
    lim = max(1e-4, 100 * tol)
    errs, lims, ok = {}, {}, True
    for oname, (terms, _) in tr.outputs.items():
        y = None
        for c, path in terms:
            y = c * rv[path] if y is None else y + c * rv[path]
        errs[oname] = compare(vals[oname][m], y)
        lims[oname] = allowed(y, lim, tol)
        ok = ok and errs[oname] <= lims[oname]
    worst = max(errs.values()) if errs else 0.0
    out["checks"]["reference"] = {"ok": ok, "covered": True, "max_error_rel": worst, "tol": lim,
                                  "per_output": errs, "tol_per_output": lims}


def _ref_eigen(out, tr):
    r = tr.ref
    if r is None or not r.exact or getattr(r, "events", None):
        out["checks"]["reference"] = {"ok": True, "covered": False,
                                      "why": "piecewise-linear elements (the linearisation depends on their mode)"
                                      if r is not None and getattr(r, "events", None) else "nonlinear or escape hatch"}
        return
    om = [complex(*e) for e in out["linearization"]["eigenvalues"]]
    rf = list(r.eigenvalues())
    if len(om) != len(rf):
        out["checks"]["reference"] = {"ok": False, "covered": True,
                                      "why": f"{len(om)} eigenvalues from OpenModelica, {len(rf)} from the reference"}
        return
    scale = max([abs(x) for x in rf] + [1.0])
    worst, left = 0.0, list(rf)
    for lam in om:
        j = int(np.argmin([abs(lam - x) for x in left]))
        worst = max(worst, abs(lam - left.pop(j)) / scale)
    out["checks"]["reference"] = {"ok": worst <= 1e-5, "covered": True, "max_eigenvalue_error_rel": worst, "tol": 1e-5}


def _verdict(out, study):
    from .post import check_expect
    if study.get("expect"):
        out["expect"] = check_expect(out, study["expect"])
        out["passed"] = all(e["passed"] for e in out["expect"])
    out["reliable"] = bool(out["ok"]) and all(c.get("ok", True) for c in out.get("checks", {}).values())
    if not out["reliable"]:
        out["unreliable_because"] = [k for k, c in out.get("checks", {}).items() if not c.get("ok", True)] or \
                                    [out.get("why", "failed")]
    return out


def _run(name, study, folder, solver, timeout, cancel) -> dict:
    from . import energy as E
    from . import modelica as M
    from .format import validate_study

    v = validate_study(study)
    if not v["ok"]:
        return {"ok": False, "why": "invalid study", "errors": v["errors"], "warnings": v["warnings"]}
    warnings = list(v["warnings"])
    if study.get("sweep"):
        from .sweep import run_sweep
        res = run_sweep(name, study, folder, solver, timeout, cancel, run_study)
        res["warnings"] = list(dict.fromkeys(warnings + res.get("warnings", [])))
        if study.get("expect"):
            from .post import check_expect
            res["expect"] = check_expect(res, study["expect"])
            res["passed"] = all(e["passed"] for e in res["expect"])
        return res
    an = study["analysis"]
    seconds = {}
    t = time.perf_counter()
    tr = M.escape_translation(study) if "modelica" in study else M.translate(study, name)
    seconds["translate"] = round(time.perf_counter() - t, 3)
    (folder / "model.mo").write_text(tr.text)
    files = {"model": str(folder / "model.mo")}
    out = {"ok": True, "warnings": warnings, "files": files, "seconds": seconds, "solver": solver,
           "inputs": tr.net.inputs if tr.net is not None else {}, "fixed_initial_values": tr.fixed, "checks": {}}
    units = {k: u for k, (_, u) in tr.outputs.items()}
    if solver == "ref":
        out["_folder"] = str(folder)
        res = _run_ref(study, tr, out, units)
        res.pop("_folder", None)
        return _verdict(res, study)
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
    # values the compiled model cannot take at run time (isValueChangeable false in _init.xml): fine when the
    # build has the same value compiled in; otherwise a variant build with these values in its key
    run_overrides = dict(tr.overrides)
    ch = b.get("changeable") or {}
    fixed = sorted(k for k in tr.overrides if ch.get(k) is False)
    if fixed:
        comp = b.get("compiled_values") or {}
        if any(comp.get(k) != tr.overrides[k] for k in fixed):
            b = omc.build(tr, timeout=timeout, cancel=cancel, variant={k: tr.overrides[k] for k in fixed})
            seconds["build_variant"] = b.get("seconds")
            out["model_info"].update({"hash": b.get("hash"), "cache_hit": b.get("cache_hit"), "variant": fixed})
            if not b.get("ok"):
                out.update({"ok": False, "why": "the model did not build", "errors": b.get("errors", [])})
                return out
            comp = b.get("compiled_values") or {}
        still = [k for k in fixed if comp.get(k) != tr.overrides[k]]
        if still:
            out.update({"ok": False, "why": "values the compiled model cannot change",
                        "errors": [f"{k} is fixed in the compiled model at {comp.get(k)!r}, not {tr.overrides[k]!r}"
                                   for k in still]})
            return out
        for k in fixed:
            run_overrides.pop(k)
        warnings.append(f"compiled into the model (OpenModelica cannot change them at run time; another value "
                        f"rebuilds it): {', '.join(fixed)}")
    t0, t1, n, tol, method = _times(an)
    lin = an["kind"] == "linearize"
    pl = E.plan(tr.net)
    filt = None if lin else M.variable_filter(tr, extra=(pl or {}).get("paths", ()))
    sim = simrun.simulate(b["dir"], folder, run_overrides, start=t0, stop=t1 if not lin else max(t1, t0),
                          intervals=n, tolerance=tol, method=method, variable_filter=filt,
                          timeout=timeout, linearize_at=t1 if lin else None, cancel=cancel)
    seconds["simulate"] = sim["seconds"]
    if sim["ok"] and not lin:
        from .executor import record_run
        record_run(b["dir"], sim["seconds"], n)
    out["files"].update(sim["files"])
    warnings += sim["warnings"]
    out["checks"].update({"balance": {"ok": True, **(b.get("check") or {})},
                          "initialization": {"ok": sim["ok"] or sim["init_homotopy_steps"] is not None,
                                             "homotopy_steps": sim["init_homotopy_steps"]},
                          "solver": {"ok": sim["ok"], **sim["stats"]}})
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
            try:
                vals = _post(out, study, tr, r.time, r.series, units, solver, folder)
            except KeyError as exc:
                out.update({"ok": False, "why": f"{exc} is not in the result file"})
                return out
            if not out["ok"]:
                return out
            if "modelica" not in study:
                _reference(out, study, tr, r.time, vals, tol)
            if study.get("converge"):
                _converge(out, b, folder, run_overrides, tr, r, vals, t0, t1, n, tol, method, filt, timeout, cancel)
    if lin:
        from .linear import analyse, bode_freqs, read
        L = read(sim["linear"])
        out["linearization"] = analyse(L, bode_freqs(an.get("bode")))
        if "modelica" not in study:
            _ref_eigen(out, tr)
    return _verdict(out, study)


def _converge(out, b, folder, overrides, tr, r, vals, t0, t1, n, tol, method, filt, timeout, cancel):
    """A second run at tolerance / 10; outputs compared on the first run's non-event points."""
    from . import simrun
    from .matfile import Result
    from .post import combine, not_events

    sim = simrun.simulate(b["dir"], folder, overrides, start=t0, stop=t1, intervals=n, tolerance=tol / 10,
                          method=method, variable_filter=filt, timeout=timeout, cancel=cancel, tag="_converge")
    if not sim["ok"]:
        out["checks"]["converge"] = {"ok": False, "why": "the run at tolerance/10 failed: " + "; ".join(sim["errors"])}
        return
    r2 = Result(sim["result"])
    m1, m2 = not_events(r.time), not_events(r2.time)
    lim = max(1e-6, 100 * tol)
    diffs, lims, ok = {}, {}, True
    for oname, (terms, _) in tr.outputs.items():
        y2 = combine(r2, terms)
        yi = np.interp(r.time[m1], r2.time[m2], y2[m2])
        y1 = vals[oname][m1]
        rng = float(np.max(y1) - np.min(y1)) or max(float(np.max(np.abs(y1))), 1e-300)
        diffs[oname] = float(np.max(np.abs(y1 - yi)) / rng) if y1.size else 0.0
        lims[oname] = allowed(y1, lim, tol) if y1.size else lim
        ok = ok and diffs[oname] <= lims[oname]
    worst = max(diffs.values()) if diffs else 0.0
    out["checks"]["converge"] = {"ok": ok, "tolerance": tol / 10, "max_change_rel": worst, "tol": lim,
                                 "per_output": diffs, "tol_per_output": lims}


def _run_ref(study, tr, out, units) -> dict:
    """The reference implementation alone (no OpenModelica)."""
    r = tr.ref
    if r is None:
        out.update({"ok": False, "why": "the escape hatch needs OpenModelica (no reference)"})
        return out
    if not r.exact:
        out.update({"ok": False, "why": "the reference implementation covers linear networks only: " + "; ".join(r.not_exact)})
        return out
    an = study["analysis"]
    t0, t1, n, tol, method = _times(an)
    out["checks"]["reference_only"] = {"ok": True, "note": "solver ref: no OpenModelica run"}
    if an["kind"] == "linearize" and r.events:
        out.update({"ok": False, "why": "the reference does not linearise piecewise-linear elements: use OpenModelica"})
        return out
    if an["kind"] == "linearize":
        from .linear import bode_freqs, modes
        ev = r.eigenvalues()
        _, md = modes(np.diag(ev) if len(ev) else np.zeros((0, 0)), [f"mode{i}" for i in range(len(ev))])
        osc = sorted((m_ for m_ in md if m_["kind"] == "oscillatory"), key=lambda m_: m_["f_n_Hz"])
        lin = {"eigenvalues": [[float(x.real), float(x.imag)] for x in ev], "outputs": list(tr.outputs),
               "inputs": sorted(tr.net.free_inputs), "modes": [{k: v for k, v in m_.items() if k != "dominant_states"}
                                                                for m_ in md],
               "natural_frequencies_Hz": [m_["f_n_Hz"] for m_ in osc], "damping_ratios": [m_["zeta"] for m_ in osc],
               "time_constants_s": sorted(m_["tau_s"] for m_ in md if m_["kind"] == "real")}
        dc = {}
        for o, (terms, _) in tr.outputs.items():
            dc[o] = {}
            for i in lin["inputs"]:
                try:
                    g0 = r.freq_response(i, {p: c for c, p in terms}, [0.0])[0]
                    dc[o][i] = float(g0.real) if np.isfinite(g0) and abs(g0) < 1e300 else None
                except (np.linalg.LinAlgError, KeyError):
                    dc[o][i] = None
        lin["dc_gain"] = dc
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
        tt = np.linspace(t0, t1, n + 1)
        vals = r.simulate(tt)
        _post(out, study, tr, tt, vals.__getitem__, units, "ref", out.get("_folder"))
    return out
