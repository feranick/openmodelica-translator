"""test_npsh.py — pumps' NPSH (V5 FL16): vapour pressures, NPSH3 from cfd-translator sweeps, the run's npsh block.
Version: 2026.11.19.27
"""
import copy
import math

import pytest

from openmodelica_translator import npsh as N
from openmodelica_translator.format import validate_study
from openmodelica_translator.network import build
from openmodelica_translator.runner import run_study

LIFT = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": 2.0, "level_start": 1, "T_start": 353.15},
                       "pipe": {"kind": "fl_pipe", "length": 6, "diameter": 0.04, "height_ab": 4.0},
                       "pump": {"kind": "fl_pump", "curve": [[0, 25], [0.004, 22], [0.008, 12]], "N_nominal": "2900 rpm",
                                "npsh_required": [[0.002, 1.5], [0.006, 3.0], [0.01, 5.0]], "inlet_diameter": 0.04},
                       "v": {"kind": "fl_valve", "Kv": 15, "dp_nominal": 1e5, "m_flow_nominal": 4},
                       "b": {"kind": "fl_boundary", "p": 1.5e5, "T": 353.15}},
        "connect": [["tank.port", "pipe.port_a"], ["pipe.port_b", "pump.port_a"], ["pump.port_b", "v.port_a"],
                    ["v.port_b", "b.port"]],
        "outputs": {"m": "pump.m_flow", "p": "pump.port_a.p"},
        "analysis": {"kind": "transient", "stop_time": 60, "intervals": 6, "tolerance": 1e-8}}


def test_vapour_pressures():
    assert N.psat_water(293.15) == pytest.approx(2339.2, abs=0.2)          # IAPWS-IF97 tables: 2.3392 kPa
    assert N.psat_water(373.124) == pytest.approx(101325, rel=1e-4)        # boiling at 1 atm
    assert N.psat_water(453.15) == pytest.approx(1.0028e6, rel=1e-3)
    g = N.VAPOUR_TABLES["glycol47"]
    for c, p in g:                                                          # MSL's table points exactly
        assert N.vapour_pressure("glycol47", c + 273.15)[0] == pytest.approx(p, rel=1e-12)
    assert 500 < N.vapour_pressure("glycol47", 283.15)[0] < 1900            # monotonic between them
    assert N.vapour_pressure({"rho": 1000, "cp": 4000, "p_vapour": 5000}, 300)[0] == 5000
    assert "IF97" in N.vapour_pressure({"rho": 1000, "cp": 4000}, 300)[1]
    assert N.vapour_pressure("air", 300)[0] is None


def _cav(V, rpm, npsh, head, name, flow_error=0.0):
    return {"name": name, "reliable": True,
            "results": {"cavitation": {"npsh_m": npsh, "flows": {"liquid_flow_error": flow_error}},
                        "pump": {"head_m": head, "flow_m3h": V * 3600}, "rotating": {"impeller": {"rpm": rpm}}}}


def _sweep(V, rpm, tag, scale=1.0):
    """Head 20 m above NPSH 3, 19.0 at 2.5, 15 at 2 (NPSH3 = 3 - 0.3/0.7 x 0.5 = 2.785714): at rpm, NPSH and head
    x scale^2."""
    pts = [(6, 20), (4, 20), (3, 19.7), (2.5, 19.0), (2, 15)]
    return [_cav(V, rpm, n * scale ** 2, h * scale ** 2, f"{tag}_npsh{n}") for n, h in pts]


def test_npsh3_from_cfdt_sweeps():
    n3 = 3 - (19.7 - 19.4) / (19.7 - 19.0) * 0.5
    r = N.from_cfdt(_sweep(0.004, 1450, "a") + _sweep(0.008, 1450, "b"), "p")
    assert r["curve"] == [[0.004, pytest.approx(n3)], [0.008, pytest.approx(n3)]]
    s = 1200 / 1450                                                          # a sweep at 1200 rpm, moved to 1450
    r = N.from_cfdt(_sweep(0.004, 1450, "a") + _sweep(0.006 * s, 1200, "c", s), "p")
    assert r["curve"][1] == [pytest.approx(0.006), pytest.approx(n3)]
    bad = _sweep(0.004, 1450, "a")
    bad[3]["results"]["cavitation"]["flows"]["liquid_flow_error"] = 0.05       # left out, as cfd-translator does
    assert N.npsh3([{"npsh": p["results"]["cavitation"]["npsh_m"], "head": p["results"]["pump"]["head_m"],
                     "flow_error": p["results"]["cavitation"]["flows"]["liquid_flow_error"]} for p in bad]) \
        == pytest.approx(3 - (19.7 - 19.4) / (19.7 - 15) * 1.0)
    with pytest.raises(N.NpshError, match="3 % head drop"):
        N.from_cfdt([_cav(0.004, 1450, n, 20.0, f"x{n}") for n in (6, 4, 3)], "p")
    with pytest.raises(N.NpshError, match="cavitation study"):
        N.from_cfdt([{"name": "q", "results": {"pump": {"head_m": 20}}}], "p")


def test_study_keys():
    st = copy.deepcopy(LIFT)
    assert validate_study(st)["ok"]
    st["components"]["pump"]["npsh_required"] = 2.5                        # a number: constant
    assert build(st).elems["pump"]["params"]["npsh_required"] == [[0.0, 2.5]]
    st["components"]["pump"]["npsh_required"] = [[0.006, 3.0], [0.002, 1.5]]
    assert "flows rising" in " ".join(validate_study(st)["errors"])
    st = copy.deepcopy(LIFT)
    del st["components"]["pump"]["npsh_required"]
    st["components"]["pump"]["npsh_cfdt"] = _sweep(0.004, 2900, "a") + _sweep(0.008, 2900, "b")
    net = build(st)
    assert not net.errors and net.elems["pump"]["params"]["_npsh_from"].startswith("cfd-translator")
    assert net.inputs["pump.npsh_cfdt"]["parameters"] == ["npsh_required"]
    st["components"]["tank"]["medium"] = {"rho": 1000, "cp": 4000, "p_vapour": 3000}
    assert validate_study(st)["ok"]


def test_suction_lift_warns_and_stays_reliable(tmp_path):
    """80 degC water lifted 3 m (tank level 1 m, pump 4 m above the port): NPSHa = (p_in + rho v^2/2 - p_vap) /
    (rho g) = 1.143 m against 1.1 x 2.39 m required."""
    out = run_study("lift", LIFT, tmp_path, solver="ref")
    assert out["ok"] and out["reliable"]
    b = out["npsh"]["pump"]
    rho, A = 995.586, math.pi / 4 * 0.04 ** 2
    v = out["outputs"]["m"]["final"] / (rho * A)
    expect = (b["p_in_at_min_Pa"] + rho * v * v / 2 - N.psat_water(b["T_in_at_min_K"])) / (rho * 9.80665)
    assert b["NPSHa_min_m"] == pytest.approx(expect, rel=1e-12) and b["short"]
    assert b["T_in_at_min_K"] == pytest.approx(353.15 - 9.80665 * 4.0 / 4184, abs=1e-6)   # the static pipe's rise
    assert any("would cavitate" in w for w in out["warnings"])
    st = copy.deepcopy(LIFT)
    st["components"]["pump"]["npsh_required"] = 0.5                         # enough: no warning
    out = run_study("ok", st, tmp_path / "ok", solver="ref")
    assert not out["npsh"]["pump"]["short"] and not any("cavitate" in w for w in out["warnings"])
