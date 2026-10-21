"""post.py — results from the simulated series: outputs, derived quantities, steady states, comparisons, expect.
Version: 2026.10.21.4

outputs(t, values, units)      {name: {unit, finite, initial, final, min, max, t_min, t_max, series: {t, y}}}
derived(t, values, specs, ...) the study's derived quantities, from the full series (not the downsampled one):
    step      rise_time_s (rise [0.1, 0.9] of the change), overshoot_pct, peak, peak_time_s (from t0),
              settling_time_s (last exit from band around final, from t0), initial, final, steady_state_error
    crossing  crossing_time_s: first crossing of level (linear interpolation), or None
    window    mean, rms, ac_rms, min, max, p2p over [t0, t1] (trapezoid integrals)
    steady    value, t_steady_s, reached (the last 10 % of the run within band x range of the final value)
compare(t, a, b)               max |a - b| / scale, where the series are compared at times that are not events
check_expect(res, expect)      [{quantity, expected, got, error, passed}] (tol relative, default 0.02; or abs)
Series may repeat a time at an event (the values before and after): the left value comes first.
"""
from __future__ import annotations

import math

import numpy as np

MAX_POINTS = 400


def _r(x):
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def downsample(t, y, n: int = MAX_POINTS):
    t, y = np.asarray(t, float), np.asarray(y, float)
    if t.size <= n:
        return t, y
    keep = set(np.linspace(0, t.size - 1, n - 8 if n > 16 else n).round().astype(int).tolist())
    keep |= {0, t.size - 1, int(np.argmin(y)), int(np.argmax(y))}
    ev = np.nonzero(np.diff(t) == 0)[0]
    for i in ev[: max(0, n // 4)]:
        keep |= {int(i), int(i) + 1}
    idx = np.array(sorted(keep))
    return t[idx], y[idx]


def outputs(t, values: dict, units: dict) -> dict:
    out = {}
    t = np.asarray(t, float)
    for name, y in values.items():
        y = np.asarray(y, float)
        fin = np.isfinite(y)
        rec = {"unit": units.get(name, ""), "finite": bool(fin.all())}
        if fin.any():
            yy = np.where(fin, y, np.nan)
            i0, i1 = int(np.nanargmin(yy)), int(np.nanargmax(yy))
            rec.update({"initial": _r(y[0]), "final": _r(y[-1]), "min": _r(y[i0]), "max": _r(y[i1]),
                        "t_min": _r(t[i0]), "t_max": _r(t[i1])})
        ts, ys = downsample(t, y)
        rec["series"] = {"t": [_r(x) for x in ts], "y": [_r(x) for x in ys]}
        out[name] = rec
    return out


def combine(result, terms) -> np.ndarray:
    """An output from the result file: sum of coef * series."""
    y = None
    for c, path in terms:
        s = c * result.series(path)
        y = s if y is None else y + s
    return y


# ---- derived quantities ---------------------------------------------------------------------------
def _cross(t, y, level, start=0, direction="any"):
    """First time at index >= start where y crosses level (linear interpolation), or None."""
    d = y - level
    for i in range(max(start, 0), len(y) - 1):
        a, b = d[i], d[i + 1]
        up = a < 0 <= b
        dn = a > 0 >= b
        if a == 0 and i == start:
            if direction == "any":
                return float(t[i])
            continue
        if (direction in ("up", "any") and up) or (direction in ("down", "any") and dn):
            if t[i + 1] == t[i] or b == a:
                return float(t[i + 1])
            return float(t[i] + (t[i + 1] - t[i]) * (-a) / (b - a))
    return None


def step(t, y, spec, t_start):
    t0 = float(spec.get("t0", t_start))
    i0 = int(np.searchsorted(t, t0, side="left"))         # the first point at t0 (before the event)
    i0 = min(i0, len(t) - 1)
    initial = float(spec["initial"]) if "initial" in spec else float(y[i0])
    final = float(spec["final"]) if "final" in spec else float(y[-1])
    delta = final - initial
    out = {"initial": initial, "final": final, "t0": t0}
    if "target" in spec:
        out["steady_state_error"] = float(spec["target"]) - final
    if abs(delta) <= 1e-12 * max(abs(initial), abs(final), 1e-300):
        out["note"] = "no change between initial and final: no step metrics"
        return out
    z = (y[i0:] - initial) / delta
    tt = t[i0:]
    lo, hi = spec.get("rise", [0.1, 0.9])
    a = _cross(tt, z, lo, 0, "up")
    b = _cross(tt, z, hi, 0, "up")
    out["rise_time_s"] = (b - a) if a is not None and b is not None else None
    k = int(np.argmax(z))
    out["peak"] = float(y[i0 + k])
    out["peak_time_s"] = float(tt[k] - t0)
    out["overshoot_pct"] = max(0.0, float(z[k] - 1.0) * 100.0)
    band = float(spec.get("band", 0.02))
    outside = np.nonzero(np.abs(z - 1.0) > band)[0]
    if outside.size == 0:
        out["settling_time_s"] = 0.0
    elif outside[-1] == len(z) - 1:
        out["settling_time_s"] = None
        out["note"] = f"not settled within {band * 100:g} % by the end of the run"
    else:
        j = int(outside[-1])
        zj, zk = z[j], z[j + 1]
        edge = 1.0 + band if zj > 1 else 1.0 - band
        tc = tt[j] + (tt[j + 1] - tt[j]) * (edge - zj) / (zk - zj) if zk != zj else tt[j + 1]
        out["settling_time_s"] = float(tc - t0)
    return out


def window(t, y, t0, t1):
    m = (t >= t0) & (t <= t1)
    tt, yy = t[m], y[m]
    if tt.size < 2 or tt[-1] <= tt[0]:
        return {"note": "fewer than two points in the window"}
    T = tt[-1] - tt[0]
    mean = float(np.trapezoid(yy, tt) / T) if hasattr(np, "trapezoid") else float(np.trapz(yy, tt) / T)
    ms = float(np.trapezoid(yy * yy, tt) / T) if hasattr(np, "trapezoid") else float(np.trapz(yy * yy, tt) / T)
    return {"mean": mean, "rms": math.sqrt(max(ms, 0.0)), "ac_rms": math.sqrt(max(ms - mean * mean, 0.0)),
            "min": float(yy.min()), "max": float(yy.max()), "p2p": float(yy.max() - yy.min()), "t0": float(t0),
            "t1": float(t1)}


def steady(t, y, band=1e-3):
    final = float(y[-1])
    rng = float(np.max(y) - np.min(y))
    scale = rng if rng > 0 else max(abs(final), 1e-300)
    out_ = np.nonzero(np.abs(y - final) > band * scale)[0]
    ts = float(t[out_[-1] + 1]) if out_.size else float(t[0])
    span = float(t[-1] - t[0])
    return {"value": final, "t_steady_s": ts - float(t[0]), "reached": bool(ts <= t[-1] - 0.1 * span), "band": band}


def derived(t, values: dict, specs: list, t_start: float, level_si=None) -> dict:
    t = np.asarray(t, float)
    out = {}
    for d in specs or []:
        y = np.asarray(values[d["signal"]], float)
        name = d.get("name", d["signal"])
        rec = out.setdefault(name, {})
        if d["kind"] == "step":
            rec.update(step(t, y, d, t_start))
        elif d["kind"] == "crossing":
            lvl = level_si(d) if level_si else float(d["level"])
            rec["crossing_time_s"] = _cross(t, y, lvl, 0, d.get("direction", "any"))
            rec["level"] = lvl
        elif d["kind"] == "window":
            rec.update(window(t, y, float(d.get("t0", t[0])), float(d.get("t1", t[-1]))))
        elif d["kind"] == "steady":
            rec.update(steady(t, y, float(d.get("band", 1e-3))))
    return out


# ---- comparisons ----------------------------------------------------------------------------------
def not_events(t) -> np.ndarray:
    """Mask of the points that are not on either side of an event (a repeated time)."""
    t = np.asarray(t, float)
    m = np.ones(t.size, bool)
    rep = np.nonzero(np.diff(t) == 0)[0]
    m[rep] = False
    m[rep + 1] = False
    return m


def compare(a, b, mask=None) -> float:
    """max |a - b| / (range of b, or its size if constant)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if mask is not None:
        a, b = a[mask], b[mask]
    if a.size == 0:
        return 0.0
    scale = float(np.max(b) - np.min(b))
    scale = scale if scale > 0 else max(float(np.max(np.abs(b))), 1e-300)
    return float(np.max(np.abs(a - b)) / scale)


# ---- expect ---------------------------------------------------------------------------------------
def get_path(res: dict, path: str):
    cur = res
    for k in path.split("."):
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        elif isinstance(cur, list) and k.lstrip("-").isdigit():
            cur = cur[int(k)]
        else:
            raise KeyError(path)
    return cur


def check_expect(res: dict, expect: list) -> list:
    out = []
    for e in expect or []:
        q = e["quantity"]
        try:
            got = float(get_path(res, q))
        except (KeyError, ValueError, IndexError, TypeError):
            out.append({"quantity": q, "expected": e["value"], "got": None, "passed": False,
                        "why": "no such result (see the result keys)"})
            continue
        v = float(e["value"])
        if "abs" in e:
            ok, err = abs(got - v) <= float(e["abs"]), abs(got - v)
        else:
            tol = float(e.get("tol", 0.02))
            err = abs(got - v) / abs(v) if v != 0 else abs(got)
            ok = err <= tol
        out.append({"quantity": q, "expected": v, "got": got, "error": err, "passed": bool(ok)})
    return out
