"""test_fit.py — parameter estimation (V4 F1) on the reference solver: synthetic data from the reference itself.
Version: 2026.11.19.6
"""
import copy

import numpy as np
import pytest
from studies import RC, THERMAL

from openmodelica_translator import modelica
from openmodelica_translator.fit import FitError, load
from openmodelica_translator.format import study_format, validate_study
from openmodelica_translator.runner import run_study

T = np.linspace(0, 0.005, 26)
Y = 1 - np.exp(-T / 1e-3)                 # RC: R = 1000, C = 1 uF, 1 V step


def rc_fit(**fit):
    st = copy.deepcopy(RC)
    st["fit"] = {"params": {"R.R": {"initial": "3 kOhm", "min": 100, "max": 1e5}},
                 "data": {"v": {"t": T.tolist(), "y": Y.tolist()}}, **fit}
    return st


def errors(st):
    return " | ".join(validate_study(st)["errors"])


def thermal(**fit):
    st = copy.deepcopy(THERMAL)
    st.pop("derived")
    st["fit"] = fit
    return st


def test_rc_recovers_R(tmp_path):
    r = run_study("rc", rc_fit(), tmp_path, "ref", 600)
    assert r["ok"] and r["reliable"], r.get("unreliable_because")
    f = r["fit"]
    p = f["params"]["R.R"]
    assert p["value"] == pytest.approx(1000, rel=1e-6)
    assert p["initial"] == 3000 and p["given"] == "3 kOhm" and p["scale"] == "log" and not p["at_bound"]
    assert f["status"] == "converged" and f["identifiable"] and f["failed_evaluations"] == 0
    assert f["quality"]["v"]["r2"] == pytest.approx(1.0) and f["quality"]["v"]["points"] == 26
    assert len(f["residuals"]["v"]["model"]) == 26
    assert r["outputs"]["v"]["final"] == pytest.approx(1 - np.exp(-5), rel=1e-6)   # the final run at the best R
    assert "energy" in r["checks"]                                         # the final run has its checks


def test_rc_R_and_C_not_separable(tmp_path):
    st = rc_fit()
    st["fit"]["params"]["C.C"] = {"initial": "2 uF", "min": 1e-8, "max": 1e-4}
    r = run_study("rc2", st, tmp_path, "ref", 600)
    f = r["fit"]
    assert not f["identifiable"]
    assert f["not_separable"][0][:2] == ["R.R", "C.C"] and abs(f["not_separable"][0][2]) > 0.98
    assert not f["params"]["R.R"]["separable"]
    assert any("not separable" in w for w in r["warnings"])
    R, C = f["params"]["R.R"]["value"], f["params"]["C.C"]["value"]
    assert R * C == pytest.approx(1e-3, rel=1e-5)                          # only the time constant is determined


def test_noise_with_sigma_and_an_initial_value(tmp_path):
    rng = np.random.default_rng(1)
    y = 0.3 + 0.7 * (1 - np.exp(-T / 1e-3)) + rng.normal(0, 0.01, T.size)
    st = copy.deepcopy(RC)
    st["fit"] = {"params": {"R.R": {"initial": 2000, "min": 100, "max": 1e5},
                            "C.v0": {"initial": 0, "min": -1, "max": 1}},
                 "data": {"v": {"t": T.tolist(), "y": (1000 * y).tolist(), "unit": "mV", "sigma": 10}}}
    r = run_study("rc3", st, tmp_path, "ref", 600)
    f = r["fit"]
    assert f["sigma_from"] == "the data's sigma" and f["params"]["C.v0"]["scale"] == "lin"
    for k, true in (("R.R", 1000), ("C.v0", 0.3)):
        p = f["params"][k]
        assert abs(p["value"] - true) < 3 * p["stderr"]
        assert p["ci95"][0] < p["value"] < p["ci95"][1]
    assert 0 < f["params"]["R.R"]["stderr"] < 200


def test_steady_points_alone(tmp_path):
    # T_ss = T_amb + Q / (h A): h = 10, A = 0.05 -> 120 degC at 50 W, 70 degC at 25 W
    st = thermal(params={"conv.h": {"initial": 30, "min": 1, "max": 200}},
                 steady=[{"set": {"q.Q_flow": 50}, "values": {"T": "120 degC"}},
                         {"set": {"q.Q_flow": 25}, "values": {"T": "70 degC"}}])
    r = run_study("th", st, tmp_path, "ref", 600)
    f = r["fit"]
    assert f["params"]["conv.h"]["value"] == pytest.approx(10, rel=1e-6)
    pts = f["quality"]["steady"]
    assert pts[0]["values"]["T"]["data"] == pytest.approx(393.15) and abs(pts[1]["values"]["T"]["error"]) < 1e-6


def test_heating_curve_in_degC_with_T0(tmp_path):
    base = thermal()
    base.pop("fit")
    tr = modelica.translate(base)
    tt = np.linspace(0, 20000, 41)
    v = tr.ref.simulate(tt, t0=0)
    y = sum(c * v[p] for c, p in tr.outputs["T"][0]) - 273.15
    st = thermal(params={"blk.m": {"initial": 5, "min": 0.1, "max": 50}, "conv.h": {"initial": 30, "min": 1, "max": 200},
                         "blk.T0": {"initial": "30 degC", "min": "0 degC", "max": "60 degC"}},
                 data={"T": {"t": (tt / 60).tolist(), "t_unit": "min", "y": y.tolist(), "unit": "degC"}})
    r = run_study("th2", st, tmp_path, "ref", 600)
    p = r["fit"]["params"]
    assert p["blk.m"]["value"] == pytest.approx(2, rel=1e-5)
    assert p["conv.h"]["value"] == pytest.approx(10, rel=1e-5)
    assert p["blk.T0"]["value"] == pytest.approx(293.15, abs=1e-4) and p["blk.T0"]["scale"] == "log"
    assert r["fit"]["identifiable"]


def test_csv_and_multistart(tmp_path):
    f = tmp_path / "rc.csv"
    f.write_text("time,volts\ns,V\n" + "".join(f"{a},{b}\n" for a, b in zip(T, Y)))
    st = rc_fit(starts=3)
    st["fit"]["data"] = {"v": {"csv": str(f), "t": "time", "y": "volts"}}
    assert validate_study(st)["ok"]
    r = run_study("csv", st, tmp_path / "run", "ref", 600)
    fit = r["fit"]
    assert fit["params"]["R.R"]["value"] == pytest.approx(1000, rel=1e-6)
    assert len(fit["starts"]) == 3 and fit["starts"][0]["initial"]["R.R"] == 3000
    assert {s["start"] for s in fit["starts"]} == {0, 1, 2}


def test_load_converts_units():
    st = rc_fit()
    st["fit"]["data"]["v"].update(unit="mV", t_unit="ms", sigma=2)
    st["fit"]["data"]["v"]["t"] = (T * 1000).tolist()
    params, series, steady = load(st, {"v": "V"})
    assert params[0].initial == 3000 and params[0].log
    assert series[0].t[-1] == pytest.approx(0.005) and series[0].y[-1] == pytest.approx(Y[-1] / 1000)
    assert series[0].scale == pytest.approx(0.002) and series[0].has_sigma and steady == []


@pytest.mark.parametrize("change, msg", [
    (lambda f: f["params"].update({"R.R": {"initial": 10, "min": 100, "max": 1e5}}), "initial must be within"),
    (lambda f: f["params"].update({"R.R": {"initial": 10}}), "{initial, min, max"),
    (lambda f: f["params"].update({"X.R": {"initial": 1, "min": 0.1, "max": 2}}), "no component 'X'"),
    (lambda f: f["params"].update({"R.Q": {"initial": 1, "min": 0.1, "max": 2}}), "has parameters"),
    (lambda f: f["params"].update({"C.v0": {"initial": 0, "min": -1, "max": 1, "scale": "log"}}), "log needs min > 0"),
    (lambda f: f["params"].update({"C.C": {"initial": 1e-6, "min": 0, "max": 1e-5}}), "(> 0)"),
    (lambda f: f["params"].update({"R.R": {"initial": 1, "min": -1, "max": 2}}), "parameter's own range"),
    (lambda f: f["data"].update({"w": {"t": [0, 1e-3], "y": [0, 1]}}), "not an output"),
    (lambda f: f["data"]["v"].update(t=[0, 1]), "same length"),
    (lambda f: f["data"].update({"v": {"t": [0, 0.01], "y": [0, 1]}}), "must lie within"),
    (lambda f: f["data"]["v"].update(sigma=0), "sigma: must be > 0"),
    (lambda f: f["data"]["v"].update(unit="kg"), "fit.data['v']"),
    (lambda f: f["data"].update({"v": {"csv": "/nonexistent.csv", "t": "t", "y": "y"}}), "no file"),
    (lambda f: f.update(starts=0), "fit.starts"),
    (lambda f: f.update(bogus=1), "unknown keys"),
    (lambda f: f.pop("data"), "give data"),
])
def test_validation(change, msg):
    st = rc_fit()
    change(st["fit"])
    assert msg in errors(st)


def test_structural_and_combinations_refused():
    st = rc_fit()
    st["sweep"] = {"params": {"R.R": [1, 2]}}
    assert "not together with sweep" in errors(st)
    st = rc_fit()
    st["converge"] = True
    assert "not together with converge" in errors(st)
    st = rc_fit()
    st["fit"]["params"]["R.R"] = {"initial": 1, "min": 0.5, "max": 2}
    st["fit"]["data"]["v"] = {"t": [0.001], "y": [0.5]}
    assert "t and y" in errors(st)


def test_omc_path_comes_with_F2(tmp_path):
    r = run_study("rc", rc_fit(), tmp_path, "omc", 600)
    assert not r["ok"] and "F2" in r["why"] and r["reliable"] is False


def test_cancel(tmp_path):
    (tmp_path / "cancel").write_text("")
    r = run_study("rc", rc_fit(), tmp_path, "ref", 600)
    assert r.get("state") == "cancelled"


def test_study_format_lists_fit():
    assert "fit" in study_format()["study_keys"]


def test_parameter_value_errors_raise():
    st = rc_fit()
    st["fit"]["params"]["R.R"]["min"] = "3 kg"
    with pytest.raises(FitError):
        load(st, {"v": "V"})
