"""npsh.py — net positive suction head of Modelica.Fluid pumps (V5 FL16).
Version: 2026.11.20.2

MSL's pumps carry one single-phase medium and its PumpMonitoringNPSH needs a two-phase one, so OpenModelica does not
say when a pump would cavitate. omct reads every liquid pump's inlet state (MSL's pump.monitoring.state_in: p and T
of inStream(port_a), recorded in OpenModelica's result; the same from the reference) and reports per pump

    NPSHa(t) = (p_in + rho v^2/2 - p_vap(T_in)) / (rho(T_in) g)       (the velocity head with inlet_diameter only)

against the pump's NPSH3 (3 % head drop), known values (npsh_required) or upper bounds (npsh3_below: "below 2 m at
20 m3/h", a cfd-translator sweep whose head did not fall 3 % down to its lowest NPSH), or both from npsh_cfdt. Between
the flows given: linear; below the lowest flow: its value held; above the highest: x (V/V_max)^npsh_flow_exponent
(2 by default); other speeds: x (N/N0)^2 at V N0/N (affinity). Status per pump (FL17, Nicola 2026-11-20): 'below'
(NPSHa/NPSH3 < 1: the 3 % drop is expected), 'approaching' (< npsh_margin, 1.5 by default: inception comes well
before the 3 % drop), 'ok'; against an upper bound the ratio is only a lower bound ('ok' when even it clears the
margin, else 'unresolved'). A warning names the flows the data apply to and the scaling; the run stays reliable (a
physical finding, not a numerical error).

Onset (optional, FL17): with the pump's eye dip below the inlet and its pressure pulsation amplitude (eye_dip,
pulse_amplitude, Pa; or from a cfd-translator free-flow run: onset_cfdt), p_eye_min = p_in - dip - amplitude
(both x (N/N0)^2 from the speed they were measured at) is compared with the vapour pressure (cavitation onset) and with
the air-saturation pressure (air release: the liquid saturated with air at air_saturation_pressure, p_ambient by
default).

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
ONSET_JITTER = 3000.0       # Pa: trips this close to a threshold with a raw (unfiltered) amplitude: possibly jitter
                            # (the S50 eye probe's step-to-step jitter, +-3 kPa; Nicola 2026-11-20)

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
    more than 1 % off are left out. points: [{npsh, head, flow_error}]. Returns (value, kind): kind 'at' (the drop
    found), 'below' (the head held down to the lowest NPSH: NPSH3 is below that, value = it), or (None, why)."""
    pts = sorted([p for p in points if p.get("head") is not None and not (
        p.get("flow_error") is not None and abs(p["flow_error"]) > 0.01)], key=lambda p: -p["npsh"])
    if len(pts) < 2:
        return None, "fewer than two points"
    target = (1 - head_drop) * pts[0]["head"]
    for a, b in zip(pts[:-1], pts[1:]):
        if a["head"] >= target > b["head"]:
            t = (a["head"] - target) / (a["head"] - b["head"])
            return a["npsh"] + t * (b["npsh"] - a["npsh"]), "at"
    if pts[-1]["head"] >= target:
        return pts[-1]["npsh"], "below"
    return None, "no 3 % crossing found"


def _rpm_of(res, where, name):
    rpms = sorted({float(z["rpm"]) for z in (res.get("rotating") or {}).values()
                   if isinstance(z, dict) and z.get("rpm") is not None})
    if not rpms:
        raise NpshError(f"{where} ({name}): no rotating.<zone>.rpm")
    return rpms[0]


def from_cfdt(results, where: str, N_nominal: float | None = None) -> dict:
    """NPSH3 at N_nominal from cfd-translator cavitation results (one study per NPSH point, run_studies' shape:
    results.cavitation.npsh_m, results.pump.head_m and flow_m3h, rotating.<zone>.rpm, cavitation.flows.
    liquid_flow_error): grouped by flow and speed (one sweep each), NPSH3 per sweep or, when the head held 3 % down to
    the sweep's lowest NPSH, that NPSH as an upper bound; moved to N_nominal by the affinity laws (V ~ N, NPSH ~ N^2).
    Returns {known: [[V, m]], below: [[V, m]], N_nominal, record}."""
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
        rpm = _rpm_of(res, where, name)
        if pump.get("flow_m3h") is not None:
            V = float(pump["flow_m3h"]) / 3600.0
        else:
            V = abs(float(((res.get("flow") or {}).get("inlet") or {}).get("Q", float("nan"))))
        if not math.isfinite(V) or V <= 0:
            raise NpshError(f"{where} ({name}): no flow rate (pump.flow_m3h or flow.inlet.Q)")
        key = (round(V, 9), round(rpm, 6))
        groups.setdefault(key, []).append({"name": name, "npsh": float(cav["npsh_m"]), "head": float(pump["head_m"]),
                                           "flow_error": (cav.get("flows") or {}).get("liquid_flow_error")})
    N0 = float(N_nominal) if N_nominal is not None else sorted(groups)[0][1] * RPM
    known, below, sweeps = [], [], []
    for (V, rpm), pts in sorted(groups.items()):
        n3, kind = npsh3(pts)
        sweeps.append({"V_flow": V, "V_flow_m3h": V * 3600, "rpm": rpm, "points": len(pts),
                       "npsh3_m": n3, "kind": kind})
        if n3 is None:
            raise NpshError(f"{where}: the sweep at {V * 3600:.4g} m3/h, {rpm:g} rpm: {kind}")
        s = N0 / (rpm * RPM)
        (known if kind == "at" else below).append([V * s, n3 * s * s])
    for lst in (known, below):
        lst.sort()
    flows = sorted(v for v, _ in known + below)
    if any(b <= a for a, b in zip(flows, flows[1:])):
        raise NpshError(f"{where}: two sweeps at the same flow (after moving to N_nominal)")
    return {"known": known, "below": below, "N_nominal": N0,
            "record": {"from": "cfdt", "sweeps": sweeps, "N_nominal_rpm": N0 / RPM,
                       "note": "NPSH3 per sweep (3 % head drop; 'below': no 3 % drop down to the sweep's lowest NPSH, "
                               "an upper bound), V ~ N, NPSH ~ N^2"}}


def _onset_revs(r, res, ff, probe, inlet, rpm, spec, where, name):
    """The revolution numbers onset_cfdt uses: spec 'revolutions' (a list), else those that start at or after the
    run's averaging start (results.averaged.settings.from, or pump.averaged_over[0]; revolution k spans
    switched_at + (k-1, k) x 60/rpm): a run freed at t = 0 has the start from rest in revolution 1 (rung 2 g 5,
    spark1 job 756: +2 to +5 kPa on the dip and the amplitude). Without an averaging start: all."""
    have = [x["rev"] for x in probe if "rev" in x]
    if len(have) != len(probe) or not all(any(y.get("rev") == k for y in inlet) for k in have):
        return [x.get("rev") for x in probe], "all (no revolution numbers)"
    if spec.get("revolutions") is not None:
        want = spec["revolutions"]
        if not isinstance(want, list) or not want or any(k not in have for k in want):
            raise NpshError(f"{where} ({name}): revolutions {want!r}: a list of the result's revolutions {have}")
        return list(want), "given"
    t0 = ((res.get("averaged") or {}).get("settings") or {}).get("from")
    if t0 is None:
        t0 = ((res.get("pump") or {}).get("averaged_over") or [None])[0]
    if t0 is None:
        return have, "all (no averaging start in the result)"
    T = 60.0 / rpm
    tsw = float(ff.get("switched_at_s") or 0.0)
    keep = [k for k in have if tsw + (k - 1) * T >= float(t0) - 1e-3 * T]
    if not keep:
        raise NpshError(f"{where} ({name}): no whole revolution after the averaging start {t0:g} s (run longer, or "
                        f"give onset_cfdt.revolutions)")
    return keep, f"from the averaging start {float(t0):.6g} s"


def onset_from_cfdt(spec, where: str) -> dict:
    """{eye_dip, pulse_amplitude, rpm, V_flow, raw} from a cfd-translator free-flow run (results.free_flow):
    spec {result, eye_probe, inlet, revolutions (optional)}. Over the revolutions after the run's averaging start
    (_onset_revs): dip = the inlet's mean static pressure (static_Pa, not total) - the eye probe's mean: the mean dip;
    amplitude = the eye's minimum below its mean, mean - min_f per revolution (cfd-translator 2026.11.20.1: [1 2 1]/4
    over time steps, without the step-to-step jitter), averaged: p_in - dip - amplitude is then the eye's mean
    minimum (the pulse is not symmetric: rung 2, eye probe, 26.4 kPa below the mean against 20.3 half
    peak-to-peak; FL19). Without min_f, the raw min (raw: True; an upper bound, the jitter deepens it)."""
    if not isinstance(spec, dict) or not isinstance(spec.get("result"), dict) or not spec.get("eye_probe") \
            or not spec.get("inlet") or set(spec) - {"result", "eye_probe", "inlet", "revolutions"}:
        raise NpshError(f"{where}: {{result: <a cfd-translator transient pump result with free_flow>, eye_probe: "
                        f"<probe name>, inlet: <inlet bc name>, revolutions (optional): [k, ...]}}")
    r = spec["result"]
    name = r.get("name", "result")
    res = r.get("results") or {}
    ff = res.get("free_flow") or {}
    probe = (ff.get("probes_p_Pa_by_revolution") or {}).get(spec["eye_probe"])
    inlet = ((ff.get("pressure_by_revolution") or {}).get(spec["inlet"]) or {}).get("static_Pa")
    if not probe or not inlet:
        raise NpshError(f"{where} ({name}): no free_flow.probes_p_Pa_by_revolution['{spec['eye_probe']}'] or "
                        f"free_flow.pressure_by_revolution['{spec['inlet']}'].static_Pa (solve.transient.free_flow "
                        f"with a probe at the eye)")
    rpm = _rpm_of(res, where, name)
    revs, revs_from = _onset_revs(r, res, ff, probe, inlet, rpm, spec, where, name)
    pick = (lambda rows: [x for x in rows if x.get("rev") in revs]) if revs_from[:3] != "all" else (lambda rows: rows)
    probe, inlet = pick(probe), pick(inlet)
    mean = lambda rows: float(np.mean([x["mean"] for x in rows]))          # noqa: E731
    dip = mean(inlet) - mean(probe)
    raw = not all("min_f" in x and "max_f" in x for x in probe)
    lo, hi = ("min", "max") if raw else ("min_f", "max_f")
    amp = float(np.mean([x["mean"] - x[lo] for x in probe]))
    half = float(np.mean([(x[hi] - x[lo]) / 2 for x in probe]))
    flows = [x["mean"] for x in pick(ff.get("flow_m3h_by_revolution") or [])]
    V = float(np.mean(flows)) / 3600 if flows else None
    return {"eye_dip": dip, "pulse_amplitude": amp, "rpm": rpm, "V_flow": V, "raw": raw,
            "record": {"from": "cfdt free_flow", "result": name, "eye_probe": spec["eye_probe"],
                       "inlet": spec["inlet"], "inlet_pressure": "static_Pa mean", "revolutions": revs,
                       "revolutions_from": revs_from, "eye_dip_Pa": dip,
                       "eye_dip_is": "mean (inlet static mean - eye mean)", "pulse_amplitude_Pa": amp,
                       "pulse_amplitude_is": "the eye's minimum below its mean (mean - min" +
                                             ("" if raw else "_f") + ")",
                       "half_peak_to_peak_Pa": half,
                       "amplitude_from": ("raw per-step min/max (no min_f/max_f: an upper bound, the step-to-step "
                                          "jitter adds to it)") if raw else "min_f/max_f ([1 2 1]/4 over time steps)",
                       "rpm": rpm, **({"V_flow_m3h": V * 3600} if V else {})}}


def merge_curve(known, below) -> list:
    """[[V, m, bound]] from the known NPSH3 values and the upper bounds; one entry per flow."""
    rows = [[v, m, False] for v, m in (known or [])] + [[v, m, True] for v, m in (below or [])]
    rows.sort(key=lambda r: r[0])
    for a, b in zip(rows, rows[1:]):
        if b[0] <= a[0]:
            raise NpshError(f"NPSH3 given twice at {a[0] * 3600:.4g} m3/h (npsh_required and npsh3_below)")
    return rows


def npshr(curve, V: float, exponent: float = 2.0):
    """NPSH3 at V (at N_nominal) from [[V_flow, m, bound], ...]: (value, bound, how). A single entry given without
    a flow (V 0, a plain number): constant. Between flows: linear (a bound if either side is); below the lowest:
    its value held; above the highest: x (V / V_max)^exponent."""
    if len(curve) == 1 and curve[0][0] == 0.0:
        return curve[0][1], curve[0][2], "constant"
    Vs = [c[0] for c in curve]
    if V <= Vs[0]:
        return curve[0][1], curve[0][2], "at" if V == Vs[0] else "held below the lowest flow"
    if V >= Vs[-1]:
        f = (V / Vs[-1]) ** exponent
        return curve[-1][1] * f, curve[-1][2], "at" if V == Vs[-1] else f"scaled (V/V_max)^{exponent:g} above it"
    k = int(np.searchsorted(Vs, V)) - 1
    a, b = curve[k], curve[k + 1]
    t = (V - a[0]) / (b[0] - a[0])
    return a[1] + t * (b[1] - a[1]), a[2] or b[2], "between"


def _m3h(V):
    return f"{V * 3600:.4g} m3/h"


def describe(curve, exponent) -> str:
    """The flows the NPSH3 data apply to and the scaling between them, for the warning and the block."""
    if len(curve) == 1 and curve[0][0] == 0.0:
        return (f"NPSH3 {'below ' if curve[0][2] else ''}{curve[0][1]:.3g} m at every flow (no flow given)")
    pts = ", ".join(f"{'< ' if b else ''}{m:.3g} m at {_m3h(v)}" for v, m, b in curve)
    return (f"NPSH3 {pts} (at N_nominal); linear between those flows, held below the lowest, x (V/V_max)^"
            f"{exponent:g} above the highest; x (N/N_nominal)^2 at other speeds")


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


def _status(ratio, bound, margin):
    if bound:
        return "ok" if ratio >= margin else "unresolved"
    return "below" if ratio < 1.0 else "approaching" if ratio < margin else "ok"


def evaluate(net, t, series) -> tuple[dict, list]:
    """({pump: npsh block}, warnings) from a run's series(path)."""
    from .fluidref import medium_of
    from .post import _r6, downsample
    blocks, warns = {}, []
    p_ambient = next((float(d["params"].get("p_ambient") or 101325.0) for d in net.elems.values()
                      if d["kind"] == "fl_system"), 101325.0)
    t = np.asarray(t, float)
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
        got = [vapour_pressure(mv, float(T)) for T in Tin]
        pv = np.array([g[0] for g in got], float)
        rho = np.array([med.d(float(T), float(p)) for T, p in zip(Tin, pin)])
        hv = np.zeros_like(pin)
        if p_.get("inlet_diameter") is not None:
            A = math.pi / 4 * float(p_["inlet_diameter"]) ** 2
            hv = 0.5 * (mflow / (rho * A)) ** 2 * rho        # rho v^2 / 2
        npsha = (pin + hv - pv) / (rho * G_STD)
        N0 = float(p_["N_nominal"])
        r = np.array([N[j] / N0 if N[j] > 0 else 1.0 for j in range(len(t))])
        blk = {"covered": True, "p_vapour_from": got[0][1] if got else None,
               "velocity_head": "with inlet_diameter" if p_.get("inlet_diameter") is not None else
               "not included (static inlet pressure: NPSHa is low by v^2/2g; give inlet_diameter)"}
        k = int(np.argmin(npsha))
        blk.update({"NPSHa_min_m": float(npsha[k]), "t_NPSHa_min": float(t[k]), "V_flow_at_min_m3h": float(V[k] * 3600),
                    "p_in_at_min_Pa": float(pin[k]), "T_in_at_min_K": float(Tin[k]), "p_vapour_at_min_Pa": float(pv[k])})
        curve = p_.get("_npsh_curve")
        if curve:
            expo = float(p_.get("npsh_flow_exponent") or 2.0)
            margin = float(p_.get("npsh_margin") or 1.5)
            req, bnd, how = [], [], []
            for j in range(len(t)):
                val, b, h = npshr(curve, max(V[j], 0.0) / r[j], expo)
                req.append(val * r[j] * r[j])
                bnd.append(b)
                how.append(h)
            req = np.array(req)
            ratio = np.where(req > 0, npsha / np.where(req > 0, req, 1.0), np.inf)
            kr = int(np.argmin(ratio))
            st = [_status(ratio[j], bnd[j], margin) for j in range(len(t))]
            worst = "below" if "below" in st else "approaching" if "approaching" in st else                 "unresolved" if "unresolved" in st else "ok"
            blk.update({"npsh3_from": p_.get("_npsh_from", "given"), "npsh3_data": describe(curve, expo),
                        "margin": margin, "status": worst,
                        "NPSH3_at_ratio_min_m": float(req[kr]), "NPSH3_is_upper_bound": bool(bnd[kr]),
                        "NPSH3_how": how[kr], "ratio_min": float(ratio[kr]), "t_ratio_min": float(t[kr]),
                        "NPSHa_at_ratio_min_m": float(npsha[kr]), "V_flow_at_ratio_min_m3h": float(V[kr] * 3600),
                        "short": worst in ("below", "approaching")})
            for level in ("below", "approaching", "unresolved"):
                hit = np.array([s == level for s in st])
                if hit.any():
                    dt = np.diff(t)
                    blk[f"{level}_from_t"] = float(t[int(np.argmax(hit))])
                    blk[f"{level}_s"] = float(np.sum(dt[hit[1:] | hit[:-1]])) if len(dt) else 0.0
            at = (f"at t = {t[kr]:.4g} s, {V[kr] * 3600:.4g} m3/h")
            ref = "NPSH3" if not bnd[kr] else "the upper bound of NPSH3"
            data = blk["npsh3_data"]
            if worst == "below":
                warns.append(f"{comp}: NPSH available {npsha[kr]:.3g} m is below NPSH3 {req[kr]:.3g} m {at} (ratio "
                             f"{ratio[kr]:.2f} < 1): the 3 % head drop is expected there; the single-phase results "
                             f"past that point are not physical ({data})")
            elif worst == "approaching":
                warns.append(f"{comp}: approaching cavitation: NPSH available {npsha[kr]:.3g} m is "
                             f"{ratio[kr]:.2f} x {ref} {req[kr]:.3g} m {at} (< {margin:g}: inception comes well "
                             f"before the 3 % drop) ({data})")
            elif worst == "unresolved":
                warns.append(f"{comp}: NPSH margin unresolved: NPSH3 is only known to be below {req[kr]:.3g} m "
                             f"{at}, and NPSH available {npsha[kr]:.3g} m is {ratio[kr]:.2f} x that bound (< {margin:g}); "
                             f"the true ratio is at least that ({data}); a cfd-translator sweep down to lower NPSH "
                             f"would resolve it")
        else:
            blk["note"] = "no npsh_required, npsh3_below or npsh_cfdt: NPSHa only"
        on = _onset(p_, t, pin, pv, r, V, p_ambient, comp)
        if on:
            blk["onset"], w = on
            warns.extend(w)
        ts, ys = downsample(t, npsha)
        blk["series"] = {"t": [_r6(x) for x in ts], "NPSHa": [_r6(x) for x in ys]}
        blocks[comp] = blk
    return blocks, warns


def _onset(p_, t, pin, pv, r, V, p_ambient, comp):
    """The eye's lowest pressure against the vapour pressure and the air-saturation pressure (FL17)."""
    if p_.get("eye_dip") is None and p_.get("pulse_amplitude") is None:
        return None
    dip0 = float(p_.get("eye_dip") or 0.0)
    amp0 = float(p_.get("pulse_amplitude") or 0.0)
    rref = float(p_.get("onset_N") or p_["N_nominal"]) / float(p_["N_nominal"])    # N_measured / N_nominal
    s = (r / rref) ** 2                                          # dip and amplitude ~ N^2
    peye = pin - (dip0 + amp0) * s
    psat_air = float(p_["air_saturation_pressure"]) if p_.get("air_saturation_pressure") is not None else p_ambient
    mv = peye - pv
    ma = peye - psat_air
    kv, ka = int(np.argmin(mv)), int(np.argmin(ma))
    ke = int(np.argmin(peye))
    raw = bool(p_.get("_onset_raw"))
    jit = float(p_["onset_jitter"]) if p_.get("onset_jitter") is not None else (ONSET_JITTER if raw else 0.0)
    out = {"eye_dip_Pa": dip0, "pulse_amplitude_Pa": amp0, "from": p_.get("_onset_from", "given"),
           "eye_dip_is": "the mean dip (inlet static mean - eye mean); p_eye_min = p_in - dip - amplitude",
           "measured_at_rpm": rref * float(p_["N_nominal"]) / RPM,
           "scaling": "x (N/N_measured)^2 (not with flow)",
           "air_saturation_pressure_Pa": psat_air,
           "air_saturation_from": "given" if p_.get("air_saturation_pressure") is not None else
           "p_ambient (the liquid saturated with air at the free surface's pressure)",
           "p_eye_min_Pa": float(peye[ke]), "t_p_eye_min": float(t[ke]),
           "vapour_margin_min_Pa": float(mv[kv]), "t_vapour_margin_min": float(t[kv]),
           "air_margin_min_Pa": float(ma[ka]), "t_air_margin_min": float(t[ka]),
           "eye_min_over_air_saturation": float(peye[ka] / psat_air),
           "cavitation_onset": bool(mv[kv] < 0), "air_release": bool(ma[ka] < 0)}
    if raw:
        out["amplitude_upper_bound"] = True
    if jit > 0:
        out["jitter_Pa"] = jit
        out["cavitation_onset_possibly_jitter"] = bool(-jit <= mv[kv] < 0)
        out["air_release_possibly_jitter"] = bool(-jit <= ma[ka] < 0)
    Vm = p_.get("onset_V_flow")
    if Vm:                                      # the flow it was measured at, moved with the speed (V ~ N)
        Vs = float(Vm) * np.asarray(r) / rref
        out["measured_at_m3h"] = float(Vm) * 3600
        far = np.abs(V - Vs) > 0.1 * Vs
        if far.any():
            out["note"] = (f"the flow left 10 % of the {float(Vm) * 3600:.4g} m3/h the dip and amplitude were measured "
                           f"at (scaled to the speed) for {float(np.mean(far)) * 100:.0f} % of the samples: they are "
                           f"not scaled with flow")
    w = []
    how = (f"p_in - dip {dip0:.4g} Pa - amplitude {amp0:.4g} Pa{' (raw: an upper bound)' if raw else ''} (at "
           f"{out['measured_at_rpm']:.5g} rpm, x (N/N_measured)^2)")
    jtxt = (f"; within {jit:.3g} Pa of it: possibly jitter{' (the raw amplitude)' if raw else ''}")
    if raw:
        w.append(f"{comp}: onset: pulse_amplitude from the eye probe's raw per-step min/max (the cfd-translator result "
                 f"has no min_f/max_f): an upper bound, conservative (the step-to-step jitter only deepens the eye "
                 f"minimum); a cfd-translator reprocess (2026.11.20.1 or later) adds min_f/max_f and removes the bias")
    if out["cavitation_onset"]:
        w.append(f"{comp}: cavitation onset: the eye's lowest pressure {peye[kv]:.4g} Pa = {how} is below the "
                 f"vapour pressure {pv[kv]:.4g} Pa at t = {t[kv]:.4g} s (by {-mv[kv]:.4g} Pa)"
                 f"{jtxt if out.get('cavitation_onset_possibly_jitter') else ''}")
    if out["air_release"]:
        w.append(f"{comp}: air release: the eye's lowest pressure {peye[ka]:.4g} Pa = {how} is below the "
                 f"air-saturation pressure {psat_air:.6g} Pa ({out['air_saturation_from']}) at t = {t[ka]:.4g} s "
                 f"(by {-ma[ka]:.4g} Pa; {out['eye_min_over_air_saturation']:.3f} of it): dissolved air comes out "
                 f"there, a cause of head loss under suction{jtxt if out.get('air_release_possibly_jitter') else ''}"
                 f"{'; with saturation at p_ambient any suction lift trips this: judge by the fraction' if p_.get('air_saturation_pressure') is None else ''}")
    if "note" in out:
        w.append(f"{comp}: onset: {out['note']}")
    return out, w
