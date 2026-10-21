"""test_post.py — derived quantities and the energy balance on known series.
Version: 2026.10.21.4
"""
import math

import numpy as np
import pytest
from studies import RC

from openmodelica_translator import energy, post
from openmodelica_translator.network import build


def test_step_metrics_second_order():
    z, wn = 0.5, 100.0
    wd = wn * math.sqrt(1 - z * z)
    t = np.linspace(0, 0.2, 200001)
    y = 1 - np.exp(-z * wn * t) * (np.cos(wd * t) + z / math.sqrt(1 - z * z) * np.sin(wd * t))
    d = post.step(t, y, {"final": 1.0, "initial": 0.0}, 0.0)
    assert d["overshoot_pct"] == pytest.approx(100 * math.exp(-math.pi * z / math.sqrt(1 - z * z)), rel=1e-6)
    assert d["peak_time_s"] == pytest.approx(math.pi / wd, rel=1e-4)
    assert d["settling_time_s"] == pytest.approx(0.0808, rel=0.05)          # ~ 4/(z wn), the 2 % band
    assert 0.01 < d["rise_time_s"] < 0.03


def test_crossing_window_steady():
    t = np.linspace(0, 10, 1001)
    y = 1 - np.exp(-t)
    assert post._cross(t, y, 1 - math.exp(-1)) == pytest.approx(1.0, rel=1e-4)
    assert post._cross(t, y, 2.0) is None
    w = post.window(t, np.sin(2 * math.pi * t), 0, 10)
    assert w["mean"] == pytest.approx(0, abs=1e-6) and w["rms"] == pytest.approx(math.sqrt(0.5), rel=1e-4)
    s = post.steady(t, y, 1e-3)
    assert s["reached"] and s["t_steady_s"] == pytest.approx(-math.log(1e-3 / (1 - math.exp(-10))) + 0, rel=0.05)


def test_events_masked_and_expect_paths():
    t = np.array([0, 1, 1, 2.0])
    assert post.not_events(t).tolist() == [True, False, False, True]
    res = {"a": {"b": [1.0, 2.0]}}
    e = post.check_expect(res, [{"quantity": "a.b.1", "value": 2.0}, {"quantity": "a.c", "value": 1}])
    assert e[0]["passed"] and not e[1]["passed"]


def test_energy_balance_catches_a_wrong_series():
    net = build(RC)
    pl = energy.plan(net)
    assert pl["covered"] and {"C.v", "R.LossPower", "V.v", "V.i"} <= pl["paths"]
    t = np.linspace(0, 5e-3, 5001)
    v = 1 - np.exp(-t / 1e-3)
    i = 1e-6 * np.exp(-t / 1e-3) / 1e-3
    ser = {"C.v": v, "R.LossPower": 1000 * i * i, "V.v": np.ones_like(t), "V.i": -i}
    b = energy.balance(pl, t, ser.__getitem__)
    assert b["ok"] and b["error_rel"] < 1e-5
    ser["R.LossPower"] = 2000 * i * i
    assert not energy.balance(pl, t, ser.__getitem__)["ok"]
