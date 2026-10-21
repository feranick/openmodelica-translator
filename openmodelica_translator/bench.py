"""bench.py — benchmark studies with closed-form answers: is the toolchain right?
Version: 2026.10.21.10

Each case is a study plus checks [(result path, expected, tolerance, why)]; tolerances are relative unless
written {"abs": x}. Chains (chain_feat_modal, chain_b3dt_mass) run the other translator first and only when named; they are skipped
when it is not installed. A case passes when the run is ok and reliable (balance, initialisation, solver, values read
back, energy, reference) and every check holds. The expected values are computed here from the closed forms.
"""
from __future__ import annotations

import math

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


def cases() -> dict:
    out = {}
    for name, fn in (("rc_step", _rc), ("rlc_step", lambda: _rlc("step")), ("rlc_poles_bode", lambda: _rlc("lin")),
                     ("msd_modes", lambda: _msd("modes")), ("msd_free", lambda: _msd("free")),
                     ("inertia_torque_step", _inertia), ("dc_motor_steady", _dc_motor),
                     ("thermal_rc", lambda: _thermal("rc")), ("thermal_steady", lambda: _thermal("steady")),
                     ("two_mass", _two_mass), ("pi_first_order", _pi_loop), ("sweep_rc", _sweep_rc)):
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


CHAINS = {"chain_feat_modal": {"needs": "fea_translator", "prepare": _chain_feat_modal},
          "chain_b3dt_mass": {"needs": "build123d_translator", "prepare": _chain_b3dt_mass}}


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
    out = {n: {"skipped": why} for n, why in skipped.items()}
    for n, st in studies.items():
        r = run_study(n, st, Path(folder) / n, solver, 600)
        out[n] = evaluate(n, r, checks=checks[n])
        out[n]["checks_run"] = {k: v.get("ok") for k, v in (r.get("checks") or {}).items()}
    return out
