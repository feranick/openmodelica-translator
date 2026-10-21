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


def test_chains_skip_without_the_other_translator(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__

    def no_feat(name, *a, **k):
        if name.startswith(("fea_translator", "build123d_translator")):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_feat)
    r = bench.run("ref", ["chain_feat_modal", "chain_b3dt_mass"], tmp_path)
    assert "needs fea_translator" in r["chain_feat_modal"]["skipped"]
    assert "needs build123d_translator" in r["chain_b3dt_mass"]["skipped"]


def test_chain_feat_modal_with_a_stand_in_feat(tmp_path, monkeypatch):
    """feat replaced by exact Euler-Bernoulli answers: Rayleigh here must be 1.0146 x feat's frequency."""
    import math
    import sys
    import types
    E, L, b, h, rho = 210000.0, 100.0, 10.0, 10.0, 7850.0
    I = b * h ** 3 / 12
    u = 100 * L ** 3 / (3 * E * I)                                        # mm
    m = rho * L * b * h * 1e-9
    f1 = 1.875104068711961 ** 2 / (2 * math.pi) * math.sqrt(E * 1e6 * I * 1e-12 / (m / (L * 1e-3) * (L * 1e-3) ** 4))
    fake_api = types.SimpleNamespace(run=lambda st, solver, wait: {"studies": {
        "cantilever": {"ok": True, "reliable": True, "faces": {"tip": {"mean_u": {"uz": -u}}}},
        "cantilever_modal": {"ok": True, "reliable": True, "frequencies": [f1, f1], "mass_kg": m}}})
    fake_bench = types.SimpleNamespace(cases=lambda: {"cantilever": {"expect": []}, "cantilever_modal": {}})
    pkg = types.ModuleType("fea_translator")
    pkg.api, pkg.bench = fake_api, fake_bench
    monkeypatch.setitem(sys.modules, "fea_translator", pkg)
    monkeypatch.setitem(sys.modules, "fea_translator.api", fake_api)
    monkeypatch.setitem(sys.modules, "fea_translator.bench", fake_bench)
    r = bench.run("ref", ["chain_feat_modal"], tmp_path)["chain_feat_modal"]
    assert r["passed"], r
    got = r["checks"][0]["got"]
    assert got / f1 == pytest.approx(1.01465, abs=2e-5)
