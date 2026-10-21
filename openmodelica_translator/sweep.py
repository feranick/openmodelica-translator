"""sweep.py — one study, many value sets: one build, one run per point, a table.
Version: 2026.10.21.6

run_sweep(name, study, folder, solver, timeout, cancel, run_point) -> the study's result with
    sweep: {points, ok_points, reliable_points, params, columns, table: [{point, params, values, ok, reliable,
            why}], failed}
Each point is the study with the point's values set (grid: every combination; zip: element by element), run in
<folder>/points/pNNNN/ by run_point (runner._run_point). The first point runs alone: it builds the model (or
finds it in the cache); the others reuse that build and run in threads, OMCT_PROCS or the study's run.procs at
a time (the work is the simulation executable, a separate process, so threads suffice; worker processes of
the pool cannot start processes of their own). A point is reported as it finishes (points/pNNNN/pNNNN.json),
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
    for key, v in values.items():
        comp, _, p = key.partition(".")
        st["components"][comp][p] = v
    return st


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
    if len(pts) > 1:
        with ThreadPoolExecutor(max_workers=max(1, min(n, len(pts) - 1))) as ex:
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
        if not r.get("ok") or not r.get("reliable"):
            row["why"] = r.get("why") or r.get("unreliable_because")
            failed.append(i)
        table.append(row)
    ok_n = sum(1 for r in table if r["ok"])
    rel_n = sum(1 for r in table if r["reliable"])
    out = {"ok": ok_n > 0, "reliable": rel_n == len(pts), "solver": solver,
           "sweep": {"points": len(pts), "ok_points": ok_n, "reliable_points": rel_n, "params": list(sw["params"]),
                     "mode": sw.get("mode", "grid"), "procs": n, "columns": cols, "table": table, "failed": failed,
                     "seconds": round(time.perf_counter() - t0, 3)},
           "warnings": list(first.get("warnings") or []), "files": {"points": str(folder / "points")},
           "model_info": first.get("model_info"), "inputs": first.get("inputs")}
    builds = {(r.get("model_info") or {}).get("hash") for r in results.values() if r.get("model_info")}
    out["sweep"]["builds"] = len(builds)
    if not out["reliable"]:
        out["unreliable_because"] = [f"{len(pts) - rel_n} of {len(pts)} points not reliable: {failed[:10]}"]
    if not out["ok"]:
        out["why"] = f"no point ran: {first.get('why')}"
        out["errors"] = first.get("errors")
    return out


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
        return float(v.get("value"))
    try:
        from .units import _NUM
        return float(_NUM.match(str(v)).group(1))
    except Exception:  # noqa: BLE001
        return float("nan")
