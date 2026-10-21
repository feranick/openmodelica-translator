"""test_matfile.py — the MAT v4 reader on files in OpenModelica's layout.
Version: 2026.10.21.2
"""
import numpy as np
import pytest

from openmodelica_translator import matfile


@pytest.mark.parametrize("trans", [True, False])
def test_roundtrip(tmp_path, trans):
    t = np.array([0.0, 0.5, 0.5, 1.0])               # an event: the time repeats
    p = tmp_path / "r.mat"
    matfile.write(p, t, {"C.v": [0, 1, 2, 3], "L.i": [1, 1, 0, 0]}, {"R.R": 10.0, "C.C": 1e-4}, trans=trans)
    r = matfile.Result(p)
    assert r.trans == trans
    assert r.names == ["time", "C.v", "L.i", "R.R", "C.C"]
    assert np.array_equal(r.time, t)
    assert np.array_equal(r.series("C.v"), [0, 1, 2, 3])
    assert r.value("R.R") == 10.0 and r.value("C.C") == 1e-4 and r.kind("R.R") == 1
    assert r.value("L.i") == 0.0 and np.all(r.series("R.R") == 10.0)


def test_negated_alias(tmp_path):
    p = tmp_path / "r.mat"
    matfile.write(p, [0, 1], {"a": [1, 2]}, {"k": 3.0})
    r = matfile.Result(p)
    r.names.append("b")
    r.index["b"] = len(r.names) - 1
    r.info = np.vstack([r.info, [2, -2, 0, -1], ])
    assert np.array_equal(r.series("b"), [-1, -2])


def test_not_a_result(tmp_path):
    p = tmp_path / "x.mat"
    p.write_bytes(np.array([0, 1, 1, 0, 2], "<i4").tobytes() + b"x\0" + np.array([1.0]).tobytes())
    with pytest.raises(ValueError):
        matfile.Result(p)
