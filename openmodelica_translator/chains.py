"""chains.py — values from the other translators' results, as study values with their provenance.
Version: 2026.10.21.10

The tools pass specs, never paths (servers may run on different machines): a value from another translator is
written into a study as {"value", "unit", "from", "what"} (units.to_si converts it and the result's `inputs`
records it). These helpers make such values from the results the other translators return:

  feat_stiffness(static, face, comp, force)   k = |F| / |mean displacement of a face| (feat: faces.<face>.mean_u.<c>, mm)
  feat_mass(result, factor)                   mass_kg (x factor: 33/140 for a cantilever's tip by Rayleigh)
  feat_frequency(modal, i)                    frequencies.<i> (Hz)
  feat_conductance(thermal, face, dT)         |heat_flow.<face>| / dT (W/K)
  b3dt_mass(goal, density)                    volume (mm3, build123d-translator check) x density (kg/m3)
  rayleigh_cantilever_hz(k, m)                sqrt(k / (33/140 m)) / 2 pi: 1.46 % above the first bending frequency

Interface keys read: feat faces.<face>.mean_u.<ux|uy|uz>, applied_force, frequencies, mass_kg, heat_flow.<face>;
build123d-translator check()[name].volume.
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
    from build123d_translator import api as B
    r = B.check({"part": goal})["part"]
    if not r.get("buildable"):
        raise ValueError(f"b3dt: the goal does not build: {r.get('why')}")
    v = float(r["volume"])
    return {"value": v * 1e-9 * density, "unit": "kg", "from": "b3dt",
            "what": what or f"volume {v:.6g} mm3 x density {density:g} kg/m3"}


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
