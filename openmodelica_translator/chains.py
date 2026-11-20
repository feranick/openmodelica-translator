"""chains.py — values from the other translators' results, as study values with their provenance.
Version: 2026.11.19.14

The tools pass specs, never paths (servers may run on different machines): a value from another translator is
written into a study as {"value", "unit", "from", "what"} (units.to_si converts it and the result's `inputs`
records it). These helpers make such values from the results the other translators return:

  feat_stiffness(static, face, comp, force)   k = |F| / |mean displacement of a face| (feat: faces.<face>.mean_u.<c>, mm)
  feat_mass(result, factor)                   mass_kg (x factor: 33/140 for a cantilever's tip by Rayleigh)
  feat_frequency(modal, i)                    frequencies.<i> (Hz)
  feat_conductance(thermal, face, dT)         |heat_flow.<face>| / dT (W/K)
  b3dt_mass(goal, density)                    volume (mm3, build123d-translator check) x density (kg/m3)
  b3dt_inertia(goal, density, axis, point)    moment of inertia about an axis (kg m2) from the check's
                                              center_of_mass and inertia_mm5 (b3dt 2026.10.21.1), parallel axes
  inertia_from_props(props, density, ...)     the same from a b3dt result already in hand (units.to_si uses it
                                              for study values {"b3dt": props, "density", "axis", "point"})
  rayleigh_cantilever_hz(k, m)                sqrt(k / (33/140 m)) / 2 pi: 1.46 % above the first bending frequency
  cfdt_pump(spec, where, N_nominal)           fl_pump curve, N_nominal, power curve, rho_power from cfd-translator
                                              pump results (affinity laws to one speed, least squares)
  cfdt_resistance(spec, where)                fl_resistance a, b from total_pressure_drop at the inflow
                                              (non-negative least squares); the network scales a by density, b by
                                              viscosity to the circuit's medium

Interface keys read: feat faces.<face>.mean_u.<ux|uy|uz>, applied_force, frequencies, mass_kg, heat_flow.<face>;
build123d-translator check()[name].volume, center_of_mass (mm), inertia_mm5 (unit density, about the centre
of mass); cfd-translator (V5 FL5) results.flow.<bc>.Q / mdot (inflow, density), pump.head_m / flow_m3h /
power_W, rotating.<zone>.rpm, total_pressure_drop, and a study result's reliable (false: refused).
"""
from __future__ import annotations

import math

RAYLEIGH_CANTILEVER = 33.0 / 140.0
#  first bending mode of a clamped-free Euler-Bernoulli beam: (beta L)^2 = 1.8751^2
EB_FIRST = 1.875104068711961 ** 4


def _get(res: dict, path: str):
    from .post import get_path
    return get_path(res, path)


def feat_stiffness(static: dict, face: str = "tip", comp: str = "uz", force: float | None = None,
                   what: str = "") -> dict:
    u = float(_get(static, f"faces.{face}.mean_u.{comp}"))
    F = float(force) if force is not None else float(_get(static, "applied_force"))
    if u == 0:
        raise ValueError(f"feat: faces.{face}.mean_u.{comp} is 0: no stiffness")
    return {"value": abs(F) / abs(u), "unit": "N/mm", "from": "feat",
            "what": what or f"|F| / |faces.{face}.mean_u.{comp}| ({abs(F):g} N / {abs(u):.6g} mm)"}


def feat_mass(res: dict, factor: float = 1.0, what: str = "") -> dict:
    m = float(_get(res, "mass_kg"))
    return {"value": m * factor, "unit": "kg", "from": "feat", "what": what or f"{factor:g} x mass_kg ({m:.6g} kg)"}


def feat_frequency(modal: dict, i: int = 0) -> float:
    return float(_get(modal, f"frequencies.{i}"))


def feat_conductance(thermal: dict, face: str, dT: float, what: str = "") -> dict:
    q = float(_get(thermal, f"heat_flow.{face}"))
    return {"value": abs(q) / abs(dT), "unit": "W/K", "from": "feat",
            "what": what or f"|heat_flow.{face}| / dT ({abs(q):.6g} W / {abs(dT):g} K)"}


def b3dt_mass(goal: dict, density: float, what: str = "") -> dict:
    r = _b3dt_check(goal)
    v = float(r["volume"])
    return {"value": v * 1e-9 * density, "unit": "kg", "from": "b3dt",
            "what": what or f"volume {v:.6g} mm3 x density {density:g} kg/m3"}


def _b3dt_check(goal: dict) -> dict:
    from build123d_translator import api as B
    r = B.check({"part": goal})["part"]
    if not r.get("buildable"):
        raise ValueError(f"b3dt: the goal does not build: {r.get('why')}")
    return r


def inertia_from_props(props: dict, density: float, axis=(0.0, 0.0, 1.0), point=None) -> tuple[float, str]:
    """(J [kg m2], how) from b3dt mass properties {volume, center_of_mass, inertia_mm5}: density x (n^T I n +
    V d^2) x 1e-15, with I the unit-density inertia about the centre of mass (mm^5), V the volume (mm3) and d the
    distance (mm) of the centre of mass from the axis through point (default: the centre of mass)."""
    try:
        I = [[float(x) for x in row] for row in props["inertia_mm5"]]
        c = [float(x) for x in props["center_of_mass"]]
        V = float(props["volume"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("b3dt mass properties need volume, center_of_mass and inertia_mm5 (build123d-translator "
                         "2026.10.21.1 or later: check_goals or build_parts results)") from None
    if len(I) != 3 or any(len(r) != 3 for r in I) or len(c) != 3:
        raise ValueError("inertia_mm5 must be 3x3 and center_of_mass 3 numbers")
    n = [float(x) for x in axis]
    norm = math.sqrt(sum(x * x for x in n))
    if len(n) != 3 or norm == 0:
        raise ValueError("axis must be 3 numbers, not all zero")
    n = [x / norm for x in n]
    p = c if point is None else [float(x) for x in point]
    j_c = sum(n[i] * I[i][k] * n[k] for i in range(3) for k in range(3))
    d = [c[i] - p[i] for i in range(3)]
    along = sum(d[i] * n[i] for i in range(3))
    d2 = max(sum(x * x for x in d) - along * along, 0.0)
    where = "its centre of mass" if point is None else f"({p[0]:g}, {p[1]:g}, {p[2]:g}) mm"
    how = (f"about the axis ({n[0]:g}, {n[1]:g}, {n[2]:g}) through {where}: density {density:g} kg/m3 x "
           f"({j_c:.6g} + {V:.6g} x {d2:.6g}) mm^5")
    return float(density) * (j_c + V * d2) * 1e-15, how


def b3dt_inertia(goal: dict, density: float, axis=(0.0, 0.0, 1.0), point=None, what: str = "") -> dict:
    """J about the axis through point [mm] (default: the centre of mass), from build123d-translator's check."""
    J, how = inertia_from_props(_b3dt_check(goal), density, axis, point)
    return {"value": J, "unit": "kg.m2", "from": "b3dt", "what": what or "inertia " + how}


def rayleigh_cantilever_hz(k_N_per_m: float, m_kg: float) -> float:
    return math.sqrt(k_N_per_m / (RAYLEIGH_CANTILEVER * m_kg)) / (2 * math.pi)


def rayleigh_ratio() -> float:
    """Rayleigh's frequency with the static tip stiffness over the exact first bending frequency."""
    return math.sqrt((3.0 / RAYLEIGH_CANTILEVER) / EB_FIRST)


def available(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


# ---- cfd-translator (V5 FL5): pump curves and resistances for the Modelica.Fluid kinds ------------------------------
CFDT_KEYS = {"results", "mu", "what"}
RPM = 2 * math.pi / 60


class ChainError(ValueError):
    pass


def quad_points(pts, line_as_two: bool = False) -> tuple[list, dict]:
    """Least squares y = c0 + c1 V (+ c2 V^2) through [[V, y], ...] (a line from 2 flows, else a quadratic), written
    as points at the data's smallest, middle and largest V (a line as 2 points with line_as_two): (points, fit).
    A quadratic through 3 points is the same quadratic, so MSL's quadraticFlow / quadraticPower reproduce it."""
    import numpy as np
    V = np.array([float(p[0]) for p in pts])
    y = np.array([float(p[1]) for p in pts])
    if len(np.unique(V)) < 2:
        raise ChainError("at least 2 different flows are needed")
    deg = min(2, len(np.unique(V)) - 1)
    c = np.polyfit(V, y, deg)
    fit = np.polyval(c, V)
    lo, hi = float(V.min()), float(V.max())
    xs = [lo, hi] if (deg == 1 and line_as_two) else [lo, (lo + hi) / 2, hi]
    out = [[x, float(np.polyval(c, x))] for x in xs]
    scale = float(np.max(np.abs(y))) or 1.0
    info = {"degree": deg, "coefficients": [float(x) for x in c[::-1]],
            "rms_residual_rel": float(np.sqrt(np.mean((fit - y) ** 2)) / scale)}
    return out, info


def _cfdt_items(spec, where: str) -> tuple[list, dict]:
    if isinstance(spec, list):
        spec = {"results": spec}
    if not isinstance(spec, dict) or not isinstance(spec.get("results"), list) or not spec["results"]:
        raise ChainError(f"{where}: a list of cfd-translator study results (or {{results: [...], mu, what}})")
    bad = set(spec) - CFDT_KEYS
    if bad:
        raise ChainError(f"{where}: unknown keys {sorted(bad)} ({', '.join(sorted(CFDT_KEYS))})")
    pts = []
    for i, r in enumerate(spec["results"]):
        w = f"{where}.results[{i}]"
        if not isinstance(r, dict):
            raise ChainError(f"{w}: a cfd-translator study result (run_studies / study_status: one study)")
        res, name, reliable = r, r.get("name") or f"#{i}", None
        if isinstance(r.get("results"), dict):
            res, reliable = r["results"], r.get("reliable")
        if reliable is False:
            raise ChainError(f"{w} ({name}): cfd-translator marked it unreliable "
                             f"({'; '.join(map(str, r.get('unreliable_because') or [])) or 'see its result'}): rerun it")
        flows = res.get("flow")
        if not isinstance(flows, dict) or not flows:
            raise ChainError(f"{w} ({name}): no 'flow' (inlet and outlet flow rates) in the result")
        qin = [(float(v["Q"]), float(v.get("mdot", 0.0))) for v in flows.values()
               if isinstance(v, dict) and v.get("Q") is not None and float(v["Q"]) > 0]
        if not qin or sum(q for q, _ in qin) <= 0:
            raise ChainError(f"{w} ({name}): no inflow in its 'flow'")
        Q = sum(q for q, _ in qin)
        md = sum(m for _, m in qin)
        rho = md / Q if md > 0 else None
        pts.append({"name": name, "res": res, "Q": Q, "rho": rho, "reliable": reliable})
    return pts, spec


def _rho_of(pts, where):
    rhos = [p["rho"] for p in pts if p["rho"]]
    if not rhos:
        raise ChainError(f"{where}: the results give no density (flow.<bc>.mdot / Q)")
    if max(rhos) > 1.01 * min(rhos):
        raise ChainError(f"{where}: the results are for different densities ({min(rhos):g} to {max(rhos):g} kg/m3)")
    return sum(rhos) / len(rhos)


def cfdt_pump(spec, where: str, N_nominal: float | None = None) -> dict:
    """{curve, N_nominal (rad/s), power, rho_power, record} for fl_pump / fl_pump_shaft from cfd-translator pump
    results (results.pump.head_m, flow_m3h, power_W; rotating.<zone>.rpm): points at another speed move to N_nominal
    by the affinity laws (V ~ N, H ~ N^2, P ~ N^3); head and power by least squares (quadratic from 3 points)."""
    pts, spec = _cfdt_items(spec, where)
    rows = []
    for p in pts:
        res, name = p["res"], p["name"]
        pump = res.get("pump")
        if not isinstance(pump, dict) or pump.get("head_m") is None or pump.get("power_W") is None:
            raise ChainError(f"{where} ({name}): no pump.head_m / pump.power_W (a study with a rotating zone)")
        rpms = sorted({float(z["rpm"]) for z in (res.get("rotating") or {}).values() if isinstance(z, dict)
                       and z.get("rpm") is not None})
        if not rpms:
            raise ChainError(f"{where} ({name}): no rotating.<zone>.rpm")
        if rpms[-1] > rpms[0] * (1 + 1e-6):
            raise ChainError(f"{where} ({name}): rotating zones at different speeds {rpms}")
        V = float(pump["flow_m3h"]) / 3600.0 if pump.get("flow_m3h") is not None else p["Q"]
        rows.append({"name": name, "rpm": rpms[0], "V_m3s": V, "head_m": float(pump["head_m"]),
                     "power_W": float(pump["power_W"]), "rho": p["rho"], "reliable": p["reliable"]})
    rho = _rho_of(pts, where)
    N0 = float(N_nominal) if N_nominal is not None else rows[0]["rpm"] * RPM
    head, power = [], []
    for r in rows:
        s = N0 / (r["rpm"] * RPM)
        head.append([r["V_m3s"] * s, r["head_m"] * s * s])
        power.append([r["V_m3s"] * s, r["power_W"] * s ** 3])
    head.sort()
    power.sort()
    try:
        curve, hfit = quad_points(head, line_as_two=True)
        pw, pfit = quad_points(power)
    except ChainError as exc:
        raise ChainError(f"{where}: {exc}") from None
    c = hfit["coefficients"]
    for v in (curve[0][0], curve[-1][0]):
        slope = c[1] + (2 * c[2] * v if len(c) > 2 else 0.0)
        if not slope < 0:
            raise ChainError(f"{where}: the head from cfd-translator does not fall with the flow at "
                             f"{v * 3600:.4g} m3/h (dH/dV = {slope:.4g} s/m2): MSL's curves need a falling head")
    rec = {"from": "cfdt", "what": spec.get("what") or f"pump curve from {len(rows)} cfd-translator results",
           "N_nominal_rpm": N0 / RPM, "rho_kg_m3": rho, "points": rows, "head_fit": hfit, "power_fit": pfit,
           "note": "head and power by least squares, at N_nominal (affinity laws); MSL extrapolates the head "
                   "linearly outside the flows given, the power quadratically"}
    return {"curve": curve, "N_nominal": N0, "power": pw, "rho_power": rho, "record": rec}


def cfdt_resistance(spec, where: str) -> dict:
    """{a, b, rho, mu, record} for fl_resistance: dp = a V|V| + b V by non-negative least squares to cfd-translator's
    total_pressure_drop (Pa) at the inflow V (m3/s) of each result (one result: b = 0)."""
    import numpy as np
    pts, spec = _cfdt_items(spec, where)
    rows = []
    for p in pts:
        dp = p["res"].get("total_pressure_drop")
        if dp is None:
            raise ChainError(f"{where} ({p['name']}): no total_pressure_drop (an inlet and an outlet are needed)")
        rows.append({"name": p["name"], "V_m3s": p["Q"], "dp_total_Pa": float(dp), "rho": p["rho"],
                     "reliable": p["reliable"]})
    rho = _rho_of(pts, where)
    V = np.array([r["V_m3s"] for r in rows])
    dp = np.array([r["dp_total_Pa"] for r in rows])
    if len(rows) == 1:
        a, b = float(dp[0] / V[0] ** 2), 0.0
    else:
        from scipy.optimize import nnls
        A = np.column_stack([V * np.abs(V), V])
        s = np.array([max(float(np.max(np.abs(A[:, j]))), 1e-300) for j in range(2)])
        x, _ = nnls(A / s, dp)
        a, b = float(x[0] / s[0]), float(x[1] / s[1])
    if not a > 0:
        raise ChainError(f"{where}: the pressure drops are linear in the flow (a = 0): fl_resistance needs a > 0; "
                         f"use fl_valve_linear (m_flow_nominal / dp_nominal) for a linear law")
    fit = a * V * np.abs(V) + b * V
    mu = spec.get("mu")
    if mu is not None and not (isinstance(mu, (int, float)) and mu > 0):
        raise ChainError(f"{where}.mu: cfd-translator's viscosity in Pa.s (> 0)")
    rec = {"from": "cfdt", "what": spec.get("what") or f"dp = a V|V| + b V from {len(rows)} cfd-translator results",
           "a": a, "b": b, "rho_kg_m3": rho, "mu_Pa_s": mu, "points": rows,
           "rms_residual_rel": float(np.sqrt(np.mean((fit - dp) ** 2)) / max(float(np.max(np.abs(dp))), 1e-300))}
    return {"a": a, "b": b, "rho": rho, "mu": mu, "record": rec}
