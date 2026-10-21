"""test_modelica.py — the generated model, build script and override file.
Version: 2026.10.21.1
"""
import re

from studies import DC_PI, RC, RLC, THERMAL, TWO_MASS, study

from openmodelica_translator import modelica as M


def test_stable_text_and_values():
    a = M.translate(RLC, "rlc")
    b = M.translate(study(RLC), "rlc")
    assert a.text == b.text and a.structure == b.structure
    assert "Modelica.Electrical.Analog.Basic.Capacitor C(C=0.0001, v(start=0.0, fixed=true))" in a.text
    assert "Modelica.Electrical.Analog.Sources.StepVoltage V(V=12.0, offset=0.0, startTime=0.001)" in a.text
    assert "connect(C.n, gnd.p);" in a.text
    assert "experiment(StartTime=0.0, StopTime=0.05, Interval=2.5e-05, Tolerance=1e-06)" in a.text
    assert a.overrides["C.C"] == 1e-4 and a.overrides["C.v"] == 0.0 and a.overrides["R.R"] == 10.0
    assert a.fixed == ["C.v0", "L.i0"]


def test_structure_ignores_values_but_not_topology():
    a = M.translate(RLC)
    b = study(RLC)
    b["components"]["R"]["R"] = 22
    b["components"]["C"]["v0"] = 3
    b["analysis"] = {"kind": "transient", "stop_time": 1.0, "intervals": 10}
    bb = M.translate(b)
    assert a.structure == bb.structure and a.text != bb.text
    assert M.structure_hash(a.structure, "1.27.1", "4.0.0") == M.structure_hash(bb.structure, "1.27.1", "4.0.0")
    assert M.structure_hash(a.structure, "1.27.1", "4.0.0") != M.structure_hash(a.structure, "1.26.0", "4.0.0")
    c = study(RLC)
    c["connect"] = [["V.p", "L.p"], ["L.n", "R.p"], ["R.n", "C.p"], ["C.n", "gnd"], ["V.n", "gnd"]]
    assert M.translate(c).structure != a.structure


def test_structural_values_are_in_the_structure():
    s = study(RC)
    s["components"]["tf"] = {"kind": "transfer_function", "b": [1], "a": [1, 1]}
    s["components"]["c"] = {"kind": "constant", "k": 1}
    s["connect"].append(["c.y", "tf.u"])
    a = M.translate(s)
    s["components"]["tf"]["a"] = [2, 1]
    assert "a={2.0, 1.0}" in M.translate(s).structure and "a={1.0, 1.0}" in a.structure


def test_composites_flatten():
    t = M.translate(DC_PI)
    assert "Modelica.Electrical.Analog.Basic.RotationalEMF M__emf(k=0.05)" in t.text
    assert "connect(M__emf.flange, M__rotor.flange_a);" in t.text
    assert "Modelica.Blocks.Continuous.PI pi(T=0.02, initType=Modelica.Blocks.Types.Init.InitialState, k=0.5)" in t.text
    assert t.outputs["i"] == ([(1.0, "M__La.i")], "A")
    # the load is rigidly joined to the rotor: its initial values follow
    assert "load(J=0.0002)" in t.text and "M__rotor.phi" in t.overrides and "load.phi" not in t.overrides


def test_thermal_alternatives_and_convection():
    t = M.translate(THERMAL)
    assert "HeatCapacitor blk(C=1800.0, T(start=293.15, fixed=true))" in t.text
    assert "Constant conv__Gc(k=0.5)" in t.text
    assert "connect(conv__Gc.y, conv__conv.Gc);" in t.text
    assert t.overrides["conv__Gc.k"] == 0.5 and t.overrides["amb.T"] == 293.15


def test_linearize_top_level_io():
    t = M.translate(TWO_MASS)
    assert "Modelica.Blocks.Interfaces.RealInput omct_u_tau" in t.text
    assert "connect(omct_u_tau, T.tau);" in t.text
    assert "omct_y_w2 = J2.w;" in t.text


def test_mos_and_override_file():
    mos = M.build_mos("model.mo", "4.0.0", 0.05, 2000, 1e-6)
    assert 'loadModel(Modelica, {"4.0.0"}); getErrorString();' in mos
    assert 'setCommandLineOptions("--linearizationDumpLanguage=modelica")' in mos
    assert re.search(r'buildModel\(omct_model, .*fileNamePrefix="omct_model", outputFormat="mat"', mos)
    t = M.translate(RLC)
    txt = M.override_file(t.overrides)
    assert txt.splitlines()[0] == "C.C=0.0001" and txt.endswith("\n")
    assert "C\\.v" in M.variable_filter(t)


def test_bool_override_values():
    s = {"components": {"J": {"kind": "inertia", "J": 1}, "load": {"kind": "linear_speed_load", "tau_nominal": -1,
                                                                    "w_nominal": 10, "TorqueDirection": False}},
         "connect": [["J.flange_a", "load.flange"]], "outputs": {"w": "J.w"},
         "analysis": {"kind": "transient", "stop_time": 1}}
    t = M.translate(s)
    assert t.overrides["load.TorqueDirection"] is False
    assert "load.TorqueDirection=false" in M.override_file(t.overrides)


def test_examples_validate_and_translate():
    import json
    from pathlib import Path

    from openmodelica_translator.format import validate
    ex = json.loads((Path(__file__).resolve().parents[1] / "examples" / "studies.json").read_text())
    v = validate(ex)
    assert v["ok"], {k: s["errors"] for k, s in v["studies"].items() if not s["ok"]}
    for name, st in ex.items():
        t = M.translate(st, name)
        assert t.text.count("model omct_model") == 1 and t.overrides


def test_generated_text_parses():
    """Modelica syntax of every example (modparc, a Modelica grammar in Python; skipped when missing)."""
    import json
    from pathlib import Path

    import pytest
    modparc = pytest.importorskip("modparc")
    ex = json.loads((Path(__file__).resolve().parents[1] / "examples" / "studies.json").read_text())
    for name, st in ex.items():
        modparc.parse(M.translate(st, name).text)
