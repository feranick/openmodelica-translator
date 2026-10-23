"""test_backend.py — the omc driver, the runner and the result reading: on outputs recorded on mochi
(tests/fixtures), with a fake omc (tests/fake_omc.py), and with the real OpenModelica when it is installed.
Version: 2026.10.23.1
"""
import json
import math
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from studies import RC, RLC, TWO_MASS, study

from openmodelica_translator import linear, omc, simrun
from openmodelica_translator.runner import run_study

FIX = Path(__file__).resolve().parent / "fixtures"


# ---- recorded on mochi ------------------------------------------------------------------------------
def test_parse_log_fixtures():
    ok = simrun.parse_log((FIX / "run_ok.log").read_text())
    assert ok["init_ok"] and ok["finished"] and ok["homotopy_steps"] == 0
    assert ok["stats"]["steps"] == 218 and ok["stats"]["state_events"] == 1 and ok["stats"]["solver"] == "dassl"
    ov = simrun.parse_log((FIX / "run_overrides.log").read_text())
    assert ov["override_refused"] == ["Rdep", "Rev", "Rfin"] and ov["override_unknown"] == ["nosuch"]
    a = simrun.parse_log((FIX / "run_assert.log").read_text())
    assert a["asserts"] and not a["finished"]
    i = simrun.parse_log((FIX / "run_initfail.log").read_text())
    assert not i["init_ok"] and "terminated by an assertion at initialization" in i["asserts"][0]
    c = simrun.parse_log((FIX / "run_chatter.log").read_text())
    assert c["chattering"]["t0"] == pytest.approx(1.0000000001)
    s = simrun.parse_log((FIX / "run_steady.log").read_text())
    assert s["steady_state_time"] == 9980.0
    z = simrun.parse_log("LOG_STDOUT        | warning | You are overriding V.offset with a small value or zero.\n"
                         "|                 | |       | This could lead to numerically dirty solutions.\n")
    assert z["warnings"] == []
    lz = simrun.parse_log((FIX / "run_linearize.log").read_text())
    assert lz["linear_model"].endswith("linearized_model.mo")


def test_omc_values_and_messages():
    vals = omc.parse_values((FIX / "omc_probe_script.out").read_text())
    assert vals[:5] == [True, "", True, "", "4.0.0"]
    assert vals[5][0] == ["Modelica", "/home/nicola/.openmodelica/libraries/Modelica 4.0.0+maint.om"]
    assert "44 equation(s) and 44 variable(s)" in vals[8]
    m = omc.messages('[/x/model.mo:3:3-3:40:writable] Error: Modified element Rx not found in class Resistor.\n',
                     "model omct_model\n  // c\n  Modelica.Electrical.Analog.Basic.Resistor R(Rx=1) \"R\";\nend omct_model;")
    assert m == [{"level": "error", "message": "Modified element Rx not found in class Resistor.", "line": 3, "element": "R"}]


def test_interpret_build_unbalanced_and_failed():
    good = ('true\n""\ntrue\n""\n"4.0.0"\ntrue\n""\n"Check of omct_model completed successfully.\nClass omct_model has '
            '4 equation(s) and 5 variable(s).\n1 of these are trivial equation(s)."\n""\n{"/x/omct_model", "i.xml"}\n""\n')
    r = omc.interpret_build(good, "", {"exit": 0})
    assert not r["ok"] and "not balanced: 4 equations for 5 variables" in r["errors"][0]
    bad = good.replace("4 equation(s)", "5 equation(s)").replace('{"/x/omct_model", "i.xml"}\n""',
                                                                '{"", ""}\n"Error: Internal error IndexReduction failed\n"')
    r = omc.interpret_build(bad, "", {"exit": 0})
    assert not r["ok"] and "IndexReduction" in r["errors"][0]
    r = omc.interpret_build(good.replace("4 equation(s)", "5 equation(s)"), "", {"exit": 0})
    assert r["ok"] and r["check"] == {"equations": 5, "variables": 5, "trivial": 1}
    assert not omc.interpret_build('true\n""\nfalse\n', "", {"exit": 0})["ok"]


def test_linearized_model_fixture():
    L = linear.read(FIX / "linearized_model.mo")
    assert L["states"] == ["C.v", "L.i"] and L["inputs"] == ["u"] and L["outputs"] == ["y"]
    assert L["A"][0, 1] == pytest.approx(1e4) and L["B"][1, 0] == pytest.approx(100, rel=1e-7)
    a = linear.analyse(L, [1.0])
    ev = [complex(*e) for e in a["eigenvalues"]]
    # R = 10, L = 0.01, C = 1e-4, R2 = 24 kOhm across C (the probe's RLC)
    A = np.array([[-1 / (24000 * 1e-4), 1e4], [-100.0, -1000.0]])
    assert np.allclose(sorted(np.linalg.eigvals(A), key=lambda x: x.imag), sorted(ev, key=lambda x: x.imag), rtol=1e-6)
    assert a["modes"][0]["zeta"] == pytest.approx(0.5, rel=1e-3)
    assert a["dc_gain"]["y"]["u"] == pytest.approx(24000 / 24010, rel=1e-6)


# ---- with the fake omc ------------------------------------------------------------------------------
@pytest.fixture
def fake(tmp_path, monkeypatch):
    w = tmp_path / "bin" / "omc"
    w.parent.mkdir()
    w.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve().parent / "fake_omc.py"}" "$@"\n')
    w.chmod(0o755)
    monkeypatch.setenv("OMCT_OMC", str(w))
    monkeypatch.setenv("OMCT_OUT", str(tmp_path / "runs"))
    omc.find_omc.cache_clear()
    omc.msl_status.cache_clear()
    yield tmp_path
    omc.find_omc.cache_clear()
    omc.msl_status.cache_clear()


def _with_study(monkeypatch, tmp_path, st):
    p = tmp_path / "study.json"
    p.write_text(json.dumps(st))
    monkeypatch.setenv("OMCT_FAKE_STUDY", str(p))


def test_fake_run_end_to_end(fake, monkeypatch):
    st = study(RLC)
    _with_study(monkeypatch, fake, st)
    r = run_study("rlc", st, fake / "req" / "rlc", "omc", 60)
    assert r["ok"] and r["reliable"], r
    assert r["model_info"]["equations"] == 10 and r["model_info"]["cache_hit"] is False
    assert r["checks"]["overrides"]["ok"] and r["checks"]["overrides"]["checked"] >= 7
    assert r["outputs"]["v_C"]["final"] == pytest.approx(12.0, rel=1e-3) and r["outputs"]["v_C"]["unit"] == "V"
    assert len(r["outputs"]["v_C"]["series"]["t"]) <= 400
    assert r["checks"]["energy"]["ok"] and r["checks"]["energy"]["covered"] and r["checks"]["energy"]["error_rel"] < 1e-3
    assert r["checks"]["reference"]["ok"] and r["checks"]["reference"]["max_error_rel"] < 1e-8
    assert json.loads((fake / "req" / "rlc" / "rlc.json").read_text())["ok"]
    cache = Path(os.environ["OMCT_OUT"]) / ".cache" / r["model_info"]["hash"]
    assert (cache / "omct_model").is_file() and not (cache / "omct_model.c").exists()
    # a second study with other values: the same build
    st2 = study(RLC)
    st2["components"]["R"]["R"] = 5
    _with_study(monkeypatch, fake, st2)
    r2 = run_study("rlc2", st2, fake / "req" / "rlc2", "omc", 60)
    assert r2["ok"] and r2["model_info"]["cache_hit"] and r2["model_info"]["hash"] == r["model_info"]["hash"]
    assert "R.R=5.0" in (fake / "req" / "rlc2" / "params.txt").read_text()


def test_fake_failures(fake, monkeypatch):
    st = study(RC)
    _with_study(monkeypatch, fake, st)
    monkeypatch.setenv("OMCT_FAKE_BUILD_FAIL", "1")
    r = run_study("rc", st, fake / "a" / "rc", "omc", 60)
    assert not r["ok"] and r["why"] == "the model did not build" and "Rx" in r["errors"][0]
    monkeypatch.delenv("OMCT_FAKE_BUILD_FAIL")
    monkeypatch.setenv("OMCT_FAKE_UNBALANCED", "1")
    st["components"]["R"]["R"] = 2000          # same structure: the failed build was not cached
    r = run_study("rc", st, fake / "b" / "rc", "omc", 60)
    assert not r["ok"] and "not balanced" in r["errors"][0]
    monkeypatch.delenv("OMCT_FAKE_UNBALANCED")
    monkeypatch.setenv("OMCT_FAKE_SHIFT", "R.R")
    r = run_study("rc", st, fake / "c" / "rc", "omc", 60)
    assert not r["ok"] and r["why"] == "values were not applied as asked" and r["errors"][0].startswith("R.R")


def test_fake_value_compiled_in(fake, monkeypatch):
    """A value the model refuses at run time (an alias, measured on mochi with a mass's v): the same value runs
    from the build; another value gets a variant build."""
    st = study(RC)
    monkeypatch.setenv("OMCT_CACHE", str(fake / "cache2"))
    _with_study(monkeypatch, fake, st)
    monkeypatch.setenv("OMCT_FAKE_REFUSE", "C.C")
    r = run_study("rc", st, fake / "d" / "rc", "omc", 60)
    assert r["ok"], r
    assert "C.C=" not in (fake / "d" / "rc" / "params.txt").read_text()
    assert any("compiled into the model" in w and "C.C" in w for w in r["warnings"])
    h1 = r["model_info"]["hash"]
    r = run_study("rc", st, fake / "e" / "rc", "omc", 60)
    assert r["ok"] and r["model_info"]["hash"] == h1 and r["model_info"]["cache_hit"]
    st["components"]["C"]["C"] = "2 uF"
    _with_study(monkeypatch, fake, st)
    r = run_study("rc", st, fake / "f" / "rc", "omc", 60)
    assert r["ok"] and r["model_info"]["variant"] == ["C.C"] and r["model_info"]["hash"] != h1
    assert r["checks"]["overrides"]["ok"]


def test_fake_linearize(fake, monkeypatch):
    st = study(TWO_MASS)
    _with_study(monkeypatch, fake, st)
    r = run_study("tm", st, fake / "e" / "tm", "omc", 60)
    assert r["ok"], r
    assert r["linearization"]["states"] == ["C.v", "L.i"]        # the fixture's model


def test_ref_solver_run(tmp_path):
    r = run_study("rc", study(RC), tmp_path / "rc", "ref", 60)
    assert r["ok"] and r["outputs"]["v"]["final"] == pytest.approx(1 - math.exp(-5), rel=1e-9)
    r = run_study("tm", study(TWO_MASS, analysis={"kind": "linearize", "inputs": {"tau": "T.tau"},
                                                   "bode": [1.0, 10.0]}), tmp_path / "tm", "ref", 60)
    assert r["ok"] and len(r["linearization"]["eigenvalues"]) == 4 and "tau->w2" in r["linearization"]["bode"]


def test_invalid_study_result(tmp_path):
    r = run_study("x", {"components": {}}, tmp_path / "x", "ref", 60)
    assert not r["ok"] and r["why"] == "invalid study" and r["reliable"] is False


# ---- with OpenModelica ------------------------------------------------------------------------------
def _real_omc():
    exe = os.environ.get("OMCT_OMC") or shutil.which("omc")
    return exe if exe and "fake" not in exe else None


@pytest.mark.skipif(_real_omc() is None, reason="OpenModelica (omc) is not installed here")
def test_real_rc_against_closed_form(tmp_path, monkeypatch):
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    omc.find_omc.cache_clear()
    st = study(RC, analysis={"kind": "transient", "stop_time": 0.005, "intervals": 500})
    r = run_study("rc", st, tmp_path / "rc", "omc", 300)
    assert r["ok"] and r["reliable"], r
    s = r["outputs"]["v"]["series"]
    t, y = np.array(s["t"]), np.array(s["y"])
    assert np.max(np.abs(y - (1 - np.exp(-t / 1e-3)))) < 1e-4
    assert r["checks"]["overrides"]["ok"]


@pytest.mark.skipif(_real_omc() is None, reason="OpenModelica (omc) is not installed here")
def test_real_examples_build_and_run(tmp_path, monkeypatch):
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    omc.find_omc.cache_clear()
    ex = json.loads((Path(__file__).resolve().parents[1] / "examples" / "studies.json").read_text())
    bad = {}
    for name, st in ex.items():
        if st.get("sweep"):
            continue
        r = run_study(name, st, tmp_path / name, "omc", 300)
        if not r["ok"]:
            bad[name] = (r.get("why"), r.get("errors"))
        print(name, "ok" if r["ok"] else "FAILED", r.get("why", ""), r.get("errors", ""), r.get("warnings", ""))
    assert not bad, bad


def test_fake_converge_and_expect(fake, monkeypatch):
    st = study(RC, converge=True, expect=[{"quantity": "outputs.v.final", "value": 0.99, "tol": 0.001}])
    _with_study(monkeypatch, fake, st)
    r = run_study("rc", st, fake / "g" / "rc", "omc", 60)
    assert r["ok"] and r["checks"]["converge"]["ok"] and r["checks"]["converge"]["tolerance"] == 1e-7
    assert r["passed"] is False and r["expect"][0]["got"] == pytest.approx(1 - math.exp(-5), rel=1e-6)
    assert r["reliable"]          # expect sets passed, not reliable


def test_fake_sweep_one_build(fake, monkeypatch):
    st = study(RC, sweep={"params": {"R.R": [500, 1000, 2000], "C.v0": [0, 0.5]}, "mode": "grid"}, run={"procs": 3})
    _with_study(monkeypatch, fake, st)
    r = run_study("sw", st, fake / "h" / "sw", "omc", 60)
    assert r["ok"] and r["sweep"]["points"] == 6 and r["sweep"]["builds"] == 1, r.get("sweep")
    params = {(fake / "h" / "sw" / "points" / f"p{i:04d}" / "params.txt").read_text() for i in range(6)}
    assert len(params) == 6
    assert r["sweep"]["table"][5]["params"] == {"R.R": 2000, "C.v0": 0.5}
    assert r["sweep"]["table"][5]["params_si"] == {"R.R": 2000.0, "C.v0": 0.5}


def test_msl_status_refuses_a_fallback_version(fake, monkeypatch):
    """omc loads a newer MSL when 4.0.0 is missing: status must say so (carbonio, 2026-10-21)."""
    monkeypatch.setenv("OMCT_FAKE_MSL", "4.1.0")
    st = omc.msl_status()
    assert "error" in st and "4.1.0" in st["error"] and "not installed" in st["error"]
    omc.msl_status.cache_clear()
    monkeypatch.setenv("OMCT_FAKE_MSL", "4.0.0")
    assert omc.msl_status() == {"version": "4.0.0", "path": "/fake/libraries/Modelica 4.0.0"}


def test_no_override_file_when_nothing_to_override(tmp_path, monkeypatch):
    """OpenModelica 1.27.1 cannot read an empty override file: an escape-hatch model runs without the flag."""
    from openmodelica_translator import simrun
    seen = {}

    def fake_run(cmd, cwd, timeout, log, cancel=None, env=None):
        seen["cmd"] = [str(c) for c in cmd]
        Path(log).write_text("")
        return {"exit": 0, "seconds": 0.0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(simrun, "run_proc", fake_run)
    simrun.simulate(tmp_path, tmp_path / "r", {}, start=0.0, stop=1.0, intervals=10, tolerance=1e-6)
    assert not any(c.startswith("-overrideFile") for c in seen["cmd"])
    simrun.simulate(tmp_path, tmp_path / "r", {"R.R": 1.0}, start=0.0, stop=1.0, intervals=10, tolerance=1e-6)
    assert any(c.startswith("-overrideFile") for c in seen["cmd"])
