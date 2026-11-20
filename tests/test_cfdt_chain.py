"""test_cfdt_chain.py — values from cfd-translator results for the Modelica.Fluid kinds (V5 FL5).
Version: 2026.11.19.14
"""
import copy
import json
import math
from pathlib import Path

import pytest

from openmodelica_translator.bench import _cfdt_pump_result as pump_res
from openmodelica_translator.chains import ChainError, cfdt_pump, cfdt_resistance, quad_points
from openmodelica_translator.format import validate_study
from openmodelica_translator.modelica import translate
from openmodelica_translator.network import build

LIQ = {"rho": 1000.0, "cp": 4000.0, "eta": 1e-3}
PIPE = json.loads((Path(__file__).parent / "fixtures" / "cfdt_pipe_chain1.json").read_text())


def res_dp(V, dp, rho=1000.0, **kw):
    return {"name": f"V{V}", "reliable": True, **kw,
            "results": {"total_pressure_drop": dp, "flow": {"in": {"Q": V, "mdot": rho * V}, "out": {"Q": -V, "mdot": -rho * V}}}}


def pump_study(**pump):
    return {"components": {"src": {"kind": "fl_boundary", "medium": LIQ}, "pump": {"kind": "fl_pump", **pump},
                           "r": {"kind": "fl_resistance", "a": 3e9}, "out": {"kind": "fl_boundary"}},
            "connect": [["src.port", "pump.port_a"], ["pump.port_b", "r.port_a"], ["r.port_b", "out.port"]],
            "outputs": {"V": "pump.V_flow", "W": "pump.W"}, "analysis": {"kind": "transient", "stop_time": 1}}


def test_quad_points_reproduce_the_least_squares_quadratic():
    pts = [[0.0, 1.0], [1.0, 2.1], [2.0, 4.9], [3.0, 10.2]]
    out, fit = quad_points(pts)
    assert [p[0] for p in out] == [0.0, 1.5, 3.0] and fit["degree"] == 2 and fit["rms_residual_rel"] < 0.02
    line, fit = quad_points([[0, 1], [2, 5]], line_as_two=True)
    assert line == [[0.0, pytest.approx(1.0)], [2.0, pytest.approx(5.0)]] and fit["degree"] == 1
    with pytest.raises(ChainError):
        quad_points([[1, 1], [1, 2]])


def test_pump_points_at_other_speeds_follow_the_affinity_laws():
    pts = [pump_res(0.002, 1450, "a"), pump_res(0.005, 1450, "b"), pump_res(0.006, 1200, "c"), pump_res(0.008, 1450, "d")]
    r = cfdt_pump(pts, "p")
    assert r["N_nominal"] == pytest.approx(1450 * 2 * math.pi / 60)
    for V, H in r["curve"]:                              # H = 20 - 2e5 V^2 exactly
        assert H == pytest.approx(20 - 2e5 * V * V, rel=1e-9)
    for V, W in r["power"]:
        assert W == pytest.approx(500 + 1e5 * V, rel=1e-9)
    assert r["rho_power"] == 1000.0 and r["record"]["head_fit"]["rms_residual_rel"] < 1e-12
    r2 = cfdt_pump(pts, "p", N_nominal=1200 * 2 * math.pi / 60)     # a given N_nominal: the curve moves there
    s = 1200 / 1450
    assert r2["curve"][0][1] == pytest.approx(s * s * (20 - 2e5 * (r2["curve"][0][0] / s) ** 2), rel=1e-9)


@pytest.mark.parametrize("change,msg", [
    (lambda p: p[0].update(reliable=False, unreliable_because=["mesh"]), "unreliable"),
    (lambda p: p[0]["results"].pop("pump"), "no pump.head_m"),
    (lambda p: p[0]["results"]["flow"]["inlet"].update(mdot=1.2 * p[0]["results"]["flow"]["inlet"]["mdot"]),
     "different densities"),
    (lambda p: p[1]["results"]["pump"].update(head_m=30.0), "does not fall"),
])
def test_pump_refuses(change, msg):
    pts = [pump_res(0.002, 1450, "a"), pump_res(0.005, 1450, "b"), pump_res(0.008, 1450, "d")]
    change(pts)
    with pytest.raises(ChainError, match=msg):
        cfdt_pump(pts, "p")


def test_pump_study_from_cfdt_power_curve_and_start_flow():
    pts = [pump_res(0.002, 1450, "a"), pump_res(0.005, 1450, "b"), pump_res(0.008, 1450, "d")]
    st = pump_study(cfdt=pts)
    v = validate_study(st)
    assert v["ok"], v["errors"]
    tr = translate(st)
    line = next(x for x in tr.text.splitlines() if "PrescribedPump" in x)
    assert "use_powerCharacteristic=true" in line and "quadraticPower" in line and "rho_nominal=1000.0" in line
    assert "m_flow_start=5.0" in line                    # MSL's homotopy divides by it (carbonio probe)
    rec = build(st).inputs["pump.cfdt"]
    assert rec["from"] == "cfdt" and rec["parameters"] == ["N_nominal", "curve", "power", "rho_power"]
    st = pump_study(cfdt=pts, eta=0.7)                   # a given efficiency wins over the power curve
    line = next(x for x in translate(st).text.splitlines() if "PrescribedPump" in x)
    assert "use_powerCharacteristic" not in line and "constantEfficiency" in line
    st = pump_study(curve=[[0, 20], [0.01, 0]], N_nominal=150, eta=0.7, power=[[0, 100], [0.01, 900]])
    assert "eta (constant) or power" in " ".join(validate_study(st)["errors"])


def test_resistance_fit_and_scaling_to_the_medium():
    pts = [res_dp(V, 3e8 * V * V + 2e4 * V) for V in (0.001, 0.002, 0.004)]

    def st(cfdt, medium=None, **extra):
        s = {"components": {"a": {"kind": "fl_boundary", "p": 2e5}, "r": {"kind": "fl_resistance", "cfdt": cfdt, **extra},
                            "b": {"kind": "fl_boundary"}},
             "connect": [["a.port", "r.port_a"], ["r.port_b", "b.port"]], "outputs": {"m": "r.m_flow"},
             "analysis": {"kind": "transient", "stop_time": 1}}
        if medium:
            s["components"]["a"]["medium"] = medium
        return s
    r = cfdt_resistance(pts, "r")
    assert r["a"] == pytest.approx(3e8, rel=1e-9) and r["b"] == pytest.approx(2e4, rel=1e-7)
    n = build(st(pts))                                   # water: a x 995.586/1000, b kept (no cfdt viscosity)
    assert n.elems["r"]["params"]["a"] == pytest.approx(3e8 * 0.995586)
    assert n.elems["r"]["params"]["b"] == pytest.approx(2e4) and "b_not_scaled" in n.inputs["r.cfdt"]
    n = build(st({"results": pts, "mu": 0.01}))
    assert n.elems["r"]["params"]["b"] == pytest.approx(2e3)
    n = build(st(pts, medium=LIQ, a=5e8))                # a given a wins (and is not rescaled)
    assert n.elems["r"]["params"]["a"] == 5e8 and n.inputs["r.cfdt"]["parameters"] == ["b"]
    v = validate_study(st(pts, medium="glycol47"))
    assert v["ok"] and any("used as they are with glycol47" in w for w in v["warnings"])
    one = cfdt_resistance(pts[:1], "r")
    assert one["b"] == 0 and one["a"] == pytest.approx((3e8 * 1e-6 + 20) / 1e-6)
    with pytest.raises(ChainError, match="linear"):
        cfdt_resistance([res_dp(V, 2e4 * V) for V in (0.001, 0.002)], "r")


def test_real_cfdt_pipe_results_predict_a_held_out_point():
    """carbonio run_fl5_pipe_chain1: three of four OpenFOAM pipe runs predict the fourth within 0.1 %."""
    from openmodelica_translator.fluidref import FluidRef
    import numpy as np
    Q15, dp15 = PIPE["pipe_u15"]["results"]["flow"]["inlet"]["Q"], PIPE["pipe_u15"]["results"]["total_pressure_drop"]
    st = {"components": {"a": {"kind": "fl_boundary", "p": 101325 + dp15, "medium": {"rho": 1000, "cp": 4000, "eta": 0.01}},
                         "r": {"kind": "fl_resistance", "cfdt": {"results": [PIPE[k] for k in ("pipe_u05", "pipe_u10",
                                                                                               "pipe_u20")], "mu": 0.01}},
                         "b": {"kind": "fl_boundary"}},
          "connect": [["a.port", "r.port_a"], ["r.port_b", "b.port"]], "outputs": {"V": "r.V_flow"},
          "analysis": {"kind": "transient", "stop_time": 1}}
    assert validate_study(st)["ok"]
    V = FluidRef(build(st)).simulate(np.array([0.0]))["r.V_flow"][0]
    assert abs(V / Q15 - 1) < 1e-3


def test_cfdt_key_only_where_supported():
    st = pump_study(curve=[[0, 20], [0.01, 0]], N_nominal=150)
    st = copy.deepcopy(st)
    st["components"]["out"]["cfdt"] = []
    assert "unknown keys ['cfdt']" in " ".join(validate_study(st)["errors"])
    st = pump_study(cfdt="nope")
    assert "cfd-translator study results" in " ".join(validate_study(st)["errors"])
