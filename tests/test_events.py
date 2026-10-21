"""test_events.py — the reference's piecewise-linear elements (V2.5): modes, switching events, steady states,
against independent integrations of the MSL equations written out here.
Version: 2026.10.21.17
"""
import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from openmodelica_translator import bench, network
from openmodelica_translator.ref import Ref, RefError


def ref_of(st):
    return Ref(network.build(st))


def test_rectifier_with_capacitor_against_brute_force():
    st = {"components": {"vs": {"kind": "sine_voltage", "V": 10, "f": 50}, "d": {"kind": "ideal_diode", "Vknee": 0.7},
                         "rl": {"kind": "resistor", "R": 100}, "c": {"kind": "capacitor", "C": 1e-4}, "g": {"kind": "ground"}},
          "connect": [["vs.p", "d.p"], ["d.n", "rl.p"], ["rl.n", "g"], ["c.p", "d.n"], ["c.n", "g"], ["vs.n", "g"]]}
    t = np.linspace(0, 0.04, 801)
    r = ref_of(st)
    v = r.simulate(t)["c.v"]
    Ron, Goff, Vk, R, C = 1e-5, 1e-5, 0.7, 100.0, 1e-4

    def i_d(vd):        # MSL IdealDiode: off below the knee (i = Goff v), on above it
        return Goff * vd if vd < Vk * (1 + 0 * Goff) else (vd - Vk) / Ron + Goff * Vk
    sol = solve_ivp(lambda tt, y: [(i_d(10 * math.sin(2 * math.pi * 50 * tt) - y[0]) - y[0] / R) / C], (0, 0.04), [0.0],
                    method="Radau", rtol=1e-10, atol=1e-12, max_step=2e-5, dense_output=True)
    assert np.max(np.abs(v - sol.sol(t)[0])) < 1e-6
    assert len(r.switches) >= 4                         # on and off twice


def test_lim_pid_anti_windup_against_brute_force():
    """PI (k 2, Ti 0.5) with limits +-1 on a first-order plant (T 1 s), a set-point step to 0.8: saturates, the
    integrator tracks (Ni 0.9), then leaves the limit. The MSL LimPID equations written out."""
    st = {"components": {"sp": {"kind": "step", "height": 0.8}, "pid": {"kind": "lim_pid", "controllerType": "PI", "k": 2,
                                                                         "Ti": 0.5, "yMax": 1},
                         "plant": {"kind": "first_order", "k": 1, "T": 1}},
          "connect": [["sp.y", "pid.u_s"], ["plant.y", "pid.u_m"], ["pid.y", "plant.u"]]}
    t = np.linspace(0, 6, 601)
    out = ref_of(st).simulate(t)
    k, Ti, Ni, ymax = 2.0, 0.5, 0.9, 1.0

    def f(tt, x):
        I, y = x
        e = 0.8 - y
        yu = k * (e + I)
        u = min(max(yu, -ymax), ymax)
        return [(e + (u - yu) / (k * Ni)) / Ti, u - y]
    sol = solve_ivp(f, (0, 6), [0.0, 0.0], method="LSODA", rtol=1e-11, atol=1e-13, max_step=1e-3, dense_output=True)
    assert np.max(np.abs(out["plant.y"] - sol.sol(t)[1])) < 1e-6
    assert np.max(np.abs(out["pid.I.y"] - sol.sol(t)[0])) < 1e-6


def test_dead_zone_on_a_ramp():
    st = {"components": {"u": {"kind": "ramp", "height": 4, "duration": 4, "offset": -2},
                         "dz": {"kind": "dead_zone", "uMax": 1}, "i": {"kind": "integrator"}},
          "connect": [["u.y", "dz.u"], ["dz.y", "i.u"]]}
    t = np.linspace(0, 4, 401)
    out = ref_of(st).simulate(t)
    u = -2 + t
    y = np.where(u > 1, u - 1, np.where(u < -1, u + 1, 0.0))
    assert np.max(np.abs(out["dz.y"] - y)) < 1e-9
    assert out["i.y"][-1] == pytest.approx(-0.5 + 0.5, abs=1e-8)    # -1/2 then +1/2


def test_steady_state_picks_the_mode():
    st, checks = bench._limiter_loop()
    r = ref_of(st)
    ss = r.steady(1.0)
    assert ss["plant.y"] == pytest.approx(50 / 6, rel=1e-12)
    st["components"]["kp"]["k"] = 50                    # saturated for good? no: y -> 10 * 50/51 < limit 20
    assert ref_of(st).steady(1.0)["lim.y"] == pytest.approx(500 / 51, rel=1e-12)
    st["components"]["lim"]["uMax"] = 5                 # now it stays at the limit: y -> 5
    assert ref_of(st).steady(1.0)["plant.y"] == pytest.approx(5, rel=1e-12)


def test_no_linearisation_with_events():
    st, _ = bench._limiter_loop()
    with pytest.raises(RefError):
        ref_of(st).eigenvalues()
