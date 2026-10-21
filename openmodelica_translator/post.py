"""post.py — results from the simulated series: output statistics and downsampled series.
Version: 2026.10.21.2

outputs(t, values, units) -> {name: {unit, initial, final, min, max, t_min, t_max, series: {t, y}}}
downsample(t, y, n) keeps <= n points, always the first, the last, both sides of every event (a repeated time)
and the extremes.
Derived quantities, the energy balance and the reference comparison come with milestone 3.
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
