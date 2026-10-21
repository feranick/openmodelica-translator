"""test_units.py — value strings and objects to SI.
Version: 2026.10.21.1
"""
import math

import pytest

from openmodelica_translator.units import UnitError, parse_unit, to_si


@pytest.mark.parametrize("given, unit, si", [
    (12, "V", 12.0), ("10 mH", "H", 0.01), ("100 uF", "F", 1e-4), ("4.7 kOhm", "Ohm", 4700.0),
    ("50 mm", "m", 0.05), ("2.5 g", "kg", 0.0025), ("3000 rpm", "rad/s", 3000 * 2 * math.pi / 60),
    ("1.2 N/mm", "N/m", 1200.0), ("10 W/(m2.K)", "W/(m2.K)", 10.0), ("5 kg.m2", "kg.m2", 5.0),
    ("50 %", "%", 50.0), ("1 kHz", "Hz", 1000.0), ("2 min", "s", 120.0), ("1 bar", "Pa", 1e5),
    ("90 deg", "rad", math.pi / 2), ("0.05 N.m/A", "N.m/A", 0.05), ("3 ms", "s", 0.003),
    ({"value": 1.5, "unit": "N/mm", "from": "feat"}, "N/m", 1500.0),
])
def test_to_si(given, unit, si):
    v, rec = to_si(given, unit)
    assert v == pytest.approx(si, rel=1e-14)
    if not isinstance(given, (int, float)):
        assert rec["si"] == v and rec["unit"] == unit


def test_temperatures():
    assert to_si("80 degC", "K", absolute_temperature=True)[0] == pytest.approx(353.15)
    assert to_si("212 degF", "K", absolute_temperature=True)[0] == pytest.approx(373.15)
    assert to_si("300 K", "K", absolute_temperature=True)[0] == 300.0
    with pytest.raises(UnitError):
        to_si("20 degC", "W/K")


def test_provenance_kept():
    _, rec = to_si({"value": 2.0, "unit": "kg", "from": "b3dt", "what": "bracket mass"}, "kg")
    assert rec["from"] == "b3dt" and rec["what"] == "bracket mass"


@pytest.mark.parametrize("given, unit, msg", [
    ("50 Hz", "rad/s", "2*pi"), ("100 rpm", "Hz", "2*pi"), ("3 m", "s", "does not fit"),
    ("10 furlongs", "m", "unknown unit"), ("ten V", "V", "<number> <unit>"), ({"unit": "V"}, "V", "value"),
    ({"value": 1, "unit": "V", "bogus": 1}, "V", "unknown keys"), (True, "V", "number"),
])
def test_errors(given, unit, msg):
    with pytest.raises(UnitError, match=msg.replace("*", r"\*").replace("<", "<")):
        to_si(given, unit, where="x")


def test_parse_unit_dims():
    f, d = parse_unit("N.m.s/rad")
    assert f == 1.0 and d == (1, 2, -1, 0, 0)
    assert parse_unit("mm")[0] == pytest.approx(1e-3)
    assert parse_unit("min")[0] == 60.0


def test_untyped_signal_values():
    assert to_si("2000 rpm", "*")[0] == pytest.approx(2000 * math.pi / 30)
    assert to_si("80 degC", "*")[0] == pytest.approx(353.15)
    assert to_si(3, "*")[0] == 3.0
