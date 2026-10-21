"""linear.py — OpenModelica's linearized_model.mo, and what follows from A, B, C, D.
Version: 2026.10.21.2

read(path) -> {states, inputs, outputs, x0, u0, A, B, C, D} (names without the x_ / u_ / y_ prefixes; our
top-level names omct_u_<name> / omct_y_<name> become <name>). Format measured on mochi (1.27.1): Modelica
matrices '[a, b; c, d]' with 16 digits, 'zeros(0)' for empty vectors, "Real 'x_C.v' = x[1];".
analyse(lin, freqs) -> eigenvalues, modes (f_n_Hz, zeta, tau_s, dominant states), dc_gain, bode.
"""
from __future__ import annotations

import math
import re

import numpy as np


def _matrix(text: str, name: str, rows: int, cols: int) -> np.ndarray:
    m = re.search(rf"parameter Real {name}\[[^\]]*\]\s*=\s*(\[.*?\]|zeros\([^)]*\))\s*;", text, re.S)
    if not m or rows == 0 or cols == 0:
        return np.zeros((rows, cols))
    body = m.group(1).strip()
    if body.startswith("zeros"):
        return np.zeros((rows, cols))
    out = [[float(x) for x in r.split(",") if x.strip()] for r in body.strip("[]").split(";")]
    a = np.array(out, float)
    return a.reshape(rows, cols)


def _vector(text: str, name: str, n: int) -> np.ndarray:
    m = re.search(rf"parameter Real {name}\[[^\]]*\]\s*=\s*(\{{.*?\}}|zeros\([^)]*\))\s*;", text, re.S)
    if not m or n == 0 or m.group(1).startswith("zeros"):
        return np.zeros(n)
    return np.array([float(x) for x in m.group(1).strip("{}").split(",") if x.strip()], float)


def _strip(name: str) -> str:
    for p in ("omct_u_", "omct_y_"):
        if name.startswith(p):
            return name[len(p):]
    return name


def read(path) -> dict:
    text = open(path).read()
    dims = {k: int(re.search(rf"parameter Integer {k} = (\d+)", text).group(1)) for k in ("n", "m", "p")}
    n, m, p = dims["n"], dims["m"], dims["p"]
    names = {"x": [], "u": [], "y": []}
    for kind, nm, i in re.findall(r"Real '([xuy])_(.*?)' = [xuy]\[(\d+)\];", text):
        names[kind].append((int(i), _strip(nm)))
    order = {k: [nm for _, nm in sorted(v)] for k, v in names.items()}
    return {"states": order["x"], "inputs": order["u"], "outputs": order["y"],
            "x0": _vector(text, "x0", n), "u0": _vector(text, "u0", m),
            "A": _matrix(text, "A", n, n), "B": _matrix(text, "B", n, m),
            "C": _matrix(text, "C", p, n), "D": _matrix(text, "D", p, m)}


def modes(A: np.ndarray, states: list[str]) -> tuple[np.ndarray, list[dict]]:
    if A.size == 0:
        return np.zeros(0, complex), []
    w, V = np.linalg.eig(A)
    order = np.lexsort((w.imag, np.abs(w)))
    w, V = w[order], V[:, order]
    out = []
    for k, lam in enumerate(w):
        mag = abs(lam)
        if lam.imag < -1e-9 * max(1.0, mag):          # one mode per conjugate pair
            continue
        v = np.abs(V[:, k])
        v = v / v.max() if v.max() > 0 else v
        dom = [states[i] for i in np.argsort(-v)[:3] if v[i] > 0.1]
        mode = {"eigenvalue": [float(lam.real), float(lam.imag)], "dominant_states": dom}
        if mag < 1e-12:
            mode.update({"kind": "integrator (zero eigenvalue)"})
        elif abs(lam.imag) > 1e-9 * mag:
            mode.update({"kind": "oscillatory", "f_n_Hz": mag / (2 * math.pi), "f_d_Hz": abs(lam.imag) / (2 * math.pi),
                         "zeta": -lam.real / mag})
        else:
            mode.update({"kind": "real", "tau_s": -1 / lam.real if lam.real != 0 else None,
                         "stable": bool(lam.real < 0)})
        out.append(mode)
    return w, out


def freq_response(lin: dict, freqs) -> np.ndarray:
    """G(j 2 pi f): shape (len(freqs), p, m)."""
    A, B, C, D = lin["A"], lin["B"], lin["C"], lin["D"]
    n = A.shape[0]
    out = []
    for f in np.atleast_1d(freqs):
        s = 2j * math.pi * f
        if n:
            X = np.linalg.solve(s * np.eye(n) - A, B)
            out.append(C @ X + D)
        else:
            out.append(D.astype(complex))
    return np.array(out)


def analyse(lin: dict, freqs=None) -> dict:
    w, md = modes(lin["A"], lin["states"])
    res = {"states": lin["states"], "inputs": lin["inputs"], "outputs": lin["outputs"],
           "A": lin["A"].tolist(), "B": lin["B"].tolist(), "C": lin["C"].tolist(), "D": lin["D"].tolist(),
           "eigenvalues": [[float(x.real), float(x.imag)] for x in w], "modes": md,
           "stable": bool(np.all(w.real < 0)) if w.size else True}
    A, B, C, D = lin["A"], lin["B"], lin["C"], lin["D"]
    try:
        if A.size and abs(np.linalg.det(A)) > 0 and np.linalg.cond(A) < 1e12:
            dc = D - C @ np.linalg.solve(A, B)
        elif not A.size:
            dc = D
        else:
            dc = None
    except np.linalg.LinAlgError:
        dc = None
    res["dc_gain"] = None if dc is None else {o: {i: float(dc[a, b]) for b, i in enumerate(lin["inputs"])}
                                               for a, o in enumerate(lin["outputs"])}
    if freqs is not None and len(freqs):
        G = freq_response(lin, freqs)
        bode = {}
        for a, o in enumerate(lin["outputs"]):
            for b, i in enumerate(lin["inputs"]):
                g = G[:, a, b]
                mag = np.abs(g)
                bode[f"{i}->{o}"] = {"f_Hz": [float(f) for f in freqs],
                                     "gain_dB": [float(20 * math.log10(x)) if x > 0 else None for x in mag],
                                     "phase_deg": [float(x) for x in np.degrees(np.unwrap(np.angle(g)))]}
        res["bode"] = bode
    return res


def bode_freqs(spec) -> list[float]:
    if spec is None:
        return []
    if isinstance(spec, list):
        return [float(f) for f in spec]
    return list(np.geomspace(float(spec["f_min"]), float(spec["f_max"]), int(spec.get("points", 50))))
