"""test_format.py — the study format and validation: every error names its fix.
Version: 2026.10.21.14
"""
import json

import pytest
from studies import DC_PI, MSD, RC, RLC, THERMAL, TWO_MASS, study

from openmodelica_translator import catalog as C
from openmodelica_translator.format import EXAMPLE, study_format, validate, validate_study


def errors(st):
    return " | ".join(validate_study(st)["errors"])


def test_format_is_compact_and_lists_every_kind():
    sf = study_format()
    assert len(json.dumps(sf)) < 32000
    listed = {k for d in sf["kinds"].values() for k in d}
    assert listed == set(C.public_kinds())


@pytest.mark.parametrize("st", [RC, RLC, MSD, THERMAL, DC_PI, TWO_MASS, EXAMPLE["rlc_step"]])
def test_valid_studies(st):
    v = validate_study(st)
    assert v["ok"], v["errors"]


def test_validate_shape():
    v = validate({"a": RC, "b": {"components": {}}})
    assert not v["ok"] and v["studies"]["a"]["ok"] and not v["studies"]["b"]["ok"]


def _without(st, comp):
    s = study(st)
    del s["components"][comp]
    s["connect"] = [c for c in s["connect"] if not any(x.split(".")[0] == comp for x in c)]
    return s


@pytest.mark.parametrize("mutate, fragment", [
    (lambda s: _without(s, "gnd"), "has no ground"),
    (lambda s: s["components"].__setitem__("X", {"kind": "resistorr"}), "unknown kind 'resistorr'"),
    (lambda s: s["components"]["R"].__setitem__("Rx", 1), "unknown keys ['Rx']"),
    (lambda s: s["components"]["R"].pop("R"), "missing parameter 'R'"),
    (lambda s: s["components"]["R"].__setitem__("R", -1), "must be >= 0"),
    (lambda s: s["components"]["L"].__setitem__("L", "10 mF"), "does not fit"),
    (lambda s: s["connect"].append(["R.q", "L.p"]), "has no port 'q'"),
    (lambda s: s["components"].__setitem__("m", {"kind": "mass", "m": 1}) or s["connect"].append(["m.flange_a", "R.p"]),
     "is a translational port"),
    (lambda s: s["outputs"].__setitem__("x", "C.q"), "outputs.x: 'C.q'"),
    (lambda s: s["analysis"].__setitem__("stop_time", -1), "analysis.stop_time"),
    (lambda s: s["analysis"].__setitem__("kind", "harmonic"), "analysis: {kind"),
    (lambda s: s.__setitem__("derived", [{"kind": "step", "signal": "nope"}]), "derived[0].signal"),
    (lambda s: s.__setitem__("expect", [{"quantity": "x"}]), "expect[0]"),
    (lambda s: s.__setitem__("sweep", {"params": {"R.R": [1, 2]}, "mode": "zipper"}), "sweep.mode"),
    (lambda s: s.__setitem__("run", {"timeout_s": -3}), "run.timeout_s"),
    (lambda s: s["components"].__setitem__("1bad", {"kind": "ground"}), "names are identifiers"),
    (lambda s: s["components"].__setitem__("a__b", {"kind": "ground"}), "names are identifiers"),
    (lambda s: s["components"]["C"].__setitem__("v0", "3 V") or s["components"].__setitem__("C2", {"kind": "capacitor", "C": 1e-6, "v0": 1})
     or s["connect"].extend([["C2.p", "C.p"], ["C2.n", "gnd"]]), "C2.v0: this initial value is not free"),
])
def test_errors(mutate, fragment):
    s = study(RLC)
    r = mutate(s)
    s = r if isinstance(r, dict) and "components" in r else s
    assert fragment in errors(s)


def test_signal_errors():
    s = study(DC_PI)
    s["connect"] = [c for c in s["connect"] if c != ["ref.y", "fb.u1"]]
    assert "fb.u1 (signal input) is not connected" in errors(s)
    s = study(DC_PI)
    s["components"]["c2"] = {"kind": "constant", "k": 1}
    s["connect"].append(["c2.y", "fb.u1"])
    assert "driven by 2 outputs" in errors(s)
    s = study(DC_PI)
    s["connect"].append(["ref.y", "spd.w"])
    assert "a signal output connects to signal inputs only" in errors(s)


def test_reference_rules():
    s = {"components": {"k": {"kind": "spring", "c": 10}, "F": {"kind": "force_step", "stepForce": 1}},
         "connect": [["F.flange", "k.flange_a"]], "outputs": {"s": "k.s_rel"}, "analysis": {"kind": "transient", "stop_time": 1}}
    assert "neither a mass nor a fixed point" in errors(s)
    s = {"components": {"G": {"kind": "thermal_conductor", "G": 1}, "q": {"kind": "fixed_heat_flow", "Q_flow": 1}},
         "connect": [["q.port", "G.port_a"]], "outputs": {"d": "G.dT"}, "analysis": {"kind": "transient", "stop_time": 1}}
    assert "no heat capacitor, no fixed or prescribed" in errors(s)


def test_alternatives_and_units():
    s = study(THERMAL)
    s["components"]["blk"]["C"] = 1800
    assert "give exactly one of C or m + cp" in errors(s)
    s = study(THERMAL)
    del s["components"]["blk"]["cp"]
    assert "give exactly one of C or m + cp" in errors(s)
    s = study(THERMAL)
    s["derived"] = [{"kind": "crossing", "signal": "T", "level": "80 W"}]
    assert "does not fit" in errors(s)


def test_bare_names_and_ports():
    s = study(RC)
    s["connect"] = [["V.p", "R.p"], ["R.n", "C.p"], ["C.n", "g"], ["V.n", "g"]]
    assert validate_study(s)["ok"]
    s["connect"][0] = ["V", "R.p"]
    assert "say which port of V" in errors(s)


def test_linearize_inputs():
    s = study(TWO_MASS)
    s["analysis"] = {"kind": "linearize", "inputs": {"tau": "T.tau"}, "bode": {"f_min": 1, "f_max": 1000, "points": 20}}
    assert validate_study(s)["ok"]
    s["analysis"] = {"kind": "transient", "stop_time": 1}
    assert "T.tau (signal input) is not connected" in errors(s)
    s["analysis"] = {"kind": "linearize", "inputs": {}}
    assert "analysis.inputs" in errors(s)


def test_sweep_rules():
    s = study(RC)
    s["sweep"] = {"params": {"R.R": [500, "1 kOhm", 2000], "C.v0": [0, 0.5]}}
    assert validate_study(s)["ok"]
    s["sweep"] = {"params": {"R.R": [-1]}}
    assert "out of range" in errors(s)
    s = study(RC)
    s["components"]["tf"] = {"kind": "transfer_function", "b": [1], "a": [1, 1]}
    s["components"]["c"] = {"kind": "constant", "k": 1}
    s["connect"].append(["c.y", "tf.u"])
    s["sweep"] = {"params": {"tf.a": [[1, 2]]}}
    assert "structural" in errors(s)
    s = study(RC)
    s["sweep"] = {"params": {"R.R": list(range(1, 3000))}}
    assert "at most 2000" in errors(s)


def test_run_caps(monkeypatch):
    monkeypatch.setenv("OMCT_MAX_PROCS", "4")
    monkeypatch.setenv("OMCT_MAX_TIMEOUT", "100")
    s = study(RC, run={"procs": 8, "timeout_s": 50})
    assert "OMCT_MAX_PROCS" in errors(s)
    s = study(RC, run={"procs": 2, "timeout_s": 500})
    assert "OMCT_MAX_TIMEOUT" in errors(s)


def test_escape_hatch():
    s = {"modelica": {"text": "model M Real x(start=1, fixed=true); equation der(x) = -x; end M;", "model": "M"},
         "outputs": {"x": "x"}, "analysis": {"kind": "transient", "stop_time": 1}}
    v = validate_study(s)
    assert v["ok"] and "escape hatch" in v["warnings"][0]
