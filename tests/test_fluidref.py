"""test_fluidref.py — the reference for Modelica.Fluid liquids (V5 FL4): MSL's functions, coverage, the network.
Version: 2026.11.19.26
"""
import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from openmodelica_translator import fluidref as F
from openmodelica_translator.format import validate_study
from openmodelica_translator.modelica import translate
from openmodelica_translator.network import build

LIQ = {"rho": 1000.0, "cp": 4000.0, "eta": 1e-3}


# ---- MSL functions ----------------------------------------------------------------------------------------------
def test_reg_root2_is_the_root_outside_and_c1_at_the_joints():
    for k1, k2 in ((1000, 1000), (1000, 500), (200, 900)):
        assert F.reg_root2(4.0, 1.0, k1, k2) == pytest.approx(math.sqrt(4 * k1))
        assert F.reg_root2(-4.0, 1.0, k1, k2) == pytest.approx(-math.sqrt(4 * k2))
        for x in (1.0, -1.0):                         # continuous at +-x_small, monotonic through 0
            e = 1e-9
            assert F.reg_root2(x - e, 1.0, k1, k2) == pytest.approx(F.reg_root2(x + e, 1.0, k1, k2), rel=1e-6)
        xs = np.linspace(-1.5, 1.5, 301)
        assert np.all(np.diff([F.reg_root2(x, 1.0, k1, k2) for x in xs]) > 0)
    assert F.reg_root2(0.0, 1.0, 1000, 1000) == 0.0


def test_smooth_power_and_reg_root():
    assert F.smooth_power(4.0, 1.0, 0.5) == 2.0 and F.smooth_power(-4.0, 1.0, 0.5) == -2.0
    assert F.smooth_power(1.0 - 1e-12, 1.0, 0.5) == pytest.approx(1.0)
    assert F.reg_root(100.0, 0.0) == pytest.approx(10.0)


def test_reg_fun3_meets_its_end_points():
    for args in ((0.0, 1.0, 0.0, 2.0, 1.0, 3.0), (-1.0, 0.0, -2.0, 0.0, 3.0, 0.5), (0.0, 2.0, 0.0, 1.0, 0.1, 0.1)):
        x0, x1, y0, y1, d0, d1 = args
        assert F.reg_fun3(x0, *args)[0] == pytest.approx(y0, abs=1e-12)
        assert F.reg_fun3(x1, *args)[0] == pytest.approx(y1, abs=1e-12)
        ys = [F.reg_fun3(x, *args)[0] for x in np.linspace(x0, x1, 101)]
        assert np.all(np.diff(ys) >= -1e-12)          # co-monotone


def test_detailed_friction_laminar_turbulent_and_continuous():
    rho, mu, L = 1000.0, 1e-3, 10.0
    d = 0.01
    hp = rho * math.pi * d ** 4 * 10.0 / (128 * mu * L)
    assert F.pipe_detailed(10.0, rho, mu, L, d, 0.0, 1.0, 0.0) == pytest.approx(hp, rel=1e-12)
    d, rough, dp = 0.02, 2.5e-5, 1e4                  # Colebrook-White, explicit in dp
    lam2 = 2 * dp * d ** 3 * rho / (L * mu ** 2)
    Re = -2 * math.sqrt(lam2) * math.log10(2.51 / math.sqrt(lam2) + 0.27 * rough / d)
    assert F.pipe_detailed(dp, rho, mu, L, d, rough, 1.0, 0.0) == pytest.approx(math.pi * d * mu * Re / 4, rel=1e-12)
    dps = np.geomspace(1e-3, 1e5, 4000)               # laminar -> transition -> turbulent: continuous, rising
    m = np.array([F.pipe_detailed(x, rho, mu, L, d, rough, 1.0, 0.0) for x in dps])
    assert np.all(np.diff(m) > 0) and np.max(np.diff(m) / m[1:]) < 0.02
    assert F.pipe_detailed(-dp, rho, mu, L, d, rough, 1.0, 0.0) == pytest.approx(-F.pipe_detailed(dp, rho, mu, L, d,
                                                                                                   rough, 1.0, 0.0))
    gh = 9.80665 * 2.0                                # static head: no flow at dp = rho g h
    assert F.pipe_detailed(rho * gh, rho, mu, L, d, rough, 1.0, gh) == pytest.approx(0.0, abs=1e-15)


def test_pump_curves_extrapolate_linearly():
    assert F.pump_curve(0.5, [0, 1], [10, 0]) == pytest.approx(5.0)
    v, h = [0.0, 0.005, 0.01], [20.0, 15.0, 0.0]
    assert F.pump_curve(0.005, v, h) == pytest.approx(15.0)
    c = np.polyfit(v, h, 2)
    slope = 2 * c[0] * 0.01 + c[1]
    assert F.pump_curve(0.012, v, h) == pytest.approx(0.0 + 0.002 * slope)
    assert F.valve_char("equal_percentage", 1.0, 20) == 1.0
    assert F.valve_char("equal_percentage", 0.5, 20) == pytest.approx(20 ** -0.5)


# ---- coverage ---------------------------------------------------------------------------------------------------
DRAIN = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": 0.5, "level_start": 2, "medium": LIQ},
                        "o": {"kind": "fl_orifice", "diameter": 0.03, "zeta": 1.0}, "out": {"kind": "fl_boundary"}},
         "connect": [["tank.port", "o.port_a"], ["o.port_b", "out.port"]],
         "outputs": {"l": "tank.level", "m": "o.m_flow"}, "analysis": {"kind": "transient", "stop_time": 100}}


def test_translate_uses_the_fluid_reference_when_covered():
    tr = translate(DRAIN)
    assert getattr(tr.ref, "fluid", False) and tr.ref.exact
    assert not any("not covered" in w for w in validate_study(DRAIN)["warnings"])


@pytest.mark.parametrize("change,why", [
    ({"o": {"kind": "fl_pipe", "length": 10.0, "diameter": 0.03, "friction": "turbulent"}}, "nominal_laminar only"),
    ({"o": {"kind": "fl_dynamic_pipe", "length": 10.0, "diameter": 0.03, "friction": "turbulent"}},
     "nominal_laminar only"),
])
def test_not_covered_says_why(change, why):
    st = copy.deepcopy(DRAIN)
    st["components"].update(change)
    if "out" in change:
        st["components"]["out2"] = {"kind": "fl_boundary"}
        st["connect"].append(["out.port", "out2.port"])
    if st["components"]["tank"]["kind"] != "fl_tank":
        st["outputs"] = {"m": "o.m_flow"}
    v = validate_study(st)
    if not v["ok"]:
        pytest.skip("; ".join(v["errors"]))
    ok, msg = F.covered(build(st))
    assert not ok and why in msg
    assert any(why in w for w in v["warnings"])
    assert not getattr(translate(st).ref, "fluid", False)


def test_inputs_that_change_the_flows_are_not_covered():
    st = copy.deepcopy(DRAIN)
    st["components"]["out"]["p"] = 1e5
    st["components"]["ps"] = {"kind": "step", "height": 1e4, "offset": 1e5, "startTime": 10}
    st["connect"].append(["ps.y", "out.p_in"])
    del st["components"]["out"]["p"]
    v = validate_study(st)
    assert v["ok"], v["errors"]
    ok, msg = F.covered(build(st))
    assert not ok and "input p_in" in msg


# ---- the network --------------------------------------------------------------------------------------------------
def test_drain_matches_torricelli_and_port_flows_balance():
    r = F.FluidRef(build(DRAIN))
    t = np.linspace(0, 100, 11)
    v = r.simulate(t)
    Ao, A, g = math.pi * 0.03 ** 2 / 4, 0.5, 9.80665
    h = (math.sqrt(2) - Ao / (2 * A) * math.sqrt(2 * g) * t) ** 2
    assert np.allclose(v["tank.level"], h, rtol=1e-8)
    assert np.allclose(v["tank.ports[1].m_flow"], -v["o.port_a.m_flow"])       # out of the tank
    assert np.allclose(v["out.ports[1].m_flow"], v["o.port_a.m_flow"])         # into the boundary
    assert np.allclose(v["o.port_a.p"], 101325 + 1000 * g * h, rtol=1e-12)


def test_parallel_branches_and_mass_flow_source():
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": 2.0, "medium": LIQ},
                         "p1": {"kind": "fl_pipe", "length": 10, "diameter": 0.03},
                         "p2": {"kind": "fl_pipe", "length": 20, "diameter": 0.03},
                         "ps": {"kind": "fl_pressure"}, "out": {"kind": "fl_boundary"}},
          "connect": [["src.port", "p1.port_a"], ["src.port", "p2.port_a"], ["p1.port_b", "out.port"],
                      ["p2.port_b", "out.port"], ["ps.port", "p1.port_a"]],
          "outputs": {"m1": "p1.m_flow", "m2": "p2.m_flow", "p": "ps.p"}, "analysis": {"kind": "transient", "stop_time": 1}}
    assert validate_study(st)["ok"], validate_study(st)["errors"]
    v = F.FluidRef(build(st)).simulate(np.array([0.0, 1.0]))
    m1, m2 = v["p1.port_a.m_flow"], v["p2.port_a.m_flow"]
    assert np.allclose(m1 + m2, 2.0, rtol=1e-12) and np.all(m1 > m2)
    assert np.allclose(v["src.ports[1].m_flow"] + v["src.ports[2].m_flow"], -2.0, rtol=1e-12)
    assert np.allclose(v["src.ports[1].m_flow"], -m1)
    assert np.allclose(v["ps.p"], v["p1.port_a.p"]) and np.allclose(v["p1.port_a.p"], v["p2.port_a.p"])


def test_reference_check_compares_temperatures_and_skips_what_it_lacks():
    from openmodelica_translator.runner import _reference
    st = copy.deepcopy(DRAIN)
    st["components"]["out"]["T"] = 300.0
    st["outputs"]["T"] = "tank.T"
    tr = translate(st)
    t = np.linspace(0, 100, 51)
    v = tr.ref.simulate(t)
    assert np.allclose(v["tank.medium.T"], 293.15)          # draining: the tank keeps its start temperature
    vals = {"l": v["tank.level"], "m": v["o.port_a.m_flow"] * (1 + 1e-9), "T": np.full(t.size, 293.15)}
    out = {"checks": {}}
    _reference(out, st, tr, t, vals, 1e-6)
    ref = out["checks"]["reference"]
    assert ref["covered"] and ref["ok"] and "T" in ref["per_output"] and "not_compared" not in ref
    vals["T"] = np.full(t.size, 300.0)
    out = {"checks": {}}
    _reference(out, st, tr, t, vals, 1e-6)
    assert not out["checks"]["reference"]["ok"]
    sim = tr.ref.simulate                                     # a path the reference lacks: not compared
    tr.ref.simulate = lambda tt, t0=None: {k: x for k, x in sim(tt, t0).items() if k != "tank.medium.T"}
    vals["T"] = np.full(t.size, 293.15)
    out = {"checks": {}}
    _reference(out, st, tr, t, vals, 1e-6)
    assert out["checks"]["reference"]["not_compared"] == ["T"]


def test_steady_without_tanks_for_fits():
    st = {"components": {"a": {"kind": "fl_boundary", "p": 1.1e5, "medium": LIQ},
                         "v": {"kind": "fl_valve", "Kv": 10, "dp_nominal": 1e5, "m_flow_nominal": 3.0},
                         "b": {"kind": "fl_boundary", "p": 1e5}},
          "connect": [["a.port", "v.port_a"], ["v.port_b", "b.port"]], "outputs": {"m": "v.m_flow"},
          "analysis": {"kind": "transient", "stop_time": 1}}
    s = translate(st).ref.steady(1.0)
    assert s["v.port_a.m_flow"] == pytest.approx(27.7e-6 * 10 * math.sqrt(1000 * 1e4), rel=1e-9)


def test_covered_studies_skip_the_cross_solver_run():
    from openmodelica_translator.format import cross_checked
    assert not cross_checked(DRAIN)
    assert cross_checked({**DRAIN, "cross_check": True})
    assert not cross_checked({**DRAIN, "cross_check": False})
    st = copy.deepcopy(DRAIN)
    st["components"]["o"] = {"kind": "fl_pipe", "length": 10.0, "diameter": 0.03, "friction": "turbulent"}  # not covered
    assert cross_checked(st)


# ---- FL7: check valves, tank port losses, multi-port outputs ------------------------------------------------------
def test_reg_square2_and_reg_step():
    for k1, k2 in ((1.0, 1.0), (0.04, 1.5), (2.0, 0.5)):
        assert F.reg_square2(2.0, 1.0, k1, k2) == pytest.approx(4 * k1)
        assert F.reg_square2(-2.0, 1.0, k1, k2) == pytest.approx(-4 * k2)
        for x in (1.0, -1.0):
            assert F.reg_square2(x - 1e-9, 1.0, k1, k2) == pytest.approx(F.reg_square2(x + 1e-9, 1.0, k1, k2), rel=1e-6)
        ys = [F.reg_square2(x, 1.0, k1, k2) for x in np.linspace(-1.5, 1.5, 301)]
        assert np.all(np.diff(ys) > 0)
    assert F.reg_step(1.0, 3.0, 5.0, 0.1) == 3.0 and F.reg_step(-1.0, 3.0, 5.0, 0.1) == 5.0
    assert F.reg_step(0.0, 3.0, 5.0, 0.1) == 4.0


def test_check_valve_relation_is_zero_backward():
    assert F.reg_root2(-0.5, 1.0, 1.0, 0.0, True, 0.0) == 0.0
    assert F.reg_root2(-2.0, 1.0, 1.0, 0.0, True, 0.0) == 0.0
    assert F.reg_root2(4.0, 1.0, 1.0, 0.0, True, 0.0) == pytest.approx(2.0)
    assert 0 < F.reg_root2(0.5, 1.0, 1.0, 0.0, True, 0.0) < math.sqrt(0.5)


def test_pump_check_valve_needs_a_volume_and_gets_dynamic_energy():
    st = {"components": {"src": {"kind": "fl_boundary", "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.01, 0]], "N_nominal": 150, "checkValve": True},
                         "out": {"kind": "fl_boundary"}},
          "connect": [["src.port", "pump.port_a"], ["pump.port_b", "out.port"]], "outputs": {"m": "pump.m_flow"},
          "analysis": {"kind": "transient", "stop_time": 1}}
    assert "needs a volume V > 0" in " ".join(validate_study(st)["errors"])
    st["components"]["pump"]["V"] = 0.001
    assert validate_study(st)["ok"]
    line = next(x for x in translate(st).text.splitlines() if "PrescribedPump" in x)
    assert "energyDynamics=system.energyDynamics" in line and "m_flow_start=" in line
    assert getattr(translate(st).ref, "fluid", False)
    from openmodelica_translator.runner import fluid_mass_terms
    assert "pump.m" in fluid_mass_terms(translate(st))[1]


def test_multi_port_outputs():
    st = copy.deepcopy(DRAIN)
    st["components"]["out2"] = {"kind": "fl_boundary"}
    st["components"]["o2"] = {"kind": "fl_orifice", "diameter": 0.02, "zeta": 1.0}
    st["connect"] += [["tank.port", "o2.port_a"], ["o2.port_b", "out2.port"]]
    st["outputs"] = {"total": "tank.port.m_flow", "first": "tank.port[1].m_flow", "p_out": "out.port.p"}
    tr = translate(st)
    assert tr.outputs["total"][0] == [(1.0, "tank.ports[1].m_flow"), (1.0, "tank.ports[2].m_flow")]
    assert tr.outputs["first"][0] == [(1.0, "tank.ports[1].m_flow")]
    assert tr.outputs["p_out"][0] == [(1.0, "out.ports[1].p")]
    st["outputs"] = {"p": "tank.port.p"}
    assert "say which" in " ".join(validate_study(st)["errors"])
    st["outputs"] = {"p": "tank.port[3].p"}
    assert "2 connection(s)" in " ".join(validate_study(st)["errors"])


# ---- FL9: temperatures ------------------------------------------------------------------------------------------
def test_heated_tank_and_capacitor_through_a_conductor():
    """Closed tank (no flow) heated at Q and tied to a capacitor: total heat conserved, T exact for the tank alone."""
    st = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": 0.5, "level_start": 1.0,
                                  "T_start": 290.0, "medium": LIQ},
                         "src": {"kind": "fl_mass_flow", "m_flow": 0.0},
                         "heater": {"kind": "fixed_heat_flow", "Q_flow": 5000.0},
                         "g": {"kind": "thermal_conductor", "G": 100.0},
                         "cap": {"kind": "heat_capacitor", "C": 2e5, "T0": 350.0}},
          "connect": [["src.port", "tank.port"], ["heater.port", "tank.heatPort"], ["tank.heatPort", "g.port_a"],
                      ["g.port_b", "cap.port"]],
          "outputs": {"T": "tank.T"}, "analysis": {"kind": "transient", "stop_time": 600}}
    assert validate_study(st)["ok"], validate_study(st)["errors"]
    r = F.FluidRef(build(st))
    t = np.linspace(0, 600, 61)
    v = r.simulate(t)
    mc = 1000 * 0.5 * 1.0 * 4000                       # the tank's heat capacity m cv
    E = mc * (v["tank.medium.T"] - 290) + 2e5 * (v["cap.T"] - 350)
    assert np.allclose(E, 5000 * t, rtol=1e-8, atol=1e-3)
    assert np.allclose(v["g.Q_flow"], 100 * (v["tank.medium.T"] - v["cap.T"]))
    assert np.allclose(v["tank.heatPort.Q_flow"], 5000 - v["g.Q_flow"])


def test_mixing_at_a_junction_and_sensor():
    """Two boundaries (300 K, 340 K) through equal pipes into one node, then on to a third: the sensor after the
    node reads the mass-weighted mix (ideal mixing of stream connectors)."""
    st = {"components": {"a": {"kind": "fl_boundary", "p": 2e5, "T": 300.0, "medium": LIQ},
                         "b": {"kind": "fl_boundary", "p": 1.6e5, "T": 340.0},
                         "pa": {"kind": "fl_pipe", "length": 10, "diameter": 0.02},
                         "pb": {"kind": "fl_pipe", "length": 10, "diameter": 0.02},
                         "Ts": {"kind": "fl_temperature"}, "pc": {"kind": "fl_pipe", "length": 10, "diameter": 0.03},
                         "c": {"kind": "fl_boundary", "p": 1e5}},
          "connect": [["a.port", "pa.port_a"], ["b.port", "pb.port_a"], ["pa.port_b", "Ts.port_a"],
                      ["pb.port_b", "Ts.port_a"], ["Ts.port_b", "pc.port_a"], ["pc.port_b", "c.port"]],
          "outputs": {"T": "Ts.T"}, "analysis": {"kind": "transient", "stop_time": 1}}
    assert validate_study(st)["ok"], validate_study(st)["errors"]
    v = F.FluidRef(build(st)).simulate(np.array([0.0]))
    ma, mb = v["pa.port_a.m_flow"][0], v["pb.port_a.m_flow"][0]
    assert ma > 0 and mb > 0
    assert v["Ts.T"][0] == pytest.approx((300 * ma + 340 * mb) / (ma + mb), rel=1e-12)


def test_pump_volume_heating_closed_form():
    from openmodelica_translator import bench
    st, checks = bench._fl_pump_heating()
    r = F.FluidRef(build(st))
    v = r.simulate(np.linspace(0, 20, 101))
    assert v["Ts.T"][-1] == pytest.approx(dict((c[0], c[1]) for c in checks)["outputs.T.final"], abs=1e-8)


def test_temperature_inputs_switch_temperatures_off():
    st = copy.deepcopy(DRAIN)
    st["components"]["Tin"] = {"kind": "constant", "k": 300.0}
    st["connect"].append(["Tin.y", "out.T_in"])
    v = validate_study(st)
    if not v["ok"]:
        pytest.skip("; ".join(v["errors"]))
    ok, _ = F.covered(build(st))
    if ok:
        r = F.FluidRef(build(st))
        assert not r.thermal and "T_in" in r.thermal_why


# ---- dynamic pipes and closed volumes (FL11) --------------------------------------------------------------------
def _heated_pipe(**pipe):
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": 0.2, "T": 300.0, "medium": LIQ},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 10.0, "diameter": 0.05, "nNodes": 5,
                                  "heat_transfer": "constant", "alpha": 500.0, **pipe},
                         "wall": {"kind": "fixed_temperature", "T": 350.0},
                         "Ts": {"kind": "fl_temperature"}, "out": {"kind": "fl_boundary", "T": 300.0}},
          "connect": [["src.port", "pipe.port_a"], ["pipe.port_b", "Ts.port_a"], ["Ts.port_b", "out.port"],
                      ["pipe.heat", "wall.port"]],
          "outputs": {"T": "Ts.T", "Q": "pipe.heat.Q_flow"},
          "analysis": {"kind": "transient", "stop_time": 1200, "intervals": 12}}
    return st


def test_dynamic_pipe_heated_volumes_in_series():
    st = _heated_pipe()
    assert validate_study(st)["ok"] and F.covered(build(st))[0]
    o = F.FluidRef(build(st)).simulate(np.linspace(0, 1200, 13))
    Ak, T = math.pi * 0.05 * 10.0 / 5, 300.0
    for _ in range(5):
        T = (0.2 * 4000 * T + 500 * Ak * 350) / (0.2 * 4000 + 500 * Ak)
    assert o["Ts.T"][-1] == pytest.approx(T, rel=1e-9)
    assert o["omct_pipe_coll.port_b.Q_flow"][-1] == pytest.approx(0.2 * 4000 * (T - 300), rel=1e-7)
    assert o["pipe.Us[1]"][0] == pytest.approx(1000 * math.pi * 0.05 ** 2 / 4 * 2 * 4000 * (293.15 - 273.15))


def test_dynamic_pipe_segments_by_model_structure():
    st = _heated_pipe(nNodes=4, height_ab=2.0)
    r = F.FluidRef(build(st))
    seg = [f for f in r.flows if f["k"] == "dp_seg"]
    assert [f["L"] for f in seg] == pytest.approx([3.75, 2.5, 3.75])       # av_vb: {1.5, 1, 1.5} L/n
    assert [f["dh"] for f in seg] == pytest.approx([0.75, 0.5, 0.75])
    assert r.dpipes[0]["vn"][0] == r.dpipes[0]["na"] and r.dpipes[0]["vn"][-1] == r.dpipes[0]["nb"]
    net = build(st)
    net.elems["pipe"]["params"]["_structure"] = "av_b"                      # after a pump on a loop
    r = F.FluidRef(net)
    seg = [f for f in r.flows if f["k"] == "dp_seg"]
    assert [f["L"] for f in seg] == pytest.approx([2.5] * 4) and seg[-1]["b"] == r.dpipes[0]["nb"]


def test_dynamic_pipe_work_is_the_static_head():
    """Steady momentum: DynamicPipe's Wb_flows reduce to -m g dh per volume (carbonio run_fl11_probe2 agreed)."""
    st = {"components": {"a": {"kind": "fl_boundary", "p": 3e5, "T": 290.0, "medium": LIQ},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 20.0, "diameter": 0.03, "nNodes": 4,
                                  "height_ab": 5.0, "nParallel": 2, "T_start": 330.0},
                         "b": {"kind": "fl_boundary", "p": 1.5e5}},
          "connect": [["a.port", "pipe.port_a"], ["pipe.port_b", "b.port"]], "outputs": {"m": "pipe.m_flow"},
          "analysis": {"kind": "transient", "stop_time": 3}}
    o = F.FluidRef(build(st)).simulate(np.array([0.0, 3.0]))
    m = o["pipe.port_a.m_flow"][-1]
    assert m == pytest.approx(5.327575765748562, rel=1e-12)                # OpenModelica, run_fl11_probe1
    for i in range(1, 5):
        assert o[f"pipe.Wb_flows[{i}]"][-1] == pytest.approx(-m * 9.80665 * 1.25, rel=1e-9)
    assert o["pipe.mediums[2].p"][-1] < o["pipe.mediums[1].p"][-1]


@pytest.mark.parametrize("name,expect", [
    ("p3", {"pump.port_a.m_flow": 3.8420157125666847, "tank.medium.T": 314.543370595903,
            "omct_pipe_coll.port_b.Q_flow": 209040.6105346213}),
    ("p5", {"pipe.port_a.m_flow": -0.25874396124324234, "omct_pipe_coll.port_b.Q_flow": 29153.604365297506}),
])
def test_local_heat_transfer_matches_openmodelica(name, expect):
    """carbonio run_fl11_probe1 (tolerance 1e-8): av_b after a pump on a loop (p3), reversed flow (p5)."""
    if name == "p3":
        st = {"components": {
            "tank": {"kind": "fl_tank", "height": 5, "crossArea": 1.0, "level_start": 1, "T_start": 293.15,
                     "medium": LIQ},
            "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1450 rpm"},
            "pipe": {"kind": "fl_dynamic_pipe", "length": 10.0, "diameter": 0.04, "nNodes": 3, "height_ab": 1.0,
                     "heat_transfer": "local"},
            "v": {"kind": "fl_valve", "Kv": 20, "opening": 0.6, "dp_nominal": 1e5, "m_flow_nominal": 5},
            "wall": {"kind": "fixed_temperature", "T": 340.0}},
            "connect": [["tank.port", "pump.port_a"], ["pump.port_b", "pipe.port_a"], ["pipe.port_b", "v.port_a"],
                        ["v.port_b", "tank.port"], ["pipe.heat", "wall.port"]],
            "outputs": {"m": "pump.m_flow"}, "analysis": {"kind": "transient", "stop_time": 300}}
        t1 = 300.0
    else:
        st = {"components": {
            "a": {"kind": "fl_boundary", "p": 1.0e5, "T": 290.0, "medium": LIQ},
            "pipe": {"kind": "fl_dynamic_pipe", "length": 4.0, "diameter": 0.02, "nNodes": 3,
                     "heat_transfer": "local", "T_start": 300.0},
            "b": {"kind": "fl_boundary", "p": 1.02e5, "T": 310.0}, "w": {"kind": "fixed_temperature", "T": 360.0}},
            "connect": [["a.port", "pipe.port_a"], ["pipe.port_b", "b.port"], ["pipe.heat", "w.port"]],
            "outputs": {"m": "pipe.m_flow"}, "analysis": {"kind": "transient", "stop_time": 30}}
        t1 = 30.0
    net = build(st)
    if name == "p3":
        assert net.elems["pipe"]["params"]["_structure"] == "av_b"
    o = F.FluidRef(net).simulate(np.linspace(0, t1, 11))
    for path, v in expect.items():
        assert o[path][-1] == pytest.approx(v, rel=1e-7)


def test_closed_volume_ports_and_heat_port():
    """carbonio run_fl11_probe2: a two-port volume fed by a source, a heat port through a conductor."""
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": 0.05, "T": 300.0, "medium": LIQ},
                         "vol": {"kind": "fl_volume", "V": 0.01, "T_start": 330.0},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 5.0, "diameter": 0.02, "nNodes": 2,
                                  "friction": "nominal_laminar", "dp_nominal": 2e4, "m_flow_nominal": 0.1,
                                  "T_start": 305.0},
                         "out": {"kind": "fl_boundary", "T": 280.0},
                         "g": {"kind": "thermal_conductor", "G": 50.0}, "amb": {"kind": "fixed_temperature", "T": 350.0}},
          "connect": [["src.port", "vol.port"], ["vol.port", "pipe.port_a"], ["pipe.port_b", "out.port"],
                      ["vol.heatPort", "g.port_a"], ["g.port_b", "amb.port"]],
          "outputs": {"T": "vol.T"}, "analysis": {"kind": "transient", "stop_time": 50}}
    assert validate_study(st)["ok"]
    o = F.FluidRef(build(st)).simulate(np.linspace(0, 50, 6))
    assert o["vol.medium.T"][-1] == pytest.approx(324.6323125789169, rel=1e-9)
    assert o["pipe.mediums[2].T"][-1] == pytest.approx(323.17840504955774, rel=1e-8)
    assert o["vol.medium.p"][-1] == pytest.approx(101325 + 1e4)
    assert o["vol.ports[1].m_flow"][-1] == pytest.approx(0.05) and o["vol.ports[2].m_flow"][-1] == pytest.approx(-0.05)


def test_dynamic_pipe_limits():
    st = _heated_pipe(nNodes=1)
    assert any("nNodes: at least 2" in e for e in validate_study(st)["errors"])
    st = _heated_pipe(heat_transfer="ideal")          # volumes at the wall's T: hydraulics only, cross-solver kept
    r = F.FluidRef(build(st))
    assert not r.thermal and "ideal heat transfer" in r.thermal_why
    from openmodelica_translator.format import cross_checked
    assert cross_checked(st) and not cross_checked(_heated_pipe())
    st = _heated_pipe()
    st["components"]["system"] = {"kind": "fl_system", "dynamics": "steady_state_initial"}
    assert "fixed_initial" in F.FluidRef(build(st)).thermal_why


# ---- the energy balance of a reference run (FL12) ---------------------------------------------------------------
@pytest.mark.parametrize("name", ["fl_heated_loop", "fl_pipe_loop_reference", "fl_volume_reference", "fl_pump_heating",
                                  "fl_thermal_reference", "fl_tank_port_drain"])
def test_reference_runs_check_their_energy_balance(name, tmp_path):
    from openmodelica_translator import bench
    from openmodelica_translator.runner import run_study
    out = run_study(name, bench.cases()[name]["study"], tmp_path, solver="ref")
    fe = out["checks"]["fluid_energy"]
    assert out["ok"] and fe["covered"] and fe["ok"], fe


def test_reference_energy_check_catches_a_wrong_term(tmp_path, monkeypatch):
    """A pump's work left out of its ports' h_outflow: the stored energy no longer matches what flows in."""
    from openmodelica_translator import bench
    from openmodelica_translator.runner import run_study
    real = F.FluidRef._h_outflow

    def wrong(self, dis, hmix, m, W, ht, hp, hx):
        out, ins = real(self, dis, hmix, m, W, ht, hp, hx)
        return {k: (v + 2000.0 if k.endswith("ports[1].h_outflow") and k.startswith("src") else v)
                for k, v in out.items()}, ins
    monkeypatch.setattr(F.FluidRef, "_h_outflow", wrong)
    out = run_study("x", bench.cases()["fl_pump_into_tank"]["study"], tmp_path, solver="ref")
    assert out["checks"]["fluid_energy"]["covered"] and not out["checks"]["fluid_energy"]["ok"]


def test_h_outflow_follows_msl():
    """Static parts pass inStream through, storages carry their medium.h, a pump without a volume adds W/m."""
    st = {"components": {"a": {"kind": "fl_boundary", "p": 2e5, "T": 300.0, "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.01, 0]], "N_nominal": 150.0, "eta": 0.5},
                         "v": {"kind": "fl_valve", "Kv": 10, "dp_nominal": 1e5, "m_flow_nominal": 2},
                         "b": {"kind": "fl_boundary", "p": 2e5, "T": 350.0}},
          "connect": [["a.port", "pump.port_a"], ["pump.port_b", "v.port_a"], ["v.port_b", "b.port"]],
          "outputs": {"m": "pump.m_flow"}, "analysis": {"kind": "transient", "stop_time": 1}}
    o = F.FluidRef(build(st)).simulate(np.array([0.0, 1.0]))
    m, W = o["pump.m_flow"][-1], o["pump.W_total"][-1]
    h_a = 4000 * (300 - 273.15)
    assert m > 0 and o["a.ports[1].h_outflow"][-1] == pytest.approx(h_a)
    assert o["pump.port_b.h_outflow"][-1] == pytest.approx(h_a + W / m)
    assert o["v.port_b.h_outflow"][-1] == pytest.approx(h_a + W / m)            # inStream(port_a)
    assert o["v.port_a.h_outflow"][-1] == pytest.approx(4000 * (350 - 273.15))  # inStream(port_b): b's h
    assert o["pump.Wb_flow"][-1] == pytest.approx(W) and o["pump.U"][-1] == 0.0


# ---- T-dependent liquids (FL14) ---------------------------------------------------------------------------------
def test_table_based_media_follow_msl():
    """Modelica.Media.Incompressible.TableBased: quadratic fits, h from 0 degC, u = h - reference_p/d."""
    g = F.medium_of({"medium": "glycol47"})
    assert g.var and g.h(273.15) == 0.0
    assert g.d(293.15) == pytest.approx(1044.0, rel=2e-3) and g.cp(293.15) == pytest.approx(3620.0, rel=2e-3)
    for T in (250.0, 300.0, 360.0):
        assert g.T_h(g.h(T)) == pytest.approx(T, abs=1e-9)
        assert g.u(T) == pytest.approx(g.h(T) - 1.013e5 / g.d(T))
        e = 1e-4
        assert g.du(T) == pytest.approx((g.u(T + e) - g.u(T - e)) / (2 * e), rel=1e-6)
    eso = F.medium_of({"medium": "essotherm650"})
    assert eso.mu(373.15) == pytest.approx(26.64, rel=0.15)        # a quadratic in 1/T through 4 decades
    U = 0.01 * g.d(330.0) * g.u(330.0)
    assert F.T_of_U(g, U, V=0.01) == pytest.approx(330.0, abs=1e-9)
    with pytest.raises(F.FluidRefError):
        F.medium_of({"medium": "unknown_gas"})


FL14 = json.loads((Path(__file__).parent / "fixtures" / "fl14_tablebased.json").read_text())


@pytest.mark.parametrize("name", sorted(FL14))
def test_table_based_liquids_match_openmodelica(name):
    """carbonio run_fl14_probe2 (tolerance 1e-8): glycol47 tank, volume, pump volume, reversed flow; essotherm650
    static chain with a rise and a locally heated dynamic pipe."""
    c = FL14[name]
    st = c["study"]
    net = build(st)
    an = st["analysis"]
    o = F.FluidRef(net).simulate(np.linspace(0, an["stop_time"], an.get("intervals", 10) + 1))
    for out, v in c["om"].items():
        terms, _ = net.var_expr(st["outputs"][out])
        y = sum(cf * o[p] for cf, p in terms)
        assert y[-1] == pytest.approx(v, rel=2e-7), out


def test_static_pipe_rise_lowers_the_enthalpy():
    """StaticPipe: port_b.h_outflow = inStream(port_a.h_outflow) - g height_ab (water: 9.8/4184 K per metre)."""
    st = {"components": {"a": {"kind": "fl_boundary", "p": 2e5, "T": 300.0, "medium": LIQ},
                         "pipe": {"kind": "fl_pipe", "length": 5, "diameter": 0.02, "height_ab": 3.0},
                         "Ts": {"kind": "fl_temperature"}, "b": {"kind": "fl_boundary"}},
          "connect": [["a.port", "pipe.port_a"], ["pipe.port_b", "Ts.port_a"], ["Ts.port_b", "b.port"]],
          "outputs": {"T": "Ts.T"}, "analysis": {"kind": "transient", "stop_time": 1}}
    o = F.FluidRef(build(st)).simulate(np.array([0.0, 1.0]))
    assert o["Ts.T"][-1] == pytest.approx(300.0 - 9.80665 * 3.0 / 4000.0, abs=1e-12)


def test_mass_check_allows_the_integrators_error_on_stored_mass():
    """A closed loop has no throughput: 10 tol of the stored mass is the allowance (FL14)."""
    from openmodelica_translator.runner import _fluid_mass
    from openmodelica_translator.modelica import translate
    st = {"components": {"tank": {"kind": "fl_tank", "height": 5, "crossArea": 1.0, "level_start": 1, "medium": LIQ},
                         "pump": {"kind": "fl_pump", "curve": [[0, 20], [0.01, 0]], "N_nominal": "1450 rpm"},
                         "v": {"kind": "fl_valve", "Kv": 20, "dp_nominal": 1e5, "m_flow_nominal": 5}},
          "connect": [["tank.port", "pump.port_a"], ["pump.port_b", "v.port_a"], ["v.port_b", "tank.port"]],
          "outputs": {"m": "pump.m_flow"}, "analysis": {"kind": "transient", "stop_time": 10}}
    tr = translate(st)

    class R:
        time = np.linspace(0, 10, 11)

        def __init__(self, drift):
            self.drift = drift

        def series(self, p):
            return 1000.0 + self.drift * self.time / 10 if p == "tank.m" else np.zeros(11)
    out = {"checks": {}}
    _fluid_mass(out, tr, R(3.5e-4), 1e-6)            # 3.5e-7 of the stored mass: the integrator's
    assert out["checks"]["mass"]["ok"]
    _fluid_mass(out, tr, R(0.1), 1e-6)               # 1e-4 of it: not
    assert not out["checks"]["mass"]["ok"]


# ---- gases (FL15) -----------------------------------------------------------------------------------------------
def test_ideal_gas_media_follow_msl():
    a, sa = F.medium_of({"medium": "air"}), F.medium_of({"medium": "simple_air"})
    assert a.gas and a.d(300.0, 1e5) == pytest.approx(1e5 / (287.0512249529787 * 300.0))
    assert a.cp(300.0) == pytest.approx(1004.8, abs=0.5) and a.mu(300.0) == pytest.approx(1.857e-5, rel=1e-3)
    for T in (250.0, 300.0, 900.0, 1100.0):           # both NASA ranges
        assert a.T_h(a.h(T)) == pytest.approx(T, abs=1e-9)
        assert (a.h(T + 1e-3) - a.h(T - 1e-3)) / 2e-3 == pytest.approx(a.cp(T), rel=1e-7)
        assert a.u(T) == pytest.approx(a.h(T) - a.R * T)
    assert sa.h(298.15) == 0.0 and sa.R == pytest.approx(8.31446261815324 / 0.0289651159)


FL15 = json.loads((Path(__file__).parent / "fixtures" / "fl15_gases.json").read_text())


@pytest.mark.parametrize("name", sorted(FL15))
def test_gases_match_openmodelica(name):
    """carbonio run_fl15_probe1/2 (tolerance 1e-8): simple_air through a static pipe into a volume, air through a
    heated dynamic pipe between boundaries, a heated volume fed by a source, reversed flow through a rising static
    pipe, orifice, resistance and sensors, a choked OpPoint valve and a check valve."""
    c = FL15[name]
    st, an = c["study"], c["study"]["analysis"]
    net = build(st)
    o = F.FluidRef(net).simulate(np.linspace(0, an["stop_time"], an.get("intervals", 10) + 1))
    for out, v in c["om"].items():
        terms, _ = net.var_expr(st["outputs"][out])
        y = sum(cf * o[p] for cf, p in terms)
        assert y[-1] == pytest.approx(v, rel=3e-7), out


def test_gas_volumes_on_one_point_are_refused():
    st = {"components": {"src": {"kind": "fl_mass_flow", "m_flow": 0.05, "T": 300.0, "medium": "air"},
                         "v1": {"kind": "fl_volume", "V": 0.05},
                         "pipe": {"kind": "fl_dynamic_pipe", "length": 5, "diameter": 0.04, "nNodes": 2},
                         "b": {"kind": "fl_boundary"}},
          "connect": [["src.port", "v1.port"], ["v1.port", "pipe.port_a"], ["pipe.port_b", "b.port"]],
          "outputs": {"m": "pipe.m_flow"}, "analysis": {"kind": "transient", "stop_time": 1}}
    with pytest.raises(F.FluidRefError, match="two gas volumes"):
        F.FluidRef(build(st))
