"""matfile.py — OpenModelica's MAT v4 result files: read (and write, for tests).
Version: 2026.10.21.2

Layout (OpenModelica UsersGuide, "The MATv4 Result File Format"; measured on mochi with 1.27.1): matrices
Aclass (row 4 'binTrans': every matrix stored transposed), name, description, dataInfo (per variable: matrix
0 = time / 1 = data_1 / 2 = data_2, signed 1-based row, negative = negated alias), data_1 (parameters: values at
start and stop), data_2 (time series; row 1 is time). With binTrans, data_2 is stored as one column per time
point: n_var rows x n_times columns. Events add time points (the values before and after).
Parameters are always written, whatever -variableFilter says (measured), so overrides can be read back.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_DT = {0: "<f8", 1: "<f4", 2: "<i4", 3: "<i2", 4: "<u2", 5: "<u1"}


def _read_matrices(path):
    raw = Path(path).read_bytes()
    out, pos = {}, 0
    while pos + 20 <= len(raw):
        mopt, m, n, imagf, namlen = np.frombuffer(raw, "<i4", 5, pos)
        pos += 20
        if mopt >= 1000 or imagf:
            raise ValueError(f"{path}: not a little-endian real MAT v4 file")
        name = raw[pos:pos + namlen].rstrip(b"\0").decode()
        pos += namlen
        dt = np.dtype(_DT[(mopt // 10) % 10])
        cnt = int(m) * int(n)
        a = np.frombuffer(raw, dt, cnt, pos).reshape((int(n), int(m))).T      # column-major
        pos += cnt * dt.itemsize
        out[name] = a
    return out


def _strings(a):
    return ["".join(chr(int(c)) for c in row).rstrip("\0 ") for row in a]


class Result:
    """Names, parameters and time series of one result file."""

    def __init__(self, path):
        mats = _read_matrices(path)
        for k in ("Aclass", "name", "dataInfo", "data_2"):
            if k not in mats:
                raise ValueError(f"{path}: no '{k}' matrix (not an OpenModelica result file?)")
        aclass = _strings(mats["Aclass"])
        self.trans = len(aclass) > 3 and aclass[3].startswith("binTrans")
        tr = (lambda a: a.T) if self.trans else (lambda a: a)
        self.names = _strings(tr(mats["name"]))
        self.info = tr(mats["dataInfo"]).astype(int)            # n_var x 4
        d1 = mats.get("data_1")
        self.d1 = tr(d1).T if d1 is not None else np.zeros((0, 2))   # rows = variables
        self.d2 = tr(mats["data_2"]).T                                # rows = variables, columns = times
        self.index = {n: i for i, n in enumerate(self.names)}
        self.time = self.d2[0].astype(float)

    def __contains__(self, name):
        return name in self.index

    def kind(self, name) -> int:
        return int(self.info[self.index[name], 0])

    def series(self, name) -> np.ndarray:
        """Values of a variable at every time point (a parameter: constant)."""
        i = self.index[name]
        k, col = int(self.info[i, 0]), int(self.info[i, 1])
        s = -1.0 if col < 0 else 1.0
        if k == 0:
            return self.time.copy()
        if k == 1:
            return np.full(self.time.shape, s * float(self.d1[abs(col) - 1, 0]))
        return s * self.d2[abs(col) - 1].astype(float)

    def value(self, name) -> float:
        """A parameter's value (data_1), or a variable's final value."""
        i = self.index[name]
        k, col = int(self.info[i, 0]), int(self.info[i, 1])
        s = -1.0 if col < 0 else 1.0
        if k == 1:
            return s * float(self.d1[abs(col) - 1, 0])
        return float(self.series(name)[-1])


def write(path, time, series: dict, params: dict, trans=True):
    """A result file in OpenModelica's layout (for tests): series {name: array}, params {name: value}."""
    names = ["time"] + list(series) + list(params)
    L = max(len(n) for n in names)
    nm = np.array([[ord(c) for c in n.ljust(L, "\0")] for n in names], np.uint8)
    info = [[0, 1, 0, -1]] + [[2, i + 2, 0, -1] for i in range(len(series))] + \
           [[1, i + 2, 0, 0] for i in range(len(params))]
    info = np.array(info, np.int32)
    t = np.asarray(time, float)
    d2 = np.vstack([t] + [np.asarray(v, float) for v in series.values()])
    d1 = np.vstack([[t[0], t[-1]]] + [[float(v), float(v)] for v in params.values()])
    aclass = np.array([[ord(c) for c in s.ljust(11)] for s in ("Atrajectory", "1.1", "", "binTrans" if trans else "binNormal")], np.uint8)
    mats = [("Aclass", aclass, False), ("name", nm, trans), ("description", nm * 0, trans), ("dataInfo", info, trans),
            ("data_1", d1, not trans), ("data_2", d2, not trans)]
    with open(path, "wb") as f:
        for name, a, store_t in mats:
            a = a.T if store_t else a              # what is stored, row-major logical
            P = {np.dtype(np.uint8): 5, np.dtype(np.int32): 2, np.dtype(float): 0}[a.dtype]
            T = 1 if a.dtype == np.uint8 else 0
            m, n = a.shape
            f.write(np.array([P * 10 + T, m, n, 0, len(name) + 1], "<i4").tobytes())
            f.write(name.encode() + b"\0")
            f.write(np.ascontiguousarray(a.T).astype(a.dtype.newbyteorder("<")).tobytes())
