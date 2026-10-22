"""bench.py — benchmark studies with closed-form answers: is the toolchain right?
Version: 2026.10.21.23

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


NONLINEAR_CASES = {"im_grid_slip", "pmsm_current_torque", "im_vf_start", "pmsm_damper_grid", "quadratic_load"}


def omc_only() -> set:
    """Cases the reference does not cover yet: the AC machines unless OMCT_REF_NONLINEAR=1 (experimental)."""
    return set() if os.environ.get("OMCT_REF_NONLINEAR", "") in ("1", "true", "yes") else set(NONLINEAR_CASES)


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
                     ("pmsm_damper_grid", _pmd_grid), ("quadratic_load", _quad_load)):
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
