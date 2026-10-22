"""ref.py — an independent implementation of the study's equations, without OpenModelica.
Version: 2026.10.21.25

Built from the study (network.Net), never from the generated Modelica text, so a translation mistake shows up as
a mismatch. Each catalog kind writes its equations (the MSL 4.0.0 ones, read from the library's sources) into a
linear descriptor system in residual form

    A z + B dz/dt + G F(t) = 0

with z: node potentials, port flows, signals and component variables; F: the sources' time functions and their
derivatives. Then:
  - index reduction (the shuffle algorithm: rows whose derivative part vanishes are constraints; they are kept,
    and their time derivative replaces them) until B is regular; the constraints define the consistent states;
  - the dynamic degrees of freedom d = n - rank(constraints), and their coordinates w (z = z_p(t) + V w);
  - init_selection(): which initial values (capacitor voltages, mass positions and speeds, ...) are independent:
    exactly these get fixed=true in the Modelica model, the others are left to follow;
  - simulate(): piecewise between the sources' breakpoints (solve_ivp Radau, rtol 1e-10), the storage values
    carried across each breakpoint;
  - eigenvalues(), steady(), freq_response() (from the descriptor system: (A + j w B) Z = -G_u).
Coolant loops (Thermal.FluidHeatFlow): hydraulics() solves the pressures and flows first (algebraic, Newton on the
MSL friction, valve and source relations, constant sources); then the temperatures are linear in the system above
with those flows (upwind enthalpy flows, mixing at junctions, m cv dT/dt per component).
Nonlinear kinds (diode, limiters, LimPID, radiation, product, ...) get a linear stand-in with the same structure:
enough for init_selection and the singularity check, never for numbers (covered = False).
"""
from __future__ import annotations

import math
import os
import warnings

import numpy as np

from . import catalog as C
from . import refqs

EPS = 2.220446049250313e-16
SQ2 = math.sqrt(2.0)


# ------------------------------------------------------------------------------------------- time functions
class Fn:
    """A time function with derivatives (piecewise smooth; right-continuous at breakpoints)."""
    name = "fn"

    def breaks(self, t0, t1):
        return []

    def val(self, t, k=0, side=1):
        raise NotImplementedError

    @staticmethod
    def after(t, a, side):
        return t >= a if side > 0 else t > a


class Const(Fn):
    def __init__(self, c):
        self.c = float(c)

    def val(self, t, k=0, side=1):
        return self.c if k == 0 else 0.0


class Step(Fn):
    def __init__(self, height, offset=0.0, start=0.0):
        self.h, self.o, self.s = float(height), float(offset), float(start)

    def breaks(self, t0, t1):
        return [self.s] if t0 < self.s < t1 else []

    def val(self, t, k=0, side=1):
        if k:
            return 0.0
        return self.o + (self.h if self.after(t, self.s, side) else 0.0)


class Ramp(Fn):
    def __init__(self, height, duration, offset=0.0, start=0.0):
        self.h, self.d, self.o, self.s = float(height), float(duration), float(offset), float(start)

    def breaks(self, t0, t1):
        return [b for b in (self.s, self.s + self.d) if t0 < b < t1]

    def val(self, t, k=0, side=1):
        if self.d <= 0:
            return Step(self.h, self.o, self.s).val(t, k, side)
        on = self.after(t, self.s, side) and not self.after(t, self.s + self.d, side)
        if k == 0:
            if not self.after(t, self.s, side):
                return self.o
            if self.after(t, self.s + self.d, side):
                return self.o + self.h
            return self.o + (t - self.s) * self.h / self.d
        return self.h / self.d if (k == 1 and on) else 0.0


class Sine(Fn):
    def __init__(self, amplitude, f, phase=0.0, offset=0.0, start=0.0):
        self.a, self.f, self.p, self.o, self.s = float(amplitude), float(f), float(phase), float(offset), float(start)

    def breaks(self, t0, t1):
        return [self.s] if t0 < self.s < t1 else []

    def val(self, t, k=0, side=1):
        if not self.after(t, self.s, side):
            return self.o if k == 0 else 0.0
        w = 2 * math.pi * self.f
        return (self.o if k == 0 else 0.0) + self.a * w ** k * math.sin(w * (t - self.s) + self.p + k * math.pi / 2)


class Pulse(Fn):
    """Blocks.Sources.Pulse: on (amplitude) from s + k*T for width % of T, k = 0, 1, ... (nperiod < 0: forever).
    The edges are computed one way (_edges) for both breaks() and val(), so they agree to the last bit."""

    def __init__(self, amplitude, width, period, nperiod=-1, offset=0.0, start=0.0):
        self.a, self.w, self.T = float(amplitude), float(width), float(period)
        self.n, self.o, self.s = int(nperiod), float(offset), float(start)

    def _edges(self, k):
        b0 = self.s + k * self.T
        return b0, b0 + self.T * self.w / 100

    def breaks(self, t0, t1):
        if self.n == 0:
            return []
        out, k = [], max(0, int((t0 - self.s) // self.T) - 1)
        while True:
            b0, b1 = self._edges(k)
            if b0 >= t1 or (0 < self.n <= k):
                break
            for b in (b0, b1):
                if t0 < b < t1:
                    out.append(b)
            k += 1
        return out

    def val(self, t, k=0, side=1):
        if k:
            return 0.0
        if not self.after(t, self.s, side) or self.n == 0:
            return self.o
        c = int(math.floor((t - self.s) / self.T))
        for _ in range(3):                                  # settle the period index against the edges
            if c > 0 and not self.after(t, self._edges(c)[0], side):
                c -= 1
            elif self.after(t, self._edges(c + 1)[0], side):
                c += 1
            else:
                break
        if self.n > 0 and c >= self.n:
            return self.o
        on = not self.after(t, self._edges(c)[1], side)
        return self.o + (self.a if on else 0.0)


class Table(Fn):
    """CombiTimeTable with LinearSegments and HoldLastPoint (first and last values held), offset 0,
    shiftTime = startTime; output 0 before startTime."""

    def __init__(self, table, start=0.0):
        self.t = [float(r[0]) for r in table]
        self.y = [float(r[1]) for r in table]
        self.s = float(start)

    def breaks(self, t0, t1):
        return [b for b in [self.s] + [self.s + x for x in self.t] if t0 < b < t1]

    def val(self, t, k=0, side=1):
        if not self.after(t, self.s, side):
            return 0.0
        x, ts, ys = t - self.s, self.t, self.y
        if len(ts) == 1 or x < ts[0] or (x == ts[0] and side < 0):
            return ys[0] if k == 0 else 0.0
        if x > ts[-1] or (x == ts[-1] and side > 0):
            return ys[-1] if k == 0 else 0.0
        i = max(j for j in range(len(ts) - 1) if (ts[j] <= x if side > 0 else ts[j] < x))
        slope = (ys[i + 1] - ys[i]) / (ts[i + 1] - ts[i])
        if k == 0:
            return ys[i] + slope * (x - ts[i])
        return slope if k == 1 else 0.0


class Input(Fn):
    """A free input (linearisation): 0 in simulations."""

    def __init__(self, name):
        self.name = name

    def val(self, t, k=0, side=1):
        return 0.0


# ------------------------------------------------------------------------------------------- the system
class _ODEDense:
    """z(t) and z'(t) of a segment integrated as an ODE (for Cascade sources)."""

    def __init__(self, sol, zfull, b):
        self.sol, self.zfull, self.b = sol, zfull, b

    def __call__(self, t):
        return self.zfull(t, self.sol.sol(t), -1 if t >= self.b else 1)[0]

    def deriv(self, t):
        h = 1e-7 * max(1.0, abs(t))
        return (self(t + h) - self(t - h)) / (2 * h)


class Cascade(Fn):
    """Heat from a loss into a thermal network: q(t) = sum coef * (L1 z)(L2 z) on the solution of the electrical or
    mechanical part (one way: that part does not depend on temperatures). The base Ref runs once with q = 0 (the
    sources of the losses do not see q), then again with q from that run's dense solution; the copies in the
    systems of other modes share its state through 'ref' (the base) and 'key'."""
    name = "cascade"

    def __init__(self, terms, key):
        self.terms = terms                 # [(coef, {var: c}, {var: c})]
        self.key = key
        self.ref = None

    def val(self, t, k=0, side=1):
        r = self.ref
        if r is None:
            return 0.0
        if r._casc_frozen is not None:
            return r._casc_frozen.get(self.key, 0.0) if k == 0 else 0.0
        if not r._casc_on or r._in_stage1 or r._stage1 is None or k >= 2:
            return 0.0
        z, zd = r._stage1_state(t, side)
        out = 0.0
        for cf, f1, f2 in self.terms:
            L1, L2 = r._functional(f1), r._functional(f2)
            a, b = L1 @ z, L2 @ z
            out += cf * a * b if k == 0 else cf * ((L1 @ zd) * b + a * (L2 @ zd))
        return float(out)


class System:
    def __init__(self):
        self.vars: list[str] = []
        self.idx: dict[str, int] = {}
        self.rows: list[tuple[dict, dict, list]] = []   # (A part, B part, [(coef, fn)])
        self.fns: list[Fn] = []
        self.fn_idx: dict[int, int] = {}
        self.paths: dict[str, dict] = {}                # Modelica path -> linear functional {var: coef}
        self.products: dict = {}                        # path -> (f1, f2) or [(coef, f1, f2), ...]: sums of products
        self.cascades: list = []                        # Cascade sources (losses heating a thermal network)
        self.funcs: dict = {}                           # path -> ("rms", [functionals]) | ("call", fn, deps): afterwards
        self.nl: list = []                              # nonlinear terms: (row, fn(values), dependency names)
        self.orient: dict = {}                          # PMSM cores written for a current feed: {elem: "c"}
        self.modes = None                               # None: neutral stand-ins (validation); else {elem: mode}
        self.pwl: dict = {}                             # piecewise-linear elements: {elem: initial mode}
        self.guards: list = []                          # [{elem, f, c, dir, to}]: leave the mode when f z + c crosses 0
        self.inits: list[dict] = []                     # candidate initial values
        self.exact = True
        self.not_exact: list[str] = []

    def v(self, name: str) -> str:
        if name not in self.idx:
            self.idx[name] = len(self.vars)
            self.vars.append(name)
        return name

    def fn(self, f: Fn) -> Fn:
        if id(f) not in self.fn_idx:
            self.fn_idx[id(f)] = len(self.fns)
            self.fns.append(f)
        return f

    def eq(self, a: dict, b: dict | None = None, src: list | None = None, nl=None):
        """A row: sum a z + sum b z' + sum c F(t) (+ nl: (fn, deps): fn(values of deps) for the nonlinear terms)."""
        for x in list(a) + list(b or {}):
            self.v(x)
        self.rows.append((dict(a), dict(b or {}), [(c, self.fn(f)) for c, f in (src or [])]))
        if nl is not None:
            fn, deps = nl
            for x in deps:
                self.v(x)
            self.nl.append((len(self.rows) - 1, fn, list(deps)))


class _Ctx:
    """Helpers for one element's equations."""

    def __init__(self, sys_: System, net, e: str):
        self.s, self.net, self.e = sys_, net, e
        self.d = net.elems[e]
        self.p = self.d["params"]
        self.k = C.KINDS[self.d["kind"]]

    def pot(self, port):
        return self.s.v(f"n{self.net.node_of[f'{self.e}.{port}']}")

    def flow(self, port):
        return self.s.v(f"{self.e}.{port}#flow")

    def sig(self, port):
        return self.s.v(f"s{self.net.signal_of[f'{self.e}.{port}']}")

    def hpot(self, port):
        """Specific enthalpy of a fluid node (the connector's second potential)."""
        return self.s.v(f"n{self.net.node_of[f'{self.e}.{port}']}#h")

    def hflow(self, port):
        """Enthalpy flow into the component at a fluid port."""
        return self.s.v(f"{self.e}.{port}#H")

    def enabled(self, port):
        return port in self.d.get("enabled", ())

    def mode(self, default):
        """This element's mode (piecewise-linear kinds): None for the neutral stand-in of the base system."""
        self.s.pwl[self.e] = default
        return None if self.s.modes is None else self.s.modes.get(self.e, default)

    def guard(self, f, c0, direction, to):
        """Leave the current mode for 'to' when f z + c0 crosses 0 upwards (direction 1) or downwards (-1)."""
        self.s.guards.append({"elem": self.e, "f": f, "c": float(c0), "dir": direction, "to": to})

    def var(self, name):
        return self.s.v(f"{self.e}.{name}")

    def eq(self, a, b=None, src=None, nl=None):
        self.s.eq(a, b, src, nl)

    def func(self, name, fn, deps):
        """An output computed from the solution: fn(*columns of deps) (vectorised)."""
        self.s.funcs[f"{self.e}.{name}"] = ("call", fn, list(deps))

    def path(self, name, functional):
        self.s.paths[f"{self.e}.{name}"] = functional

    def init(self, name, functional, value, priority, given, forced=False):
        self.s.inits.append({"elem": self.e, "name": name, "f": functional, "value": float(value),
                             "priority": priority, "given": given, "forced": forced})

    def approx(self, why):
        self.s.exact = False
        self.s.not_exact.append(f"{self.d['comp']} ({self.d['kind']}): {why}")


def _reduce_linear_hidden(A, B, G, rs, nl_rows, K, Jg0=None, max_iter=6):
    """Row operations on M z' = f: the system is index 1 when N (A + Jg) V0 is regular (N: left null space of B,
    V0: its right null space). Combinations of rows y with y N (A + Jg) V0 = 0 are hidden constraints; those
    without a nonlinear row (rigidly joined inertias, a capacitor across a source) are replaced by their time
    derivatives (the sources' derivative columns shift), as the shuffle algorithm does; nonlinear ones stay (index
    2: the integrator scales their components). Returns (A, B, G, Tg) of the integrated system; Tg maps the
    nonlinear terms g(z) into its rows."""
    n = A.shape[0]
    Tg = np.diag(rs.astype(float))
    nl_mask = np.zeros(n, bool)
    nl_mask[list(nl_rows)] = True
    Jg0 = np.zeros((n, n)) if Jg0 is None else Jg0
    A, B, G = A.copy(), B.copy(), G.copy()
    for _ in range(max_iter):
        N, _R = _rank_split(B)
        if N.shape[0] == 0:
            break
        V0, _rk = _null_right(B, n)
        S = N @ (A + Tg @ Jg0) @ V0
        W = np.hstack([S, N @ Tg[:, nl_mask]]) if nl_mask.any() else S
        Yl = _rank_split(W)[0]
        if Yl.shape[0] == 0:
            break
        C_ = Yl @ N
        Uc, sc, Vc = np.linalg.svd(C_, full_matrices=True)
        rk = int(np.sum(sc > 1e-10 * (sc[0] if sc.size else 1.0)))
        if rk == 0:
            break
        Cr = np.linalg.qr((Uc[:, :rk].T @ C_).T)[0].T       # the constraint rows (orthonormal combinations)
        Q = np.linalg.svd(Cr, full_matrices=True)[2][rk:]      # an orthonormal complement
        CA, CG = Cr @ A, Cr @ G
        if np.any(np.abs(CG[:, [j * K + K - 1 for j in range(G.shape[1] // K)]]) > 0):
            break
        CGs = np.zeros_like(CG)
        for j in range(G.shape[1] // K):
            for o in range(K - 1):
                CGs[:, j * K + o + 1] = CG[:, j * K + o]
        A = np.vstack([Q @ A, np.zeros_like(CA)])
        B = np.vstack([Q @ B, CA])
        G = np.vstack([Q @ G, CGs])
        Tg = np.vstack([Q @ Tg, np.zeros((rk, n))])
    return A, B, G, Tg


def _cascades_to_nl(s: "System"):
    """Turn the Cascade sources (losses heating a thermal network) into nonlinear terms of their rows."""
    casc = {id(f): f for f in s.cascades}
    for i, (a, b, src) in enumerate(s.rows):
        keep, terms = [], []
        for cf, f in src:
            if id(f) in casc:
                terms.append((cf, casc[id(f)]))
            else:
                keep.append((cf, f))
        if not terms:
            continue
        s.rows[i] = (a, b, keep)
        deps = sorted({x for _, f in terms for _, f1, f2 in f.terms for x in list(f1) + list(f2)})
        pos = {x: k for k, x in enumerate(deps)}

        def fn(v, terms=terms, pos=pos):
            out = 0.0
            for cf, f in terms:
                for c2, f1, f2 in f.terms:
                    out += cf * c2 * sum(c_ * v[pos[x]] for x, c_ in f1.items()) * sum(c_ * v[pos[x]] for x, c_ in f2.items())
            return out
        s.nl.append((i, fn, deps))
    s.cascades = []


def _heat_out(c: _Ctx, terms, port="heatPort"):
    """flow(heatPort) = -loss: the loss leaves as heat into the thermal network (a Cascade source)."""
    f = Cascade([(-cf, f1, f2) for cf, f1, f2 in terms], f"{c.e}.{port}")
    c.s.cascades.append(f)
    c.eq({c.flow(port): 1}, None, [(-1.0, f)])


def _ports_paths(c: _Ctx):
    """Register Modelica paths of the element's port variables."""
    for p, t in c.k["ports"].items():
        if p in c.k["cond_ports"] and not c.enabled(p):
            continue
        mp = c.net.mpath(f"{c.e}.{p}")
        if C.is_complex(t):
            continue                    # quasi-static ports and complex signals: refqs (no port paths)
        if t in ("in", "out"):
            c.s.paths[mp] = {c.sig(p): 1.0}
        elif t in ("vin", "vout"):
            for k in range(1, 4):
                c.s.paths[f"{mp}[{k}]"] = {c.sig(f"{p}[{k}]"): 1.0}
        elif t == "plug":
            for k in range(1, 4):
                c.s.paths[f"{mp}.pin[{k}].v"] = {c.pot(f"{p}.pin[{k}]"): 1.0}
                c.s.paths[f"{mp}.pin[{k}].i"] = {c.flow(f"{p}.pin[{k}]"): 1.0}
        else:
            pt = C.PORT_TYPES[t]
            c.s.paths[f"{mp}.{pt['potential']}"] = {c.pot(p): 1.0}
            c.s.paths[f"{mp}.{pt['flow']}"] = {c.flow(p): 1.0}
            if t == "fluid":
                c.s.paths[f"{mp}.h"] = {c.hpot(p): 1.0}
                c.s.paths[f"{mp}.H_flow"] = {c.hflow(p): 1.0}


def _one_port(c: _Ctx, a="p", b="n"):
    """v = a.v - b.v, i = a.flow, a.flow + b.flow = 0 (OnePort)."""
    v, i = c.var("v"), c.var("i")
    c.eq({v: 1, c.pot(a): -1, c.pot(b): 1})
    c.eq({i: 1, c.flow(a): -1})
    c.eq({c.flow(a): 1, c.flow(b): 1})
    c.path("v", {v: 1.0})
    c.path("i", {i: 1.0})
    return v, i


def _init(c: _Ctx, name, functional):
    spec = c.k["inits"][name]
    value, given = c.d["inits"][name]
    c.init(name, functional, value, spec["priority"], given)


def _flange_rel(c: _Ctx, a="flange_a", b="flange_b", rel="s_rel", f="f"):
    s_rel, ff = c.var(rel), c.var(f)
    c.eq({s_rel: 1, c.pot(b): -1, c.pot(a): 1})
    c.eq({ff: 1, c.flow(b): -1})
    c.eq({c.flow(a): 1, ff: 1})
    c.path(rel, {s_rel: 1.0})
    c.path(f, {ff: 1.0})
    return s_rel, ff


def _source_fn(kname, p):
    if kname in ("constant_voltage",):
        return Const(p["V"])
    if kname == "constant_current":
        return Const(p["I"])
    if kname == "step_voltage":
        return Step(p["V"], p["offset"], p["startTime"])
    if kname == "step_current":
        return Step(p["I"], p["offset"], p["startTime"])
    if kname == "ramp_voltage":
        return Ramp(p["V"], p["duration"], p["offset"], p["startTime"])
    if kname == "sine_voltage":
        return Sine(p["V"], p["f"], p["phase"], p["offset"], p["startTime"])
    if kname == "sine_current":
        return Sine(p["I"], p["f"], p["phase"], p["offset"], p["startTime"])
    if kname == "pulse_voltage":
        return Pulse(p["V"], p["width"], p["period"], -1, p["offset"], p["startTime"])
    if kname == "constant":
        return Const(p["k"])
    if kname == "step":
        return Step(p["height"], p["offset"], p["startTime"])
    if kname == "ramp":
        return Ramp(p["height"], p["duration"], p["offset"], p["startTime"])
    if kname == "sine":
        return Sine(p["amplitude"], p["f"], p["phase"], p["offset"], p["startTime"])
    if kname == "pulse":
        return Pulse(p["amplitude"], p["width"], p["period"], p["nperiod"], p["offset"], p["startTime"])
    if kname == "table":
        return Table(p["table"], p["startTime"])
    raise KeyError(kname)


def _stamp(c: _Ctx):
    kn, p = c.d["kind"], c.p
    _ports_paths(c)
    if c.k.get("domain") == "fluid":
        _stamp_fluid(c)
        return
    if kn == "dc_machine":
        _stamp_dc_machine(c)
        return
    if kn in POLY:
        _stamp_poly(c)
        return
    if kn in AC_PARTS:
        _stamp_ac(c)
        return
    if kn in refqs.QS_PARTS:
        refqs.stamp(c)
        return
    # ---- electrical
    if kn == "resistor":
        v, i = _one_port(c)
        Th, Ra = c.var("T_heatPort"), c.var("R_actual")
        al, Tr = p["alpha"], p["T_ref"]
        if c.enabled("heatPort"):
            c.eq({Th: 1, c.pot("heatPort"): -1})
            R = p["R"]
            if al != 0:
                c.approx("resistance follows the heat port's temperature (two-way coupling)")
            _heat_out(c, [(R, {i: 1.0}, {i: 1.0})])
        else:
            T = p["T"] if p.get("T") is not None else Tr
            c.eq({Th: 1}, None, [(-T, Const(1.0))])
            R = p["R"] * (1 + al * (T - Tr))
        c.eq({Ra: 1, Th: -p["R"] * al}, None, [(-p["R"] * (1 - al * Tr), Const(1.0))])
        c.eq({v: 1, i: -R})
        c.path("T_heatPort", {Th: 1.0})
        c.path("R_actual", {Ra: 1.0})
        c.s.products[f"{c.e}.LossPower"] = ({v: 1.0}, {i: 1.0})
    elif kn == "ideal_diode":
        v, i = _one_port(c)
        md = c.mode("off")
        Ron, Goff, Vk = p["Ron"], p["Goff"], p["Vknee"]
        if md is None:
            c.eq({v: 1, i: -1.0})                     # structure only
        elif md == "off":                             # MSL: v = s + Vknee, i = Goff s + Goff Vknee: i = Goff v
            c.eq({i: 1, v: -Goff})
            c.guard({v: 1.0}, -Vk, 1, "on")           # s = v - Vknee rises through 0
        else:                                         # on: v = Ron s + Vknee, i = s + Goff Vknee
            c.eq({v: 1, i: -Ron}, None, [(Ron * Goff * Vk - Vk, Const(1.0))])
            c.guard({i: 1.0}, -Goff * Vk, -1, "off")  # s = i - Goff Vknee falls through 0
        c.s.products[f"{c.e}.LossPower"] = ({v: 1.0}, {i: 1.0})
        if c.enabled("heatPort"):
            _heat_out(c, [(1.0, {v: 1.0}, {i: 1.0})])
    elif kn == "capacitor":
        v, i = _one_port(c)
        c.eq({i: 1}, {v: -p["C"]})
        _init(c, "v0", {v: 1.0})
    elif kn == "inductor":
        v, i = _one_port(c)
        c.eq({v: 1}, {i: -p["L"]})
        _init(c, "i0", {i: 1.0})
    elif kn == "ground":
        c.eq({c.pot("p"): 1})
    elif kn == "emf":
        v, i = _one_port(c)
        phi, w, tau, te = c.var("phi"), c.var("w"), c.var("tau"), c.var("tauElectrical")
        c.eq({phi: 1, c.pot("flange"): -1})
        c.eq({w: 1}, {phi: -1})
        c.eq({v: 1, w: -p["k"]})
        c.eq({tau: 1, c.flow("flange"): -1})
        c.eq({tau: 1, i: p["k"]})
        c.eq({te: 1, tau: 1})
        for n_, x in (("phi", phi), ("w", w), ("tau", tau), ("tauElectrical", te)):
            c.path(n_, {x: 1.0})
    elif kn == "transformer":
        for s_ in ("1", "2"):
            v, i = c.var("v" + s_), c.var("i" + s_)
            c.eq({v: 1, c.pot("p" + s_): -1, c.pot("n" + s_): 1})
            c.eq({i: 1, c.flow("p" + s_): -1})
            c.eq({c.flow("p" + s_): 1, c.flow("n" + s_): 1})
            c.path("v" + s_, {v: 1.0})
            c.path("i" + s_, {i: 1.0})
        v1, v2, i1, i2 = c.var("v1"), c.var("v2"), c.var("i1"), c.var("i2")
        c.eq({v1: 1}, {i1: -p["L1"], i2: -p["M"]})
        c.eq({v2: 1}, {i1: -p["M"], i2: -p["L2"]})
        _init(c, "i1_0", {i1: 1.0})
        _init(c, "i2_0", {i2: 1.0})
    elif kn in ("constant_voltage", "step_voltage", "ramp_voltage", "sine_voltage", "pulse_voltage"):
        v, i = _one_port(c)
        c.eq({v: 1}, None, [(-1.0, _source_fn(kn, p))])
    elif kn in ("constant_current", "step_current", "sine_current"):
        v, i = _one_port(c)
        c.eq({i: 1}, None, [(-1.0, _source_fn(kn, p))])
    elif kn == "signal_voltage":
        v, i = _one_port(c)
        c.eq({v: 1, c.sig("v"): -1})
    elif kn == "signal_current":
        v, i = _one_port(c)
        c.eq({i: 1, c.sig("i"): -1})
    elif kn == "voltage_sensor":
        c.eq({c.flow("p"): 1})
        c.eq({c.flow("n"): 1})
        c.eq({c.sig("v"): 1, c.pot("p"): -1, c.pot("n"): 1})
    elif kn == "current_sensor":
        c.eq({c.pot("p"): 1, c.pot("n"): -1})
        c.eq({c.flow("p"): 1, c.flow("n"): 1})
        c.eq({c.sig("i"): 1, c.flow("p"): -1})
    elif kn == "potential_sensor":
        c.eq({c.flow("p"): 1})
        c.eq({c.sig("phi"): 1, c.pot("p"): -1})
    elif kn == "opamp":
        c.eq({c.flow("in_p"): 1})
        c.eq({c.flow("in_n"): 1})
        c.eq({c.pot("in_p"): 1, c.pot("in_n"): -1})
    # ---- translational and rotational (same equations, other names)
    elif kn in ("mass", "inertia"):
        s_, v_, a_ = ("s", "v", "a") if kn == "mass" else ("phi", "w", "a")
        m = p["m"] if kn == "mass" else p["J"]
        s, v, a = c.var(s_), c.var(v_), c.var(a_)
        c.eq({s: 1, c.pot("flange_a"): -1})
        c.eq({s: 1, c.pot("flange_b"): -1})
        c.eq({v: 1}, {s: -1})
        c.eq({a: 1}, {v: -1})
        c.eq({a: m, c.flow("flange_a"): -1, c.flow("flange_b"): -1})
        for n_, x in ((s_, s), (v_, v), (a_, a)):
            c.path(n_, {x: 1.0})
        _init(c, "s0" if kn == "mass" else "phi0", {s: 1.0})
        _init(c, "v0" if kn == "mass" else "w0", {v: 1.0})
    elif kn in ("spring", "rot_spring"):
        rel, f = ("s_rel", "f") if kn == "spring" else ("phi_rel", "tau")
        r, ff = _flange_rel(c, rel=rel, f=f)
        c0 = p["s_rel0"] if kn == "spring" else p["phi_rel0"]
        c.eq({ff: 1, r: -p["c"]}, None, [(p["c"] * c0, Const(1.0))])
        _init(c, f"{rel}_init", {r: 1.0})
    elif kn in ("damper", "rot_damper", "spring_damper", "rot_spring_damper"):
        tr = kn in ("damper", "spring_damper")
        rel, f, vrel = ("s_rel", "f", "v_rel") if tr else ("phi_rel", "tau", "w_rel")
        r, ff = _flange_rel(c, rel=rel, f=f)
        vr = c.var(vrel)
        c.eq({vr: 1}, {r: -1})
        c.path(vrel, {vr: 1.0})
        if kn.endswith("spring_damper"):
            c0 = p["s_rel0"] if tr else p["phi_rel0"]
            c.eq({ff: 1, r: -p["c"], vr: -p["d"]}, None, [(p["c"] * c0, Const(1.0))])
        else:
            c.eq({ff: 1, vr: -p["d"]})
        c.s.products[f"{c.e}.lossPower"] = ({vr: p["d"]}, {vr: 1.0})
        if c.enabled("heatPort"):
            _heat_out(c, [(p["d"], {vr: 1.0}, {vr: 1.0})])
        _init(c, f"{rel}_init", {r: 1.0})
    elif kn == "fixed":
        c.eq({c.pot("flange"): 1}, None, [(-p["s0"], Const(1.0))])
    elif kn == "rot_fixed":
        c.eq({c.pot("flange"): 1}, None, [(-p["phi0"], Const(1.0))])
    elif kn in ("force", "torque"):
        c.eq({c.flow("flange"): 1, c.sig("f" if kn == "force" else "tau"): 1})
    elif kn == "force_step":
        c.eq({c.flow("flange"): 1}, None, [(1.0, Step(p["stepForce"], p["offsetForce"], p["startTime"]))])
    elif kn == "constant_force":
        c.eq({c.flow("flange"): 1}, None, [(p["f_constant"], Const(1.0))])
    elif kn in ("torque_step", "constant_torque"):
        tau = c.var("tau")
        fn = Step(p["stepTorque"], p["offsetTorque"], p["startTime"]) if kn == "torque_step" else Const(p["tau_constant"])
        c.eq({tau: 1}, None, [(-1.0, fn)])
        c.eq({c.flow("flange"): 1, tau: 1})
        c.path("tau", {tau: 1.0})
        if kn == "constant_torque":
            w = c.var("w")
            c.eq({w: 1}, {c.pot("flange"): -1})
            c.path("w", {w: 1.0})
    elif kn in ("linear_speed_load", "quadratic_speed_load"):
        tau, w = c.var("tau"), c.var("w")
        c.eq({w: 1}, {c.pot("flange"): -1})
        tn, wn = float(p["tau_nominal"]), float(p["w_nominal"])
        quad, same = kn == "quadratic_speed_load", bool(p["TorqueDirection"])
        if not quad and not same:
            c.eq({tau: 1, w: -tn / wn})
        else:
            # MSL: tau = tau_nominal*(w/w_nominal)^2 (TorqueDirection) or *sign(w)*(w/w_nominal)^2; linear:
            # tau = tau_nominal*abs(w/w_nominal) (TorqueDirection)
            if quad and same:
                f = lambda x: -tn * (x[0] / wn) ** 2
            elif quad:
                f = lambda x: -tn * x[0] * abs(x[0]) / wn ** 2
            else:
                f = lambda x: -tn * abs(x[0] / wn)
            c.eq({tau: 1}, nl=(f, [w]))
        c.eq({c.flow("flange"): 1, tau: 1})
        c.path("tau", {tau: 1.0})
        c.path("w", {w: 1.0})
    elif kn in ("position_sensor", "angle_sensor"):
        c.eq({c.flow("flange"): 1})
        c.eq({c.sig("s" if kn == "position_sensor" else "phi"): 1, c.pot("flange"): -1})
    elif kn in ("speed_sensor", "rot_speed_sensor"):
        c.eq({c.flow("flange"): 1})
        c.eq({c.sig("v" if kn == "speed_sensor" else "w"): 1}, {c.pot("flange"): -1})
    elif kn in ("acc_sensor", "rot_acc_sensor"):
        v = c.var("v")
        c.eq({c.flow("flange"): 1})
        c.eq({v: 1}, {c.pot("flange"): -1})
        c.eq({c.sig("a"): 1}, {v: -1})
    elif kn in ("force_sensor", "torque_sensor"):
        out = "f" if kn == "force_sensor" else "tau"
        c.eq({c.pot("flange_a"): 1, c.pot("flange_b"): -1})
        c.eq({c.flow("flange_a"): 1, c.flow("flange_b"): 1})
        c.eq({c.sig(out): 1, c.flow("flange_a"): -1})
    elif kn == "ideal_gear":
        c.eq({c.pot("flange_a"): 1, c.pot("flange_b"): -p["ratio"]})
        c.eq({c.flow("flange_a"): p["ratio"], c.flow("flange_b"): 1})
    # ---- thermal
    elif kn == "heat_capacitor":
        T, dT = c.var("T"), c.var("der_T")
        Cv = p["C"] if p.get("C") is not None else p["m"] * p["cp"]
        c.eq({T: 1, c.pot("port"): -1})
        c.eq({dT: 1}, {T: -1})
        c.eq({c.flow("port"): 1}, {T: -Cv})
        c.path("T", {T: 1.0})
        c.path("der_T", {dT: 1.0})
        _init(c, "T0", {T: 1.0})
    elif kn in ("thermal_conductor", "thermal_resistor", "radiation", "convection_core"):
        a_, b_ = ("solid", "fluid") if kn == "convection_core" else ("port_a", "port_b")
        dT, Q = c.var("dT"), c.var("Q_flow")
        c.eq({dT: 1, c.pot(a_): -1, c.pot(b_): 1})
        c.eq({Q: 1, c.flow(a_): -1})
        c.eq({c.flow(a_): 1, c.flow(b_): 1})
        c.path("dT", {dT: 1.0})
        c.path("Q_flow", {Q: 1.0})
        if kn == "thermal_conductor":
            G = p["G"] if p.get("G") is not None else p["k"] * p["A"] / p["L"]
            c.eq({Q: 1, dT: -G})
        elif kn == "thermal_resistor":
            c.eq({dT: 1, Q: -p["R"]})
        elif kn == "radiation":
            c.eq({Q: 1, dT: -1.0})
            c.approx("radiation (T^4)")
        else:
            gc = _constant_driver(c, "Gc")
            if gc is None:
                c.eq({Q: 1, dT: -1.0, c.sig("Gc"): 0.0})
                c.approx("convection with a varying Gc")
            else:
                c.eq({Q: 1, dT: -gc, c.sig("Gc"): 0.0})
    elif kn == "fixed_temperature":
        c.eq({c.pot("port"): 1}, None, [(-p["T"], Const(1.0))])
    elif kn == "prescribed_temperature":
        c.eq({c.pot("port"): 1, c.sig("T"): -1})
    elif kn == "fixed_heat_flow":
        q, al, tr = p["Q_flow"], p["alpha"], p["T_ref"]
        c.eq({c.flow("port"): 1, c.pot("port"): q * al}, None, [(q * (1 - al * tr), Const(1.0))])
    elif kn == "prescribed_heat_flow":
        c.eq({c.flow("port"): 1, c.sig("Q_flow"): 1})
    elif kn == "temperature_sensor":
        c.eq({c.flow("port"): 1})
        c.eq({c.sig("T"): 1, c.pot("port"): -1})
    elif kn == "heat_flow_sensor":
        c.eq({c.pot("port_a"): 1, c.pot("port_b"): -1})
        c.eq({c.flow("port_a"): 1, c.flow("port_b"): 1})
        c.eq({c.sig("Q_flow"): 1, c.flow("port_a"): -1})
    # ---- signals
    elif kn in ("constant", "step", "ramp", "sine", "pulse", "table"):
        c.eq({c.sig("y"): 1}, None, [(-1.0, _source_fn(kn, p))])
    elif kn == "gain":
        c.eq({c.sig("y"): 1, c.sig("u"): -p["k"]})
    elif kn == "add":
        c.eq({c.sig("y"): 1, c.sig("u1"): -p["k1"], c.sig("u2"): -p["k2"]})
    elif kn == "feedback":
        c.eq({c.sig("y"): 1, c.sig("u1"): -1, c.sig("u2"): 1})
    elif kn in ("product", "division"):
        c.eq({c.sig("y"): 1, c.sig("u1"): -1, c.sig("u2"): -1})
        c.approx(kn)
    elif kn in ("limiter", "dead_zone"):
        y, u = c.sig("y"), c.sig("u")
        hi, lo = p["uMax"], C.KINDS[kn]["mods"]["uMin"](p)
        md = c.mode("mid")
        if md is None:
            c.eq({y: 1, u: -1})
        elif md == "mid":
            if kn == "limiter":
                c.eq({y: 1, u: -1})
            else:
                c.eq({y: 1})
            c.guard({u: 1.0}, -hi, 1, "high")
            c.guard({u: 1.0}, -lo, -1, "low")
        elif md == "high":
            if kn == "limiter":
                c.eq({y: 1}, None, [(-hi, Const(1.0))])
            else:
                c.eq({y: 1, u: -1}, None, [(hi, Const(1.0))])
            c.guard({u: 1.0}, -hi, -1, "mid")
        else:
            if kn == "limiter":
                c.eq({y: 1}, None, [(-lo, Const(1.0))])
            else:
                c.eq({y: 1, u: -1}, None, [(lo, Const(1.0))])
            c.guard({u: 1.0}, -lo, 1, "mid")
    elif kn == "integrator":
        y = c.sig("y")
        c.eq({c.sig("u"): p["k"]}, {y: -1})
        c.init("y", {y: 1.0}, p["y_start"], 0, True, forced=True)
    elif kn in ("first_order", "slew_rate_limiter"):
        y = c.sig("y")
        if kn == "first_order":
            c.eq({c.sig("u"): p["k"], y: -1}, {y: -p["T"]})
        else:
            u, Td = c.sig("u"), p["Td"]
            Ri, Fa = p["Rising"], C.KINDS[kn]["mods"]["Falling"](p)
            md = c.mode("free")
            val = {u: 1.0 / Td, y: -1.0 / Td}                   # MSL: val = (u - y)/Td
            if md in (None, "free"):
                c.eq({u: 1, y: -1}, {y: -Td})
                if md == "free":
                    c.guard(val, -Ri, 1, "rising")
                    c.guard(val, -Fa, -1, "falling")
            elif md == "rising":
                c.eq({}, {y: 1}, [(-Ri, Const(1.0))])
                c.guard(val, -Ri, -1, "free")
            else:
                c.eq({}, {y: 1}, [(-Fa, Const(1.0))])
                c.guard(val, -Fa, 1, "free")
        c.init("y", {y: 1.0}, p["y_start"], 0, True, forced=True)
    elif kn == "second_order":
        y, yd = c.sig("y"), c.var("yd")
        c.eq({yd: 1}, {y: -1})
        w, D, k = p["w"], p["D"], p["k"]
        c.eq({c.sig("u"): w * w * k, y: -w * w, yd: -2 * D * w}, {yd: -1})
        c.path("yd", {yd: 1.0})
        c.init("y", {y: 1.0}, p["y_start"], 0, True, forced=True)
        c.init("yd", {yd: 1.0}, p["yd_start"], 0, True, forced=True)
    elif kn == "derivative":
        x, y, u = c.var("x"), c.sig("y"), c.sig("u")
        if abs(p["k"]) < EPS:
            c.eq({}, {x: 1})
            c.eq({y: 1})
        else:
            c.eq({u: 1, x: -1}, {x: -p["T"]})
            c.eq({y: 1, u: -p["k"] / p["T"], x: p["k"] / p["T"]})
        c.path("x", {x: 1.0})
        c.init("x", {x: 1.0}, 0.0, 0, True, forced=True)
    elif kn == "pi":
        x, y, u = c.var("x"), c.sig("y"), c.sig("u")
        c.eq({u: 1}, {x: -p["T"]})
        c.eq({y: 1, x: -p["k"], u: -p["k"]})
        c.path("x", {x: 1.0})
        c.init("x", {x: 1.0}, 0.0, 0, True, forced=True)
    elif kn == "pid":
        _pid(c, p["k"], p["Ti"], p["Td"], p["Nd"], c.sig("u"), None, True, True)
    elif kn == "lim_pid":
        _lim_pid(c)
    elif kn == "transfer_function":
        _tf(c, p["b"], p["a"])
    else:
        raise KeyError(f"no reference equations for {kn}")


def _stamp_dc_machine(c: _Ctx):
    """MSL DC_PermanentMagnet (useSupport = false): pin_ap - strayLoad (v = 0) - ra - X; X - la - airgap - Y and the
    core conductance X - Y; Y - brush - pin_an. Airgap: vai = k w, tauElectrical = k i_la with k = ViNominal /
    wNominal (psi_e = 1). Rotor: Jr dw/dt = tauElectrical + flange.tau - friction torque - stray-load torque."""
    from .network import dcpm_vi_nominal
    p = c.p
    k = dcpm_vi_nominal(p) / p["wNominal"]
    a20 = p["alpha20a"]
    alpha = a20 / (1 + a20 * (p["TaRef"] - 293.15))
    thermal = bool(c.d.get("enabled"))
    ap, an, fl = "pin_ap", "pin_an", "flange"
    va, ia, X, L, Y = c.var("va"), c.var("ia"), c.var("nX"), c.var("nL"), c.var("nY")
    ila, icore, w, phi, tauE = c.var("la.i"), c.var("core.i"), c.var("wMechanical"), c.var("phiMechanical"), c.var("tauElectrical")
    Ta, Ract = c.var("Ta"), c.var("Ra_actual")
    c.eq({c.flow(ap): 1, ia: -1})
    c.eq({c.flow(ap): 1, c.flow(an): 1})
    c.eq({va: 1, c.pot(ap): -1, c.pot(an): 1})
    if thermal:
        c.eq({Ta: 1, c.pot("heatPortArmature"): -1})
    else:
        c.eq({Ta: 1}, None, [(-p["TaOperational"], Const(1.0))])
    c.eq({Ract: 1, Ta: -p["Ra"] * alpha}, None, [(-p["Ra"] * (1 - alpha * p["TaRef"]), Const(1.0))])
    R = p["Ra"] * (1 + alpha * ((p["TaOperational"] if not thermal else p["TaRef"]) - p["TaRef"]))
    if thermal and alpha != 0:
        c.approx("armature resistance follows the heat port's temperature (two-way coupling)")
    c.eq({c.pot(ap): 1, X: -1, ia: -R})
    c.eq({X: 1, L: -1}, {ila: -p["La"]})
    c.eq({L: 1, Y: -1, w: -k})
    VRef = p.get("VRef_core") or dcpm_vi_nominal(p)
    Gc = p["PRef_core"] / VRef ** 2 if p["PRef_core"] > 0 else 0.0
    c.eq({icore: 1, X: -Gc, Y: Gc})
    c.eq({ia: 1, ila: -1, icore: -1})
    Vb = p["V_brush"]
    IL = p.get("ILinear_brush") or 0.01 * p["IaNominal"]
    one = c.var("one")
    c.eq({one: 1}, None, [(-1.0, Const(1.0))])
    vbr = {}
    if Vb > 0:
        md = c.mode("mid")
        if md in (None, "mid"):
            c.eq({Y: 1, c.pot(an): -1, ia: -Vb / IL})
            vbr = {ia: Vb / IL}
            if md == "mid":
                c.guard({ia: 1.0}, -IL, 1, "high")
                c.guard({ia: 1.0}, IL, -1, "low")
        else:
            sg = 1.0 if md == "high" else -1.0
            c.eq({Y: 1, c.pot(an): -1}, None, [(-sg * Vb, Const(1.0))])
            c.guard({ia: 1.0}, -sg * IL, -sg, "mid")
            vbr = None                                # constant drop: loss = V |ia|
            vb_sign = sg
    else:
        c.eq({Y: 1, c.pot(an): -1})
    wRef = p.get("wRef_friction") or p["wNominal"]
    cf = p["PRef_friction"] / wRef / wRef if p["PRef_friction"] > 0 else 0.0
    if p["PRef_friction"] > 0 and abs(p["power_w_friction"] - 1.0) > 1e-12:
        c.approx("friction torque ~ w^(power_w - 1) (linear only for power_w = 1)")
    if p["PRef_strayLoad"] > 0:
        c.approx("stray-load torque ~ i^2")
    c.eq({phi: 1, c.pot(fl): -1})
    c.eq({w: 1}, {phi: -1})
    c.eq({tauE: 1, ila: -k})
    c.eq({tauE: 1, c.flow(fl): 1, w: -cf}, {w: -p["Jr"]})
    vb = vbr if vbr is not None else {}
    quads = {"lossPowerArmature": [(R, {ia: 1.0}, {ia: 1.0})],
             "lossPowerCore": [(Gc, {X: 1.0, Y: -1.0}, {X: 1.0, Y: -1.0})],
             "lossPowerFriction": [(cf, {w: 1.0}, {w: 1.0})],
             "lossPowerBrush": [(1.0, vb, {ia: 1.0})] if vb else
                               ([(vb_sign * Vb, {ia: 1.0}, {one: 1.0})] if vbr is None else [(0.0, {ia: 1.0}, {ia: 1.0})]),
             "lossPowerStrayLoad": [(0.0, {ia: 1.0}, {ia: 1.0})]}
    quads["lossPowerTotal"] = [t for v in quads.values() for t in v]
    quads["powerArmature"] = [(1.0, {va: 1.0}, {ia: 1.0})]
    quads["powerMechanical"] = [(-1.0, {w: 1.0}, {c.flow(fl): 1.0})]
    for v, q in quads.items():
        c.s.products[f"{c.e}.{C.var_path('dc_machine', v)}"] = q
    for v, x in (("va", va), ("ia", ia), ("wMechanical", w), ("phiMechanical", phi), ("tauElectrical", tauE),
                 ("Ta", Ta), ("Ra_actual", Ract)):
        c.path(C.var_path("dc_machine", v), {x: 1.0})
    c.path("tauShaft", {c.flow(fl): -1.0})
    c.path("la.i", {ila: 1.0})
    if thermal:
        heat = {"heatPortArmature": quads["lossPowerArmature"], "heatPortCore": quads["lossPowerCore"],
                "heatPortFriction": quads["lossPowerFriction"], "heatPortBrush": quads["lossPowerBrush"],
                "heatPortStrayLoad": quads["lossPowerStrayLoad"], "heatPortPermanentMagnet": [(0.0, {ia: 1.0}, {ia: 1.0})]}
        for h in C.DCPM_HEAT:
            _heat_out(c, heat[h], h)
    _init(c, "ia0", {ia: 1.0})
    _init(c, "w0", {w: 1.0})
    _init(c, "phi0", {phi: 1.0})


# ------------------------------------------------------------------------------------------- three-phase parts
POLY = {"ac_voltage", "star", "delta", "plug_to_pin", "ac_resistor", "ac_inductor", "ac_capacitor", "ac_current_rms",
        "ac_voltage_rms", "ac_power"}


def _ph(port, k):
    return f"{port}.pin[{k}]"


def _sig_used(c, port):
    """Whether a signal output drives any input (then a post-computed output cannot stand in for it)."""
    net = c.net
    tid = net.signal_of.get(f"{c.e}.{port}")
    return any(v == tid and ep != f"{c.e}.{port}" and net.port_type(ep) == "in" for ep, v in net.signal_of.items())


def _clarke(f3):
    """Space phasor (alpha, beta) of three phase functionals: 2/3 (x1 - x2/2 - x3/2), 2/3 sqrt(3)/2 (x2 - x3)."""
    a, b = {}, {}
    for cf, f in zip((2 / 3, -1 / 3, -1 / 3), f3):
        for x, v in f.items():
            a[x] = a.get(x, 0.0) + cf * v
    for cf, f in zip((0.0, 3 ** 0.5 / 3, -3 ** 0.5 / 3), f3):
        for x, v in f.items():
            b[x] = b.get(x, 0.0) + cf * v
    return a, b


def _stamp_poly(c: _Ctx):
    kn, p = c.d["kind"], c.p
    if kn in ("ac_voltage", "ac_resistor", "ac_inductor", "ac_capacitor"):
        mods = C.KINDS[kn]["mods"]
        for k in range(1, 4):
            v, i = c.var(f"v[{k}]"), c.var(f"i[{k}]")
            a, b = _ph("plug_p", k), _ph("plug_n", k)
            c.eq({v: 1, c.pot(a): -1, c.pot(b): 1})
            c.eq({i: 1, c.flow(a): -1})
            c.eq({c.flow(a): 1, c.flow(b): 1})
            c.path(f"v[{k}]", {v: 1.0})
            c.path(f"i[{k}]", {i: 1.0})
            if kn == "ac_voltage":
                c.eq({v: 1}, None, [(-1.0, Sine(mods["V"](p)[k - 1], p["f"], mods["phase"](p)[k - 1]))])
            elif kn == "ac_resistor":
                c.eq({v: 1, i: -p["R"]})
            elif kn == "ac_inductor":
                c.eq({v: 1}, {i: -p["L"]})
                _init_phase(c, "i0", k, {i: 1.0})
            else:
                c.eq({i: 1}, {v: -p["C"]})
                _init_phase(c, "v0", k, {v: 1.0})
    elif kn == "star":
        for k in range(1, 4):
            c.eq({c.pot(_ph("plug_p", k)): 1, c.pot("pin_n"): -1})
        c.eq({c.flow("pin_n"): 1, **{c.flow(_ph("plug_p", k)): 1 for k in range(1, 4)}})
    elif kn == "delta":
        for j in range(1, 4):
            nxt = j % 3 + 1
            c.eq({c.pot(_ph("plug_n", j)): 1, c.pot(_ph("plug_p", nxt)): -1})
            c.eq({c.flow(_ph("plug_n", j)): 1, c.flow(_ph("plug_p", nxt)): 1})
    elif kn == "plug_to_pin":
        k = int(p["k"])
        c.eq({c.pot(_ph("plug_p", k)): 1, c.pot("pin_p"): -1})
        c.eq({c.flow(_ph("plug_p", k)): 1, c.flow("pin_p"): 1})
        for j in range(1, 4):
            if j != k:
                c.eq({c.flow(_ph("plug_p", j)): 1})
    elif kn in ("ac_current_rms", "ac_voltage_rms"):
        out = "I" if kn == "ac_current_rms" else "V"
        fs = []
        for k in range(1, 4):
            a, b = _ph("plug_p", k), _ph("plug_n", k)
            if kn == "ac_current_rms":
                c.eq({c.pot(a): 1, c.pot(b): -1})
                c.eq({c.flow(a): 1, c.flow(b): 1})
                fs.append({c.flow(a): 1.0})
            else:
                c.eq({c.flow(a): 1})
                c.eq({c.flow(b): 1})
                fs.append({c.pot(a): 1.0, c.pot(b): -1.0})
        c.eq({c.sig(out): 1})                        # stand-in: the output is computed afterwards
        c.s.funcs[c.net.mpath(f"{c.e}.{out}")] = ("rms", fs)
        if _sig_used(c, out):
            c.approx("a quasi-RMS value driving other blocks (nonlinear)")
    elif kn == "ac_power":
        vs, is_ = [], []
        for k in range(1, 4):
            a, ni, nv = _ph("plug_p", k), _ph("plug_ni", k), _ph("plug_nv", k)
            c.eq({c.pot(a): 1, c.pot(ni): -1})
            c.eq({c.flow(a): 1, c.flow(ni): 1})
            c.eq({c.flow(nv): 1})
            vs.append({c.pot(a): 1.0, c.pot(nv): -1.0})
            is_.append({c.flow(a): 1.0})
        va, vb = _clarke(vs)
        ia, ib = _clarke(is_)
        c.eq({c.sig("P"): 1})
        c.eq({c.sig("Q"): 1})
        c.s.products[c.net.mpath(f"{c.e}.P")] = [(1.5, va, ia), (1.5, vb, ib)]
        c.s.products[c.net.mpath(f"{c.e}.Q")] = [(-1.5, va, ib), (1.5, vb, ia)]
        if _sig_used(c, "P") or _sig_used(c, "Q"):
            c.approx("electrical power driving other blocks (nonlinear)")


def _init_phase(c: _Ctx, name, k, functional):
    value, given = c.d["inits"][name]
    v = value[k - 1] if isinstance(value, list) else value
    c.init(f"{name}[{k}]", functional, v, c.k["inits"][name]["priority"], given)


# ------------------------------------------------------------------------------------------- AC machines and drives
AC_PARTS = {"im_core", "pmsm_core", "pmsm_damper_core", "terminal_box", "vf_controller", "signal_ac_voltage",
            "signal_ac_current", "dq_to_three_phase"}


def _stamp_ac(c: _Ctx):
    """Until the nonlinear reference (V3.3): the machines and the drives' blocks get stand-ins with their states
    (three stator currents, two rotor currents, speed and angle; the V/f integrator), enough for the choice of
    fixed initial values and the singularity check, never for numbers."""
    kn, p = c.d["kind"], c.p
    if kn == "terminal_box":
        for k in range(1, 4):
            sp, su, sn = _ph("plug_sp", k), _ph("plugSupply", k), _ph("plug_sn", k)
            c.eq({c.pot(sp): 1, c.pot(su): -1})
            if p["terminal"] == "Y":
                c.eq({c.flow(sp): 1, c.flow(su): 1})
            else:
                prev = _ph("plug_sn", (k - 2) % 3 + 1)        # delta: plug_sn[j] joins plug_sp[j + 1]
                c.eq({c.pot(prev): 1, c.pot(sp): -1})
                c.eq({c.flow(sp): 1, c.flow(su): 1, c.flow(prev): 1})
        if p["terminal"] == "Y":
            c.eq({c.pot(_ph("plug_sn", 1)): 1, c.pot(_ph("plug_sn", 2)): -1})
            c.eq({c.pot(_ph("plug_sn", 2)): 1, c.pot(_ph("plug_sn", 3)): -1})
            c.eq({c.pot(_ph("plug_sn", 1)): 1, c.pot("starpoint"): -1})
            c.eq({c.flow("starpoint"): 1, **{c.flow(_ph("plug_sn", k)): 1 for k in range(1, 4)}})
        else:
            c.eq({c.pot("starpoint"): 1})      # no star point in delta (its node, unconnected, carries no current)
        return
    if kn in ("signal_ac_voltage", "signal_ac_current"):
        x = "v" if kn == "signal_ac_voltage" else "i"
        for k in range(1, 4):
            v, i = c.var(f"v[{k}]"), c.var(f"i[{k}]")
            a, b = _ph("plug_p", k), _ph("plug_n", k)
            c.eq({v: 1, c.pot(a): -1, c.pot(b): 1})
            c.eq({i: 1, c.flow(a): -1})
            c.eq({c.flow(a): 1, c.flow(b): 1})
            c.eq({v if x == "v" else i: 1, c.sig(f"{x}[{k}]"): -1})
            c.path(f"v[{k}]", {v: 1.0})
            c.path(f"i[{k}]", {i: 1.0})
        return
    if kn == "vf_controller":
        _stamp_vf(c)
        return
    if kn == "dq_to_three_phase":
        _stamp_dq3(c)
        return
    _stamp_machine(c)


PHI3 = [k * 2 * math.pi / 3 for k in range(3)]       # symmetricOrientation(3)


def _alpha(a20, Tref):
    return a20 / (1 + a20 * (Tref - 293.15))


def _stamp_vf(c: _Ctx):
    """MSL VfController: der(x) = 2 pi u; amplitude = sqrt(2) VNominal (|u| < fNominal ? |u|/fNominal : 1);
    y[k] = amplitude sin(x - (k-1) 2 pi/3)."""
    p = c.p
    x, am, u = c.var("x"), c.var("amplitude"), c.sig("u")
    VN, fN = p["VNominal"], p["fNominal"]
    c.eq({u: 2 * math.pi}, {x: -1})
    c.eq({am: 1}, None, None, (lambda v, VN=VN, fN=fN: -SQ2 * VN * (abs(v[0]) / fN if abs(v[0]) < fN else 1.0), [u]))
    for k in range(1, 4):
        th = -PHI3[k - 1]
        c.eq({c.sig(f"y[{k}]"): 1}, None, None, (lambda v, th=th: -v[0] * math.sin(v[1] + th), [am, x]))
    c.path("x", {x: 1.0})
    c.path("amplitude", {am: 1.0})
    c.init("x", {x: 1.0}, 0.0, 0, True, forced=True)


def _stamp_dq3(c: _Ctx):
    """MSL DQToThreePhase: y[k] = sqrt(2) (d cos(phi_k - p phi) + q sin(phi_k - p phi)), phi_k = (k-1) 2 pi/3."""
    pp = c.p["p"]
    d, q, ph = c.sig("d"), c.sig("q"), c.sig("phi")
    for k in range(1, 4):
        th = PHI3[k - 1]
        c.eq({c.sig(f"y[{k}]"): 1}, None, None,
             (lambda v, th=th: -SQ2 * (v[0] * math.cos(th - pp * v[2]) + v[1] * math.sin(th - pp * v[2])), [d, q, ph]))


def _clarke_rows(xs):
    """(alpha, beta, zero) functionals of three phase variables (MSL: 2/3 sum x_k (cos, sin)(phi_k); sum/3)."""
    a = {x: 2 / 3 * math.cos(th) for x, th in zip(xs, PHI3)}
    b = {x: 2 / 3 * math.sin(th) for x, th in zip(xs, PHI3)}
    z = {x: 1 / 3 for x in xs}
    return a, b, z


def _loss_torque(PRef, wRef, power_w, w, factor=1.0):
    """The MSL loss torques (Friction, StrayLoad, PermanentMagnetLosses) on the flange: tauRef sign(w) (|w|/wRef)^pw,
    friction linear below 0.001 wRef."""
    if PRef <= 0:
        return 0.0
    tauRef = PRef / wRef
    return factor * tauRef * math.copysign(1.0, w) * (abs(w) / wRef) ** power_w


def _friction_torque(PRef, wRef, power_w, w):
    if PRef <= 0:
        return 0.0
    tauRef = PRef / wRef
    wL = 0.001 * wRef
    if abs(w) > wL:
        return tauRef * math.copysign(1.0, w) * (abs(w) / wRef) ** power_w
    return tauRef * (wL / wRef) ** power_w * w / wL


def _stamp_machine(c: _Ctx):
    """IM_SquirrelCage and SM_PermanentMagnet, written independently of MSL's structure (same equations):
    stator phases -> Rs (Ts) -> space phasor u = 2/3 sum u_k e^(j phi_k), zero sequence u0 = Lszero d i0/dt; core
    conductance Gc = PRef/(3 VRef^2) across u; stray inductance Lssigma; airgap. IM: stator frame, states the stray
    current i_L and the rotor current i_r (stator frame): u - Gc... ; Lm d(i_L + i_r)/dt + Lrsigma d i_r/dt
    + Rr i_r - p w J (Lm (i_L + i_r) + Lrsigma i_r) = 0. PMSM: rotor frame (gamma = p phi), states i_dq (and the
    damper's i_D); psi = (Lmd (i_d + Ie - i_Dd), Lmq (i_q - i_Dq)). Torque 3/2 p (i x psi); loss torques of
    friction, stray load and the magnet; rotor inertia Jr. Nonlinear terms: the rotation, speed x flux, torque,
    the loss torques, temperature-dependent resistances."""
    kn, p = c.d["kind"], c.p
    pp = p["p"]
    m3 = (1, 2, 3)
    th = c.d.get("enabled", set())
    a_s = _alpha(p["alpha20s"], p["TsRef"])
    ws = 2 * math.pi * p["fsNominal"]
    # stator: phases
    vs = [c.var(f"vs[{k}]") for k in m3]
    is_ = [c.var(f"is[{k}]") for k in m3]
    u = [c.var(f"u[{k}]") for k in m3]
    for k, v_, i_, u_ in zip(m3, vs, is_, u):
        a, b = _ph("plug_sp", k), _ph("plug_sn", k)
        c.eq({v_: 1, c.pot(a): -1, c.pot(b): 1})
        c.eq({i_: 1, c.flow(a): -1})
        c.eq({c.flow(a): 1, c.flow(b): 1})
        hp = f"heatPortStatorWinding{k}"
        if hp in th and a_s != 0:
            T = c.pot(hp)
            c.eq({u_: 1, v_: -1}, None, None,
                 (lambda v, R=p["Rs"], a_=a_s, Tr=p["TsRef"]: R * (1 + a_ * (v[1] - Tr)) * v[0], [i_, T]))
        else:
            Tk = c.pot(hp) if hp in th else None
            R = p["Rs"] * (1 + a_s * (p["TsOperational"] - p["TsRef"])) if Tk is None else p["Rs"]
            c.eq({u_: 1, v_: -1, i_: R})
        c.path(f"vs[{k}]", {v_: 1.0})
        c.path(f"is[{k}]", {i_: 1.0})
        _init_phase(c, "is0", k, {i_: 1.0})
    ua, ub, u0 = c.var("u_a"), c.var("u_b"), c.var("u0")
    ia, ib, i0 = c.var("i_a"), c.var("i_b"), c.var("i0")
    for x, f_ in zip((ua, ub, u0), _clarke_rows(u)):
        c.eq({x: 1, **{k_: -v_ for k_, v_ in f_.items()}})
    for x, f_ in zip((ia, ib, i0), _clarke_rows(is_)):
        c.eq({x: 1, **{k_: -v_ for k_, v_ in f_.items()}})
    Ls0 = p.get("Lszero") or p["Lssigma"]
    c.eq({u0: 1}, {i0: -Ls0})
    c.path("i_0_s", {i0: -1.0})
    Gc = 0.0
    if (p.get("PRef_statorCore") or 0) > 0:
        Gc = p["PRef_statorCore"] / (3 * p["VRef_statorCore"] ** 2)
    iLa, iLb = c.var("airGap.i_ss[1]"), c.var("airGap.i_ss[2]")
    Lsig = p["Lssigma"]
    phi, w = c.var("phiMechanical"), c.var("wMechanical")
    tau = c.var("tauElectrical")
    if kn == "im_core":
        Lm, Lr, Rr = p["Lm"], p["Lrsigma"], p["Rr"]
        a_r = _alpha(p["alpha20r"], p["TrRef"])
        ira, irb = c.var("airGap.i_rs[1]"), c.var("airGap.i_rs[2]")
        # i_sp = i_L + Gc u
        c.eq({ia: 1, iLa: -1, ua: -Gc})
        c.eq({ib: 1, iLb: -1, ub: -Gc})
        # u = Lssigma d i_L/dt + Lm d(i_L + i_r)/dt
        c.eq({ua: 1}, {iLa: -(Lsig + Lm), ira: -Lm})
        c.eq({ub: 1}, {iLb: -(Lsig + Lm), irb: -Lm})
        # rotor in the stator frame
        rot_hp = "heatPortRotorWinding" in th
        Trw = c.pot("heatPortRotorWinding") if rot_hp else None
        RrOp = Rr * (1 + a_r * (p["TrOperational"] - p["TrRef"])) if not rot_hp else Rr

        def rotor(v, comp, Lm=Lm, Lr=Lr, Rr=RrOp, a_r=a_r if rot_hp else 0.0, Tr=p["TrRef"], pp=pp):
            iL1, iL2, ir1, ir2, w_ = v[:5]
            R_ = Rr * (1 + a_r * (v[5] - Tr)) if len(v) > 5 else Rr
            we = pp * w_
            psi1, psi2 = Lm * (iL1 + ir1) + Lr * ir1, Lm * (iL2 + ir2) + Lr * ir2
            # - we J psi: J = [[0, -1], [1, 0]] -> (we psi2, -we psi1)
            return (R_ * ir1 + we * psi2) if comp == 0 else (R_ * ir2 - we * psi1)
        deps = [iLa, iLb, ira, irb, w] + ([Trw] if rot_hp and a_r != 0 else [])
        c.eq({}, {iLa: Lm, ira: Lm + Lr}, None, (lambda v: rotor(v, 0), deps))
        c.eq({}, {iLb: Lm, irb: Lm + Lr}, None, (lambda v: rotor(v, 1), deps))
        # torque 3/2 p Lm (iL_b ir_a - iL_a ir_b)
        c.eq({tau: 1}, None, None, (lambda v, k_=1.5 * pp * Lm: -k_ * (v[1] * v[2] - v[0] * v[3]), [iLa, iLb, ira, irb]))
        psa, psb = c.var("airGap.psi_ms[1]"), c.var("airGap.psi_ms[2]")
        c.eq({psa: 1, iLa: -Lm, ira: -Lm})
        c.eq({psb: 1, iLb: -Lm, irb: -Lm})
        c.path("airGap.psi_ms[1]", {psa: 1.0})
        c.path("airGap.psi_ms[2]", {psb: 1.0})
        c.path("airGap.i_ms[1]", {iLa: 1.0, ira: 1.0})
        c.path("airGap.i_ms[2]", {iLb: 1.0, irb: 1.0})
        c.path("airGap.i_ss[1]", {iLa: 1.0})
        c.path("airGap.i_ss[2]", {iLb: 1.0})
        # MSL's ir: the cage current in the rotor frame = -R(gamma)^T i_rs
        c.func("ir[1]", lambda a1, a2, f_, pp=pp: -(np.cos(pp * f_) * a1 + np.sin(pp * f_) * a2), [ira, irb, phi])
        c.func("ir[2]", lambda a1, a2, f_, pp=pp: -(-np.sin(pp * f_) * a1 + np.cos(pp * f_) * a2), [ira, irb, phi])
        # initial cage currents: the rotor-frame value at the initial angle
        g0 = pp * c.d["inits"]["phi0"][0]
        for k, (ca, cb) in enumerate(((-math.cos(g0), -math.sin(g0)), (math.sin(g0), -math.cos(g0))), 1):
            _init_phase(c, "ir0", k, {ira: ca, irb: cb})
        rot_loss = lambda a1, a2, R_=RrOp: 1.5 * R_ * (a1 ** 2 + a2 ** 2)
        rot_deps = [ira, irb]
    else:
        Lmd, Lmq = p["Lmd"], p["Lmq"]
        Ie = SQ2 * p["VsOpenCircuit"] / (Lmd * ws)
        dmp = kn == "pmsm_damper_core"
        idd, iqq = c.var("airGap.i_sr[1]"), c.var("airGap.i_sr[2]")
        if dmp:
            iDd, iDq = c.var("iD[1]"), c.var("iD[2]")
            Rd, Rq = p["Rrd"], p.get("Rrq") or p["Rrd"]
            Ld, Lq = p["Lrsigmad"], p.get("Lrsigmaq") or p["Lrsigmad"]
            a_r = _alpha(p["alpha20r"], p["TrRef"])
            fr = 1 + a_r * (p["TrOperational"] - p["TrRef"])
            # damper: Lmd d(i_d - i_Dd)/dt = R_D i_Dd + L_D d i_Dd/dt (rotor-side voltage d psi_mr/dt)
            c.eq({iDd: -Rd * fr}, {idd: Lmd, iDd: -(Lmd + Ld)})
            c.eq({iDq: -Rq * fr}, {iqq: Lmq, iDq: -(Lmq + Lq)})
            c.path("ir[1]", {iDd: -1.0})
            c.path("ir[2]", {iDq: -1.0})
            _init_phase(c, "ir0", 1, {iDd: -1.0})
            _init_phase(c, "ir0", 2, {iDq: -1.0})
        # stator in the rotor frame: R^T u = Lssigma (i' + we J i) + psi' + we J psi
        bd = {idd: -(Lsig + Lmd)}
        bq = {iqq: -(Lsig + Lmq)}
        if dmp:
            bd[iDd] = Lmd
            bq[iDq] = Lmq

        def stat(v, comp, Lmd=Lmd, Lmq=Lmq, Ie=Ie, Lsig=Lsig, pp=pp, dmp=dmp):
            id_, iq_, w_ = v[:3]
            Dd, Dq = (v[3], v[4]) if dmp else (0.0, 0.0)
            we = pp * w_
            psd, psq = Lmd * (id_ + Ie - Dd), Lmq * (iq_ - Dq)
            # J x = (-x2, x1)
            if comp == 0:
                return -we * (-(Lsig * iq_) - psq)
            return -we * (Lsig * id_ + psd)
        vd, vq = c.var("v_d"), c.var("v_q")
        deps = [idd, iqq, w] + ([iDd, iDq] if dmp else [])
        c.eq({vd: 1}, bd, None, (lambda v: stat(v, 0), deps))
        c.eq({vq: 1}, bq, None, (lambda v: stat(v, 1), deps))
        # the rotation between the frames, written in the direction the network drives: a voltage-fed machine
        # (orientation "v": v_dq = R^T u, i_sp = R i_dq) or a current-fed one ("c": u = R v_dq, i_dq = R^T i_L)
        rot = lambda a1, a2, g: (math.cos(g) * a1 - math.sin(g) * a2, math.sin(g) * a1 + math.cos(g) * a2)
        rotT = lambda a1, a2, g: (math.cos(g) * a1 + math.sin(g) * a2, -math.sin(g) * a1 + math.cos(g) * a2)
        if c.s.orient.get(c.e, "v") == "v":
            for k_, x in enumerate((vd, vq)):
                c.eq({x: -1}, None, None, (lambda v, k_=k_, pp=pp: rotT(v[0], v[1], pp * v[2])[k_], [ua, ub, phi]))
            # i_sp = R i_dq + Gc u (the stray current in the stator frame)
            for k_, (x, ux) in enumerate(((ia, ua), (ib, ub))):
                c.eq({x: 1, ux: -Gc}, None, None, (lambda v, k_=k_, pp=pp: -rot(v[0], v[1], pp * v[2])[k_], [idd, iqq, phi]))
        else:
            for k_, x in enumerate((ua, ub)):
                c.eq({x: 1}, None, None, (lambda v, k_=k_, pp=pp: -rot(v[0], v[1], pp * v[2])[k_], [vd, vq, phi]))
            c.eq({iLa: 1, ia: -1, ua: Gc})
            c.eq({iLb: 1, ib: -1, ub: Gc})
            for k_, x in enumerate((idd, iqq)):
                c.eq({x: 1}, None, None, (lambda v, k_=k_, pp=pp: -rotT(v[0], v[1], pp * v[2])[k_], [iLa, iLb, phi]))

        def torque(v, Lmd=Lmd, Lmq=Lmq, Ie=Ie, pp=pp, dmp=dmp):
            id_, iq_ = v[0], v[1]
            Dd, Dq = (v[2], v[3]) if dmp else (0.0, 0.0)
            return -1.5 * pp * (iq_ * Lmd * (id_ + Ie - Dd) - id_ * Lmq * (iq_ - Dq))
        c.eq({tau: 1}, None, None, (torque, [idd, iqq] + ([iDd, iDq] if dmp else [])))
        psd_, psq_ = c.var("airGap.psi_mr[1]"), c.var("airGap.psi_mr[2]")
        c.eq({psd_: 1, idd: -Lmd, **({iDd: Lmd} if dmp else {})}, None, [(-Lmd * Ie, Const(1.0))])
        c.eq({psq_: 1, iqq: -Lmq, **({iDq: Lmq} if dmp else {})})
        c.path("airGap.psi_mr[1]", {psd_: 1.0})
        c.path("airGap.psi_mr[2]", {psq_: 1.0})
        c.path("airGap.i_sr[1]", {idd: 1.0})
        c.path("airGap.i_sr[2]", {iqq: 1.0})
        if dmp:
            c.func("airGap.i_mr[1]", lambda a1, a2, Ie=Ie: a1 + Ie - a2, [idd, iDd])
            c.path("airGap.i_mr[2]", {iqq: 1.0, iDq: -1.0})
        else:
            c.func("airGap.i_mr[1]", lambda a1, Ie=Ie: a1 + Ie, [idd])
            c.path("airGap.i_mr[2]", {iqq: 1.0})
        c.func("airGap.i_ss[1]", lambda a1, a2, f_, pp=pp: np.cos(pp * f_) * a1 - np.sin(pp * f_) * a2, [idd, iqq, phi])
        c.func("airGap.i_ss[2]", lambda a1, a2, f_, pp=pp: np.sin(pp * f_) * a1 + np.cos(pp * f_) * a2, [idd, iqq, phi])
        if c.s.orient.get(c.e, "v") == "v":
            for k_, x in enumerate((iLa, iLb)):
                c.eq({x: 1}, None, None, (lambda v, k_=k_, pp=pp: -rot(v[0], v[1], pp * v[2])[k_], [idd, iqq, phi]))
        if dmp:
            rot_loss = lambda a1, a2, Rd=Rd * fr, Rq=Rq * fr: 1.5 * (Rd * a1 ** 2 + Rq * a2 ** 2)
            rot_deps = [iDd, iDq]
        else:
            rot_loss, rot_deps = None, []
    # mechanics: Jr dw/dt = tau + flange.tau - friction - stray - magnet losses
    c.eq({phi: 1, c.pot("flange"): -1})
    c.eq({w: 1}, {phi: -1})
    fr_ = (p["PRef_friction"], p.get("wRef_friction") or ws / pp, p["power_w_friction"])
    st_ = (p.get("PRef_strayLoad") or 0.0, p.get("IRef_strayLoad") or 1.0, p.get("wRef_strayLoad") or ws / pp,
           p.get("power_w_strayLoad") or 1.0)
    pm_ = (p.get("PRef_pm") or 0.0, p.get("IRef_pm") or 1.0, p.get("wRef_pm") or ws / pp)

    def losses_t(v):
        w_, i1, i2, i3 = v
        irms2 = (i1 * i1 + i2 * i2 + i3 * i3) / 3
        tf = _friction_torque(fr_[0], fr_[1], fr_[2], w_)
        ts = _loss_torque(st_[0], st_[2], st_[3], w_, irms2 / st_[1] ** 2)
        tp = _loss_torque(pm_[0], pm_[2], 1.0, w_, (math.sqrt(irms2) / pm_[1]) ** 2) if pm_[0] > 0 else 0.0
        return tf, ts, tp
    c.eq({tau: -1, c.flow("flange"): -1}, {w: p["Jr"]}, None, (lambda v: sum(losses_t(v)), [w] + is_))
    c.path("phiMechanical", {phi: 1.0})
    c.path("wMechanical", {w: 1.0})
    c.path("tauElectrical", {tau: 1.0})
    c.path("tauShaft", {c.flow("flange"): -1.0})
    _init(c, "w0", {w: 1.0})
    _init(c, "phi0", {phi: 1.0})
    # power balance (MSL powerBalance.*) as outputs
    Rs_op = p["Rs"] * (1 + a_s * (p["TsOperational"] - p["TsRef"]))
    cl = [(1.5 * Gc, {ua: 1.0}, {ua: 1.0}), (1.5 * Gc, {ub: 1.0}, {ub: 1.0})]
    va, vb, _ = _clarke_rows(vs)
    pr = c.s.products
    pr[f"{c.e}.powerBalance.powerStator"] = [(1.5, va, {ia: 1.0}), (1.5, vb, {ib: 1.0})]
    pr[f"{c.e}.powerBalance.powerMechanical"] = [(-1.0, {w: 1.0}, {c.flow("flange"): 1.0})]
    pr[f"{c.e}.powerBalance.lossPowerStatorWinding"] = [(Rs_op, {x: 1.0}, {x: 1.0}) for x in is_]
    pr[f"{c.e}.powerBalance.lossPowerStatorCore"] = cl
    pr[f"{c.e}.powerBalance.lossPowerRotorCore"] = [(0.0, {w: 1.0}, {w: 1.0})]
    c.func("powerBalance.lossPowerFriction", lambda w_: np.vectorize(lambda x: _friction_torque(*fr_, x) * x)(w_), [w])
    c.func("powerBalance.lossPowerStrayLoad",
           lambda w_, i1, i2, i3: np.vectorize(lambda x, a, b, d: _loss_torque(st_[0], st_[2], st_[3], x,
                                                                            (a * a + b * b + d * d) / 3 / st_[1] ** 2) * x)(w_, i1, i2, i3),
           [w] + is_)
    if kn != "im_core":
        c.func("powerBalance.lossPowerPermanentMagnet",
               lambda w_, i1, i2, i3: np.vectorize(lambda x, a, b, d: (_loss_torque(pm_[0], pm_[2], 1.0, x,
                                                                                    (a * a + b * b + d * d) / 3 / pm_[1] ** 2) if pm_[0] > 0 else 0.0) * x)(w_, i1, i2, i3),
               [w] + is_)
    if rot_loss is not None:
        c.func("powerBalance.lossPowerRotorWinding", rot_loss, rot_deps)
    parts = ["lossPowerStatorWinding", "lossPowerStatorCore", "lossPowerFriction", "lossPowerStrayLoad"]
    parts += (["lossPowerRotorWinding"] if rot_loss is not None else []) + (["lossPowerPermanentMagnet"] if kn != "im_core" else [])
    deps_all = sorted(set([w, ua, ub] + is_ + rot_deps))

    def total(*cols, deps_all=deps_all):
        v = dict(zip(deps_all, cols))
        out = Rs_op * (v[is_[0]] ** 2 + v[is_[1]] ** 2 + v[is_[2]] ** 2) + 1.5 * Gc * (v[ua] ** 2 + v[ub] ** 2)
        out = out + np.vectorize(lambda x: _friction_torque(*fr_, x) * x)(v[w])
        irms2 = (v[is_[0]] ** 2 + v[is_[1]] ** 2 + v[is_[2]] ** 2) / 3
        out = out + np.vectorize(lambda x, r2: _loss_torque(st_[0], st_[2], st_[3], x, r2 / st_[1] ** 2) * x)(v[w], irms2)
        if kn != "im_core" and pm_[0] > 0:
            out = out + np.vectorize(lambda x, r2: _loss_torque(pm_[0], pm_[2], 1.0, x, r2 / pm_[1] ** 2) * x)(v[w], irms2)
        if rot_loss is not None:
            out = out + rot_loss(*[v[d] for d in rot_deps])
        return out
    c.func("powerBalance.lossPowerTotal", total, deps_all)
    # heat ports: the losses leave as heat (nonlinear terms)
    if th:
        for k, i_ in zip(m3, is_):
            hp = f"heatPortStatorWinding{k}"
            if a_s != 0:
                c.eq({c.flow(hp): 1}, None, None,
                     (lambda v, R=p["Rs"], a_=a_s, Tr=p["TsRef"]: R * (1 + a_ * (v[1] - Tr)) * v[0] ** 2, [i_, c.pot(hp)]))
            else:
                c.eq({c.flow(hp): 1}, None, None, (lambda v, R=p["Rs"]: R * v[0] ** 2, [i_]))
        c.eq({c.flow("heatPortStatorCore"): 1}, None, None, (lambda v, G=Gc: 1.5 * G * (v[0] ** 2 + v[1] ** 2), [ua, ub]))
        c.eq({c.flow("heatPortRotorCore"): 1})
        c.eq({c.flow("heatPortFriction"): 1}, None, None, (lambda v: _friction_torque(*fr_, v[0]) * v[0], [w]))
        c.eq({c.flow("heatPortStrayLoad"): 1}, None, None,
             (lambda v: _loss_torque(st_[0], st_[2], st_[3], v[0], (v[1] ** 2 + v[2] ** 2 + v[3] ** 2) / 3 / st_[1] ** 2) * v[0],
              [w] + is_))
        if "heatPortPermanentMagnet" in c.k["ports"]:
            c.eq({c.flow("heatPortPermanentMagnet"): 1}, None, None,
                 (lambda v: (_loss_torque(pm_[0], pm_[2], 1.0, v[0], (v[1] ** 2 + v[2] ** 2 + v[3] ** 2) / 3 / pm_[1] ** 2)
                             if pm_[0] > 0 else 0.0) * v[0], [w] + is_))
        if "heatPortRotorWinding" in c.k["ports"]:
            if kn == "im_core":
                a_r = _alpha(p["alpha20r"], p["TrRef"])
                c.eq({c.flow("heatPortRotorWinding"): 1}, None, None,
                     (lambda v, R=p["Rr"], a_=a_r, Tr=p["TrRef"]: 1.5 * R * (1 + a_ * (v[2] - Tr)) * (v[0] ** 2 + v[1] ** 2),
                      rot_deps + [c.pot("heatPortRotorWinding")]))
            else:
                c.eq({c.flow("heatPortRotorWinding"): 1}, None, None, (lambda v: rot_loss(v[0], v[1]), rot_deps))

# ------------------------------------------------------------------------------------------- coolant loops
G_N = 9.80665           # Modelica.Constants.g_n
TWO_PORT_FLUID = {"pipe", "valve", "volume_flow", "signal_volume_flow", "pressure_increase", "signal_pressure_increase",
                  "ideal_pump", "volume_flow_sensor"}


class _At:
    """A stand-in context for _constant_driver."""
    def __init__(self, net, e):
        self.net, self.e = net, e


def _friction(p):
    """(laminar slope dpL/VL, VL, k) of MSL SimpleFriction with the catalog's defaults."""
    VL, dpL = C.KINDS["pipe"]["mods"]["V_flowLaminar"](p), C.KINDS["pipe"]["mods"]["dpLaminar"](p)
    VN, dpN = p["V_flowNominal"], p["dpNominal"]
    k = (dpN - dpL / VL * VN) / (VN - VL) ** 2
    return dpL / VL, VL, k


def _fric_dp(V, slope, VL, k):
    if V > VL:
        return slope * V + k * (V - VL) ** 2
    if V < -VL:
        return slope * V - k * (V + VL) ** 2
    return slope * V


def _valve_kv(p, y):
    yl = max(min(y, p["y1"]), 0.0)
    if p["LinearCharacteristic"]:
        return p["Kv1"] * (p["kv0"] + (1 - p["kv0"]) * yl / p["y1"])
    return p["Kv1"] * p["kv0"] * math.exp(math.log(1 / p["kv0"]) * yl / p["y1"])


def hydraulics(net) -> dict:
    """The coolant loops' pressures and flows (no storage: algebraic), solved once by Newton on the MSL relations,
    for constant sources. Returns {"p": {node: Pa}, "m": {element port: kg/s into the component}, "V": {elem: m3/s},
    "dp": {elem: Pa}, "exact": bool, "why": [...]}; signal-driven sources that are not constant and machine-driven
    pumps get stand-in values (exact = False)."""
    elems = [e for e in sorted(net.elems) if C.KINDS[net.elems[e]["kind"]].get("domain") == "fluid"]
    out = {"p": {}, "m": {}, "m_eff": {}, "V": {}, "dp": {}, "exact": True, "why": []}
    if not elems:
        return out
    nodes = sorted({net.node_of[f"{e}.{pt}"] for e in elems for pt, t in C.KINDS[net.elems[e]["kind"]]["ports"].items()
                    if t == "fluid"})
    ni = {n: i for i, n in enumerate(nodes)}
    two = [e for e in elems if net.elems[e]["kind"] in TWO_PORT_FLUID]
    one = [e for e in elems if net.elems[e]["kind"] not in TWO_PORT_FLUID]
    nP, nV, nQ = len(nodes), len(two), len(one)
    rho = {e: C.medium_props(net.elems[e]["params"]["medium"])["rho"] for e in elems}
    rel = {}
    pscale, vscale = 1.0, 0.0
    for e in two:
        d = net.elems[e]
        kn, p = d["kind"], d["params"]
        if kn == "pipe":
            sl, VL, k = _friction(p)
            hg = rho[e] * G_N * p["h_g"]
            rel[e] = lambda V, dp, sl=sl, VL=VL, k=k, hg=hg: dp - (_fric_dp(V, sl, VL, k) + hg)
            pscale, vscale = max(pscale, p["dpNominal"]), max(vscale, p["V_flowNominal"])
        elif kn == "valve":
            y = _constant_driver(_At(net, e), "y")
            if y is None:
                out["exact"] = False
                out["why"].append(f"{d['comp']}: valve opening is not a constant")
                y = p["y1"]
            Kv = _valve_kv(p, y)
            a = p["dp0"] * rho[e] / C.KINDS["valve"]["mods"]["rho0"](p)
            rel[e] = lambda V, dp, a=a, Kv=Kv: dp - a * (V / Kv) * abs(V / Kv)
            pscale, vscale = max(pscale, p["dp0"]), max(vscale, p["Kv1"])
        elif kn in ("volume_flow", "signal_volume_flow"):
            Vc = p.get("constantVolumeFlow") if kn == "volume_flow" else _constant_driver(_At(net, e), "volumeFlow")
            if Vc is None:
                out["exact"] = False
                out["why"].append(f"{d['comp']}: volume flow input is not a constant")
                Vc = 1e-4
            rel[e] = lambda V, dp, Vc=Vc: (V - Vc) * 1e8
            vscale = max(vscale, abs(Vc))
        elif kn in ("pressure_increase", "signal_pressure_increase"):
            P_ = p.get("constantPressureIncrease") if kn == "pressure_increase" else _constant_driver(_At(net, e), "pressureIncrease")
            if P_ is None:
                out["exact"] = False
                out["why"].append(f"{d['comp']}: pressure increase input is not a constant")
                P_ = 1e4
            rel[e] = lambda V, dp, P_=P_: dp + P_
            pscale = max(pscale, abs(P_))
        elif kn == "ideal_pump":
            out["exact"] = False
            out["why"].append(f"{d['comp']}: a pump driven by its flange (speed from the mechanics)")
            dp0, V0 = p["dp0"], p["V_flow0"]
            rel[e] = lambda V, dp, dp0=dp0, V0=V0: dp + dp0 * (1 - V / V0)
            pscale, vscale = max(pscale, dp0), max(vscale, V0)
        else:                                            # volume_flow_sensor
            rel[e] = lambda V, dp: dp
    for e in one:
        d = net.elems[e]
        if d["kind"] == "ambient":
            pscale = max(pscale, abs(d["params"]["constantAmbientPressure"]))
        elif d["kind"] == "pressure_reference":
            pscale = max(pscale, abs(d["params"]["p"]))
    vscale = vscale or 1e-3
    rscale = max(rho.values())

    def unpack(x):
        return x[:nP] * pscale, x[nP:nP + nV] * vscale, x[nP + nV:] * vscale * rscale

    def resid(x):
        P_, V, Q = unpack(x)
        r = []
        bal = np.zeros(nP)
        for j, e in enumerate(two):
            na, nb = ni[net.node_of[f"{e}.flowPort_a"]], ni[net.node_of[f"{e}.flowPort_b"]]
            r.append(rel[e](V[j], P_[na] - P_[nb]) / pscale)
            bal[na] += rho[e] * V[j]
            bal[nb] -= rho[e] * V[j]
        for j, e in enumerate(one):
            d = net.elems[e]
            n = ni[net.node_of[f"{e}.flowPort"]]
            bal[n] += Q[j]
            if d["kind"] == "ambient":
                r.append((P_[n] - d["params"]["constantAmbientPressure"]) / pscale)
            elif d["kind"] == "pressure_reference":
                r.append((P_[n] - d["params"]["p"]) / pscale)
            else:
                r.append(Q[j] / (vscale * rscale))
        return np.concatenate([np.array(r), bal / (vscale * rscale)])

    x = np.zeros(nP + nV + nQ)

    def jac(x):
        J = np.zeros((x.size, x.size))
        for i in range(x.size):
            h = 1e-6 * max(1.0, abs(x[i]))
            xp, xm = x.copy(), x.copy()
            xp[i] += h
            xm[i] -= h
            J[:, i] = (resid(xp) - resid(xm)) / (2 * h)
        return J

    J = jac(x)
    for it in range(200):
        f = resid(x)
        nf = np.linalg.norm(f)
        if nf < 1e-15:
            break
        J = jac(x)
        dx = np.linalg.lstsq(J, -f, rcond=None)[0]
        lam = 1.0
        while lam > 1e-8 and np.linalg.norm(resid(x + lam * dx)) > (1 - 1e-4 * lam) * nf:
            lam /= 2
        x = x + lam * dx
        if np.linalg.norm(lam * dx) < 1e-15 * max(1.0, np.linalg.norm(x)):
            break
    if np.linalg.norm(resid(x)) > 1e-11:
        raise ValueError("the coolant loop's pressures and flows did not converge (reference)")
    if np.linalg.matrix_rank(J, tol=1e-10 * max(1.0, np.abs(J).max())) < x.size:
        raise RefError("the coolant loop's pressures or flows are not unique (a loop of volume-flow sources, or "
                       "pressure sources in parallel?)")
    P_, V, Q = unpack(x)
    for n, i in ni.items():
        out["p"][n] = float(P_[i])
    for j, e in enumerate(two):
        out["V"][e] = float(V[j])
        na, nb = net.node_of[f"{e}.flowPort_a"], net.node_of[f"{e}.flowPort_b"]
        out["dp"][e] = float(P_[ni[na]] - P_[ni[nb]])
        out["m"][f"{e}.flowPort_a"] = float(rho[e] * V[j])
        out["m"][f"{e}.flowPort_b"] = float(-rho[e] * V[j])
    for j, e in enumerate(one):
        out["m"][f"{e}.flowPort"] = float(Q[j])
    # flows the enthalpy equations use: a component without flow gets a tiny one (a -> b), so that its temperature
    # and the junctions stay determined (a stand-in: with no flow the mixing temperatures are not unique)
    tiny = 1e-12 * vscale * rscale
    out["m_eff"] = dict(out["m"])
    for e in two:
        if abs(out["m"][f"{e}.flowPort_a"]) <= tiny:
            out["exact"] = False
            out["why"].append(f"{net.elems[e]['comp']}: no flow")
            out["m_eff"][f"{e}.flowPort_a"] = 1e-9 * vscale * rho[e]
            out["m_eff"][f"{e}.flowPort_b"] = -1e-9 * vscale * rho[e]
    return out


def _semilinear(c, port, m, h_own_terms, src=None):
    """H_flow = semiLinear(m_flow, port.h, h): m > 0 (into the component) carries the node's h, m < 0 the
    component's own h (a linear functional, or a constant through src)."""
    H = c.hflow(port)
    tiny = 1e-15
    if m > tiny:
        c.eq({H: 1, c.hpot(port): -m})
    elif m < -tiny:
        row = {H: 1}
        for x, cf in h_own_terms.items():
            row[x] = row.get(x, 0) - m * cf
        c.eq(row, None, [(-m * src, Const(1.0))] if src is not None else None)
    else:
        c.eq({H: 1})


def _stamp_fluid(c: _Ctx):
    kn, p = c.d["kind"], c.p
    hyd = getattr(c.s, "hyd", None)
    if hyd is None:                                   # a stand-alone stamp (catalog tests): unit flows a -> b
        ports = [q for q, t in c.k["ports"].items() if t == "fluid"]
        m0 = {f"{c.e}.flowPort_a": 1e-3, f"{c.e}.flowPort_b": -1e-3, f"{c.e}.flowPort": 0.0}
        hyd = {"m": {f"{c.e}.{q}": m0[f"{c.e}.{q}"] for q in ports}, "V": {c.e: 1e-6}, "dp": {c.e: 1.0},
               "exact": False, "why": ["stand-alone"]}
        hyd["m_eff"] = hyd["m"]
    med = C.medium_props(p["medium"])
    rho, cp, cv = med["rho"], med["cp"], med["cv"]
    if not hyd["exact"] and not getattr(c.s, "fluid_noted", False):
        c.s.fluid_noted = True
        c.s.exact = False
        c.s.not_exact.append("coolant loop: " + ("; ".join(hyd["why"][:3]) or "not covered"))
    if kn in TWO_PORT_FLUID:
        a, b = "flowPort_a", "flowPort_b"
        ma = hyd["m"][f"{c.e}.{a}"]
        me = hyd["m_eff"][f"{c.e}.{a}"]
        # hydraulics: the port flows from the solution (mass balance), the pressures are fixed at the nodes
        c.eq({c.flow(a): 1}, None, [(-ma, Const(1.0))])
        c.eq({c.flow(a): 1, c.flow(b): 1})
        V, dp, T, Q = c.var("V_flow"), c.var("dp"), c.var("T"), c.var("Q_flow")
        c.eq({V: 1, c.flow(a): -1 / rho})
        c.eq({dp: 1, c.pot(a): -1, c.pot(b): 1})
        Ta, Tb, dT = c.var("T_a"), c.var("T_b"), c.var("dT")
        c.eq({Ta: 1, c.hpot(a): -1 / cp})
        c.eq({Tb: 1, c.hpot(b): -1 / cp})
        if me >= 0:
            c.eq({dT: 1, T: -1, Ta: 1})
        else:
            c.eq({dT: 1, Tb: -1, T: 1})
        for nm, x in (("V_flow", V), ("dp", dp), ("T", T), ("Q_flow", Q), ("T_a", Ta), ("T_b", Tb), ("dT", dT)):
            c.path(nm, {x: 1.0})
        # energy: m cv dT/dt = H_a + H_b + Q
        m_med = p.get("m") or 0.0
        if m_med > 1e-300:                           # Modelica.Constants.small
            c.eq({c.hflow(a): 1, c.hflow(b): 1, Q: 1}, {T: -m_med * cv})
        else:
            c.eq({c.hflow(a): 1, c.hflow(b): 1, Q: 1})
        if "T0" in c.k["inits"]:
            _init(c, "T0", {T: 1.0})                 # free only with medium inside (m > 0)
        _semilinear(c, a, me, {T: cp})
        _semilinear(c, b, -me, {T: cp})
        # Q: heat port and friction fed to the medium
        q_fric = 0.0
        if kn == "pipe":
            sl, VL, k = _friction(p)
            Vv = hyd["V"][c.e]
            pd = _fric_dp(Vv, sl, VL, k)
            q_fric = p["frictionLoss"] * Vv * pd
            pdv, qf = c.var("pressureDrop"), c.var("Q_friction")
            c.eq({pdv: 1, dp: -1}, None, [(rho * G_N * p["h_g"], Const(1.0))])
            c.eq({qf: 1}, None, [(-q_fric, Const(1.0))])
            c.path("pressureDrop", {pdv: 1.0})
            c.path("Q_friction", {qf: 1.0})
            c.path("T_q", {T: 1.0})                   # tapT = 1
            if c.enabled("heatPort"):
                c.eq({c.pot("heatPort"): 1, T: -1})
                c.eq({Q: 1, c.flow("heatPort"): -1}, None, [(-q_fric, Const(1.0))])
            else:
                c.eq({Q: 1}, None, [(-q_fric, Const(1.0))])
        elif kn == "valve":
            q_fric = p["frictionLoss"] * hyd["V"][c.e] * hyd["dp"][c.e]
            c.eq({Q: 1}, None, [(-q_fric, Const(1.0))])
        else:
            c.eq({Q: 1})
        if kn == "ideal_pump":
            fl = c.flow("flange_a")
            w = p["wNominal"]
            c.eq({fl: 1}, None, [(hyd["dp"][c.e] * hyd["V"][c.e] / w, Const(1.0))])     # tau w = -dp V (stand-in w)
            c.path("w", {c.var("w"): 1.0})
            c.eq({c.var("w"): 1}, {c.pot("flange_a"): -1})
        if kn == "volume_flow_sensor":
            c.eq({c.sig("y"): 1, V: -1})
    else:
        port = "flowPort"
        m = hyd["m"][f"{c.e}.{port}"]
        c.eq({c.flow(port): 1}, None, [(-m, Const(1.0))])
        if kn == "ambient":
            Tamb = p["constantAmbientTemperature"]
            _semilinear(c, port, m, {}, src=cp * Tamb)
            tp = c.var("T_port")
            c.eq({tp: 1, c.hpot(port): -1 / cp})
            c.path("T_port", {tp: 1.0})
        else:
            c.eq({c.hflow(port): 1})
            if kn == "fluid_temperature_sensor":
                c.eq({c.sig("y"): 1, c.hpot(port): -1 / cp})
            elif kn == "fluid_pressure_sensor":
                c.eq({c.sig("y"): 1, c.pot(port): -1})


def _fluid_nodes(s: "System", net, nodes: dict):
    """Node equations of the coolant loops: the pressure from the hydraulic solution (the mass balance holds in
    it), and the enthalpy balance sum(H_flow) = 0; a node without flow takes the mean h of its components
    (a stand-in: no unique mixing temperature)."""
    hyd = s.hyd
    for nid in sorted(nodes):
        eps = nodes[nid]
        s.eq({f"n{nid}": 1.0}, None, [(-hyd["p"][nid], Const(1.0))])
        if any(abs(hyd["m_eff"].get(ep, 0.0)) > 1e-15 for ep in eps):
            s.eq({f"{ep}#H": 1.0 for ep in sorted(eps)})
        else:
            s.exact = False
            s.not_exact.append(f"a coolant junction without flow ({', '.join(sorted(net.elems[ep.split('.')[0]]['comp'] for ep in eps)[:3])})")
            row = {f"n{nid}#h": 1.0}
            temps = [f"{ep.split('.')[0]}.T" for ep in eps if f"{ep.split('.')[0]}.T" in s.idx]
            for x in temps:
                cp = C.medium_props(net.elems[x.split('.')[0]]["params"]["medium"])["cp"]
                row[x] = row.get(x, 0) - cp / len(temps)
            s.eq(row, None, None if temps else [(-C.medium_props(net.elems[eps[0].split('.')[0]]["params"]["medium"])["cp"] * 293.15, Const(1.0))])


def _pid(c, k, Ti, Td, Nd, u, um, with_i, with_d, wp=1.0, wd=0.0):
    """MSL PID (additive form) and the structure of LimPID: y = k*(e_p + I + D)."""
    y = c.sig("y")
    ep = {u: wp} if um is not None else {u: 1.0}
    ed = {u: wd} if um is not None else {u: 1.0}
    if um is not None:
        ep[um] = ep.get(um, 0) - 1.0
        ed[um] = ed.get(um, 0) - 1.0
    row = {y: 1.0}
    for x, cf in ep.items():
        row[x] = row.get(x, 0) - k * cf
    if with_i:
        iy = c.var("I.y")
        c.eq({x: cf for x, cf in ep.items()}, {iy: -Ti})          # Ti*dI/dt = e (MSL: I.k = 1/Ti)
        row[iy] = -k
        c.path("I.y", {iy: 1.0})
        c.init("I.y", {iy: 1.0}, 0.0, 0, True, forced=True)
    if with_d:
        dx, dy = c.var("D.x"), c.var("D.y")
        T = max(Td / Nd, 100 * EPS)
        if abs(Td) < EPS:
            c.eq({}, {dx: 1})
            c.eq({dy: 1})
        else:
            c.eq({**{x: cf for x, cf in ed.items()}, dx: -1}, {dx: -T})
            c.eq({dy: 1, **{x: -Td / T * cf for x, cf in ed.items()}, dx: Td / T})
        row[dy] = -k
        c.path("D.x", {dx: 1.0})
        c.init("D.x", {dx: 1.0}, 0.0, 0, True, forced=True)
    merged = {}
    for x, cf in row.items():
        merged[x] = merged.get(x, 0) + cf
    c.eq(merged)


def _lim_pid(c):
    """MSL LimPID: P on wp u_s - u_m, D (Td s/(1 + Td/Nd s)) on wd u_s - u_m, I (1/Ti) on u_s - u_m plus the
    anti-windup track (y - yu)/(k Ni); yu = k (P + I + D); y = limiter(yu) between yMin and yMax."""
    p = c.p
    ct, k, Ti, Td, Nd, Ni = p["controllerType"], p["k"], p["Ti"], p["Td"], p["Nd"], p["Ni"]
    yMax, yMin = p["yMax"], C.KINDS["lim_pid"]["mods"]["yMin"](p)
    us, um, y = c.sig("u_s"), c.sig("u_m"), c.sig("y")
    yu = c.var("limiter.u")
    c.path("limiter.u", {yu: 1.0})
    row = {yu: 1.0, us: -k * p["wp"], um: k}
    if "I" in ct:
        iy, tr = c.var("I.y"), c.var("gainTrack.y")
        c.eq({tr: 1, y: -1 / (k * Ni), yu: 1 / (k * Ni)})
        c.eq({us: 1, um: -1, tr: 1}, {iy: -Ti})          # Ti dI/dt = u_s - u_m + track
        row[iy] = -k
        c.path("I.y", {iy: 1.0})
        c.init("I.y", {iy: 1.0}, 0.0, 0, True, forced=True)
    if "D" in ct:
        dx, dy = c.var("D.x"), c.var("D.y")
        T = max(Td / Nd, 1e-14)
        ed = {us: p["wd"], um: -1.0}
        if abs(Td) < EPS:
            c.eq({}, {dx: 1})
            c.eq({dy: 1})
        else:
            c.eq({**ed, dx: -1}, {dx: -T})
            c.eq({dy: 1, **{x: -Td / T * cf for x, cf in ed.items()}, dx: Td / T})
        row[dy] = row.get(dy, 0) - k
        c.path("D.x", {dx: 1.0})
        c.init("D.x", {dx: 1.0}, 0.0, 0, True, forced=True)
    merged = {}
    for x, cf in row.items():
        merged[x] = merged.get(x, 0) + cf
    c.eq(merged)
    md = c.mode("mid")
    if md in (None, "mid"):
        c.eq({y: 1, yu: -1})
        if md == "mid":
            c.guard({yu: 1.0}, -yMax, 1, "high")
            c.guard({yu: 1.0}, -yMin, -1, "low")
    elif md == "high":
        c.eq({y: 1}, None, [(-yMax, Const(1.0))])
        c.guard({yu: 1.0}, -yMax, -1, "mid")
    else:
        c.eq({y: 1}, None, [(-yMin, Const(1.0))])
        c.guard({yu: 1.0}, -yMin, 1, "mid")


def _tf(c, b, a):
    """MSL TransferFunction: controllable canonical form on x_scaled, states 0 at start."""
    na, nb = len(a), len(b)
    nx = na - 1
    bb = [0.0] * max(0, na - nb) + list(b)
    d = bb[0] / a[0]
    norm = math.sqrt(sum(x * x for x in a))
    a_end = a[-1] if abs(a[-1]) > 100 * EPS * norm else 1.0
    y, u = c.sig("y"), c.sig("u")
    if nx == 0:
        c.eq({y: 1, u: -d})
        return
    xs = [c.var(f"x_scaled[{i + 1}]") for i in range(nx)]
    row = {u: a_end}
    for i in range(nx):
        row[xs[i]] = row.get(xs[i], 0) - a[i + 1]
    c.eq(row, {xs[0]: -a[0]})
    for i in range(1, nx):
        c.eq({xs[i - 1]: 1}, {xs[i]: -1})
    out = {y: 1.0, u: -d}
    for i in range(nx):
        out[xs[i]] = out.get(xs[i], 0) - (bb[i + 1] - d * a[i + 1]) / a_end
    c.eq(out)
    for i in range(nx):
        c.init(f"x[{i + 1}]", {xs[i]: 1.0}, 0.0, 0, True, forced=True)


def _constant_driver(c: _Ctx, port):
    """The value of a constant block driving this signal input, or None."""
    net = c.net
    target = net.signal_of[f"{c.e}.{port}"]
    for e, d in net.elems.items():
        if d["kind"] == "constant" and net.signal_of.get(f"{e}.y") == target:
            return d["params"]["k"]
    return None


# ------------------------------------------------------------------------------------------- numerics
def _particular_on(H, Hg, prefer):
    """A particular solution P of H P = -Hg supported on as few non-preferred columns as possible (column-pivoted
    QR with the preferred columns weighted up), or None. With the algebraic variables preferred, the states stay in
    V w only (the min-norm solution spreads a constraint over the states it mentions)."""
    from scipy.linalg import qr
    if not H.shape[0]:
        return None
    r_, c_ = _equil(H)
    Hs = H * r_[:, None] * c_[None, :]
    wgt = np.where(prefer, 1e3, 1.0)
    _, Rq, piv = qr(Hs * wgt[None, :], pivoting=True, mode="economic")
    dg = np.abs(np.diag(Rq))
    if not dg.size or dg[0] == 0:
        return None
    rk = int(np.sum(dg > 1e-10 * dg[0]))
    cols = np.sort(piv[:rk])
    sol = np.linalg.lstsq(Hs[:, cols], -(Hg * r_[:, None]), rcond=None)[0]
    P = np.zeros((H.shape[1], Hg.shape[1]))
    P[cols] = c_[cols, None] * sol
    if np.max(np.abs(H @ P + Hg), initial=0) > 1e-9 * max(1.0, np.max(np.abs(Hg), initial=0)):
        return None
    return P


def _equil(M):
    """Row and column scalings (powers of 2) that bring every nonzero row and column max to ~1."""
    M = np.abs(np.asarray(M, float))
    r = np.ones(M.shape[0])
    c = np.ones(M.shape[1])
    for _ in range(6):
        S = M * r[:, None] * c[None, :]
        rm = S.max(axis=1) if S.shape[1] else np.zeros(S.shape[0])
        r = np.where(rm > 0, r / np.where(rm > 0, 2.0 ** np.round(np.log2(np.where(rm > 0, rm, 1))), 1), r)
        S = M * r[:, None] * c[None, :]
        cm = S.max(axis=0) if S.shape[0] else np.zeros(S.shape[1])
        c = np.where(cm > 0, c / np.where(cm > 0, 2.0 ** np.round(np.log2(np.where(cm > 0, cm, 1))), 1), c)
    return r, c


def _rank_split(M, tol=1e-9):
    """Left null space N and row-space rows R of M (both in M's original row coordinates)."""
    if M.size == 0:
        return np.zeros((M.shape[0], M.shape[0])), np.zeros((0, M.shape[0]))
    r, c = _equil(M)
    Ms = M * r[:, None] * c[None, :]
    U, s, _ = np.linalg.svd(Ms)
    rank = int(np.sum(s > tol * (s[0] if s.size and s[0] > 0 else 1.0)))
    N = U[:, rank:].T * r[None, :]
    R = U[:, :rank].T * r[None, :]
    return N, R


def _null_right(H, n, tol=1e-9):
    """Basis of the right null space of H (n columns), and H's rank."""
    if H.shape[0] == 0:
        return np.eye(n), 0
    r, c = _equil(H)
    Hs = H * r[:, None] * c[None, :]
    _, s, Vt = np.linalg.svd(Hs)
    rank = int(np.sum(s > tol * (s[0] if s.size and s[0] > 0 else 1.0)))
    V = (Vt[rank:].T) * c[:, None]
    return V, rank


def nonlinear_on() -> bool:
    """The nonlinear reference (machines, speed loads): on unless OMCT_REF_NONLINEAR is 0 / false / no / off."""
    return os.environ.get("OMCT_REF_NONLINEAR", "1").strip().lower() not in ("0", "false", "no", "off")


class RefError(ValueError):
    pass


class Ref:
    """The study's equations, reduced; built from a network.Net without errors."""

    MAXORD = 4

    def __init__(self, net, free_inputs: dict | None = None, modes: dict | None = None, base=None, orient=None):
        self.net = net
        self.base = base or self
        s = System()
        s.modes = modes
        s.orient = dict(orient or {})
        self.sys = s
        s.hyd = hydraulics(net)
        self.hydraulics = s.hyd
        for e in sorted(net.elems):
            _stamp(_Ctx(s, net, e))
        # node balances (flows into the components at a node sum to zero); coolant nodes: see _fluid_nodes
        nodes, fluid, qsn = {}, {}, {}
        for ep, nid in net.node_of.items():
            pt = net.port_type(ep)
            (fluid if pt == "fluid" else qsn if pt == "qs_pin" else nodes).setdefault(nid, []).append(ep)
        for nid in sorted(nodes):
            s.eq({f"{ep}#flow": 1.0 for ep in sorted(nodes[nid])})
        for nid in sorted(qsn):                  # quasi-static nodes: real and imaginary parts
            for part in ("#re", "#im"):
                s.eq({f"{ep}#flow{part}": 1.0 for ep in sorted(qsn[nid])})
        _fluid_nodes(s, net, fluid)
        # free (linearisation) inputs
        self.input_fns = {}
        for name, ep in sorted((net.free_inputs or {}).items()):
            f = Input(name)
            self.input_fns[name] = f
            s.eq({f"s{net.signal_of[ep]}": 1.0}, None, [(-1.0, f)])
        for f in s.cascades:
            f.ref = self.base
        self._casc_on, self._casc_frozen, self._in_stage1, self._stage1 = False, None, False, None
        self._mode_cache = {}
        self.events = sorted(s.pwl) if base is None else []     # piecewise-linear elements (base system)
        self.exact = s.exact
        self.not_exact = s.not_exact
        if s.nl and s.cascades:
            _cascades_to_nl(s)                # with a nonlinear system the heat of losses is a nonlinear term too
        if s.nl and s.pwl:
            self.exact = s.exact = False
            s.not_exact.append("piecewise-linear elements together with nonlinear machines (not combined)")
        n = len(s.vars)
        if len(s.rows) != n:
            raise RefError(f"{len(s.rows)} equations for {n} variables (a reference-implementation bug, or an "
                           f"unconnected signal input)")
        self.n = n
        nf = len(s.fns)
        K = self.MAXORD + 1
        A = np.zeros((n, n))
        B = np.zeros((n, n))
        G = np.zeros((n, nf * K))
        for i, (a, b, src) in enumerate(s.rows):
            for x, cf in a.items():
                A[i, s.idx[x]] += cf
            for x, cf in b.items():
                B[i, s.idx[x]] += cf
            for cf, f in src:
                G[i, s.fn_idx[id(f)] * K] += cf
        self.nonlinear = bool(s.nl)
        if self.nonlinear and not nonlinear_on():
            self.exact = False
            self.not_exact.append("nonlinear terms (AC machines and drives, speed-dependent loads): the nonlinear "
                                  "reference is switched off (OMCT_REF_NONLINEAR=0)")
        if self.nonlinear:
            # the nonlinear terms: kept for the integrator; their linearisation at z = 0 (angles 0, speeds 0) makes
            # the linear stand-in with the same states, for validation and the choice of initial values
            self._nl = [(r, fn, np.array([s.idx[d] for d in deps])) for r, fn, deps in s.nl]
            self.A_lin0, self.G_lin0 = A.copy(), G.copy()
            one = self.sys.fn(Const(1.0)) if not hasattr(self, "_one") else self._one
            self._one = one
            if len(s.fns) * K > G.shape[1]:
                G = np.hstack([G, np.zeros((n, len(s.fns) * K - G.shape[1]))])
            A_raw, G_raw = A.copy(), G.copy()
            z0 = np.zeros(n)
            g0 = self._g(z0)
            Jg0 = self._jg(z0)
            A = A + Jg0
            G[:, s.fn_idx[id(one)] * K] += g0
        self.A0, self.B0, self.G0 = A.copy(), B.copy(), G.copy()
        self.to_base = np.arange(n)

        def reduce_scaled(A, B, G):
            # rows scaled by powers of 2 (exact) to comparable size before the reduction mixes them
            rmax = np.maximum(np.abs(A).max(axis=1, initial=0), np.abs(B).max(axis=1, initial=0))
            rs = np.where(rmax > 0, 2.0 ** -np.round(np.log2(np.where(rmax > 0, rmax, 1.0))), 1.0)
            self._reduce(A * rs[:, None], B * rs[:, None], G * rs[:, None])
        try:
            reduce_scaled(A, B, G)
        except RefError:
            if not self.nonlinear:
                raise
            # the linearisation at 0 can lose couplings (a quasi-static machine's voltages are omega times its
            # fluxes: at omega = 0 they vanish): linearise at a generic point instead (structure only)
            zg = np.random.default_rng(20261021).uniform(0.3, 1.0, n) * np.where(np.arange(n) % 2, 1.0, -1.0)
            Jg = self._jg(zg)
            A = A_raw + Jg
            G = G_raw.copy()
            G[:, s.fn_idx[id(one)] * K] += self._g(zg) - Jg @ zg
            self.A0, self.G0 = A.copy(), G.copy()
            reduce_scaled(A, B, G)
        for f in s.cascades:                # their second derivatives are not available
            j = s.fn_idx[id(f)]
            cols = slice(j * K + 2, (j + 1) * K)
            big = 1e-9 * max(np.abs(self.P).max(initial=0), np.abs(self.Gz).max(initial=0), 1e-300)
            if np.abs(self.P[:, cols]).max(initial=0) > big or np.abs(self.Gz[:, cols]).max(initial=0) > big:
                self.exact = False
                self.not_exact.append("heat from losses into a network that needs its second derivative")
                break

    # ---- index reduction ------------------------------------------------------------------------------
    def _reduce(self, A, B, G):
        n, K = self.n, self.MAXORD + 1
        Hs, Hg = [], []
        for _ in range(self.MAXORD + 1):
            N, R = _rank_split(B)
            if N.shape[0] == 0:
                break
            NA, NG = N @ A, N @ G
            Hs.append(NA)
            Hg.append(NG)
            # derivative of the constraints: NA dz/dt + NG F'(t) = 0
            NGs = np.zeros_like(NG)
            for j in range(NG.shape[1] // K):
                for o in range(K - 1):
                    NGs[:, j * K + o + 1] = NG[:, j * K + o]
                if np.any(NG[:, j * K + K - 1] != 0):
                    raise RefError("index too high for the reference implementation")
            A = np.vstack([R @ A, np.zeros_like(NA)])
            B = np.vstack([R @ B, NA])
            G = np.vstack([R @ G, NGs])
        else:
            raise RefError("the system is singular (no unique solution): check grounds, fixed points, and loops of "
                           "sources")
        Nl, _ = _rank_split(B)
        if Nl.shape[0]:
            raise RefError("the system is singular (no unique solution)")
        Binv = np.linalg.solve(B, np.eye(n))
        self.Az = -Binv @ A
        self.Gz = -Binv @ G
        H = np.vstack(Hs) if Hs else np.zeros((0, n))
        Hgm = np.vstack(Hg) if Hg else np.zeros((0, G.shape[1]))
        V, rank = _null_right(H, n)
        self.H, self.Hg, self.V, self.d = H, Hgm, V, n - rank
        if H.shape[0]:
            r, c = _equil(H)
            Hp = (np.linalg.pinv(H * r[:, None] * c[None, :], rcond=1e-10) * c[:, None]) * r[None, :]
            self.P = -Hp @ Hgm           # z_p(t) = P F(t)
            # again with the columns scaled by the variables' sizes (flows of 1e-5 next to enthalpies of 1e6):
            # the same solution, accurate relative to each variable
            mag = np.abs(self.P).max(axis=1, initial=0)
            if np.any(mag > 0):
                mag = np.where(mag > 0, 2.0 ** np.round(np.log2(np.where(mag > 0, mag, 1.0))), 1.0)
                Hm = H * mag[None, :]
                r2, c2 = _equil(Hm)
                Hp2 = (np.linalg.pinv(Hm * r2[:, None] * c2[None, :], rcond=1e-10) * c2[:, None]) * r2[None, :]
                P2 = -(mag[:, None] * (Hp2 @ Hgm))
                if np.max(np.abs(H @ P2 + Hgm), initial=0) <= np.max(np.abs(H @ self.P + Hgm), initial=0) * 10 + 1e-300:
                    self.P = P2
            if np.max(np.abs(H @ self.P + Hgm), initial=0) > 1e-6 * max(1.0, np.max(np.abs(Hgm), initial=0)):
                raise RefError("the constraints are inconsistent (a loop of sources that disagree?)")
        else:
            self.P = np.zeros((n, G.shape[1]))
        self.Vp = np.linalg.pinv(V) if V.size else np.zeros((0, n))
        self.Ad = self.Vp @ self.Az @ V if V.size else np.zeros((0, 0))

    # ---- sources ----------------------------------------------------------------------------------------
    def F(self, t, side=1, shift=0):
        K = self.MAXORD + 1
        out = np.zeros(len(self.sys.fns) * K)
        for j, f in enumerate(self.sys.fns):
            for o in range(K):
                out[j * K + o] = f.val(t, o + shift, side)
        return out

    def breaks(self, t0, t1, ends=False):
        """The sources' breakpoints inside (t0, t1); with ends, also those at t0 and t1."""
        b = set()
        lo, hi = (t0 - 1e-300, t1 + abs(t1) * 1e-15 + 1e-300) if ends else (t0, t1)
        for f in self.sys.fns:
            b.update(x for x in f.breaks(lo - abs(lo) * 1e-12 - 1e-12, hi + abs(hi) * 1e-12 + 1e-12) if
                     (t0 <= x <= t1 if ends else t0 < x < t1))
        return sorted(b)

    # ---- initial values ---------------------------------------------------------------------------------
    def init_selection(self):
        """(selected, dependent_given, missing): the candidate initial values in priority order (forced block
        states, values the study gives, then defaults; priority 1 before 2), each kept when independent of the
        ones before on the consistent states. missing = degrees of freedom left without one."""
        cands = sorted(self.sys.inits, key=lambda x: (0 if x["forced"] else 1 if x["given"] else 2,
                                                      x["priority"], x["elem"], x["name"]))
        selected, dep = [], []
        base = [self.H] if self.H.shape[0] else []
        cur = self._rank(base)
        for cd in cands:
            L = self._functional(cd["f"])[None, :]
            rk = self._rank(base + [L])
            if rk > cur:
                base.append(L)
                selected.append(cd)
                cur = rk
            elif cd["given"]:
                dep.append(cd)
        cur -= self.n - self.d
        self.selected = selected
        return selected, dep, self.d - cur

    def _rank(self, blocks):
        if not blocks:
            return 0
        M = np.vstack(blocks)
        r, c = _equil(M)
        sv = np.linalg.svd(M * r[:, None] * c[None, :], compute_uv=False)
        return int(np.sum(sv > 1e-9 * sv[0])) if sv.size and sv[0] > 0 else 0

    def _functional(self, f):
        L = np.zeros(self.n)
        for x, cf in f.items():
            L[self.sys.idx[x]] += cf
        return L

    def _w_from(self, t, values, side=1):
        """Dynamic coordinates w from the selected initial values at t."""
        if self.d == 0:
            return np.zeros(0)
        zp = self.P @ self.F(t, side)
        Ls = np.array([self._functional(cd["f"]) for cd in self.selected])
        M = Ls @ self.V
        rhs = np.array(values) - Ls @ zp
        return np.linalg.solve(M, rhs)

    # ---- results ------------------------------------------------------------------------------------------
    # ---- nonlinear terms ------------------------------------------------------------------------------------
    def _g(self, z):
        out = np.zeros(self.n)
        for r, fn, dj in self._nl:
            out[r] += fn(z[dj])
        return out

    def _jg(self, z):
        J = np.zeros((self.n, self.n))
        for r, fn, dj in self._nl:
            base = fn(z[dj])
            for k, j in enumerate(dj):
                h = 1e-7 * max(1.0, abs(z[j]))
                zz = z[dj].copy()
                zz[k] += h
                zm = z[dj].copy()
                zm[k] -= h
                J[r, j] += (fn(zz) - fn(zm)) / (2 * h)
        return J

    def simulate(self, t_eval, t0=None):
        """{variable: array} on t_eval (sorted). Storage values carried across each source breakpoint and each
        switching of a piecewise-linear element (events). With losses heating a thermal network (Cascade sources):
        first without them (the loss sources do not depend on them), then with their heat from that solution."""
        if not self.sys.cascades:
            return self._run(t_eval, t0)
        self._stage1, self._casc_on, self._casc_frozen = None, False, None
        self._in_stage1 = True
        try:
            self._run(t_eval, t0)
        finally:
            self._in_stage1 = False
        self._stage1 = self._dense
        self._casc_on = True
        try:
            return self._run(t_eval, t0)
        finally:
            self._casc_on = False

    def _run(self, t_eval, t0):
        if getattr(self, "nonlinear", False):
            return self._simulate_nl(t_eval, t0)
        return self._simulate_events(t_eval, t0) if self.events else self._simulate(t_eval, t0)

    def _nl_ode(self):
        """The linear reduction of the system with the nonlinear terms as inputs u (residual rows: linear + u = 0,
        u = g(z)): z = P [F(t); u; u'] + V w, w' = Ad w + ... . Usable when no term needs u'' and the terms that read
        a part of z needing u' (the 'late' ones: a current-fed machine's voltages) neither drive the dynamics nor
        other terms; they are computed from the state afterwards. None otherwise."""
        if hasattr(self, "_nlode"):
            return self._nlode
        self._nlode = None
        s, n, K = self.sys, self.n, self.MAXORD + 1
        nf = len(s.fns)
        nu = len(self._nl)
        A = self.A_lin0
        B = self.B0
        G = np.zeros((n, (nf + nu) * K))
        G[:, :self.G_lin0.shape[1]] = self.G_lin0
        for j, (r, fn, dj) in enumerate(self._nl):
            G[r, (nf + j) * K] += 1.0
        sub = Ref.__new__(Ref)
        sub.n, sub.MAXORD = n, self.MAXORD
        rmax = np.maximum(np.abs(A).max(axis=1, initial=0), np.abs(B).max(axis=1, initial=0))
        rs = np.where(rmax > 0, 2.0 ** -np.round(np.log2(np.where(rmax > 0, rmax, 1.0))), 1.0)
        try:
            sub._reduce(A * rs[:, None], B * rs[:, None], G * rs[:, None])
        except RefError:
            return None
        # the constrained part on the algebraic variables (columns without a derivative) where possible
        alg = ~np.any(B != 0, axis=0)
        P2 = _particular_on(sub.H, sub.Hg, alg)
        if P2 is not None:
            sub.P = P2
        P = sub.P
        col = lambda j, o: (nf + j) * K + o
        ucols = [col(j, 0) for j in range(nu)]
        d1 = [col(j, 1) for j in range(nu)]
        d2 = [col(j, o) for j in range(nu) for o in range(2, K)]
        big = 1e-9 * max(np.abs(P).max(initial=0), 1e-300)
        if np.abs(P[:, d2]).max(initial=0) > big:
            return None                                         # u'' in z
        nz = lambda cols: set(np.nonzero(np.abs(P[:, cols]).max(axis=1, initial=0) > big)[0].tolist()) if cols else set()
        # late terms: they read rows that need u', or rows that hold other late terms
        late = set()
        while True:
            bad = nz(d1) | nz([ucols[j] for j in late])
            more = {j for j, (_, _, dj) in enumerate(self._nl) if j not in late and bad & set(dj.tolist())}
            if not more:
                break
            late |= more
        L = sorted(late)
        if L and np.abs(P[:, [d1[j] for j in L]]).max(initial=0) > big:
            return None                                         # a late term would need its own derivative
        if sub.d:
            Vp, Az, Gz = sub.Vp, sub.Az, sub.Gz
            # the dynamics' coefficients of u and u' (and u'', which must vanish)
            C0 = Vp @ (Az @ P[:, ucols] + Gz[:, ucols])
            C1 = Vp @ (Az @ P[:, d1] + Gz[:, d1] - P[:, ucols])
            C2 = Vp @ (Az @ P[:, [col(j, 2) for j in range(nu)]] + Gz[:, [col(j, 2) for j in range(nu)]] - P[:, d1])
            sc = 1e-9 * max(1.0, np.abs(C0).max(initial=0), np.abs(sub.Ad).max(initial=0))
            if np.abs(C2).max(initial=0) > sc or np.abs(Vp @ Gz[:, d2]).max(initial=0) > sc:
                return None
            if L and (np.abs(C0[:, L]).max(initial=0) > sc or np.abs(C1[:, L]).max(initial=0) > sc):
                return None                                     # a late term drives the dynamics
            Mv = -C1
            Mv[:, L] = 0.0
        else:
            Mv = np.zeros((0, nu))
        sub.d1 = d1
        # u moving the constrained part along the dynamic coordinates (a voltage from a V/f controller across
        # inductors): the dynamics need u' = Jg z', solved with w' at each evaluation
        sub.Mv = Mv
        sub.need_ud = bool(sub.d and np.abs(Mv).max(initial=0) > 1e-9 * max(1.0, np.abs(P[:, ucols]).max(initial=0)))
        sub.late = L
        sub.early = [j for j in range(nu) if j not in late]
        # the outputs need u' of the early terms where z holds it (a current-fed machine's voltages)
        sub.need_ud_out = sub.need_ud or bool(nz(d1))
        sub.nf, sub.nu, sub.ucols = nf, nu, ucols
        self._nlode = sub
        return sub

    def _simulate_nl_ode(self, sub, t_eval, t0):
        """Integrate the nonlinear system as an ODE in the linear reduction's dynamic coordinates (solve_ivp Radau),
        u = g(z) solved at each evaluation (Newton on the few nonlinear terms)."""
        from scipy.integrate import solve_ivp
        n, K = self.n, self.MAXORD + 1
        nf, nu = sub.nf, sub.nu
        P, V, Vp, Ad, Az, Gz = sub.P, sub.V, sub.Vp, sub.Ad, sub.Az, sub.Gz
        Pu = P[:, sub.ucols]
        Fn_cols = nf * K

        def Fs(t, side, shift=0):
            out = np.zeros((nf + nu) * K)
            out[:Fn_cols] = self.F(t, side, shift)
            return out

        nl = self._nl
        E, Lt = sub.early, sub.late
        u_prev = [np.zeros(nu)]

        def jac_u(z, which=None):
            Jg = np.zeros((nu, n))
            for k in (range(nu) if which is None else which):
                _, fn, dj = nl[k]
                for q, j in enumerate(dj):
                    hh = 6e-6 * max(1.0, abs(z[j]))      # eps^(1/3): central differences to ~1e-11
                    zz = z[dj].copy()
                    zz[q] += hh
                    zm = z[dj].copy()
                    zm[q] -= hh
                    Jg[k, j] = (fn(zz) - fn(zm)) / (2 * hh)
            return Jg

        bigP = 1e-12 * max(np.abs(Pu).max(initial=0), 1e-300)
        dep_on = [set(np.nonzero(np.abs(Pu[dj]).max(axis=0, initial=0) > bigP)[0].tolist()) for _, _, dj in nl]
        topo_cache = {}

        def topo(which):
            """The terms of 'which' as strongly connected components in dependency order (Tarjan): each block
            reads only blocks before it; a block of one term without a self-loop is evaluated directly."""
            if which not in topo_cache:
                ws = set(which)
                index, low, stack, on, blocks = {}, {}, [], set(), []
                counter = [0]

                def strong(v):
                    index[v] = low[v] = counter[0]
                    counter[0] += 1
                    stack.append(v)
                    on.add(v)
                    for x in sorted(dep_on[v] & ws):
                        if x not in index:
                            strong(x)
                            low[v] = min(low[v], low[x])
                        elif x in on:
                            low[v] = min(low[v], index[x])
                    if low[v] == index[v]:
                        blk = []
                        while True:
                            x = stack.pop()
                            on.discard(x)
                            blk.append(x)
                            if x == v:
                                break
                        blocks.append(sorted(blk))
                for v in which:
                    if v not in index:
                        strong(v)
                topo_cache[which] = [(blk, len(blk) > 1 or blk[0] in dep_on[blk[0]]) for blk in blocks]
            return topo_cache[which]

        def solve_block(zF, blk, u):
            """Newton on one strongly connected block (plain iteration first while it contracts)."""
            w_ = np.array(blk)
            u0 = u[w_].copy()
            prev = None
            for it in range(4):
                z = zF + Pu @ u
                gu = np.array([nl[k][1](z[nl[k][2]]) for k in blk])
                d = np.max(np.abs(gu - u[w_]), initial=0)
                if d <= 1e-14 * max(1.0, np.max(np.abs(gu), initial=0)):
                    u[w_] = gu
                    return
                if prev is not None and d > 0.5 * prev:          # not contracting: Newton from the start value
                    u[w_] = u0
                    break
                prev = d
                u[w_] = gu
            for it in range(40):
                z = zF + Pu @ u
                gu = np.array([nl[k][1](z[nl[k][2]]) for k in blk])
                r = u[w_] - gu
                if np.max(np.abs(r), initial=0) <= 1e-13 * max(1.0, np.max(np.abs(gu), initial=0)):
                    break
                Jg = jac_u(z, blk)[w_]
                Mu = np.eye(len(blk)) - Jg @ Pu[:, w_]
                u[w_] = u[w_] - np.linalg.solve(Mu, r)

        def solve_set(zF, which, u0):
            """u[which] = g(zF + Pu u)[which], the other entries of u0 fixed, block by block in dependency order."""
            u = u0.copy()
            if not which:
                return u
            for blk, loop in topo(tuple(which)):
                if not loop:
                    k = blk[0]
                    _, fn, dj = nl[k]
                    u[k] = fn(zF[dj] + Pu[dj] @ u)
                else:
                    solve_block(zF, blk, u)
            return u

        def solve_u(zF):
            u = solve_set(zF, E, np.where(np.isin(np.arange(nu), E), u_prev[0], 0.0))
            u_prev[0] = u
            return u

        VpPu = sub.Mv
        Pd1 = P[:, sub.d1]

        def zfull(t, w_, side):
            Fv = Fs(t, side)
            zF = P @ Fv + (V @ w_ if sub.d else 0)
            u = solve_u(zF)
            return zF + Pu @ u, Fv, u

        def closure(seed):
            """The terms whose u' is needed for those of seed: they and the early terms they read."""
            out, todo = set(), list(seed)
            while todo:
                k = todo.pop()
                if k in out or k not in Eset:
                    continue
                out.add(k)
                todo += list(dep_on[k])
            return sorted(out)

        Eset = set(E)
        bigM = 1e-12 * max(np.abs(VpPu).max(initial=0), 1e-300)
        R_dyn = closure(np.nonzero(np.abs(VpPu).max(axis=0, initial=0) > bigM)[0].tolist()) if sub.d else []
        R_out = closure(set(R_dyn) | set(np.nonzero(np.abs(Pd1).max(axis=0, initial=0) > 1e-12 * max(
            np.abs(Pd1).max(initial=0), 1e-300))[0].tolist()))

        def wdot(t, w_, side, z, Fv, u, want_ud=False):
            Fv = Fv.copy()
            Fv[sub.ucols] = u
            Fd = Fs(t, side, shift=1)
            zp = P @ Fv                         # the constrained part (with u); Ad w carries the rest
            a_ = Ad @ w_ + Vp @ (Az @ zp + Gz @ Fv - P @ Fd) if sub.d else np.zeros(0)
            if not (sub.need_ud or want_ud):
                return a_, None
            # u' = Jg z' with z' = P F' + Pu u' + Pd1 u'' (none for the early terms) + V w', w' = a_ - Mv u'
            Jg = jac_u(z, R_out if want_ud else R_dyn)
            Mv = VpPu
            lhs = np.eye(nu) - Jg @ Pu + (Jg @ V @ Mv if sub.d else 0)
            ud = np.linalg.solve(lhs, Jg @ (P @ Fd + (V @ a_ if sub.d else 0)))
            return (a_ - Mv @ ud if sub.d else a_), ud

        def zfull_out(t, w_, side):
            """z at an output point: the early terms, their u' where z holds it, then the late terms."""
            z, Fv, u = zfull(t, w_, side)
            if not sub.need_ud_out and not Lt:
                return z, Fv, u
            _, ud = wdot(t, w_, side, z, Fv, u, want_ud=True)
            zF = z - Pu @ u + Pd1 @ ud
            u = solve_set(zF, Lt, u)
            return zF + Pu @ u, Fv, u

        def rhs_factory(b):
            def rhs(t, w_):
                side = -1 if t >= b else 1
                z, Fv, u = zfull(t, w_, side)
                return wdot(t, w_, side, z, Fv, u)[0]
            return rhs

        # initial values: the selected ones (the stand-in's choice), mapped to w by least squares through z
        Ls = np.array([self._functional(cd["f"]) for cd in self.selected]) if self.selected else np.zeros((0, n))
        vals = np.array([cd["value"] for cd in self.selected])

        def w_from(t, values, side=1):
            if not sub.d:
                return np.zeros(0)
            w_ = np.zeros(sub.d)
            for _ in range(20):
                z, _, _ = zfull_out(t, w_, side)
                r = Ls @ z - values
                if np.max(np.abs(r), initial=0) < 1e-12 * max(1.0, np.max(np.abs(values), initial=0)):
                    break
                Jw = np.zeros((len(values), sub.d))
                for k in range(sub.d):
                    e = np.zeros(sub.d)
                    hh = 1e-7 * max(1.0, abs(w_[k]))
                    e[k] = hh
                    Jw[:, k] = (Ls @ zfull_out(t, w_ + e, side)[0] - Ls @ zfull_out(t, w_ - e, side)[0]) / (2 * hh)
                w_ = w_ - np.linalg.lstsq(Jw, r, rcond=None)[0]
            return w_

        t1 = float(t_eval[-1])
        w_ = w_from(t0, vals)
        Z = np.zeros((len(t_eval), n))
        edges = [t0] + self.breaks(t0, t1) + [t1]
        dense = []
        scale = max(1.0, float(np.max(np.abs(w_), initial=0)))
        for a, b in zip(edges[:-1], edges[1:]):
            if b <= a:
                continue
            last = b == t1
            idx = np.nonzero((t_eval >= a) & ((t_eval < b) | (last & (t_eval <= b))))[0]
            if sub.d:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    sol = solve_ivp(rhs_factory(b), (a, b), w_, method="Radau", rtol=1e-8, atol=1e-10 * scale,
                                    dense_output=True, t_eval=t_eval[idx] if idx.size else None)
                if not sol.success:
                    raise RefError(f"integration failed: {sol.message}")
                w_end = sol.sol(b)
                for jj, i in enumerate(idx):
                    Z[i] = zfull_out(t_eval[i], sol.y[:, jj], -1 if t_eval[i] >= b else 1)[0]
                dense.append((a, b, _ODEDense(sol, zfull_out, b), None, self))
            else:
                w_end = w_
                for i in idx:
                    Z[i] = zfull_out(t_eval[i], w_, -1 if t_eval[i] >= b else 1)[0]
            if not last:
                z_left = zfull_out(b, w_end, -1)[0]
                w_ = w_from(b, Ls @ z_left, side=1)
        self.t, self.Z = t_eval, Z
        self._dense = dense
        return self.values(Z)

    def _alt_orientation(self):
        """The same network with its PMSM cores written for a current feed (u = R v_dq, i_dq = R^T i_L), when that
        makes path (a) usable; the initial values chosen here carry over (same variables). None otherwise."""
        if self.sys.orient:
            return None
        pm = {e: "c" for e, d in self.net.elems.items() if d["kind"] in ("pmsm_core", "pmsm_damper_core")}
        if not pm:
            return None
        alt = Ref(self.net, orient=pm)
        if not alt.nonlinear or alt._nl_ode() is None:
            return None
        alt.selected = self.selected
        return alt

    def _simulate_nl(self, t_eval, t0=None):
        """The nonlinear system: as an ODE in the linear reduction's coordinates when the nonlinear terms do not need
        differentiating (_nl_ode), else with refnl.radau between the sources' breakpoints (consistent initial values
        from the selected ones, then from the left limits at each breakpoint)."""
        from . import refnl
        if not hasattr(self, "selected"):
            sel, dep, missing = self.init_selection()
            if missing:
                raise RefError(f"{missing} state(s) without an initial value")
        t_eval = np.asarray(t_eval, float)
        t0 = float(t_eval[0]) if t0 is None else float(t0)
        sub = self._nl_ode()
        if sub is not None:
            return self._simulate_nl_ode(sub, t_eval, t0)
        alt = self._alt_orientation()
        if alt is not None:
            out = alt._simulate_nl_ode(alt._nl_ode(), t_eval, t0)
            self.t, self._dense = alt.t, alt._dense
            return out
        t1 = float(t_eval[-1])
        n, K = self.n, self.MAXORD + 1
        s = self.sys
        A, B = self.A_lin0, self.B0
        G = self.G_lin0
        if G.shape[1] < len(s.fns) * K:
            G = np.hstack([G, np.zeros((n, len(s.fns) * K - G.shape[1]))])
        rmax = np.maximum(np.abs(A).max(axis=1, initial=0), np.abs(B).max(axis=1, initial=0))
        rs = np.where(rmax > 0, 2.0 ** -np.round(np.log2(np.where(rmax > 0, rmax, 1.0))), 1.0)
        As, Bs, Gs = A * rs[:, None], B * rs[:, None], G * rs[:, None]
        side = [1]

        # the original system: consistent initial values satisfy all of it
        def f(t, z):
            return -(As @ z + Gs @ self.F(t, side[0]) + rs * self._g(z))

        def jac(t, z):
            return -(As + rs[:, None] * self._jg(z))

        sysm = refnl.System(Bs, f, jac)
        alg = [i for i in range(n) if not np.any(Bs[i])]
        # the integrated system: linear constraints on differential variables only (rigidly joined inertias, a
        # capacitor across a source) replaced by their time derivatives, as the shuffle algorithm does
        Ar, Br, Gr, Tg = _reduce_linear_hidden(As, Bs, Gs, rs, [r for r, _, _ in self._nl], K, self._jg(np.zeros(n)))

        bend = [np.inf]

        def fr(t, z):
            sd = -1 if t >= bend[0] else 1          # at the segment's end: the sources' values before the breakpoint
            return -(Ar @ z + Gr @ self.F(t, sd) + Tg @ self._g(z))

        def jacr(t, z):
            return -(Ar + Tg @ self._jg(z))

        sysr = refnl.System(Br, fr, jacr)
        Ls = np.array([self._functional(cd["f"]) for cd in self.selected]) if self.selected else np.zeros((0, n))

        def dF_dt(t):
            return Gs @ self.F(t, side[0], shift=1)

        # first guess: the linear stand-in's consistent state
        wb = self._w_from(t0, [cd["value"] for cd in self.selected])
        zg = self.P @ self.F(t0, 1) + (self.V @ wb if self.d else 0)
        vals = np.array([cd["value"] for cd in self.selected])
        try:
            z = refnl.consistent(sysm, t0, zg, Ls, vals, alg, dF_dt)
        except refnl.DAEError as exc:
            raise RefError(str(exc)) from None
        # error weights: differential components constrained by the algebraic rows count as index 2
        algr = [i for i in range(n) if not np.any(Br[i])]
        diff = np.array([np.any(Br[:, j]) for j in range(n)])
        Jal = (-jacr(t0, z))[algr] if algr else np.zeros((0, n))
        i2 = np.zeros(n, bool)
        if Jal.size:
            U, sv, Vt = np.linalg.svd(Jal, full_matrices=False)
            rk = int(np.sum(sv > 1e-9 * (sv[0] if sv.size else 1)))
            Rw = Vt[:rk]
            for j in np.nonzero(diff)[0]:
                e = np.zeros(n)
                e[j] = 1.0
                i2[j] = np.linalg.norm(e - Rw.T @ (Rw @ e)) < 1e-6
        scale = np.maximum(np.abs(z), 1.0)
        edges = [t0] + self.breaks(t0, t1) + [t1]
        Z = np.zeros((len(t_eval), n))
        dense = []
        for a, b in zip(edges[:-1], edges[1:]):
            if b <= a:
                continue
            side[0] = 1
            bend[0] = b
            last = b == t1
            try:
                steps, z_end = refnl.radau(sysr, a, b, z, rtol=1e-9, atol=np.full(n, 1e-9), index2=i2)
            except refnl.DAEError as exc:
                raise RefError(f"integration failed: {exc}") from None
            for st_ in steps:
                dense.append((st_.t0, st_.t0 + st_.h, st_, None, self))
            idx = np.nonzero((t_eval >= a) & ((t_eval < b) | (last & (t_eval <= b))))[0]
            j = 0
            for i in idx:
                ti = t_eval[i]
                while j < len(steps) - 1 and ti > steps[j].t0 + steps[j].h:
                    j += 1
                Z[i] = steps[j](ti) if steps else z
            if not last:
                side[0] = 1
                bend[0] = np.inf
                vals_b = Ls @ z_end if len(Ls) else np.zeros(0)
                try:
                    z = refnl.consistent(sysm, b, z_end, Ls, vals_b, alg, dF_dt)
                except refnl.DAEError as exc:
                    raise RefError(str(exc)) from None
        self.t, self.Z = t_eval, Z
        self._dense = dense
        return self.values(Z)

    def _stage1_state(self, t, side=1):
        """z and dz/dt (base indexing) of the first run (without the cascades' heat) at t."""
        segs = self._stage1
        seg = segs[-1]
        for sg in segs:
            a, b = sg[0], sg[1]
            if (a <= t < b) or (side < 0 and a < t <= b) or (t == b and sg is segs[-1]):
                seg = sg
                break
        a, b, sol, wconst, r = seg
        if hasattr(sol, "deriv"):             # a step of the nonlinear integrator: z directly
            return sol(t), sol.deriv(t)
        self._in_stage1 = True
        try:
            sd = -1 if (side < 0 or t >= b) else 1
            F0, F1 = r.F(t, sd), r.F(t, sd, shift=1)
            z, zd = r.P @ F0, r.P @ F1
            if sol is not None:
                w = sol.sol(t)
                wd = r.Ad @ w + r.Vp @ (r.Az @ (r.P @ F0) + r.Gz @ F0 - r.P @ F1)
                z, zd = z + r.V @ w, zd + r.V @ wd
            zb, zdb = np.zeros(self.n), np.zeros(self.n)
            zb[r.to_base], zdb[r.to_base] = z, zd
            return zb, zdb
        finally:
            self._in_stage1 = False

    def _wscale(self, w, t0, t1, edges):
        """One absolute scale for the dynamic coordinates: initial values, and how far the sources push them."""
        wscale = float(np.max(np.abs(w), initial=0))
        if self.d:
            span = t1 - t0
            for tt in np.linspace(t0, t1, 7).tolist() + list(edges):
                F0 = self.F(tt, 1)
                drive = self.Vp @ (self.Az @ (self.P @ F0) + self.Gz @ F0 - self.P @ self.F(tt, 1, shift=1))
                wscale = max(wscale, float(np.max(np.abs(drive), initial=0)) * span)
                if np.linalg.cond(self.Ad) < 1e10:
                    wscale = max(wscale, float(np.max(np.abs(np.linalg.solve(self.Ad, drive)))))
        return wscale if wscale > 0 else 1.0

    def _rhs(self, b):
        def rhs(t, w_):
            side = -1 if t >= b else 1        # the solver evaluates at b: use the value before the breakpoint
            F0 = self.F(t, side)
            return self.Ad @ w_ + self.Vp @ (self.Az @ (self.P @ F0) + self.Gz @ F0 - self.P @ self.F(t, side, shift=1))
        return rhs

    def _simulate(self, t_eval, t0=None):
        from scipy.integrate import solve_ivp

        if not hasattr(self, "selected"):
            sel, dep, missing = self.init_selection()
            if missing:
                raise RefError(f"{missing} state(s) without an initial value")
        t_eval = np.asarray(t_eval, float)
        t0 = float(t_eval[0]) if t0 is None else float(t0)
        t1 = float(t_eval[-1])
        Ls = np.array([self._functional(cd["f"]) for cd in self.selected]) if self.selected else np.zeros((0, self.n))
        values = [cd["value"] for cd in self.selected]
        w = self._w_from(t0, values)
        Z = np.zeros((len(t_eval), self.n))
        edges = [t0] + self.breaks(t0, t1) + [t1]
        dense = []
        V, P, Ad = self.V, self.P, self.Ad
        wscale = self._wscale(w, t0, t1, edges)
        for a, b in zip(edges[:-1], edges[1:]):
            if b <= a:
                continue
            rhs = self._rhs(b)
            last = b == t1
            idx = np.nonzero((t_eval >= a) & ((t_eval < b) | (last & (t_eval <= b))))[0]
            te = t_eval[idx]
            if self.d:
                sol = solve_ivp(rhs, (a, b), w, method="Radau", t_eval=te if te.size else None, rtol=1e-10,
                                atol=1e-12 * wscale, jac=Ad, dense_output=True)
                if not sol.success:
                    raise RefError(f"integration failed: {sol.message}")
                w_end = sol.sol(b)
                dense.append((a, b, sol, None, self))
                for j, i in enumerate(idx):
                    Z[i] = P @ self.F(t_eval[i], 1) + V @ sol.y[:, j]
            else:
                w_end = w
                dense.append((a, b, None, w, self))
                for i in idx:
                    Z[i] = P @ self.F(t_eval[i], 1)
            if last and self.d and not np.array_equal(self.F(b, -1), self.F(b, 1)) and idx.size and t_eval[idx[-1]] == b:
                # a source steps exactly at the end: the storage values carry over, the rest takes the new value
                z_left = P @ self.F(b, -1) + V @ w_end
                w_b = self._w_from(b, list(Ls @ z_left) if len(Ls) else [], side=1)
                Z[idx[-1]] = P @ self.F(b, 1) + V @ w_b
            if not last:
                # carry the storage values across the breakpoint
                z_left = P @ self.F(b, -1) + (V @ w_end if self.d else 0)
                w = self._w_from(b, list(Ls @ z_left) if len(Ls) else [], side=1)
        self.t, self.Z = t_eval, Z
        self._dense = dense
        return self.values(Z)

    # ---- piecewise-linear elements: modes and events --------------------------------------------------
    def mode_ref(self, modes: dict):
        """The system of one set of modes (built once): its variables in the base order (to_base)."""
        key = tuple(sorted(modes.items()))
        r = self._mode_cache.get(key)
        if r is None:
            r = Ref(self.net, modes=dict(modes), base=self)
            if set(r.sys.vars) != set(self.sys.vars):
                raise RefError("the modes of a piecewise-linear element change the variables (a reference bug)")
            r.to_base = np.array([self.sys.idx[v] for v in r.sys.vars])
            r.init_selection()
            r.guard_rows = [(g, r._functional(g["f"]), g["c"]) for g in r.sys.guards]
            self._mode_cache[key] = r
        return r

    @staticmethod
    def _gval(r, z_m, g):
        _, L, c0 = g
        return float(L @ z_m + c0)

    def _consistent(self, modes, t, z_base, given=None, side=1):
        """(mode ref, modes, w) at t whose guards all hold: starts from modes, switches the elements whose guard
        is violated (an event iteration, as at initialisation). Storage values: the given initial values, or those
        of z_base (the state just before)."""
        modes = dict(modes)
        seen = set()
        for _ in range(4 * len(modes) + 8):
            r = self.mode_ref(modes)
            vals = []
            for cd in r.selected:
                key = (cd["elem"], cd["name"])
                vals.append(given[key] if given is not None and key in given else
                            float(self._functional(cd["f"]) @ z_base))
            w = r._w_from(t, vals, side=side)
            z_m = r.P @ r.F(t, side) + (r.V @ w if r.d else 0)
            scale = max(1.0, float(np.max(np.abs(z_m), initial=0)))
            bad = None
            for g in r.guard_rows:
                v = self._gval(r, z_m, g)
                if (g[0]["dir"] > 0 and v > 1e-9 * scale) or (g[0]["dir"] < 0 and v < -1e-9 * scale):
                    bad = g[0]
                    break
            if bad is None:
                return r, modes, w
            modes[bad["elem"]] = bad["to"]
            key = tuple(sorted(modes.items()))
            if key in seen:
                raise RefError(f"no consistent mode for the piecewise-linear elements at t = {t:g}")
            seen.add(key)
        raise RefError(f"no consistent mode for the piecewise-linear elements at t = {t:g}")

    def _simulate_events(self, t_eval, t0=None):
        """Integrate in one set of modes, stop where a guard crosses zero (solve_ivp events), switch that element,
        carry the storage values and go on; source breakpoints as in _simulate."""
        import os

        from scipy.integrate import solve_ivp
        from scipy.optimize import brentq
        if not hasattr(self, "selected"):
            sel, dep, missing = self.init_selection()
            if missing:
                raise RefError(f"{missing} state(s) without an initial value")
        maxev = int(os.environ.get("OMCT_REF_MAX_EVENTS", "5000"))
        t_eval = np.asarray(t_eval, float)
        t0 = float(t_eval[0]) if t0 is None else float(t0)
        t1 = float(t_eval[-1])
        given = {(cd["elem"], cd["name"]): cd["value"] for cd in self.selected}
        wb = self._w_from(t0, [cd["value"] for cd in self.selected])
        zb = self.P @ self.F(t0, 1) + (self.V @ wb if self.d else 0)
        r, modes, w = self._consistent(self.sys.pwl, t0, zb, given)
        Z = np.zeros((len(t_eval), self.n))
        edges = [t0] + self.breaks(t0, t1) + [t1]
        dense, nev = [], 0
        wscale = self._wscale(wb, t0, t1, edges)
        self.switches = []
        which, used = np.zeros(len(t_eval), dtype=np.int64), {}

        def z_at(r, t, w_, side=1):
            z = r.P @ r.F(t, side) + (r.V @ w_ if r.d else 0)
            zb_ = np.zeros(self.n)
            zb_[r.to_base] = z
            return zb_

        for a, b in zip(edges[:-1], edges[1:]):
            if b <= a:
                continue
            last = b == t1
            t = a
            while True:
                idx = np.nonzero((t_eval >= t) & ((t_eval < b) | (last & (t_eval <= b))))[0]
                hit = None
                if r.d:
                    evs = []
                    for g in r.guard_rows:
                        def ev(tt, w_, g=g, r=r, b=b):
                            sd = -1 if tt >= b else 1
                            return self._gval(r, r.P @ r.F(tt, sd) + r.V @ w_, g)
                        ev.terminal, ev.direction = True, g[0]["dir"]
                        evs.append(ev)
                    with warnings.catch_warnings():         # scipy's Radau divides by a zero error norm
                        warnings.simplefilter("ignore", RuntimeWarning)
                        sol = solve_ivp(r._rhs(b), (t, b), w, method="Radau", rtol=1e-10, atol=1e-12 * wscale,
                                        jac=r.Ad, dense_output=True, events=evs or None)
                    if not sol.success:
                        raise RefError(f"integration failed: {sol.message}")
                    te = b
                    for k, tk in enumerate(sol.t_events or []):
                        if len(tk) and tk[0] > t + 1e-14 * max(1.0, abs(t)) and tk[0] < te:
                            te, hit = float(tk[0]), r.guard_rows[k][0]
                    w_end = sol.sol(te)
                    dense.append((t, te, sol, None, r))
                else:
                    # no states: the guards follow the sources; find a crossing on a fine grid, then brentq
                    te, w_end = b, w
                    grid = np.linspace(t, b, 401)
                    for g in r.guard_rows:
                        vals = [self._gval(r, r.P @ r.F(x, -1 if x >= b else 1), g) for x in grid]
                        for j in range(1, len(grid)):
                            lo, hi = vals[j - 1], vals[j]
                            if (g[0]["dir"] > 0 and lo < 0 <= hi) or (g[0]["dir"] < 0 and lo > 0 >= hi):
                                tz = brentq(lambda x, g=g: self._gval(r, r.P @ r.F(x, -1 if x >= b else 1), g),
                                            grid[j - 1], grid[j], xtol=1e-15, rtol=4 * EPS)
                                if t + 1e-14 * max(1.0, abs(t)) < tz < te:
                                    te, hit = float(tz), g[0]
                                break
                    dense.append((t, te, None, w, r))
                stop = te if hit is not None else b
                for i in idx:
                    ti = t_eval[i]
                    if ti < stop or (hit is None and last and ti == b):
                        w_i = sol.sol(ti) if r.d else w
                        Z[i] = z_at(r, ti, w_i, -1 if (ti >= b and hit is None) else 1)
                        which[i] = id(r)
                        used[id(r)] = r
                if hit is None:
                    break
                nev += 1
                if nev > maxev:
                    raise RefError(f"more than {maxev} switchings (chattering?) before t = {te:g}")
                z_left = z_at(r, te, w_end, -1 if te >= b else 1)
                modes[hit["elem"]] = hit["to"]
                r, modes, w = self._consistent(modes, te, z_left)
                self.switches.append((te, hit["elem"], hit["to"]))
                t = te
            if not last:
                z_left = z_at(r, b, w_end if r.d else w, -1)
                r, modes, w = self._consistent(modes, b, z_left, side=1)
        self.t, self.Z = t_eval, Z
        self._dense = dense
        out = self.values(Z)
        for rid, rm in used.items():          # products (losses) as the active mode defines them
            m = which == rid
            if rm is self or not m.any():
                continue
            Zm = Z[m][:, rm.to_base]
            for path, pr in rm.sys.products.items():
                terms = pr if isinstance(pr, list) else [(1.0, pr[0], pr[1])]
                out[path][m] = sum(cf * (Zm @ rm._functional(f1)) * (Zm @ rm._functional(f2)) for cf, f1, f2 in terms)
        return out

    def values(self, Z):
        out = {}
        for path, f in self.sys.paths.items():
            out[path] = Z @ self._functional(f)
        for path, pr in self.sys.products.items():
            terms = pr if isinstance(pr, list) else [(1.0, pr[0], pr[1])]
            out[path] = sum(cf * (Z @ self._functional(f1)) * (Z @ self._functional(f2)) for cf, f1, f2 in terms)
        for path, spec in self.sys.funcs.items():
            if spec[0] == "rms":
                fs = spec[1]
                out[path] = np.sqrt(sum((Z @ self._functional(f)) ** 2 for f in fs) / len(fs))
            elif spec[0] == "call":
                _, fn, deps = spec
                out[path] = np.asarray(fn(*[Z[:, self.sys.idx[d]] for d in deps]), float) * np.ones(Z.shape[0])
        return out

    def eigenvalues(self):
        """Eigenvalues of the reduced (consistent) dynamics: d values."""
        if self.events or getattr(self, "nonlinear", False):
            raise RefError("piecewise-linear or nonlinear elements: the reference does not linearise them")
        if self.d == 0:
            return np.zeros(0, complex)
        return np.linalg.eigvals(self.Ad)

    def pencil_eigenvalues(self):
        """Finite generalised eigenvalues of A + s B (a cross-check of eigenvalues())."""
        from scipy.linalg import eig
        w = eig(self.A0, -self.B0, right=False)
        return w[np.isfinite(w)]

    def steady(self, t):
        """Steady state with the sources frozen at their values at t (derivatives 0): {variable: value}, or
        raises RefError when it is not unique (free integrators, rigid-body motion). Cascades: the losses of the
        steady state without them, then the steady state with that heat. Piecewise-linear elements: the modes
        whose guards hold at the steady state."""
        if getattr(self, "nonlinear", False):
            raise RefError("nonlinear machines: no steady state in the reference (AC quantities oscillate)")
        casc = self.sys.cascades
        if not casc:
            z = self._steady_modes(t)
        else:
            try:
                self._casc_frozen = {f.key: 0.0 for f in casc}
                z1 = self._steady_modes(t)
                self._casc_frozen = {f.key: float(sum(cf * (self._functional(f1) @ z1) * (self._functional(f2) @ z1)
                                                      for cf, f1, f2 in f.terms)) for f in casc}
                z = self._steady_modes(t)
            finally:
                self._casc_frozen = None
        return {k: float(v[0]) for k, v in self.values(z[None, :]).items()}

    def _steady_modes(self, t):
        if not self.events:
            return self._steady_z(t)
        modes, seen = dict(self.sys.pwl), set()
        for _ in range(4 * len(modes) + 8):
            r = self.mode_ref(modes)
            zm = r._steady_z(t)
            scale = max(1.0, float(np.max(np.abs(zm), initial=0)))
            bad = next((g[0] for g in r.guard_rows if (g[0]["dir"] > 0 and self._gval(r, zm, g) > 1e-9 * scale) or
                        (g[0]["dir"] < 0 and self._gval(r, zm, g) < -1e-9 * scale)), None)
            if bad is None:
                z = np.zeros(self.n)
                z[r.to_base] = zm
                return z
            modes[bad["elem"]] = bad["to"]
            key = tuple(sorted(modes.items()))
            if key in seen:
                break
            seen.add(key)
        raise RefError("no steady state with consistent modes of the piecewise-linear elements")

    def _steady_z(self, t):
        K = self.MAXORD + 1
        F0 = self.F(t, 1)
        for j in range(len(self.sys.fns)):
            F0[j * K + 1:(j + 1) * K] = 0.0
        zp = self.P @ F0
        if self.d:
            if np.linalg.matrix_rank(self.Ad, tol=1e-10 * max(1.0, np.max(np.abs(self.Ad)))) < self.d:
                raise RefError("no unique steady state (a free integrator or rigid-body motion)")
            w = -np.linalg.solve(self.Ad, self.Vp @ (self.Az @ zp + self.Gz @ F0))
            z = zp + self.V @ w
        else:
            z = zp
        return z

    def freq_response(self, inp: str, out_functional: dict, freqs):
        """Complex gain from the free input 'inp' to a linear functional {Modelica path: coef} at freqs [Hz]."""
        if self.events or getattr(self, "nonlinear", False):
            raise RefError("piecewise-linear or nonlinear elements: the reference does not linearise them")
        K = self.MAXORD + 1
        j = self.sys.fn_idx[id(self.input_fns[inp])]
        g = self.G0[:, j * K]
        L = np.zeros(self.n)
        for path, cf in out_functional.items():
            L += cf * self._functional(self.sys.paths[path])
        out = []
        for f in np.atleast_1d(freqs):
            M = self.A0 + 2j * math.pi * f * self.B0
            Zc = np.linalg.solve(M, -g)
            out.append(L @ Zc)
        return np.array(out)

    def functional(self, terms):
        """Linear functional for an output given as [(coef, Modelica path)]; None if a term is a product."""
        f = {}
        for cf, path in terms:
            if path not in self.sys.paths:
                return None
            for x, c in self.sys.paths[path].items():
                f[x] = f.get(x, 0.0) + cf * c
        return f
