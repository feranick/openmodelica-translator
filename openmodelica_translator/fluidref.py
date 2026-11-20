"""fluidref.py — the reference implementation for Modelica.Fluid liquid networks (V5 FL4).
Version: 2026.11.19.19

An independent solver of the same equations MSL 4.0.0 writes for constant-density liquids, ported from the MSL
sources (tag v4.0.0) rather than reused: what OpenModelica must reproduce. Covered (covered(net)):

  media       water (ConstantPropertyLiquidWater) and custom liquids {rho, cp, eta, lambda}
  kinds       fl_boundary, fl_mass_flow, fl_pipe (friction detailed or nominal_laminar),
              fl_resistance (a > 0), fl_orifice, fl_valve (check valve too), fl_valve_linear, fl_pump (constant
              speed, check valve too, a volume too), fl_tank (port losses too; system dynamics fixed_initial),
              fl_pressure, fl_temperature, fl_mass_flow_sensor, fl_volume_flow_sensor, fl_relative_pressure, fl_system
  thermal     temperatures (FL9): mixing at connection points, tanks and pump volumes, a pump's W, and a linear
              thermal network on tank heat ports (fixed_temperature, fixed_heat_flow, thermal_conductor,
              thermal_resistor, heat_capacitor, temperature_sensor); T_in inputs switch temperatures off
  not         anything else outside Modelica.Fluid, dynamic pipes and volumes, gases, inputs that change the flows
              (p_in, m_flow_in, opening, w_in)

Hydraulics are algebraic (momentum steady, MSL's default): at each instant, node pressures and component flows from
a Newton solve of the component relations and the node mass balances; boundaries and tank ports fix pressures
(p_ambient + rho g level: OpenTank without portsData); the tank levels are the states (A rho dlevel/dt = inflow,
level_start fixed), integrated with Radau (rtol 1e-10) together with the energy states (tanks, pump volumes,
capacitors): temperatures do not change the flows (constant density and viscosity), the flows drive them.

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
  check      valve: m = rc Av sqrt(rho) regRoot2(dp, dp_small, 1, 0, use_yd0, 0); pump (NoHomotopy): MSL's s,
             head = (N/N0)^2 H(s/rho N0/N) for s > 0, (N/N0)^2 H(0) - s (1 m per kg/s) else, m = nParallel max(s, 0)
  tank ports with port_diameter (use_portsData, VesselPortsData zeta_out 0.5, zeta_in 1.04): ports[i].p =
             p_static + 0.5/A_p^2 regSquare2(m, m_flow_small, (zeta_in - 1 + A_p^2/A^2)/rho pen,
             (zeta_out + 1 - A_p^2/A^2)/rho/pen), pen = regStep(level - 0.1 D, 1, 1e-3, 0.1 D)
  pump       p_b - p_a = rho g (N/N0)^2 H(V N0 / N); H linearFlow, quadraticFlow, polynomialFlow (linear outside the
             points); W = dp V / eta, or with a power curve (FL5) MSL's quadraticPower:
             W = (N/N0)^3 (rho/rho_nominal) P(V N0 / N), eta = dp V / W
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
# thermal parts the reference solves with the fluid's temperatures (FL9): a linear network on the storages' heat ports
THERMAL = {"fixed_temperature", "fixed_heat_flow", "thermal_conductor", "thermal_resistor", "heat_capacitor",
           "temperature_sensor"}
T0_MEDIUM = 273.15          # PartialSimpleMedium T0 (ConstantPropertyLiquidWater): h = cp (T - T0), u = cv (T - T0)


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


def _reg_root2_utility(x, x1, k1, k2, use_yd0=False, yd0=1.0):
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
    if use_yd0:
        y0d = yd0
    else:
        w = x2 / x1
        y0d = ((3 * y2 - x2 * y2d) / w - (3 * y1 - x1 * y1d) * w) / (2 * x1 * (1 - w))
    w1 = sk1 * math.sqrt(8.75 / x1)
    w2 = sk2 * math.sqrt(8.75 / abs(x2))
    y0d = min(y0d, 0.9 * min(w1, w2))
    if x >= 0:
        return y1 * _poly3_d0(x / x1, 1, 1, y1d * x1 / y1, y0d * x1 / y1)
    return y1 * _poly3_d0(x / x1, x2 / x1, y2 / y1, y2d * x1 / y1, y0d * x1 / y1)


def reg_root2(x, x_small, k1, k2, use_yd0=False, yd0=1.0):
    """Utilities.regRoot2."""
    if x >= x_small:
        return math.sqrt(k1) * math.sqrt(x)
    if x <= -x_small:
        return -math.sqrt(k2) * math.sqrt(abs(x))
    if k1 >= k2:
        return _reg_root2_utility(x, x_small, k1, k2, use_yd0, yd0)
    return -_reg_root2_utility(-x, x_small, k2, k1, use_yd0, yd0)


def _reg_square2_utility(x, x1, k1, k2):
    x2 = -x1
    if x <= x2:
        return -k2 * x * x
    y1, y2 = k1 * x1 * x1, -k2 * x2 * x2
    y1d, y2d = k1 * 2 * x1, -k2 * 2 * x2
    w = x2 / x1
    y0d = ((3 * y2 - x2 * y2d) / w - (3 * y1 - x1 * y1d) * w) / (2 * x1 * (1 - w))
    ww = 0.9 * min(math.sqrt(5) * k1 * x1, math.sqrt(5) * k2 * abs(x2))
    if ww < y0d:
        y0d = ww
    return _poly3_d0(x, x1, y1, y1d, y0d) if x >= 0 else _poly3_d0(x, x2, y2, y2d, y0d)


def reg_square2(x, x_small, k1, k2):
    """Utilities.regSquare2 (use_yd0 = false): k1 x|x| for x >= 0, k2 x|x| below, cubic within x_small."""
    if x >= x_small:
        return k1 * x * x
    if x <= -x_small:
        return -k2 * x * x
    if k1 >= k2:
        return _reg_square2_utility(x, x_small, k1, k2)
    return -_reg_square2_utility(-x, x_small, k2, k1)


def reg_step(x, y1, y2, x_small=1e-5):
    """Utilities.regStep."""
    if x > x_small:
        return y1
    if x < -x_small:
        return y2
    if x_small > 0:
        return (x / x_small) * ((x / x_small) ** 2 - 3) * (y2 - y1) / 4 + (y1 + y2) / 2
    return (y1 + y2) / 2


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
        if k in THERMAL:
            continue
        if C.KINDS[k].get("domain") != "mfluid":
            return False, (f"{c} ({k}) is not Modelica.Fluid or a thermal part the reference has ("
                           f"{', '.join(sorted(THERMAL))})")
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
        if k == "fl_tank":
            if sysp.get("dynamics", "fixed_initial") != "fixed_initial":
                return False, f"{c}: system dynamics {sysp.get('dynamics')} (tank levels from fixed_initial only)"
    if sysp and not sysp.get("allowFlowReversal", True):
        return False, "allowFlowReversal false"
    return True, ""


def _cp(p):
    """(cp, cv): ConstantPropertyLiquidWater 4184, 4184; a custom liquid's cp for both (omct sets cv_const = cp)."""
    m = p.get("medium") or "water"
    if isinstance(m, dict):
        return float(m["cp"]), float(m["cp"])
    return 4184.0, 4184.0


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
                tk = {"e": e, "A": float(p["crossArea"]), "rho": rho, "nodes": ports, "loss": [],
                      "h0": max(float(p["level_start"]), EPS), "ports": [net.node_of[f"{e}.port[{j}]"]
                                                                         for j in range(1, nports + 1)]}
                self.tanks.append(tk)
                D = p.get("port_diameter")
                for j, n_ in enumerate(ports, 1):
                    if D is None:          # use_portsData false: ports[i].p = vessel_ps_static[i]
                        self._fix(n_, ("tank", ti))
                        self.fixed_ports.append((e, j, net.node_of[f"{e}.port[{j}]"]))
                        continue
                    # use_portsData: a loss between the port and the static pressure (an element of its own,
                    # m = ports[j].m_flow into the tank, from the port's node to a node at the static pressure)
                    st = ("tank_static", e, j)
                    self.nodes.setdefault(st, len(self.nodes))
                    self._fix(st, ("tank", ti))
                    tk["loss"].append((j, len(self.flows)))
                    self.flows.append({"e": e, "k": "tank_port", "p": p, "rho": rho, "mu": None, "ti": ti,
                                       "D": float(D), "a": n_, "b": st, "a0": net.node_of[f"{e}.port[{j}]"],
                                       "b0": st, "j": j})
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
        self._thermal_setup(sysp)

    def _fix(self, n, how):
        if n in self.fixed and self.fixed[n] != how:
            raise FluidRefError("two fixed pressures at one connection point (boundaries or tanks joined directly)")
        self.fixed[n] = how

    def breaks(self, t0, t1, ends=False):
        return []

    def _m_of(self, x):
        """Mass flows from the unknowns: a pump with a check valve solves for MSL's s (m = nParallel max(s, 0))."""
        m = np.array(x, float)
        for i, f in enumerate(self.flows):
            if f["k"] == "fl_pump" and f["p"].get("checkValve"):
                m[i] = float(f["p"]["nParallel"]) * max(x[i], 0.0)
        return m

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
        if k == "tank_port":       # PartialLumpedVessel, use_portsData (VesselPortsData zeta_out 0.5, zeta_in 1.04)
            D = f["D"]
            Ap, Av = math.pi / 4 * D * D, float(p["crossArea"])
            level = self._levels[f["ti"]]
            pen = reg_step(level - 0.1 * D, 1.0, 1e-3, 0.1 * D)
            k_in = (1.04 - 1 + Ap ** 2 / Av ** 2) / rho * pen
            k_out = (0.5 + 1 - Ap ** 2 / Av ** 2) / rho / pen
            return (dp - 0.5 / Ap ** 2 * reg_square2(m, self.m_small, k_in, k_out)) / 1e5
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
            if p.get("checkValve"):
                return m - rc * Av * math.sqrt(rho) * reg_root2(dp, self.dp_small, 1.0, 0.0, True, 0.0)
            return m - rc * Av * reg_root2(dp, self.dp_small, rho, rho)
        if k == "fl_valve_linear":
            return m - float(p["opening"]) * float(p["m_flow_nominal"]) / float(p["dp_nominal"]) * dp
        if k == "fl_pump":
            n0 = float(p["N_nominal"])
            n = float(p["N"]) if p.get("N") is not None else n0
            pts = p["curve"]
            V, H = [r[0] for r in pts], [r[1] for r in pts]
            if p.get("checkValve"):          # m here is MSL's s (NoHomotopy): closed for s <= 0
                if m > 0:
                    head = (n / n0) ** 2 * pump_curve(m / rho * n0 / n, V, H)
                else:
                    head = (n / n0) ** 2 * pump_curve(0.0, V, H) - m
            else:
                vs = m / rho / float(p["nParallel"])
                head = (n / n0) ** 2 * pump_curve(vs * n0 / n, V, H)
            return ((pb - pa) - rho * self.g * head) / 1e5
        raise FluidRefError(f"no relation for {k}")

    def _solve(self, levels):
        from scipy.optimize import root
        nf, fi = len(self.flows), {n: i for i, n in enumerate(self.free)}

        def pressures(x):
            return {n: (x[nf + fi[n]] * 1e5 if n in fi else self._p_fixed(n, levels)) for n in self.nodes}

        self._levels = levels

        def res(x):
            P = pressures(x)
            r = np.empty(nf + len(fi))
            for i, f in enumerate(self.flows):
                r[i] = self._relation(f, x[i], P[f["a"]], P[f["b"]])
            mf = self._m_of(x[:nf])
            bal = np.zeros(len(fi))
            for i, f in enumerate(self.flows):
                if f["a"] in fi:
                    bal[fi[f["a"]]] -= mf[i]
                if f["b"] in fi:
                    bal[fi[f["b"]]] += mf[i]
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
        self._s = best[0][:nf]
        return self._m_of(best[0][:nf]), pressures(best[0])

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
        return np.array([sum(ff[(tk["e"], j)] for j in range(1, len(tk["ports"]) + 1) if (tk["e"], j) in ff) +
                         sum(m[i] for _, i in tk["loss"]) for tk in self.tanks])

    # ---- temperatures (FL9) -----------------------------------------------------------------------------------------
    def _thermal_setup(self, sysp):
        """Energy states and the thermal network. The hydraulics do not depend on temperature (constant density and
        viscosity), so temperatures follow the flows: ideal mixing at each connection point (Modelica's stream
        connectors: the inflows' enthalpy weighted by their mass flows), isenthalpic static parts, a pump without a
        volume adding W / m_flow, tanks and pump volumes with der(U) = Hb_flow + Qb_flow + Wb_flow (U = m cv (T - T0),
        h = cp (T - T0), T0 273.15; a vessel's Hb_flow includes m v^2/2 at ports with port_diameter), and a linear
        thermal network on the tanks' heat ports (OpenTank's IdealHeatTransfer: the port is at the fluid's T)."""
        net = self.net
        self.thermal, self.thermal_why = True, ""
        self.T_amb = float(sysp.get("T_ambient", 293.15))
        for e, d in net.elems.items():
            if d["kind"] in ("fl_boundary", "fl_mass_flow") and "T_in" in (d.get("enabled") or ()):
                self.thermal, self.thermal_why = False, f"{d['comp']}: input T_in (temperatures not in the reference)"
        for tk in self.tanks:
            p = net.elems[tk["e"]]["params"]
            tk["cp"], tk["cv"] = _cp(p)
            tk["T_start"] = float(p["T_start"]) if p.get("T_start") is not None else self.T_amb
        self.pvols = []
        for i, f in enumerate(self.flows):
            f["cp"] = _cp(f["p"])[0] if f["k"] != "tank_port" else self.tanks[f["ti"]]["cp"]
            if f["k"] == "fl_pump" and float(f["p"].get("V") or 0) > 0:
                f["vol"] = len(self.pvols)
                cp, cv = _cp(f["p"])
                self.pvols.append({"i": i, "m": f["rho"] * float(f["p"]["V"]), "cp": cp, "cv": cv,
                                   "T_start": float(f["p"]["T_start"]) if f["p"].get("T_start") is not None
                                   else self.T_amb})
        self.h_bnd = {}
        for e, d in net.elems.items():
            if d["kind"] in ("fl_boundary", "fl_mass_flow"):
                cp, _ = _cp(d["params"])
                self.h_bnd[e] = cp * (float(d["params"]["T"]) - T0_MEDIUM)
        self.h_default = 4184.0 * (self.T_amb - T0_MEDIUM)
        # the thermal network: nodes of heat ports; fixed by fixed_temperature, a capacitor or a tank's heat port
        par = {}

        def find(n):
            while par.get(n, n) != n:
                n = par[n]
            return n
        tnode = {}
        for ep, nid in net.node_of.items():
            if net.port_type(ep) == "heat":
                tnode[ep] = nid
        self.tnode = {ep: find(n) for ep, n in tnode.items()}
        self.cond, self.hflows, self.tfix, self.caps, self.tsens = [], [], {}, [], []
        self.tel = {}

        def fix(n, how, who):
            if n in self.tfix and self.tfix[n] != how:
                raise FluidRefError(f"{who}: two parts fix one temperature (a fixed temperature, a capacitor or a "
                                    f"tank's heat port on one thermal node)")
            self.tfix[n] = how
        for e, d in sorted(net.elems.items()):
            k, p = d["kind"], d["params"]
            if k not in THERMAL:
                continue
            if k == "thermal_conductor":
                G = float(p["G"]) if p.get("G") is not None else float(p["k"]) * float(p["A"]) / float(p["L"])
                self.cond.append((self.tnode[f"{e}.port_a"], self.tnode[f"{e}.port_b"], G, e))
            elif k == "thermal_resistor":
                self.cond.append((self.tnode[f"{e}.port_a"], self.tnode[f"{e}.port_b"], 1.0 / float(p["R"]), e))
            elif k == "fixed_heat_flow":
                self.hflows.append((self.tnode[f"{e}.port"], float(p["Q_flow"]), float(p["alpha"]), float(p["T_ref"]), e))
            elif k == "fixed_temperature":
                fix(self.tnode[f"{e}.port"], ("T", float(p["T"])), d["comp"])
            elif k == "heat_capacitor":
                C_ = float(p["C"]) if p.get("C") is not None else float(p["m"]) * float(p["cp"])
                ci = len(self.caps)
                self.caps.append({"e": e, "C": C_, "T0": float(d["inits"]["T0"][0]), "n": self.tnode[f"{e}.port"]})
                fix(self.tnode[f"{e}.port"], ("cap", ci), d["comp"])
            elif k == "temperature_sensor":
                self.tsens.append((e, self.tnode[f"{e}.port"]))
        for ti, tk in enumerate(self.tanks):
            ep = f"{tk['e']}.heatPort"
            tk["hn"] = self.tnode.get(ep) if "heatPort" in (net.elems[tk["e"]].get("enabled") or ()) else None
            if tk["hn"] is not None:
                fix(tk["hn"], ("tank", ti), net.elems[tk["e"]]["comp"])
        allt = set(self.tnode.values())
        self.tfree = sorted((n for n in allt if n not in self.tfix), key=str)
        if (self.cond or self.hflows or self.caps or self.tsens) and not self.thermal:
            self.thermal_why += " (the thermal parts are then not computed either)"

    def _thermal_net(self, Tfix):
        """Free node temperatures from the conductances and heat flows (linear), and the heat into each fixing part
        {node: Q into the part fixing it}."""
        fi = {n: i for i, n in enumerate(self.tfree)}
        nf = len(fi)
        A, b = np.zeros((nf, nf)), np.zeros(nf)

        def T_of(n, x=None):
            return Tfix[n] if n in Tfix else x[fi[n]]
        for na, nb, G, _ in self.cond:
            for n, m_ in ((na, nb), (nb, na)):
                if n in fi:
                    A[fi[n], fi[n]] += G
                    if m_ in fi:
                        A[fi[n], fi[m_]] -= G
                    else:
                        b[fi[n]] += G * Tfix[m_]
        for n, Q, al, Tr, _ in self.hflows:
            if n in fi:                       # injected Q (1 + al (T - Tr)) into the node
                A[fi[n], fi[n]] -= Q * al
                b[fi[n]] += Q * (1 - al * Tr)
        x = np.linalg.solve(A, b) if nf else np.zeros(0)
        T = dict(Tfix)
        T.update({n: x[i] for n, i in fi.items()})
        Qin = {n: 0.0 for n in Tfix}
        for na, nb, G, _ in self.cond:
            q = G * (T[na] - T[nb])              # from a to b
            if na in Qin:
                Qin[na] -= q
            if nb in Qin:
                Qin[nb] += q
        for n, Q, al, Tr, _ in self.hflows:
            if n in Qin:
                Qin[n] += Q * (1 + al * (T[n] - Tr))
        return T, Qin

    def _pump_power(self, f, m, pa, pb):
        p, rho = f["p"], f["rho"]
        n0 = float(p["N_nominal"])
        n = float(p["N"]) if p.get("N") is not None else n0
        npar = float(p["nParallel"])
        if p.get("power") is not None:
            from .chains import quad_points
            pw, _ = quad_points(p["power"])
            c = np.linalg.solve(np.vander([r[0] for r in pw], 3, increasing=True), [r[1] for r in pw])
            rho_n = float(p["rho_power"]) if p.get("rho_power") is not None else rho
            vs = m / rho / npar * n0 / n
            return npar * (n / n0) ** 3 * (rho / rho_n) * (c[0] + c[1] * vs + c[2] * vs * vs)
        return (pb - pa) * (m / rho) / float(p["eta"])

    def _energy(self, levels, Ut, Up, Tc, m, P):
        """(dU tanks, dU pump volumes, dT capacitors, details) at one instant."""
        nt = len(self.tanks)
        mt = np.array([tk["rho"] * tk["A"] * max(levels[i], EPS) for i, tk in enumerate(self.tanks)])
        Tt = np.array([T0_MEDIUM + Ut[i] / (mt[i] * tk["cv"]) for i, tk in enumerate(self.tanks)])
        ht = np.array([tk["cp"] * (Tt[i] - T0_MEDIUM) for i, tk in enumerate(self.tanks)])
        Tp = np.array([T0_MEDIUM + Up[j] / (pv["m"] * pv["cv"]) for j, pv in enumerate(self.pvols)])
        hp = np.array([pv["cp"] * (Tp[j] - T0_MEDIUM) for j, pv in enumerate(self.pvols)])
        W = {}
        for i, f in enumerate(self.flows):
            if f["k"] == "fl_pump":
                W[i] = self._pump_power(f, m[i], P[f["a"]], P[f["b"]])
        # discharges into each connection point: (weight, constant h) or (weight, upstream node, added h)
        dis = {}

        def add(n, w, h=None, up=None, extra=0.0):
            if w > 0:
                dis.setdefault(n, []).append((w, h, up, extra))
        ff = self._fixed_flows(m)
        for e, j, n0 in self.fixed_ports:
            mp = ff[(e, j)]
            if e in self.h_bnd:
                add(self.nodes_root[n0], -mp, h=self.h_bnd[e])
            else:
                ti = next(i for i, tk in enumerate(self.tanks) if tk["e"] == e)
                add(self.nodes_root[n0], -mp, h=ht[ti])
        for n, ms, e, orig in self.sources:
            add(n, ms, h=self.h_bnd[e])
        for i, f in enumerate(self.flows):
            mi = m[i]
            if f["k"] == "tank_port":
                add(f["a"], -mi, h=ht[f["ti"]])
                continue
            dst, src = (f["b"], f["a"]) if mi > 0 else (f["a"], f["b"])
            if "vol" in f:
                add(dst, abs(mi), h=hp[f["vol"]])
            else:
                add(dst, abs(mi), up=src, extra=(W[i] / abs(mi) if i in W and abs(mi) > 0 else 0.0))
        keys = list(self.nodes)
        ix = {n: i for i, n in enumerate(keys)}
        A, b = np.zeros((len(keys), len(keys))), np.zeros(len(keys))
        for n in keys:
            r = ix[n]
            items = dis.get(n, [])
            wsum = sum(w for w, *_ in items)
            if wsum <= 0:
                A[r, r], b[r] = 1.0, self.h_default
                continue
            A[r, r] = wsum
            for w, h, up, extra in items:
                if up is None:
                    b[r] += w * h
                else:
                    A[r, ix[up]] -= w
                    b[r] += w * extra
        hmix = dict(zip(keys, np.linalg.solve(A, b)))
        # heat ports
        Tfix = {}
        for n, how in self.tfix.items():
            Tfix[n] = how[1] if how[0] == "T" else (Tc[how[1]] if how[0] == "cap" else Tt[how[1]])
        Tnet, Qin = self._thermal_net(Tfix) if self.tnode else ({}, {})
        dUt = np.zeros(nt)
        Qt = np.zeros(nt)
        for ti, tk in enumerate(self.tanks):
            H = 0.0
            for j in range(1, len(tk["ports"]) + 1):
                if (tk["e"], j) in ff:
                    mp = ff[(tk["e"], j)]
                    n0 = self.nodes_root[tk["ports"][j - 1]]
                    H += mp * (hmix[n0] if mp > 0 else ht[ti])
            for j, i in tk["loss"]:
                f = self.flows[i]
                mi = m[i]
                Ap = math.pi / 4 * f["D"] ** 2
                H += mi * (hmix[f["a"]] if mi > 0 else ht[ti]) + mi * 0.5 * (mi / (tk["rho"] * Ap)) ** 2
            Qt[ti] = Qin.get(tk["hn"], 0.0) if tk.get("hn") is not None else 0.0
            dUt[ti] = H + Qt[ti]
        dUp = np.zeros(len(self.pvols))
        for jv, pv in enumerate(self.pvols):
            i = pv["i"]
            f = self.flows[i]
            mi = m[i]
            up = f["a"] if mi > 0 else f["b"]
            dUp[jv] = abs(mi) * (hmix[up] - hp[jv]) + W[i]
        dTc = np.array([Qin.get(c["n"], 0.0) / c["C"] for c in self.caps])
        return dUt, dUp, dTc, {"hmix": hmix, "Tt": Tt, "Tp": Tp, "Tnet": Tnet, "Qin": Qin, "Qt": Qt, "W": W}

    # ---- over time --------------------------------------------------------------------------------------------------
    def simulate(self, t_eval, t0=None) -> dict:
        from scipy.integrate import solve_ivp
        t = np.asarray(t_eval, float)
        self._x = None
        nt, nv, nc = len(self.tanks), len(self.pvols), len(self.caps)
        th = self.thermal
        h0 = np.array([tk["h0"] for tk in self.tanks])
        rho_a = np.array([tk["rho"] * tk["A"] for tk in self.tanks])
        U0 = np.array([tk["rho"] * tk["A"] * tk["h0"] * tk["cv"] * (tk["T_start"] - T0_MEDIUM) for tk in self.tanks])
        V0 = np.array([pv["m"] * pv["cv"] * (pv["T_start"] - T0_MEDIUM) for pv in self.pvols])
        C0 = np.array([c["T0"] for c in self.caps])
        x0 = np.concatenate([h0] + ([U0, V0, C0] if th else []))

        def split(x):
            return x[:nt], x[nt:2 * nt], x[2 * nt:2 * nt + nv], x[2 * nt + nv:]

        def rhs(_, x):
            lv, Ut, Up, Tc = split(x)
            m, P = self._solve(lv)
            dl = self._tank_inflows(m, P) / rho_a if nt else np.zeros(0)
            if not th:
                return dl
            dUt, dUp, dTc, _ = self._energy(lv, Ut, Up, Tc, m, P)
            return np.concatenate([dl, dUt, dUp, dTc])
        ts = float(t[0] if t0 is None else t0)
        X = np.repeat(x0[:, None], t.size, axis=1)
        if x0.size and t[-1] > ts:
            sc = np.concatenate([np.full(nt, max(1.0, float(np.max(h0)) if nt else 1.0) * 1e-12)] +
                                ([np.maximum(np.abs(U0), 1.0) * 1e-12, np.maximum(np.abs(V0), 1.0) * 1e-12,
                                  np.full(nc, 1e-9)] if th else []))
            sol = solve_ivp(rhs, (ts, float(t[-1])), x0, t_eval=t[t >= ts], method="Radau", rtol=1e-10, atol=sc)
            if not sol.success:
                raise FluidRefError(f"the tank levels and temperatures did not integrate: {sol.message}")
            X[:, t >= ts] = sol.y
        H = X[:nt]
        if np.any(H < 0):
            raise FluidRefError("a tank ran empty (below its ports: MSL's inflow-only mode, not in the reference)")
        self._x = None
        cols, ecols = [], []
        for j in range(t.size):
            lv, Ut, Up, Tc = split(X[:, j])
            m, P = self._solve(lv)
            cols.append((m, P))
            ecols.append(self._energy(lv, Ut, Up, Tc, m, P) if th else None)
        out = self._paths(t, H, cols)
        if th:
            out.update(self._thermal_paths(t, X, nt, nv, cols, ecols))
        return out

    def _thermal_paths(self, t, X, nt, nv, cols, ecols) -> dict:
        out, net = {}, self.net

        def ser(fn):
            return np.array([fn(k) for k in range(t.size)], float)
        for ti, tk in enumerate(self.tanks):
            e = tk["e"]
            out[f"{e}.medium.T"] = ser(lambda k, ti=ti: ecols[k][3]["Tt"][ti])
            out[f"{e}.U"] = X[nt + ti]
            if tk.get("hn") is not None:
                out[f"{e}.heatPort.T"] = out[f"{e}.medium.T"]
                out[f"{e}.heatPort.Q_flow"] = ser(lambda k, ti=ti: ecols[k][3]["Qt"][ti])
        for jv, pv in enumerate(self.pvols):
            e = self.flows[pv["i"]]["e"]
            out[f"{e}.medium.T"] = ser(lambda k, jv=jv: ecols[k][3]["Tp"][jv])
        for i, f in enumerate(self.flows):
            if f["k"] == "fl_temperature":
                cp = f["cp"]

                def Ts(k, f=f, i=i, cp=cp):
                    hm = ecols[k][3]["hmix"]
                    Ta, Tb = T0_MEDIUM + hm[f["a"]] / cp, T0_MEDIUM + hm[f["b"]] / cp
                    return reg_step(cols[k][0][i], Ta, Tb, self.m_small)
                out[f"{f['e']}.T"] = ser(Ts)
        # thermal parts
        def Tn(n):
            return ser(lambda k: ecols[k][3]["Tnet"][n])
        for ci, c in enumerate(self.caps):
            e = c["e"]
            out[f"{e}.T"] = X[2 * nt + nv + ci]
            out[f"{e}.der_T"] = ser(lambda k, c=c: ecols[k][3]["Qin"].get(c["n"], 0.0) / c["C"])
            out[f"{e}.port.T"] = out[f"{e}.T"]
            out[f"{e}.port.Q_flow"] = out[f"{e}.der_T"] * c["C"]
        for na, nb, G, e in self.cond:
            Ta, Tb = Tn(na), Tn(nb)
            out.update({f"{e}.port_a.T": Ta, f"{e}.port_b.T": Tb, f"{e}.dT": Ta - Tb, f"{e}.Q_flow": G * (Ta - Tb),
                        f"{e}.port_a.Q_flow": G * (Ta - Tb), f"{e}.port_b.Q_flow": -G * (Ta - Tb)})
        for n, Q, al, Tr, e in self.hflows:
            T_ = Tn(n)
            out.update({f"{e}.port.T": T_, f"{e}.port.Q_flow": -Q * (1 + al * (T_ - Tr))})
        for e, d in net.elems.items():
            if d["kind"] == "fixed_temperature":
                n = self.tnode[f"{e}.port"]
                out[f"{e}.port.T"] = Tn(n)
                out[f"{e}.port.Q_flow"] = ser(lambda k, n=n: ecols[k][3]["Qin"].get(n, 0.0))
        for e, n in self.tsens:
            out[f"{e}.T"] = Tn(n)
            out[f"{e}.port.T"] = out[f"{e}.T"]
        return out

    def steady(self, t1=None) -> dict:
        """Without tanks the hydraulics are algebraic: the state at any time. With tanks: RefError (no steady
        state is implied; the caller runs to the end instead)."""
        from .ref import RefError
        if self.tanks or (self.thermal and (self.pvols or self.caps)):
            raise RefError("tank levels or temperatures: no algebraic steady state")
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
            if k == "tank_port":
                out.update({f"{e}.ports[{f['j']}].m_flow": mm, f"{e}.ports[{f['j']}].p": pa})
                continue
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
                dpp = pb - pa
                npar = float(p["nParallel"])
                if p.get("power") is not None:     # quadraticPower through quad_points' 3 points, eta = dp V / W
                    from .chains import quad_points
                    pw, _ = quad_points(p["power"])
                    c = np.linalg.solve(np.vander([r[0] for r in pw], 3, increasing=True), [r[1] for r in pw])
                    rho_n = float(p["rho_power"]) if p.get("rho_power") is not None else rho
                    vs = mm / rho / npar * n0 / n
                    W1 = (n / n0) ** 3 * (rho / rho_n) * (c[0] + c[1] * vs + c[2] * vs * vs)
                    W = W1 * npar
                    eta = dpp * (mm / rho / npar) / W1
                else:
                    eta = np.full(t.size, float(p["eta"]))
                    W = dpp * (mm / rho) / eta
                out[f"{e}.m"] = np.full(t.size, rho * float(p.get("V") or 0.0))    # constant density
                out.update({f"{e}.m_flow": mm, f"{e}.V_flow": mm / rho, f"{e}.dp_pump": dpp,
                            f"{e}.head": dpp / (rho * g), f"{e}.eta": eta,
                            f"{e}.W_total": W, f"{e}.N": np.full(t.size, n * C.W_RPM)})
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
