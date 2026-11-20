"""fluidref.py — the reference implementation for Modelica.Fluid liquid networks (V5 FL4).
Version: 2026.11.19.23

An independent solver of the same equations MSL 4.0.0 writes for constant-density liquids, ported from the MSL
sources (tag v4.0.0) rather than reused: what OpenModelica must reproduce. Covered (covered(net)):

  media       water (ConstantPropertyLiquidWater) and custom liquids {rho, cp, eta, lambda}
  kinds       fl_boundary, fl_mass_flow, fl_pipe (friction detailed or nominal_laminar),
              fl_resistance (a > 0), fl_orifice, fl_valve (check valve too), fl_valve_linear, fl_pump (constant
              speed, check valve too, a volume too), fl_tank (port losses too; system dynamics fixed_initial),
              fl_dynamic_pipe (FL11: friction detailed or nominal_laminar, modelStructure av_vb or av_b; heat
              constant or local), fl_volume (FL11), fl_pressure, fl_temperature, fl_mass_flow_sensor,
              fl_volume_flow_sensor, fl_relative_pressure, fl_system
  thermal     temperatures (FL9): mixing at connection points, tanks, pump volumes, closed volumes and the volumes of
              dynamic pipes, a pump's W, a pipe's Wb_flows, and a linear thermal network on the heat ports
              (fixed_temperature, fixed_heat_flow, thermal_conductor, thermal_resistor, heat_capacitor,
              temperature_sensor); T_in inputs, a dynamic pipe's ideal heat transfer and system dynamics other than
              fixed_initial switch temperatures off
  not         anything else outside Modelica.Fluid, gases and T-dependent liquids, inputs that change the flows
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
  dynamic    DynamicPipe (PartialTwoPortFlow, FL11): nNodes volumes of length L/n; momentum steady (system default),
  pipe       so the flow model's nFM segments are static pipes in series between the volumes' pressures: av_vb
             (default) n-1 segments, port_a.p = mediums[1].p, port_b.p = mediums[n].p, pathLengths
             {1.5, 1, ..., 1, 1.5} L/n (L for n = 2); av_b n segments of L/n, the last to port_b; each segment
             DetailedPipeFlow (massFlowRate_dp_staticHead, dp_small/nFM, the segment's static head) or
             NominalLaminarFlow (dp_nominal/nFM); constant density: every m_flows equal. Energy per volume:
             der(Us) = Hb_flows + Wb_flows + Qb_flows, upwind H_flows (semiLinear), Wb_flows as DynamicPipe writes
             them (vs A (dp/dx + friction - g dh rho)), Qb_flows = ConstantFlowHeatTransfer alpha0 pi D L/n
             (T_wall - T_i) nParallel or LocalPipeFlowHeatTransfer (Gnielinski / Hausen spliced at Re 6150 +- 3850)
             through omct's ThermalCollector (every heatPorts[i] at the wall node's T)
  volume     ClosedVolume without portsData: every port at medium.p, U with the ports' enthalpy flows and an ideal
             heat port (heatPort.T = medium.T)
"""
from __future__ import annotations

import math

import numpy as np

from . import catalog as C

KV2AV, CV2AV, RE_TURBULENT = 27.7e-6, 24.0e-6, 4000.0
EPS = 2.220446049250313e-16
COVERED = {"fl_system", "fl_boundary", "fl_mass_flow", "fl_pipe", "fl_resistance", "fl_orifice", "fl_valve",
           "fl_valve_linear", "fl_pump", "fl_tank", "fl_pressure", "fl_temperature", "fl_mass_flow_sensor",
           "fl_volume_flow_sensor", "fl_relative_pressure", "fl_dynamic_pipe", "fl_volume"}
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
            return False, f"{c} ({k}) is not in the Fluid reference (gas valves, shaft pumps)"
        if k == "fl_system":
            continue
        if C.fmedium(p.get("medium") or "water")["class"] != "liquid_const":
            return False, f"{c}: medium {C.fmedium(p['medium'])['name']} (constant-density liquids only)"
        inputs = sorted(HYDRAULIC_INPUTS & set(d.get("enabled") or ()))
        if inputs:
            return False, f"{c}: input {inputs[0]} connected (constant values only)"
        if k in ("fl_pipe", "fl_dynamic_pipe"):
            if p.get("friction") not in ("detailed", "nominal_laminar"):
                return False, f"{c}: friction {p.get('friction')} (detailed and nominal_laminar only)"
        if k == "fl_dynamic_pipe":
            st = p.get("_structure") or "av_vb"
            if st not in ("av_vb", "av_b"):
                return False, f"{c}: modelStructure {st} (av_vb and av_b only)"
            if st == "av_vb" and int(p["nNodes"]) < 2:
                return False, f"{c}: nNodes 1 with modelStructure av_vb (MSL asserts nNodes >= 2)"
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


def _lambda(p):
    """Thermal conductivity: ConstantPropertyLiquidWater lambda_const 0.598, or a custom liquid's lambda."""
    m = p.get("medium") or "water"
    return float(m.get("lambda", 0.598)) if isinstance(m, dict) else 0.598


def splice(pos, neg, x, deltax):
    """Modelica.Media.Air.MoistAir.Utilities.spliceFunction (LocalPipeFlowHeatTransfer's laminar-turbulent blend)."""
    s1 = x / deltax
    if s1 <= -0.999999999:
        y = 0.0
    elif s1 >= 0.999999999:
        y = 1.0
    else:
        y = (math.tanh(math.tan(s1 * math.asin(1.0))) + 1) / 2
    return pos * y + (1 - y) * neg


def local_alpha(Re, Pr, D, Li, pos, lam):
    """LocalPipeFlowHeatTransfer's alpha of one segment: Nus_turb (Gnielinski with the entry term), Nus_lam
    (Hausen-type with Nu_1 = 3.66), spliced at Re - 6150 over 3850; alpha = Nu lambda / D. pos: i - 0.5 for
    vs >= 0, n - i + 0.5 else (1-based i)."""
    Xi = (1.8 * math.log10(max(1e-10, Re)) - 1.5) ** -2
    turb = (Xi / 8) * abs(Re) * Pr / (1 + 12.7 * (Xi / 8) ** 0.5 * (Pr ** (2 / 3) - 1)) * \
        (1 + 1 / 3 * (D / Li / pos) ** (2 / 3))
    nu2 = 1.077 * (abs(Re) * Pr * D / Li / pos) ** (1 / 3)
    lamn = (3.66 ** 3 + 0.7 ** 3 + (nu2 - 0.7) ** 3) ** (1 / 3)
    return splice(turb, lamn, Re - 6150, 3850) * lam / D


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
            if d["kind"] in ("fl_mass_flow", "fl_volume"):     # a closed volume's ports: all at medium.p
                ns = [net.node_of[f"{e}.port[{j}]"] for j in range(1, d.get("multi_n", {}).get("port", 0) + 1)]
                for n in ns[1:]:
                    a, b = find(ns[0]), find(n)
                    if a != b:
                        parent[b] = a
        self.find = lambda ep: find(net.node_of[ep])
        self.nodes_root = {n: find(n) for n in set(net.node_of.values())}
        self.nodes, self.fixed, self.tanks, self.sources, self.flows, self.fixed_ports = {}, {}, [], [], [], []
        self.xvols, self.dpipes = [], []     # closed volumes and dynamic pipes' volumes (energy states); the pipes

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
            elif k == "fl_volume":
                rho, _ = _medium(p)
                cp, cv = _cp(p)
                if nports:
                    node(f"{e}.port[1]")
                self.xvols.append({"kind": "vol", "e": e, "m": rho * float(p["V"]), "cp": cp, "cv": cv, "rho": rho,
                                   "T_start": p.get("T_start"),
                                   "ports": [net.node_of[f"{e}.port[{j}]"] for j in range(1, nports + 1)]})
            elif k == "fl_dynamic_pipe":
                self._dpipe(e, d, node)
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
        # a closed volume's port flows come from what the other components put into its connection points: one
        # volume port there and no boundary, tank or multi-port source
        other = [n for _, _, n in self.fixed_ports] + [n0 for s in self.sources if len(s[3]) > 1 for n0 in s[3]]
        vports = [n0 for xv in self.xvols if xv["kind"] == "vol" for n0 in xv["ports"]]
        for xv in self.xvols:
            if xv["kind"] == "vol" and any(n0 in other or vports.count(n0) > 1 for n0 in xv["ports"]):
                raise FluidRefError(f"{net.elems[xv['e']]['comp']}: a closed volume's port on a connection point "
                                    f"with a boundary, a tank, a source of several ports or another volume port (not in the "
                                    f"reference)")
        self._x = None
        self._thermal_setup(sysp)

    def _dpipe(self, e, d, node):
        """A DynamicPipe (PartialTwoPortFlow, momentum steady): its volumes' hydraulic nodes (port_a's node is
        mediums[1]'s; av_vb: port_b's is mediums[n]'s) and the flow model's segments between them."""
        net, p = self.net, d["params"]
        n, st = int(p["nNodes"]), p.get("_structure") or "av_vb"
        rho, mu = _medium(p)
        cp, cv = _cp(p)
        L, D, npar = float(p["length"]), float(p["diameter"]), float(p["nParallel"])
        A = math.pi * D * D / 4
        li, hi = L / n, float(p["height_ab"]) / n            # lengths = L/n, dheights = height_ab dxs
        na, nb = node(f"{e}.port_a"), node(f"{e}.port_b")
        a0, b0 = net.node_of[f"{e}.port_a"], net.node_of[f"{e}.port_b"]
        if st == "av_vb":
            vn = [na] + [("dpipe", e, i) for i in range(2, n)] + [nb]
            if n == 2:
                pl, dh = [2 * li], [2 * hi]
            else:
                pl = [1.5 * li] + [li] * (n - 3) + [1.5 * li]
                dh = [1.5 * hi] + [hi] * (n - 3) + [1.5 * hi]
            ends = list(zip(vn[:-1], vn[1:]))
        else:                                                   # av_b
            vn = [na] + [("dpipe", e, i) for i in range(2, n + 1)]
            pl, dh = [li] * n, [hi] * n
            ends = list(zip(vn, vn[1:] + [nb]))
        for v in vn:
            self.nodes.setdefault(v, len(self.nodes))
        pi_ = len(self.dpipes)
        segs = []
        for k, (u, v) in enumerate(ends):
            segs.append(len(self.flows))
            self.flows.append({"e": e, "k": "dp_seg", "p": p, "rho": rho, "mu": mu, "a": u, "b": v,
                               "a0": a0 if k == 0 else u, "b0": b0 if k == len(ends) - 1 else v,
                               "L": pl[k], "dh": dh[k], "nFM": len(ends), "pipe": pi_, "seg": k})
        x0 = len(self.xvols)
        for i in range(n):
            self.xvols.append({"kind": "pipe", "e": e, "pipe": pi_, "i": i, "m": rho * A * li * npar, "cp": cp,
                               "cv": cv, "rho": rho, "T_start": p.get("T_start")})
        self.dpipes.append({"e": e, "n": n, "st": st, "vn": vn, "na": na, "nb": nb, "a0": a0, "b0": b0,
                            "segs": segs, "x0": x0, "rho": rho, "mu": mu, "A": A, "D": D, "li": li, "hi": hi,
                            "npar": npar, "cp": cp, "lam": _lambda(p), "p": p,
                            "heat": "heat" in (d.get("enabled") or ())})

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
        if k == "dp_seg":            # one segment of a dynamic pipe's flow model (dps_fg = p_i - p_(i+1), steady)
            gh, nfm = self.g * f["dh"], f["nFM"]
            if p["friction"] == "nominal_laminar":
                return (dp - gh * rho - float(p["dp_nominal"]) / nfm / float(p["m_flow_nominal"]) * m) / 1e5
            return m - float(p["nParallel"]) * pipe_detailed(dp, rho, f["mu"], f["L"], float(p["diameter"]),
                                                             float(p["roughness"]), self.dp_small / nfm, gh)
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
        for dp in self.dpipes:
            if dp["heat"] and dp["p"].get("heat_transfer", "ideal") not in ("constant", "local"):
                self.thermal, self.thermal_why = False, (f"{net.elems[dp['e']]['comp']}: ideal heat transfer (the "
                                                         f"volumes at the wall's temperature: not in the reference)")
        dyn = sysp.get("dynamics", "fixed_initial")
        if dyn != "fixed_initial" and (self.xvols or any(float(f["p"].get("V") or 0) > 0 for f in self.flows
                                                         if f["k"] == "fl_pump")):
            self.thermal, self.thermal_why = False, f"system dynamics {dyn} (temperatures from fixed_initial only)"
        for xv in self.xvols:
            xv["T_start"] = float(xv["T_start"]) if xv["T_start"] is not None else self.T_amb
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
        for xi, xv in enumerate(self.xvols):         # a closed volume's heat port: IdealHeatTransfer, at medium.T
            xv["hn"] = None
            if xv["kind"] == "vol" and "heatPort" in (net.elems[xv["e"]].get("enabled") or ()):
                xv["hn"] = self.tnode.get(f"{xv['e']}.heatPort")
                if xv["hn"] is not None:
                    fix(xv["hn"], ("x", xi), net.elems[xv["e"]]["comp"])
        # a dynamic pipe's heat port: omct's ThermalCollector puts every heatPorts[i] at the wall node's T; each
        # volume is a node fixed at its T, joined to the wall by alpha_i pi D L/n nParallel (per instant: local)
        self.pcond = []
        for pi_, dp in enumerate(self.dpipes):
            if not dp["heat"] or dp["p"].get("heat_transfer") not in ("constant", "local"):
                continue
            wall = self.tnode.get(f"{dp['e']}.heat")
            for i in range(dp["n"]):
                vn = ("dpvol", dp["e"], i)
                fix(vn, ("x", dp["x0"] + i), net.elems[dp["e"]]["comp"])
                self.pcond.append((wall, vn, pi_, i))
        allt = set(self.tnode.values())
        self.tfree = sorted((n for n in allt if n not in self.tfix), key=str)
        self.has_tnet = bool(self.tnode)
        if (self.cond or self.hflows or self.caps or self.tsens) and not self.thermal:
            self.thermal_why += " (the thermal parts are then not computed either)"

    def _thermal_net(self, Tfix, extra=()):
        """Free node temperatures from the conductances (self.cond and extra, the pipes' of this instant) and heat
        flows (linear), and the heat into each fixing part {node: Q into the part fixing it}."""
        fi = {n: i for i, n in enumerate(self.tfree)}
        nf = len(fi)
        A, b = np.zeros((nf, nf)), np.zeros(nf)
        cond = list(self.cond) + list(extra)
        for na, nb, G, _ in cond:
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
        for na, nb, G, _ in cond:
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

    def _vol_flows(self, m):
        """{(xvol index, j): ports[j].m_flow into a closed volume}: what the other components put into its
        connection point (one volume port there, no boundary, tank or source: __init__)."""
        q = self._q_orig(m)
        return {(xi, j): q.get(n0, 0.0) for xi, xv in enumerate(self.xvols) if xv["kind"] == "vol"
                for j, n0 in enumerate(xv["ports"], 1)}

    def _pipe_flow(self, dp, m, P):
        """A dynamic pipe's m_flows[1..n+1], vs, port and volume pressures and dps_fg at one instant."""
        sg = [m[i] for i in dp["segs"]]
        mf = [sg[0]] + sg + ([sg[-1]] if dp["st"] == "av_vb" else [])
        rho, A, npar = dp["rho"], dp["A"], dp["npar"]
        vs = [0.5 * (mf[i] + mf[i + 1]) / rho / A / npar for i in range(dp["n"])]
        dps = [P[self.flows[i]["a"]] - P[self.flows[i]["b"]] for i in dp["segs"]]
        return {"mf": mf, "vs": vs, "pm": [P[v] for v in dp["vn"]], "pa": P[dp["na"]], "pb": P[dp["nb"]],
                "dps": dps}

    def _pipe_wb(self, dp, fl):
        """DynamicPipe's Wb_flows (vs A (dp/dx + friction - g dh rho) nParallel, as MSL writes them per
        modelStructure)."""
        n, A, npar, rho, g, dh = dp["n"], dp["A"], dp["npar"], dp["rho"], self.g, dp["hi"]
        vs, pm, dps, pa, pb = fl["vs"], fl["pm"], fl["dps"], fl["pa"], fl["pb"]
        if n == 1:
            return [vs[0] * A * ((pb - pa) + sum(dps) - g * dh * rho) * npar]
        W = [0.0] * n
        for i in range(1, n - 1):
            W[i] = vs[i] * A * ((pm[i + 1] - pm[i - 1]) / 2 + (dps[i - 1] + dps[i]) / 2 - g * dh * rho) * npar
        W[0] = vs[0] * A * ((pm[1] - pm[0]) / 2 + dps[0] / 2 - g * dh * rho) * npar
        if dp["st"] == "av_vb":
            W[n - 1] = vs[n - 1] * A * ((pm[n - 1] - pm[n - 2]) / 2 + dps[n - 2] / 2 - g * dh * rho) * npar
        else:
            W[n - 1] = vs[n - 1] * A * ((pb - pm[n - 2]) / 1.5 + dps[n - 2] / 2 + dps[n - 1] - g * dh * rho) * npar
        return W

    def _pipe_alphas(self, dp, fl):
        """Heat transfer coefficients of a dynamic pipe's volumes: alpha0 (constant) or LocalPipeFlowHeatTransfer."""
        p, n = dp["p"], dp["n"]
        if p.get("heat_transfer") == "constant":
            return [float(p["alpha"])] * n
        rho, mu, D, lam = dp["rho"], dp["mu"], dp["D"], dp["lam"]
        Pr = mu * dp["cp"] / lam
        out = []
        for i, v in enumerate(fl["vs"], 1):
            Re = abs(v) * rho * D / mu
            out.append(local_alpha(Re, Pr, D, dp["li"], (i - 0.5) if v >= 0 else (n - i + 0.5), lam))
        return out

    def _energy(self, levels, Ut, Up, Ux, Tc, m, P, ports=False):
        """(dU tanks, dU pump volumes, dU closed and pipe volumes, dT capacitors, details) at one instant. Mixing
        at each connection point of the network (the study's, not the joined hydraulic nodes); ports: also the
        ports' h_outflow (output points only)."""
        nt = len(self.tanks)
        mt = np.array([tk["rho"] * tk["A"] * max(levels[i], EPS) for i, tk in enumerate(self.tanks)])
        Tt = np.array([T0_MEDIUM + Ut[i] / (mt[i] * tk["cv"]) for i, tk in enumerate(self.tanks)])
        ht = np.array([tk["cp"] * (Tt[i] - T0_MEDIUM) for i, tk in enumerate(self.tanks)])
        Tp = np.array([T0_MEDIUM + Up[j] / (pv["m"] * pv["cv"]) for j, pv in enumerate(self.pvols)])
        hp = np.array([pv["cp"] * (Tp[j] - T0_MEDIUM) for j, pv in enumerate(self.pvols)])
        Tx = np.array([T0_MEDIUM + Ux[j] / (xv["m"] * xv["cv"]) for j, xv in enumerate(self.xvols)])
        hx = np.array([xv["cp"] * (Tx[j] - T0_MEDIUM) for j, xv in enumerate(self.xvols)])
        W = {}
        for i, f in enumerate(self.flows):
            if f["k"] == "fl_pump":
                W[i] = self._pump_power(f, m[i], P[f["a"]], P[f["b"]])
        # discharges into each connection point: (weight, constant h) or (weight, upstream node, added h), and the
        # port that discharges (inStream at a port excludes its own outflow)
        dis = {}

        def add(n, w, h=None, up=None, extra=0.0, own=None):
            if w > 0:
                dis.setdefault(n, []).append((w, h, up, extra, own))
        ff = self._fixed_flows(m)
        for e, j, n0 in self.fixed_ports:
            mp = ff[(e, j)]
            if e in self.h_bnd:
                add(n0, -mp, h=self.h_bnd[e], own=(e, j))
            else:
                ti = next(i for i, tk in enumerate(self.tanks) if tk["e"] == e)
                add(n0, -mp, h=ht[ti], own=(e, j))
        q_nosrc = None
        for n, ms, e, orig in self.sources:
            if len(orig) == 1:
                add(orig[0], ms, h=self.h_bnd[e], own=(e, 1))
            else:
                q_nosrc = self._q_orig(m, False) if q_nosrc is None else q_nosrc
                for j, n0 in enumerate(orig, 1):
                    add(n0, -q_nosrc.get(n0, 0.0), h=self.h_bnd[e], own=(e, j))
        vq = self._vol_flows(m)
        for (xi, j), mp in vq.items():
            add(self.xvols[xi]["ports"][j - 1], -mp, h=hx[xi], own=(self.xvols[xi]["e"], j))
        for i, f in enumerate(self.flows):
            mi = m[i]
            if f["k"] == "tank_port":
                add(f["a0"], -mi, h=ht[f["ti"]], own=(f["e"], f["j"]))
                continue
            if f["k"] == "dp_seg":
                continue
            dst, src = (f["b0"], f["a0"]) if mi > 0 else (f["a0"], f["b0"])
            if "vol" in f:
                add(dst, abs(mi), h=hp[f["vol"]], own=f["e"])
            else:
                add(dst, abs(mi), up=src, extra=(W[i] / abs(mi) if i in W and abs(mi) > 0 else 0.0), own=f["e"])
        pfl = []
        for dp in self.dpipes:                   # a pipe's ports discharge its end volumes' h
            fl = self._pipe_flow(dp, m, P)
            pfl.append(fl)
            if fl["mf"][0] < 0:
                add(dp["a0"], -fl["mf"][0], h=hx[dp["x0"]], own=dp["e"])
            if fl["mf"][-1] > 0:
                add(dp["b0"], fl["mf"][-1], h=hx[dp["x0"] + dp["n"] - 1], own=dp["e"])
        keys = list(dict.fromkeys(list(self.nodes) + list(self.nodes_root) +
                                  [f[x] for f in self.flows for x in ("a0", "b0")]))
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
            for w, h, up, extra, _ in items:
                if up is None:
                    b[r] += w * h
                else:
                    A[r, ix[up]] -= w
                    b[r] += w * extra
        hmix = dict(zip(keys, np.linalg.solve(A, b)))
        hout = self._h_outflow(dis, hmix, m, W, ht, hp, hx) if ports else {}
        # heat ports
        Tfix = {}
        for n, how in self.tfix.items():
            Tfix[n] = {"T": lambda v: v, "cap": lambda v: Tc[v], "tank": lambda v: Tt[v],
                       "x": lambda v: Tx[v]}[how[0]](how[1])
        alphas = [self._pipe_alphas(dp, pfl[pi_]) if dp["heat"] and dp["p"].get("heat_transfer") in
                  ("constant", "local") else None for pi_, dp in enumerate(self.dpipes)]
        extra = [(wall, vn, alphas[pi_][i] * math.pi * self.dpipes[pi_]["D"] * self.dpipes[pi_]["li"] *
                  self.dpipes[pi_]["npar"], None) for wall, vn, pi_, i in self.pcond]
        Tnet, Qin = self._thermal_net(Tfix, extra) if (self.tnode or extra) else ({}, {})
        dUt = np.zeros(nt)
        Qt = np.zeros(nt)
        for ti, tk in enumerate(self.tanks):
            H = 0.0
            for j in range(1, len(tk["ports"]) + 1):
                if (tk["e"], j) in ff:
                    mp = ff[(tk["e"], j)]
                    n0 = tk["ports"][j - 1]
                    H += mp * (hmix[n0] if mp > 0 else ht[ti])
            for j, i in tk["loss"]:
                f = self.flows[i]
                mi = m[i]
                Ap = math.pi / 4 * f["D"] ** 2
                H += mi * (hmix[f["a0"]] if mi > 0 else ht[ti]) + mi * 0.5 * (mi / (tk["rho"] * Ap)) ** 2
            Qt[ti] = Qin.get(tk["hn"], 0.0) if tk.get("hn") is not None else 0.0
            dUt[ti] = H + Qt[ti]
        dUp = np.zeros(len(self.pvols))
        for jv, pv in enumerate(self.pvols):
            i = pv["i"]
            f = self.flows[i]
            mi = m[i]
            up = f["a0"] if mi > 0 else f["b0"]
            dUp[jv] = abs(mi) * (hmix[up] - hp[jv]) + W[i]
        dUx = np.zeros(len(self.xvols))
        Qx = np.zeros(len(self.xvols))
        for xi, xv in enumerate(self.xvols):
            if xv["kind"] == "vol":
                H = sum(mp * (hmix[xv["ports"][j - 1]] if mp > 0 else hx[xi])
                        for (x_, j), mp in vq.items() if x_ == xi)
                Qx[xi] = Qin.get(xv["hn"], 0.0) if xv["hn"] is not None else 0.0
                dUx[xi] = H + Qx[xi]
        pdet = []
        for pi_, dp in enumerate(self.dpipes):
            fl, n, x0 = pfl[pi_], dp["n"], dp["x0"]
            h = hx[x0:x0 + n]
            mf = fl["mf"]
            H = [mf[0] * (hmix[dp["a0"]] if mf[0] > 0 else h[0])]
            H += [mf[i] * (h[i - 1] if mf[i] >= 0 else h[i]) for i in range(1, n)]
            H += [mf[n] * (h[n - 1] if mf[n] > 0 else hmix[dp["b0"]])]
            Hb = [H[i] - H[i + 1] for i in range(n)]
            Wb = self._pipe_wb(dp, fl)
            Qb = [Qin.get(("dpvol", dp["e"], i), 0.0) for i in range(n)]
            for i in range(n):
                Qx[x0 + i] = Qb[i]
                dUx[x0 + i] = Hb[i] + Wb[i] + Qb[i]
            pdet.append({"H": H, "Hb": Hb, "Wb": Wb, "Qb": Qb, "alphas": alphas[pi_]})
        dTc = np.array([Qin.get(c["n"], 0.0) / c["C"] for c in self.caps])
        return dUt, dUp, dUx, dTc, {"hmix": hmix, "Tt": Tt, "Tp": Tp, "Tx": Tx, "hx": hx, "Tnet": Tnet, "Qin": Qin,
                                    "Qt": Qt, "Qx": Qx, "W": W, "pipes": pdet, "hout": hout}

    def _h_outflow(self, dis, hmix, m, W, ht, hp, hx) -> dict:
        """{Modelica path of a port's h_outflow: value} as MSL defines them (FL12, for the energy check of a
        reference run): a storage's ports carry its medium.h; a static part's port_a carries inStream at port_b
        and back (PartialTwoPortTransport); a pump without a volume carries medium.h = inStream(inlet) + W/|m|
        (its steady energy balance); pressure sensors Medium.h_default. inStream at a port (Modelica's stream
        connectors): the other ports' outflows at its connection point mixed by their flows; without any outflow
        there, the other port's h_outflow (two ports) or their mean."""
        net = self.net
        vals = {}
        for n, items in dis.items():
            vals[n] = [(own, w, h if up is None else hmix[up] + extra) for w, h, up, extra, own in items]
        H, rules, at = {}, [], {}            # rules: (path, own, kind, data) of the ports that depend on inStream

        def port(path, node, own, value=None, rule=None):
            at.setdefault(node, []).append((own, path))
            if rule is None:
                H[path] = value
            else:
                rules.append((path, own, rule))
        for e, j, n0 in self.fixed_ports:
            v = self.h_bnd[e] if e in self.h_bnd else ht[next(i for i, tk in enumerate(self.tanks) if tk["e"] == e)]
            port(f"{e}.ports[{j}].h_outflow", n0, (e, j), v)
        for _, _, e, orig in self.sources:
            for j, n0 in enumerate(orig, 1):
                port(f"{e}.ports[{j}].h_outflow", n0, (e, j), self.h_bnd[e])
        for xi, xv in enumerate(self.xvols):
            if xv["kind"] == "vol":
                for j, n0 in enumerate(xv["ports"], 1):
                    port(f"{xv['e']}.ports[{j}].h_outflow", n0, (xv["e"], j), hx[xi])
        for i, f in enumerate(self.flows):
            e, k = f["e"], f["k"]
            if k == "tank_port":
                port(f"{e}.ports[{f['j']}].h_outflow", f["a0"], (e, f["j"]), ht[f["ti"]])
            elif k == "dp_seg":
                continue
            elif "vol" in f:
                port(f"{e}.port_a.h_outflow", f["a0"], e, hp[f["vol"]])
                port(f"{e}.port_b.h_outflow", f["b0"], e, hp[f["vol"]])
            elif k == "fl_pump":
                port(f"{e}.port_a.h_outflow", f["a0"], e, rule=("pump", f, i))
                port(f"{e}.port_b.h_outflow", f["b0"], e, rule=("pump", f, i))
            else:
                port(f"{e}.port_a.h_outflow", f["a0"], e, rule=("pass", f["b0"]))
                port(f"{e}.port_b.h_outflow", f["b0"], e, rule=("pass", f["a0"]))
        for dp in self.dpipes:
            port(f"{dp['e']}.port_a.h_outflow", dp["a0"], dp["e"], hx[dp["x0"]])
            port(f"{dp['e']}.port_b.h_outflow", dp["b0"], dp["e"], hx[dp["x0"] + dp["n"] - 1])
        for e, d in net.elems.items():       # PartialAbsoluteSensor, PartialRelativeSensor: h_default (20 degC)
            if d["kind"] in ("fl_pressure", "fl_relative_pressure"):
                hd = _cp(d["params"])[0] * (293.15 - T0_MEDIUM)
                for pn in (("port",) if d["kind"] == "fl_pressure" else ("port_a", "port_b")):
                    port(f"{e}.{pn}.h_outflow", net.node_of[f"{e}.{pn}"], e, hd)

        def instream(n, own):
            its = [(w, hv) for o, w, hv in vals.get(n, ()) if o != own]
            ws = sum(w for w, _ in its)
            if ws > 0:
                return sum(w * hv for w, hv in its) / ws
            others = [H.get(pth) for o, pth in at.get(n, ()) if o != own]
            others = [v for v in others if v is not None]
            return sum(others) / len(others) if others else hmix.get(n, self.h_default)
        for _ in range(len(rules) + 1):       # a chain of parts without flow passes its ends' h along
            changed = False
            for path, own, rule in rules:
                if rule[0] == "pass":
                    v = instream(rule[1], own)
                else:
                    f, i = rule[1], rule[2]
                    mi = m[i]
                    v = (instream(f["a0"], own) + W[i] / mi if mi > 0 else
                         instream(f["b0"], own) + W[i] / -mi if mi < 0 else instream(f["a0"], own))
                if H.get(path) is None or abs(H[path] - v) > 1e-9 * max(1.0, abs(v)):
                    H[path], changed = v, True
            if not changed:
                break
        return H
    def simulate(self, t_eval, t0=None) -> dict:
        from scipy.integrate import solve_ivp
        t = np.asarray(t_eval, float)
        self._x = None
        nt, nv, nx, nc = len(self.tanks), len(self.pvols), len(self.xvols), len(self.caps)
        th = self.thermal
        h0 = np.array([tk["h0"] for tk in self.tanks])
        rho_a = np.array([tk["rho"] * tk["A"] for tk in self.tanks])
        U0 = np.array([tk["rho"] * tk["A"] * tk["h0"] * tk["cv"] * (tk["T_start"] - T0_MEDIUM) for tk in self.tanks])
        V0 = np.array([pv["m"] * pv["cv"] * (pv["T_start"] - T0_MEDIUM) for pv in self.pvols])
        X0 = np.array([xv["m"] * xv["cv"] * (xv["T_start"] - T0_MEDIUM) for xv in self.xvols])
        C0 = np.array([c["T0"] for c in self.caps])
        x0 = np.concatenate([h0] + ([U0, V0, X0, C0] if th else []))
        self._split = (nt, nv, nx)

        def split(x):
            o = 2 * nt + nv
            return x[:nt], x[nt:2 * nt], x[2 * nt:o], x[o:o + nx], x[o + nx:]

        def rhs(_, x):
            lv, Ut, Up, Ux, Tc = split(x)
            m, P = self._solve(lv)
            dl = self._tank_inflows(m, P) / rho_a if nt else np.zeros(0)
            if not th:
                return dl
            dUt, dUp, dUx, dTc, _ = self._energy(lv, Ut, Up, Ux, Tc, m, P)
            return np.concatenate([dl, dUt, dUp, dUx, dTc])
        ts = float(t[0] if t0 is None else t0)
        X = np.repeat(x0[:, None], t.size, axis=1)
        if x0.size and t[-1] > ts:
            sc = np.concatenate([np.full(nt, max(1.0, float(np.max(h0)) if nt else 1.0) * 1e-12)] +
                                ([np.maximum(np.abs(U0), 1.0) * 1e-12, np.maximum(np.abs(V0), 1.0) * 1e-12,
                                  np.maximum(np.abs(X0), 1.0) * 1e-12, np.full(nc, 1e-9)] if th else []))
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
            lv, Ut, Up, Ux, Tc = split(X[:, j])
            m, P = self._solve(lv)
            cols.append((m, P))
            ecols.append(self._energy(lv, Ut, Up, Ux, Tc, m, P, ports=True) if th else None)
        out = self._paths(t, H, cols)
        if th:
            out.update(self._thermal_paths(t, X, nt, nv, cols, ecols))
        return out

    def _thermal_paths(self, t, X, nt, nv, cols, ecols) -> dict:
        nx = len(self.xvols)
        D = 4             # ecols[k][D]: the details
        out, net = {}, self.net

        def ser(fn):
            return np.array([fn(k) for k in range(t.size)], float)
        if t.size:
            for path in ecols[0][D]["hout"]:
                out[path] = ser(lambda k, path=path: ecols[k][D]["hout"][path])
        for ti, tk in enumerate(self.tanks):
            e = tk["e"]
            out[f"{e}.medium.T"] = ser(lambda k, ti=ti: ecols[k][D]["Tt"][ti])
            out[f"{e}.U"] = X[nt + ti]
            # OpenTank: Wb_flow = 0 (singleState media), Qb_flow = the heat port's, ports_E_flow: m v^2/2 at ports
            # with port_diameter (portsData_height 0), else 0
            out[f"{e}.Qb_flow"] = ser(lambda k, ti=ti: ecols[k][D]["Qt"][ti])
            out[f"{e}.Wb_flow"] = np.zeros(t.size)
            if tk.get("hn") is not None:
                out[f"{e}.heatPort.T"] = out[f"{e}.medium.T"]
                out[f"{e}.heatPort.Q_flow"] = out[f"{e}.Qb_flow"]
            loss = dict(tk["loss"])
            for j in range(1, len(tk["ports"]) + 1):
                if j in loss:
                    f = self.flows[loss[j]]
                    Ap = math.pi / 4 * f["D"] ** 2
                    mm = np.array([c[0][loss[j]] for c in cols], float)
                    out[f"{e}.ports_E_flow[{j}]"] = mm * 0.5 * (mm / (tk["rho"] * Ap)) ** 2
                else:
                    out[f"{e}.ports_E_flow[{j}]"] = np.zeros(t.size)
        pv_of = {pv["i"]: jv for jv, pv in enumerate(self.pvols)}
        for i, f in enumerate(self.flows):          # pumps (PartialPump: Wb_flow = W_total, no heat transfer)
            if f["k"] == "fl_pump":
                e = f["e"]
                out[f"{e}.U"] = X[2 * nt + pv_of[i]] if i in pv_of else np.zeros(t.size)
                out[f"{e}.Qb_flow"] = np.zeros(t.size)
                out[f"{e}.Wb_flow"] = ser(lambda k, i=i: ecols[k][D]["W"][i])
        for jv, pv in enumerate(self.pvols):
            e = self.flows[pv["i"]]["e"]
            out[f"{e}.medium.T"] = ser(lambda k, jv=jv: ecols[k][D]["Tp"][jv])
        o = 2 * nt + nv
        for xi, xv in enumerate(self.xvols):
            if xv["kind"] != "vol":
                continue
            e = xv["e"]
            out[f"{e}.medium.T"] = ser(lambda k, xi=xi: ecols[k][D]["Tx"][xi])
            out[f"{e}.U"] = X[o + xi]
            out[f"{e}.Qb_flow"] = ser(lambda k, xi=xi: ecols[k][D]["Qx"][xi])
            out[f"{e}.Wb_flow"] = np.zeros(t.size)
            for j in range(1, len(xv["ports"]) + 1):           # no portsData: no kinetic energy at the ports
                out[f"{e}.ports_E_flow[{j}]"] = np.zeros(t.size)
            if xv["hn"] is not None:
                out[f"{e}.heatPort.T"] = out[f"{e}.medium.T"]
                out[f"{e}.heatPort.Q_flow"] = out[f"{e}.Qb_flow"]
        for pi_, dp in enumerate(self.dpipes):
            e, n, x0 = dp["e"], dp["n"], dp["x0"]
            for i in range(n):
                k1 = i + 1
                out[f"{e}.mediums[{k1}].T"] = ser(lambda k, j=x0 + i: ecols[k][D]["Tx"][j])
                out[f"{e}.mediums[{k1}].h"] = ser(lambda k, j=x0 + i: ecols[k][D]["hx"][j])
                out[f"{e}.Us[{k1}]"] = X[o + x0 + i]
                for nm, key in (("Hb_flows", "Hb"), ("Wb_flows", "Wb"), ("Qb_flows", "Qb")):
                    out[f"{e}.{nm}[{k1}]"] = ser(lambda k, pi_=pi_, i=i, key=key: ecols[k][D]["pipes"][pi_][key][i])
            for i in range(n + 1):
                out[f"{e}.H_flows[{i + 1}]"] = ser(lambda k, pi_=pi_, i=i: ecols[k][D]["pipes"][pi_]["H"][i])
            if dp["heat"] and dp["p"].get("heat_transfer") in ("constant", "local"):
                wall = self.tnode.get(f"{e}.heat")
                Tw = ser(lambda k, wall=wall: ecols[k][D]["Tnet"].get(wall, np.nan))
                Qtot = sum(out[f"{e}.Qb_flows[{i + 1}]"] for i in range(n))
                for i in range(n):
                    out[f"{e}.heatTransfer.Q_flows[{i + 1}]"] = out[f"{e}.Qb_flows[{i + 1}]"]
                    out[f"{e}.heatPorts[{i + 1}].Q_flow"] = out[f"{e}.Qb_flows[{i + 1}]"]
                    out[f"{e}.heatPorts[{i + 1}].T"] = Tw
                    out[f"{e}.heatTransfer.alphas[{i + 1}]"] = ser(
                        lambda k, pi_=pi_, i=i: ecols[k][D]["pipes"][pi_]["alphas"][i])
                out[f"omct_{e}_coll.port_b.Q_flow"] = Qtot
                out[f"omct_{e}_coll.port_b.T"] = Tw
        for i, f in enumerate(self.flows):
            if f["k"] == "fl_temperature":
                cp = f["cp"]

                def Ts(k, f=f, i=i, cp=cp):
                    hm = ecols[k][D]["hmix"]
                    Ta, Tb = T0_MEDIUM + hm[f["a0"]] / cp, T0_MEDIUM + hm[f["b0"]] / cp
                    return reg_step(cols[k][0][i], Ta, Tb, self.m_small)
                out[f"{f['e']}.T"] = ser(Ts)
        # thermal parts
        def Tn(n):
            return ser(lambda k: ecols[k][D]["Tnet"][n])
        for ci, c in enumerate(self.caps):
            e = c["e"]
            out[f"{e}.T"] = X[2 * nt + nv + nx + ci]
            out[f"{e}.der_T"] = ser(lambda k, c=c: ecols[k][D]["Qin"].get(c["n"], 0.0) / c["C"])
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
                out[f"{e}.port.Q_flow"] = ser(lambda k, n=n: ecols[k][D]["Qin"].get(n, 0.0))
        for e, n in self.tsens:
            out[f"{e}.T"] = Tn(n)
            out[f"{e}.port.T"] = out[f"{e}.T"]
        return out

    def steady(self, t1=None) -> dict:
        """Without tanks the hydraulics are algebraic: the state at any time. With tanks: RefError (no steady
        state is implied; the caller runs to the end instead)."""
        from .ref import RefError
        if self.tanks or (self.thermal and (self.pvols or self.xvols or self.caps)):
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
            if k == "dp_seg":
                j = f["seg"] + 1
                out.update({f"{e}.flowModel.m_flows[{j}]": mm, f"{e}.flowModel.dps_fg[{j}]": pa - pb})
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
        for dp in self.dpipes:
            e, n = dp["e"], dp["n"]
            fls = [self._pipe_flow(dp, m, P) for m, P in cols]
            mf = np.array([f_["mf"] for f_ in fls], float)
            out.update({f"{e}.port_a.m_flow": mf[:, 0], f"{e}.port_b.m_flow": -mf[:, n],
                        f"{e}.port_a.p": np.array([f_["pa"] for f_ in fls]),
                        f"{e}.port_b.p": np.array([f_["pb"] for f_ in fls])})
            for i in range(n + 1):
                out[f"{e}.m_flows[{i + 1}]"] = mf[:, i]
            for i in range(n):
                out[f"{e}.mediums[{i + 1}].p"] = np.array([f_["pm"][i] for f_ in fls])
                out[f"{e}.vs[{i + 1}]"] = np.array([f_["vs"][i] for f_ in fls])
                out[f"{e}.ms[{i + 1}]"] = np.full(t.size, self.xvols[dp["x0"] + i]["m"])
        for xv in self.xvols:
            if xv["kind"] == "vol":
                e = xv["e"]
                n = self.find(f"{e}.port[1]") if xv["ports"] else None
                if n is not None:
                    out[f"{e}.medium.p"] = ser(lambda m, P, n=n: P[n])
                out[f"{e}.m"] = np.full(t.size, xv["m"])
                vqs = [self._vol_flows(m) for m, _ in cols]
                xi = self.xvols.index(xv)
                for j in range(1, len(xv["ports"]) + 1):
                    out[f"{e}.ports[{j}].m_flow"] = np.array([v[(xi, j)] for v in vqs], float)
                    if n is not None:
                        out[f"{e}.ports[{j}].p"] = out[f"{e}.medium.p"]
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
                out[f"{e}.port.m_flow"] = np.zeros(t.size)
            if d["kind"] == "fl_relative_pressure":
                na, nb = self.find(f"{e}.port_a"), self.find(f"{e}.port_b")
                out[f"{e}.p_rel"] = ser(lambda m, P, na=na, nb=nb: P[na] - P[nb])
                out[f"{e}.port_a.m_flow"] = out[f"{e}.port_b.m_flow"] = np.zeros(t.size)
        return out
