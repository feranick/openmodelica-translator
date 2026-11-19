"""test_fit.py — parameter estimation (V4): on the reference solver, and through OpenModelica with the fake omc
(tests/fake_omc.py runs the reference with the override values); synthetic data from the reference itself.
Version: 2026.11.19.7
"""
import copy

import numpy as np
import pytest
from studies import RC, THERMAL
from test_backend import _with_study, fake  # noqa: F401 (the fake omc fixture)

from openmodelica_translator import modelica
from openmodelica_translator.fit import FitError, _dedupe, load, optimize, series_grid, settled_end
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


# ---- OpenModelica (the fake omc) ------------------------------------------------------------------------------------
def test_omc_recovers_R(fake, monkeypatch):
    st = rc_fit()
    _with_study(monkeypatch, fake, {k: v for k, v in st.items() if k != "fit"})
    r = run_study("rc", st, fake / "req" / "rc", "omc", 60)
    assert r["ok"] and r["reliable"], (r.get("why"), r.get("errors"), r.get("unreliable_because"))
    f = r["fit"]
    assert f["params"]["R.R"]["value"] == pytest.approx(1000, rel=1e-5)
    assert f["status"] == "converged" and f["runs"] == f["evaluations"] + 1 and f["failed_evaluations"] == 0
    assert f["tolerance"] == pytest.approx(1e-7) and f["run_grid"] == {"stop": 0.005, "intervals": 500}
    assert f["model_info"]["hash"] == r["model_info"]["hash"]                    # one build, the final run's
    assert not list((fake / "req" / "rc" / "evaluations").glob("e*"))             # each run's folder removed
    assert r["checks"]["overrides"]["ok"]                                         # the final run read them back
    assert (fake / "req" / "rc" / "fit_progress.json").is_file()


def test_omc_two_params_in_parallel(fake, monkeypatch):
    st = rc_fit(max_evaluations=60)
    y = 0.3 + 0.7 * (1 - np.exp(-T / 1e-3))
    st["components"]["C"]["v0"] = 0
    st["fit"]["params"]["C.v0"] = {"initial": 0, "min": -1, "max": 1}
    st["fit"]["data"]["v"]["y"] = y.tolist()
    st["run"] = {"procs": 2}
    _with_study(monkeypatch, fake, {k: v for k, v in st.items() if k != "fit"})
    r = run_study("rc2", st, fake / "req" / "rc2", "omc", 60)
    f = r["fit"]
    assert f["workers"] == 2
    assert f["params"]["R.R"]["value"] == pytest.approx(1000, rel=1e-4)
    assert f["params"]["C.v0"]["value"] == pytest.approx(0.3, abs=1e-5)


def test_omc_steady_points(fake, monkeypatch):
    # V - R1 - (C || R2): v_ss = V R2 / (R1 + R2); R1 = R2 = 1 kOhm, tau = 0.5 ms, the run 40 tau
    st = {"components": {"V": {"kind": "step_voltage", "V": 1}, "R1": {"kind": "resistor", "R": 1000},
                         "R2": {"kind": "resistor", "R": 300}, "C": {"kind": "capacitor", "C": "1 uF"},
                         "g": {"kind": "ground"}},
          "connect": [["V.p", "R1.p"], ["R1.n", "C.p"], ["R2.p", "C.p"], ["R2.n", "g"], ["C.n", "g"], ["V.n", "g"]],
          "outputs": {"v": "C.v"}, "analysis": {"kind": "transient", "stop_time": 0.02, "intervals": 200}}
    _with_study(monkeypatch, fake, copy.deepcopy(st))
    st["fit"] = {"params": {"R2.R": {"initial": 300, "min": 10, "max": 1e5}},
                 "steady": [{"set": {"V.V": 2}, "values": {"v": 1.0}}, {"set": {"V.V": 4}, "values": {"v": 2.0}}]}
    r = run_study("div", st, fake / "req" / "div", "omc", 60)
    f = r["fit"]
    assert f["params"]["R2.R"]["value"] == pytest.approx(1000, rel=1e-4), f.get("last_failed_evaluation")
    assert f["quality"]["steady"][1]["values"]["v"]["model"] == pytest.approx(2.0, rel=1e-5)


def test_omc_compiled_in_value_is_refused(fake, monkeypatch):
    st = rc_fit()
    _with_study(monkeypatch, fake, {k: v for k, v in st.items() if k != "fit"})
    monkeypatch.setenv("OMCT_FAKE_REFUSE", "R.R")
    r = run_study("rc", st, fake / "req" / "rc", "omc", 60)
    assert not r["ok"] and "compiled R.R into the model" in r["why"]


def test_ref_refuses_custom_models(tmp_path):
    st = {"modelica": {"text": "model M Real x(start=1); parameter Real k=1; equation der(x) = -k*x; end M;",
                       "model": "M"}, "outputs": {"x": "x"}, "analysis": {"kind": "transient", "stop_time": 1},
          "fit": {"params": {"k": {"initial": 2, "min": 0.1, "max": 10}}, "data": {"x": {"t": [0, 1], "y": [1, 0.37]}}}}
    r = run_study("m", st, tmp_path, "ref", 60)
    assert not r["ok"] and "no reference solver" in r["why"]


# ---- pieces --------------------------------------------------------------------------------------------------------
def test_jacobian_takes_the_other_side_when_a_run_fails():
    from openmodelica_translator.fit import Param, Series
    t = np.linspace(0, 1, 11)
    s = [Series("y", t, 2.0 * t, 1.0)]
    p = [Param("a", 1.0, 0.0, 5.0, False)]
    seen = []

    def ev(vals):
        a = vals["a"]
        seen.append(a)
        if 1.0 < a < 1.0 + 1e-3:                       # the forward step from the start fails
            raise RuntimeError("solver failed")
        return {"y": a * t}, []

    best, runs, state = optimize(p, s, [], ev, 1, 50, 1e-5, workers=2)
    assert best["res"].x[0] == pytest.approx(2.0, rel=1e-8)
    assert state["failed"] >= 1 and "solver failed" in state["last_error"]


def test_series_grid():
    assert series_grid(0.0, np.linspace(0, 0.005, 26), 500) == (0.005, 500)       # 25 data intervals x 20
    assert series_grid(0.0, np.linspace(0, 2, 2001), 500) == (2.0, 2000)
    assert series_grid(0.0, np.array([0.1, 0.25, 0.7071]), 500) == (0.7071, 2000)  # not a grid: interpolated
    assert series_grid(1.0, np.array([1.5, 2.0]), 10) == (2.0, 10)                 # from t0 = 1


def test_settled_end_and_dedupe():
    t = np.linspace(0, 10, 101)
    assert settled_end(t, 1 - np.exp(-t / 0.5), "v") == pytest.approx(1.0, abs=1e-8)
    with pytest.raises(FitError, match="lengthen"):
        settled_end(t, 1 - np.exp(-t / 3), "v")
    tt, yy = _dedupe(np.array([0, 1, 1, 2.0]), np.array([0, 1, 5, 6.0]))
    assert tt.tolist() == [0, 1, 2] and yy.tolist() == [0, 5, 6]


# ---- the real OpenModelica (skipped where omc is not installed: run on the machines) -------------------------------
from test_backend import _real_omc  # noqa: E402

from openmodelica_translator import omc  # noqa: E402

REAL = pytest.mark.skipif(_real_omc() is None, reason="OpenModelica (omc) is not installed here")


@REAL
def test_real_rc_R(tmp_path, monkeypatch):
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    omc.find_omc.cache_clear()
    st = rc_fit()
    st["analysis"]["intervals"] = 500
    r = run_study("rc", st, tmp_path / "rc", "omc", 300)
    assert r["ok"] and r["reliable"], (r.get("why"), r.get("errors"), r.get("unreliable_because"))
    f = r["fit"]
    assert f["params"]["R.R"]["value"] == pytest.approx(1000, rel=1e-4) and f["identifiable"]
    assert r["checks"]["overrides"]["ok"] and r["checks"]["reference"]["ok"]


@REAL
def test_real_heating_curve(tmp_path, monkeypatch):
    # composite kinds: blk.m and conv.h reach the model as blk.C = m cp and conv__Gc.k = h A
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    omc.find_omc.cache_clear()
    base = thermal()
    base.pop("fit")
    tr = modelica.translate(base)
    tt = np.linspace(0, 20000, 41)
    v = tr.ref.simulate(tt, t0=0)
    y = sum(c * v[p] for c, p in tr.outputs["T"][0])
    st = thermal(params={"blk.m": {"initial": 5, "min": 0.1, "max": 50}, "conv.h": {"initial": 30, "min": 1, "max": 200},
                         "blk.T0": {"initial": 300, "min": 273.15, "max": 340}},
                 data={"T": {"t": tt.tolist(), "y": y.tolist()}}, max_evaluations=200)
    st["run"] = {"procs": 3}
    r = run_study("th", st, tmp_path / "th", "omc", 300)
    assert r["ok"] and r["reliable"], (r.get("why"), r.get("errors"), r.get("unreliable_because"))
    p = r["fit"]["params"]
    assert p["blk.m"]["value"] == pytest.approx(2, rel=1e-3) and p["conv.h"]["value"] == pytest.approx(10, rel=1e-3)
    assert p["blk.T0"]["value"] == pytest.approx(293.15, abs=0.01)


@REAL
def test_real_custom_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    omc.find_omc.cache_clear()
    t = np.linspace(0, 2, 41)
    st = {"modelica": {"text": "model M Real x(start=1, fixed=true); parameter Real k=1; equation der(x) = -k*x; end M;",
                       "model": "M", "values": {"k": 1.0}},
          "outputs": {"x": "x"}, "analysis": {"kind": "transient", "stop_time": 2, "intervals": 400},
          "fit": {"params": {"k": {"initial": 1, "min": 0.1, "max": 10}},
                  "data": {"x": {"t": t.tolist(), "y": np.exp(-2.5 * t).tolist()}}}}
    r = run_study("m", st, tmp_path / "m", "omc", 300)
    assert r["ok"], (r.get("why"), r.get("errors"))
    assert r["fit"]["params"]["k"]["value"] == pytest.approx(2.5, rel=1e-4)
