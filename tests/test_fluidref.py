"""test_fluidref.py — the reference for Modelica.Fluid liquids (V5 FL4): MSL's functions, coverage, the network.
Version: 2026.11.19.19
"""
import copy
import math

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
    ({"tank": {"kind": "fl_tank", "height": 3, "crossArea": 0.5, "level_start": 2, "medium": "glycol47"}},
     "constant-density"),
    ({"out": {"kind": "fl_volume", "V": 1.0}}, "not in the Fluid reference"),
])
def test_not_covered_says_why(change, why):
    st = copy.deepcopy(DRAIN)
    st["components"].update(change)
    if "out" in change:
        st["components"]["out2"] = {"kind": "fl_boundary"}
        st["connect"].append(["out.port", "out2.port"])
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
    st["components"]["tank"]["medium"] = "glycol47"           # T-dependent: not covered
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
