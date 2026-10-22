"""test_nonlinear.py — the reference's nonlinear terms (V3.3; OMCT_REF_NONLINEAR=0 switches it off): speed-dependent loads
against closed forms, AC machines (current-fed PMSM with and without damper cage, a line-connected PMSM, the
induction machine's energy balance), the Radau IIA DAE integrator, and the flag.
Version: 2026.10.21.26
"""
import copy
import math

import numpy as np
import pytest

from openmodelica_translator import bench, modelica, network, refnl, runner
from openmodelica_translator.ref import Ref

W50 = 2 * math.pi * 50


@pytest.fixture
def nl_on(monkeypatch):
    monkeypatch.delenv("OMCT_REF_NONLINEAR", raising=False)     # on by default


def _load_study(load):
    return {"components": {"src": {"kind": "constant_torque", "tau_constant": -10.0 if load.get("neg") else 10.0},
                           "J": {"kind": "inertia", "J": 0.5},
                           "tl": {k: v for k, v in load.items() if k != "neg"}},
            "connect": [["src.flange", "J.flange_a"], ["J.flange_b", "tl.flange"]]}


def test_quadratic_load_tanh(nl_on):
    # J w' = 10 - 0.2 w^2 (tau = -20 (w/10)^2 for w > 0): w = sqrt(50) tanh(t sqrt(2)/J)
    st = _load_study({"kind": "quadratic_speed_load", "tau_nominal": -20.0, "w_nominal": 10.0, "TorqueDirection": False})
    r = Ref(network.build(st))
    assert r.exact and r.nonlinear
    t = np.linspace(0, 2, 401)
    w = r.simulate(t)["J.w"]
    ex = math.sqrt(50) * np.tanh(t * math.sqrt(2) / 0.5)
    assert np.max(np.abs(w - ex)) < 1e-7 * math.sqrt(50)


def test_linear_load_torque_direction_abs(nl_on):
    # TorqueDirection true: tau = 2 |w| opposes w < 0: J w' = -10 - 2 w -> w = -5 (1 - exp(-4 t))
    st = _load_study({"kind": "linear_speed_load", "tau_nominal": 2.0, "w_nominal": 1.0, "TorqueDirection": True,
                      "neg": True})
    r = Ref(network.build(st))
    assert r.exact and r.nonlinear
    t = np.linspace(0, 2, 401)
    w = r.simulate(t)["J.w"]
    assert np.max(np.abs(w + 5 * (1 - np.exp(-4 * t)))) < 1e-7 * 5


def test_linear_load_stays_linear():
    st = _load_study({"kind": "linear_speed_load", "tau_nominal": -2.0, "w_nominal": 1.0, "TorqueDirection": False})
    r = Ref(network.build(st))
    assert r.exact and not r.nonlinear


def _pmsm_current(stop=0.5, damper=False):
    st = copy.deepcopy(bench.cases()["pmsm_current_torque"]["study"])
    if damper:
        st["components"]["M"].update({"kind": "pmsm_damper", "Lrsigmad": 0.05 / W50, "Rrd": 0.04})
    st["analysis"] = {"kind": "transient", "stop_time": stop, "intervals": int(2000 * stop)}
    st["derived"] = [{"kind": "window", "signal": "T", "t0": 0.8 * stop, "t1": stop}]
    return st


@pytest.mark.parametrize("damper", [False, True])
def test_pmsm_current_drive_torque_and_energy(nl_on, tmp_path, damper):
    st = _pmsm_current(damper=damper)
    tr = modelica.translate(st)
    assert tr.ref._nl_ode() is None                 # voltage-fed orientation: not an ODE
    alt = tr.ref._alt_orientation()
    assert alt is not None, getattr(tr.ref, "_alt_why", "")   # current-fed orientation: an ODE with late terms
    res = runner.run_study("pm", st, tmp_path / "pm", "ref", 300)
    assert res["ok"] and res["reliable"], res.get("why")
    T = 1.5 * 2 * (math.sqrt(2) * 112.3 / W50) * math.sqrt(2) * 84.6
    assert abs(res["derived"]["T"]["mean"] - T) < 1e-8 * T
    assert res["checks"]["energy"]["ok"] and res["checks"]["energy"]["error_rel"] < 1e-6


def test_pmsm_damper_on_the_grid_runs_synchronous(nl_on, tmp_path):
    st = {"components": {"g": {"kind": "three_phase_grid", "V_rms": 112.3, "f": 50},
                         "M": dict(bench.PM_DATA, kind="pmsm_damper", Lrsigmad=0.05 / W50, Rrd=0.04, w0=W50 / 2),
                         "load": {"kind": "inertia", "J": 0.29},
                         "tl": {"kind": "torque_step", "stepTorque": -50, "startTime": 0.2}},
          "connect": [["g.plug", "M.plug"], ["M.flange", "load.flange_a"], ["load.flange_b", "tl.flange"]],
          "outputs": {"w": "M.wMechanical", "T": "M.tauElectrical"},
          "analysis": {"kind": "transient", "stop_time": 1.5, "intervals": 3000},
          "derived": [{"kind": "window", "signal": x, "t0": 1.3, "t1": 1.5} for x in ("w", "T")]}
    res = runner.run_study("pmg", st, tmp_path / "pmg", "ref", 300)
    assert res["ok"] and res["reliable"], res.get("why")
    assert abs(res["derived"]["w"]["mean"] - W50 / 2) < 1e-6 * W50
    assert abs(res["derived"]["T"]["mean"] - 50.0) < 1e-5 * 50
    assert res["checks"]["energy"]["ok"]


def test_induction_machine_start_energy(nl_on, tmp_path):
    st = copy.deepcopy(bench.cases()["im_grid_slip"]["study"])
    st["analysis"] = {"kind": "transient", "stop_time": 0.2, "intervals": 400}
    st["derived"] = []
    res = runner.run_study("im", st, tmp_path / "im", "ref", 300)
    assert res["ok"] and res["reliable"], res.get("why")
    assert res["checks"]["energy"]["ok"] and res["checks"]["energy"]["error_rel"] < 1e-3


def test_flag_0_leaves_machines_uncovered(monkeypatch, tmp_path):
    monkeypatch.setenv("OMCT_REF_NONLINEAR", "0")
    st = _pmsm_current(stop=0.1)
    r = modelica.translate(st).ref
    assert not r.exact and any("OMCT_REF_NONLINEAR" in x for x in r.not_exact)
    res = runner.run_study("pm", st, tmp_path / "pm", "ref", 60)
    assert not res["ok"] and "switched off" in res["why"]


def test_radau_index1_dae():
    # y' = -y + z, 0 = z - y^2: y = 1 / (1 + (1/y0 - 1) e^t)
    M = np.array([[1.0, 0.0], [0.0, 0.0]])
    f = lambda t, x: np.array([-x[0] + x[1], x[0] ** 2 - x[1]])
    jac = lambda t, x: np.array([[-1.0, 1.0], [2 * x[0], -1.0]])
    y0 = 0.5
    steps, z1 = refnl.radau(refnl.System(M, f, jac), 0.0, 2.0, np.array([y0, y0 ** 2]), rtol=1e-10,
                            atol=np.full(2, 1e-12))
    ex = 1 / (1 + (1 / y0 - 1) * math.exp(2.0))
    assert abs(z1[0] - ex) < 1e-9 and abs(z1[1] - ex ** 2) < 1e-9
    st = steps[len(steps) // 2]
    tm = st.t0 + 0.5 * st.h
    assert abs(st(tm)[0] - 1 / (1 + (1 / y0 - 1) * math.exp(tm))) < 1e-8
