"""test_catalog.py — the catalog is consistent, matches MSL 4.0.0, and the reference covers every variable.
Version: 2026.10.21.1
"""
import importlib.util
import os
from pathlib import Path

import pytest

from openmodelica_translator import catalog as C
from openmodelica_translator import ref as R
from openmodelica_translator.units import parse_unit

ROOT = Path(__file__).resolve().parents[1]


def test_kinds_are_consistent():
    for name, k in C.KINDS.items():
        pts = C.port_types(name)
        assert all(t in C.PORT_TYPES for t in pts.values()), name
        for p, s in k["params"].items():
            if s["unit"] != "*":            # '*': an untyped signal value, any unit
                parse_unit(s["unit"])
        if k.get("parts") is None:
            for i, s in k["inits"].items():
                parse_unit(s["unit"])
        if k.get("parts") is not None:
            for part, (pk, sub) in k["parts"].items():
                assert pk in C.KINDS
                for pp in sub:
                    assert pp in C.KINDS[pk]["params"], (name, part, pp)
            for a, b in k["conns"]:
                for x in (a, b):
                    part, port = x.split(".")
                    assert port in C.KINDS[k["parts"][part][0]]["ports"], (name, x)
            for ci, target in k["inits"].items():
                part, ii = target.split(".")
                assert ii in C.KINDS[k["parts"][part][0]]["inits"], (name, ci)
        else:
            assert k["msl"].startswith("Modelica."), name


def test_units_of_vars_parse():
    for name, k in C.KINDS.items():
        for v, u in k["vars"].items():
            parse_unit(u if isinstance(u, str) else u[1])


class _FakeNet:
    def __init__(self, kname, params):
        e = "X"
        self.elems = {e: {"kind": kname, "params": params, "inits": {}, "comp": "X", "part": None}}
        k = C.KINDS[kname]
        for i, s in k["inits"].items():
            d = s["default"]
            self.elems[e]["inits"][i] = (params.get(d, 0.0) if isinstance(d, str) else d, False)
        self.node_of, self.signal_of = {}, {}
        n = 0
        for p, t in k["ports"].items():
            keys = [f"{e}.{p}.pin[{j}]" for j in (1, 2, 3)] if t == "plug" else \
                [f"{e}.{p}[{j}]" for j in (1, 2, 3)] if t in ("vin", "vout") else [f"{e}.{p}"]
            for key in keys:
                (self.signal_of if t in ("in", "out", "vin", "vout") else self.node_of)[key] = n
                n += 1
        self.free_inputs = {}

    def port_type(self, ep):
        e, p = ep.split(".", 1)
        return "pin" if ".pin[" in p else C.KINDS[self.elems[e]["kind"]]["ports"][p]

    def mpath(self, ep):
        e, p = ep.split(".", 1)
        return f"{e}." + C.PORT_PATHS.get((self.elems[e]["kind"], p), p)


SAMPLE = {"table": [[0.0, 0.0], [1.0, 1.0]], "b": [1.0], "a": [1.0, 1.0], "controllerType": "PID", "law": "linear", "medium": "water", "friction": "quadratic", "V_flowNominal": 1.0, "V_flowLaminar": 0.1, "dpLaminar": 0.01,
          "TorqueDirection": False}


@pytest.mark.parametrize("kname", sorted(k for k, v in C.KINDS.items() if v.get("parts") is None))
def test_reference_covers_every_variable(kname):
    k = C.KINDS[kname]
    params = {}
    for p, s in k["params"].items():
        d = s["default"]
        params[p] = SAMPLE.get(p, 1.0 if d is C.REQ or d is None else d)
    for grp in k["alternatives"][1:]:
        for p in grp:
            params[p] = None
    net = _FakeNet(kname, params)
    sys_ = R.System()
    R._stamp(R._Ctx(sys_, net, "X"))
    have = set(sys_.paths) | set(sys_.products) | set(sys_.funcs)
    for v in k["vars"]:
        assert f"X.{C.var_path(kname, v)}" in have, f"{kname}.{v} has no reference value"
    for i, s in k["inits"].items():
        assert any(c["name"] == i or c["name"].startswith(i + "[") for c in sys_.inits), \
            f"{kname}.{i} is not a reference initial value"


def _msl_root():
    for cand in (os.environ.get("OMCT_MSL_SRC"), "/tmp/msl"):
        if cand and (Path(cand) / "Modelica").is_dir():
            return cand
    return None


@pytest.mark.skipif(_msl_root() is None, reason="no MSL 4.0.0 sources (set OMCT_MSL_SRC to a checkout of tag v4.0.0)")
def test_catalog_matches_msl_sources():
    spec = importlib.util.spec_from_file_location("check_catalog_msl", ROOT / "tools" / "check_catalog_msl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.check(_msl_root()) == []
