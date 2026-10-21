"""test_machine.py — the MSL DC machine (DC_PermanentMagnet): parameters, heat ports (none, one group, six),
the generated Modelica, the reference.
Version: 2026.10.21.15
"""
import copy

import pytest

from openmodelica_translator import bench, modelica as M, network, ref
from openmodelica_translator.format import validate_study


def machine(thermal=None):
    st, _ = bench._dcpm(False)
    st = copy.deepcopy(st)
    if thermal:
        st["components"].update({"cap": {"kind": "heat_capacitor", "C": 2000},
                                 "gth": {"kind": "thermal_conductor", "G": 10},
                                 "amb": {"kind": "fixed_temperature", "T": 293.15}})
        st["connect"] += [["cap.port", "gth.port_a"], ["gth.port_b", "amb.port"]]
        if thermal == "one":
            st["connect"].append(["M.heatPort", "cap.port"])
        else:
            st["connect"] += [[f"M.{h}", "cap.port"] for h in thermal]
    return st


def test_no_heat_ports():
    st = machine()
    assert validate_study(st)["ok"]
    tr = M.translate(st, "m")
    decl = [x for x in tr.text.splitlines() if " M(" in x][0]
    assert "useThermalPort" not in decl and "frictionParameters(PRef=0.0, power_w=2.0)" in decl
    assert "M.frictionParameters.PRef" not in tr.overrides and "M.VaNominal" in tr.overrides   # loss records: structural
    assert ref.Ref(network.build(st)).exact
    st["components"]["M"]["PRef_friction"] = 100.0
    t2 = M.translate(st, "m")
    assert t2.structure != tr.structure and "frictionParameters(PRef=100.0, power_w=2.0, wRef=" in t2.text


def test_one_heat_port_takes_every_loss():
    st = machine("one")
    v = validate_study(st)
    assert v["ok"], v["errors"]
    tr = M.translate(st, "m")
    assert "useThermalPort=true" in tr.text
    for h in ("Armature", "Core", "StrayLoad", "Friction", "Brush", "PermanentMagnet"):
        assert f"connect(M.thermalPort.heatPort{h}, cap.port);" in tr.text
    assert ref.Ref(network.build(st)).exact              # one way: the losses heat the network, nothing comes back
    st["components"]["M"]["alpha20a"] = 3.9e-3
    assert not ref.Ref(network.build(st)).exact          # two way: Ra follows the armature temperature


def test_some_heat_ports_only_is_an_error():
    st = machine(["heatPortArmature", "heatPortCore"])
    e = " | ".join(validate_study(st)["errors"])
    assert "are not connected: connect M.heatPort (every loss into one node) or all of" in e


def test_brush_and_vi_nominal_checks():
    st = machine()
    st["components"]["M"]["Ra"] = 1.0
    assert "VaNominal must exceed" in " | ".join(validate_study(st)["errors"])
    st = machine()
    st["components"]["M"].update({"V_brush": 2.0})
    v = validate_study(st)
    assert v["ok"] and not v["warnings"]                # the brush drop is piecewise linear: events (V2.5)
    assert ref.Ref(network.build(st)).events == ["M"]


def test_variables_and_outputs():
    st = machine()
    st["outputs"].update({"Pm": "M.powerMechanical", "Ta": "M.Ta", "va": "M.va"})
    tr = M.translate(st, "m")
    assert tr.outputs["Pm"][0] == [(1.0, "M.powerBalance.powerMechanical")]
    assert tr.outputs["Ta"][0] == [(1.0, "M.ra.T_heatPort")]
    mp = pytest.importorskip("modparc")
    mp.parse(tr.text)


def test_dc_motor_heat_port_group():
    from studies import DC_PI, study
    st = study(DC_PI)
    motor = next(n for n, c in st["components"].items() if c["kind"] == "dc_motor")
    st["components"].update({"cap": {"kind": "heat_capacitor", "C": 500}, "gth": {"kind": "thermal_conductor", "G": 2},
                             "amb": {"kind": "fixed_temperature", "T": 293.15}})
    st["connect"] += [[f"{motor}.heatPort", "cap.port"], ["cap.port", "gth.port_a"], ["gth.port_b", "amb.port"]]
    v = validate_study(st)
    assert v["ok"], v["errors"]
    t = M.translate(st, "x").text
    assert t.count("useHeatPort=true") == 2
    assert f"connect({motor}__Ra.heatPort, cap.port);" in t and f"connect({motor}__fric.heatPort, cap.port);" in t
