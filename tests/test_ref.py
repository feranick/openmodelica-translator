"""test_ref.py — the reference implementation against closed forms (it runs without OpenModelica).
Version: 2026.10.21.1
"""
import math

import numpy as np
import pytest
from studies import DC_PI, MSD, RC, RLC, THERMAL, TWO_MASS, study

from openmodelica_translator.network import build
from openmodelica_translator.ref import Ref, RefError


def ref_of(st):
    net = build(st)
    assert not net.errors, net.errors
    r = Ref(net)
    sel, dep, missing = r.init_selection()
    assert not dep and missing == 0
    return r


def test_rc_step():
    r = ref_of(RC)
    t = np.linspace(0, 5e-3, 51)
    v = r.simulate(t)["C.v"]
    assert np.max(np.abs(v - (1 - np.exp(-t / 1e-3)))) < 1e-9
    assert r.eigenvalues() == pytest.approx([-1000.0])


def test_rlc_underdamped():
    r = ref_of(RLC)
    t = np.linspace(0, 0.05, 2001)
    out = r.simulate(t)
    R, L, Cc = 10, 0.01, 1e-4
    w0, z = 1 / math.sqrt(L * Cc), R / 2 * math.sqrt(Cc / L)
    wd = w0 * math.sqrt(1 - z * z)
    tt = np.clip(t - 0.001, 0, None)
    ex = 12 * (1 - np.exp(-z * w0 * tt) * (np.cos(wd * tt) + z / math.sqrt(1 - z * z) * np.sin(wd * tt)))
    assert np.max(np.abs(out["C.v"] - ex)) < 1e-8
    ev = sorted(r.eigenvalues(), key=lambda x: x.imag)
    assert ev[1].real == pytest.approx(-z * w0, rel=1e-10) and ev[1].imag == pytest.approx(wd, rel=1e-10)
    # LossPower = v*i (a product of linear variables)
    assert np.allclose(out["R.LossPower"], out["R.v"] * out["R.i"])


def test_msd_free_vibration():
    r = ref_of(MSD)
    t = np.linspace(0, 2, 401)
    s = r.simulate(t)["m.s"]
    wn, ze = math.sqrt(400), 4 / (2 * math.sqrt(1600))
    wd = wn * math.sqrt(1 - ze ** 2)
    ex = 0.1 * np.exp(-ze * wn * t) * (np.cos(wd * t) + ze / math.sqrt(1 - ze ** 2) * np.sin(wd * t))
    assert np.max(np.abs(s - ex)) < 1e-10


def test_thermal_rc_and_steady():
    r = ref_of(THERMAL)
    t = np.linspace(0, 20000, 201)
    T = r.simulate(t)["blk.T"]
    tau = 1800 / 0.5
    assert np.max(np.abs(T - (293.15 + 100 * (1 - np.exp(-t / tau))))) < 1e-7
    assert r.steady(1e9)["blk.T"] == pytest.approx(393.15, rel=1e-12)


def test_inertia_torque_step_with_gear_and_dependent_init():
    st = {"components": {"J1": {"kind": "inertia", "J": 1, "w0": 1}, "J2": {"kind": "inertia", "J": 2},
                         "gr": {"kind": "ideal_gear", "ratio": 2}, "J3": {"kind": "inertia", "J": 4},
                         "T": {"kind": "torque_step", "stepTorque": 3}},
          "connect": [["T.flange", "J1.flange_a"], ["J1.flange_b", "J2.flange_a"], ["J2.flange_b", "gr.flange_a"],
                      ["gr.flange_b", "J3.flange_a"]]}
    r = ref_of(st)
    out = r.simulate(np.linspace(0, 1, 11))
    jeq = 1 + 2 + 4 / 4
    assert out["J1.w"][-1] == pytest.approx(1 + 3 / jeq, rel=1e-10)
    assert out["J3.w"][-1] == pytest.approx((1 + 3 / jeq) / 2, rel=1e-10)
    st["components"]["J2"]["w0"] = 1
    net = build(st)
    _, dep, _ = Ref(net).init_selection()
    assert [(d["elem"], d["name"]) for d in dep] == [("J2", "w0")]


def test_dc_motor_steady_speed():
    st = {"components": {"M": {"kind": "dc_motor", "R": 2.0, "L": 1e-3, "k": 0.1, "J": 1e-4, "d": 1e-4},
                         "V": {"kind": "constant_voltage", "V": 12}, "g": {"kind": "ground"}},
          "connect": [["V.p", "M.p"], ["V.n", "g"], ["M.n", "g"]]}
    r = ref_of(st)
    w = r.simulate(np.linspace(0, 2, 201))["M__rotor.w"]        # the free angle has no steady state
    assert w[-1] == pytest.approx(0.1 * 12 / (0.01 + 2.0 * 1e-4), rel=1e-9)
    with pytest.raises(RefError):
        r.steady(1.0)
    # electrical and mechanical poles: s^2 L J + s (R J + L d) + (R d + k^2) = 0
    ev = np.sort(r.eigenvalues().real)
    roots = np.sort(np.roots([1e-3 * 1e-4, 2.0 * 1e-4 + 1e-3 * 1e-4, 2.0 * 1e-4 + 0.01]).real)
    assert ev[:2] == pytest.approx(roots, rel=1e-9) and abs(ev[2]) < 1e-9   # the third: the free angle


def test_pi_loop_closed_form():
    """PI (k, T) on a first-order plant K/(tau s + 1): closed loop is second order with a zero."""
    k, T, K, tau = 2.0, 0.5, 1.5, 0.2
    st = {"components": {"r": {"kind": "step", "height": 1}, "fb": {"kind": "feedback"},
                         "pi": {"kind": "pi", "k": k, "T": T}, "p": {"kind": "first_order", "k": K, "T": tau}},
          "connect": [["r.y", "fb.u1"], ["p.y", "fb.u2"], ["fb.y", "pi.u"], ["pi.y", "p.u"]]}
    r = ref_of(st)
    t = np.linspace(0, 3, 301)
    y = r.simulate(t)["p.y"]
    # Y/R = kK(Ts+1) / (tau T s^2 + T(1 + kK) s + kK)
    from scipy import signal
    sys_ = signal.lti([k * K * T, k * K], [tau * T, T * (1 + k * K), k * K])
    _, ye = signal.step(sys_, T=t)
    assert np.max(np.abs(y - ye)) < 1e-8


def test_two_mass_eigenvalues_and_frequency_response():
    r = ref_of(TWO_MASS)
    ev = r.eigenvalues()
    J1, J2, c = 0.01, 0.04, 1000.0
    w = math.sqrt(c * (J1 + J2) / (J1 * J2))
    im = np.sort(np.abs(ev.imag))
    assert im[-1] == pytest.approx(w, rel=1e-9) and np.max(np.abs(ev.real)) < 1e-6
    assert np.sort_complex(r.pencil_eigenvalues()).size == 4
    # at low frequency the shaft is rigid: w2/tau = 1/((J1 + J2) j omega)
    f = 0.1
    g = r.freq_response("tau", {"J2.w": 1.0}, [f])[0]
    assert g == pytest.approx(1 / (1j * 2 * math.pi * f * (J1 + J2)), rel=1e-4)


def test_index2_capacitor_across_source_and_parallel_caps():
    st = {"components": {"V": {"kind": "sine_voltage", "V": 1, "f": 50}, "C1": {"kind": "capacitor", "C": 1e-6},
                         "R": {"kind": "resistor", "R": 100}, "C2": {"kind": "capacitor", "C": 1e-5},
                         "C3": {"kind": "capacitor", "C": 2e-5}, "g": {"kind": "ground"}},
          "connect": [["V.p", "C1.p"], ["C1.n", "g"], ["V.n", "g"], ["V.p", "R.p"], ["R.n", "C2.p"], ["C2.n", "g"],
                      ["C3.p", "C2.p"], ["C3.n", "g"]]}
    r = ref_of(st)
    t = np.linspace(0, 0.02, 201)
    out = r.simulate(t)
    w, tau = 2 * math.pi * 50, 100 * 3e-5
    assert np.max(np.abs(out["C1.i"] - 1e-6 * w * np.cos(w * t))) < 1e-12
    ex = (w * tau * np.exp(-t / tau) + np.sin(w * t) - w * tau * np.cos(w * t)) / (1 + (w * tau) ** 2)
    assert np.max(np.abs(out["C2.v"] - ex)) < 1e-9
    assert [(s["elem"], s["name"]) for s in r.selected] == [("C2", "v0")]


def test_blocks_table_tf_pid_and_sensor_between_caps():
    st = {"components": {"tb": {"kind": "table", "table": [[0, 0], [1, 1], [2, 1]]},
                         "tf": {"kind": "transfer_function", "b": [1], "a": [0.5, 1]},
                         "fo": {"kind": "first_order", "T": 0.5}, "pid": {"kind": "pid", "k": 2, "Ti": 0.5, "Td": 0.1}},
          "connect": [["tb.y", "tf.u"], ["tb.y", "fo.u"], ["tb.y", "pid.u"]]}
    r = ref_of(st)
    t = np.linspace(0, 3, 301)
    out = r.simulate(t)
    assert np.max(np.abs(out["tf.y"] - out["fo.y"])) < 1e-12
    assert out["tb.y[1]"][50] == pytest.approx(0.5) and out["tb.y[1]"][-1] == pytest.approx(1.0)
    # PID: y = k*(u + 1/Ti*int(u) + D), D -> Td*du/dt; at t = 3: u = 1, int(u) = 0.5 + 2 = 2.5, D ~ 0
    assert out["pid.y"][-1] == pytest.approx(2 * (1 + 2.5 / 0.5), rel=1e-9)
    st2 = {"components": {"I": {"kind": "constant_current", "I": 1e-3}, "C1": {"kind": "capacitor", "C": 1e-6},
                          "C2": {"kind": "capacitor", "C": 3e-6}, "cs": {"kind": "current_sensor"},
                          "g": {"kind": "ground"}, "out": {"kind": "gain", "k": 1}},
           "connect": [["I.n", "C1.p"], ["I.p", "g"], ["C1.n", "g"], ["C1.p", "cs.p"], ["cs.n", "C2.p"],
                       ["C2.n", "g"], ["cs.i", "out.u"]]}
    r2 = ref_of(st2)
    o2 = r2.simulate(np.linspace(0, 1e-3, 11))
    assert o2["C1.v"][-1] == pytest.approx(0.25, rel=1e-10)
    assert o2["out.y"][-1] == pytest.approx(0.75e-3, rel=1e-10)


def test_pulse_and_opamp_integrator():
    st = {"components": {"V": {"kind": "pulse_voltage", "V": 1, "period": 2e-3}, "Ri": {"kind": "resistor", "R": 1000},
                         "Cf": {"kind": "capacitor", "C": 1e-6}, "op": {"kind": "opamp"}, "g": {"kind": "ground"}},
          "connect": [["V.p", "Ri.p"], ["V.n", "g"], ["Ri.n", "op.in_n"], ["op.in_p", "g"], ["Cf.p", "op.in_n"],
                      ["Cf.n", "op.out"]]}
    r = ref_of(st)
    t = np.linspace(0, 4e-3, 41)
    v = r.simulate(t)["op.out.v"]
    # on for 1 ms (ramps to -1 V), off for 1 ms (holds), twice
    assert v[10] == pytest.approx(-1.0, rel=1e-9) and v[20] == pytest.approx(-1.0, rel=1e-9)
    assert v[40] == pytest.approx(-2.0, rel=1e-9)


def test_singular_and_nonlinear():
    st = {"components": {"I": {"kind": "constant_current", "I": 1}, "L": {"kind": "inductor", "L": 1},
                         "g": {"kind": "ground"}},
          "connect": [["I.p", "g"], ["I.n", "L.p"], ["L.n", "g"]]}
    net = build(st)
    r = Ref(net)                  # an inductor in series with a current source: its current follows
    sel, dep, missing = r.init_selection()
    assert sel == [] and missing == 0 and r.d == 0
    st = study(RC)
    st["components"]["d"] = {"kind": "ideal_diode"}
    st["connect"] = [["V.p", "d.p"], ["d.n", "R.p"], ["R.n", "C.p"], ["C.n", "g"], ["V.n", "g"]]
    r = Ref(build(st))
    assert not r.exact and "diode" in r.not_exact[0]


def test_dc_pi_runs():
    r = ref_of(DC_PI)
    out = r.simulate(np.linspace(0, 0.2, 401))
    assert out["load.w"][-1] == pytest.approx(100, rel=1e-3)


def test_inconsistent_sources_raise():
    st = {"components": {"V1": {"kind": "constant_voltage", "V": 1}, "V2": {"kind": "constant_voltage", "V": 2},
                         "g": {"kind": "ground"}},
          "connect": [["V1.p", "V2.p"], ["V1.n", "g"], ["V2.n", "g"]]}
    with pytest.raises(RefError):
        Ref(build(st))
