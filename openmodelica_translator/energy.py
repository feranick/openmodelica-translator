"""energy.py — the energy balance of a run, from the elements' values and the recorded series.
Version: 2026.10.21.16

    work in (boundaries) = change of stored energy + dissipated energy

Boundaries (energy into the system): electrical sources -int(v i) dt (passive sign: a source absorbs v*i);
op-amp outputs -int(out.v out.i) dt (the supply); force and torque sources and speed-dependent loads
int(-flange.f) d(flange.s) (a Stieltjes trapezoid on the recorded position, so no velocity is needed);
fixed and prescribed temperatures and heat flows -int(port.Q_flow) dt.
Storage: 1/2 C v^2, 1/2 L i^2 (transformer 1/2 (L1 i1^2 + 2 M i1 i2 + L2 i2^2)), 1/2 m v^2, 1/2 J w^2,
1/2 c (s_rel - s_rel0)^2, C T for heat capacitors.
Dissipation: LossPower of resistors and diodes, lossPower of dampers (with their heatPort connected the loss heats
the thermal network instead: no term here, the heat shows up there); a DC machine's powerBalance.lossPowerTotal
(storage 1/2 La i^2 + 1/2 Jr w^2); with heat ports connected the losses heat the thermal network instead.
Coolant loops: storage m cv T per component with medium inside; pumps (volume flow, pressure increase) put in
-dp V_flow; ambients and pressure references -(H_flow + p m_flow / rho) (enthalpy and pressure work); pipes and valves
lose V_flow dp minus the friction heat fed back to the medium (Q_friction); an ideal pump only converts its flange's
work (neutral, counted at the mechanical source).
Lossless or neutral: grounds, fixed points, sensors, EMF, gears, conductors, convection, radiation (they move
energy, they do not make or lose it), signal blocks.
The integrals are trapezoids on the output grid (an event's two points add nothing); their error is estimated
by Richardson (every point against every other point) and sets the tolerance with a floor of 1 %.
"""
from __future__ import annotations

import numpy as np

NEUTRAL = {"ground", "fixed", "rot_fixed", "emf", "ideal_gear", "thermal_conductor", "thermal_resistor",
           "convection_core", "radiation", "voltage_sensor", "current_sensor", "potential_sensor", "position_sensor",
           "speed_sensor", "acc_sensor", "force_sensor", "angle_sensor", "rot_speed_sensor", "rot_acc_sensor",
           "torque_sensor", "temperature_sensor", "heat_flow_sensor"}
EL_SOURCES = {"constant_voltage", "step_voltage", "ramp_voltage", "sine_voltage", "pulse_voltage", "constant_current",
              "step_current", "sine_current", "signal_voltage", "signal_current"}
MECH_T = {"force", "force_step", "constant_force"}
MECH_R = {"torque", "torque_step", "constant_torque", "linear_speed_load", "quadratic_speed_load"}
HEAT_B = {"fixed_temperature", "prescribed_temperature", "fixed_heat_flow", "prescribed_heat_flow"}
FLUID_PUMPS = {"volume_flow", "signal_volume_flow", "pressure_increase", "signal_pressure_increase"}
FLUID_NEUTRAL = {"fluid_temperature_sensor", "fluid_pressure_sensor", "volume_flow_sensor"}


def _trap(y, t):
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(t)))


def _stieltjes(f, s):
    return float(np.sum(0.5 * (f[1:] + f[:-1]) * np.diff(s)))


def plan(net) -> dict | None:
    """{paths: Modelica variables needed, terms: [...]}, or None when an element is not covered."""
    from . import catalog as C
    if net is None:
        return None
    terms, paths, unknown = [], set(), []
    for e in sorted(net.elems):
        d = net.elems[e]
        k, p = d["kind"], d["params"]
        if C.KINDS[k]["ports"] and all(t in ("in", "out") for t in C.KINDS[k]["ports"].values()):
            continue                                    # signal blocks
        if k in NEUTRAL or k in FLUID_NEUTRAL:
            continue
        if C.KINDS[k].get("domain") == "fluid":
            _fluid_terms(terms, e, k, p)
            continue
        if k == "dc_machine":
            terms.append(("store", e, [f"{e}.la.i", f"{e}.wMechanical"],
                          lambda g, e=e, La=p["La"], J=p["Jr"]: 0.5 * La * g(f"{e}.la.i") ** 2 + 0.5 * J * g(f"{e}.wMechanical") ** 2))
            if not d.get("enabled"):
                terms.append(("loss", e, [f"{e}.powerBalance.lossPowerTotal"],
                              lambda g, e=e: g(f"{e}.powerBalance.lossPowerTotal")))
            continue
        if k == "capacitor":
            terms.append(("store", e, [f"{e}.v"], lambda g, e=e, C_=p["C"]: 0.5 * C_ * g(f"{e}.v") ** 2))
        elif k == "inductor":
            terms.append(("store", e, [f"{e}.i"], lambda g, e=e, L=p["L"]: 0.5 * L * g(f"{e}.i") ** 2))
        elif k == "transformer":
            terms.append(("store", e, [f"{e}.i1", f"{e}.i2"],
                          lambda g, e=e, p=p: 0.5 * (p["L1"] * g(f"{e}.i1") ** 2 + 2 * p["M"] * g(f"{e}.i1") * g(f"{e}.i2")
                                                     + p["L2"] * g(f"{e}.i2") ** 2)))
        elif k == "mass":
            terms.append(("store", e, [f"{e}.v"], lambda g, e=e, m=p["m"]: 0.5 * m * g(f"{e}.v") ** 2))
        elif k == "inertia":
            terms.append(("store", e, [f"{e}.w"], lambda g, e=e, J=p["J"]: 0.5 * J * g(f"{e}.w") ** 2))
        elif k in ("spring", "spring_damper", "rot_spring", "rot_spring_damper"):
            rel = "s_rel" if k in ("spring", "spring_damper") else "phi_rel"
            r0 = p["s_rel0"] if rel == "s_rel" else p["phi_rel0"]
            terms.append(("store", e, [f"{e}.{rel}"], lambda g, e=e, c=p["c"], rel=rel, r0=r0:
                          0.5 * c * (g(f"{e}.{rel}") - r0) ** 2))
            if k.endswith("damper") and "heatPort" not in d.get("enabled", ()):
                terms.append(("loss", e, [f"{e}.lossPower"], lambda g, e=e: g(f"{e}.lossPower")))
        elif k in ("damper", "rot_damper"):
            if "heatPort" not in d.get("enabled", ()):
                terms.append(("loss", e, [f"{e}.lossPower"], lambda g, e=e: g(f"{e}.lossPower")))
        elif k in ("resistor", "ideal_diode"):
            if "heatPort" not in d.get("enabled", ()):
                terms.append(("loss", e, [f"{e}.LossPower"], lambda g, e=e: g(f"{e}.LossPower")))
        elif k == "heat_capacitor":
            Cv = p["C"] if p.get("C") is not None else p["m"] * p["cp"]
            terms.append(("store", e, [f"{e}.T"], lambda g, e=e, Cv=Cv: Cv * g(f"{e}.T")))
        elif k in EL_SOURCES:
            terms.append(("power", e, [f"{e}.v", f"{e}.i"], lambda g, e=e: -g(f"{e}.v") * g(f"{e}.i")))
        elif k == "opamp":
            terms.append(("power", e, [f"{e}.out.v", f"{e}.out.i"], lambda g, e=e: -g(f"{e}.out.v") * g(f"{e}.out.i")))
        elif k in HEAT_B:
            terms.append(("power", e, [f"{e}.port.Q_flow"], lambda g, e=e: -g(f"{e}.port.Q_flow")))
        elif k in MECH_T or k in MECH_R:
            f, s = ("f", "s") if k in MECH_T else ("tau", "phi")
            terms.append(("work", e, [f"{e}.flange.{f}", f"{e}.flange.{s}"],
                          (lambda g, e=e, f=f: -g(f"{e}.flange.{f}"), lambda g, e=e, s=s: g(f"{e}.flange.{s}"))))
        else:
            unknown.append(k)
    for term in terms:
        paths.update(term[2])
    if unknown:
        return {"covered": False, "why": f"no energy terms for {sorted(set(unknown))}", "paths": set()}
    if not terms:
        return {"covered": False, "why": "no physical elements", "paths": set()}
    return {"covered": True, "terms": terms, "paths": paths}


def _fluid_terms(terms, e, k, p):
    from . import catalog as C
    med = C.medium_props(p["medium"])
    if k in ("ambient", "pressure_reference"):
        port, rho = f"{e}.flowPort", med["rho"]
        terms.append(("power", e, [f"{port}.H_flow", f"{port}.p", f"{port}.m_flow"],
                      lambda g, port=port, rho=rho: -(g(f"{port}.H_flow") + g(f"{port}.p") * g(f"{port}.m_flow") / rho)))
        return
    if (p.get("m") or 0.0) > 0:
        terms.append(("store", e, [f"{e}.T"], lambda g, e=e, c=p["m"] * med["cv"]: c * g(f"{e}.T")))
    if k in FLUID_PUMPS:
        terms.append(("power", e, [f"{e}.dp", f"{e}.V_flow"], lambda g, e=e: -g(f"{e}.dp") * g(f"{e}.V_flow")))
    elif k == "pipe":
        terms.append(("loss", e, [f"{e}.dp", f"{e}.V_flow", f"{e}.Q_friction"],
                      lambda g, e=e: g(f"{e}.dp") * g(f"{e}.V_flow") - g(f"{e}.Q_friction")))
    elif k == "valve":
        terms.append(("loss", e, [f"{e}.dp", f"{e}.V_flow"],
                      lambda g, e=e, fl=p["frictionLoss"]: (1 - fl) * g(f"{e}.dp") * g(f"{e}.V_flow")))


def balance(pl, t, get) -> dict:
    """The balance on the series. get(path) -> array."""
    if not pl or not pl.get("covered"):
        return {"ok": True, "covered": False, "why": (pl or {}).get("why", "no network (escape hatch)")}
    t = np.asarray(t, float)
    sums = {"work_in_J": 0.0, "stored_change_J": 0.0, "dissipated_J": 0.0}
    coarse = {"work_in_J": 0.0, "stored_change_J": 0.0, "dissipated_J": 0.0}
    idx2 = np.arange(0, t.size, 2)
    if idx2[-1] != t.size - 1:
        idx2 = np.append(idx2, t.size - 1)
    gross = 0.0                     # the size of the energies moved (terms may cancel: a store feeding another)
    for kind, e, _, fn in pl["terms"]:
        if kind == "store":
            E = np.asarray(fn(get), float)
            d = float(E[-1] - E[0])
            sums["stored_change_J"] += d
            coarse["stored_change_J"] += d
            gross = max(gross, abs(d))
        elif kind == "loss":
            P = np.asarray(fn(get), float)
            x = _trap(P, t)
            sums["dissipated_J"] += x
            coarse["dissipated_J"] += _trap(P[idx2], t[idx2])
            gross = max(gross, abs(x))
        elif kind == "power":
            P = np.asarray(fn(get), float)
            x = _trap(P, t)
            sums["work_in_J"] += x
            coarse["work_in_J"] += _trap(P[idx2], t[idx2])
            gross = max(gross, abs(x))
        else:
            F, S = (np.asarray(x(get), float) for x in fn)
            x = _stieltjes(F, S)
            sums["work_in_J"] += x
            coarse["work_in_J"] += _stieltjes(F[idx2], S[idx2])
            gross = max(gross, abs(x))
    scale = max(max(abs(v) for v in sums.values()), gross)
    if scale <= 0:
        return {"ok": True, "covered": True, **sums, "error_rel": 0.0, "note": "no energy moved"}
    resid = sums["work_in_J"] - sums["stored_change_J"] - sums["dissipated_J"]
    est = sum(abs(sums[k] - coarse[k]) for k in sums) / 3.0
    err = abs(resid) / scale
    tol = max(0.01, 3.0 * est / scale)
    return {"ok": bool(err <= tol), "covered": True, **{k: float(v) for k, v in sums.items()},
            "error_rel": float(err), "integration_error_est_rel": float(est / scale), "tol": float(tol)}
