"""sweep.py — one study, many value sets: one build, one run per point, a table.
Version: 2026.10.26.2

run_sweep(name, study, folder, solver, timeout, cancel, run_point) -> the study's result with
    sweep: {points, ok_points, reliable_points, params, columns, table: [{point, params, values, ok, reliable,
            why}], failed}
Each point is the study with the point's values set (grid: every combination; zip: element by element), run in
<folder>/points/pNNNN/ by run_point (runner._run_point). The first point runs alone: it builds the model (or
finds it in the cache); the others reuse that build and run in threads, OMCT_PROCS or the study's run.procs at
a time: in worker processes when the sweep runs in a normal process (a SLURM job, the CLI), in threads when it
runs inside the server's daemonic pool (which cannot start processes). A point is reported as it finishes (points/pNNNN/pNNNN.json),
so progress counts them.
"""
from __future__ import annotations

import copy
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .format import sweep_points
from .post import get_path


def point_study(study: dict, values: dict) -> dict:
    st = copy.deepcopy(study)
    st.pop("sweep", None)
    st.pop("images", None)
    st.pop("expect", None)
    if "modelica" in st:                 # the escape hatch: the point's values are Modelica paths, applied at run time
        st["modelica"] = dict(st["modelica"], values={**(st["modelica"].get("values") or {}), **values})
        return st
    for key, v in values.items():
        comp, _, p = key.partition(".")
        st["components"][comp][p] = v
    return st


INDEPENDENT = ("reference", "cross_solver", "invariants", "converge")


def _point_checks(r: dict) -> dict:
    """A point's independent checks in a line: {check: {ok, max_error_rel | covered: false, retried}}, so a sweep
    shows that they ran (2026-10-26: the custom models' cross_solver ran on every point but only the point files
    said so)."""
    out = {}
    for k in INDEPENDENT:
        c = (r.get("checks") or {}).get(k)
        if not c:
            continue
        d = {"ok": bool(c.get("ok", True))}
        if c.get("covered") is False:
            d["covered"] = False
        for f in ("max_error_rel", "max_change_rel"):
            if f in c:
                d[f] = c[f]
        if "retry" in c:
            d["retried_at_tolerance"] = c["retry"].get("tolerance")
        out[k] = d
    return out


def _checks_summary(table: list) -> dict:
    """{check: {points, failed, not_covered, retried, max_error_rel}} over the sweep."""
    out = {}
    for row in table:
        for k, d in (row.get("checks") or {}).items():
            s = out.setdefault(k, {"points": 0, "failed": 0, "not_covered": 0, "retried": 0})
            s["points"] += 1
            s["failed"] += not d["ok"]
            s["not_covered"] += d.get("covered") is False
            s["retried"] += "retried_at_tolerance" in d
            e = d.get("max_error_rel", d.get("max_change_rel"))
            if e is not None:
                s["max_error_rel"] = max(s.get("max_error_rel", 0.0), e)
    return out


def default_report(study: dict, first: dict) -> list[str]:
    cols = []
    kind = study["analysis"]["kind"]
    if kind == "linearize":
        cols += [f"linearization.{k}" for k in ("natural_frequencies_Hz", "damping_ratios", "time_constants_s")]
    elif kind == "steady":
        cols += [f"steady.{o}.value" for o in (first.get("steady") or {})]
    else:
        cols += [f"outputs.{o}.final" for o in (first.get("outputs") or {})]
    for name, d in (first.get("derived") or {}).items():
        for k, v in d.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in ("t0", "level", "band", "initial"):
                cols.append(f"derived.{name}.{k}")
    return cols


def procs(study: dict) -> int:
    p = (study.get("run") or {}).get("procs")
    return int(p) if p else max(1, int(os.environ.get("OMCT_PROCS", "1") or 1))


def run_sweep(name, study, folder: Path, solver, timeout, cancel, run_point) -> dict:
    sw = study["sweep"]
    pts = sweep_points(sw)
    folder = Path(folder)
    (folder / "points").mkdir(parents=True, exist_ok=True)
    (folder / "sweep.json").write_text(json.dumps({"points": len(pts), "params": list(sw["params"])}))
    t0 = time.perf_counter()
    results: dict[int, dict] = {}

    def one(i):
        if cancel.exists():
            return i, {"ok": False, "state": "cancelled", "why": "cancelled"}
        pn = f"p{i:04d}"
        r = run_point(pn, point_study(study, pts[i]), folder / "points" / pn, solver, timeout, cancel)
        return i, r

    if pts:
        i, r = one(0)
        results[i] = r
    n = procs(study)
    kind = "threads"
    if len(pts) > 1:
        workers = max(1, min(n, len(pts) - 1))
        import multiprocessing as mp
        if workers > 1 and not mp.current_process().daemon:
            # processes: each point's checks (reading, the reference implementation) are Python and would
            # serialise on the GIL in threads (measured on mochi: 120 points on 30 threads took 200 s)
            from concurrent.futures import ProcessPoolExecutor
            kind = "processes"
            jobs = [(f"p{i:04d}", point_study(study, pts[i]), str(folder / "points" / f"p{i:04d}"), solver, timeout,
                     str(cancel)) for i in range(1, len(pts))]
            with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
                for i, r in zip(range(1, len(pts)), ex.map(_point_job, jobs)):
                    results[i] = r
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for i, r in ex.map(one, range(1, len(pts))):
                    results[i] = r
    first = next((r for r in results.values() if r.get("ok")), results.get(0, {}))
    cols = sw.get("report") or default_report(study, first)
    from .network import build
    table, failed = [], []
    for i in range(len(pts)):
        try:
            net = build(point_study(study, pts[i]))
            si = {}
            for key in pts[i]:
                comp, _, p = key.partition(".")
                c = net.comps[comp]
                si[key] = c["params"].get(p) if p in c["params"] else None
                if si[key] is None:
                    e = next((e for e, d in net.elems.items() if d["comp"] == comp and p in d["inits"]), None)
                    si[key] = net.elems[e]["inits"][p][0] if e else None
        except Exception:  # noqa: BLE001
            si = {}
        r = results.get(i, {"ok": False, "why": "not run"})
        vals = {}
        for c in cols:
            try:
                vals[c] = get_path(r, c)
            except (KeyError, IndexError, TypeError):
                vals[c] = None
        row = {"point": i, "params": pts[i], "params_si": si, "values": vals, "ok": bool(r.get("ok")), "reliable": bool(r.get("reliable"))}
        ck = _point_checks(r)
        if ck:
            row["checks"] = ck
        if not r.get("ok") or not r.get("reliable"):
            row["why"] = r.get("why") or r.get("unreliable_because")
            failed.append(i)
        table.append(row)
    ok_n = sum(1 for r in table if r["ok"])
    rel_n = sum(1 for r in table if r["reliable"])
    out = {"ok": ok_n > 0, "reliable": rel_n == len(pts), "solver": solver,
           "sweep": {"points": len(pts), "ok_points": ok_n, "reliable_points": rel_n, "params": list(sw["params"]),
                     "mode": sw.get("mode", "grid"), "procs": n, "parallel": kind, "columns": cols, "table": table, "failed": failed,
                     "seconds": round(time.perf_counter() - t0, 3)},
           "warnings": list(first.get("warnings") or []), "files": {"points": str(folder / "points")},
           "model_info": first.get("model_info"), "inputs": first.get("inputs")}
    summ = _checks_summary(table)
    if summ:
        out["sweep"]["checks"] = summ
        # the same place as a single study's checks, so study_status shows them for sweeps too
        out["checks"] = {k: {"ok": v["failed"] == 0, "covered": v["not_covered"] < v["points"], **v} for k, v in summ.items()}
    builds = {(r.get("model_info") or {}).get("hash") for r in results.values() if r.get("model_info")}
    out["sweep"]["builds"] = len(builds)
    if not out["reliable"]:
        out["unreliable_because"] = [f"{len(pts) - rel_n} of {len(pts)} points not reliable: {failed[:10]}"]
    if not out["ok"]:
        out["why"] = f"no point ran: {first.get('why')}"
        out["errors"] = first.get("errors")
    return out


def _point_job(args):
    """One point in a worker process (a sweep run outside the daemonic pool: a SLURM job, the CLI)."""
    from .runner import run_study
    pn, st, folder, solver, timeout, cancel = args
    if Path(cancel).exists():
        return {"ok": False, "state": "cancelled", "why": "cancelled"}
    r = run_study(pn, st, Path(folder), solver, timeout, Path(cancel))
    r.pop("trace", None)
    return r


def plot(folder: Path, res: dict) -> dict:
    """Each numeric column against the first parameter (one line per value of the second, if any)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    sw = res["sweep"]
    p0 = sw["params"][0]
    p1 = sw["params"][1] if len(sw["params"]) > 1 else None
    cols = [c for c in sw["columns"] if any(isinstance(r["values"].get(c), (int, float)) for r in sw["table"])]
    if not cols:
        return {}
    fig, axes = plt.subplots(len(cols), 1, figsize=(8, 2.4 * len(cols) + 0.6), squeeze=False)
    groups = {}
    for r in sw["table"]:
        groups.setdefault(json.dumps(r["params"].get(p1)) if p1 else "", []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: _num(r["params_si"].get(p0, r["params"][p0])))
    for ax, c in zip(axes[:, 0], cols):
        for g, rows in groups.items():
            xs = [_num(r["params_si"].get(p0, r["params"][p0])) for r in rows if isinstance(r["values"].get(c), (int, float))]
            ys = [r["values"][c] for r in rows if isinstance(r["values"].get(c), (int, float))]
            if xs:
                order = sorted(range(len(xs)), key=lambda k: xs[k])
                ax.plot([xs[k] for k in order], [ys[k] for k in order], "o-", label=f"{p1} = {g}" if p1 else None)
        ax.set_ylabel(c, fontsize=8)
        ax.grid(True, alpha=0.3)
        if p1:
            ax.legend(fontsize=7)
    axes[-1, 0].set_xlabel(p0)
    fig.tight_layout()
    img = Path(folder) / "images"
    img.mkdir(exist_ok=True)
    path = img / "sweep.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return {"sweep": {"path": str(path), "what": f"reported values against {p0}"}}


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return float(v.get("value", "nan"))
    try:
        from .units import _NUM
        return float(_NUM.match(str(v)).group(1))
    except Exception:  # noqa: BLE001
        return float("nan")
