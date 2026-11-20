"""fluidref.py — the reference implementation for Modelica.Fluid liquid networks (V5 FL4).
Version: 2026.11.19.27

An independent solver of the same equations MSL 4.0.0 writes for constant-density liquids, ported from the MSL
sources (tag v4.0.0) rather than reused: what OpenModelica must reproduce. Covered (covered(net)):

  media       water (ConstantPropertyLiquidWater), custom liquids {rho, cp, eta, lambda}, and (FL14) glycol47 and
              essotherm650 (Incompressible.TableBased: density, cp, conductivity and log viscosity quadratic in T;
              flows then depend on the temperatures, volumes' masses on their T); (FL15) the ideal gases air
              (DryAirNasa) and simple_air: a gas volume holds mass and energy and sets its node's pressure
              (p = m R T / V), or follows a boundary's pressure where it sits on one; fl_valve_gas
              (ValveCompressible, choked flow); densities at each port's p and T
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
  not         anything else outside Modelica.Fluid, inputs that change the flows (p_in, m_flow_in, opening, w_in);
              T-dependent liquids and gases without temperatures (T_in, ideal pipe heat transfer, dynamics other
              than fixed_initial); two gas volumes on one connection point; gases in pumps or tanks

Hydraulics are algebraic (momentum steady, MSL's default): at each instant, node pressures and component flows from
a Newton solve of the component relations and the node mass balances (a node holding a volume takes its mass change
V d'(T) dT/dt); boundaries and tank ports fix pressures (p_ambient + rho g level: OpenTank without portsData); the
states are the tanks' masses and energies, the volumes' energies and the capacitors' temperatures (level_start,
T_start fixed), integrated with Radau (rtol 1e-10). With constant-density liquids the temperatures follow the flows;
with T-dependent ones each instant repeats hydraulics -> mixing and energy -> the port states (densities,
viscosities at inStream of each port, the pumps' medium, the volumes' T) and the volumes' mass changes until they
agree to 1e-11 K (FL14).

MSL relations ported (Modelica.Fluid 4.0.0, tag v4.0.0; planning.md V5 FL4):
  static     StaticPipe: port_b.h_outflow = inStream(port_a.h_outflow) - g height_ab and back (the potential energy)
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
           "fl_volume_flow_sensor", "fl_relative_pressure", "fl_dynamic_pipe", "fl_volume", "fl_valve_gas"}
TWO_PORT = {"fl_pipe", "fl_resistance", "fl_orifice", "fl_valve", "fl_valve_gas", "fl_valve_linear", "fl_pump",
            "fl_temperature",
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
def m_flow_of_dp_fric(dp, rho_a, mu_a, L, D, A, Re1, Re2, Delta, rho_b=None, mu_b=None):
    """Detailed.Internal.m_flow_of_dp_fric: (m_flow, dm_flow/ddp). Hagen-Poiseuille below Re1, Colebrook-White
    above, a cubic Hermite spline in log-log space (lambda2 = lambda Re^2, Re) between Re1 and Re2; the upstream
    density and viscosity (port_a's for dp >= 0, port_b's else)."""
    rho, mu = (rho_a, mu_a) if dp >= 0 or rho_b is None else (rho_b, mu_b)
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


def pipe_detailed(dp, rho, mu, L, D, rough, dp_small, g_h, rho_b=None, mu_b=None):
    """Detailed.massFlowRate_dp_staticHead (Re_turbulent 4000, crossArea pi D^2/4): the static head and the
    friction with port_a's density and viscosity for flow a -> b and port_b's for b -> a (rho_b, mu_b: the same as
    port_a's when not given), regFun3 between dp_b = min(heads) - dp_small and dp_a = max(heads) + dp_small through
    zero flow at their mean."""
    rho_b = rho if rho_b is None else rho_b
    mu_b = mu if mu_b is None else mu_b
    Delta = rough / D
    Re1 = min((745 * math.exp(1 if Delta <= 0.0065 else 0.0065 / Delta)) ** 0.97, RE_TURBULENT)
    Re2 = RE_TURBULENT
    dga, dgb = g_h * rho, g_h * rho_b
    dp_a, dp_b = max(dga, dgb) + dp_small, min(dga, dgb) - dp_small
    args = (rho, mu, L, D, math.pi * D * D / 4, Re1, Re2, Delta, rho_b, mu_b)
    if dp >= dp_a:
        return m_flow_of_dp_fric(dp - dga, *args)[0]
    if dp <= dp_b:
        return m_flow_of_dp_fric(dp - dgb, *args)[0]
    ma, da = m_flow_of_dp_fric(dp_a - dga, *args)
    mb, db = m_flow_of_dp_fric(dp_b - dgb, *args)
    dz0 = (dga + dgb) / 2
    _, dz = reg_fun3(dz0, dp_b, dp_a, mb, ma, db, da)
    if dp > dz0:
        return reg_fun3(dp, dz0, dp_a, 0.0, ma, dz, da)[0]
    return reg_fun3(dp, dp_b, dz0, mb, 0.0, db, dz)[0]


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
        mv = p.get("medium") or "water"
        if C.fmedium(mv)["class"] != "liquid_const" and not (isinstance(mv, str) and mv in
                                                             (*TABLES, "air", "simple_air")):
            return False, f"{c}: medium {C.fmedium(mv)['name']} (not in the reference)"
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


# ---- media ----------------------------------------------------------------------------------------------------------
P_REF_TABLE = 1.013e5        # TableBased reference_p (u = h - reference_p / d: singleState)
T_DEFAULT, T_REFERENCE = 293.15, 298.15    # PartialMedium T_default (rho_nominal, h_default), reference_T


class ConstLiquid:
    """Modelica.Media.Water.ConstantPropertyLiquidWater (or omct's custom liquid built on it): every property
    constant, h = cp (T - T0), u = cv (T - T0)."""
    var = False

    def __init__(self, rho, cp, cv, eta, lam):
        self.rho, self.cp_, self.cv_, self.eta, self.lam_ = rho, cp, cv, eta, lam
        self.d_nom = self.d_ref = rho
        self.h_default = self.h(T_DEFAULT)

    gas = False

    def d(self, T, p=None):
        return self.rho

    def dd(self, T, p=None):
        return 0.0

    def cp(self, T):
        return self.cp_

    def h(self, T):
        return self.cp_ * (T - T0_MEDIUM)

    def T_h(self, h):
        return T0_MEDIUM + h / self.cp_

    def u(self, T):
        return self.cv_ * (T - T0_MEDIUM)

    def du(self, T):
        return self.cv_

    def mu(self, T):
        return self.eta

    def lam(self, T):
        return self.lam_


# Modelica.Media.Incompressible.Examples (MSL 4.0.0): tables in degC (TinK false), npol 2 for every property
TABLES = {
    "glycol47": {"T_min": 243.15, "T_max": 373.15,
                 "rho": [[-30, 1066], [-20, 1062], [-10, 1058], [0, 1054], [20, 1044], [40, 1030], [60, 1015],
                         [80, 999], [100, 984]],
                 "cp": [[-30, 3450], [-20, 3490], [-10, 3520], [0, 3560], [20, 3620], [40, 3690], [60, 3760],
                        [80, 3820], [100, 3890]],
                 "lam": [[-30, 0.397], [-20, 0.396], [-10, 0.395], [0, 0.395], [20, 0.394], [40, 0.393], [60, 0.392],
                         [80, 0.391], [100, 0.390]],
                 "eta": [[-30, 0.160], [-20, 0.0743], [-10, 0.0317], [0, 0.0190], [20, 0.00626], [40, 0.00299],
                         [60, 0.00162], [80, 0.00110], [100, 0.00081]]},
    "essotherm650": {"T_min": 273.15, "T_max": 593.15,
                     "rho": [[0, 909], [20, 897], [40, 884], [60, 871], [80, 859], [100, 846], [150, 813], [200, 781],
                             [250, 748], [300, 715], [320, 702]],
                     "cp": [[0, 1770], [20, 1850], [40, 1920], [60, 1990], [80, 2060], [100, 2130], [150, 2310],
                            [200, 2490], [250, 2670], [300, 2850], [320, 2920]],
                     "lam": [[0, 0.1302], [20, 0.1288], [40, 0.1274], [60, 0.1260], [80, 0.1246], [100, 0.1232],
                             [150, 0.1197], [200, 0.1163], [250, 0.1128], [300, 0.1093], [320, 0.1079]],
                     "eta": [[0, 14370], [20, 1917], [40, 424], [60, 134], [80, 54.5], [100, 26.64], [150, 7.47],
                             [200, 3.22], [250, 1.76], [300, 1.10], [320, 0.94]]},
}


def _horner(c, x):
    y = 0.0
    for a in c:
        y = y * x + a
    return y


class TableLiquid:
    """Modelica.Media.Incompressible.TableBased (MSL 4.0.0, enthalpyOfT true, singleState true): least-squares
    polynomials of degree 2 (Polynomials.fitting) of rho, cp, lambda over T in degC and of log(eta) over 1/T [K];
    h = h0 + integral of cp from T0 (0 degC), u = h - reference_p / d, T from h by inverting h(T)."""
    var = True

    def __init__(self, name):
        t = TABLES[name]
        self.name, self.T_min, self.T_max = name, t["T_min"], t["T_max"]

        def fit(rows, x=lambda v: v, y=lambda v: v):
            a = np.array(rows, float)
            return np.polyfit(x(a[:, 0]), y(a[:, 1]), 2)
        self.p_rho, self.p_cp, self.p_lam = fit(t["rho"]), fit(t["cp"]), fit(t["lam"])
        self.p_eta = fit(t["eta"], x=lambda c: 1.0 / (c + 273.15), y=np.log)
        self.p_drho, self.p_h = np.polyder(self.p_rho), np.polyint(self.p_cp)
        self.p_rho, self.p_cp, self.p_lam, self.p_eta, self.p_drho, self.p_h = (
            [float(v) for v in c] for c in (self.p_rho, self.p_cp, self.p_lam, self.p_eta, self.p_drho, self.p_h))
        self.d_nom, self.d_ref = self.d(T_DEFAULT), self.d(T_REFERENCE)
        self.h_default = self.h(T_DEFAULT)

    gas = False

    def d(self, T, p=None):
        return _horner(self.p_rho, T - 273.15)

    def dd(self, T, p=None):
        return _horner(self.p_drho, T - 273.15)

    def cp(self, T):
        return _horner(self.p_cp, T - 273.15)

    def h(self, T):
        return _horner(self.p_h, T - 273.15)

    def T_h(self, h):
        T = 293.15 + (h - self.h(293.15)) / self.cp(293.15)
        for _ in range(60):
            dT = (self.h(T) - h) / self.cp(T)
            T -= dT
            if abs(dT) < 1e-13 * max(1.0, T):
                break
        return T

    def u(self, T):
        return self.h(T) - P_REF_TABLE / self.d(T)

    def du(self, T):
        d = self.d(T)
        return self.cp(T) + P_REF_TABLE * self.dd(T) / (d * d)

    def mu(self, T):
        return math.exp(_horner(self.p_eta, 1.0 / T))

    def lam(self, T):
        return _horner(self.p_lam, T - 273.15)


R_UNIVERSAL = 8.31446261815324          # Modelica.Constants.R
P_DEFAULT, P_REFERENCE = 101325.0, 101325.0


class IdealGas:
    """Ideal gases (FL15): d = p / (R T), u = h - R T (both media of MSL 4.0.0 omct offers):
    simple_air: Air.SimpleAir (PartialSimpleIdealGasMedium: cp 1005.45, MM 0.0289651159, eta 1.82e-5, lambda 0.026,
                h = cp (T - T0) with T0 = reference_T 298.15);
    air: Air.DryAirNasa (SingleGasNasa with SingleGasesData.Air: NASA-7 polynomials, excludeEnthalpyOfFormation,
         ZeroAt0K: h = h_T - Hf + H0; viscosity and conductivity polynomials in degC, evaluateWithRange -150..1000
         degC, linear outside)."""
    var, gas = True, True
    NASA_AIR = {"MM": 0.0289651159, "Hf": -4333.833858403446, "H0": 298609.6803431054, "Tlimit": 1000.0,
                "alow": [10099.5016, -196.827561, 5.00915511, -0.00576101373, 1.06685993e-005, -7.94029797e-009,
                         2.18523191e-012], "blow": [-176.796731, -3.921504225],
                "ahigh": [241521.443, -1257.8746, 5.14455867, -0.000213854179, 7.06522784e-008, -1.07148349e-011,
                          6.57780015e-016], "bhigh": [6462.26319, -8.147411905], "R_s": 287.0512249529787}
    ETA = [9.7391102886305869E-15, -3.1353724870333906E-11, 4.3004876595642225E-08, -3.8228016291758240E-05,
           5.0427874367180762E-02, 1.7239260139242528E+01]
    LAM = [6.5691470817717812E-15, -3.4025961923050509E-11, 5.3279284846303157E-08, -4.5340839289219472E-05,
           7.6129675309037664E-02, 2.4169481088097051E+01]

    def __init__(self, name):
        self.name = name
        if name == "simple_air":
            self.R = R_UNIVERSAL / 0.0289651159
            self.cp_c, self.T0 = 1005.45, 298.15
        else:
            self.R = self.NASA_AIR["R_s"]
        self.d_nom = self.d(T_DEFAULT, P_DEFAULT)
        self.d_ref = self.d(T_REFERENCE, P_REFERENCE)
        self.h_default = self.h(T_DEFAULT)

    def d(self, T, p=None):
        return p / (self.R * T)

    def dd(self, T, p=None):
        return -p / (self.R * T * T)

    def cp(self, T):
        if self.name == "simple_air":
            return self.cp_c
        D = self.NASA_AIR
        a = D["alow"] if T < D["Tlimit"] else D["ahigh"]
        return D["R_s"] * (1 / (T * T) * (a[0] + T * (a[1] + T * (a[2] + T * (a[3] + T * (a[4] + T * (a[5] +
                                                                                                    a[6] * T)))))))

    def h(self, T):
        if self.name == "simple_air":
            return self.cp_c * (T - self.T0)
        D = self.NASA_AIR
        a, b = (D["alow"], D["blow"]) if T < D["Tlimit"] else (D["ahigh"], D["bhigh"])
        return D["R_s"] * ((-a[0] + T * (b[0] + a[1] * math.log(T) + T * (a[2] + T * (0.5 * a[3] + T * (
            1 / 3 * a[4] + T * (0.25 * a[5] + 0.2 * a[6] * T)))))) / T) - D["Hf"] + D["H0"]

    def T_h(self, h):
        if self.name == "simple_air":
            return self.T0 + h / self.cp_c
        T = 300.0 + (h - self.h(300.0)) / self.cp(300.0)
        for _ in range(60):
            dT = (self.h(T) - h) / self.cp(T)
            T -= dT
            if abs(dT) < 1e-13 * max(1.0, T):
                break
        return T

    def u(self, T):
        return self.h(T) - self.R * T

    def du(self, T):
        return self.cp(T) - self.R

    @staticmethod
    def _range(c, x):                   # Polynomials.evaluateWithRange over -150..1000 degC
        lo, hi = 123.15 - 273.15, 1273.15 - 273.15

        def ev(u):
            return _horner(c, u)

        def der(u):
            n = len(c) - 1
            return sum(c[k] * (n - k) * u ** (n - k - 1) for k in range(n))
        if x < lo:
            return ev(lo) - der(lo) * (lo - x)
        if x > hi:
            return ev(hi) + der(hi) * (x - hi)
        return ev(x)

    def mu(self, T):
        return 1.82e-5 if self.name == "simple_air" else 1e-6 * self._range(self.ETA, T - 273.15)

    def lam(self, T):
        return 0.026 if self.name == "simple_air" else 1e-3 * self._range(self.LAM, T - 273.15)


_MEDIA = {}


def medium_of(p):
    """The medium object of an element's params (water, a custom constant liquid {rho, cp, eta, lambda}, glycol47,
    essotherm650); FluidRefError for the others (gases)."""
    m = p.get("medium") or "water"
    key = repr(m)
    if key not in _MEDIA:
        if isinstance(m, dict):
            cp = float(m.get("cp", 4184.0))
            _MEDIA[key] = ConstLiquid(float(m.get("rho", 995.586)), cp, cp, float(m.get("eta", 1.0e-3)),
                                      float(m.get("lambda", 0.598)))
        elif m == "water":
            _MEDIA[key] = ConstLiquid(995.586, 4184.0, 4184.0, 1.0e-3, 0.598)
        elif m in TABLES:
            _MEDIA[key] = TableLiquid(m)
        elif m in ("air", "simple_air"):
            _MEDIA[key] = IdealGas(m)
        else:
            raise FluidRefError(f"medium {m}: not in the Fluid reference")
    return _MEDIA[key]


def T_of_U(med, U, V=None, m=None, p=None):
    """Temperature of a storage from its internal energy: a fixed volume V (m = V d(T, p)) or a given mass m."""
    if not med.var:
        mm = m if m is not None else V * med.rho
        return T0_MEDIUM + U / (mm * med.cv_)
    T = 300.0
    for _ in range(100):
        if V is not None:
            f = V * med.d(T, p) * med.u(T) - U
            df = V * (med.dd(T, p) * med.u(T) + med.d(T, p) * med.du(T))
        else:
            f = m * med.u(T) - U
            df = m * med.du(T)
        dT = f / df
        T -= dT
        if abs(dT) < 1e-12 * max(1.0, T):
            break
    return T


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




# ---- the network ----------------------------------------------------------------------------------------------------
class FluidRef:
    """The liquid network of a covered study (covered(net)): simulate(t) -> {Modelica path: values}. Drop-in for
    ref.Ref where the runner and the fit use it (exact, not_exact, events, breaks, simulate, steady).

    At each instant (FL14): the hydraulics (flows, pressures) from the component relations and the node balances,
    where a node holding a volume takes its mass change (V d'(T) dT/dt); then the mixing at the connection points,
    the storages' energy balances, the port temperatures the relations use (densities, viscosities) and the volumes'
    mass changes; the two repeated until they agree (one pass for constant-density liquids)."""

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
        self.T_amb = float(sysp.get("T_ambient", 293.15))
        self.p_start = self.p_amb                       # System.p_start = p_ambient
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
        self.xvols, self.dpipes, self.pvols = [], [], []     # closed volumes and pipe volumes; pump volumes

        def node(ep):
            n = self.find(ep)
            self.nodes.setdefault(n, len(self.nodes))
            return n

        def T_start(p):
            return float(p["T_start"]) if p.get("T_start") is not None else self.T_amb
        for e, d in sorted(net.elems.items()):
            k, p = d["kind"], d["params"]
            nports = d.get("multi_n", {}).get("port", 0)
            if k == "fl_boundary":
                for j in range(1, nports + 1):
                    self._fix(node(f"{e}.port[{j}]"), ("p", float(p["p"])))
                    self.fixed_ports.append((e, j, net.node_of[f"{e}.port[{j}]"]))
            elif k == "fl_tank":
                med = medium_of(p)
                ti = len(self.tanks)
                ports = [node(f"{e}.port[{j}]") for j in range(1, nports + 1)]
                tk = {"e": e, "A": float(p["crossArea"]), "med": med, "nodes": ports, "loss": [],
                      "h0": max(float(p["level_start"]), EPS), "T_start": T_start(p),
                      "ports": [net.node_of[f"{e}.port[{j}]"] for j in range(1, nports + 1)]}
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
                    self.flows.append({"e": e, "k": "tank_port", "p": p, "med": med, "ti": ti, "own": (e, j),
                                       "D": float(D), "a": n_, "b": st, "a0": net.node_of[f"{e}.port[{j}]"],
                                       "b0": st, "j": j})
            elif k == "fl_mass_flow":
                if nports:
                    self.sources.append((node(f"{e}.port[1]"), float(p["m_flow"]), e,
                                         [net.node_of[f"{e}.port[{j}]"] for j in range(1, nports + 1)]))
            elif k == "fl_volume":
                if nports:
                    node(f"{e}.port[1]")
                self.xvols.append({"kind": "vol", "e": e, "V": float(p["V"]), "med": medium_of(p),
                                   "T_start": T_start(p), "node": self.find(f"{e}.port[1]") if nports else None,
                                   "p_start": float(p["p_start"]) if p.get("p_start") is not None else self.p_start,
                                   "ports": [net.node_of[f"{e}.port[{j}]"] for j in range(1, nports + 1)]})
            elif k == "fl_dynamic_pipe":
                self._dpipe(e, d, node, T_start(p))
            elif k in TWO_PORT:
                f = {"e": e, "k": k, "p": p, "med": medium_of(p), "own": e,
                     "a": node(f"{e}.port_a"), "b": node(f"{e}.port_b"),
                     "a0": net.node_of[f"{e}.port_a"], "b0": net.node_of[f"{e}.port_b"]}
                if k == "fl_pump" and float(p.get("V") or 0) > 0:
                    # PartialPump with a volume: port_b.p = medium.p, its mass at port_b's node
                    f["vol"] = len(self.pvols)
                    self.pvols.append({"i": len(self.flows), "V": float(p["V"]), "med": f["med"],
                                       "T_start": T_start(p), "node": f["b"]})
                self.flows.append(f)
            elif k == "fl_pressure":
                node(f"{e}.port")
            elif k == "fl_relative_pressure":
                node(f"{e}.port_a"), node(f"{e}.port_b")
        # a gas volume sets its node's pressure from its mass and energy (states m, U; FL15), unless a boundary
        # already does (a pipe's end volume at a boundary: m follows p and T, as a T-dependent liquid's volume)
        self.gstate = []
        for xi, xv in enumerate(self.xvols):
            xv["mode"] = "follow"
            if not xv["med"].gas or xv["node"] is None:
                continue
            how = self.fixed.get(xv["node"])
            if how is None:
                xv["mode"] = "state"
                self._fix(xv["node"], ("x", xi))
                self.gstate.append(xi)
            elif how[0] != "p":
                raise FluidRefError(f"{net.elems[xv['e']]['comp']}: two gas volumes (or a tank) on one connection point "
                                    f"(not in the reference)")
        for f in self.flows:
            if f["med"].gas and f["k"] in ("fl_pump", "tank_port"):
                raise FluidRefError(f"{net.elems[f['e']]['comp']}: a gas in a pump or tank (not in the reference)")
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
        self.var = any(x["med"].var for x in self.flows + self.tanks + self.xvols + self.pvols)
        self._x = None
        # per instant: the port temperatures the relations use, the volumes' mass changes, pumps' medium T
        self._pT = [[self.T_amb, self.T_amb] for _ in self.flows]
        self._Tpump = {i: self.T_amb for i, f in enumerate(self.flows) if f["k"] == "fl_pump"}
        self._mdp = np.zeros(len(self.pvols))
        self._mdx = np.zeros(len(self.xvols))
        self._px = np.array([xv["p_start"] if "p_start" in xv else self.p_amb for xv in self.xvols], float)
        self._thermal_setup(sysp)

    def _dpipe(self, e, d, node, T0):
        """A DynamicPipe (PartialTwoPortFlow, momentum steady): its volumes' hydraulic nodes (port_a's node is
        mediums[1]'s; av_vb: port_b's is mediums[n]'s) and the flow model's segments between them."""
        net, p = self.net, d["params"]
        n, st = int(p["nNodes"]), p.get("_structure") or "av_vb"
        med = medium_of(p)
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
        x0 = len(self.xvols)
        segs = []
        for k, (u, v) in enumerate(ends):
            segs.append(len(self.flows))
            self.flows.append({"e": e, "k": "dp_seg", "p": p, "med": med, "a": u, "b": v, "own": e,
                               "a0": a0 if k == 0 else u, "b0": b0 if k == len(ends) - 1 else v,
                               "L": pl[k], "dh": dh[k], "nFM": len(ends), "pipe": pi_, "seg": k,
                               "xa": x0 + k, "xb": x0 + k + 1 if k + 1 < n else None})
        pa0 = float(p["p_a_start"]) if p.get("p_a_start") is not None else self.p_start
        pb0 = float(p["p_b_start"]) if p.get("p_b_start") is not None else pa0
        ps0 = np.linspace(pa0, pb0, n) if n > 1 else [pa0]     # PartialDistributedVolume ps_start
        for i in range(n):
            self.xvols.append({"kind": "pipe", "e": e, "pipe": pi_, "i": i, "V": A * li * npar, "med": med,
                               "T_start": T0, "node": vn[i], "p_start": float(ps0[i])})
        self.dpipes.append({"e": e, "n": n, "st": st, "vn": vn, "na": na, "nb": nb, "a0": a0, "b0": b0,
                            "segs": segs, "x0": x0, "med": med, "A": A, "D": D, "li": li, "hi": hi,
                            "npar": npar, "p": p, "heat": "heat" in (d.get("enabled") or ())})

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

    # ---- the storages' state ------------------------------------------------------------------------------------------
    def _set_state(self, mt, Ut, Up, Ux, Mg=()):
        """Temperatures, masses and levels of the storages from the states (tank masses and energies, the volumes'
        energies, gas volumes' masses: their pressure p = m R T / V); without temperatures (thermal off) the
        storages stay at their start temperatures."""
        th = self.thermal
        self._Tt = np.array([T_of_U(tk["med"], Ut[i], m=max(mt[i], EPS)) if th else tk["T_start"]
                             for i, tk in enumerate(self.tanks)])
        self._mt = np.asarray(mt, float)
        self._levels = np.array([self._mt[i] / (tk["med"].d(self._Tt[i]) * tk["A"]) for i, tk in enumerate(self.tanks)])
        self._Tp = np.array([T_of_U(pv["med"], Up[j], V=pv["V"]) if th else pv["T_start"]
                             for j, pv in enumerate(self.pvols)])
        Tx = []
        gi = {xi: k for k, xi in enumerate(self.gstate)}
        for j, xv in enumerate(self.xvols):
            if not th:
                Tx.append(xv["T_start"])
            elif j in gi:                                  # a gas volume with its own mass
                m = max(Mg[gi[j]], EPS)
                T = T_of_U(xv["med"], Ux[j], m=m)
                Tx.append(T)
                self._px[j] = m / xv["V"] * xv["med"].R * T
            else:
                pv = self._p_fixed(xv["node"]) if xv["med"].gas else None
                if xv["med"].gas:
                    self._px[j] = pv
                Tx.append(T_of_U(xv["med"], Ux[j], V=xv["V"], p=pv))
        self._Tx = np.array(Tx)
        for j, pv in enumerate(self.pvols):
            self._Tpump[pv["i"]] = self._Tp[j]
        for i, f in enumerate(self.flows):            # a pipe segment's states are its volumes'
            if f["k"] == "dp_seg":
                self._pT[i][0] = self._Tx[f["xa"]]
                if f["xb"] is not None:
                    self._pT[i][1] = self._Tx[f["xb"]]

    def _props(self, i, pa=None, pb=None):
        """(rho_a, rho_b, mu_a, mu_b) at a flow element's two states this instant (cached for one solve; a gas's
        densities at the port pressures pa, pb)."""
        med = self.flows[i]["med"]
        pc = getattr(self, "_pc", None)
        if pc is not None and not med.gas:
            return pc[i]
        Ta, Tb = self._pT[i]
        if med.gas:
            return med.d(Ta, pa), med.d(Tb, pb), med.mu(Ta), med.mu(Tb)
        return med.d(Ta), med.d(Tb), med.mu(Ta), med.mu(Tb)

    # ---- one instant: hydraulics ------------------------------------------------------------------------------------
    def _p_fixed(self, n, levels=None):
        pf = getattr(self, "_pfix", None)
        if pf and n in pf:
            return pf[n]
        how = self.fixed[n]
        if how[0] == "p":
            return how[1]
        if how[0] == "x":                            # a gas volume's own pressure
            return self._px[how[1]]
        ti = how[1]
        tk = self.tanks[ti]
        return max(0.0, self._levels[ti]) * self.g * tk["med"].d(self._Tt[ti]) + self.p_amb

    def _valve_Av(self, p, med):
        ch, rg = p["characteristic"], float(p.get("rangeability") or 20.0)
        if p.get("Kv") is not None:
            return float(p["Kv"]) * KV2AV
        if p.get("Cv") is not None:
            return float(p["Cv"]) * CV2AV
        if p.get("Av") is not None:
            return float(p["Av"])
        # CvTypes.OpPoint at opening_nominal 1, rho_nominal = density at p_default, T_default
        return float(p["m_flow_nominal"]) / (valve_char(ch, 1.0, rg) * math.sqrt(med.d_nom) *
                                             reg_root(float(p["dp_nominal"]), self.dp_small))

    def _relation(self, i, m, pa, pb):
        """Residual of one component: kg/s where MSL writes m_flow = f(dp), Pa/1e5 where it writes a pressure.
        Densities and viscosities at the component's states: inStream at each port (PartialTwoPortTransport), the
        volumes' for a pipe segment, the pump's medium."""
        f = self.flows[i]
        k, p, med = f["k"], f["p"], f["med"]
        dp = pa - pb
        if k == "tank_port":       # PartialLumpedVessel, use_portsData (VesselPortsData zeta_out 0.5, zeta_in 1.04)
            D = f["D"]
            Ap, Av = math.pi / 4 * D * D, float(p["crossArea"])
            ti = f["ti"]
            level = self._levels[ti]
            pen = reg_step(level - 0.1 * D, 1.0, 1e-3, 0.1 * D)
            rho_in, rho_t = med.d(self._pT[i][0]), med.d(self._Tt[ti])     # portInDensities, medium.d
            k_in = (1.04 - 1 + Ap ** 2 / Av ** 2) / rho_in * pen
            k_out = (0.5 + 1 - Ap ** 2 / Av ** 2) / rho_t / pen
            return (dp - 0.5 / Ap ** 2 * reg_square2(m, self.m_small, k_in, k_out)) / 1e5
        if k in ZERO_DP:
            return dp / 1e5
        if k == "fl_pump":
            rho = med.d(self._Tpump[i])                   # PartialPump: rho = medium.d
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
        rho_a, rho_b, mu_a, mu_b = self._props(i, pa, pb)
        if k == "fl_valve_gas":
            return m - self._valve_gas(p, med, pa, pb, rho_a, rho_b)
        if k in ("dp_seg", "fl_pipe"):
            if k == "dp_seg":            # one segment of a dynamic pipe's flow model (dps_fg = p_i - p_(i+1), steady)
                gh, nfm, L = self.g * f["dh"], f["nFM"], f["L"]
            else:
                gh, nfm, L = self.g * float(p["height_ab"]), 1, float(p["length"])
            if p["friction"] == "nominal_laminar":     # upwind density in the static head (useUpstreamScheme)
                return (dp - gh * (rho_a if m > 0 else rho_b) -
                        float(p["dp_nominal"]) / nfm / float(p["m_flow_nominal"]) * m) / 1e5
            return m - float(p["nParallel"]) * pipe_detailed(dp, rho_a, mu_a, L, float(p["diameter"]),
                                                             float(p["roughness"]), self.dp_small / nfm, gh,
                                                             rho_b, mu_b)
        if k == "fl_orifice":
            D, z = float(p["diameter"]), float(p["zeta"])
            kk = 8 * z / (math.pi ** 2 * D ** 4)              # lossConstant_D_zeta
            return m - reg_root2(dp, self.dp_small, rho_a / kk, rho_b / kk)
        if k == "fl_resistance":
            a, b = float(p["a"]), float(p.get("b") or 0.0)
            rho = reg_step(dp, rho_a, rho_b, self._resistance_dp_small(p, med))
            if b > 0:
                v = (math.copysign(1.0, dp) if dp != 0 else 0.0) * (-b / (2 * a) + math.sqrt((b / (2 * a)) ** 2
                                                                                          + abs(dp) / a))
            else:
                dmin = self._resistance_dp_small(p, med)
                v = smooth_power(dp / a, max(EPS, dmin) / a, 0.5)
            return m - rho * v
        if k == "fl_valve":
            ch, rg = p["characteristic"], float(p.get("rangeability") or 20.0)
            rc = valve_char(ch, float(p["opening"]), rg)   # opening_actual = opening (no filter: no leakage limit)
            Av = self._valve_Av(p, med)
            if p.get("checkValve"):
                return m - rc * Av * math.sqrt(rho_a) * reg_root2(dp, self.dp_small, 1.0, 0.0, True, 0.0)
            return m - rc * Av * reg_root2(dp, self.dp_small, rho_a, rho_b)
        if k == "fl_valve_linear":
            return m - float(p["opening"]) * float(p["m_flow_nominal"]) / float(p["dp_nominal"]) * dp
        raise FluidRefError(f"no relation for {k}")

    def _valve_gas(self, p, med, pa, pb, rho_a, rho_b):
        """ValveCompressible (IEC 534): x = dp / max(pa, pb), xs = x within +-Fxt (Fxt_full, xtCharacteristic one),
        Y = 1 - |xs| / (3 Fxt), m = rc Av Y regRoot2(p xs, dp_small, d_a, d_b); a check valve sqrt(d_a) regRoot(p xs)
        for xs >= 0; OpPoint: Av from m_flow_nominal at p_nominal, dp_nominal, rho_nominal (p_default, T_default)."""
        ch, rg = p["characteristic"], float(p.get("rangeability") or 20.0)
        rc = valve_char(ch, float(p["opening"]), rg)
        Fxt = float(p["Fxt_full"])
        if p.get("Kv") is not None:
            Av = float(p["Kv"]) * KV2AV
        elif p.get("Cv") is not None:
            Av = float(p["Cv"]) * CV2AV
        elif p.get("Av") is not None:
            Av = float(p["Av"])
        else:
            pn = float(p["p_nominal"])
            xn = float(p["dp_nominal"]) / pn
            xsn = Fxt if xn > Fxt else xn
            Yn = 1 - abs(xsn) / (3 * Fxt)
            Av = float(p["m_flow_nominal"]) / (valve_char(ch, 1.0, rg) * Yn * math.sqrt(med.d_nom) *
                                               reg_root(pn * xsn, self.dp_small))
        pp = max(pa, pb)
        xs = max(-Fxt, min((pa - pb) / pp, Fxt))
        Y = 1 - abs(xs) / (3 * Fxt)
        if p.get("checkValve"):
            return rc * Av * Y * math.sqrt(rho_a) * (reg_root(pp * xs, self.dp_small) if xs >= 0 else 0.0)
        return rc * Av * Y * reg_root2(pp * xs, self.dp_small, rho_a, rho_b)

    def _resistance_dp_small(self, p, med):
        """GenericResistances.VolumeFlowRate's dp_small: dp_volumeFlowRate_DP at m_flow_small with the density at
        reference_p, reference_T (dp_min 1e-10)."""
        a, b = float(p["a"]), float(p.get("b") or 0.0)
        vs = self.m_small / med.d_ref
        if b > 0:
            return a * vs * vs + b * vs
        return a * smooth_power(vs, (1e-10 / a) ** 0.5, 2)

    def _storage_at(self):
        """{hydraulic node: the mass its volumes take (kg/s)} this instant."""
        out = {}
        for j, pv in enumerate(self.pvols):
            out[pv["node"]] = out.get(pv["node"], 0.0) + self._mdp[j]
        for j, xv in enumerate(self.xvols):
            if xv["node"] is not None:
                out[xv["node"]] = out.get(xv["node"], 0.0) + self._mdx[j]
        return out

    def _solve(self, levels=None):
        from scipy.optimize import root
        nf, fi = len(self.flows), {n: i for i, n in enumerate(self.free)}
        store = self._storage_at()
        self._pc = None
        self._pc = [None if self.flows[i]["med"].gas else self._props(i) for i in range(nf)]   # fixed in a solve
        self._pfix = {}
        self._pfix = {n: self._p_fixed(n) for n in self.fixed}

        def pressures(x):
            return {n: (x[nf + fi[n]] * 1e5 if n in fi else self._p_fixed(n)) for n in self.nodes}

        def res(x):
            P = pressures(x)
            r = np.empty(nf + len(fi))
            for i, f in enumerate(self.flows):
                r[i] = self._relation(i, x[i], P[f["a"]], P[f["b"]])
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
            for n, md in store.items():
                if n in fi:
                    bal[fi[n]] -= md
            r[nf:] = bal
            return r
        if self._x is not None:
            x0 = self._x
        else:
            pf = [self._p_fixed(n) for n in self.fixed] or [self.p_amb]
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
        self._pc, self._pfix = None, None
        if best[1] > 1e-8:
            raise FluidRefError(f"the hydraulics did not converge (residual {best[1]:.3g})")
        self._x = best[0]
        self._s = best[0][:nf]
        return self._m_of(best[0][:nf]), pressures(best[0])

    def _q_orig(self, m, sources=True):
        """{network node: the flow the components put into it}: flow elements (-m at port_a, +m at port_b, a
        volume at a port taking its mass change) and, with sources, single-port mass-flow sources. A boundary's or
        tank's port takes it (Sum m_flow = 0)."""
        q = {}
        for i, f in enumerate(self.flows):
            q[f["a0"]] = q.get(f["a0"], 0.0) - m[i]
            q[f["b0"]] = q.get(f["b0"], 0.0) + m[i]
        for j, pv in enumerate(self.pvols):                     # port_b.m_flow = -(port_a.m_flow - dm/dt)
            b0 = self.flows[pv["i"]]["b0"]
            q[b0] -= self._mdp[j]
        for dp in self.dpipes:                                  # port_a.m_flow = m_flows[2] + dm1/dt (and port_b)
            q[dp["a0"]] -= self._mdx[dp["x0"]]
            if dp["st"] == "av_vb":
                q[dp["b0"]] -= self._mdx[dp["x0"] + dp["n"] - 1]
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

    def _tank_inflows(self, m, P=None):
        ff = self._fixed_flows(m)
        return np.array([sum(ff[(tk["e"], j)] for j in range(1, len(tk["ports"]) + 1) if (tk["e"], j) in ff) +
                         sum(m[i] for _, i in tk["loss"]) for tk in self.tanks])

    # ---- temperatures (FL9, FL14) -------------------------------------------------------------------------------------
    def _thermal_setup(self, sysp):
        """Energy states and the thermal network: ideal mixing at each connection point (Modelica's stream
        connectors: the inflows' enthalpy weighted by their mass flows), isenthalpic static parts, a pump without a
        volume adding W / m_flow, storages with der(U) = Hb_flow + Qb_flow + Wb_flow (a vessel's Hb_flow includes
        m v^2/2 at ports with port_diameter), and a linear thermal network on the heat ports (IdealHeatTransfer of
        vessels: the port is at the fluid's T)."""
        net = self.net
        self.thermal, self.thermal_why = True, ""
        for e, d in net.elems.items():
            if d["kind"] in ("fl_boundary", "fl_mass_flow") and "T_in" in (d.get("enabled") or ()):
                self.thermal, self.thermal_why = False, f"{d['comp']}: input T_in (temperatures not in the reference)"
        for dp in self.dpipes:
            if dp["heat"] and dp["p"].get("heat_transfer", "ideal") not in ("constant", "local"):
                self.thermal, self.thermal_why = False, (f"{net.elems[dp['e']]['comp']}: ideal heat transfer (the "
                                                         f"volumes at the wall's temperature: not in the reference)")
        dyn = sysp.get("dynamics", "fixed_initial")
        if dyn != "fixed_initial" and (self.xvols or self.pvols):
            self.thermal, self.thermal_why = False, f"system dynamics {dyn} (temperatures from fixed_initial only)"
        if self.var and not self.thermal:
            raise FluidRefError(f"a liquid whose properties change with temperature, without its temperatures: "
                                f"{self.thermal_why}")
        self.h_bnd, self.T_bnd = {}, {}
        for e, d in net.elems.items():
            if d["kind"] in ("fl_boundary", "fl_mass_flow"):
                self.T_bnd[e] = float(d["params"]["T"])
                self.h_bnd[e] = medium_of(d["params"]).h(self.T_bnd[e])
        med0 = next((x["med"] for x in self.flows + self.tanks + self.xvols), medium_of({}))
        self.h_default = med0.h(self.T_amb)
        # the thermal network: nodes of heat ports; fixed by fixed_temperature, a capacitor or a vessel's heat port
        tnode = {}
        for ep, nid in net.node_of.items():
            if net.port_type(ep) == "heat":
                tnode[ep] = nid
        self.tnode = dict(tnode)
        self.cond, self.hflows, self.tfix, self.caps, self.tsens = [], [], {}, [], []

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

    def _pump_power(self, i, m, pa, pb):
        """W_total: (pb - pa) V / eta, or MSL's quadraticPower (N/N0)^3 (rho/rho_nominal) P(V N0/N) per pump; rho the
        pump medium's, rho_nominal the density at p_default, T_default unless rho_power is given (FL5)."""
        f = self.flows[i]
        p, med = f["p"], f["med"]
        rho = med.d(self._Tpump[i])
        n0 = float(p["N_nominal"])
        n = float(p["N"]) if p.get("N") is not None else n0
        npar = float(p["nParallel"])
        if p.get("power") is not None:
            from .chains import quad_points
            pw, _ = quad_points(p["power"])
            c = np.linalg.solve(np.vander([r[0] for r in pw], 3, increasing=True), [r[1] for r in pw])
            rho_n = float(p["rho_power"]) if p.get("rho_power") is not None else med.d_nom
            vs = m / rho / npar * n0 / n
            return npar * (n / n0) ** 3 * (rho / rho_n) * (c[0] + c[1] * vs + c[2] * vs * vs)
        return (pb - pa) * (m / rho) / float(p["eta"])

    def _node_inflow(self, n, m):
        """The flows the elements and sources put into a hydraulic node."""
        q = 0.0
        for i, f in enumerate(self.flows):
            if f["b"] == n:
                q += m[i]
            if f["a"] == n:
                q -= m[i]
        for n_, ms, _, _ in self.sources:
            if n_ == n:
                q += ms
        return q

    def _vol_flows(self, m):
        """{(xvol index, j): ports[j].m_flow into a closed volume}: what the other components put into its
        connection point (one volume port there, no boundary, tank or source: __init__)."""
        q = self._q_orig(m)
        return {(xi, j): q.get(n0, 0.0) for xi, xv in enumerate(self.xvols) if xv["kind"] == "vol"
                for j, n0 in enumerate(xv["ports"], 1)}

    def _pipe_flow(self, dp, m, P):
        """A dynamic pipe's m_flows[1..n+1] (the volumes' mass changes included), vs, densities, port and volume
        pressures and dps_fg at one instant."""
        n, x0 = dp["n"], dp["x0"]
        sg = [m[i] for i in dp["segs"]]
        md = self._mdx[x0:x0 + n]
        mf = [sg[0] + md[0]] + sg + ([sg[-1] - md[n - 1]] if dp["st"] == "av_vb" else [])
        ds = [dp["med"].d(self._Tx[x0 + i], P[dp["vn"][i]]) for i in range(n)]
        A, npar = dp["A"], dp["npar"]
        vs = [0.5 * (mf[i] + mf[i + 1]) / ds[i] / A / npar for i in range(n)]
        dps = [P[self.flows[i]["a"]] - P[self.flows[i]["b"]] for i in dp["segs"]]
        return {"mf": mf, "vs": vs, "ds": ds, "pm": [P[v] for v in dp["vn"]], "pa": P[dp["na"]], "pb": P[dp["nb"]],
                "dps": dps}

    def _pipe_wb(self, dp, fl):
        """DynamicPipe's Wb_flows (vs A (dp/dx + friction - g dh d) nParallel, as MSL writes them per
        modelStructure)."""
        n, A, npar, g, dh = dp["n"], dp["A"], dp["npar"], self.g, dp["hi"]
        vs, pm, dps, pa, pb, ds = fl["vs"], fl["pm"], fl["dps"], fl["pa"], fl["pb"], fl["ds"]
        if n == 1:
            return [vs[0] * A * ((pb - pa) + sum(dps) - g * dh * ds[0]) * npar]
        W = [0.0] * n
        for i in range(1, n - 1):
            W[i] = vs[i] * A * ((pm[i + 1] - pm[i - 1]) / 2 + (dps[i - 1] + dps[i]) / 2 - g * dh * ds[i]) * npar
        W[0] = vs[0] * A * ((pm[1] - pm[0]) / 2 + dps[0] / 2 - g * dh * ds[0]) * npar
        if dp["st"] == "av_vb":
            W[n - 1] = vs[n - 1] * A * ((pm[n - 1] - pm[n - 2]) / 2 + dps[n - 2] / 2 - g * dh * ds[n - 1]) * npar
        else:
            W[n - 1] = vs[n - 1] * A * ((pb - pm[n - 2]) / 1.5 + dps[n - 2] / 2 + dps[n - 1] -
                                        g * dh * ds[n - 1]) * npar
        return W

    def _pipe_alphas(self, dp, fl):
        """Heat transfer coefficients of a dynamic pipe's volumes: alpha0 (constant) or LocalPipeFlowHeatTransfer
        with each volume's properties."""
        p, n = dp["p"], dp["n"]
        if p.get("heat_transfer") == "constant":
            return [float(p["alpha"])] * n
        med, D, x0 = dp["med"], dp["D"], dp["x0"]
        out = []
        for i, v in enumerate(fl["vs"], 1):
            T = self._Tx[x0 + i - 1]
            rho, mu, lam = fl["ds"][i - 1], med.mu(T), med.lam(T)
            Re = abs(v) * rho * D / mu
            Pr = mu * med.cp(T) / lam
            out.append(local_alpha(Re, Pr, D, dp["li"], (i - 0.5) if v >= 0 else (n - i + 0.5), lam))
        return out

    def _energy(self, Tc, m, P, ports=False):
        """(dU tanks, dU pump volumes, dU closed and pipe volumes, dT capacitors, details) at one instant, from the
        storages' state (_set_state) and this instant's flows. Mixing at each connection point of the network (the
        study's, not the joined hydraulic nodes). details['mdp'], ['mdx']: the volumes' mass changes these energies
        imply (V d'(T) dT/dt); ['hout'], ['instream']: the ports' h_outflow and inStream (ports, or T-dependent
        media)."""
        nt = len(self.tanks)
        Tt, mt = self._Tt, self._mt
        for xi in self.gstate:                    # a gas volume with its own mass takes its node's net inflow
            self._mdx[xi] = self._node_inflow(self.xvols[xi]["node"], m)
        ht = np.array([tk["med"].h(Tt[i]) for i, tk in enumerate(self.tanks)])
        Tp, Tx = self._Tp, self._Tx
        hp = np.array([pv["med"].h(Tp[j]) for j, pv in enumerate(self.pvols)])
        hx = np.array([xv["med"].h(Tx[j]) for j, xv in enumerate(self.xvols)])
        W = {}
        for i, f in enumerate(self.flows):
            if f["k"] == "fl_pump":
                W[i] = self._pump_power(i, m[i], P[f["a"]], P[f["b"]])
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
        pflows = {}                   # a pump volume's port flows (into the pump): port_a, port_b
        for jv, pv in enumerate(self.pvols):
            i = pv["i"]
            pflows[i] = (m[i], -(m[i] - self._mdp[jv]))
        for i, f in enumerate(self.flows):
            mi = m[i]
            if f["k"] == "tank_port":
                add(f["a0"], -mi, h=ht[f["ti"]], own=f["own"])
                continue
            if f["k"] == "dp_seg":
                continue
            if "vol" in f:
                fa, fb = pflows[i]
                add(f["a0"], -fa, h=hp[f["vol"]], own=f["e"])
                add(f["b0"], -fb, h=hp[f["vol"]], own=f["e"])
                continue
            dst, src = (f["b0"], f["a0"]) if mi > 0 else (f["a0"], f["b0"])
            ex = W[i] / abs(mi) if i in W and abs(mi) > 0 else 0.0
            if f["k"] == "fl_pipe":          # StaticPipe: h_outflow(b) = inStream(a) - g height_ab, and back
                ex = -self.g * float(f["p"]["height_ab"]) * (1.0 if mi > 0 else -1.0)
            add(dst, abs(mi), up=src, extra=ex, own=f["e"])
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
        hout, instream = self._h_outflow(dis, hmix, m, W, ht, hp, hx) if (ports or self.var) else ({}, None)
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
                # ports_E_flow: m v^2/2, v with the density of the actual stream (portVelocities)
                rho_s = tk["med"].d(self._pT[i][0]) if mi > 0 else tk["med"].d(Tt[ti])
                H += mi * (hmix[f["a0"]] if mi > 0 else ht[ti]) + mi * 0.5 * (mi / (rho_s * Ap)) ** 2
            Qt[ti] = Qin.get(tk["hn"], 0.0) if tk.get("hn") is not None else 0.0
            dUt[ti] = H + Qt[ti]
        dUp = np.zeros(len(self.pvols))
        for jv, pv in enumerate(self.pvols):
            i = pv["i"]
            f = self.flows[i]
            fa, fb = pflows[i]
            dUp[jv] = (fa * (hmix[f["a0"]] if fa > 0 else hp[jv]) + fb * (hmix[f["b0"]] if fb > 0 else hp[jv]) +
                       W[i])
        dUx = np.zeros(len(self.xvols))
        Qx = np.zeros(len(self.xvols))
        for xi, xv in enumerate(self.xvols):
            if xv["kind"] == "vol":
                H = sum(mp * (hmix[xv["ports"][j - 1]] if mp > 0 else hx[xi])
                        for (x_, j), mp in vq.items() if x_ == xi)
                Qx[xi] = Qin.get(xv["hn"], 0.0) if xv["hn"] is not None else 0.0
                dUx[xi] = H + Qx[xi]
        pdet = []
        sx = np.zeros(len(self.xvols))         # dE/d(dm/dt) of a pipe's end volume: its port's stream enthalpy
        for pi_, dp in enumerate(self.dpipes):
            fl, n, x0 = pfl[pi_], dp["n"], dp["x0"]
            h = hx[x0:x0 + n]
            mf = fl["mf"]
            H = [mf[0] * (hmix[dp["a0"]] if mf[0] > 0 else h[0])]
            H += [mf[i] * (h[i - 1] if mf[i] >= 0 else h[i]) for i in range(1, n)]
            H += [mf[n] * (h[n - 1] if mf[n] > 0 else hmix[dp["b0"]])]
            Hb = [H[i] - H[i + 1] for i in range(n)]
            sx[x0] = hmix[dp["a0"]] if mf[0] > 0 else h[0]          # port_a.m_flow = m_flows[2] + dm1/dt
            if dp["st"] == "av_vb":                                   # port_b.m_flow = -(m_flows[n] - dmn/dt)
                sx[x0 + n - 1] = h[n - 1] if mf[n] > 0 else hmix[dp["b0"]]
            Wb = self._pipe_wb(dp, fl)
            Qb = [Qin.get(("dpvol", dp["e"], i), 0.0) for i in range(n)]
            for i in range(n):
                Qx[x0 + i] = Qb[i]
                dUx[x0 + i] = Hb[i] + Wb[i] + Qb[i]
            pdet.append({"H": H, "Hb": Hb, "Wb": Wb, "Qb": Qb, "alphas": alphas[pi_], "mf": mf})
        dTc = np.array([Qin.get(c["n"], 0.0) / c["C"] for c in self.caps])

        def mdot(med, V, T, dU, p=None, s_=0.0, md0=0.0):
            """der(m) = V d'(T) dT/dt (p fixed) with der(U) = der(m) u + m u' dT/dt = E, where E took md0 through a
            port of stream enthalpy s_: E(md) = dU + s_ (md - md0), solved for md (a gas at a boundary couples
            strongly: a fixed point would not converge)."""
            if not med.var:
                return 0.0
            dd = med.dd(T, p)
            k = V * dd / (V * med.d(T, p) * med.du(T))
            return k * (dU - s_ * md0) / (1 + k * med.u(T) - k * s_)
        mdp = np.array([mdot(pv["med"], pv["V"], Tp[j], dUp[j]) for j, pv in enumerate(self.pvols)])
        mdx = np.array([self._mdx[j] if xv["mode"] == "state" else
                        mdot(xv["med"], xv["V"], Tx[j], dUx[j], self._px[j] if xv["med"].gas else None,
                             sx[j], self._mdx[j])
                        for j, xv in enumerate(self.xvols)])
        return dUt, dUp, dUx, dTc, {"hmix": hmix, "Tt": Tt, "Tp": Tp, "Tx": Tx, "hx": hx, "Tnet": Tnet, "Qin": Qin,
                                    "Qt": Qt, "Qx": Qx, "W": W, "pipes": pdet, "hout": hout, "instream": instream,
                                    "mdp": mdp, "mdx": mdx, "pflows": pflows}

    def _h_outflow(self, dis, hmix, m, W, ht, hp, hx):
        """({Modelica path of a port's h_outflow: value}, inStream(node, port)) as MSL defines them (FL12): a
        storage's ports carry its medium.h; a static part's port_a carries inStream at port_b and back
        (PartialTwoPortTransport); a pump without a volume carries medium.h = inStream(inlet) + W/|m| (its steady
        energy balance); pressure sensors Medium.h_default. inStream at a port (Modelica's stream connectors): the
        other ports' outflows at its connection point mixed by their flows; without any outflow there, the other
        port's h_outflow (two ports) or their mean."""
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
                port(f"{e}.ports[{f['j']}].h_outflow", f["a0"], f["own"], ht[f["ti"]])
            elif k == "dp_seg":
                continue
            elif "vol" in f:
                port(f"{e}.port_a.h_outflow", f["a0"], e, hp[f["vol"]])
                port(f"{e}.port_b.h_outflow", f["b0"], e, hp[f["vol"]])
            elif k == "fl_pump":
                port(f"{e}.port_a.h_outflow", f["a0"], e, rule=("pump", f, i))
                port(f"{e}.port_b.h_outflow", f["b0"], e, rule=("pump", f, i))
            else:
                gh = self.g * float(f["p"]["height_ab"]) if k == "fl_pipe" else 0.0
                port(f"{e}.port_a.h_outflow", f["a0"], e, rule=("pass", f["b0"], gh))
                port(f"{e}.port_b.h_outflow", f["b0"], e, rule=("pass", f["a0"], -gh))
        for dp in self.dpipes:
            port(f"{dp['e']}.port_a.h_outflow", dp["a0"], dp["e"], hx[dp["x0"]])
            port(f"{dp['e']}.port_b.h_outflow", dp["b0"], dp["e"], hx[dp["x0"] + dp["n"] - 1])
        for e, d in net.elems.items():       # PartialAbsoluteSensor, PartialRelativeSensor: h_default (20 degC)
            if d["kind"] in ("fl_pressure", "fl_relative_pressure"):
                hd = medium_of(d["params"]).h_default
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
                    v = instream(rule[1], own) + rule[2]
                else:
                    f, i = rule[1], rule[2]
                    mi = m[i]
                    v = (instream(f["a0"], own) + W[i] / mi if mi > 0 else
                         instream(f["b0"], own) + W[i] / -mi if mi < 0 else instream(f["a0"], own))
                if H.get(path) is None or abs(H[path] - v) > 1e-15 * max(1.0, abs(v)):
                    H[path], changed = v, True
            if not changed:
                break
        return H, instream

    def _update_props(self, en) -> float:
        """Take the port temperatures, pumps' medium temperatures and volumes' mass changes an energy pass gives;
        the largest change (K, or kg/s scaled) for the instant's fixed point."""
        en = en[4]
        ins, H = en["instream"], en["hout"]
        dmax = 0.0
        for i, f in enumerate(self.flows):
            med, k = f["med"], f["k"]
            if k == "dp_seg":
                if f["xb"] is None:                      # av_b's last segment: state_b = inStream at port_b
                    T = med.T_h(ins(f["b0"], f["own"]))
                    dmax = max(dmax, abs(T - self._pT[i][1]))
                    self._pT[i][1] = T
                continue
            if k == "tank_port":
                new = [med.T_h(ins(f["a0"], f["own"])), self._pT[i][1]]
            else:
                new = [med.T_h(ins(f["a0"], f["own"])), med.T_h(ins(f["b0"], f["own"]))]
            dmax = max(dmax, abs(new[0] - self._pT[i][0]), abs(new[1] - self._pT[i][1]))
            self._pT[i] = new
            if k == "fl_pump" and "vol" not in f:        # medium.h of a pump without a volume
                T = med.T_h(H[f"{f['e']}.port_b.h_outflow"])
                dmax = max(dmax, abs(T - self._Tpump[i]))
                self._Tpump[i] = T
        M = max(1.0, float(np.max(np.abs(self._s))) if getattr(self, "_s", None) is not None and self._s.size else 1.0)
        for old, new in ((self._mdp, en["mdp"]), (self._mdx, en["mdx"])):
            if old.size:                       # 1e-13 of the flows counts as 1e-11 K
                dmax = max(dmax, float(np.max(np.abs(new - old))) / M * 100.0)
                old[:] = new
        return dmax

    def _instant(self, x, ports=False):
        """Flows, pressures and the energy details at one instant of the states x (the coupled fixed point for
        T-dependent liquids and gases)."""
        nt, nv, nx = self._split
        mt, Ut, Up, Ux, Tc = self._unpack(x)
        self._set_state(mt, Ut, Up, Ux, self._Mg)
        if not self.thermal:
            m, P = self._solve()
            return m, P, None
        for _ in range(100):
            m, P = self._solve()
            en = self._energy(Tc, m, P, ports=ports)
            if not self.var:
                if ports:                                # the port temperatures the outputs use
                    self._update_props(en)
                return m, P, en
            if self._update_props(en) < 1e-11:
                return m, P, en
        raise FluidRefError("the flows and temperatures did not agree at one instant (T-dependent liquid)")

    def _unpack(self, x):
        """(tank masses, tank U, pump-volume U, volume U, capacitor T); the gas volumes' masses (after the volumes'
        U) go to self._Mg."""
        nt, nv, nx = self._split
        o = 2 * nt + nv
        ng = len(self.gstate)
        if not self.thermal:
            self._Mg = np.zeros(0)
            return x[:nt], np.zeros(nt), np.zeros(nv), np.zeros(nx), np.zeros(0)
        self._Mg = x[o + nx:o + nx + ng]
        return x[:nt], x[nt:2 * nt], x[2 * nt:o], x[o:o + nx], x[o + nx + ng:]

    # ---- over time --------------------------------------------------------------------------------------------------
    def simulate(self, t_eval, t0=None) -> dict:
        from scipy.integrate import solve_ivp
        t = np.asarray(t_eval, float)
        self._x = None
        nt, nv, nx, nc = len(self.tanks), len(self.pvols), len(self.xvols), len(self.caps)
        self._split = (nt, nv, nx)
        th = self.thermal
        self._mdp[:], self._mdx[:] = 0.0, 0.0
        m0 = np.array([tk["med"].d(tk["T_start"]) * tk["A"] * tk["h0"] for tk in self.tanks])
        U0 = np.array([m0[i] * tk["med"].u(tk["T_start"]) for i, tk in enumerate(self.tanks)])
        V0 = np.array([pv["V"] * pv["med"].d(pv["T_start"]) * pv["med"].u(pv["T_start"]) for pv in self.pvols])
        def p0(xv):                       # a gas volume: p_start (own mass) or its boundary's p
            if not xv["med"].gas:
                return None
            return xv["p_start"] if xv["mode"] == "state" else self._p_fixed(xv["node"])
        X0 = np.array([xv["V"] * xv["med"].d(xv["T_start"], p0(xv)) * xv["med"].u(xv["T_start"]) for xv in self.xvols])
        G0 = np.array([self.xvols[xi]["V"] * self.xvols[xi]["med"].d(self.xvols[xi]["T_start"],
                                                                      self.xvols[xi]["p_start"]) for xi in self.gstate])
        C0 = np.array([c["T0"] for c in self.caps])
        if self.gstate and not th:
            raise FluidRefError("a gas volume without its temperatures")
        x0 = np.concatenate([m0] + ([U0, V0, X0, G0, C0] if th else []))

        def rhs(_, x):
            m, P, en = self._instant(x)
            dm = self._tank_inflows(m) if nt else np.zeros(0)
            if not th:
                return dm
            dUt, dUp, dUx, dTc, _ = en
            dMg = np.array([self._mdx[xi] for xi in self.gstate])
            return np.concatenate([dm, dUt, dUp, dUx, dMg, dTc])
        ts = float(t[0] if t0 is None else t0)
        X = np.repeat(x0[:, None], t.size, axis=1)
        if x0.size and t[-1] > ts:
            sc = np.concatenate([np.maximum(m0, 1.0) * 1e-12] +
                                ([np.maximum(np.abs(U0), 1.0) * 1e-12, np.maximum(np.abs(V0), 1.0) * 1e-12,
                                  np.maximum(np.abs(X0), 1.0) * 1e-12, np.maximum(G0, 1e-6) * 1e-10,
                                  np.full(nc, 1e-9)] if th else []))
            sol = solve_ivp(rhs, (ts, float(t[-1])), x0, t_eval=t[t >= ts], method="Radau", rtol=1e-10, atol=sc)
            if not sol.success:
                raise FluidRefError(f"the tank levels and temperatures did not integrate: {sol.message}")
            X[:, t >= ts] = sol.y
        if nt and np.any(X[:nt] < 0):
            raise FluidRefError("a tank ran empty (below its ports: MSL's inflow-only mode, not in the reference)")
        self._x = None
        rows = []
        for j in range(t.size):
            m, P, en = self._instant(X[:, j], ports=True)
            rows.append(self._row(X[:, j], m, P, en))
        keys = list(dict.fromkeys(k for r in rows for k in r))
        return {k: np.array([r.get(k, np.nan) for r in rows], float) for k in keys}

    def steady(self, t1=None) -> dict:
        """Without tanks the hydraulics are algebraic: the state at any time. With tanks: RefError (no steady
        state is implied; the caller runs to the end instead)."""
        from .ref import RefError
        if self.tanks or (self.thermal and (self.pvols or self.xvols or self.caps)):
            raise RefError("tank levels or temperatures: no algebraic steady state")
        v = self.simulate(np.array([0.0]))
        return {k: float(x[0]) for k, x in v.items()}

    def _row(self, x, m, P, en) -> dict:
        """{Modelica path: value} at one instant (after _instant)."""
        out, g, net = {}, self.g, self.net
        nt, nv, nx = self._split
        mt, Ut, Up, Ux, Tc = self._unpack(x)
        for i, f in enumerate(self.flows):
            e, k, p, med = f["e"], f["k"], f["p"], f["med"]
            mm, pa, pb = m[i], P[f["a"]], P[f["b"]]
            if k == "tank_port":
                out.update({f"{e}.ports[{f['j']}].m_flow": mm, f"{e}.ports[{f['j']}].p": pa})
                continue
            if k == "dp_seg":
                j = f["seg"] + 1
                out.update({f"{e}.flowModel.m_flows[{j}]": mm, f"{e}.flowModel.dps_fg[{j}]": pa - pb})
                continue
            out.update({f"{e}.port_a.m_flow": mm, f"{e}.port_b.m_flow": -mm, f"{e}.port_a.p": pa,
                        f"{e}.port_b.p": pb})
            rho_a, rho_b, _, _ = self._props(i, pa, pb)
            if k in ("fl_resistance", "fl_orifice", "fl_valve", "fl_valve_gas", "fl_valve_linear", "fl_pipe",
                     "fl_volume_flow_sensor"):
                # PartialTwoPortTransport's V_flow, VolumeFlowRate sensor: m_flow / regStep(m_flow, d_a, d_b)
                out[f"{e}.V_flow"] = mm / reg_step(mm, rho_a, rho_b, self.m_small)
            if k == "fl_mass_flow_sensor":
                out[f"{e}.m_flow"] = mm
            if k == "fl_pump":
                rho = med.d(self._Tpump[i])
                n0 = float(p["N_nominal"])
                n = float(p["N"]) if p.get("N") is not None else n0
                dpp = pb - pa
                npar = float(p["nParallel"])
                W = self._pump_power(i, mm, pa, pb)
                eta = dpp * (mm / rho / npar) / (W / npar) if p.get("power") is not None else float(p["eta"])
                out[f"{e}.m"] = rho * float(p.get("V") or 0.0)
                out[f"{e}.monitoring.state_in.p"] = pa              # PartialPump's monitoring (NPSH, FL16)
                out.update({f"{e}.m_flow": mm, f"{e}.V_flow": mm / rho, f"{e}.dp_pump": dpp,
                            f"{e}.head": dpp / (rho * g), f"{e}.eta": eta,
                            f"{e}.W_total": W, f"{e}.N": n * C.W_RPM})
                if "vol" in f:                  # port_b.m_flow = -(port_a.m_flow - der(m))
                    out[f"{e}.port_b.m_flow"] = -(mm - self._mdp[f["vol"]])
        for dp in self.dpipes:
            e, n, x0 = dp["e"], dp["n"], dp["x0"]
            fl = self._pipe_flow(dp, m, P)
            out.update({f"{e}.port_a.m_flow": fl["mf"][0], f"{e}.port_b.m_flow": -fl["mf"][n],
                        f"{e}.port_a.p": fl["pa"], f"{e}.port_b.p": fl["pb"]})
            for i in range(n + 1):
                out[f"{e}.m_flows[{i + 1}]"] = fl["mf"][i]
            for i in range(n):
                out[f"{e}.mediums[{i + 1}].p"] = fl["pm"][i]
                out[f"{e}.mediums[{i + 1}].d"] = fl["ds"][i]
                out[f"{e}.vs[{i + 1}]"] = fl["vs"][i]
                out[f"{e}.ms[{i + 1}]"] = self.xvols[x0 + i]["V"] * fl["ds"][i]
        vq = self._vol_flows(m)
        for xi, xv in enumerate(self.xvols):
            if xv["kind"] == "vol":
                e = xv["e"]
                d = xv["med"].d(self._Tx[xi], self._px[xi] if xv["med"].gas else None)
                if xv["node"] is not None:
                    out[f"{e}.medium.p"] = P[xv["node"]]
                out[f"{e}.m"] = xv["V"] * d
                out[f"{e}.medium.d"] = d
                for j in range(1, len(xv["ports"]) + 1):
                    out[f"{e}.ports[{j}].m_flow"] = vq[(xi, j)]
                    if xv["node"] is not None:
                        out[f"{e}.ports[{j}].p"] = out[f"{e}.medium.p"]
        for j, tk in enumerate(self.tanks):
            e, A = tk["e"], tk["A"]
            out[f"{e}.level"] = self._levels[j]
            out[f"{e}.V"] = A * self._levels[j]
            out[f"{e}.m"] = self._mt[j]
            out[f"{e}.medium.d"] = tk["med"].d(self._Tt[j])
        # vessels', boundaries' and sources' ports: ports[j].p, ports[j].m_flow (into the component)
        ff = self._fixed_flows(m)
        for e, j, n in self.fixed_ports:
            out[f"{e}.ports[{j}].p"] = P[self.nodes_root[n]]
            out[f"{e}.ports[{j}].m_flow"] = ff[(e, j)]
        for n, ms, e, orig in self.sources:
            q = self._q_orig(m, False)
            for j, n0 in enumerate(orig, 1):
                out[f"{e}.ports[{j}].p"] = P[n]
                out[f"{e}.ports[{j}].m_flow"] = -ms if len(orig) == 1 else q.get(n0, 0.0)
        for e, d in net.elems.items():
            if d["kind"] == "fl_pressure":
                n = self.find(f"{e}.port")
                out[f"{e}.p"] = out[f"{e}.port.p"] = P[n]
                out[f"{e}.port.m_flow"] = 0.0
            if d["kind"] == "fl_relative_pressure":
                na, nb = self.find(f"{e}.port_a"), self.find(f"{e}.port_b")
                out[f"{e}.p_rel"] = P[na] - P[nb]
                out[f"{e}.port_a.m_flow"] = out[f"{e}.port_b.m_flow"] = 0.0
        if self.thermal:
            out.update(self._thermal_row(x, m, P, en))
        return out

    def _thermal_row(self, x, m, P, en) -> dict:
        dUt, dUp, dUx, dTc, D = en
        out, net = dict(D["hout"]), self.net
        nt, nv, nx = self._split
        mt, Ut, Up, Ux, Tc = self._unpack(x)
        for ti, tk in enumerate(self.tanks):
            e = tk["e"]
            out[f"{e}.medium.T"] = D["Tt"][ti]
            out[f"{e}.U"] = Ut[ti]
            # OpenTank: Wb_flow = 0 (singleState media), Qb_flow = the heat port's, ports_E_flow: m v^2/2 at ports
            # with port_diameter (portsData_height 0; v with the actual stream's density), else 0
            out[f"{e}.Qb_flow"] = D["Qt"][ti]
            out[f"{e}.Wb_flow"] = 0.0
            if tk.get("hn") is not None:
                out[f"{e}.heatPort.T"] = out[f"{e}.medium.T"]
                out[f"{e}.heatPort.Q_flow"] = out[f"{e}.Qb_flow"]
            loss = dict(tk["loss"])
            for j in range(1, len(tk["ports"]) + 1):
                if j in loss:
                    i = loss[j]
                    f = self.flows[i]
                    Ap = math.pi / 4 * f["D"] ** 2
                    mm = m[i]
                    rho_s = tk["med"].d(self._pT[i][0]) if mm > 0 else tk["med"].d(D["Tt"][ti])
                    out[f"{e}.ports_E_flow[{j}]"] = mm * 0.5 * (mm / (rho_s * Ap)) ** 2
                else:
                    out[f"{e}.ports_E_flow[{j}]"] = 0.0
        pv_of = {pv["i"]: jv for jv, pv in enumerate(self.pvols)}
        for i, f in enumerate(self.flows):          # pumps (PartialPump: Wb_flow = W_total, no heat transfer)
            if f["k"] == "fl_pump":
                e = f["e"]
                out[f"{e}.U"] = Up[pv_of[i]] if i in pv_of else 0.0
                out[f"{e}.Qb_flow"] = 0.0
                out[f"{e}.Wb_flow"] = D["W"][i]
        for jv, pv in enumerate(self.pvols):
            out[f"{self.flows[pv['i']]['e']}.medium.T"] = D["Tp"][jv]
        for i, f in enumerate(self.flows):         # monitoring.state_in.T: inStream at port_a (NPSH, FL16)
            if f["k"] == "fl_pump":
                out[f"{f['e']}.monitoring.state_in.T"] = self._pT[i][0]
        for xi, xv in enumerate(self.xvols):
            if xv["kind"] != "vol":
                continue
            e = xv["e"]
            out[f"{e}.medium.T"] = D["Tx"][xi]
            out[f"{e}.U"] = Ux[xi]
            out[f"{e}.Qb_flow"] = D["Qx"][xi]
            out[f"{e}.Wb_flow"] = 0.0
            for j in range(1, len(xv["ports"]) + 1):           # no portsData: no kinetic energy at the ports
                out[f"{e}.ports_E_flow[{j}]"] = 0.0
            if xv["hn"] is not None:
                out[f"{e}.heatPort.T"] = out[f"{e}.medium.T"]
                out[f"{e}.heatPort.Q_flow"] = out[f"{e}.Qb_flow"]
        for pi_, dp in enumerate(self.dpipes):
            e, n, x0 = dp["e"], dp["n"], dp["x0"]
            det = D["pipes"][pi_]
            for i in range(n):
                k1 = i + 1
                out[f"{e}.mediums[{k1}].T"] = D["Tx"][x0 + i]
                out[f"{e}.mediums[{k1}].h"] = D["hx"][x0 + i]
                out[f"{e}.Us[{k1}]"] = Ux[x0 + i]
                for nm, key in (("Hb_flows", "Hb"), ("Wb_flows", "Wb"), ("Qb_flows", "Qb")):
                    out[f"{e}.{nm}[{k1}]"] = det[key][i]
            for i in range(n + 1):
                out[f"{e}.H_flows[{i + 1}]"] = det["H"][i]
            if dp["heat"] and dp["p"].get("heat_transfer") in ("constant", "local"):
                Tw = D["Tnet"].get(self.tnode.get(f"{e}.heat"), np.nan)
                for i in range(n):
                    out[f"{e}.heatTransfer.Q_flows[{i + 1}]"] = det["Qb"][i]
                    out[f"{e}.heatPorts[{i + 1}].Q_flow"] = det["Qb"][i]
                    out[f"{e}.heatPorts[{i + 1}].T"] = Tw
                    out[f"{e}.heatTransfer.alphas[{i + 1}]"] = det["alphas"][i]
                out[f"omct_{e}_coll.port_b.Q_flow"] = sum(det["Qb"])
                out[f"omct_{e}_coll.port_b.T"] = Tw
        for i, f in enumerate(self.flows):
            if f["k"] == "fl_temperature":       # TemperatureTwoPort: regStep(m_flow, T_a_inflow, T_b_inflow)
                Ta, Tb = self._pT[i]
                out[f"{f['e']}.T"] = reg_step(m[i], Ta, Tb, self.m_small)
        # thermal parts
        Tn = D["Tnet"]
        for ci, c in enumerate(self.caps):
            e = c["e"]
            out[f"{e}.T"] = Tc[ci]
            out[f"{e}.der_T"] = D["Qin"].get(c["n"], 0.0) / c["C"]
            out[f"{e}.port.T"] = out[f"{e}.T"]
            out[f"{e}.port.Q_flow"] = out[f"{e}.der_T"] * c["C"]
        for na, nb, G, e in self.cond:
            Ta, Tb = Tn[na], Tn[nb]
            out.update({f"{e}.port_a.T": Ta, f"{e}.port_b.T": Tb, f"{e}.dT": Ta - Tb, f"{e}.Q_flow": G * (Ta - Tb),
                        f"{e}.port_a.Q_flow": G * (Ta - Tb), f"{e}.port_b.Q_flow": -G * (Ta - Tb)})
        for n, Q, al, Tr, e in self.hflows:
            T_ = Tn[n]
            out.update({f"{e}.port.T": T_, f"{e}.port.Q_flow": -Q * (1 + al * (T_ - Tr))})
        for e, d in net.elems.items():
            if d["kind"] == "fixed_temperature":
                n = self.tnode[f"{e}.port"]
                out[f"{e}.port.T"] = Tn[n]
                out[f"{e}.port.Q_flow"] = D["Qin"].get(n, 0.0)
        for e, n in self.tsens:
            out[f"{e}.T"] = out[f"{e}.port.T"] = Tn[n]
        return out
