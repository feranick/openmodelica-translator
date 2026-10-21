"""test_bench.py — the benchmarks' expected values against the reference implementation, and their evaluation.
Version: 2026.10.21.4
"""
import pytest

from openmodelica_translator import bench
from openmodelica_translator.format import validate_study


@pytest.mark.parametrize("name", list(bench.cases()))
def test_case_valid_and_passes_with_ref(name, tmp_path):
    c = bench.cases()[name]
    assert validate_study(c["study"])["ok"]
    r = bench.run("ref", [name], tmp_path)[name]
    assert r["passed"], r


def test_evaluate_fails_on_unreliable_and_wrong_values():
    cs = bench.cases()
    res = {"ok": True, "reliable": True, "outputs": {"w": {"final": 3.6}, "phi": {"final": 1.62}}}
    assert bench.evaluate("inertia_torque_step", res, cs)["passed"]
    res["outputs"]["w"]["final"] = 3.7
    assert not bench.evaluate("inertia_torque_step", res, cs)["passed"]
    res = {"ok": True, "reliable": False, "unreliable_because": ["energy"], "outputs": {"w": {"final": 3.6}, "phi": {"final": 1.62}}}
    e = bench.evaluate("inertia_torque_step", res, cs)
    assert not e["passed"] and e["why"] == ["energy"]
