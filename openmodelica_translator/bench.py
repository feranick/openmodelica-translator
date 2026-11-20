"""bench.py — benchmark studies with closed-form answers: is the toolchain right?
Version: 2026.11.19.28

Each case is a study plus checks [(result path, expected, tolerance, why)]; tolerances are relative unless
written {"abs": x}. Chains (chain_feat_modal, chain_b3dt_mass, chain_b3dt_inertia) run the other translator first and only when named; they are skipped
when it is not installed. A case passes when the run is ok and reliable (balance, initialisation, solver, values read
back, energy, reference) and every check holds. The expected values are computed here from the closed forms.
"""
from __future__ import annotations

import math
import os

import numpy as np


def _rc():
    st = {"components": {"V": {"kind": "step_voltage", "V": 1}, "R": {"kind": "resistor", "R": 1000},
                         "C": {"kind": "capacitor", "C": 1e-6}, "g": {"kind": "ground"}},
          "connect": [["V.p", "R.p"], ["R.n", "C.p"], ["C.n", "g"], ["V.n", "g"]],
          "outputs": {"v": "C.v"}, "analysis": {"kind": "transient", "stop_time": 0.005, "intervals": 1000},
          "derived": [{"kind": "crossing", "signal": "v", "level": 1 - math.exp(-1)}]}
    return st, [("derived.v.crossing_time_s", 1e-3, 1e-4, "tau = RC: v reaches 63.2 % at t = tau; grid "
                                                           "interpolation and dassl rtol 1e-6 stay below 1e-5"),
                ("outputs.v.final", 1 - math.exp(-5), 1e-4, "v(5 tau) = 1 - exp(-5)")]


def _rlc(kind):
    R, L, C = 10.0, 0.01, 1e-4
    w0, z = 1 / math.sqrt(L * C), R / 2 * math.sqrt(C / L)
    wd = w0 * math.sqrt(1 - z * z)
    comps = {"R": {"kind": "resistor", "R": R}, "L": {"kind": "inductor", "L": L}, "C": {"kind": "capacitor", "C": C},
             "gnd": {"kind": "ground"}}
    if kind == "step":
        comps["V"] = {"kind": "step_voltage", "V": 12, "startTime": 0.001}
        st = {"components": comps,
              "connect": [["V.p", "R.p"], ["R.n", "L.p"], ["L.n", "C.p"], ["C.n", "gnd"], ["V.n", "gnd"]],
              "outputs": {"v_C": "C.v"}, "analysis": {"kind": "transient", "stop_time": 0.03, "intervals": 15000},
              "derived": [{"kind": "step", "signal": "v_C", "t0": 0.001, "final": 12}]}
        os_ = math.exp(-math.pi * z / math.sqrt(1 - z * z)) * 100
        return st, [("derived.v_C.overshoot_pct", os_, 2e-3, "overshoot = exp(-pi zeta / sqrt(1 - zeta^2)), zeta = 0.5; "
                                                              "the grid (2 us) shifts the sampled peak by < 1e-4"),
                    ("derived.v_C.peak_time_s", math.pi / wd, 2e-3, "peak at pi / omega_d (one grid step: 5e-4)")]
    comps["Vs"] = {"kind": "signal_voltage"}
    st = {"components": comps,
          "connect": [["Vs.p", "R.p"], ["R.n", "L.p"], ["L.n", "C.p"], ["C.n", "gnd"], ["Vs.n", "gnd"]],
          "outputs": {"v_C": "C.v"},
          "analysis": {"kind": "linearize", "inputs": {"u": "Vs.v"}, "bode": [10.0, w0 / (2 * math.pi), 1000.0]}}
    g = [1 / abs(1 - (2 * math.pi * f) ** 2 * L * C + 1j * 2 * math.pi * f * R * C) for f in (10.0, w0 / (2 * math.pi), 1000.0)]
    return st, [("linearization.natural_frequencies_Hz.0", w0 / (2 * math.pi), 1e-5, "f_n = 1/(2 pi sqrt(LC))"),
                ("linearization.damping_ratios.0", z, 1e-5, "zeta = R/2 sqrt(C/L)"),
                ("linearization.bode.u->v_C.gain_dB.1", 20 * math.log10(g[1]), {"abs": 1e-3}, "|H| = 1/(2 zeta) at f_n"),
                ("linearization.bode.u->v_C.gain_dB.2", 20 * math.log10(g[2]), {"abs": 1e-3}, "|H(j 2 pi 1 kHz)|")]


def _msd(kind):
    m, c, d = 2.0, 800.0, 4.0
    wn, ze = math.sqrt(c / m), d / (2 * math.sqrt(c * m))
    if kind == "modes":
        st = {"components": {"m": {"kind": "mass", "m": m}, "k": {"kind": "spring", "c": c}, "d": {"kind": "damper", "d": d},
                             "f": {"kind": "fixed"}, "F": {"kind": "force"}},
              "connect": [["f.flange", "k.flange_a"], ["k.flange_b", "m.flange_a"], ["d.flange_a", "f.flange"],
                          ["d.flange_b", "m.flange_a"], ["F.flange", "m.flange_b"]],
              "outputs": {"s": "m.s"}, "analysis": {"kind": "linearize", "inputs": {"F": "F.f"}}}
        return st, [("linearization.natural_frequencies_Hz.0", wn / (2 * math.pi), 1e-5, "omega_n = sqrt(c/m)"),
                    ("linearization.damping_ratios.0", ze, 1e-5, "zeta = d / (2 sqrt(c m))"),
                    ("linearization.dc_gain.s.F", 1 / c, 1e-6, "static compliance 1/c")]
    st = {"components": {"m": {"kind": "mass", "m": m, "s0": 0.1}, "k": {"kind": "spring", "c": c},
                         "d": {"kind": "damper", "d": d}, "f": {"kind": "fixed"}},
          "connect": [["f.flange", "k.flange_a"], ["k.flange_b", "m.flange_a"], ["d.flange_a", "f.flange"],
                      ["d.flange_b", "m.flange_a"]],
          "outputs": {"s": "m.s"}, "analysis": {"kind": "transient", "stop_time": 2, "intervals": 20000},
          "derived": [{"kind": "crossing", "signal": "s", "level": 0, "direction": "down"}]}
    wd = wn * math.sqrt(1 - ze * ze)
    t_zero = (math.pi - math.atan(math.sqrt(1 - ze * ze) / ze)) / wd
    return st, [("derived.s.crossing_time_s", t_zero, 1e-4, "free vibration x0 e^(-zeta wn t)(cos wd t + zeta/sqrt(1-zeta^2) "
                                                            "sin wd t): first zero at (pi - atan(sqrt(1-zeta^2)/zeta))/wd")]


def _inertia():
    st = {"components": {"J": {"kind": "inertia", "J": 0.5}, "T": {"kind": "torque_step", "stepTorque": 2, "startTime": 0.1}},
          "connect": [["T.flange", "J.flange_a"]], "outputs": {"w": "J.w", "phi": "J.phi"},
          "analysis": {"kind": "transient", "stop_time": 1.0, "intervals": 1000}}
    return st, [("outputs.w.final", 2 * 0.9 / 0.5, 1e-6, "w = tau (t - t0) / J"),
                ("outputs.phi.final", 0.5 * 2 / 0.5 * 0.9 ** 2, 1e-6, "phi = tau (t - t0)^2 / (2 J)")]


def _dc_motor():
    R, L, k, J, d, V = 2.0, 1e-3, 0.1, 1e-4, 1e-4, 12.0
    st = {"components": {"M": {"kind": "dc_motor", "R": R, "L": L, "k": k, "J": J, "d": d},
                         "V": {"kind": "constant_voltage", "V": V}, "g": {"kind": "ground"}},
          "connect": [["V.p", "M.p"], ["V.n", "g"], ["M.n", "g"]], "outputs": {"w": "M.w", "i": "M.i"},
          "analysis": {"kind": "transient", "stop_time": 1.0, "intervals": 2000}}
    w_ss = k * V / (k * k + R * d)
    return st, [("outputs.w.final", w_ss, 1e-5, "steady speed k V / (k^2 + R d) (time constants ~20 ms)"),
                ("outputs.i.final", d * w_ss / k, 1e-5, "steady current d w / k")]


def _thermal(kind):
    C, G, Q, Ta = 1800.0, 0.6, 50.0, 293.15
    tau, dT = C / G, Q / G
    st = {"components": {"blk": {"kind": "heat_capacitor", "m": 2, "cp": 900, "T0": Ta},
                         "q": {"kind": "fixed_heat_flow", "Q_flow": Q},
                         "air": {"kind": "convection", "h": 10, "A": 0.06},
                         "amb": {"kind": "fixed_temperature", "T": Ta}},
          "connect": [["q.port", "blk.port"], ["blk.port", "air.solid"], ["air.fluid", "amb.port"]],
          "outputs": {"T": "blk.T"}}
    if kind == "rc":
        st["analysis"] = {"kind": "transient", "stop_time": 20000, "intervals": 4000}
        st["derived"] = [{"kind": "crossing", "signal": "T", "level": "80 degC"}]
        t80 = -tau * math.log(1 - 60.0 / dT)
        return st, [("derived.T.crossing_time_s", t80, 1e-4, "T = Ta + Q/G (1 - exp(-t G/C)); time to 80 degC"),
                    ("outputs.T.final", Ta + dT * (1 - math.exp(-20000 / tau)), 1e-6, "T(20000 s)")]
    st["analysis"] = {"kind": "steady", "max_time": 20 * tau, "intervals": 2000}
    return st, [("steady.T.value", Ta + dT * (1 - math.exp(-20)), 1e-7, "T_inf = Ta + Q/G (exp(-20) left)")]


def _two_mass():
    J1, J2, c = 0.01, 0.04, 1000.0
    st = {"components": {"J1": {"kind": "inertia", "J": J1}, "J2": {"kind": "inertia", "J": J2},
                         "sh": {"kind": "rot_spring_damper", "c": c, "d": 0.0}, "T": {"kind": "torque"}},
          "connect": [["T.flange", "J1.flange_a"], ["J1.flange_b", "sh.flange_a"], ["sh.flange_b", "J2.flange_a"]],
          "outputs": {"w2": "J2.w"}, "analysis": {"kind": "linearize", "inputs": {"tau": "T.tau"}}}
    w = math.sqrt(c * (J1 + J2) / (J1 * J2))
    return st, [("linearization.natural_frequencies_Hz.0", w / (2 * math.pi), 1e-5,
                 "two inertias on a shaft: omega^2 = c (J1 + J2) / (J1 J2) (plus a rigid-body pair)")]


def _pi_loop():
    k, T, K, tau = 8.0, 0.05, 1.5, 0.2
    st = {"components": {"r": {"kind": "step", "height": 1, "startTime": 0.0}, "fb": {"kind": "feedback"},
                         "pi": {"kind": "pi", "k": k, "T": T}, "p": {"kind": "first_order", "k": K, "T": tau}},
          "connect": [["r.y", "fb.u1"], ["p.y", "fb.u2"], ["fb.y", "pi.u"], ["pi.y", "p.u"]],
          "outputs": {"y": "p.y"}, "analysis": {"kind": "transient", "stop_time": 3, "intervals": 30000},
          "derived": [{"kind": "step", "signal": "y", "final": 1, "initial": 0}]}
    from scipy import signal
    t = np.linspace(0, 3, 300001)
    _, y = signal.step(signal.lti([k * K * T, k * K], [tau * T, T * (1 + k * K), k * K]), T=t)
    i = int(np.argmax(y))
    return st, [("derived.y.overshoot_pct", (y[i] - 1) * 100, 2e-3, "Y/R = kK(Ts+1)/(tau T s^2 + T(1+kK)s + kK): "
                                                                     "underdamped with a zero (scipy.signal, 1e-5 s grid)"),
                ("derived.y.peak_time_s", t[i], 2e-3, "the same closed loop's peak (grid 1e-4 s)")]


def _sweep_rc():
    st, _ = _rc()
    st = dict(st)
    st["sweep"] = {"params": {"R.R": [500, "1 kOhm", 2000]}, "report": ["derived.v.crossing_time_s"]}
    checks = [(f"sweep.table.{i}.values.derived.v.crossing_time_s", R * 1e-6, 1e-4, f"tau = R C with R = {R:g} Ohm")
              for i, R in enumerate((500.0, 1000.0, 2000.0))]
    checks.append(("sweep.builds", 0.5, {"abs": 0.5}, "every point runs on one build (0 with the reference solver)"))
    checks.append(("sweep.reliable_points", 3, 0, "every point reliable"))
    return st, checks


RHO_W, CP_W = 995.6, 4177.0             # MSL FluidHeatFlow.Media.Water


def _pipe(**kw):
    d = {"kind": "pipe", "m": 0.5, "V_flowNominal": 1e-4, "dpNominal": 2000, "V_flowLaminar": 1e-5, "dpLaminar": 100}
    d.update(kw)
    return d


def _coolant_open():
    """Water from an ambient through a pump (0.1 l/s) and a pipe heated with 2 kW to an ambient: the outlet
    temperature rises as a first-order lag, tau = m cv / (m_flow cp) = 5.0 s, to T_in + Q / (m_flow cp)."""
    V, Q, m = 1e-4, 2000.0, 0.5
    mf = RHO_W * V
    dT, tau = Q / (mf * CP_W), m * CP_W / (mf * CP_W)
    st = {"components": {"a1": {"kind": "ambient", "constantAmbientTemperature": "20 degC"},
                         "pump": {"kind": "volume_flow", "constantVolumeFlow": "0.1 l/s"}, "pipe": _pipe(m=m),
                         "a2": {"kind": "ambient"}, "q": {"kind": "fixed_heat_flow", "Q_flow": Q}},
          "connect": [["a1.flowPort", "pump.flowPort_a"], ["pump.flowPort_b", "pipe.flowPort_a"],
                      ["pipe.flowPort_b", "a2.flowPort"], ["q.port", "pipe.heatPort"]],
          "outputs": {"T": "pipe.T", "dp": "pipe.dp"},
          # tolerance 1e-8: dassl's relative tolerance acts on the absolute temperature (~300 K), the rise is 4.8 K
          "analysis": {"kind": "transient", "stop_time": 40, "intervals": 4000, "tolerance": 1e-8},
          "derived": [{"kind": "crossing", "signal": "T", "level": 293.15 + dT * (1 - math.exp(-1))}]}
    sl, VL = 100 / 1e-5, 1e-5
    k = (2000 - sl * 1e-4) / (1e-4 - VL) ** 2
    return st, [("derived.T.crossing_time_s", tau, 1e-4, "tau = m cv / (m_flow cp): 63.2 % of the rise at t = tau"),
                ("outputs.T.final", 293.15 + dT * (1 - math.exp(-40 / tau)), 1e-5, "T(40 s), first-order lag"),
                ("outputs.dp.final", sl * V + k * (V - VL) ** 2, 1e-9, "the friction curve through the nominal point")]


def _coolant_loop():
    """A closed water loop (pressure reference, pump 0.1 l/s), a heater (1 kW) and a cooler (200 W/K to 20 degC):
    steady cooler outlet T_amb + Q/G = 25 degC, heater outlet + Q / (m_flow cp)."""
    V, Q, G = 1e-4, 1000.0, 200.0
    st = {"components": {"pref": {"kind": "pressure_reference", "p": "1 bar"},
                         "pump": {"kind": "volume_flow", "constantVolumeFlow": V}, "heater": _pipe(), "cooler": _pipe(),
                         "q": {"kind": "fixed_heat_flow", "Q_flow": Q}, "g": {"kind": "thermal_conductor", "G": G},
                         "amb": {"kind": "fixed_temperature", "T": "20 degC"}, "tHot": {"kind": "fluid_temperature_sensor"}},
          "connect": [["pump.flowPort_b", "heater.flowPort_a"], ["heater.flowPort_b", "cooler.flowPort_a"],
                      ["cooler.flowPort_b", "pump.flowPort_a"], ["pref.flowPort", "pump.flowPort_a"],
                      ["q.port", "heater.heatPort"], ["cooler.heatPort", "g.port_a"], ["g.port_b", "amb.port"],
                      ["tHot.flowPort", "heater.flowPort_b"]],
          "outputs": {"T_hot": "tHot.y", "T_cool": "cooler.T"},
          "analysis": {"kind": "steady", "max_time": 3000}}
    Tc = 293.15 + Q / G
    return st, [("steady.T_cool.value", Tc, 1e-5, "T_amb + Q/G"),
                ("steady.T_hot.value", Tc + Q / (RHO_W * V * CP_W), 1e-5, "+ Q / (m_flow cp)")]


def _coolant_hydraulics():
    """A pressure source (5 kPa) driving water through a pipe (quadratic friction) and a half-open valve between
    two ambients: the operating point solves dp_source = friction(V) + dp0 (V/Kv)^2 (a quadratic in V)."""
    dps, VN, dpN, Kv1, dp0 = 5000.0, 1e-4, 2000.0, 2e-4, 1e5
    VL, dpL = 0.1 * VN, 0.01 * dpN
    sl = dpL / VL
    k = (dpN - sl * VN) / (VN - VL) ** 2
    Kv = Kv1 * (0.01 + 0.99 * 0.5)
    a = dp0 / Kv ** 2
    A, B, Cq = k + a, sl - 2 * k * VL, k * VL * VL - dps          # V > VL
    V = (-B + math.sqrt(B * B - 4 * A * Cq)) / (2 * A)
    st = {"components": {"a1": {"kind": "ambient"}, "src": {"kind": "pressure_increase", "constantPressureIncrease": dps},
                         "pipe": {"kind": "pipe", "V_flowNominal": VN, "dpNominal": dpN},
                         "v": {"kind": "valve", "Kv1": Kv1, "dp0": dp0}, "y": {"kind": "constant", "k": 0.5},
                         "a2": {"kind": "ambient"}},
          "connect": [["a1.flowPort", "src.flowPort_a"], ["src.flowPort_b", "pipe.flowPort_a"],
                      ["pipe.flowPort_b", "v.flowPort_a"], ["v.flowPort_b", "a2.flowPort"], ["y.y", "v.y"]],
          "outputs": {"V": "pipe.V_flow", "dp_valve": "v.dp"}, "analysis": {"kind": "transient", "stop_time": 1}}
    return st, [("outputs.V.final", V, 1e-7, "root of (k + dp0/Kv^2) V^2 + (dpL/VL - 2 k VL) V + k VL^2 = dp_source"),
                ("outputs.dp_valve.final", a * V * V, 1e-7, "dp0 (V/Kv)^2")]


DCPM = {"kind": "dc_machine", "VaNominal": 100, "IaNominal": 100, "wNominal": "1425 rpm", "Ra": 0.05, "La": 0.0015,
        "Jr": 0.15}                       # MSL's DcPermanentMagnetData


def _dcpm(losses: bool):
    """The MSL DC_PermanentMagnet (example data) at 100 V; nominal load torque from t = 1 s. Without losses:
    no-load speed Va/k, then wNominal at IaNominal (k = (VaNominal - Ra IaNominal)/wNominal). With linear
    friction (power_w 1, 100 W at wNominal) and core losses (50 W at the nominal induced voltage): the steady
    state of the linear equations."""
    wN = 1425 * 2 * math.pi / 60
    Va, R, IaN = 100.0, 0.05, 100.0
    k = (Va - R * IaN) / wN
    TL = k * IaN
    m = dict(DCPM)
    st = {"components": {"V": {"kind": "constant_voltage", "V": Va}, "g": {"kind": "ground"}, "M": m,
                         "load": {"kind": "torque_step", "stepTorque": -TL, "startTime": 1}},
          "connect": [["V.p", "M.pin_ap"], ["M.pin_an", "V.n"], ["V.n", "g"], ["M.flange", "load.flange"]],
          "outputs": {"w": "M.wMechanical", "i": "M.ia", "P_loss": "M.lossPowerTotal", "tau": "M.tauShaft"}}
    if not losses:
        st["analysis"] = {"kind": "transient", "stop_time": 2, "intervals": 2000}
        st["derived"] = [{"kind": "window", "signal": "w", "t0": 0.8, "t1": 0.99}]
        return st, [("derived.w.mean", Va / k, 1e-4, "no load: w = Va / k (mechanical time constant Jr R / k^2 = 18.5 ms)"),
                    ("outputs.w.final", wN, 1e-5, "nominal load at nominal voltage: wNominal"),
                    ("outputs.i.final", IaN, 1e-4, "IaNominal"),
                    ("outputs.P_loss.final", R * IaN ** 2, 1e-4, "Ra IaNominal^2")]
    m.update({"PRef_friction": 100.0, "power_w_friction": 1.0, "PRef_core": 50.0})
    st["analysis"] = {"kind": "steady", "max_time": 5}
    cf = 100.0 / wN ** 2
    Gc = 50.0 / (Va - R * IaN) ** 2
    w = (k * Va / R - TL) / (k * k * (1 + R * Gc) / R + cf)
    ila = (Va - k * w * (1 + R * Gc)) / R
    ia = ila + Gc * k * w
    loss = R * ia ** 2 + Gc * (k * w) ** 2 + cf * w ** 2
    return st, [("steady.w.value", w, 1e-5, "steady speed of the linear machine equations"),
                ("steady.i.value", ia, 1e-5, "armature current = airgap + core current"),
                ("steady.P_loss.value", loss, 1e-5, "Ra ia^2 + Gc (k w)^2 + cf w^2"),
                ("steady.tau.value", TL, 1e-5, "shaft torque = the load")]


def _self_heating():
    """A 10 ohm resistor on 10 V heats a 100 J/K capacitor through its heatPort, cooled by 0.5 W/K to 20 degC:
    T = T_amb + P/G (1 - exp(-t G/C)), tau = C/G = 200 s, steady + 20 K (one way: alpha = 0)."""
    P, G, Cth = 10.0, 0.5, 100.0
    st = {"components": {"v": {"kind": "constant_voltage", "V": 10}, "g": {"kind": "ground"}, "r": {"kind": "resistor", "R": 10},
                         "cr": {"kind": "heat_capacitor", "C": Cth}, "gr": {"kind": "thermal_conductor", "G": G},
                         "amb": {"kind": "fixed_temperature", "T": "20 degC"}},
          "connect": [["v.p", "r.p"], ["r.n", "v.n"], ["v.n", "g"], ["r.heatPort", "cr.port"], ["cr.port", "gr.port_a"],
                      ["gr.port_b", "amb.port"]],
          "outputs": {"T": "cr.T", "T_r": "r.T_heatPort"},
          "analysis": {"kind": "transient", "stop_time": 1000, "intervals": 1000, "tolerance": 1e-8},
          "derived": [{"kind": "crossing", "signal": "T", "level": 293.15 + P / G * (1 - math.exp(-1))}]}
    return st, [("derived.T.crossing_time_s", Cth / G, 1e-4, "tau = C/G"),
                ("outputs.T.final", 293.15 + P / G * (1 - math.exp(-1000 * G / Cth)), 1e-5, "T(1000 s)"),
                ("outputs.T_r.final", 293.15 + P / G * (1 - math.exp(-1000 * G / Cth)), 1e-5, "the resistor at its port's T")]


def _damper_heating():
    """A flywheel (1 kg m2, 100 rad/s) spins down through a rotational damper (0.5 N m s/rad) whose heatPort feeds a
    1 kJ/K capacitor: w = w0 exp(-d t/J); the capacitor gains J w0^2 / 2 (1 - exp(-2 d t / J))."""
    J, w0, d, Cth = 1.0, 100.0, 0.5, 1000.0
    st = {"components": {"J": {"kind": "inertia", "J": J, "w0": w0}, "d": {"kind": "rot_damper", "d": d},
                         "f": {"kind": "rot_fixed"}, "c": {"kind": "heat_capacitor", "C": Cth}},
          "connect": [["J.flange_b", "d.flange_a"], ["d.flange_b", "f.flange"], ["d.heatPort", "c.port"]],
          "outputs": {"T": "c.T", "w": "J.w"}, "analysis": {"kind": "transient", "stop_time": 4, "intervals": 2000}}
    return st, [("outputs.T.final", 293.15 + J * w0 ** 2 / 2 * (1 - math.exp(-2 * d * 4 / J)) / Cth, 1e-5,
                 "the kinetic energy lost, as heat"),
                ("outputs.w.final", w0 * math.exp(-d * 4 / J), 1e-4, "w0 exp(-d t / J)")]


def _dcpm_heating():
    """The MSL DC machine at nominal load with its heatPort on a 2 kJ/K capacitor cooled by 10 W/K to 20 degC:
    every loss (here Ra IaNominal^2 = 500 W) heats it: steady T = T_amb + 500/10 = 70 degC, and so is Ta."""
    st, _ = _dcpm(False)
    st["components"].update({"cap": {"kind": "heat_capacitor", "C": 2000}, "gth": {"kind": "thermal_conductor", "G": 10},
                             "amb": {"kind": "fixed_temperature", "T": 293.15}})
    st["connect"] += [["M.heatPort", "cap.port"], ["cap.port", "gth.port_a"], ["gth.port_b", "amb.port"]]
    st["outputs"].update({"T": "cap.T", "Ta": "M.Ta"})
    st["analysis"] = {"kind": "steady", "max_time": 5000}
    st.pop("derived", None)
    return st, [("steady.T.value", 293.15 + 500 / 10, 1e-5, "T_amb + Ra IaNominal^2 / G"),
                ("steady.Ta.value", 293.15 + 500 / 10, 1e-5, "the armature at the heat port's T"),
                ("steady.w.value", 1425 * 2 * math.pi / 60, 1e-5, "alpha20a = 0: the speed does not depend on T")]


def _limiter_loop():
    """P control (k = 5) of a first-order plant (T = 1 s) through a limiter (+-20): saturated until 5 (10 - y) = 20,
    i.e. y = 6, reached at t = 0.1 - ln(1 - 6/20); then y -> 50/6 with tau = 1/6 s (events in the reference)."""
    st = {"components": {"sp": {"kind": "step", "height": 10, "startTime": 0.1}, "fb": {"kind": "feedback"},
                         "kp": {"kind": "gain", "k": 5}, "lim": {"kind": "limiter", "uMax": 20},
                         "plant": {"kind": "first_order", "k": 1, "T": 1}},
          "connect": [["sp.y", "fb.u1"], ["fb.y", "kp.u"], ["kp.y", "lim.u"], ["lim.y", "plant.u"], ["plant.y", "fb.u2"]],
          "outputs": {"y": "plant.y"}, "analysis": {"kind": "transient", "stop_time": 2, "intervals": 4000},
          "derived": [{"kind": "crossing", "signal": "y", "level": 6.0}]}
    tc = 0.1 - math.log(1 - 6 / 20)
    return st, [("derived.y.crossing_time_s", tc, 1e-4, "the end of saturation: 20 (1 - exp(-(t - 0.1))) = 6"),
                ("outputs.y.final", 50 / 6 + (6 - 50 / 6) * math.exp(-(2 - tc) * 6), 1e-5, "then the linear loop")]


def _rectifier():
    """Half-wave rectifier (IdealDiode, Vknee 0.7 V, Ron = Goff = 1e-5) on 10 V 50 Hz into 100 ohm: the mean
    current over a period from MSL's characteristic (it switches at vs = Vknee (1 + R Goff))."""
    V, Vk, R, Ron, Goff = 10.0, 0.7, 100.0, 1e-5, 1e-5
    th = math.asin(Vk * (1 + R * Goff) / V)
    a = Vk * (1 - Ron * Goff)
    mean = (2 * V * math.cos(th) - a * (math.pi - 2 * th)) / (2 * math.pi * (R + Ron)) \
        - Goff * 2 * V * math.cos(th) / (2 * math.pi * (1 + R * Goff))
    st = {"components": {"vs": {"kind": "sine_voltage", "V": V, "f": 50}, "d": {"kind": "ideal_diode", "Vknee": Vk},
                         "rl": {"kind": "resistor", "R": R}, "g": {"kind": "ground"}},
          "connect": [["vs.p", "d.p"], ["d.n", "rl.p"], ["rl.n", "g"], ["vs.n", "g"]],
          "outputs": {"i": "d.i"}, "analysis": {"kind": "transient", "stop_time": 0.04, "intervals": 4000},
          "derived": [{"kind": "window", "signal": "i", "t0": 0.02, "t1": 0.04}]}
    return st, [("derived.i.mean", mean, 1e-3, "mean of (vs - Vknee)/(R + Ron) over the conduction angle"),
                ("outputs.i.max", (V - a) / (R + Ron), 1e-4, "peak (V - Vknee)/(R + Ron)")]


def _slew():
    """A unit step through a slew-rate limiter (Rising 2/s, Td 1 ms): a ramp of 2/s until (u - y)/Td = 2, i.e.
    y = 1 - 2 Td, then an exponential with Td."""
    Ri, Td = 2.0, 1e-3
    t1 = 0.1 + (1 - Ri * Td) / Ri
    st = {"components": {"u": {"kind": "step", "height": 1, "startTime": 0.1},
                         "s": {"kind": "slew_rate_limiter", "Rising": Ri, "Td": Td}},
          "connect": [["u.y", "s.u"]], "outputs": {"y": "s.y"},
          "analysis": {"kind": "transient", "stop_time": 0.6, "intervals": 6000},
          "derived": [{"kind": "crossing", "signal": "y", "level": 0.5}]}
    return st, [("derived.y.crossing_time_s", 0.1 + 0.5 / Ri, 1e-4, "on the ramp"),
                ("outputs.y.final", 1 - Ri * Td * math.exp(-(0.6 - t1) / Td), 1e-6, "after the ramp")]


def _dcpm_brush():
    """The DC machine with a 2 V brush drop (above ILinear = 1 A) at nominal load: k = (Va - Ra IaN - 2)/wNominal;
    steady ia = TL / k, w = (Va - 2 - Ra ia) / k (the brush in its constant-drop mode)."""
    st, _ = _dcpm(False)
    st["components"]["M"].update({"V_brush": 2.0, "ILinear_brush": 1.0})
    st["analysis"] = {"kind": "steady", "max_time": 5}
    st.pop("derived", None)
    wN = 1425 * 2 * math.pi / 60
    TL = (100 - 5) / wN * 100
    k = (100 - 5 - 2) / wN
    ia = TL / k
    return st, [("steady.i.value", ia, 1e-5, "ia = TL / k"), ("steady.w.value", (100 - 2 - 0.05 * ia) / k, 1e-5,
                                                               "w = (Va - V_brush - Ra ia) / k")]


def _three_phase(load):
    """A symmetric 230 V 50 Hz source (star, grounded): star R-L load (10 ohm, 20 mH, star point floating) or a
    delta of 10 ohm resistors. Steady state over the last period: the quasi-RMS line current and P, Q from the
    phasors (star: I = V/|Z|; delta: line current sqrt(3) times the phase current)."""
    V, f, R, L = 230.0, 50.0, 10.0, 0.02
    comps = {"src": {"kind": "ac_voltage", "V_rms": V, "f": f}, "s1": {"kind": "star"}, "g": {"kind": "ground"},
             "irms": {"kind": "ac_current_rms"}, "pw": {"kind": "ac_power"}, "R": {"kind": "ac_resistor", "R": R}}
    conns = [["src.plug_n", "s1.plug_p"], ["s1.pin_n", "g"], ["src.plug_p", "pw.plug_p"], ["pw.plug_ni", "irms.plug_p"],
             ["pw.plug_nv", "s1.plug_p"], ["irms.plug_n", "R.plug_p"]]
    if load == "star":
        comps.update({"L": {"kind": "ac_inductor", "L": L}, "s2": {"kind": "star"}})
        conns += [["R.plug_n", "L.plug_p"], ["L.plug_n", "s2.plug_p"]]
        Z = complex(R, 2 * math.pi * f * L)
        I = V / abs(Z)
        P, Q = 3 * I * I * R, 3 * I * I * Z.imag
    else:
        comps["d"] = {"kind": "delta"}
        conns += [["R.plug_n", "d.plug_n"], ["d.plug_p", "R.plug_p"]]
        I, P, Q = 3 * V / R, 9 * V * V / R, 0.0
    st = {"components": comps, "connect": conns, "outputs": {"I": "irms.I", "P": "pw.P", "Q": "pw.Q"},
          "analysis": {"kind": "transient", "stop_time": 0.2, "intervals": 4000},
          "derived": [{"kind": "window", "signal": s_, "t0": 0.18, "t1": 0.2} for s_ in ("I", "P", "Q")]}
    checks = [("derived.I.mean", I, 1e-5, "the line current (quasi RMS)"), ("derived.P.mean", P, 1e-5, "3 I^2 R")]
    checks.append(("derived.Q.mean", Q, 1e-5, "3 I^2 X") if Q else ("derived.Q.mean", 0.0, {"abs": 1e-3 * P}, "no reactance"))
    return st, checks


W50 = 2 * math.pi * 50
IM_DATA = {"kind": "induction_machine", "p": 2, "fsNominal": 50, "Rs": 0.03, "Rr": 0.04, "Jr": 0.29,
           "Lssigma": 3 * (1 - math.sqrt(1 - 0.0667)) / W50, "Lrsigma": 3 * (1 - math.sqrt(1 - 0.0667)) / W50,
           "Lm": 3 * math.sqrt(1 - 0.0667) / W50}                      # MSL IM_SquirrelCageData
PM_DATA = {"kind": "pmsm", "p": 2, "fsNominal": 50, "Rs": 0.03, "Jr": 0.29, "Lssigma": 0.1 / W50,
           "Lmd": 0.3 / W50, "Lmq": 0.3 / W50, "VsOpenCircuit": 112.3}  # MSL SM_PermanentMagnetData


def im_steady(V, f, T, d=IM_DATA):
    """Slip, speed and RMS stator current of the induction machine at load torque T (equivalent circuit, per phase)."""
    w = 2 * math.pi * f

    def tq(s):
        Zs = d["Rs"] + 1j * w * d["Lssigma"]
        Zm = 1j * w * d["Lm"]
        Zr = d["Rr"] / s + 1j * w * d["Lrsigma"]
        Is = V / (Zs + Zm * Zr / (Zm + Zr))
        Ir = Is * Zm / (Zm + Zr)
        return 3 * d["p"] / w * abs(Ir) ** 2 * d["Rr"] / s, abs(Is)
    lo, hi = 1e-9, 0.15
    for _ in range(200):
        m_ = (lo + hi) / 2
        lo, hi = (m_, hi) if tq(m_)[0] < T else (lo, m_)
    s_ = (lo + hi) / 2
    return s_, w / d["p"] * (1 - s_), tq(s_)[1]


def _im_grid():
    """The MSL induction machine (IM_SquirrelCageData, Y) direct on a 100 V 50 Hz grid, 0.29 kg m2 load, 100 N.m from
    0.5 s: the steady speed and current from the equivalent circuit (Rs, Lssigma, Lm, Lrsigma, Rr/s per phase)."""
    s_, w, I = im_steady(100.0, 50.0, 100.0)
    st = {"components": {"grid": {"kind": "three_phase_grid", "V_rms": 100, "f": 50}, "M": dict(IM_DATA),
                         "irms": {"kind": "ac_current_rms"}, "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "torque_step", "stepTorque": -100, "startTime": 0.5}},
          "connect": [["grid.plug", "irms.plug_p"], ["irms.plug_n", "M.plug"], ["M.flange", "load.flange_a"],
                      ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical", "I": "irms.I", "T": "M.tauElectrical"},
          "analysis": {"kind": "transient", "stop_time": 2.5, "intervals": 5000},
          "derived": [{"kind": "window", "signal": x, "t0": 2.3, "t1": 2.5} for x in ("w", "I", "T")]}
    return st, [("derived.w.mean", w, 1e-4, f"equivalent circuit: slip {s_:.5f}"),
                ("derived.I.mean", I, 1e-3, "RMS stator current of the equivalent circuit"),
                ("derived.T.mean", 100.0, 1e-4, "electrical torque = the load")]


def _pmsm_current():
    """The MSL PMSM (SM_PermanentMagnetData, no damper cage) fed by the ideal current drive, Id = -53.5, Iq = 84.6 A
    RMS: torque 3/2 p psiM sqrt(2) Iq (Ld = Lq), psiM = sqrt(2) VsOpenCircuit/(2 pi fsNominal); against a quadratic
    load (181.4 N.m at 157.08 rad/s) the speed settles where the torques meet."""
    psi = math.sqrt(2) * 112.3 / W50
    T = 1.5 * 2 * psi * math.sqrt(2) * 84.6
    wN = 157.07963267949
    st = {"components": {"drv": {"kind": "pmsm_current_drive", "p": 2}, "M": dict(PM_DATA),
                         "id": {"kind": "constant", "k": -53.5}, "iq": {"kind": "constant", "k": 84.6}, "gm": {"kind": "ground"},
                         "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "quadratic_speed_load", "tau_nominal": -181.4, "w_nominal": wN,
                                "TorqueDirection": False}},
          "connect": [["drv.plug", "M.plug"], ["drv.flange", "M.flange"], ["id.y", "drv.id"], ["iq.y", "drv.iq"],
                      ["M.starpoint", "gm"],
                      ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical", "T": "M.tauElectrical"},
          "analysis": {"kind": "transient", "stop_time": 3, "intervals": 6000},
          "derived": [{"kind": "window", "signal": x, "t0": 2.8, "t1": 3} for x in ("w", "T")]}
    return st, [("derived.T.mean", T, 1e-4, "3/2 p psiM sqrt(2) Iq"),
                ("derived.w.mean", wN * math.sqrt(T / 181.4), 1e-4, "where the quadratic load equals it")]


def _im_vf():
    """The same induction machine started by a V/f inverter (0 -> 50 Hz in 1 s, 100 V at 50 Hz), 100 N.m from
    1.2 s: at 50 Hz the grid's steady speed."""
    s_, w, I = im_steady(100.0, 50.0, 100.0)
    st = {"components": {"inv": {"kind": "vf_inverter", "VNominal": 100, "fNominal": 50},
                         "f": {"kind": "ramp", "height": 50, "duration": 1}, "M": dict(IM_DATA),
                         "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "torque_step", "stepTorque": -100, "startTime": 1.2}},
          "connect": [["f.y", "inv.f"], ["inv.plug", "M.plug"], ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical"}, "analysis": {"kind": "transient", "stop_time": 3, "intervals": 6000},
          "derived": [{"kind": "window", "signal": "w", "t0": 2.8, "t1": 3}]}
    return st, [("derived.w.mean", w, 1e-4, "the equivalent circuit at 50 Hz, 100 V, 100 N.m")]


def _pmd_grid():
    """The PMSM with damper cage on a 112.3 V 50 Hz grid (its open-circuit voltage), started at synchronous speed,
    50 N.m load from 0.2 s: it stays synchronous (w = 2 pi 50 / p) and the electrical torque settles at the load."""
    st = {"components": {"g": {"kind": "three_phase_grid", "V_rms": 112.3, "f": 50},
                         "M": dict(PM_DATA, kind="pmsm_damper", Lrsigmad=0.05 / W50, Rrd=0.04, w0=W50 / 2),
                         "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "torque_step", "stepTorque": -50, "startTime": 0.2}},
          "connect": [["g.plug", "M.plug"], ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical", "T": "M.tauElectrical"},
          "analysis": {"kind": "transient", "stop_time": 1.5, "intervals": 3000},
          "derived": [{"kind": "window", "signal": x, "t0": 1.3, "t1": 1.5} for x in ("w", "T")]}
    return st, [("derived.w.mean", W50 / 2, 1e-6, "synchronous speed"),
                ("derived.T.mean", 50.0, 1e-4, "the load torque")]


def _quad_load():
    """An inertia (0.5 kg.m2) driven by 10 N.m against a quadratic load (-20 N.m at 10 rad/s): J w' = 10 - 0.2 w^2,
    w = sqrt(50) tanh(t sqrt(2) / J)."""
    st = {"components": {"src": {"kind": "constant_torque", "tau_constant": 10.0}, "J": {"kind": "inertia", "J": 0.5},
                         "tl": {"kind": "quadratic_speed_load", "tau_nominal": -20.0, "w_nominal": 10.0,
                                "TorqueDirection": False}},
          "connect": [["src.flange", "J.flange_a"], ["J.flange_b", "tl.flange"]],
          "outputs": {"w": "J.w"}, "analysis": {"kind": "transient", "stop_time": 2, "intervals": 4000},
          "derived": [{"kind": "crossing", "signal": "w", "level": math.sqrt(50) / 2}]}
    tau = 0.5 / math.sqrt(2)
    return st, [("derived.w.crossing_time_s", tau * math.atanh(0.5), 1e-4, "w = sqrt(50) tanh(t / tau); grid interpolation"),
                ("outputs.w.final", math.sqrt(50) * math.tanh(2 / tau), 1e-6, "w(2 s)")]


# ---- quasi-static (V3.4): the same machines as phasor models; no electrical transients, so the closed forms hold
# to the solver's tolerance (no ripple to average)
QS_IM = {k: v for k, v in IM_DATA.items() if k != "Lszero"}
QS_IM["kind"] = "qs_induction_machine"


def _im_phasor(V, f, s_, d=IM_DATA):
    """The stator current phasor of the equivalent circuit at slip s_ (V real, RMS)."""
    w = 2 * math.pi * f
    Zs = d["Rs"] + 1j * w * d["Lssigma"]
    Zm = 1j * w * d["Lm"]
    Zr = d["Rr"] / s_ + 1j * w * d["Lrsigma"]
    return V / (Zs + Zm * Zr / (Zm + Zr))


def _qs_im_grid():
    """The QS induction machine on a QS 100 V 50 Hz grid, 100 N.m from 0.5 s: speed, current and power from the
    equivalent circuit (qs_current_rms, qs_power against a grounded star)."""
    s_, w, I = im_steady(100.0, 50.0, 100.0)
    P = 3 * (100.0 * _im_phasor(100.0, 50.0, s_).conjugate()).real
    st = {"components": {"grid": {"kind": "qs_grid", "V_rms": 100, "f": 50}, "M": dict(QS_IM),
                         "irms": {"kind": "qs_current_rms"}, "pw": {"kind": "qs_power"}, "sv": {"kind": "qs_star"},
                         "gv": {"kind": "qs_ground"}, "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "torque_step", "stepTorque": -100, "startTime": 0.5}},
          "connect": [["grid.plug", "irms.plug_p"], ["irms.plug_n", "pw.plug_p"], ["pw.plug_ni", "M.plug"],
                      ["pw.plug_nv", "sv.plug_p"], ["sv.pin_n", "gv.pin"], ["M.flange", "load.flange_a"],
                      ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical", "I": "irms.I", "T": "M.tauElectrical", "P": "pw.P"},
          "analysis": {"kind": "transient", "stop_time": 2, "intervals": 2000},
          "derived": [{"kind": "window", "signal": x, "t0": 1.8, "t1": 2} for x in ("w", "I", "T", "P")]}
    return st, [("derived.w.mean", w, 1e-6, f"equivalent circuit: slip {s_:.5f}"),
                ("derived.I.mean", I, 1e-5, "RMS stator current of the equivalent circuit"),
                ("derived.T.mean", 100.0, 1e-5, "electrical torque = the load"),
                ("derived.P.mean", P, 1e-5, "3 Re(V conj(I)) of the equivalent circuit")]


def _qs_pmsm_current():
    """The QS PMSM fed by the QS current drive (Id -53.5, Iq 84.6 A RMS): constant torque 3 p psiM Iq from the
    start, so the speed against the quadratic load is w_inf tanh(t sqrt(T k)/J) exactly; the stator voltage
    |Rs I + j p w ((Lssigma + Lmd) Id + psiM, (Lssigma + Lmq) Iq)| at the end."""
    from .catalog import QS_DRIVE_RP as Rp
    psiM = 112.3 / W50
    Id, Iq = -53.5, 84.6
    k = 181.4 / 157.07963267949 ** 2
    J = 0.29 + PM_DATA["Jr"]
    L = PM_DATA["Lssigma"] + PM_DATA["Lmd"]                     # Lmd = Lmq
    T0 = 3 * 2 * psiM * Iq
    w_end = math.sqrt(T0 / k) * math.tanh(3.0 * math.sqrt(T0 * k) / J)   # Rp changes it by ~1e-6

    def machine(w_):
        """The machine's current (the drive's 1 MOhm takes V/Rp), voltage and torque at speed w_ (rotor frame)."""
        ws = 2 * w_
        Z = PM_DATA["Rs"] + 1j * ws * L
        Im = (complex(Id, Iq) - 1j * ws * psiM / Rp) / (1 + Z / Rp)
        return Im, Z * Im + 1j * ws * psiM
    Im, Vc = machine(w_end)
    T, V = 3 * 2 * psiM * Im.imag, abs(Vc)
    st = {"components": {"drv": {"kind": "qs_pmsm_current_drive", "p": 2}, "M": dict(PM_DATA, kind="qs_pmsm"),
                         "id": {"kind": "constant", "k": Id}, "iq": {"kind": "constant", "k": Iq},
                         "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "quadratic_speed_load", "tau_nominal": -181.4, "w_nominal": 157.07963267949,
                                "TorqueDirection": False}},
          "connect": [["drv.plug", "M.plug"], ["drv.flange", "M.flange"], ["id.y", "drv.id"], ["iq.y", "drv.iq"],
                      ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical", "T": "M.tauElectrical", "V": "M.abs_vs[1]"},
          "analysis": {"kind": "transient", "stop_time": 3, "intervals": 3000}}
    return st, [("outputs.T.final", T, 1e-7, "3 p psiM Iq (Ld = Lq), Iq less the drive resistor's share"),
                ("outputs.w.final", w_end, 1e-5, "w_inf tanh(3 s sqrt(T k)/J)"),
                ("outputs.V.final", V, 1e-5, "the dq phasor voltage at the final speed")]


def _qs_im_vf():
    """The QS induction machine started by the QS V/f inverter (0 -> 50 Hz in 1 s, 100 V at 50 Hz), 100 N.m from
    1.2 s: the grid's steady speed."""
    s_, w, I = im_steady(100.0, 50.0, 100.0)
    st = {"components": {"inv": {"kind": "qs_vf_inverter", "VNominal": 100, "fNominal": 50},
                         "f": {"kind": "ramp", "height": 50, "duration": 1}, "M": dict(QS_IM),
                         "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "torque_step", "stepTorque": -100, "startTime": 1.2}},
          "connect": [["f.y", "inv.f"], ["inv.plug", "M.plug"], ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical"}, "analysis": {"kind": "transient", "stop_time": 2.5, "intervals": 2500},
          "derived": [{"kind": "window", "signal": "w", "t0": 2.3, "t1": 2.5}]}
    return st, [("derived.w.mean", w, 1e-6, "the equivalent circuit at 50 Hz, 100 V, 100 N.m")]


def _qs_pmd_grid():
    """The QS PMSM with damper cage on a QS 112.3 V 50 Hz grid, started synchronous, 50 N.m from 0.2 s: it stays
    synchronous and the torque settles at the load."""
    st = {"components": {"g": {"kind": "qs_grid", "V_rms": 112.3, "f": 50},
                         "M": dict(PM_DATA, kind="qs_pmsm_damper", Lrsigmad=0.05 / W50, Rrd=0.04, w0=W50 / 2),
                         "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "torque_step", "stepTorque": -50, "startTime": 0.2}},
          "connect": [["g.plug", "M.plug"], ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical", "T": "M.tauElectrical"},
          "analysis": {"kind": "transient", "stop_time": 1.5, "intervals": 1500},
          "derived": [{"kind": "window", "signal": x, "t0": 1.3, "t1": 1.5} for x in ("w", "T")]}
    return st, [("derived.w.mean", W50 / 2, 1e-7, "synchronous speed"),
                ("derived.T.mean", 50.0, 1e-5, "the load torque")]


def _delta(qs):
    """The induction machine connected in delta (terminal D) on a grid of 100/sqrt(3) V per phase: each winding
    sees 100 V, so speed and torque are those of the star machine on 100 V (the equivalent circuit)."""
    st, _ = (_qs_im_grid if qs else _im_grid)()
    st = {**st, "components": dict(st["components"])}
    gname = "grid"
    st["components"]["M"] = dict(st["components"]["M"], terminal="D")
    st["components"][gname] = dict(st["components"][gname], V_rms=100 / math.sqrt(3))
    for k in ("pw", "sv", "gv", "irms"):
        st["components"].pop(k, None)
    st["connect"] = [["grid.plug", "M.plug"], ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]]
    st["outputs"] = {"w": "M.wMechanical", "T": "M.tauElectrical"}
    st["derived"] = [d for d in st["derived"] if d["signal"] in ("w", "T")]
    s_, w, _ = im_steady(100.0, 50.0, 100.0)
    tol = 1e-6 if qs else 1e-4
    return st, [("derived.w.mean", w, tol, "the star machine's speed on 100 V"),
                ("derived.T.mean", 100.0, 10 * tol, "electrical torque = the load")]


NONLINEAR_CASES = {"im_grid_slip", "pmsm_current_torque", "im_vf_start", "pmsm_damper_grid", "quadratic_load",
                   "qs_im_grid_slip", "qs_pmsm_current_torque", "qs_im_vf_start", "qs_pmsm_damper_grid", "im_grid_delta",
                   "qs_im_grid_delta"}


# ---- parameter estimation (V4): data from the closed forms (not from the reference), with a fixed noise --------------
def _fit_rc(kind):
    st, _ = _rc()
    st = dict(st, analysis={"kind": "transient", "stop_time": 0.005, "intervals": 1000})
    st.pop("derived")
    t = np.linspace(0, 0.005, 201)
    if kind == "tau":                     # R from a noisy step (sigma 1e-3), C known
        sig = 1e-3
        y = 1 - np.exp(-t / 1e-3) + np.random.default_rng(7).normal(0, sig, t.size)
        st["fit"] = {"params": {"R.R": {"initial": "3 kOhm", "min": 100, "max": 1e5}},
                     "data": {"v": {"t": t.tolist(), "y": y.tolist(), "sigma": sig}}}
        J = t / (1000.0 ** 2 * 1e-6) * np.exp(-t / 1e-3)             # dv/dR (v = 1 - exp(-t/(RC)))
        se = sig / math.sqrt(float(J @ J))
        return st, [("fit.params.R.R.value", 1000.0, {"abs": 4 * se}, f"R within 4 standard errors ({se:.3g} Ohm)"),
                    ("fit.params.R.R.stderr", se, 0.05, "the stderr against sigma / |dv/dR| (linearised)"),
                    ("fit.chi2_reduced", 1.0, {"abs": 0.3}, "the noise is sigma: reduced chi-square about 1")]
    y = 1 - np.exp(-t / 1e-3)             # R and C: only RC is in the data
    st["fit"] = {"params": {"R.R": {"initial": 3000, "min": 100, "max": 1e5},
                            "C.C": {"initial": "2 uF", "min": 1e-8, "max": 1e-4}},
                 "data": {"v": {"t": t.tolist(), "y": y.tolist()}}}
    return st, [("fit.identifiable", 0, {"abs": 0.5}, "R and C from one RC step: not separable"),
                ("fit.not_separable.0.2", -1.0, {"abs": 0.02}, "their correlation is -1 (only R C is determined)")]


def _fit_thermal(kind):
    st, _ = _thermal("rc")
    st = dict(st)
    st.pop("derived")
    Ta, Q, A = 293.15, 50.0, 0.06
    if kind == "steady":                  # h from two steady temperatures: T = Ta + Q / (h A)
        # OpenModelica's steady points are runs that must settle: 30 time constants at h = 10 (tau = 3000 s)
        st["analysis"] = {"kind": "transient", "stop_time": 90000, "intervals": 2000}
        st["fit"] = {"params": {"air.h": {"initial": 30, "min": 1, "max": 200}},
                     "steady": [{"set": {"q.Q_flow": q}, "values": {"T": Ta + q / (10 * A)}} for q in (50, 20)]}
        return st, [("fit.params.air.h.value", 10.0, 1e-5, "T_inf = Ta + Q / (h A) at Q = 50 and 20 W")]
    t = np.linspace(0, 20000, 81)         # m, h and T0 from a heating curve
    C, G = 1800.0, 10 * A
    T0 = 303.15
    y = Ta + Q / G + (T0 - Ta - Q / G) * np.exp(-t * G / C)
    st["components"] = {**st["components"], "blk": dict(st["components"]["blk"], T0=T0)}
    st["fit"] = {"params": {"blk.m": {"initial": 5, "min": 0.1, "max": 50},
                            "air.h": {"initial": 30, "min": 1, "max": 200},
                            "blk.T0": {"initial": 290, "min": 273.15, "max": 340}},
                 "data": {"T": {"t": t.tolist(), "y": y.tolist()}}}
    return st, [("fit.params.blk.m.value", 2.0, 1e-4, "C = m cp = 1800 J/K"),
                ("fit.params.air.h.value", 10.0, 1e-4, "G = h A = 0.6 W/K"),
                ("fit.params.blk.T0.value", T0, {"abs": 1e-3}, "T(0) = 30 degC")]


def _fit_msd():
    st, _ = _msd("free")
    st = dict(st, analysis={"kind": "transient", "stop_time": 1, "intervals": 2000})
    st.pop("derived")
    m, c, d, x0 = 2.0, 800.0, 4.0, 0.1
    wn, ze = math.sqrt(c / m), d / (2 * math.sqrt(c * m))
    wd = wn * math.sqrt(1 - ze * ze)
    t = np.linspace(0, 1, 201)
    y = x0 * np.exp(-ze * wn * t) * (np.cos(wd * t) + ze / math.sqrt(1 - ze * ze) * np.sin(wd * t))
    st["fit"] = {"params": {"k.c": {"initial": 600, "min": 100, "max": 2000}, "d.d": {"initial": 2, "min": 0.1, "max": 50}},
                 "data": {"s": {"t": t.tolist(), "y": y.tolist()}}}
    return st, [("fit.params.k.c.value", c, 1e-4, "free vibration: wn = sqrt(c/m)"),
                ("fit.params.d.d.value", d, 1e-3, "zeta = d / (2 sqrt(c m))")]


# ---- Modelica.Fluid (V5 FL3, FL4): closed forms; liquids also with the reference (fluidref) ----------------------
LIQ = {"rho": 1000.0, "cp": 4000.0, "eta": 1e-3}       # a custom constant-property liquid: round numbers
G = 9.80665


def _fl_drain():
    """Torricelli through an orifice: A dh/dt = -A_o sqrt(2 g h / zeta) -> sqrt(h) = sqrt(h0) - A_o/(2A) sqrt(2g/zeta) t."""
    A, d, zeta, h0, T = 0.5, 0.03, 1.0, 2.0, 200.0
    Ao = math.pi * d * d / 4
    st = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": A, "level_start": h0, "medium": LIQ},
                         "o": {"kind": "fl_orifice", "diameter": d, "zeta": zeta}, "out": {"kind": "fl_boundary"}},
          "connect": [["tank.port", "o.port_a"], ["o.port_b", "out.port"]],
          "outputs": {"level": "tank.level", "m": "o.m_flow"},
          "analysis": {"kind": "transient", "stop_time": T, "intervals": 200, "tolerance": 1e-8}}
    h = (math.sqrt(h0) - Ao / (2 * A) * math.sqrt(2 * G / zeta) * T) ** 2
    return st, [("outputs.level.final", h, 1e-4, "Torricelli: sqrt(h) falls linearly"),
                ("outputs.m.initial", LIQ["rho"] * Ao * math.sqrt(2 * G * h0 / zeta), 1e-4, "m = rho A_o sqrt(2 g h0 / zeta)"),
                ("checks.mass.error_rel", 0.0, {"abs": 1e-5}, "mass balance: the tank loses what flows out")]


def _fl_laminar():
    """Hagen-Poiseuille: m = rho pi d^4 dp / (128 mu L) (Re ~ 31: laminar)."""
    d, L, dp = 0.01, 10.0, 10.0
    st = {"components": {"a": {"kind": "fl_boundary", "p": 101325.0 + dp, "medium": LIQ},
                         "pipe": {"kind": "fl_pipe", "length": L, "diameter": d, "roughness": 0.0},
                         "b": {"kind": "fl_boundary", "p": 101325.0}},
          "connect": [["a.port", "pipe.port_a"], ["pipe.port_b", "b.port"]],
          "outputs": {"m": "pipe.m_flow"}, "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10}}
    return st, [("outputs.m.final", LIQ["rho"] * math.pi * d ** 4 * dp / (128 * LIQ["eta"] * L), 1e-4,
                 "Hagen-Poiseuille (MSL's detailed friction is laminar below Re ~ 745)")]


def _fl_pump_system():
    """A pump's quadratic curve H = 20 - 2e5 V^2 on a resistance dp = a V^2: rho g H(V) = a V^2."""
    a = 1e9
    rg = LIQ["rho"] * G
    V = math.sqrt(20 * rg / (a + 2e5 * rg))
    st = {"components": {"src": {"kind": "fl_boundary", "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1500 rpm"},
                         "r": {"kind": "fl_resistance", "a": a, "b": 0.0}, "out": {"kind": "fl_boundary"}},
          "connect": [["src.port", "pump.port_a"], ["pump.port_b", "r.port_a"], ["r.port_b", "out.port"]],
          "outputs": {"V": "pump.V_flow", "head": "pump.head"},
          "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10}}
    return st, [("outputs.V.final", V, 1e-4, "the curve meets the system: rho g (20 - 2e5 V^2) = a V^2"),
                ("outputs.head.final", a * V * V / rg, 1e-4, "head = a V^2 / (rho g)")]


def _fl_valve():
    """ValveIncompressible sized by Kv, fully open: m = 27.7e-6 Kv sqrt(rho dp) (MSL: Av = Kv 27.7e-6)."""
    Kv, dp = 10.0, 1e5
    st = {"components": {"a": {"kind": "fl_boundary", "p": 101325.0 + dp, "medium": LIQ},
                         "v": {"kind": "fl_valve", "Kv": Kv, "dp_nominal": dp, "m_flow_nominal": 3.0, "opening": 1.0},
                         "b": {"kind": "fl_boundary", "p": 101325.0}},
          "connect": [["a.port", "v.port_a"], ["v.port_b", "b.port"]],
          "outputs": {"m": "v.m_flow"}, "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10}}
    return st, [("outputs.m.final", 27.7e-6 * Kv * math.sqrt(LIQ["rho"] * dp), 1e-6, "the Kv mapping (Av = 27.7e-6 Kv)")]


def _fl_heated_pipe():
    """A dynamic pipe of n mixed volumes at a wall of Tw, heat alpha (Tw - T_k) on A/n each, upwind: at steady
    state T_k = (m cp T_(k-1) + alpha A_k Tw) / (m cp + alpha A_k)."""
    m, Tin, Tw, d, L, n, alpha = 0.2, 300.0, 350.0, 0.05, 10.0, 5, 500.0
    Ak = math.pi * d * L / n
    T = Tin
    for _ in range(n):
        T = (m * LIQ["cp"] * T + alpha * Ak * Tw) / (m * LIQ["cp"] + alpha * Ak)
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": m, "T": Tin, "medium": LIQ},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": L, "diameter": d, "nNodes": n,
                                  "heat_transfer": "constant", "alpha": alpha},
                         "wall": {"kind": "fixed_temperature", "T": Tw},
                         "Ts": {"kind": "fl_temperature"}, "out": {"kind": "fl_boundary", "T": Tin}},
          "connect": [["src.port", "pipe.port_a"], ["pipe.port_b", "Ts.port_a"], ["Ts.port_b", "out.port"],
                      ["pipe.heat", "wall.port"]],
          "outputs": {"T": "Ts.T", "Q": "pipe.heat.Q_flow"},
          "analysis": {"kind": "transient", "stop_time": 1200, "intervals": 120}}
    return st, [("outputs.T.final", T, {"abs": 2e-3}, f"{n} mixed volumes in series, constant alpha"),
                ("outputs.Q.final", m * LIQ["cp"] * (T - Tin), 1e-3, "the wall's heat = m cp (T_out - T_in)"),
                ("checks.mass.ok", 1, {"abs": 0.5}, "mass balance"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance (the wall's heat)"),
                ("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers dynamic pipes (FL11)"),
                ("checks.reference.ok", 1, {"abs": 0.5}, "OpenModelica = the reference")]


def _fl_air_fill():
    """An adiabatic volume filled with air at its own temperature: m cv T = m0 cv T0 + mdot cp Tin t, so
    T = T0 (m0 + gamma mdot t) / (m0 + mdot t) (gamma of DryAirNasa near 300-360 K: 1.400, within 0.1 %)."""
    V, p0, T0, mdot, t1 = 0.1, 1e5, 300.0, 0.01, 10.0
    R, gamma = 287.0512, 1.3998
    m0 = p0 * V / (R * T0)
    T = T0 * (m0 + gamma * mdot * t1) / (m0 + mdot * t1)
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": mdot, "T": T0, "medium": "air"},
                         "vol": {"kind": "fl_volume", "V": V, "p_start": p0, "T_start": T0}},
          "connect": [["src.port", "vol.port"]],
          "outputs": {"T": "vol.T", "p": "vol.p", "m": "vol.m"},
          "analysis": {"kind": "transient", "stop_time": t1, "intervals": 100}}
    return st, [("outputs.T.final", T, {"abs": 0.3}, "adiabatic filling (cp, cv of DryAirNasa vary 0.1 % here)"),
                ("outputs.m.final", m0 + mdot * t1, 1e-5, "m = m0 + mdot t (carbonio: 7e-7)"),
                ("checks.mass.error_rel", 0.0, {"abs": 1e-5}, "mass balance")]


def _fl_turbulent():
    """Colebrook-White is explicit in dp: lambda2 = 2 dp D^3 rho / (L mu^2), Re = -2 sqrt(lambda2)
    log10(2.51/sqrt(lambda2) + 0.27 roughness/D), m = pi D mu Re / 4 (MSL's detailed friction above Re 4000)."""
    d, L, rough, dp = 0.02, 10.0, 2.5e-5, 1e4
    lam2 = 2 * dp * d ** 3 * LIQ["rho"] / (L * LIQ["eta"] ** 2)
    Re = -2 * math.sqrt(lam2) * math.log10(2.51 / math.sqrt(lam2) + 0.27 * rough / d)
    st = {"components": {"a": {"kind": "fl_boundary", "p": 101325.0 + dp, "medium": LIQ},
                         "pipe": {"kind": "fl_pipe", "length": L, "diameter": d, "roughness": rough},
                         "b": {"kind": "fl_boundary", "p": 101325.0}},
          "connect": [["a.port", "pipe.port_a"], ["pipe.port_b", "b.port"]],
          "outputs": {"m": "pipe.m_flow"}, "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10}}
    return st, [("outputs.m.final", math.pi * d * LIQ["eta"] * Re / 4, 1e-6, f"Colebrook-White at Re {Re:.0f}")]


def _fl_two_tanks():
    """Two open tanks joined by a pipe with dp = k m (nominal_laminar): h1 - h2 decays with
    tau = k / (g (1/A1 + 1/A2))."""
    A1, A2, h1, h2, k, T = 1.0, 0.5, 2.0, 0.5, 2e4, 3000.0
    tau = k / (G * (1 / A1 + 1 / A2))
    d0 = h1 - h2
    hm = (A1 * h1 + A2 * h2) / (A1 + A2)
    dT = d0 * math.exp(-T / tau)
    st = {"components": {"t1": {"kind": "fl_tank", "height": 3, "crossArea": A1, "level_start": h1, "medium": LIQ},
                         "pipe": {"kind": "fl_pipe", "length": 5, "diameter": 0.05, "friction": "nominal_laminar",
                                  "dp_nominal": k, "m_flow_nominal": 1.0},
                         "t2": {"kind": "fl_tank", "height": 3, "crossArea": A2, "level_start": h2}},
          "connect": [["t1.port", "pipe.port_a"], ["pipe.port_b", "t2.port"]],
          "outputs": {"h1": "t1.level", "h2": "t2.level", "m": "pipe.m_flow"},
          "analysis": {"kind": "transient", "stop_time": T, "intervals": 300, "tolerance": 1e-8}}
    return st, [("outputs.h1.final", hm + A2 / (A1 + A2) * dT, 1e-5, f"levels equalise, tau = {tau:.0f} s"),
                ("outputs.h2.final", hm - A1 / (A1 + A2) * dT, 1e-5, "the volume is conserved"),
                ("checks.mass.error_rel", 0.0, {"abs": 1e-5}, "mass balance")]


def _fl_network():
    """A pump network without a closed form, judged against the reference (FL4): a pump lifts water through
    a pipe in the transition region in parallel with an orifice and an equal-percentage valve, then a generic
    resistance into an open tank that fills."""
    st = {"components": {"src": {"kind": "fl_boundary", "p": 1.2e5},
                         "pump": {"kind": "fl_pump", "curve": [[0, 12], [0.002, 10], [0.004, 5]],
                                  "N_nominal": "1450 rpm", "N": "1400 rpm"},
                         "pipe": {"kind": "fl_pipe", "length": 20, "diameter": 0.003, "height_ab": 1.0},
                         "o": {"kind": "fl_orifice", "diameter": 0.012, "zeta": 2.0},
                         "v": {"kind": "fl_valve", "Kv": 3.0, "dp_nominal": 1e5, "m_flow_nominal": 1.0,
                               "characteristic": "equal_percentage", "opening": 0.6},
                         "r": {"kind": "fl_resistance", "a": 2e9, "b": 1e6},
                         "ms": {"kind": "fl_mass_flow_sensor"}, "dp": {"kind": "fl_relative_pressure"},
                         "tank": {"kind": "fl_tank", "height": 10, "crossArea": 0.05, "level_start": 0.2}},
          "connect": [["src.port", "pump.port_a"], ["pump.port_b", "pipe.port_a"], ["pump.port_b", "o.port_a"],
                      ["o.port_b", "v.port_a"], ["pipe.port_b", "r.port_a"], ["v.port_b", "r.port_a"],
                      ["r.port_b", "ms.port_a"], ["ms.port_b", "tank.port"], ["dp.port_a", "pump.port_b"],
                      ["dp.port_b", "r.port_a"]],
          "outputs": {"m": "ms.m_flow", "m_pipe": "pipe.m_flow", "m_valve": "v.m_flow", "level": "tank.level",
                      "head": "pump.head", "W": "pump.W", "dp": "dp.p_rel"},
          "analysis": {"kind": "transient", "stop_time": 60, "intervals": 120}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the Fluid reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference")]


def _cfdt_pump_result(V, rpm, name, rho=1000.0):
    """A cfd-translator pump result shaped like run_studies' (results.flow, pump, rotating), from the pump
    H = 20 - 2e5 V^2 m, P = 500 + 1e5 V W at 1450 rpm (affinity laws at other speeds)."""
    s = 1450.0 / rpm
    H, P = (20 - 2e5 * (V * s) ** 2) / s ** 2, (500 + 1e5 * V * s) / s ** 3
    return {"name": name, "ok": True, "reliable": True,
            "results": {"flow": {"inlet": {"Q": V, "mdot": rho * V}, "outlet": {"Q": -V, "mdot": -rho * V}},
                        "pump": {"head_m": H, "flow_m3h": V * 3600, "power_W": P, "efficiency": rho * G * V * H / P},
                        "rotating": {"impeller": {"rpm": rpm}}, "total_pressure_drop": -rho * G * H}}


def _fl_cfdt_pump():
    """The cfdt chain (FL5): four cfd-translator pump results (one at 1200 rpm, moved to 1450 by the affinity laws)
    give the head and power curves; the pump at 1300 rpm on dp = a V^2: rho g r^2 (20 - 2e5 (V/r)^2) = a V^2,
    W = r^3 (500 + 1e5 V / r), eta = rho g V H / W (r = 1300/1450)."""
    pts = [_cfdt_pump_result(0.002, 1450, "q2"), _cfdt_pump_result(0.005, 1450, "q5"),
           _cfdt_pump_result(0.006, 1200, "q6_1200"), _cfdt_pump_result(0.008, 1450, "q8")]
    a, r = 3.0e9, 1300.0 / 1450.0
    rg = LIQ["rho"] * G
    V = math.sqrt(20 * r * r * rg / (a + 2e5 * rg))
    H = a * V * V / rg
    W = r ** 3 * (500 + 1e5 * V / r)
    st = {"components": {"src": {"kind": "fl_boundary", "medium": LIQ},
                         "pump": {"kind": "fl_pump", "cfdt": pts, "N": "1300 rpm"},
                         "r": {"kind": "fl_resistance", "a": a}, "out": {"kind": "fl_boundary"}},
          "connect": [["src.port", "pump.port_a"], ["pump.port_b", "r.port_a"], ["r.port_b", "out.port"]],
          "outputs": {"V": "pump.V_flow", "head": "pump.head", "W": "pump.W", "eta": "pump.eta"},
          "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10}}
    return st, [("outputs.V.final", V, 1e-6, "the curve from cfdt meets the system at 1300 rpm"),
                ("outputs.head.final", H, 1e-6, "head = a V^2 / (rho g)"),
                ("outputs.W.final", W, 1e-6, "the power curve from cfdt (MSL quadraticPower), N^3"),
                ("outputs.eta.final", rg * V * H / W, 1e-6, "eta = rho g V H / W")]


# total pressure drops of a 20 mm x 200 mm pipe, rho 1000, mu 0.01, uniform inflow (developing laminar flow):
# cfd-translator 2026.11.18.9 on carbonio, run_fl5_pipe_chain1 (OpenFOAM 14, 13520 cells, all reliable)
CFDT_PIPE = {"pipe_u05": (1.56827e-05, 8.37266), "pipe_u10": (3.13655e-05, 17.3712), "pipe_u15": (4.70482e-05, 26.9747),
             "pipe_u20": (6.2731e-05, 37.1391)}


def _fl_cfdt_resistance():
    """The cfdt chain (FL5): fl_resistance fitted to three cfd-translator pipe results (0.05, 0.1, 0.2 m/s) predicts
    the fourth (0.15 m/s): the flow at its total pressure drop. Exact against the fitted law
    V = (-b + sqrt(b^2 + 4 a dp)) / 2a, and within the fit's error against cfd-translator's own flow."""
    import numpy as np
    from scipy.optimize import nnls

    def res(name):
        Q, dp = CFDT_PIPE[name]
        return {"name": name, "ok": True, "reliable": True,
                "results": {"total_pressure_drop": dp, "flow": {"inlet": {"Q": Q, "mdot": 1000 * Q},
                                                                "outlet": {"Q": -Q, "mdot": -1000 * Q}}}}
    fit = ["pipe_u05", "pipe_u10", "pipe_u20"]
    Vs = np.array([CFDT_PIPE[n][0] for n in fit])
    dps = np.array([CFDT_PIPE[n][1] for n in fit])
    A = np.column_stack([Vs * Vs, Vs])
    sc = A.max(axis=0)
    x, _ = nnls(A / sc, dps)
    a, b = (float(v) for v in x / sc)
    Q15, dp15 = CFDT_PIPE["pipe_u15"]
    V = (-b + math.sqrt(b * b + 4 * a * dp15)) / (2 * a)
    liq = {"rho": 1000.0, "cp": 4000.0, "eta": 0.01}
    st = {"components": {"a": {"kind": "fl_boundary", "p": 101325.0 + dp15, "medium": liq},
                         "r": {"kind": "fl_resistance", "cfdt": {"results": [res(n) for n in fit], "mu": 0.01}},
                         "b": {"kind": "fl_boundary"}},
          "connect": [["a.port", "r.port_a"], ["r.port_b", "b.port"]], "outputs": {"V": "r.V_flow"},
          "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10}}
    return st, [("outputs.V.final", V, 1e-6, "the fitted law dp = a V^2 + b V at the held-out point"),
                ("outputs.V.final", Q15, 5e-3, "cfd-translator's own flow at 0.15 m/s (carbonio: 7.9e-4)")]


def _fl_valve_circuit(**valve):
    return {"components": {"a": {"kind": "fl_boundary", "p": 101325.0 + 1e5, "medium": LIQ},
                           "v": {"kind": "fl_valve", "dp_nominal": 1e5, "m_flow_nominal": 3.0, **valve},
                           "b": {"kind": "fl_boundary", "p": 101325.0}},
            "connect": [["a.port", "v.port_a"], ["v.port_b", "b.port"]], "outputs": {"m": "v.m_flow"},
            "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10}}


def _fl_sweep_opening():
    """A sweep over a lifted value (the valve's constant opening, FL6): one build; linear valve,
    m = opening 27.7e-6 Kv sqrt(rho dp)."""
    st = _fl_valve_circuit(Kv=10.0, opening=1.0)
    st["sweep"] = {"params": {"v.opening": [0.25, 0.5, 1.0]}, "report": ["outputs.m.final"]}
    m1 = 27.7e-6 * 10 * math.sqrt(LIQ["rho"] * 1e5)
    checks = [(f"sweep.table.{i}.values.outputs.m.final", y * m1, 1e-6, f"m = {y:g} x Av sqrt(rho dp)")
              for i, y in enumerate((0.25, 0.5, 1.0))]
    checks.append(("sweep.builds", 0.5, {"abs": 0.5}, "every point runs on one build (0 with the reference solver)"))
    checks.append(("sweep.checks.mass.failed", 0, {"abs": 0.5}, "every point's mass balance (FL13)"))
    checks.append(("sweep.checks.fluid_energy.failed", 0, {"abs": 0.5}, "every point's energy balance (FL13)"))
    return st, checks


def _fl_fit_kv():
    """A fit of a Modelica.Fluid value (FL6): Kv from a measured steady flow, Kv = m / (27.7e-6 sqrt(rho dp))."""
    st = _fl_valve_circuit(Kv=5.0)
    m = 27.7e-6 * 12.0 * math.sqrt(LIQ["rho"] * 1e5)
    st["fit"] = {"params": {"v.Kv": {"initial": 5, "min": 1, "max": 50}}, "steady": [{"values": {"m": m}}]}
    return st, [("fit.params.v.Kv.value", 12.0, 1e-6, "Kv = m / (27.7e-6 sqrt(rho dp))")]


def _fl_check_valve():
    """Check valves (FL7): a valve with checkValve forward (m = 27.7e-6 Kv sqrt(rho dp)) and backward (m = 0: MSL's
    regRoot2 with k2 = 0); a pump with checkValve against a head above and below its shutoff head (20 m):
    closed, m = 0 and head = the outlet's; open, H(V) = 20 - 2e5 V^2 = the outlet's head."""
    rg = LIQ["rho"] * G
    st = {"components": {"a": {"kind": "fl_boundary", "p": 2e5, "medium": LIQ},
                         "v": {"kind": "fl_valve", "Kv": 10.0, "dp_nominal": 1e5, "m_flow_nominal": 3.0,
                               "checkValve": True},
                         "b": {"kind": "fl_boundary", "p": 1e5},
                         "src": {"kind": "fl_boundary", "p": 1e5, "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]],
                                  "N_nominal": "1450 rpm", "checkValve": True, "V": 0.001},
                         "out": {"kind": "fl_boundary", "p": 1e5 + 25 * rg}},
          "connect": [["a.port", "v.port_a"], ["v.port_b", "b.port"], ["src.port", "pump.port_a"],
                      ["pump.port_b", "out.port"]],
          "outputs": {"m_valve": "v.m_flow", "m_pump": "pump.m_flow", "head": "pump.head"},
          "analysis": {"kind": "transient", "stop_time": 1, "intervals": 10},
          "sweep": {"params": {"b.p": [1e5, 3e5], "out.p": [1e5 + 25 * rg, 1e5 + 10 * rg]}, "mode": "zip",
                    "report": ["outputs.m_valve.final", "outputs.m_pump.final", "outputs.head.final"]}}
    Vo = math.sqrt((20 - 10) / 2e5)
    return st, [("sweep.table.0.values.outputs.m_valve.final", 27.7e-6 * 10 * math.sqrt(LIQ["rho"] * 1e5), 1e-6,
                 "check valve forward: as without it"),
                ("sweep.table.1.values.outputs.m_valve.final", 0.0, {"abs": 1e-9}, "check valve backward: closed"),
                ("sweep.table.0.values.outputs.m_pump.final", 0.0, {"abs": 1e-9},
                 "pump check valve: 25 m above a 20 m shutoff head, closed"),
                ("sweep.table.0.values.outputs.head.final", 25.0, 1e-6, "its head is the outlet's"),
                ("sweep.table.1.values.outputs.m_pump.final", LIQ["rho"] * Vo, 1e-6, "open: 20 - 2e5 V^2 = 10 m")]


def _fl_tank_port_drain():
    """A tank draining through a port with losses (FL7: use_portsData, zeta_out 0.5): rho g h = (1.5 - r^2)
    m^2 / (2 rho A_p^2), r = A_p/A, so sqrt(h) falls linearly at A_p/(2A) sqrt(2 g / (1.5 - r^2))."""
    A, d, h0, T = 0.5, 0.03, 2.0, 200.0
    Ap = math.pi * d * d / 4
    c = Ap / (2 * A) * math.sqrt(2 * G / (1.5 - (Ap / A) ** 2))
    st = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": A, "level_start": h0, "medium": LIQ,
                                  "port_diameter": d},
                         "out": {"kind": "fl_boundary"}},
          "connect": [["tank.port", "out.port"]],
          "outputs": {"level": "tank.level", "m": "out.port.m_flow"},
          "analysis": {"kind": "transient", "stop_time": T, "intervals": 200, "tolerance": 1e-8}}
    h = (math.sqrt(h0) - c * T) ** 2
    return st, [("outputs.level.final", h, 1e-6, "sqrt(h) falls linearly with the port loss (zeta_out 0.5)"),
                ("outputs.m.initial", LIQ["rho"] * Ap * math.sqrt(2 * G * h0 / (1.5 - (Ap / A) ** 2)), 1e-6,
                 "m = rho A_p sqrt(2 g h0 / (1.5 - r^2))"),
                ("checks.mass.error_rel", 0.0, {"abs": 1e-5}, "mass balance")]


def _fl_loop_reference():
    """A closed loop judged against the reference (FL7): an open tank with port losses (port_diameter, 3 ports)
    feeds two pumps in parallel (the second at a lower speed), a pipe and a valve back into the tank. (Pumps with
    check valves need a volume, which a closed liquid loop cannot have in OpenModelica 1.27.2: fl_check_valve
    runs them in open circuits.)"""
    pump = {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1500 rpm"}
    st = {"components": {"tank": {"kind": "fl_tank", "height": 5, "crossArea": 2, "level_start": 1,
                                  "port_diameter": 0.05},
                         "pump": pump, "pump2": dict(pump, N="1300 rpm"),
                         "pipe": {"kind": "fl_pipe", "length": 20, "diameter": 0.05},
                         "v": {"kind": "fl_valve", "Kv": 20, "dp_nominal": 1e5, "m_flow_nominal": 5, "opening": 0.8}},
          "connect": [["tank.port", "pump.port_a"], ["tank.port", "pump2.port_a"], ["pump.port_b", "pipe.port_a"],
                      ["pump2.port_b", "pipe.port_a"], ["pipe.port_b", "v.port_a"], ["v.port_b", "tank.port"]],
          "outputs": {"m": "pump.m_flow", "m2": "pump2.m_flow", "head": "pump.head", "dp": "pipe.dp",
                      "p_in": "tank.port[1].p", "level": "tank.level"},
          "analysis": {"kind": "transient", "stop_time": 10, "intervals": 20}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the Fluid reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.mass.error_rel", 0.0, {"abs": 1e-5}, "mass balance")]


def _fl_heated_tank():
    """The Fluid energy balance (FL8): a closed tank of a constant-property liquid (u = cv (T - T0), cv = cp)
    heated at Q: T = T_start + Q t / (rho A h cp); the balance (heat in = internal energy) to rounding."""
    A, h0, Q, T0, T = 0.5, 1.0, 5000.0, 290.0, 600.0
    st = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": A, "level_start": h0, "T_start": T0,
                                  "medium": LIQ},
                         "src": {"kind": "fl_mass_flow", "m_flow": 0.0},
                         "heater": {"kind": "fixed_heat_flow", "Q_flow": Q}},
          "connect": [["src.port", "tank.port"], ["heater.port", "tank.heatPort"]],
          "outputs": {"T": "tank.T"}, "analysis": {"kind": "transient", "stop_time": T, "intervals": 60}}
    return st, [("outputs.T.final", T0 + Q * T / (LIQ["rho"] * A * h0 * LIQ["cp"]), 1e-6, "T rises at Q / (m cp)"),
                ("checks.fluid_energy.error_rel", 0.0, {"abs": 1e-6}, "the heat in = the internal energy's rise")]


def _fl_pump_heating():
    """Temperatures in the reference (FL9): a pump with a volume (V 0.05 m3, starting at 290 K) lifts water 10 m
    between two boundaries; its power W = rho g H V / eta warms the flow: m_v cv dT/dt = m cp (T_in - T) + W
    (cv = cp), T = T_in + W / (m cp) + (T_start - T_in - W / (m cp)) exp(-m t / m_v); a sensor after it reads T."""
    rho, cp, g = LIQ["rho"], LIQ["cp"], G
    H, eta, Vp, Tin, T0, T = 10.0, 0.8, 0.05, 300.0, 290.0, 20.0
    V = math.sqrt((20 - H) / 2e5)
    m = rho * V
    W = rho * g * H * V / eta
    Tinf = Tin + W / (m * cp)
    Tend = Tinf + (T0 - Tinf) * math.exp(-m * T / (rho * Vp))
    st = {"components": {"src": {"kind": "fl_boundary", "p": 101325.0, "T": Tin, "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1450 rpm",
                                  "V": Vp, "T_start": T0, "eta": eta},
                         "Ts": {"kind": "fl_temperature"}, "out": {"kind": "fl_boundary", "p": 101325.0 + rho * g * H}},
          "connect": [["src.port", "pump.port_a"], ["pump.port_b", "Ts.port_a"], ["Ts.port_b", "out.port"]],
          "outputs": {"T": "Ts.T", "m": "pump.m_flow"},
          "analysis": {"kind": "transient", "stop_time": T, "intervals": 100, "tolerance": 1e-8}}
    return st, [("outputs.m.final", m, 1e-6, "the curve at 10 m: 20 - 2e5 V^2 = 10"),
                ("outputs.T.final", Tend, {"abs": 1e-5}, "the pump volume's temperature: W heats the flow")]


def _fl_thermal_reference():
    """Temperatures judged against the reference (FL9): water at 300 K pumped (W heats it) into a tank (290 K)
    heated by 20 kW and tied by a conductor to a capacitor (350 K) with its own heater (5 kW, alpha 0.002); the
    tank drains through an orifice; temperature sensors before and after the tank."""
    st = {"components": {
        "src": {"kind": "fl_boundary", "p": 101325, "T": 300.0, "medium": LIQ},
        "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1450 rpm"},
        "Ts1": {"kind": "fl_temperature"}, "pipe": {"kind": "fl_pipe", "length": 10.0, "diameter": 0.05},
        "tank": {"kind": "fl_tank", "height": 5, "crossArea": 0.5, "level_start": 1, "T_start": 290.0},
        "heater": {"kind": "fixed_heat_flow", "Q_flow": 20000.0}, "g": {"kind": "thermal_conductor", "G": 50.0},
        "cap": {"kind": "heat_capacitor", "C": 1e5, "T0": 350.0},
        "hc": {"kind": "fixed_heat_flow", "Q_flow": 5000.0, "alpha": 0.002, "T_ref": 300.0},
        "tsc": {"kind": "temperature_sensor"}, "o": {"kind": "fl_orifice", "diameter": 0.03, "zeta": 1.0},
        "Ts2": {"kind": "fl_temperature"}, "out": {"kind": "fl_boundary", "T": 280.0}},
        "connect": [["src.port", "pump.port_a"], ["pump.port_b", "Ts1.port_a"], ["Ts1.port_b", "pipe.port_a"],
                    ["pipe.port_b", "tank.port"], ["tank.port", "o.port_a"], ["o.port_b", "Ts2.port_a"],
                    ["Ts2.port_b", "out.port"], ["tank.heatPort", "heater.port"], ["tank.heatPort", "g.port_a"],
                    ["g.port_b", "cap.port"], ["hc.port", "cap.port"], ["tsc.port", "cap.port"]],
        "outputs": {"T_tank": "tank.T", "T1": "Ts1.T", "T2": "Ts2.T", "Tcap": "cap.T", "Qg": "g.Q_flow", "Tsc": "tsc.T",
                    "level": "tank.level", "Qport": "tank.heatPort.Q_flow"},
        "analysis": {"kind": "transient", "stop_time": 600, "intervals": 120}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the Fluid reference covers it, temperatures too"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_pump_into_tank():
    """A pump with a volume and a check valve straight into a tank (FL10: OpenModelica 1.27.2 failed to initialise
    this while the pump volume preferred p as a state), judged against the reference: the tank fills, the pump's
    work warms the water. Tolerance 1e-8: at 1e-6 OpenModelica's tank T was 1.4e-4 off (carbonio, .20); at 1e-8
    2.4e-6."""
    st = {"components": {"src": {"kind": "fl_boundary", "p": 101325.0, "T": 300.0, "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1450 rpm",
                                  "V": 0.002, "checkValve": True, "T_start": 300.0},
                         "tank": {"kind": "fl_tank", "height": 12, "crossArea": 0.5, "level_start": 1, "T_start": 290.0}},
          "connect": [["src.port", "pump.port_a"], ["pump.port_b", "tank.port"]],
          "outputs": {"m": "pump.m_flow", "level": "tank.level", "T": "tank.T"},
          "analysis": {"kind": "transient", "stop_time": 300, "intervals": 60, "tolerance": 1e-8}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_heated_loop():
    """A heated closed loop of water (FL10: volumes on closed constant-density loops were refused before): a tank,
    a pump with a volume and a check valve, a heated dynamic pipe (wall 350 K, alpha 500) after a valve; the
    balances and the reference judge it (FL11: the reference has dynamic pipes; OpenModelica at tolerance 1e-8 was
    1.3e-9 off it on carbonio, run_fl11_probe1)."""
    st = {"components": {"tank": {"kind": "fl_tank", "height": 5, "crossArea": 1.0, "level_start": 1,
                                  "T_start": 293.15, "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1450 rpm",
                                  "V": 0.001, "checkValve": True},
                         "v": {"kind": "fl_valve", "Kv": 20, "dp_nominal": 1e5, "m_flow_nominal": 5, "opening": 0.8},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 10.0, "diameter": 0.05, "nNodes": 3,
                                  "heat_transfer": "constant", "alpha": 500.0},
                         "wall": {"kind": "fixed_temperature", "T": 350.0}},
          "connect": [["tank.port", "pump.port_a"], ["pump.port_b", "v.port_a"], ["v.port_b", "pipe.port_a"],
                      ["pipe.port_b", "tank.port"], ["pipe.heat", "wall.port"]],
          "outputs": {"m": "pump.m_flow", "T": "tank.T", "Q": "pipe.heat.Q_flow"},
          "analysis": {"kind": "transient", "stop_time": 600, "intervals": 120, "tolerance": 1e-8}}
    return st, [("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy: the wall's heat and the pump's work stored"),
                ("checks.mass.ok", 1, {"abs": 0.5}, "mass balance"),
                ("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers it (FL11)"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference")]


def _fl_pipe_loop_reference():
    """FL11: a closed loop with a rising dynamic pipe right after the pump (modelStructure av_b), local heat
    transfer (Gnielinski / Hausen in MSL's LocalPipeFlowHeatTransfer) from a 340 K wall, a valve back to the tank;
    judged against the reference (carbonio run_fl11_probe1: tank T 1.9e-9, Q 2.4e-8 off at tolerance 1e-8)."""
    st = {"components": {"tank": {"kind": "fl_tank", "height": 5, "crossArea": 1.0, "level_start": 1,
                                  "T_start": 293.15, "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1450 rpm"},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 10.0, "diameter": 0.04, "nNodes": 3,
                                  "height_ab": 1.0, "heat_transfer": "local"},
                         "v": {"kind": "fl_valve", "Kv": 20, "opening": 0.6, "dp_nominal": 1e5, "m_flow_nominal": 5},
                         "wall": {"kind": "fixed_temperature", "T": 340.0}},
          "connect": [["tank.port", "pump.port_a"], ["pump.port_b", "pipe.port_a"], ["pipe.port_b", "v.port_a"],
                      ["v.port_b", "tank.port"], ["pipe.heat", "wall.port"]],
          "outputs": {"m": "pump.m_flow", "T": "tank.T", "Q": "pipe.heat.Q_flow", "pb": "pipe.p_b"},
          "analysis": {"kind": "transient", "stop_time": 300, "intervals": 30, "tolerance": 1e-8}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_volume_reference():
    """FL11: a closed volume with two ports (a source in, a laminar dynamic pipe out) and a heat port through a
    conductor to 350 K; temperatures before and after the pipe judged against the reference (carbonio
    run_fl11_probe2: 1.8e-9)."""
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": 0.05, "T": 300.0, "medium": LIQ},
                         "vol": {"kind": "fl_volume", "V": 0.01, "T_start": 330.0},
                         "Ts": {"kind": "fl_temperature"},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 5.0, "diameter": 0.02, "nNodes": 2,
                                  "friction": "nominal_laminar", "dp_nominal": 2e4, "m_flow_nominal": 0.1,
                                  "T_start": 305.0},
                         "Ts2": {"kind": "fl_temperature"}, "out": {"kind": "fl_boundary", "T": 280.0},
                         "g": {"kind": "thermal_conductor", "G": 50.0}, "amb": {"kind": "fixed_temperature", "T": 350.0}},
          "connect": [["src.port", "vol.port"], ["vol.port", "Ts.port_a"], ["Ts.port_b", "pipe.port_a"],
                      ["pipe.port_b", "Ts2.port_a"], ["Ts2.port_b", "out.port"], ["vol.heatPort", "g.port_a"],
                      ["g.port_b", "amb.port"]],
          "outputs": {"Tv": "vol.T", "Ts": "Ts.T", "T2": "Ts2.T", "Q": "g.Q_flow", "p": "vol.p"},
          "analysis": {"kind": "transient", "stop_time": 50, "intervals": 5, "tolerance": 1e-8}}
    return st, [("outputs.p.final", 101325.0 + 2e4 / 0.1 * 0.05, 1e-9, "p = p_out + dp_nominal m / m_nominal"),
                ("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_glycol_loop():
    """FL14: glycol47 (MSL TableBased: density, cp, viscosity polynomials in T) in a closed loop: a tank with port
    losses, a pump, a rising dynamic pipe right after it (av_b) with local heat transfer from 360 K, a valve; flows
    follow the temperatures. Judged against the reference (carbonio run_fl14_probe2: tank T 8e-8, Q 5e-7 off at
    tolerance 1e-8)."""
    st = {"components": {"tank": {"kind": "fl_tank", "height": 5, "crossArea": 1.0, "level_start": 1, "T_start": 300.0,
                                  "medium": "glycol47", "port_diameter": 0.06},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1450 rpm"},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 10.0, "diameter": 0.04, "nNodes": 3,
                                  "height_ab": 1.0, "heat_transfer": "local"},
                         "v": {"kind": "fl_valve", "Kv": 20, "opening": 0.6, "dp_nominal": 1e5, "m_flow_nominal": 5},
                         "w": {"kind": "fixed_temperature", "T": 360.0}},
          "connect": [["tank.port", "pump.port_a"], ["pump.port_b", "pipe.port_a"], ["pipe.port_b", "v.port_a"],
                      ["v.port_b", "tank.port"], ["pipe.heat", "w.port"]],
          "outputs": {"m": "pump.m_flow", "T": "tank.T", "Q": "pipe.heat.Q_flow", "l": "tank.level"},
          "analysis": {"kind": "transient", "stop_time": 300, "intervals": 10, "tolerance": 1e-8}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers T-dependent liquids (FL14)"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.mass.ok", 1, {"abs": 0.5}, "mass balance (a closed loop: the stored mass's own error)"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_essotherm_pipe():
    """FL14: essotherm650 (viscosity over four decades) through a rising dynamic pipe heated from 500 K with local
    heat transfer (laminar: Hausen); outlet temperature, heat and inlet pressure against the reference (carbonio
    run_fl14_probe2: 2e-12)."""
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": 0.5, "T": 400.0, "medium": "essotherm650"},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 8.0, "diameter": 0.03, "nNodes": 4,
                                  "heat_transfer": "local", "T_start": 400.0, "height_ab": 2.0},
                         "w": {"kind": "fixed_temperature", "T": 500.0}, "Ts": {"kind": "fl_temperature"},
                         "b": {"kind": "fl_boundary", "T": 400.0}},
          "connect": [["src.port", "pipe.port_a"], ["pipe.port_b", "Ts.port_a"], ["Ts.port_b", "b.port"],
                      ["pipe.heat", "w.port"]],
          "outputs": {"T": "Ts.T", "Q": "pipe.heat.Q_flow", "pa": "pipe.p_a"},
          "analysis": {"kind": "transient", "stop_time": 120, "intervals": 12, "tolerance": 1e-8}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_static_head():
    """FL14: a static pipe rising 1 m: MSL's StaticPipe gives port_b.h_outflow = inStream(port_a.h_outflow) - g
    height_ab, so the energy balance needs the rise's work -m g height_ab (carbonio run_fl14_probe2 e1_static:
    1.6e-5 off without it); essotherm650 at 150 degC in, 200 degC at the far boundary."""
    st = {"components": {"a": {"kind": "fl_boundary", "p": 3e5, "T": 423.15, "medium": "essotherm650"},
                         "pipe": {"kind": "fl_pipe", "length": 5.0, "diameter": 0.04, "height_ab": 1.0},
                         "o": {"kind": "fl_orifice", "diameter": 0.03, "zeta": 1.5},
                         "r": {"kind": "fl_resistance", "a": 1e8, "b": 1e5}, "b": {"kind": "fl_boundary", "T": 473.15}},
          "connect": [["a.port", "pipe.port_a"], ["pipe.port_b", "o.port_a"], ["o.port_b", "r.port_a"],
                      ["r.port_b", "b.port"]],
          "outputs": {"m": "pipe.m_flow", "pb": "pipe.p_b", "pr": "r.p_a"},
          "analysis": {"kind": "transient", "stop_time": 1, "intervals": 2, "tolerance": 1e-8}}
    return st, [("outputs.m.final", 0.2876681864585172, 1e-6, "carbonio run_fl14_probe2 (the reference: 8.5e-15)"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance with the rise's work"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference")]


def _fl_gas_valve_volume():
    """FL15: air (DryAirNasa) from 3 bar through a gas valve (ValveCompressible) into a 50 l volume that empties
    through an orifice: the volume's pressure and temperature (it heats by compression, then cools) against the
    reference (carbonio run_fl15_probe1: p 4.6e-8, T 1e-8 at tolerance 1e-8)."""
    st = {"components": {"a": {"kind": "fl_boundary", "p": 3e5, "T": 300.0, "medium": "air"},
                         "v": {"kind": "fl_valve_gas", "Kv": 1.0, "p_nominal": 3e5, "dp_nominal": 1e5,
                               "m_flow_nominal": 0.1},
                         "vol": {"kind": "fl_volume", "V": 0.05, "p_start": 1e5, "T_start": 300.0},
                         "o": {"kind": "fl_orifice", "diameter": 0.005, "zeta": 1.0},
                         "b": {"kind": "fl_boundary", "p": 1e5, "T": 300.0}},
          "connect": [["a.port", "v.port_a"], ["v.port_b", "vol.port"], ["vol.port", "o.port_a"], ["o.port_b", "b.port"]],
          "outputs": {"p": "vol.p", "T": "vol.T", "mv": "v.m_flow", "mo": "o.m_flow"},
          "analysis": {"kind": "transient", "stop_time": 20, "intervals": 10, "tolerance": 1e-8}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers gases (FL15)"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_gas_choked():
    """FL15: a 6 bar supply through an equal-percentage gas valve sized at its operating point (choked: x above
    Fxt), a 200 l volume cooled through a conductor, a gas check valve to 1 bar (carbonio run_fl15_probe2: 1e-10)."""
    st = {"components": {"a": {"kind": "fl_boundary", "p": 6e5, "T": 330.0, "medium": "air"},
                         "v": {"kind": "fl_valve_gas", "p_nominal": 6e5, "dp_nominal": 4e5, "m_flow_nominal": 0.2,
                               "opening": 0.7, "characteristic": "equal_percentage"},
                         "vol": {"kind": "fl_volume", "V": 0.2, "p_start": 1.2e5, "T_start": 300.0},
                         "cv": {"kind": "fl_valve_gas", "Kv": 2.0, "p_nominal": 3e5, "dp_nominal": 1e5,
                                "m_flow_nominal": 0.1, "checkValve": True},
                         "b": {"kind": "fl_boundary", "p": 1e5, "T": 300.0},
                         "g": {"kind": "thermal_conductor", "G": 20.0}, "amb": {"kind": "fixed_temperature", "T": 290.0}},
          "connect": [["a.port", "v.port_a"], ["v.port_b", "vol.port"], ["vol.port", "cv.port_a"], ["cv.port_b", "b.port"],
                      ["vol.heatPort", "g.port_a"], ["g.port_b", "amb.port"]],
          "outputs": {"p": "vol.p", "T": "vol.T", "mv": "v.m_flow", "mc": "cv.m_flow"},
          "analysis": {"kind": "transient", "stop_time": 30, "intervals": 10, "tolerance": 1e-8}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.mass.ok", 1, {"abs": 0.5}, "mass balance")]


def _fl_gas_pipe():
    """FL15: a mass-flow source fills a 100 l air volume that feeds a gas valve and a rising, cooled 4-node dynamic
    pipe into the atmosphere (the pipe's last volume at the boundary's pressure; carbonio run_fl15_probe2: Q 4.5e-7,
    the others 7e-8)."""
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": 0.08, "T": 320.0, "medium": "air"},
                         "vol": {"kind": "fl_volume", "V": 0.1, "p_start": 2e5, "T_start": 300.0},
                         "v": {"kind": "fl_valve_gas", "Kv": 3.0, "p_nominal": 2e5, "dp_nominal": 5e4,
                               "m_flow_nominal": 0.1},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 30.0, "diameter": 0.05, "nNodes": 4,
                                  "heat_transfer": "constant", "alpha": 30.0, "T_start": 300.0, "p_a_start": 1.5e5,
                                  "p_b_start": 1e5, "height_ab": 5.0},
                         "w": {"kind": "fixed_temperature", "T": 300.0}, "b": {"kind": "fl_boundary", "p": 1e5, "T": 300.0}},
          "connect": [["src.port", "vol.port"], ["vol.port", "v.port_a"], ["v.port_b", "pipe.port_a"],
                      ["pipe.port_b", "b.port"], ["pipe.heat", "w.port"]],
          "outputs": {"p": "vol.p", "T": "vol.T", "m": "v.m_flow", "pa": "pipe.p_a", "Q": "pipe.heat.Q_flow"},
          "analysis": {"kind": "transient", "stop_time": 40, "intervals": 10, "tolerance": 1e-8}}
    return st, [("checks.reference.covered", 1, {"abs": 0.5}, "the reference covers it"),
                ("checks.reference.max_error_rel", 0.0, {"abs": 1e-4}, "OpenModelica = the reference"),
                ("checks.fluid_energy.ok", 1, {"abs": 0.5}, "energy balance")]


def _fl_npsh_lift():
    """FL16, FL17: 80 degC water lifted 3 m to a pump (tank level 1 m, the pump 4 m above the tank's port): NPSH
    available = (p_in + rho v^2/2 - p_vap(T_in)) / (rho g) = 1.143 m (the reference; by hand from its p_in, v and
    IF97's p_vap) against NPSH3 2.39 m at 15.8 m3/h (linear between the curve's flows): ratio 0.477, status 'below',
    warned, reliable. Onset: p_in - eye_dip 3 kPa - pulse_amplitude 1.5 kPa = 47.99 kPa, 0.6 kPa above p_vap (no
    cavitation onset) and 53.3 kPa below p_ambient (air release)."""
    st = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": 2.0, "level_start": 1, "T_start": 353.15},
                         "pipe": {"kind": "fl_pipe", "length": 6, "diameter": 0.04, "height_ab": 4.0},
                         "pump": {"kind": "fl_pump", "curve": [[0, 25], [0.004, 22], [0.008, 12]], "N_nominal": "2900 rpm",
                                  "npsh_required": [[0.002, 1.5], [0.006, 3.0], [0.01, 5.0]], "inlet_diameter": 0.04,
                                  "eye_dip": 3000, "pulse_amplitude": 1500},
                         "v": {"kind": "fl_valve", "Kv": 15, "dp_nominal": 1e5, "m_flow_nominal": 4},
                         "b": {"kind": "fl_boundary", "p": 1.5e5, "T": 353.15}},
          "connect": [["tank.port", "pipe.port_a"], ["pipe.port_b", "pump.port_a"], ["pump.port_b", "v.port_a"],
                      ["v.port_b", "b.port"]],
          "outputs": {"m": "pump.m_flow", "p": "pump.port_a.p"},
          "analysis": {"kind": "transient", "stop_time": 60, "intervals": 6, "tolerance": 1e-8}}
    return st, [("npsh.pump.NPSHa_min_m", 1.1427307251012755, 1e-4, "NPSH available (the reference's)"),
                ("npsh.pump.short", 1, {"abs": 0.5}, "below NPSH3: warned"),
                ("npsh.pump.ratio_min", 0.47728200078016436, 1e-4, "NPSHa / NPSH3 (status 'below')"),
                ("npsh.pump.T_in_at_min_K", 353.15 - 9.80665 * 4.0 / 4184, {"abs": 1e-5},
                 "the inlet T: the tank's less g height_ab / cp (StaticPipe)"),
                ("npsh.pump.onset.air_margin_min_Pa", 52493.23150138828 - 4500 - 101325, 1e-4,
                 "p_in - dip - amplitude - p_ambient: air release"),
                ("npsh.pump.onset.cavitation_onset", 0, {"abs": 0.5}, "0.6 kPa above the vapour pressure")]


FLUID_CASES = {"fl_drain_torricelli": _fl_drain, "fl_pipe_laminar": _fl_laminar, "fl_pump_system_curve": _fl_pump_system,
               "fl_valve_kv": _fl_valve, "fl_heated_pipe": _fl_heated_pipe, "fl_air_fill": _fl_air_fill,
               "fl_pipe_turbulent": _fl_turbulent, "fl_two_tanks": _fl_two_tanks, "fl_network_reference": _fl_network,
               "fl_cfdt_pump": _fl_cfdt_pump, "fl_cfdt_resistance": _fl_cfdt_resistance,
               "fl_sweep_opening": _fl_sweep_opening, "fl_fit_kv": _fl_fit_kv, "fl_check_valve": _fl_check_valve,
               "fl_tank_port_drain": _fl_tank_port_drain, "fl_loop_reference": _fl_loop_reference,
               "fl_heated_tank": _fl_heated_tank, "fl_pump_heating": _fl_pump_heating,
               "fl_thermal_reference": _fl_thermal_reference, "fl_pump_into_tank": _fl_pump_into_tank,
               "fl_heated_loop": _fl_heated_loop, "fl_pipe_loop_reference": _fl_pipe_loop_reference,
               "fl_volume_reference": _fl_volume_reference, "fl_glycol_loop": _fl_glycol_loop,
               "fl_essotherm_pipe": _fl_essotherm_pipe, "fl_static_head": _fl_static_head,
               "fl_gas_valve_volume": _fl_gas_valve_volume, "fl_gas_choked": _fl_gas_choked, "fl_gas_pipe": _fl_gas_pipe,
               "fl_npsh_lift": _fl_npsh_lift}
# judged on OpenModelica only: checks of OpenModelica's run against the reference (checks.reference); reference
# runs check their own energy balance since FL12 (fl_heated_tank runs there), gases since FL15 (fl_air_fill)
FLUID_OMC_ONLY = {"fl_heated_pipe", "fl_network_reference", "fl_loop_reference",
                  "fl_thermal_reference", "fl_pump_into_tank", "fl_heated_loop", "fl_pipe_loop_reference",
                  "fl_volume_reference", "fl_glycol_loop", "fl_essotherm_pipe", "fl_static_head", "fl_gas_valve_volume",
                  "fl_gas_choked", "fl_gas_pipe"}


def omc_only() -> set:
    """Cases the reference does not cover: Modelica.Fluid's dynamic pipes and gases (FL4 covers liquids), and the
    nonlinear ones when OMCT_REF_NONLINEAR=0 switches it off."""
    from .ref import nonlinear_on
    return set(FLUID_OMC_ONLY) | (set() if nonlinear_on() else set(NONLINEAR_CASES))


def cases() -> dict:
    out = {}
    for name, fn in (("rc_step", _rc), ("rlc_step", lambda: _rlc("step")), ("rlc_poles_bode", lambda: _rlc("lin")),
                     ("msd_modes", lambda: _msd("modes")), ("msd_free", lambda: _msd("free")),
                     ("inertia_torque_step", _inertia), ("dc_motor_steady", _dc_motor),
                     ("thermal_rc", lambda: _thermal("rc")), ("thermal_steady", lambda: _thermal("steady")),
                     ("two_mass", _two_mass), ("pi_first_order", _pi_loop), ("sweep_rc", _sweep_rc),
                     ("coolant_open_step", _coolant_open), ("coolant_loop_steady", _coolant_loop),
                     ("coolant_hydraulics", _coolant_hydraulics), ("dc_machine_nominal", lambda: _dcpm(False)),
                     ("dc_machine_losses", lambda: _dcpm(True)), ("resistor_self_heating", _self_heating),
                     ("damper_heating", _damper_heating), ("dc_machine_heating", _dcpm_heating),
                     ("limiter_loop", _limiter_loop), ("rectifier_mean", _rectifier), ("slew_rate_ramp", _slew),
                     ("dc_machine_brush", _dcpm_brush), ("three_phase_rl", lambda: _three_phase("star")),
                     ("three_phase_delta", lambda: _three_phase("delta")), ("im_grid_slip", _im_grid),
                     ("pmsm_current_torque", _pmsm_current), ("im_vf_start", _im_vf),
                     ("pmsm_damper_grid", _pmd_grid), ("quadratic_load", _quad_load),
                     ("qs_im_grid_slip", _qs_im_grid), ("qs_pmsm_current_torque", _qs_pmsm_current),
                     ("qs_im_vf_start", _qs_im_vf), ("qs_pmsm_damper_grid", _qs_pmd_grid),
                     ("im_grid_delta", lambda: _delta(False)), ("qs_im_grid_delta", lambda: _delta(True)),
                     ("fit_rc_tau", lambda: _fit_rc("tau")), ("fit_rc_unidentifiable", lambda: _fit_rc("both")),
                     ("fit_thermal_steady", lambda: _fit_thermal("steady")),
                     ("fit_thermal_curve", lambda: _fit_thermal("curve")), ("fit_msd", _fit_msd)):
        st, checks = fn()
        out[name] = {"study": st, "checks": checks}
    for name, fn in FLUID_CASES.items():
        st, checks = fn()
        out[name] = {"study": st, "checks": checks}
    return out


# ---- chains: values from the other translators (run only when named; skipped when the translator is missing)
def _chain_feat_modal():
    """feat's static cantilever (tip stiffness k = F/u) and its mass -> a mass on a spring here; Rayleigh's
    frequency sqrt(k / (33/140 m)) is 1.46 % above the first bending frequency, which feat's modal analysis of
    the same beam gives: the ratio checks the chain (units mm -> m, N/mm -> N/m, provenance) end to end."""
    from fea_translator import api as F
    from fea_translator import bench as FB

    from . import chains as CH
    fc = FB.cases()
    st = {n: {k: v for k, v in fc[n].items() if k != "expect"} for n in ("cantilever", "cantilever_modal")}
    r = F.run(st, None, wait=None)["studies"]
    for n in st:
        if not r[n].get("ok", True) or r[n].get("reliable") is False:
            raise RuntimeError(f"feat {n} failed: {r[n].get('why') or r[n].get('unreliable_because')}")
    k = CH.feat_stiffness(r["cantilever"], "tip", "uz", 100.0, "feat cantilever (100x10x10 mm steel), 100 N tip load")
    m = CH.feat_mass(r["cantilever_modal"], CH.RAYLEIGH_CANTILEVER, "33/140 x feat cantilever mass_kg (Rayleigh)")
    f_feat = CH.feat_frequency(r["cantilever_modal"], 0)
    study = {"components": {"m": {"kind": "mass", "m": m}, "k": {"kind": "spring", "c": k}, "base": {"kind": "fixed"},
                            "F": {"kind": "force"}},
             "connect": [["base.flange", "k.flange_a"], ["k.flange_b", "m.flange_a"], ["F.flange", "m.flange_b"]],
             "outputs": {"x": "m.s"}, "analysis": {"kind": "linearize", "inputs": {"F": "F.f"}}}
    checks = [("linearization.natural_frequencies_Hz.0", f_feat * CH.rayleigh_ratio(), 0.01,
               f"Rayleigh with feat's stiffness and mass = 1.0146 x feat's modal first frequency ({f_feat:.2f} Hz)"),
              ("inputs.k.c.si", k["value"] * 1000, 1e-12, "N/mm -> N/m, recorded with provenance")]
    return study, checks


def _chain_b3dt_mass():
    """A build123d-translator box's volume x density -> the mass here; f = sqrt(k/m)/(2 pi) exactly."""
    from . import chains as CH
    goal = {"features": [{"kind": "base_box", "params": {"w": 100, "d": 10, "h": 10}}]}
    m = CH.b3dt_mass(goal, 7850.0, "b3dt 100x10x10 mm box, steel")
    k = 2.0e5
    study = {"components": {"m": {"kind": "mass", "m": m}, "k": {"kind": "spring", "c": k}, "base": {"kind": "fixed"},
                            "F": {"kind": "force"}},
             "connect": [["base.flange", "k.flange_a"], ["k.flange_b", "m.flange_a"], ["F.flange", "m.flange_b"]],
             "outputs": {"x": "m.s"}, "analysis": {"kind": "linearize", "inputs": {"F": "F.f"}}}
    mass = 100 * 10 * 10 * 1e-9 * 7850
    return study, [("inputs.m.m.si", mass, 1e-9, "volume 1e4 mm3 x 7850 kg/m3"),
                   ("linearization.natural_frequencies_Hz.0", math.sqrt(k / mass) / (2 * math.pi), 1e-5,
                    "f = sqrt(k/m) / (2 pi)")]


def _chain_b3dt_inertia():
    """Two build123d-translator steel discs (r 50 mm, h 20 mm) on one shaft, one about its own axis and one about
    a parallel axis 30 mm away, on a torsion spring: J = m r^2 / 2 and J + m d^2; f = sqrt(c / J_total) / (2 pi)."""
    from . import chains as CH
    rho, r, h, d = 7850.0, 50.0, 20.0, 30.0
    goal = {"features": [{"kind": "base_cyl", "params": {"r": r, "h": h}}]}
    J1 = CH.b3dt_inertia(goal, rho, (0, 0, 1), None, "b3dt disc r 50 h 20 mm, steel, about its axis")
    J2 = CH.b3dt_inertia(goal, rho, (0, 0, 1), (d, 0, 0), "the same disc about a parallel axis 30 mm away")
    c = 1.0e4
    study = {"components": {"d1": {"kind": "inertia", "J": J1}, "d2": {"kind": "inertia", "J": J2},
                            "k": {"kind": "rot_spring", "c": c}, "base": {"kind": "rot_fixed"}, "T": {"kind": "torque"}},
             "connect": [["base.flange", "k.flange_a"], ["k.flange_b", "d1.flange_a"], ["d1.flange_b", "d2.flange_a"],
                         ["T.flange", "d2.flange_b"]],
             "outputs": {"phi": "d1.phi"}, "analysis": {"kind": "linearize", "inputs": {"tau": "T.tau"}}}
    m = rho * math.pi * r * r * h * 1e-9
    j1 = m * (r * 1e-3) ** 2 / 2
    j2 = j1 + m * (d * 1e-3) ** 2
    return study, [("inputs.d1.J.si", j1, 1e-9, "m r^2 / 2"),
                   ("inputs.d2.J.si", j2, 1e-9, "m r^2 / 2 + m d^2 (parallel axes)"),
                   ("linearization.natural_frequencies_Hz.0", math.sqrt(c / (j1 + j2)) / (2 * math.pi), 1e-5,
                    "f = sqrt(c / (J1 + J2)) / (2 pi)")]


CHAINS = {"chain_feat_modal": {"needs": "fea_translator", "prepare": _chain_feat_modal},
          "chain_b3dt_mass": {"needs": "build123d_translator", "prepare": _chain_b3dt_mass},
          "chain_b3dt_inertia": {"needs": "build123d_translator", "prepare": _chain_b3dt_inertia}}


def prepare(names) -> tuple[dict, dict, dict]:
    """(studies, checks, skipped) for the named cases: chains run their other translator now."""
    from . import chains as CH
    cs = cases()
    studies, checks, skipped = {}, {}, {}
    for n in names:
        if n in cs:
            studies[n], checks[n] = cs[n]["study"], cs[n]["checks"]
        elif n in CHAINS:
            if not CH.available(CHAINS[n]["needs"]):
                skipped[n] = f"needs {CHAINS[n]['needs']} (not installed here)"
                continue
            try:
                studies[n], checks[n] = CHAINS[n]["prepare"]()
            except Exception as exc:  # noqa: BLE001
                skipped[n] = f"{CHAINS[n]['needs']} step failed: {type(exc).__name__}: {exc}"
        else:
            skipped[n] = f"no benchmark {n} (benchmarks: {', '.join(list(cs) + list(CHAINS))})"
    return studies, checks, skipped


def evaluate(name: str, res: dict, cs: dict | None = None, checks: list | None = None) -> dict:
    from .post import get_path
    if checks is None:
        cs = cs or cases()
        checks_in = cs[name]["checks"]
    else:
        checks_in = checks
    checks = []
    for q, exp, tol, why in checks_in:
        try:
            got = float(get_path(res, q))
        except (KeyError, ValueError, IndexError, TypeError):
            checks.append({"quantity": q, "expected": exp, "got": None, "passed": False, "why": "no such result"})
            continue
        if isinstance(tol, dict):
            err, ok = abs(got - exp), abs(got - exp) <= tol["abs"]
        else:
            err = abs(got - exp) / abs(exp) if exp != 0 else abs(got)
            ok = err <= tol
        checks.append({"quantity": q, "expected": exp, "got": got, "error": err, "tol": tol, "passed": bool(ok),
                       "why": why})
    rel = bool(res.get("ok")) and bool(res.get("reliable"))
    out = {"passed": rel and all(c["passed"] for c in checks), "ok": res.get("ok"), "reliable": res.get("reliable"),
           "checks": checks}
    if not rel:
        out["why"] = res.get("why") or res.get("unreliable_because")
    return out


def run(solver: str, names=None, folder=None) -> dict:
    from pathlib import Path

    from .runner import run_study
    names = list(names) if names else list(cases())
    studies, checks, skipped = prepare(names)
    if solver == "ref":
        for n in [n for n in studies if n in omc_only()]:
            studies.pop(n)
            skipped[n] = "needs OpenModelica (not covered by the reference yet)"
    out = {n: {"skipped": why} for n, why in skipped.items()}
    for n, st in studies.items():
        r = run_study(n, st, Path(folder) / n, solver, 600)
        out[n] = evaluate(n, r, checks=checks[n])
        out[n]["checks_run"] = {k: v.get("ok") for k, v in (r.get("checks") or {}).items()}
    return out
