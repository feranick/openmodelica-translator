"""units.py — values given with units -> SI, recorded.
Version: 2026.10.21.1

A signal block's values (unit "*") take any unit and become SI ("2000 rpm" -> 209.4 rad/s).
A study value is a number (SI), a string "<number> <unit>" ("10 mH", "80 degC", "3000 rpm", "50 %"), or an
object {"value": 12.5, "unit": "N/mm", "from": "feat", "what": "..."} (chains: the same, plus provenance).
to_si(value, si_unit) returns (number in SI, record or None) or raises UnitError with a message saying the fix.

Dimensions are exponents of (kg, m, s, A, K, cd-free). Angles are dimensionless (SI), but angular velocity and
frequency are kept apart on purpose: a value for a rad/s parameter given in Hz (or the reverse) is an error,
because the factor 2*pi would otherwise be lost silently.
"""
from __future__ import annotations

import math
import re

# name: (factor to SI, dims (kg, m, s, A, K), offset to SI (temperatures only))
_D0 = (0, 0, 0, 0, 0)
UNITS = {
    "1": (1.0, _D0), "%": (0.01, _D0), "rad": (1.0, _D0), "deg": (math.pi / 180, _D0), "rev": (2 * math.pi, _D0),
    "m": (1.0, (0, 1, 0, 0, 0)), "g": (1e-3, (1, 0, 0, 0, 0)), "t": (1e3, (1, 0, 0, 0, 0)),
    "s": (1.0, (0, 0, 1, 0, 0)), "min": (60.0, (0, 0, 1, 0, 0)), "h": (3600.0, (0, 0, 1, 0, 0)),
    "A": (1.0, (0, 0, 0, 1, 0)), "K": (1.0, (0, 0, 0, 0, 1)),
    "Hz": (1.0, (0, 0, -1, 0, 0)), "rpm": (2 * math.pi / 60, (0, 0, -1, 0, 0)),
    "N": (1.0, (1, 1, -2, 0, 0)), "J": (1.0, (1, 2, -2, 0, 0)), "W": (1.0, (1, 2, -3, 0, 0)),
    "Pa": (1.0, (1, -1, -2, 0, 0)), "bar": (1e5, (1, -1, -2, 0, 0)),
    "V": (1.0, (1, 2, -3, -1, 0)), "Ohm": (1.0, (1, 2, -3, -2, 0)), "S": (1.0, (-1, -2, 3, 2, 0)),
    "F": (1.0, (-1, -2, 4, 2, 0)), "H": (1.0, (1, 2, -2, -2, 0)), "C": (1.0, (0, 0, 1, 1, 0)),
    "Wb": (1.0, (1, 2, -2, -1, 0)), "l": (1e-3, (0, 3, 0, 0, 0)), "L": (1e-3, (0, 3, 0, 0, 0)),
}
ALIASES = {"ohm": "Ohm", "Ω": "Ohm", "°": "deg", "rad/s": None}
PREFIXES = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "c": 1e-2, "k": 1e3, "M": 1e6,
            "G": 1e9}
# temperatures with an offset: only alone, for absolute temperatures
OFFSET_UNITS = {"degC": (1.0, 273.15), "°C": (1.0, 273.15), "C°": (1.0, 273.15), "degF": (5 / 9, 459.67 * 5 / 9),
                "°F": (5 / 9, 459.67 * 5 / 9)}
_NUM = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$")


class UnitError(ValueError):
    pass


def _symbol(sym: str):
    """'mH' -> (1e-3, dims of H). Exact names first, then prefix + name."""
    sym = ALIASES.get(sym, sym) or sym
    if sym in UNITS:
        return UNITS[sym]
    if len(sym) > 1 and sym[0] in PREFIXES and sym[1:] in UNITS and sym[1:] not in ("1", "%", "min", "h", "rpm"):
        f, d = UNITS[sym[1:]]
        return PREFIXES[sym[0]] * f, d
    if sym.startswith("da") and sym[2:] in UNITS:
        f, d = UNITS[sym[2:]]
        return 10 * f, d
    raise UnitError(f"unknown unit '{sym}'")


def _term(tok: str):
    m = re.match(r"^([^\d^+-]+?)\^?([-+]?\d+)?$", tok)
    if not m:
        raise UnitError(f"cannot read unit '{tok}'")
    f, d = _symbol(m.group(1))
    p = int(m.group(2)) if m.group(2) else 1
    return f ** p, tuple(x * p for x in d)


def parse_unit(text: str):
    """'N/mm' -> (1000.0, dims). Products with '.', '*' or a space, one '/' (the rest in the denominator, with
    optional parentheses), exponents as m2, m^2, s-1."""
    t = text.strip().replace("·", ".").replace("**", "^")
    if t in ("", "1"):
        return 1.0, _D0
    num, _, den = t.partition("/")
    den = den.strip()
    if den.startswith("(") and den.endswith(")"):
        den = den[1:-1]
    if "/" in den or "(" in num or ")" in num:
        raise UnitError(f"cannot read unit '{text}': use at most one '/' (e.g. W/(m2.K))")
    fac, dims = 1.0, [0] * 5
    for part, sign in ((num, 1), (den, -1)):
        for tok in re.split(r"[.* ]+", part.strip()):
            if not tok or tok == "1":
                continue
            f, d = _term(tok)
            fac *= f ** sign
            dims = [a + sign * b for a, b in zip(dims, d)]
    return fac, tuple(dims)


def _is_angular(unit_text: str) -> bool:
    return "rad" in unit_text or "rpm" in unit_text or "rev" in unit_text or "deg" in unit_text


def to_si(value, si_unit: str, *, absolute_temperature: bool = False, where: str = ""):
    """(SI number, record or None). si_unit is the parameter's SI unit as written in the catalog."""
    rec = None
    given = value
    if isinstance(value, dict):
        if "value" not in value:
            raise UnitError(f"{where}: an object value needs 'value' (and 'unit'), e.g. {{\"value\": 12, \"unit\": \"N/mm\"}}")
        unknown = set(value) - {"value", "unit", "from", "what", "request", "quantity"}
        if unknown:
            raise UnitError(f"{where}: unknown keys {sorted(unknown)} in a value object (value, unit, from, what, quantity, request)")
        rec = {k: value[k] for k in ("from", "what", "quantity", "request") if k in value}
        num, unit = value["value"], str(value.get("unit", "") or "")
        if not isinstance(num, (int, float)) or isinstance(num, bool):
            raise UnitError(f"{where}: 'value' must be a number")
    elif isinstance(value, str):
        m = _NUM.match(value)
        if not m:
            raise UnitError(f"{where}: '{value}' is not '<number> <unit>' (e.g. '10 mH', '80 degC')")
        num, unit = float(m.group(1)), m.group(2)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), None
    else:
        raise UnitError(f"{where}: a number (SI), '<number> <unit>' or {{value, unit}}")
    num = float(num)
    unit = unit.strip()
    if si_unit == "*":                      # an untyped signal value: any unit, converted to SI
        if unit in OFFSET_UNITS:
            f, off = OFFSET_UNITS[unit]
            si = num * f + off
        else:
            try:
                si = float(f"{num * parse_unit(unit)[0]:.15g}")
            except UnitError as exc:
                raise UnitError(f"{where}: {exc}") from None
        rec = rec or {}
        rec.update({"given": given, "si": si, "unit": "SI"})
        return si, rec
    if unit in OFFSET_UNITS:
        if si_unit != "K" or not absolute_temperature:
            raise UnitError(f"{where}: {unit} is only for absolute temperatures; this parameter is in {si_unit}")
        f, off = OFFSET_UNITS[unit]
        si = num * f + off
    else:
        try:
            f, d = parse_unit(unit)
            fs, ds = parse_unit(si_unit)
        except UnitError as exc:
            raise UnitError(f"{where}: {exc}") from None
        if d != ds:
            raise UnitError(f"{where}: '{unit}' does not fit this parameter's unit {si_unit}")
        if "Hz" in si_unit and _is_angular(unit) or (_is_angular(si_unit) and "/s" in si_unit and "Hz" in unit):
            raise UnitError(f"{where}: '{unit}' for a parameter in {si_unit}: frequencies (Hz) and angular velocities "
                            f"(rad/s, rpm) differ by 2*pi; give the value in {si_unit}")
        si = float(f"{num * f / fs:.15g}")       # 100 uF -> 1e-4, not 9.999999999999999e-05
    if rec is None:
        rec = {}
    rec.update({"given": given, "si": si, "unit": si_unit})
    return si, rec
