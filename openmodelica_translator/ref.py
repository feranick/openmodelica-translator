"""ref.py — an independent implementation of the study's equations, without OpenModelica.
Version: 2026.10.21.4

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
Nonlinear kinds (diode, limiters, LimPID, radiation, product, ...) get a linear stand-in with the same structure:
enough for init_selection and the singularity check, never for numbers (covered = False).
"""
from __future__ import annotations

import math

import numpy as np

from . import catalog as C

EPS = 2.220446049250313e-16


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
class System:
    def __init__(self):
        self.vars: list[str] = []
        self.idx: dict[str, int] = {}
        self.rows: list[tuple[dict, dict, list]] = []   # (A part, B part, [(coef, fn)])
        self.fns: list[Fn] = []
        self.fn_idx: dict[int, int] = {}
        self.paths: dict[str, dict] = {}                # Modelica path -> linear functional {var: coef}
        self.products: dict[str, tuple[dict, dict]] = {}
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

    def eq(self, a: dict, b: dict | None = None, src: list | None = None):
        for x in list(a) + list(b or {}):
            self.v(x)
        self.rows.append((dict(a), dict(b or {}), [(c, self.fn(f)) for c, f in (src or [])]))


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

    def var(self, name):
        return self.s.v(f"{self.e}.{name}")

    def eq(self, a, b=None, src=None):
        self.s.eq(a, b, src)

    def path(self, name, functional):
        self.s.paths[f"{self.e}.{name}"] = functional

    def init(self, name, functional, value, priority, given, forced=False):
        self.s.inits.append({"elem": self.e, "name": name, "f": functional, "value": float(value),
                             "priority": priority, "given": given, "forced": forced})

    def approx(self, why):
        self.s.exact = False
        self.s.not_exact.append(f"{self.d['comp']} ({self.d['kind']}): {why}")


def _ports_paths(c: _Ctx):
    """Register Modelica paths of the element's port variables."""
    for p, t in c.k["ports"].items():
        mp = c.net.mpath(f"{c.e}.{p}")
        if t in ("in", "out"):
            c.s.paths[mp] = {c.sig(p): 1.0}
        else:
            pt = C.PORT_TYPES[t]
            c.s.paths[f"{mp}.{pt['potential']}"] = {c.pot(p): 1.0}
            c.s.paths[f"{mp}.{pt['flow']}"] = {c.flow(p): 1.0}


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
    # ---- electrical
    if kn == "resistor":
        v, i = _one_port(c)
        c.eq({v: 1, i: -p["R"]})
        c.s.products[f"{c.e}.LossPower"] = ({v: 1.0}, {i: 1.0})
    elif kn == "ideal_diode":
        v, i = _one_port(c)
        c.eq({v: 1, i: -1.0})
        c.approx("piecewise linear diode")
        c.s.products[f"{c.e}.LossPower"] = ({v: 1.0}, {i: 1.0})
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
        c.eq({tau: 1, w: -p["tau_nominal"] / p["w_nominal"]})
        c.eq({c.flow("flange"): 1, tau: 1})
        c.path("tau", {tau: 1.0})
        c.path("w", {w: 1.0})
        if kn == "quadratic_speed_load" or p["TorqueDirection"]:
            c.approx("speed-dependent torque (quadratic, or the same sign both ways)")
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
        c.eq({c.sig("y"): 1, c.sig("u"): -1})
        c.approx(kn)
    elif kn == "integrator":
        y = c.sig("y")
        c.eq({c.sig("u"): p["k"]}, {y: -1})
        c.init("y", {y: 1.0}, p["y_start"], 0, True, forced=True)
    elif kn in ("first_order", "slew_rate_limiter"):
        y = c.sig("y")
        if kn == "first_order":
            c.eq({c.sig("u"): p["k"], y: -1}, {y: -p["T"]})
        else:
            c.eq({c.sig("u"): 1, y: -1}, {y: -p["Td"]})
            c.approx("slew rate limits")
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
        ct = p["controllerType"]
        c.approx("output limits and anti-windup")
        _pid(c, p["k"], p["Ti"], p["Td"], p["Nd"], c.sig("u_s"), c.sig("u_m"), "I" in ct, "D" in ct,
             wp=p["wp"], wd=p["wd"])
    elif kn == "transfer_function":
        _tf(c, p["b"], p["a"])
    else:
        raise KeyError(f"no reference equations for {kn}")


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


class RefError(ValueError):
    pass


class Ref:
    """The study's equations, reduced; built from a network.Net without errors."""

    MAXORD = 4

    def __init__(self, net, free_inputs: dict | None = None):
        self.net = net
        s = System()
        self.sys = s
        for e in sorted(net.elems):
            _stamp(_Ctx(s, net, e))
        # node balances (flows into the components at a node sum to zero)
        nodes = {}
        for ep, nid in net.node_of.items():
            nodes.setdefault(nid, []).append(ep)
        for nid in sorted(nodes):
            s.eq({f"{ep}#flow": 1.0 for ep in sorted(nodes[nid])})
        # free (linearisation) inputs
        self.input_fns = {}
        for name, ep in sorted((net.free_inputs or {}).items()):
            f = Input(name)
            self.input_fns[name] = f
            s.eq({f"s{net.signal_of[ep]}": 1.0}, None, [(-1.0, f)])
        self.exact = s.exact
        self.not_exact = s.not_exact
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
        self.A0, self.B0, self.G0 = A.copy(), B.copy(), G.copy()
        self._reduce(A, B, G)

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
    def simulate(self, t_eval, t0=None):
        """{variable: array} on t_eval (sorted). Storage values carried across each source breakpoint."""
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
        Vp, Az, Gz, V, P = self.Vp, self.Az, self.Gz, self.V, self.P
        Ad = self.Ad
        # one absolute scale for the dynamic coordinates: initial values, and how far the sources push them
        wscale = float(np.max(np.abs(w), initial=0))
        if self.d:
            span = t1 - t0
            for tt in np.linspace(t0, t1, 7).tolist() + edges:
                F0 = self.F(tt, 1)
                drive = Vp @ (Az @ (P @ F0) + Gz @ F0 - P @ self.F(tt, 1, shift=1))
                wscale = max(wscale, float(np.max(np.abs(drive), initial=0)) * span)
                if np.linalg.cond(Ad) < 1e10:
                    wscale = max(wscale, float(np.max(np.abs(np.linalg.solve(Ad, drive)))))
        wscale = wscale if wscale > 0 else 1.0
        for a, b in zip(edges[:-1], edges[1:]):
            if b <= a:
                continue

            def rhs(t, w_, b=b):
                side = -1 if t >= b else 1        # the solver evaluates at b: use the value before the breakpoint
                F0 = self.F(t, side)
                return Ad @ w_ + Vp @ (Az @ (P @ F0) + Gz @ F0 - P @ self.F(t, side, shift=1))

            last = b == t1
            idx = np.nonzero((t_eval >= a) & ((t_eval < b) | (last & (t_eval <= b))))[0]
            te = t_eval[idx]
            if self.d:
                sol = solve_ivp(rhs, (a, b), w, method="Radau", t_eval=te if te.size else None, rtol=1e-10,
                                atol=1e-12 * wscale, jac=Ad, dense_output=True)
                if not sol.success:
                    raise RefError(f"integration failed: {sol.message}")
                w_end = sol.sol(b)
                for j, i in enumerate(idx):
                    Z[i] = P @ self.F(t_eval[i], 1) + V @ sol.y[:, j]
            else:
                w_end = w
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
        return self.values(Z)

    def values(self, Z):
        out = {}
        for path, f in self.sys.paths.items():
            out[path] = Z @ self._functional(f)
        for path, (f1, f2) in self.sys.products.items():
            out[path] = (Z @ self._functional(f1)) * (Z @ self._functional(f2))
        return out

    def eigenvalues(self):
        """Eigenvalues of the reduced (consistent) dynamics: d values."""
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
        raises RefError when it is not unique (free integrators, rigid-body motion)."""
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
        return {k: float(v[0]) for k, v in self.values(z[None, :]).items()}

    def freq_response(self, inp: str, out_functional: dict, freqs):
        """Complex gain from the free input 'inp' to a linear functional {Modelica path: coef} at freqs [Hz]."""
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
