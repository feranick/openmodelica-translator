"""test_fluid.py — coolant loops (Thermal.FluidHeatFlow): validation, the generated Modelica, the reference's
hydraulics and temperatures, the energy balance.
Version: 2026.10.21.14
"""
import copy

import numpy as np
import pytest

from openmodelica_translator import bench, energy, modelica as M, network, ref
from openmodelica_translator.format import validate_study


def loop():
    st, _ = bench._coolant_loop()
    return copy.deepcopy(st)


def errs(st):
    return " | ".join(validate_study(st)["errors"])


def test_validation_rules():
    st = loop()
    assert validate_study(st)["ok"], errs(st)
    s = loop()
    del s["components"]["pref"]
    s["connect"] = [c for c in s["connect"] if "pref" not in c[0] and "pref" not in c[1]]
    assert "no pressure reference" in errs(s)
    s = loop()
    s["components"]["cooler"]["medium"] = "glycol50_20degC"
    assert "mixes media" in errs(s)
    s = loop()
    s["connect"] = [c for c in s["connect"] if c != ["cooler.flowPort_b", "pump.flowPort_a"]]
    assert "cooler.flowPort_b is not connected" in errs(s)
    s = loop()
    s["components"]["heater"]["dpLaminar"] = 1000
    assert "dpNominal (2000 Pa) must be >=" in errs(s)
    s = loop()
    s["components"]["heater"]["medium"] = "honey"
    assert "unknown medium 'honey'" in errs(s)
    s = loop()
    s["components"]["heater"]["medium"] = {"rho": 1000, "cp": 4000, "nu": 1e-6}
    s["components"]["cooler"]["medium"] = {"rho": 1000, "cp": 4000, "nu": 1e-6}
    for c in ("pref", "pump", "tHot"):
        s["components"][c]["medium"] = {"rho": 1000, "cp": 4000, "nu": 1e-6}
    assert validate_study(s)["ok"], errs(s)


def test_modelica_text():
    tr = M.translate(loop(), "loop")
    t = tr.text
    assert "parameter Modelica.Thermal.FluidHeatFlow.Media.Medium omct_medium_1 = Modelica.Thermal.FluidHeatFlow.Media.Water()" in t
    assert "Modelica.Thermal.FluidHeatFlow.Components.Pipe heater(" in t and "useHeatPort=true" in t
    assert "medium=omct_medium_1" in t and "connect(heater.heatPort, q.port);" in t
    assert "heater.T" in tr.fixed[0] or any("heater.T0" in f for f in tr.fixed)
    assert "heater.V_flowNominal" in tr.overrides and "heater.V_flowLaminar" in tr.overrides
    assert "omct_medium" not in "".join(tr.overrides)
    mp = pytest.importorskip("modparc")
    mp.parse(t)


def test_heat_port_only_when_connected():
    st = loop()
    st["connect"] = [c for c in st["connect"] if "g." not in c[0] + c[1] and "amb." not in c[0] + c[1]]
    del st["components"]["g"], st["components"]["amb"]
    st["components"]["q"]["Q_flow"] = 0.0
    tr = M.translate(st, "x")
    decl = [line for line in tr.text.splitlines() if " cooler(" in line][0]
    assert "useHeatPort" not in decl


def test_hydraulics_quadratic_and_valve():
    st, checks = bench._coolant_hydraulics()
    h = ref.hydraulics(network.build(st))
    assert h["exact"]
    V = checks[0][1]
    assert h["V"]["pipe"] == pytest.approx(V, rel=1e-12) and h["V"]["v"] == pytest.approx(V, rel=1e-12)
    assert h["dp"]["src"] == pytest.approx(-5000, rel=1e-12)


def test_zero_flow_is_a_stand_in():
    st = loop()
    st["components"]["pump"]["constantVolumeFlow"] = 0.0
    v = validate_study(st)
    assert v["ok"] and any("no flow" in w for w in v["warnings"])
    r = ref.Ref(network.build(st))
    assert not r.exact


def test_energy_balance_of_the_open_loop_with_the_reference():
    st, _ = bench._coolant_open()
    net = network.build(st)
    r = ref.Ref(net)
    pl = energy.plan(net)
    assert pl["covered"]
    t = np.linspace(0, 40, 4001)
    vals = r.simulate(t)
    b = energy.balance(pl, t, lambda p: vals[p])
    assert b["ok"] and b["error_rel"] < 1e-5, b
