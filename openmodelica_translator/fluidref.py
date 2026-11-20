"""fluidref.py — the reference implementation for Modelica.Fluid liquid networks (V5 FL4).
Version: 2026.11.19.12

An independent solver of the same equations MSL 4.0.0 writes for constant-density liquids, ported from the MSL
sources (tag v4.0.0) rather than reused: what OpenModelica must reproduce. Covered (covered(net)):

  media       water (ConstantPropertyLiquidWater) and custom liquids {rho, cp, eta, lambda}
  kinds       fl_boundary, fl_mass_flow, fl_pipe (friction detailed or nominal_laminar),
              fl_resistance (a > 0), fl_orifice, fl_valve (no check valve), fl_valve_linear, fl_pump (constant speed,
              no check valve, no volume), fl_tank (no port losses; system dynamics fixed_initial), fl_pressure,
              fl_temperature, fl_mass_flow_sensor, fl_volume_flow_sensor, fl_relative_pressure, fl_system
  not         anything outside Modelica.Fluid, dynamic pipes and volumes, gases, inputs that change the flows (p_in,
              m_flow_in, opening, w_in; T_in and heat ports are fine), temperatures

Hydraulics are algebraic (momentum steady, MSL's default): at each instant, node pressures and component flows from
a Newton solve of the component relations and the node mass balances; boundaries and tank ports fix pressures
(p_ambient + rho g level: OpenTank without portsData); the tank levels are the states (A rho dlevel/dt = inflow,
level_start fixed), integrated with Radau (rtol 1e-10). Temperatures are not computed (constant density: they do
not change the flows); outputs that need them are left out of the comparison.

MSL relations ported (Modelica.Fluid 4.0.0, tag v4.0.0; planning.md V5 FL4):
  pipe       WallFriction.Detailed.massFlowRate_dp_staticHead: Detailed.Internal.m_flow_of_dp_fric outside dp_small
             of the static head (Hagen-Poiseuille below Re1 = min((745 e^(...))^0.97, 4000), Colebrook-White above,
             a log-log cubic Hermite up to Re2 = 4000), regFun3 within (not LaminarAndQuadraticTurbulent's Internal);
             NominalLaminarFlow: dp = rho g dh + dp_nominal/m_flow_nominal m
  orifice    m = regRoot2(dp, dp_small, rho/k, rho/k), k = 8 zeta / (pi^2 D^4)
  resistance m = rho sign(dp)(-b/2a + sqrt((b/2a)^2 + |dp|/a)) (b > 0); rho SmoothPower(dp/a, dp_min/a, 0.5) (b = 0)
  valve      m = rc(opening) Av regRoot2(dp, dp_small, rho, rho) (opening_actual = opening without the filter:
             leakageOpening only feeds the filter); Av = 27.7e-6 Kv, 24e-6 Cv, Av, or the op point
             m_nom / (rc(1) sqrt(rho) regRoot(dp_nom, dp_small)); rc linear, quadratic, equal percentage
  linear     m = opening m_nom / dp_nom dp
  pump       p_b - p_a = rho g (N/N0)^2 H(V N0 / N); H linearFlow, quadraticFlow, polynomialFlow (linear outside the
             points); W = dp V / eta
"""
from __future__ import annotations

import math

import numpy as np

from . import catalog as C

KV2AV, CV2AV, RE_TURBULENT = 27.7e-6, 24.0e-6, 4000.0
EPS = 2.220446049250313e-16
COVERED = {"fl_system", "fl_boundary", "fl_mass_flow", "fl_pipe", "fl_resistance", "fl_orifice", "fl_valve",
           "fl_valve_linear", "fl_pump", "fl_tank", "fl_pressure", "fl_temperature", "fl_mass_flow_sensor",
           "fl_volume_flow_sensor", "fl_relative_pressure"}
TWO_PORT = {"fl_pipe", "fl_resistance", "fl_orifice", "fl_valve", "fl_valve_linear", "fl_pump", "fl_temperature",
            "fl_mass_flow_sensor", "fl_volume_flow_sensor"}
ZERO_DP = {"fl_temperature", "fl_mass_flow_sensor", "fl_volume_flow_sensor"}


class FluidRefError(ValueError):
    pass


# ---- MSL utilities (Modelica.Fluid.Utilities, Dissipation) --------------------------------------------------------
def reg_root(x, delta=0.01):
    return x / (x * x + delta * delta) ** 0.25


def _poly3_d0(x, x1, y1, y1d, y0d):
    a1 = x1 * y0d
    a2 = 3 * y1 - x1 * y1d - 2 * a1
    a3 = y1 - a2 - a1
    xx = x / x1
    return xx * (a1 + xx * (a2 + xx * a3))


def _reg_root2_utility(x, x1, k1, k2):
    sk1, sk2 = (math.sqrt(k1) if k1 > 0 else 0.0), (math.sqrt(k2) if k2 > 0 else 0.0)
    if k2 > 0:
        x2 = -x1 * (k2 / k1)
    elif k1 > 0:
        x2 = -x1
    else:
        return 0.0
    if x <= x2:
        return -sk2 * math.sqrt(abs(x))
    y1 = sk1 * math.sqrt(x1)
    y2 = -sk2 * math.sqrt(abs(x2))
    y1d = sk1 / math.sqrt(x1) / 2
    y2d = sk2 / math.sqrt(abs(x2)) / 2
    w = x2 / x1
    y0d = ((3 * y2 - x2 * y2d) / w - (3 * y1 - x1 * y1d) * w) / (2 * x1 * (1 - w))
    w1 = sk1 * math.sqrt(8.75 / x1)
    w2 = sk2 * math.sqrt(8.75 / abs(x2))
    y0d = min(y0d, 0.9 * min(w1, w2))
    if x >= 0:
        return y1 * _poly3_d0(x / x1, 1, 1, y1d * x1 / y1, y0d * x1 / y1)
    return y1 * _poly3_d0(x / x1, x2 / x1, y2 / y1, y2d * x1 / y1, y0d * x1 / y1)


def reg_root2(x, x_small, k1, k2):
    """Utilities.regRoot2 (use_yd0 = false)."""
    if x >= x_small:
        return math.sqrt(k1) * math.sqrt(x)
    if x <= -x_small:
        return -math.sqrt(k2) * math.sqrt(abs(x))
    if k1 >= k2:
        return _reg_root2_utility(x, x_small, k1, k2)
    return -_reg_root2_utility(-x, x_small, k2, k1)


def smooth_power(x, deltax, pw):
    """Dissipation.Utilities.Functions.General.SmoothPower."""
    ad = abs(deltax)
    c3 = (pw - 1) / 2 * ad ** (pw - 3)
    c1 = (3 - pw) / 2 * ad ** (pw - 1)
    if x >= ad:
        return x ** pw
    if x <= -ad:
        return -((-x) ** pw)
    return (c1 + c3 * x * x) * x


def cubic_hermite_d(x, x1, x2, y1, y2, y1d, y2d):
    """Utilities.cubicHermite_withDerivative: (y, dy/dx)."""
    h = x2 - x1
    if not abs(h) > 0:
        return (y1 + y2) / 2, math.copysign(math.inf, y2 - y1) if y2 != y1 else 0.0
    t = (x - x1) / h
    a3, a2 = t ** 3, t ** 2
    h00, h10, h01, h11 = 2 * a3 - 3 * a2 + 1, a3 - 2 * a2 + t, -2 * a3 + 3 * a2, a3 - a2
    h00d, h10d, h01d, h11d = 6 * (a2 - t), 3 * a2 - 4 * t + 1, 6 * (t - a2), 3 * a2 - 2 * t
    return (y1 * h00 + h * y1d * h10 + y2 * h01 + h * y2d * h11,
            y1 * h00d / h + y1d * h10d + y2 * h01d / h + y2d * h11d)


def reg_fun3(x, x0, x1, y0, y1, y0d, y1d):
    """Utilities.regFun3: (y, c)."""
    sm = 100 * EPS
    h0 = x1 - x0
    d0 = (y1 - y0) / h0
    if abs(d0) <= 0:
        return y0 + d0 * (x - x0), 0.0
    if abs(y1d + y0d - 2 * d0) < sm:
        y = y0 + (x - x0) * (y0d + (x - x0) / h0 * ((-2 * y0d - y1d + 3 * d0) + (x - x0) * (y0d + y1d - 2 * d0) / h0))
        a = (x0 + x1) / 2
        c = 3 * (y0d + y1d - 2 * d0) * (a - x0) ** 2 / h0 ** 2 + 2 * (-2 * y0d - y1d + 3 * d0) * (a - x0) / h0 + y0d
        return y, c
    xstar = 1 / 3 * (-3 * x0 * y0d - 3 * x0 * y1d + 6 * x0 * d0 - 2 * h0 * y0d - h0 * y1d + 3 * h0 * d0) / (
        -y0d - y1d + 2 * d0)
    mu = xstar - x0
    eta = x1 - xstar
    omega = 3 * (y0d + y1d - 2 * d0) * (xstar - x0) ** 2 / h0 ** 2 + 2 * (-2 * y0d - y1d + 3 * d0) * (xstar - x0) / h0 + y0d
    aux01 = 0.25 * math.copysign(1.0, d0) * min(abs(omega), abs(d0))
    single = False
    if abs(y0d - y1d) <= sm:
        aux02 = y0d
        if y1 > y0 + y0d * (x1 - x0):
            single = True
    elif abs(y1d + y0d - 2 * d0) < sm:
        aux02 = (6 * d0 * (y1d + y0d - 1.5 * d0) - y1d * y0d - y1d ** 2 - y0d ** 2) * (
            1 if (y1d + y0d - 2 * d0) >= 0 else -1) * math.inf
    else:
        aux02 = (6 * d0 * (y1d + y0d - 1.5 * d0) - y1d * y0d - y1d ** 2 - y0d ** 2) / (3 * (y1d + y0d - 2 * d0))
    if (((mu > 0) and (eta < h0) and (d0 * omega <= 0)) or (abs(aux01) < abs(aux02) and aux02 * d0 >= 0) or
            (abs(aux01) < abs(0.1 * d0))) and not single:
        c = aux01
        if abs(c) < abs(aux02) and aux02 * d0 >= 0:
            c = aux02
        if abs(c) < abs(0.1 * d0):
            c = 0.1 * d0
        theta0 = (y0d * mu + y1d * eta) / h0
        if abs(theta0 - c) < 1e-6:
            c = (1 - 1e-6) * theta0
        rho = 3 * (d0 - c) / (theta0 - c)
        mu_t, eta_t = rho * mu, rho * eta
        xi1, xi2 = x0 + mu_t, x1 - eta_t
        a1 = (y0d - c) / max(mu_t ** 2, sm)
        a2 = (y1d - c) / max(eta_t ** 2, sm)
        const12 = y0 - a1 / 3 * (x0 - xi1) ** 3 - c * x0
        const3 = y1 - a2 / 3 * (x1 - xi2) ** 3 - c * x1
        if x < xi1:
            return a1 / 3 * (x - xi1) ** 3 + c * x + const12, c
        if x < xi2:
            return c * x + const12, c
        return a2 / 3 * (x - xi2) ** 3 + c * x + const3, c
    y = y0 + (x - x0) * (y0d + (x - x0) / h0 * ((-2 * y0d - y1d + 3 * d0) + (x - x0) * (y0d + y1d - 2 * d0) / h0))
    a = (x0 + x1) / 2
    c = 3 * (y0d + y1d - 2 * d0) * (a - x0) ** 2 / h0 ** 2 + 2 * (-2 * y0d - y1d + 3 * d0) * (a - x0) / h0 + y0d
    return y, c


# ---- WallFriction.Detailed (Modelica.Fluid.Pipes.BaseClasses.WallFriction.Detailed, MSL 4.0.0) ---------------------
def m_flow_of_dp_fric(dp, rho, mu, L, D, A, Re1, Re2, Delta):
    """Detailed.Internal.m_flow_of_dp_fric: (m_flow, dm_flow/ddp). Hagen-Poiseuille below Re1, Colebrook-White
    above, a cubic Hermite spline in log-log space (lambda2 = lambda Re^2, Re) between Re1 and Re2."""
    lam2 = abs(dp) * 2 * D ** 3 * rho / (L * mu * mu)
    aux1 = (2 * D ** 3 * rho) / (L * mu ** 2)
    Re = lam2 / 64
    dRe = aux1 / 64
    if Re > Re1:
        Re = -2 * math.sqrt(lam2) * math.log10(2.51 / math.sqrt(lam2) + 0.27 * Delta)
        a2 = math.sqrt(aux1 * abs(dp))
        dRe = 1 / math.log(10) * (-2 * math.log(2.51 / a2 + 0.27 * Delta) * aux1 / (2 * a2) +
                                  2 * 2.51 / (2 * abs(dp) * (2.51 / a2 + 0.27 * Delta)))
        if Re < Re2:                                    # interpolateInRegion2_withDerivative
            x1, y1, y1d = math.log10(64 * Re1), math.log10(Re1), 1.0
            aux3 = math.log10(Delta / 3.7 + 5.74 / Re2 ** 0.9)
            L2 = 0.25 * (Re2 / aux3) ** 2
            aux4 = 2.51 / math.sqrt(L2) + 0.27 * Delta
            aux5 = -2 * math.sqrt(L2) * math.log10(aux4)
            x2, y2 = math.log10(L2), math.log10(aux5)
            y2d = 0.5 + (2.51 / math.log(10)) / (aux5 * aux4)
            y, dy = cubic_hermite_d(math.log10(lam2), x1, x2, y1, y2, y1d, y2d)
            Re = 10 ** y
            dRe = Re / abs(dp) * dy
    return A / D * mu * (Re if dp >= 0 else -Re), A / D * mu * dRe


def pipe_detailed(dp, rho, mu, L, D, rough, dp_small, g_h):
    """Detailed.massFlowRate_dp_staticHead with rho_a = rho_b, mu_a = mu_b (constant density), Re_turbulent 4000,
    crossArea pi D^2/4 (StaticPipe: the mean of its two equal areas)."""
    Delta = rough / D
    Re1 = min((745 * math.exp(1 if Delta <= 0.0065 else 0.0065 / Delta)) ** 0.97, RE_TURBULENT)
    Re2 = RE_TURBULENT
    dg = g_h * rho
    dp_a, dp_b = dg + dp_small, dg - dp_small
    args = (rho, mu, L, D, math.pi * D * D / 4, Re1, Re2, Delta)
    if dp >= dp_a or dp <= dp_b:
        return m_flow_of_dp_fric(dp - dg, *args)[0]
    ma, da = m_flow_of_dp_fric(dp_a - dg, *args)
    mb, db = m_flow_of_dp_fric(dp_b - dg, *args)
    _, dz = reg_fun3(dg, dp_b, dp_a, mb, ma, db, da)
    if dp > dg:
        return reg_fun3(dp, dg, dp_a, 0.0, ma, dz, da)[0]
    return reg_fun3(dp, dp_b, dg, mb, 0.0, db, dz)[0]


# ---- pumps and valves -----------------------------------------------------------------------------------------------
def pump_curve(V, Vn, Hn):
    """PumpCharacteristics.linearFlow / quadraticFlow / polynomialFlow (linear outside the points, n > 2)."""
    n = len(Vn)
    c = np.linalg.solve(np.vander(np.asarray(Vn, float), n, increasing=True), np.asarray(Hn, float))
    if n == 2:
        return c[0] + V * c[1]
    vmin, vmax = min(Vn), max(Vn)

    def slope(v):
        return sum((i) * v ** (i - 1) * c[i] for i in range(1, n))
    if V < vmin:
        return max(Hn) + (V - vmin) * slope(vmin)
    if V > vmax:
        return min(Hn) + (V - vmax) * slope(vmax)
    return float(sum(V ** i * c[i] for i in range(n)))


def valve_char(kind, pos, rangeability=20.0, delta=0.01):
    if kind == "linear":
        return pos
    if kind == "quadratic":
        return pos * pos
    return rangeability ** (pos - 1) if pos > delta else pos / delta * rangeability ** (delta - 1)


# ---- coverage ---------------------------------------------------------------------------------------------------------
HYDRAULIC_INPUTS = {"p_in", "m_flow_in", "opening", "w_in"}     # inputs that change the flows (T_in, heat do not)


def covered(net) -> tuple[bool, str]:
    """(True, '') when every element is a Modelica.Fluid kind the reference has, on constant-density liquids."""
    if net is None or not net.elems:
        return False, "no network"
    sysp = next((d["params"] for d in net.elems.values() if d["kind"] == "fl_system"), {})
    for e, d in sorted(net.elems.items()):
        k, p, c = d["kind"], d["params"], d["comp"]
        if C.KINDS[k].get("domain") != "mfluid":
            return False, f"{c} ({k}) is not Modelica.Fluid (studies mixing Fluid with other domains: not covered)"
        if k not in COVERED:
            return False, f"{c} ({k}) is not in the Fluid reference (dynamic pipes, volumes, gas valves, shaft pumps)"
        if k == "fl_system":
            continue
        if C.fmedium(p.get("medium") or "water")["class"] != "liquid_const":
            return False, f"{c}: medium {C.fmedium(p['medium'])['name']} (constant-density liquids only)"
        inputs = sorted(HYDRAULIC_INPUTS & set(d.get("enabled") or ()))
        if inputs:
            return False, f"{c}: input {inputs[0]} connected (constant values only)"
        if k == "fl_pipe":
            if p.get("friction") not in ("detailed", "nominal_laminar"):
                return False, f"{c}: friction {p.get('friction')} (detailed and nominal_laminar only)"
        if k == "fl_resistance" and not float(p.get("a") or 0) > 0:
            return False, f"{c}: a = 0"
        if k in ("fl_valve", "fl_pump") and p.get("checkValve"):
            return False, f"{c}: check valve"
        if k == "fl_pump" and float(p.get("V") or 0) > 0:
            return False, f"{c}: a pump with a volume"
        if k == "fl_tank":
            if p.get("port_diameter") is not None:
                return False, f"{c}: port losses (port_diameter)"
            if sysp.get("dynamics", "fixed_initial") != "fixed_initial":
                return False, f"{c}: system dynamics {sysp.get('dynamics')} (tank levels from fixed_initial only)"
    if sysp and not sysp.get("allowFlowReversal", True):
        return False, "allowFlowReversal false"
    return True, ""


def _medium(p):
    """(rho, mu) of a constant-density liquid: ConstantPropertyLiquidWater (MSL 4.0.0: d_const 995.586,
    eta_const 1e-3) or a custom one {rho, cp[, eta, lambda]} (its constants replace those)."""
    m = p.get("medium") or "water"
    if isinstance(m, dict):
        return float(m.get("rho", 995.586)), float(m.get("eta", 1.0e-3))
    return 995.586, 1.0e-3


# ---- the network ----------------------------------------------------------------------------------------------------
class FluidRef:
    """The liquid network of a covered study (covered(net)): simulate(t) -> {Modelica path: values}. Drop-in for
    ref.Ref where the runner and the fit use it (exact, not_exact, events, breaks, simulate, steady)."""

    exact, events, fluid = True, [], True

    def __init__(self, net):
        ok, why = covered(net)
        if not ok:
            raise FluidRefError(why)
        self.net, self.not_exact = net, []
        sysp = next((d["params"] for d in net.elems.values() if d["kind"] == "fl_system"), {})
        self.g = float(sysp.get("g", 9.80665))
        self.p_amb = float(sysp.get("p_ambient", 101325.0))
        self.dp_small = float(sysp.get("dp_small", 1.0))
        self.m_small = float(sysp.get("m_flow_small", 0.01))
        # nodes: the network's, with a mass-flow source's ports joined (they share its medium.p: PartialSource)
        parent = {}

        def find(n):
            while parent.get(n, n) != n:
                n = parent[n]
            return n
        for e, d in sorted(net.elems.items()):
            if d["kind"] == "fl_mass_flow":
                ns = [net.node_of[f"{e}.port[{j}]"] for j in range(1, d.get("multi_n", {}).get("port", 0) + 1)]
                for n in ns[1:]:
                    a, b = find(ns[0]), find(n)
                    if a != b:
                        parent[b] = a
        self.find = lambda ep: find(net.node_of[ep])
        self.nodes_root = {n: find(n) for n in set(net.node_of.values())}
        self.nodes, self.fixed, self.tanks, self.sources, self.flows, self.fixed_ports = {}, {}, [], [], [], []

        def node(ep):
            n = self.find(ep)
            self.nodes.setdefault(n, len(self.nodes))
            return n
        for e, d in sorted(net.elems.items()):
            k, p = d["kind"], d["params"]
            nports = d.get("multi_n", {}).get("port", 0)
            if k == "fl_boundary":
                for j in range(1, nports + 1):
                    self._fix(node(f"{e}.port[{j}]"), ("p", float(p["p"])))
                    self.fixed_ports.append((e, j, net.node_of[f"{e}.port[{j}]"]))
            elif k == "fl_tank":
                rho, _ = _medium(p)
                ti = len(self.tanks)
                ports = [node(f"{e}.port[{j}]") for j in range(1, nports + 1)]
                self.tanks.append({"e": e, "A": float(p["crossArea"]), "rho": rho, "nodes": ports,
                                   "h0": max(float(p["level_start"]), EPS)})
                for j, n_ in enumerate(ports, 1):
                    self._fix(n_, ("tank", ti))
                    self.fixed_ports.append((e, j, net.node_of[f"{e}.port[{j}]"]))
                self.tanks[-1]["ports"] = [net.node_of[f"{e}.port[{j}]"] for j in range(1, nports + 1)]
            elif k == "fl_mass_flow":
                if nports:
                    self.sources.append((node(f"{e}.port[1]"), float(p["m_flow"]), e,
                                         [net.node_of[f"{e}.port[{j}]"] for j in range(1, nports + 1)]))
            elif k in TWO_PORT:
                rho, mu = _medium(p)
                self.flows.append({"e": e, "k": k, "p": p, "rho": rho, "mu": mu,
                                   "a": node(f"{e}.port_a"), "b": node(f"{e}.port_b"),
                                   "a0": net.node_of[f"{e}.port_a"], "b0": net.node_of[f"{e}.port_b"]})
            elif k == "fl_pressure":
                node(f"{e}.port")
            elif k == "fl_relative_pressure":
                node(f"{e}.port_a"), node(f"{e}.port_b")
        touched = {f[x] for f in self.flows for x in ("a", "b")} | {s[0] for s in self.sources}
        self.free = [n for n in self.nodes if n not in self.fixed and n in touched]
        dead = [n for n in self.nodes if n not in self.fixed and n not in touched]
        if dead:
            raise FluidRefError("a connection point with only sensors on it (no pressure)")
        self._x = None

    def _fix(self, n, how):
        if n in self.fixed and self.fixed[n] != how:
            raise FluidRefError("two fixed pressures at one connection point (boundaries or tanks joined directly)")
        self.fixed[n] = how

    def breaks(self, t0, t1, ends=False):
        return []

    # ---- one instant ----------------------------------------------------------------------------------------------
    def _p_fixed(self, n, levels):
        how = self.fixed[n]
        if how[0] == "p":
            return how[1]
        tk = self.tanks[how[1]]
        return max(0.0, levels[how[1]]) * self.g * tk["rho"] + self.p_amb

    def _relation(self, f, m, pa, pb):
        """Residual of one component: kg/s where MSL writes m_flow = f(dp), Pa/1e5 where it writes a pressure."""
        k, p, rho = f["k"], f["p"], f["rho"]
        dp = pa - pb
        if k in ZERO_DP:
            return dp / 1e5
        if k == "fl_pipe":
            gh = self.g * float(p["height_ab"])
            if p["friction"] == "nominal_laminar":
                return (dp - gh * rho - float(p["dp_nominal"]) / float(p["m_flow_nominal"]) * m) / 1e5
            return m - float(p["nParallel"]) * pipe_detailed(dp, rho, f["mu"], float(p["length"]),
                                                             float(p["diameter"]), float(p["roughness"]),
                                                             self.dp_small, gh)
        if k == "fl_orifice":
            D, z = float(p["diameter"]), float(p["zeta"])
            kk = 8 * z / (math.pi ** 2 * D ** 4)              # lossConstant_D_zeta
            return m - reg_root2(dp, self.dp_small, rho / kk, rho / kk)
        if k == "fl_resistance":
            a, b = float(p["a"]), float(p.get("b") or 0.0)
            if b > 0:
                v = (math.copysign(1.0, dp) if dp != 0 else 0.0) * (-b / (2 * a) + math.sqrt((b / (2 * a)) ** 2
                                                                                          + abs(dp) / a))
            else:                    # dp_small of the resistance: dp_volumeFlowRate_DP at m_flow_small, dp_min 1e-10
                vs = self.m_small / rho
                dmin = a * smooth_power(vs, (1e-10 / a) ** 0.5, 2)
                v = smooth_power(dp / a, max(EPS, dmin) / a, 0.5)
            return m - rho * v
        if k == "fl_valve":
            ch, rg = p["characteristic"], float(p.get("rangeability") or 20.0)
            rc = valve_char(ch, float(p["opening"]), rg)   # opening_actual = opening (no filter: no leakage limit)
            if p.get("Kv") is not None:
                Av = float(p["Kv"]) * KV2AV
            elif p.get("Cv") is not None:
                Av = float(p["Cv"]) * CV2AV
            elif p.get("Av") is not None:
                Av = float(p["Av"])
            else:                                          # CvTypes.OpPoint at opening_nominal 1
                Av = float(p["m_flow_nominal"]) / (valve_char(ch, 1.0, rg) * math.sqrt(rho) *
                                                  reg_root(float(p["dp_nominal"]), self.dp_small))
            return m - rc * Av * reg_root2(dp, self.dp_small, rho, rho)
        if k == "fl_valve_linear":
            return m - float(p["opening"]) * float(p["m_flow_nominal"]) / float(p["dp_nominal"]) * dp
        if k == "fl_pump":
            n0 = float(p["N_nominal"])
            n = float(p["N"]) if p.get("N") is not None else n0
            vs = m / rho / float(p["nParallel"])
            pts = p["curve"]
            head = (n / n0) ** 2 * pump_curve(vs * n0 / n, [r[0] for r in pts], [r[1] for r in pts])
            return ((pb - pa) - rho * self.g * head) / 1e5
        raise FluidRefError(f"no relation for {k}")

    def _solve(self, levels):
        from scipy.optimize import root
        nf, fi = len(self.flows), {n: i for i, n in enumerate(self.free)}

        def pressures(x):
            return {n: (x[nf + fi[n]] * 1e5 if n in fi else self._p_fixed(n, levels)) for n in self.nodes}

        def res(x):
            P = pressures(x)
            r = np.empty(nf + len(fi))
            for i, f in enumerate(self.flows):
                r[i] = self._relation(f, x[i], P[f["a"]], P[f["b"]])
            bal = np.zeros(len(fi))
            for i, f in enumerate(self.flows):
                if f["a"] in fi:
                    bal[fi[f["a"]]] -= x[i]
                if f["b"] in fi:
                    bal[fi[f["b"]]] += x[i]
            for n, m, _, _ in self.sources:
                if n in fi:
                    bal[fi[n]] += m
            r[nf:] = bal
            return r
        if self._x is not None:
            x0 = self._x
        else:
            pf = [self._p_fixed(n, levels) for n in self.fixed] or [self.p_amb]
            x0 = np.concatenate([np.full(nf, 1e-3), np.full(len(fi), float(np.mean(pf)) / 1e5)])
        best = None
        for method, opts in (("hybr", {"xtol": 1e-15, "maxfev": 50000}),
                             ("lm", {"xtol": 1e-15, "ftol": 1e-15, "maxiter": 50000})):
            sol = root(res, x0, method=method, options=opts)
            err = float(np.max(np.abs(res(sol.x)))) if sol.x.size else 0.0
            if best is None or err < best[1]:
                best = (sol.x, err)
            if err < 1e-11:
                break
        if best[1] > 1e-8:
            raise FluidRefError(f"the hydraulics did not converge (residual {best[1]:.3g})")
        self._x = best[0]
        return best[0][:nf], pressures(best[0])

    def _q_orig(self, m, sources=True):
        """{network node: the flow the components put into it}: flow elements (-m at port_a, +m at port_b) and,
        with sources, single-port mass-flow sources. A boundary's or tank's port takes it (Sum m_flow = 0)."""
        q = {}
        for i, f in enumerate(self.flows):
            q[f["a0"]] = q.get(f["a0"], 0.0) - m[i]
            q[f["b0"]] = q.get(f["b0"], 0.0) + m[i]
        if sources:
            for _, ms, _, orig in self.sources:
                if len(orig) == 1:
                    q[orig[0]] = q.get(orig[0], 0.0) + ms
        return q

    def _fixed_flows(self, m):
        """{(element, j): ports[j].m_flow} of the boundaries and tanks (into the component; a node's flow shared
        equally when several such ports meet there)."""
        q = self._q_orig(m)
        share = {}
        for _, _, n in self.fixed_ports:
            share[n] = share.get(n, 0) + 1
        return {(e, j): q.get(n, 0.0) / share[n] for e, j, n in self.fixed_ports}

    def _tank_inflows(self, m, P):
        ff = self._fixed_flows(m)
        return np.array([sum(ff[(tk["e"], j)] for j in range(1, len(tk["ports"]) + 1)) for tk in self.tanks])

    # ---- over time --------------------------------------------------------------------------------------------------
    def simulate(self, t_eval, t0=None) -> dict:
        from scipy.integrate import solve_ivp
        t = np.asarray(t_eval, float)
        self._x = None
        if self.tanks:
            h0 = np.array([tk["h0"] for tk in self.tanks])
            rho_a = np.array([tk["rho"] * tk["A"] for tk in self.tanks])

            def rhs(_, h):
                m, P = self._solve(h)
                return self._tank_inflows(m, P) / rho_a
            ts = float(t[0] if t0 is None else t0)
            if t[-1] > ts:
                sol = solve_ivp(rhs, (ts, float(t[-1])), h0, t_eval=t[t >= ts], method="Radau", rtol=1e-10,
                                atol=1e-12 * max(1.0, float(np.max(h0))))
                if not sol.success:
                    raise FluidRefError(f"the tank levels did not integrate: {sol.message}")
                H = np.empty((len(self.tanks), t.size))
                H[:, t >= ts] = sol.y
                H[:, t < ts] = h0[:, None]
            else:
                H = np.repeat(h0[:, None], t.size, axis=1)
            if np.any(H < 0):
                raise FluidRefError("a tank ran empty (below its ports: MSL's inflow-only mode, not in the reference)")
        else:
            H = np.zeros((0, t.size))
        self._x = None
        cols = [self._solve(H[:, j]) for j in range(t.size)]
        return self._paths(t, H, cols)

    def steady(self, t1=None) -> dict:
        """Without tanks the hydraulics are algebraic: the state at any time. With tanks: RefError (no steady
        state is implied; the caller runs to the end instead)."""
        from .ref import RefError
        if self.tanks:
            raise RefError("tank levels: no algebraic steady state")
        v = self.simulate(np.array([0.0]))
        return {k: float(x[0]) for k, x in v.items()}

    def _paths(self, t, H, cols) -> dict:
        out, g, net = {}, self.g, self.net

        def ser(fn):
            return np.array([fn(m, P) for m, P in cols], float)
        for i, f in enumerate(self.flows):
            e, k, p, rho = f["e"], f["k"], f["p"], f["rho"]
            a, b = f["a"], f["b"]
            mm = ser(lambda m, P, i=i: m[i])
            pa, pb = ser(lambda m, P, a=a: P[a]), ser(lambda m, P, b=b: P[b])
            out.update({f"{e}.port_a.m_flow": mm, f"{e}.port_b.m_flow": -mm, f"{e}.port_a.p": pa,
                        f"{e}.port_b.p": pb})
            if k in ("fl_resistance", "fl_orifice", "fl_valve", "fl_valve_linear", "fl_pipe"):
                out[f"{e}.V_flow"] = mm / rho
            if k == "fl_mass_flow_sensor":
                out[f"{e}.m_flow"] = mm
            if k == "fl_volume_flow_sensor":
                out[f"{e}.V_flow"] = mm / rho
            if k == "fl_pump":
                n0 = float(p["N_nominal"])
                n = float(p["N"]) if p.get("N") is not None else n0
                eta = float(p["eta"])
                dpp = pb - pa
                out.update({f"{e}.m_flow": mm, f"{e}.V_flow": mm / rho, f"{e}.dp_pump": dpp,
                            f"{e}.head": dpp / (rho * g), f"{e}.eta": np.full(t.size, eta),
                            f"{e}.W_total": dpp * (mm / rho) / eta, f"{e}.N": np.full(t.size, n * C.W_RPM)})
        for j, tk in enumerate(self.tanks):
            e, A = tk["e"], tk["A"]
            out[f"{e}.level"] = H[j]
            out[f"{e}.V"] = A * H[j]
            out[f"{e}.m"] = tk["rho"] * A * H[j]
        # vessels', boundaries' and sources' ports: ports[j].p, ports[j].m_flow (into the component)
        ffs = [self._fixed_flows(m) for m, _ in cols]
        for e, j, n in self.fixed_ports:
            out[f"{e}.ports[{j}].p"] = ser(lambda m, P, n=n: P[self.nodes_root[n]])
            out[f"{e}.ports[{j}].m_flow"] = np.array([ff[(e, j)] for ff in ffs], float)
        for n, ms, e, orig in self.sources:
            for j, n0 in enumerate(orig, 1):
                out[f"{e}.ports[{j}].p"] = ser(lambda m, P, n=n: P[n])
                out[f"{e}.ports[{j}].m_flow"] = (np.full(t.size, -ms) if len(orig) == 1 else
                                                 ser(lambda m, P, n0=n0: self._q_orig(m, False).get(n0, 0.0)))
        for e, d in net.elems.items():
            if d["kind"] == "fl_pressure":
                n = self.find(f"{e}.port")
                out[f"{e}.p"] = ser(lambda m, P, n=n: P[n])
                out[f"{e}.port.p"] = out[f"{e}.p"]
            if d["kind"] == "fl_relative_pressure":
                na, nb = self.find(f"{e}.port_a"), self.find(f"{e}.port_b")
                out[f"{e}.p_rel"] = ser(lambda m, P, na=na, nb=nb: P[na] - P[nb])
        return out
