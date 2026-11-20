"""npsh.py — net positive suction head of Modelica.Fluid pumps (V5 FL16).
Version: 2026.11.19.27

MSL's pumps carry one single-phase medium and its PumpMonitoringNPSH needs a two-phase one, so OpenModelica does not
say when a pump would cavitate. omct reads every liquid pump's inlet state (MSL's pump.monitoring.state_in: p and T
of inStream(port_a), recorded in OpenModelica's result; the same from the reference) and reports per pump

    NPSHa(t) = (p_in + rho v^2/2 - p_vap(T_in)) / (rho(T_in) g)       (the velocity head with inlet_diameter only)

against the pump's required NPSH, NPSHr(V, N) = (N/N0)^2 NPSHr0(V N0/N): npsh_required (a number or [[V_flow,
NPSHr], ...] at N_nominal) or npsh_cfdt (cfd-translator cavitation sweeps: NPSH3 per flow rate, as cfd-translator's
npsh_curve finds it). Short when NPSHa < npsh_margin x NPSHr (default 1.1): a warning; the run stays reliable (a
physical finding, not a numerical error; Nicola 2026-11-19).

Vapour pressure: water and the custom liquids based on it, IAPWS-IF97's saturation line (region 4; 273.15-647.096 K)
unless the medium gives p_vapour (Pa); glycol47 and essotherm650, the tableVaporPressure of MSL's TableBased
packages interpolated in ln(p) against 1/T (MSL's own quadratic fit of those tables, poly_pVap, goes negative and
MSL never uses it); gases: not applicable.
"""
from __future__ import annotations

import math

import numpy as np

G_STD = 9.80665
RPM = 2 * math.pi / 60

# IAPWS-IF97 region 4 (saturation line) coefficients n1..n10
IF97_N = [0.11670521452767e4, -0.72421316703206e6, -0.17073846940092e2, 0.12020824702470e5, -0.32325550322333e7,
          0.14915108613530e2, -0.48232657361591e4, 0.40511340542057e6, -0.23855557567849, 0.65017534844798e3]

# Modelica.Media.Incompressible.Examples (MSL 4.0.0): tableVaporPressure [degC, Pa]
VAPOUR_TABLES = {
    "glycol47": [[0, 500], [20, 1.9e3], [40, 5.3e3], [60, 16e3], [80, 37e3], [100, 80e3]],
    "essotherm650": [[160, 3], [180, 10], [200, 40], [220, 100], [240, 300], [260, 600], [280, 1600], [300, 3e3],
                     [320, 5.5e3]],
}


def psat_water(T: float) -> float:
    """IAPWS-IF97 saturation pressure [Pa] at T [K] (valid 273.15 K to the critical point 647.096 K)."""
    n = IF97_N
    th = T + n[8] / (T - n[9])
    A = th * th + n[0] * th + n[1]
    B = n[2] * th * th + n[3] * th + n[4]
    C = n[5] * th * th + n[6] * th + n[7]
    return (2 * C / (-B + math.sqrt(B * B - 4 * A * C))) ** 4 * 1e6


def _table_pvap(rows, T: float) -> float:
    """ln(p) linear in 1/T between the table's points, and along the end segments outside them."""
    x = [1.0 / (r[0] + 273.15) for r in rows]           # falls as T rises
    y = [math.log(r[1]) for r in rows]
    xi = 1.0 / T
    k = 0
    while k < len(x) - 2 and xi < x[k + 1]:
        k += 1
    return math.exp(y[k] + (y[k + 1] - y[k]) * (xi - x[k]) / (x[k + 1] - x[k]))


def vapour_pressure(medium, T: float):
    """(p_vap [Pa], where it comes from) for an omct Modelica.Fluid medium at T [K]; (None, why) for gases."""
    m = medium or "water"
    if isinstance(m, dict):
        if m.get("p_vapour") is not None:
            return float(m["p_vapour"]), "the medium's p_vapour"
        return psat_water(T), "IAPWS-IF97 saturation (water's: the custom liquid gives no p_vapour)"
    if m == "water":
        return psat_water(T), "IAPWS-IF97 saturation"
    if m in VAPOUR_TABLES:
        return _table_pvap(VAPOUR_TABLES[m], T), f"MSL {m}'s tableVaporPressure (ln p against 1/T)"
    return None, f"{m} is a gas: no NPSH"


# ---- required NPSH from cfd-translator cavitation sweeps ----------------------------------------------------
class NpshError(ValueError):
    pass


def npsh3(points: list, head_drop: float = 0.03):
    """cfd-translator's npsh_curve rule for one sweep: the reference head is the highest-NPSH point's; NPSH3 where
    the head first falls head_drop below it, linear between the two points around it; points whose liquid flow is
    more than 1 % off are left out. points: [{npsh, head, flow_error}]. None when it does not fall that far."""
    pts = sorted([p for p in points if p.get("head") is not None and not (
        p.get("flow_error") is not None and abs(p["flow_error"]) > 0.01)], key=lambda p: -p["npsh"])
    if len(pts) < 2:
        return None
    target = (1 - head_drop) * pts[0]["head"]
    for a, b in zip(pts[:-1], pts[1:]):
        if a["head"] >= target > b["head"]:
            t = (a["head"] - target) / (a["head"] - b["head"])
            return a["npsh"] + t * (b["npsh"] - a["npsh"])
    return None


def from_cfdt(results, where: str, N_nominal: float | None = None) -> dict:
    """npsh_required [[V_flow, NPSH3], ...] at N_nominal from cfd-translator cavitation results (one study per NPSH
    point, run_studies' shape: results.cavitation.npsh_m, results.pump.head_m and flow_m3h, rotating.<zone>.rpm,
    results.cavitation.flows.liquid_flow_error): grouped by flow and speed (one sweep each), NPSH3 per sweep, moved
    to N_nominal by the affinity laws (V ~ N, NPSH ~ N^2)."""
    if isinstance(results, dict) and "results" in results and isinstance(results["results"], list):
        results = results["results"]
    if not isinstance(results, list) or not results:
        raise NpshError(f"{where}: a list of cfd-translator cavitation results (one per NPSH point)")
    groups = {}
    for k, r in enumerate(results):
        name = (r or {}).get("name", f"#{k}")
        if not isinstance(r, dict) or not isinstance(r.get("results"), dict):
            raise NpshError(f"{where} ({name}): not a cfd-translator result")
        if r.get("reliable") is False:
            raise NpshError(f"{where} ({name}): unreliable ({r.get('unreliable_because')})")
        res = r["results"]
        cav, pump = res.get("cavitation") or {}, res.get("pump") or {}
        if cav.get("npsh_m") is None or pump.get("head_m") is None:
            raise NpshError(f"{where} ({name}): no cavitation.npsh_m / pump.head_m (a cfd-translator cavitation "
                            f"study: cavitation {{npsh: [...]}})")
        rpms = sorted({float(z["rpm"]) for z in (res.get("rotating") or {}).values()
                       if isinstance(z, dict) and z.get("rpm") is not None})
        if not rpms:
            raise NpshError(f"{where} ({name}): no rotating.<zone>.rpm")
        if pump.get("flow_m3h") is not None:
            V = float(pump["flow_m3h"]) / 3600.0
        else:
            V = abs(float(((res.get("flow") or {}).get("inlet") or {}).get("Q", float("nan"))))
        if not math.isfinite(V) or V <= 0:
            raise NpshError(f"{where} ({name}): no flow rate (pump.flow_m3h or flow.inlet.Q)")
        key = (round(V, 9), round(rpms[0], 6))
        groups.setdefault(key, []).append({"name": name, "npsh": float(cav["npsh_m"]), "head": float(pump["head_m"]),
                                           "flow_error": (cav.get("flows") or {}).get("liquid_flow_error")})
    N0 = float(N_nominal) if N_nominal is not None else sorted(groups)[0][1] * RPM
    curve, sweeps = [], []
    for (V, rpm), pts in sorted(groups.items()):
        n3 = npsh3(pts)
        sweeps.append({"V_flow": V, "rpm": rpm, "points": len(pts), "npsh3_m": n3})
        if n3 is None:
            raise NpshError(f"{where}: the sweep at {V:g} m3/s, {rpm:g} rpm does not reach a 3 % head drop (or has "
                            f"fewer than two points): add lower NPSH values")
        s = N0 / (rpm * RPM)
        curve.append([V * s, n3 * s * s])
    curve.sort()
    if any(b[0] <= a[0] for a, b in zip(curve, curve[1:])):
        raise NpshError(f"{where}: two sweeps at the same flow (after moving to N_nominal)")
    return {"curve": curve, "N_nominal": N0, "record": {"from": "cfdt", "sweeps": sweeps,
                                                        "N_nominal_rpm": N0 / RPM,
                                                        "note": "NPSH3 per sweep (3 % head drop), V ~ N, NPSH ~ N^2"}}


def npshr(curve, V: float):
    """NPSHr at V from [[V_flow, NPSHr], ...] (one point: constant): linear between the points, the end values
    outside them; (value, outside the curve's flows)."""
    if len(curve) == 1:
        return curve[0][1], False
    Vs = [c[0] for c in curve]
    if V <= Vs[0]:
        return curve[0][1], V < Vs[0]
    if V >= Vs[-1]:
        return curve[-1][1], V > Vs[-1]
    return float(np.interp(V, Vs, [c[1] for c in curve])), False


# ---- evaluation on a run ------------------------------------------------------------------------------------
def pumps(net) -> list:
    """The liquid fl_pump elements (gases: no NPSH)."""
    from . import catalog as C
    out = []
    for e, d in sorted(net.elems.items()):
        if d["kind"] == "fl_pump" and C.fmedium(d["params"].get("medium") or "water")["class"] != "gas":
            out.append(e)
    return out


def paths(net) -> list:
    """The Modelica paths the NPSH evaluation reads (the inlet state, the flow, the speed)."""
    return [f"{e}.{v}" for e in pumps(net) for v in ("monitoring.state_in.p", "monitoring.state_in.T", "V_flow",
                                                       "m_flow", "N")]


def evaluate(net, t, series) -> tuple[dict, list]:
    """({pump: npsh block}, warnings) from a run's series(path)."""
    from .fluidref import medium_of
    blocks, warns = {}, []
    for e in pumps(net):
        d = net.elems[e]
        p_ = d["params"]
        comp = d["comp"]
        try:
            pin = np.asarray(series(f"{e}.monitoring.state_in.p"), float)
            Tin = np.asarray(series(f"{e}.monitoring.state_in.T"), float)
            V = np.asarray(series(f"{e}.V_flow"), float)
            mflow = np.asarray(series(f"{e}.m_flow"), float)
            N = np.asarray(series(f"{e}.N"), float) * RPM
        except KeyError as exc:
            blocks[comp] = {"covered": False, "why": f"{exc} not recorded"}
            continue
        med = medium_of(p_)
        mv = p_.get("medium") or "water"
        pv, frm = zip(*(vapour_pressure(mv, float(T)) for T in Tin)) if len(Tin) else ((), ())
        pv = np.array(pv, float)
        rho = np.array([med.d(float(T), float(p)) for T, p in zip(Tin, pin)])
        hv = np.zeros_like(pin)
        if p_.get("inlet_diameter") is not None:
            A = math.pi / 4 * float(p_["inlet_diameter"]) ** 2
            hv = 0.5 * (mflow / (rho * A)) ** 2 * rho        # rho v^2 / 2
        npsha = (pin + hv - pv) / (rho * G_STD)
        blk = {"covered": True, "p_vapour_from": frm[0] if frm else None,
               "velocity_head": "with inlet_diameter" if p_.get("inlet_diameter") is not None else
               "not included (static inlet pressure: NPSHa is low by v^2/2g; give inlet_diameter)"}
        k = int(np.argmin(npsha))
        blk.update({"NPSHa_min_m": float(npsha[k]), "t_NPSHa_min": float(t[k]),
                    "p_in_at_min_Pa": float(pin[k]), "T_in_at_min_K": float(Tin[k]), "p_vapour_at_min_Pa": float(pv[k])})
        curve = p_.get("npsh_required")
        if curve:
            N0 = float(p_["N_nominal"])
            r = np.array([N[j] / N0 if N[j] > 0 else 1.0 for j in range(len(t))])
            req, outside = [], False
            for j in range(len(t)):
                val, out = npshr(curve, max(V[j], 0.0) / r[j])
                req.append(val * r[j] * r[j])
                outside = outside or out
            req = np.array(req)
            margin = float(p_.get("npsh_margin") or 1.1)
            ratio = np.where(req > 0, npsha / np.where(req > 0, req, 1.0), np.inf)
            short = ratio < margin
            kr = int(np.argmin(ratio))
            blk.update({"npsh_required_from": p_.get("_npsh_from", "given"), "margin": margin,
                        "NPSHr_at_min_m": float(req[k]), "ratio_min": float(ratio[kr]), "t_ratio_min": float(t[kr]),
                        "NPSHr_at_ratio_min_m": float(req[kr]), "NPSHa_at_ratio_min_m": float(npsha[kr]),
                        "short": bool(short.any())})
            if outside:
                blk["outside_curve"] = "the flow left the npsh_required curve's range: its end value was used"
            if short.any():
                dt = np.diff(np.asarray(t, float))
                mid = (short[1:] | short[:-1])
                blk["short_from_t"] = float(t[int(np.argmax(short))])
                blk["short_s"] = float(np.sum(dt[mid])) if len(dt) else 0.0
                warns.append(f"{comp}: NPSH available {npsha[kr]:.3g} m below {margin:g} x required "
                             f"{req[kr]:.3g} m at t = {t[kr]:.4g} s (from t = {blk['short_from_t']:.4g} s): the pump "
                             f"would cavitate there; the single-phase results past that point are not physical")
        else:
            blk["note"] = "no npsh_required (or npsh_cfdt): NPSHa only"
        from .post import downsample, _r6
        ts, ys = downsample(np.asarray(t, float), npsha)
        blk["series"] = {"t": [_r6(x) for x in ts], "NPSHa": [_r6(x) for x in ys]}
        blocks[comp] = blk
    return blocks, warns
