"""test_npsh.py — pumps' NPSH (V5 FL16, FL17): vapour pressures, NPSH3 (known or an upper bound) from cfd-translator
sweeps, the flow scaling, statuses, eye onset (vapour, air release; FL18: filtered or raw amplitude, jitter flags,
the eye minimum as a fraction of the air-saturation pressure; FL19: revolutions after the averaging start, the
minimum below the mean), the run's npsh block.
Version: 2026.11.20.2
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
    assert r["known"] == [[0.004, pytest.approx(n3)], [0.008, pytest.approx(n3)]] and r["below"] == []
    s = 1200 / 1450                                                          # a sweep at 1200 rpm, moved to 1450
    r = N.from_cfdt(_sweep(0.004, 1450, "a") + _sweep(0.006 * s, 1200, "c", s), "p")
    assert r["known"][1] == [pytest.approx(0.006), pytest.approx(n3)]
    bad = _sweep(0.004, 1450, "a")
    bad[3]["results"]["cavitation"]["flows"]["liquid_flow_error"] = 0.05       # left out, as cfd-translator does
    assert N.npsh3([{"npsh": p["results"]["cavitation"]["npsh_m"], "head": p["results"]["pump"]["head_m"],
                     "flow_error": p["results"]["cavitation"]["flows"]["liquid_flow_error"]} for p in bad]) \
        == (pytest.approx(3 - (19.7 - 19.4) / (19.7 - 15) * 1.0), "at")
    # no 3 % drop down to 2 m at 20 m3/h: NPSH3 below 2 m there (an upper bound), with a known one at 10 m3/h
    held = [_cav(20 / 3600, 1450, n, 20.0, f"x{n}") for n in (6, 4, 2)]
    r = N.from_cfdt(_sweep(10 / 3600, 1450, "a") + held, "p")
    assert r["below"] == [[pytest.approx(20 / 3600), 2.0]] and len(r["known"]) == 1
    assert r["record"]["sweeps"][1]["kind"] == "below"
    with pytest.raises(N.NpshError, match="fewer than two points"):
        N.from_cfdt([_cav(0.004, 1450, 6, 20, "x6")], "p")
    with pytest.raises(N.NpshError, match="cavitation study"):
        N.from_cfdt([{"name": "q", "results": {"pump": {"head_m": 20}}}], "p")


def test_npsh3_between_flows():
    """Linear between the flows given, held below the lowest, x (V/V_max)^n above; a bound if either side is."""
    c = N.merge_curve([[0.004, 2.0]], [[0.008, 3.0]])
    assert N.npshr(c, 0.002) == (2.0, False, "held below the lowest flow")
    assert N.npshr(c, 0.004)[:2] == (2.0, False)
    assert N.npshr(c, 0.006)[:2] == (pytest.approx(2.5), True)
    assert N.npshr(c, 0.016)[:2] == (pytest.approx(12.0), True)
    assert N.npshr(c, 0.016, 1.0)[0] == pytest.approx(6.0)
    assert N.npshr([[0.0, 2.5, False]], 0.1) == (2.5, False, "constant")
    with pytest.raises(N.NpshError, match="twice"):
        N.merge_curve([[0.004, 2.0]], [[0.004, 3.0]])
    d = N.describe(c, 2.0)
    assert "2 m at 14.4 m3/h" in d and "< 3 m at 28.8 m3/h" in d and "(V/V_max)^2" in d
    assert N._status(0.9, False, 1.5) == "below" and N._status(1.2, False, 1.5) == "approaching"
    assert N._status(1.6, False, 1.5) == "ok" and N._status(1.6, True, 1.5) == "ok"
    assert N._status(1.2, True, 1.5) == "unresolved" and N._status(0.5, True, 1.5) == "unresolved"


def _free_flow(dip=3000.0, amp=1200.0, rpm=1450, filtered=True, jitter=3000.0):
    """The eye probe per revolution: raw extremes +-(amp + jitter), filtered (min_f, max_f) +-amp."""
    revs = [{"mean": 5000.0 - dip + d, "min": 5000.0 - dip + d - amp - jitter, "max": 5000.0 - dip + d + amp + jitter,
             **({"min_f": 5000.0 - dip + d - amp, "max_f": 5000.0 - dip + d + amp} if filtered else {})}
            for d in (-10, 10)]
    inlet = [{"mean": 5000.0, "min": 4900, "max": 5100}] * 2
    return {"name": "s50_ff", "reliable": True,
            "results": {"rotating": {"impeller": {"rpm": rpm}},
                        "free_flow": {"probes_p_Pa_by_revolution": {"eye": revs},
                                      "pressure_by_revolution": {"inlet": {"static_Pa": inlet}},
                                      "flow_m3h_by_revolution": [{"mean": 20.0}, {"mean": 20.0}]}}}


# rung 2 g 5, ESI at 1 degree, free_flow from t = 0 (spark1 job 756, cfd-translator 2026.11.20.1): the eye probe
# [20, 0, 16] and the inlet's static pressure per revolution, as reported
RUNG2 = {"name": "r2_g5_ff_esi", "reliable": False, "results": {
    "rotating": {"imp": {"rpm": 2900.0}}, "averaged": {"settings": {"from": 0.02068968966}},
    "free_flow": {"switched_at_s": 0.0,
                  "flow_m3h_by_revolution": [{"rev": 1, "mean": 19.3757}, {"rev": 2, "mean": 20.2484},
                                             {"rev": 3, "mean": 20.0339}],
                  "pressure_by_revolution": {"inlet": {"static_Pa": [
                      {"rev": 1, "mean": 229846.0, "min": 229546.0, "max": 230064.0},
                      {"rev": 2, "mean": 229498.0, "min": 229321.0, "max": 229682.0},
                      {"rev": 3, "mean": 229585.0, "min": 229437.0, "max": 229691.0}]}},
                  "probes_p_Pa_by_revolution": {"eye": [
                      {"rev": 1, "mean": 216584.0, "min": 181527.0, "max": 248435.0, "min_f": 182020.0, "max_f": 247163.0},
                      {"rev": 2, "mean": 222624.0, "min": 193569.0, "max": 238546.0, "min_f": 194541.0, "max_f": 237515.0},
                      {"rev": 3, "mean": 222218.0, "min": 196106.0, "max": 236815.0, "min_f": 197472.0,
                       "max_f": 235556.0}]}}}}


def test_onset_from_a_real_free_flow_run():
    """Revolutions 2-3 (from the averaging start; revolution 1 holds the start from rest), the amplitude as the
    eye's minimum below its mean (26.4 kPa against 20.3 kPa half peak-to-peak: the pulse is not symmetric)."""
    r = N.onset_from_cfdt({"result": RUNG2, "eye_probe": "eye", "inlet": "inlet"}, "p")
    rec = r["record"]
    assert rec["revolutions"] == [2, 3] and rec["revolutions_from"].startswith("from the averaging start")
    assert r["eye_dip"] == pytest.approx((229498 + 229585) / 2 - (222624 + 222218) / 2)               # 7120.5 Pa
    assert r["pulse_amplitude"] == pytest.approx(((222624 - 194541) + (222218 - 197472)) / 2)          # 26414.5 Pa
    assert rec["half_peak_to_peak_Pa"] == pytest.approx(((237515 - 194541) + (235556 - 197472)) / 4)   # 20264.5 Pa
    assert r["V_flow"] == pytest.approx((20.2484 + 20.0339) / 2 / 3600) and not r["raw"]
    a = N.onset_from_cfdt({"result": RUNG2, "eye_probe": "eye", "inlet": "inlet", "revolutions": [1, 2, 3]}, "p")
    assert a["record"]["revolutions_from"] == "given" and a["eye_dip"] > r["eye_dip"] + 2000
    with pytest.raises(N.NpshError, match="revolutions"):
        N.onset_from_cfdt({"result": RUNG2, "eye_probe": "eye", "inlet": "inlet", "revolutions": [4]}, "p")
    late = copy.deepcopy(RUNG2)
    late["results"]["averaged"]["settings"]["from"] = 0.07
    with pytest.raises(N.NpshError, match="no whole revolution"):
        N.onset_from_cfdt({"result": late, "eye_probe": "eye", "inlet": "inlet"}, "p")


def test_onset_from_cfdt():
    r = N.onset_from_cfdt({"result": _free_flow(), "eye_probe": "eye", "inlet": "inlet"}, "p")
    assert r["eye_dip"] == pytest.approx(3000) and r["pulse_amplitude"] == pytest.approx(1200) and not r["raw"]
    assert r["record"]["inlet_pressure"] == "static_Pa mean"
    raw = N.onset_from_cfdt({"result": _free_flow(filtered=False), "eye_probe": "eye", "inlet": "inlet"}, "p")
    assert raw["raw"] and raw["pulse_amplitude"] == pytest.approx(4200) and raw["eye_dip"] == pytest.approx(3000)
    assert r["rpm"] == 1450 and r["V_flow"] == pytest.approx(20 / 3600)
    with pytest.raises(N.NpshError, match="probes_p_Pa_by_revolution"):
        N.onset_from_cfdt({"result": _free_flow(), "eye_probe": "nope", "inlet": "inlet"}, "p")


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
    st = copy.deepcopy(LIFT)                                               # an upper bound beside known values
    st["components"]["pump"]["npsh3_below"] = [[0.008, 2.0]]
    p = build(st).elems["pump"]["params"]
    assert p["_npsh_curve"][1] == [0.006, 3.0, False] and p["_npsh_curve"][2] == [0.008, 2.0, True]
    st["components"]["pump"]["npsh3_below"] = [[0.006, 2.0]]
    assert "twice" in " ".join(validate_study(st)["errors"])
    st["components"]["pump"]["npsh3_below"] = 2.0                          # a number with flows: ambiguous
    assert "plain number" in " ".join(validate_study(st)["errors"])
    del st["components"]["pump"]["npsh_required"]
    assert build(st).elems["pump"]["params"]["_npsh_curve"] == [[0.0, 2.0, True]]
    st = copy.deepcopy(LIFT)                                               # onset from a cfd-translator run
    st["components"]["pump"]["onset_cfdt"] = {"result": _free_flow(rpm=2900), "eye_probe": "eye", "inlet": "inlet"}
    st["components"]["pump"]["pulse_amplitude"] = 500                      # a given value wins
    net = build(st)
    p = net.elems["pump"]["params"]
    assert not net.errors and p["eye_dip"] == pytest.approx(3000) and p["pulse_amplitude"] == 500
    assert p["onset_N"] == pytest.approx(2900 * math.pi / 30)
    assert net.inputs["pump.onset_cfdt"]["parameters"] == ["eye_dip", "onset_N", "onset_V_flow"]
    assert p["_onset_raw"] is False                                         # the amplitude was given
    del st["components"]["pump"]["pulse_amplitude"]
    st["components"]["pump"]["onset_cfdt"]["result"] = _free_flow(rpm=2900, filtered=False)
    assert build(st).elems["pump"]["params"]["_onset_raw"] is True


def test_suction_lift_warns_and_stays_reliable(tmp_path):
    """80 degC water lifted 3 m (tank level 1 m, pump 4 m above the port): NPSHa = (p_in + rho v^2/2 - p_vap) /
    (rho g) = 1.143 m against NPSH3 2.39 m: 'below'. Onset: p_in - dip - amplitude against p_vap and p_ambient."""
    st = copy.deepcopy(LIFT)
    st["components"]["pump"].update({"eye_dip": 3000, "pulse_amplitude": 1500})
    out = run_study("lift", st, tmp_path, solver="ref")
    assert out["ok"] and out["reliable"]
    b = out["npsh"]["pump"]
    rho, A = 995.586, math.pi / 4 * 0.04 ** 2
    v = out["outputs"]["m"]["final"] / (rho * A)
    expect = (b["p_in_at_min_Pa"] + rho * v * v / 2 - N.psat_water(b["T_in_at_min_K"])) / (rho * 9.80665)
    assert b["NPSHa_min_m"] == pytest.approx(expect, rel=1e-12) and b["short"] and b["status"] == "below"
    V = b["V_flow_at_ratio_min_m3h"] / 3600                                 # linear between 0.002 and 0.006 m3/s
    assert b["NPSH3_at_ratio_min_m"] == pytest.approx(1.5 + (V - 0.002) / 0.004 * 1.5, rel=1e-12)
    assert b["T_in_at_min_K"] == pytest.approx(353.15 - 9.80665 * 4.0 / 4184, abs=1e-6)   # the static pipe's rise
    w = " ".join(out["warnings"])
    assert "below NPSH3" in w and "7.2 m3/h" in w and "(V/V_max)^2" in w
    on = b["onset"]
    assert on["p_eye_min_Pa"] == pytest.approx(b["p_in_at_min_Pa"] - 4500, rel=1e-12)
    assert not on["cavitation_onset"] and on["air_release"] and "air release" in w
    assert on["air_margin_min_Pa"] == pytest.approx(b["p_in_at_min_Pa"] - 4500 - 101325, rel=1e-12)
    assert on["eye_min_over_air_saturation"] == pytest.approx((b["p_in_at_min_Pa"] - 4500) / 101325, rel=1e-12)
    assert "judge by the fraction" in w and "jitter_Pa" not in on and "possibly jitter" not in w
    st["components"]["pump"]["eye_dip"] = 6000                             # now below p_vap too
    st["components"]["pump"]["air_saturation_pressure"] = 4e4               # degassed: no air release
    out = run_study("v", st, tmp_path / "v", solver="ref")
    on = out["npsh"]["pump"]["onset"]
    assert on["cavitation_onset"] and not on["air_release"] and "cavitation onset" in " ".join(out["warnings"])
    for given, status in ((1.0, "approaching"), (0.5, "ok")):               # 1.14 / 1.0 < 1.5; 1.14 / 0.5 > 1.5
        st = copy.deepcopy(LIFT)
        st["components"]["pump"]["npsh_required"] = given
        b = run_study(status, st, tmp_path / status, solver="ref")["npsh"]["pump"]
        assert b["status"] == status and b["short"] == (status != "ok") and "onset" not in b
    st = copy.deepcopy(LIFT)                                                # an upper bound only: unresolved
    del st["components"]["pump"]["npsh_required"]
    st["components"]["pump"]["npsh3_below"] = [[20 / 3600, 2.0]]
    out = run_study("ub", st, tmp_path / "ub", solver="ref")
    b = out["npsh"]["pump"]
    assert b["status"] == "unresolved" and b["NPSH3_is_upper_bound"] and not b["short"]
    assert "only known to be below 2 m" in " ".join(out["warnings"]) and out["reliable"]


def test_raw_amplitude_and_jitter_flags(tmp_path):
    """Raw cfd-translator extremes: an upper bound, warned; a trip within 3 kPa of its threshold: possibly jitter.
    p_in 52.49 kPa, p_vap 47.40 kPa: dip 3 kPa + raw amplitude 3.5 kPa trips the vapour pressure by 1.4 kPa."""
    st = copy.deepcopy(LIFT)
    ff = _free_flow(dip=3000.0, amp=500.0, rpm=2900, filtered=False)            # raw +-3.5 kPa
    st["components"]["pump"]["onset_cfdt"] = {"result": ff, "eye_probe": "eye", "inlet": "inlet"}
    out = run_study("raw", st, tmp_path, solver="ref")
    on = out["npsh"]["pump"]["onset"]
    w = " ".join(out["warnings"])
    assert on["amplitude_upper_bound"] and on["jitter_Pa"] == 3000.0 and on["pulse_amplitude_Pa"] == pytest.approx(3500)
    assert on["cavitation_onset"] and on["cavitation_onset_possibly_jitter"] and not on["air_release_possibly_jitter"]
    assert "min_f/max_f" in w and "possibly jitter" in w and out["reliable"]
    st["components"]["pump"]["onset_cfdt"]["result"] = _free_flow(dip=3000.0, amp=500.0, rpm=2900)   # filtered
    on = run_study("f", st, tmp_path / "f", solver="ref")["npsh"]["pump"]["onset"]
    assert not on["cavitation_onset"] and "jitter_Pa" not in on and "amplitude_upper_bound" not in on
