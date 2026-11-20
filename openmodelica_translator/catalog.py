"""catalog.py — the curated MSL 4.0.0 components: kinds, ports, parameters (SI), variables, initial values.
Version: 2026.11.20.1

Every name below was checked against the MSL 4.0.0 sources (tag v4.0.0): class paths, port names, parameter
names and defaults. A kind is either elementary (one MSL class) or composite (parts that are elementary kinds,
connected inside; e.g. dc_motor). The Modelica generator, the validation and the reference implementation all
read this table.

Port types: pin (electrical: v, i), flange_t (translational: s, f), flange_r (rotational: phi, tau),
heat (thermal: T, Q_flow), fluid (Thermal.FluidHeatFlow: p, m_flow; also h, H_flow), in / out (Real signals).
Flow into a component is positive (Modelica). Conditional ports (cond_ports, e.g. a pipe's heatPort) exist only
when the study connects them: connecting one sets its MSL switch (useHeatPort = true), which changes the build.

Parameter spec P(unit, default, ...): default REQ = required; structural=True puts the value in the model text
(it changes the build); every other value is applied with -overrideFile on each run.
Init spec: (Modelica variable, unit, default, priority): the value becomes var(start=..., fixed=true) when the
reference implementation finds it independent (see ref.init_selection); priority 1 = storage the user expects to
set (capacitor voltage, mass position and speed), 2 = only when nothing else fixes it (spring relaxed).
"""
from __future__ import annotations

REQ = object()

PORT_TYPES = {
    "pin": {"domain": "electrical", "potential": "v", "flow": "i", "units": {"v": "V", "i": "A"}},
    "flange_t": {"domain": "translational", "potential": "s", "flow": "f", "units": {"s": "m", "f": "N"}},
    "flange_r": {"domain": "rotational", "potential": "phi", "flow": "tau", "units": {"phi": "rad", "tau": "N.m"}},
    "heat": {"domain": "thermal", "potential": "T", "flow": "Q_flow", "units": {"T": "K", "Q_flow": "W"}},
    "plug": {"domain": "electrical", "potential": "v", "flow": "i", "units": {"v": "V", "i": "A"}, "phases": 3},
    # quasi-static (phasor) circuits: complex potentials and currents; never joined to the transient ones
    "qs_pin": {"domain": "quasi-static", "potential": "v", "flow": "i", "units": {"v": "V", "i": "A"}, "complex": True},
    "qs_plug": {"domain": "quasi-static", "potential": "v", "flow": "i", "units": {"v": "V", "i": "A"}, "phases": 3,
                "complex": True},
    "cvin": {"domain": "signal", "width": 3, "complex": True},     # three complex signals (inside QS drives)
    "cvout": {"domain": "signal", "width": 3, "complex": True},
    "fluid": {"domain": "fluid", "potential": "p", "flow": "m_flow", "extra": ("h", "H_flow"),
              "units": {"p": "Pa", "m_flow": "kg/s", "h": "J/kg", "H_flow": "W"}},
    # Modelica.Fluid.Interfaces.FluidPort: p, m_flow and the stream h_outflow (V5, 2026.11.19.10); never joined to
    # the FluidHeatFlow coolant ports ("fluid")
    "mfluid": {"domain": "mfluid", "potential": "p", "flow": "m_flow", "units": {"p": "Pa", "m_flow": "kg/s"}},
    "in": {"domain": "signal"},
    "vin": {"domain": "signal", "width": 3},          # a three-element signal input (inside drives)
    "vout": {"domain": "signal", "width": 3},
    "out": {"domain": "signal"},
}


def P(unit, default=REQ, *, min=None, max=None, gt=None, structural=False, doc="", type="real", choices=None,
      abs_T=False, msl=None):
    """A parameter. msl: the MSL parameter name when it differs (None = same name; False = not an MSL parameter
    itself, used by a modifier function)."""
    return {"unit": unit, "default": default, "min": min, "max": max, "gt": gt, "structural": structural,
            "doc": doc, "type": type, "choices": choices, "abs_T": abs_T, "msl": msl}


def I(var, unit, default, priority=1, abs_T=False, phases=None):
    """An initial value; phases=3: one per phase of an array variable (var[1..3]), given as a number or a list."""
    return {"var": var, "unit": unit, "default": default, "priority": priority, "abs_T": abs_T, "phases": phases}


INIT_STATE = "Modelica.Blocks.Types.Init.InitialState"
KINDS: dict[str, dict] = {}


def kind(name, msl, ports, params=None, vars=None, inits=None, *, doc="", text=None, mods=None, rigid=(),
         linear=True, islands=None, hidden=False, block_states=None, alternatives=None, domain=None, cond_ports=None,
         port_groups=None, var_paths=None, multi=None, fluid=None):
    """text: fixed (structural) modifier text; mods: {msl_param: function(params) -> value} for parameters
    computed from the study's (alternatives such as m + cp -> C); rigid: port pairs at the same point
    (Mass, Inertia: L = 0); islands: groups of ports linked through the component (for the ground and
    reference checks; default: all physical ports of one domain together); cond_ports: {port: MSL Boolean} for
    ports that exist only when connected (the Boolean is then set true in the model text); port_groups: {name:
    [ports]}: connecting the name connects every port of the group (a machine's six heat ports at once);
    var_paths: {var: Modelica path inside the component} for variables in records or subcomponents, or a list of
    (coefficient, path) for a sum (a pipe's dp = port_a.p - port_b.p);
    multi: {study port: (Modelica array, size parameter)}: a port connected any number of times, each connection
    its own array element (Modelica.Fluid's ports[nPorts]); fluid: Modelica.Fluid specifics (see FLUID below)."""
    KINDS[name] = {"name": name, "msl": msl, "ports": ports, "params": params or {}, "vars": vars or {},
                   "inits": inits or {}, "doc": doc, "text": text or {}, "mods": mods or {}, "rigid": list(rigid),
                   "linear": linear, "islands": islands, "hidden": hidden, "parts": None,
                   "block_states": block_states or {}, "alternatives": alternatives or [], "domain": domain,
                   "cond_ports": cond_ports or {}, "port_groups": port_groups or {}, "var_paths": var_paths or {},
                   "multi": multi or {}, "fluid": fluid}


def composite(name, parts, ports, params, vars, inits=None, *, conns=(), doc="", linear=True, alternatives=None,
              domain=None, port_groups=None):
    """parts: {part: (kind, {part_param: study_param name | function(params)}[, condition(params)])}; ports:
    {port: "part.port"}; vars: {var: (expression over "part.var" names, unit)}; inits: {init: "part.init"};
    conns: ("a.port", "b.port"[, condition(params)]): a part or a connection with a condition exists only when it
    holds (a QS machine's star-point ground with terminal Y)."""
    KINDS[name] = {"name": name, "msl": None, "parts": parts, "ports": ports, "params": params, "vars": vars,
                   "inits": inits or {}, "conns": list(conns), "doc": doc, "linear": linear, "hidden": False,
                   "alternatives": alternatives or [], "domain": domain, "port_groups": port_groups or {},
                   "cond_ports": {}, "var_paths": {}}


EA = "Modelica.Electrical.Analog."
MT = "Modelica.Mechanics.Translational."
MR = "Modelica.Mechanics.Rotational."
TH = "Modelica.Thermal.HeatTransfer."
MACH_ = "Modelica.Electrical.Machines."
FHF = "Modelica.Thermal.FluidHeatFlow."
BL = "Modelica.Blocks."
PN = {"p": "pin", "n": "pin"}
FAB_T = {"flange_a": "flange_t", "flange_b": "flange_t"}
FAB_R = {"flange_a": "flange_r", "flange_b": "flange_r"}

# ------------------------------------------------------------------------------------------- electrical
HP = {"heatPort": "useHeatPort"}
kind("resistor", EA + "Basic.Resistor", {**PN, "heatPort": "heat"},
     {"R": P("Ohm", min=0, doc="resistance at T_ref"), "T_ref": P("K", 300.15, gt=0, abs_T=True),
      "alpha": P("1/K", 0.0, doc="R_actual = R (1 + alpha (T - T_ref))"),
      "T": P("K", None, gt=0, abs_T=True, doc="device temperature without heat port (default T_ref)")},
     {"v": "V", "i": "A", "LossPower": "W", "R_actual": "Ohm", "T_heatPort": "K"}, cond_ports=HP,
     doc="v = R_actual*i; heatPort (when connected): LossPower flows out as heat, R_actual at the port's temperature")
kind("capacitor", EA + "Basic.Capacitor", PN, {"C": P("F", gt=0)}, {"v": "V", "i": "A"},
     {"v0": I("v", "V", 0.0)}, doc="i = C*dv/dt")
kind("inductor", EA + "Basic.Inductor", PN, {"L": P("H", gt=0)}, {"v": "V", "i": "A"},
     {"i0": I("i", "A", 0.0)}, doc="v = L*di/dt")
kind("ground", EA + "Basic.Ground", {"p": "pin"}, doc="p.v = 0")
kind("emf", EA + "Basic.RotationalEMF", {"p": "pin", "n": "pin", "flange": "flange_r"}, {"k": P("N.m/A")},
     {"v": "V", "i": "A", "w": "rad/s", "tau": "N.m", "tauElectrical": "N.m"},
     islands=[["p", "n"], ["flange"]], doc="v = k*w, flange torque = -k*i (housing fixed)")
kind("transformer", EA + "Basic.Transformer", {"p1": "pin", "n1": "pin", "p2": "pin", "n2": "pin"},
     {"L1": P("H", gt=0), "L2": P("H", gt=0), "M": P("H", min=0)},
     {"v1": "V", "v2": "V", "i1": "A", "i2": "A"},
     {"i1_0": I("i1", "A", 0.0), "i2_0": I("i2", "A", 0.0)}, islands=[["p1", "n1"], ["p2", "n2"]],
     doc="v1 = L1*di1/dt + M*di2/dt, v2 = M*di1/dt + L2*di2/dt")
for _n, _c, _p, _doc in (
        ("constant_voltage", "ConstantVoltage", {"V": P("V")}, "v = V"),
        ("step_voltage", "StepVoltage", {"V": P("V"), "offset": P("V", 0.0), "startTime": P("s", 0.0)},
         "v = offset + (t < startTime ? 0 : V)"),
        ("ramp_voltage", "RampVoltage", {"V": P("V"), "duration": P("s", min=0), "offset": P("V", 0.0),
                                         "startTime": P("s", 0.0)}, "v = offset + V*clip((t - startTime)/duration, 0, 1)"),
        ("sine_voltage", "SineVoltage", {"V": P("V"), "f": P("Hz", gt=0), "phase": P("rad", 0.0),
                                         "offset": P("V", 0.0), "startTime": P("s", 0.0)},
         "v = offset + (t < startTime ? 0 : V*sin(2*pi*f*(t - startTime) + phase))"),
        ("pulse_voltage", "PulseVoltage", {"V": P("V"), "width": P("%", 50.0, gt=0, max=100), "period": P("s", gt=0),
                                           "offset": P("V", 0.0), "startTime": P("s", 0.0)},
         "v = offset + V during the first width % of each period; square wave: width 50")):
    kind(_n, EA + "Sources." + _c, PN, _p, {"v": "V", "i": "A"}, doc=_doc)
for _n, _c, _p, _doc in (
        ("constant_current", "ConstantCurrent", {"I": P("A")}, "i = I (from p through the source to n)"),
        ("step_current", "StepCurrent", {"I": P("A"), "offset": P("A", 0.0), "startTime": P("s", 0.0)},
         "i = offset + (t < startTime ? 0 : I)"),
        ("sine_current", "SineCurrent", {"I": P("A"), "f": P("Hz", gt=0), "phase": P("rad", 0.0),
                                         "offset": P("A", 0.0), "startTime": P("s", 0.0)},
         "i = offset + (t < startTime ? 0 : I*sin(2*pi*f*(t - startTime) + phase))")):
    kind(_n, EA + "Sources." + _c, PN, _p, {"v": "V", "i": "A"}, doc=_doc)
kind("signal_voltage", EA + "Sources.SignalVoltage", {"p": "pin", "n": "pin", "v": "in"}, vars={"i": "A"},
     doc="p.v - n.v = input v")
kind("signal_current", EA + "Sources.SignalCurrent", {"p": "pin", "n": "pin", "i": "in"}, vars={"v": "V"},
     doc="current from p through the source to n = input i")
kind("voltage_sensor", EA + "Sensors.VoltageSensor", {"p": "pin", "n": "pin", "v": "out"}, doc="v = p.v - n.v (no current)")
kind("current_sensor", EA + "Sensors.CurrentSensor", {"p": "pin", "n": "pin", "i": "out"}, doc="i from p to n (no voltage drop)")
kind("potential_sensor", EA + "Sensors.PotentialSensor", {"p": "pin", "phi": "out"}, doc="phi = p.v")
kind("ideal_diode", EA + "Ideal.IdealDiode", {**PN, "heatPort": "heat"},
     {"Ron": P("Ohm", 1e-5, min=0), "Goff": P("S", 1e-5, min=0), "Vknee": P("V", 0.0, min=0)},
     {"v": "V", "i": "A", "LossPower": "W"}, linear=False, cond_ports=HP,
     doc="piecewise linear: Ron when conducting, Goff when blocking; heatPort (when connected) takes LossPower")
kind("opamp", EA + "Ideal.IdealOpAmp3Pin", {"in_p": "pin", "in_n": "pin", "out": "pin"},
     islands=[["in_p", "in_n", "out"]], doc="ideal (nullor): in_p.v = in_n.v, no input currents; output referenced to ground")

# ------------------------------------------------------------------------------------------- translational
kind("mass", MT + "Components.Mass", FAB_T, {"m": P("kg", gt=0)}, {"s": "m", "v": "m/s", "a": "m/s2"},
     {"s0": I("s", "m", 0.0), "v0": I("v", "m/s", 0.0)}, rigid=[("flange_a", "flange_b")],
     doc="m*a = flange_a.f + flange_b.f (length 0: both flanges at s)")
kind("spring", MT + "Components.Spring", FAB_T, {"c": P("N/m", min=0), "s_rel0": P("m", 0.0)},
     {"s_rel": "m", "f": "N"}, {"s_rel_init": I("s_rel", "m", "s_rel0", 2)}, doc="f = c*(s_rel - s_rel0), s_rel = flange_b.s - flange_a.s")
kind("damper", MT + "Components.Damper", {**FAB_T, "heatPort": "heat"}, {"d": P("N.s/m", min=0)},
     {"s_rel": "m", "v_rel": "m/s", "f": "N", "lossPower": "W"}, {"s_rel_init": I("s_rel", "m", 0.0, 2)},
     cond_ports=HP, islands=[["flange_a", "flange_b"], ["heatPort"]], doc="f = d*v_rel; heatPort (when connected) takes lossPower")
kind("spring_damper", MT + "Components.SpringDamper", {**FAB_T, "heatPort": "heat"},
     {"c": P("N/m", min=0), "d": P("N.s/m", min=0), "s_rel0": P("m", 0.0)},
     {"s_rel": "m", "v_rel": "m/s", "f": "N", "lossPower": "W"}, {"s_rel_init": I("s_rel", "m", "s_rel0", 2)},
     cond_ports=HP, islands=[["flange_a", "flange_b"], ["heatPort"]],
     doc="f = c*(s_rel - s_rel0) + d*v_rel; heatPort (when connected) takes lossPower")
kind("fixed", MT + "Components.Fixed", {"flange": "flange_t"}, {"s0": P("m", 0.0)}, doc="flange.s = s0")
kind("force", MT + "Sources.Force", {"flange": "flange_t", "f": "in"}, doc="pushes the flange with the input f [N] (housing fixed)")
kind("force_step", MT + "Sources.ForceStep", {"flange": "flange_t"},
     {"stepForce": P("N"), "offsetForce": P("N", 0.0), "startTime": P("s", 0.0)}, doc="f = offsetForce + (t < startTime ? 0 : stepForce)")
kind("constant_force", MT + "Sources.ConstantForce", {"flange": "flange_t"}, {"f_constant": P("N")}, doc="f = f_constant")
kind("position_sensor", MT + "Sensors.PositionSensor", {"flange": "flange_t", "s": "out"})
kind("speed_sensor", MT + "Sensors.SpeedSensor", {"flange": "flange_t", "v": "out"})
kind("acc_sensor", MT + "Sensors.AccSensor", {"flange": "flange_t", "a": "out"})
kind("force_sensor", MT + "Sensors.ForceSensor", {"flange_a": "flange_t", "flange_b": "flange_t", "f": "out"},
     doc="f transmitted from flange_a to flange_b (rigid)")

# ------------------------------------------------------------------------------------------- rotational
kind("inertia", MR + "Components.Inertia", FAB_R, {"J": P("kg.m2", gt=0)}, {"phi": "rad", "w": "rad/s", "a": "rad/s2"},
     {"phi0": I("phi", "rad", 0.0), "w0": I("w", "rad/s", 0.0)}, rigid=[("flange_a", "flange_b")],
     doc="J*a = flange_a.tau + flange_b.tau")
kind("rot_spring", MR + "Components.Spring", FAB_R, {"c": P("N.m/rad", min=0), "phi_rel0": P("rad", 0.0)},
     {"phi_rel": "rad", "tau": "N.m"}, {"phi_rel_init": I("phi_rel", "rad", "phi_rel0", 2)}, doc="tau = c*(phi_rel - phi_rel0)")
kind("rot_damper", MR + "Components.Damper", {**FAB_R, "heatPort": "heat"}, {"d": P("N.m.s/rad", min=0)},
     {"phi_rel": "rad", "w_rel": "rad/s", "tau": "N.m", "lossPower": "W"}, {"phi_rel_init": I("phi_rel", "rad", 0.0, 2)},
     cond_ports=HP, islands=[["flange_a", "flange_b"], ["heatPort"]], doc="tau = d*w_rel; heatPort (when connected) takes lossPower")
kind("rot_spring_damper", MR + "Components.SpringDamper", {**FAB_R, "heatPort": "heat"},
     {"c": P("N.m/rad", min=0), "d": P("N.m.s/rad", min=0), "phi_rel0": P("rad", 0.0)},
     {"phi_rel": "rad", "w_rel": "rad/s", "tau": "N.m", "lossPower": "W"},
     {"phi_rel_init": I("phi_rel", "rad", "phi_rel0", 2)}, cond_ports=HP, islands=[["flange_a", "flange_b"], ["heatPort"]],
     doc="tau = c*(phi_rel - phi_rel0) + d*w_rel; heatPort (when connected) takes lossPower")
kind("rot_fixed", MR + "Components.Fixed", {"flange": "flange_r"}, {"phi0": P("rad", 0.0)}, doc="flange.phi = phi0")
kind("ideal_gear", MR + "Components.IdealGear", FAB_R, {"ratio": P("1", gt=0)},
     doc="flange_a.phi = ratio*flange_b.phi; 0 = ratio*flange_a.tau + flange_b.tau (housing fixed)")
kind("torque", MR + "Sources.Torque", {"flange": "flange_r", "tau": "in"}, doc="drives the flange with the input tau [N.m]")
kind("torque_step", MR + "Sources.TorqueStep", {"flange": "flange_r"},
     {"stepTorque": P("N.m"), "offsetTorque": P("N.m", 0.0), "startTime": P("s", 0.0)},
     {"tau": "N.m"}, doc="tau = offsetTorque + (t < startTime ? 0 : stepTorque)")
kind("constant_torque", MR + "Sources.ConstantTorque", {"flange": "flange_r"}, {"tau_constant": P("N.m")},
     {"tau": "N.m", "w": "rad/s"}, doc="tau = tau_constant")
for _n, _c, _e in (("linear_speed_load", "LinearSpeedDependentTorque", "1"), ("quadratic_speed_load", "QuadraticSpeedDependentTorque", "2")):
    kind(_n, MR + "Sources." + _c, {"flange": "flange_r"},
         {"tau_nominal": P("N.m"), "w_nominal": P("rad/s", gt=0), "TorqueDirection": P("1", True, type="bool")},
         {"tau": "N.m", "w": "rad/s"}, linear=(_e == "1"),
         doc=f"accelerating torque tau = tau_nominal*(w/w_nominal)^{_e} (TorqueDirection true: the same sign both ways, "
             f"a load opposing motion in either direction needs tau_nominal < 0)")
kind("angle_sensor", MR + "Sensors.AngleSensor", {"flange": "flange_r", "phi": "out"})
kind("rot_speed_sensor", MR + "Sensors.SpeedSensor", {"flange": "flange_r", "w": "out"})
kind("rot_acc_sensor", MR + "Sensors.AccSensor", {"flange": "flange_r", "a": "out"})
kind("torque_sensor", MR + "Sensors.TorqueSensor", {"flange_a": "flange_r", "flange_b": "flange_r", "tau": "out"},
     doc="tau transmitted from flange_a to flange_b (rigid)")

# ------------------------------------------------------------------------------------------- thermal
kind("heat_capacitor", TH + "Components.HeatCapacitor", {"port": "heat"},
     {"C": P("J/K", gt=0, msl=False), "m": P("kg", gt=0, msl=False), "cp": P("J/(kg.K)", gt=0, msl=False)},
     {"T": "K", "der_T": "K/s"}, {"T0": I("T", "K", 293.15, abs_T=True)},
     mods={"C": lambda p: p["C"] if p.get("C") is not None else p["m"] * p["cp"]},
     alternatives=[["C"], ["m", "cp"]], doc="C*dT/dt = port.Q_flow; C = m*cp")
kind("thermal_conductor", TH + "Components.ThermalConductor", {"port_a": "heat", "port_b": "heat"},
     {"G": P("W/K", min=0, msl=False), "k": P("W/(m.K)", gt=0, msl=False), "A": P("m2", gt=0, msl=False),
      "L": P("m", gt=0, msl=False)}, {"Q_flow": "W", "dT": "K"},
     mods={"G": lambda p: p["G"] if p.get("G") is not None else p["k"] * p["A"] / p["L"]},
     alternatives=[["G"], ["k", "A", "L"]], doc="Q_flow = G*(port_a.T - port_b.T); G = k*A/L")
kind("thermal_resistor", TH + "Components.ThermalResistor", {"port_a": "heat", "port_b": "heat"},
     {"R": P("K/W", gt=0)}, {"Q_flow": "W", "dT": "K"}, doc="port_a.T - port_b.T = R*Q_flow")
kind("convection_core", TH + "Components.Convection", {"solid": "heat", "fluid": "heat", "Gc": "in"},
     vars={"Q_flow": "W", "dT": "K"}, hidden=True, linear=False)
kind("radiation", TH + "Components.BodyRadiation", {"port_a": "heat", "port_b": "heat"},
     {"Gr": P("m2", min=0, doc="emissivity x area (net radiation factor)")}, {"Q_flow": "W", "dT": "K"},
     linear=False, doc="Q_flow = Gr*sigma*(port_a.T^4 - port_b.T^4)")
kind("fixed_temperature", TH + "Sources.FixedTemperature", {"port": "heat"}, {"T": P("K", gt=0, abs_T=True)},
     doc="port.T = T (a boundary: takes or gives any heat)")
kind("prescribed_temperature", TH + "Sources.PrescribedTemperature", {"port": "heat", "T": "in"}, doc="port.T = input T [K]")
kind("fixed_heat_flow", TH + "Sources.FixedHeatFlow", {"port": "heat"},
     {"Q_flow": P("W"), "T_ref": P("K", 293.15, gt=0, abs_T=True), "alpha": P("1/K", 0.0)},
     doc="heat into the port = Q_flow*(1 + alpha*(port.T - T_ref))")
kind("prescribed_heat_flow", TH + "Sources.PrescribedHeatFlow", {"port": "heat", "Q_flow": "in"},
     doc="heat into the port = input Q_flow [W]")
kind("temperature_sensor", TH + "Sensors.TemperatureSensor", {"port": "heat", "T": "out"}, doc="T = port.T [K]")
kind("heat_flow_sensor", TH + "Sensors.HeatFlowSensor", {"port_a": "heat", "port_b": "heat", "Q_flow": "out"},
     doc="heat from port_a to port_b (same temperature)")


# ------------------------------------------------------------------------------------------- coolant loops
# Thermal.FluidHeatFlow: incompressible media with constant properties; p and h at the ports (h = cp T), mass and
# enthalpy flows; T is a component's outlet temperature (upwind). One medium per loop (the ports share it).
MEDIA = {   # name: (MSL record, rho [kg/m3], cp, cv [J/(kg.K)], lambda [W/(m.K)], nu [m2/s]) (MSL 4.0.0 sources)
    "water": ("Water", 995.6, 4177.0, 4177.0, 0.615, 0.8e-6),
    "water_10degC": ("Water_10degC", 999.7, 4192.0, 4192.0, 0.588, 1.307e-6),
    "water_90degC": ("Water_90degC", 965.3, 4205.0, 4205.0, 0.676, 0.347e-6),
    "air_30degC": ("Air_30degC", 1.149, 1007.0, 720.0, 0.0264, 16.3e-6),
    "air_70degC": ("Air_70degC", 1.015, 1010.0, 723.0, 0.0293, 20.3e-6),
    "glycol20_20degC": ("Gylcol20_20degC", 1028.0, 3910.0, 3910.0, 0.523, 1.69e-6),
    "glycol50_20degC": ("Gylcol50_20degC", 1078.0, 3300.0, 3300.0, 0.405, 3.98e-6),
    "mineral_oil": ("MineralOil", 868.0, 2010.0, 2010.0, 0.14, 81.8e-6),
}
MEDIUM_KEYS = ("rho", "cp", "cv", "lambda", "nu")


def medium_props(v) -> dict:
    """{rho, cp, cv, lambda, nu, name, msl} of a medium value (a MEDIA name or {rho, cp[, cv, lambda, nu]})."""
    if isinstance(v, str):
        rec, *vals = MEDIA[v]
        return {"name": v, "msl": FHF + "Media." + rec + "()", **dict(zip(MEDIUM_KEYS, vals))}
    d = dict(v)
    d.setdefault("cv", d["cp"])
    d.setdefault("lambda", 1.0)
    d.setdefault("nu", 1.0)
    args = ", ".join(f"{k}={float(d[k])!r}" for k in MEDIUM_KEYS)
    return {"name": "custom", "msl": f"{FHF}Media.Medium({args})", **{k: float(d[k]) for k in MEDIUM_KEYS}}


MED = {"medium": P("1", "water", structural=True, type="medium", msl=False,
                   doc="one of " + ", ".join(MEDIA) + ", or {rho, cp, cv, lambda, nu} (SI; cv, lambda, nu optional)")}
FAB_F = {"flowPort_a": "fluid", "flowPort_b": "fluid"}
TP_VARS = {"T": "K", "T_a": "K", "T_b": "K", "dT": "K", "dp": "Pa", "V_flow": "m3/s", "Q_flow": "W"}
TP_INIT = {"T0": I("T", "K", 293.15, abs_T=True)}
MASS = {"m": P("kg", 0.0, min=0, structural=True,
               doc="mass of medium inside (0: no storage, T follows at once; it changes the equations: a rebuild)")}


def _vl(p):
    if p.get("V_flowLaminar") is not None:
        return p["V_flowLaminar"]
    return p["V_flowNominal"] * (0.5 if p["friction"] == "linear" else 0.1)


def _dpl(p):
    if p.get("dpLaminar") is not None:
        return p["dpLaminar"]
    return p["dpNominal"] * (0.5 if p["friction"] == "linear" else 0.01)


kind("pipe", FHF + "Components.Pipe", {**FAB_F, "heatPort": "heat"},
     {**MED, **MASS,
      "V_flowNominal": P("m3/s", gt=0, doc="volume flow of the nominal point"),
      "dpNominal": P("Pa", gt=0, doc="pressure drop at V_flowNominal"),
      "friction": P("1", "quadratic", type="enum", choices=["quadratic", "linear"], msl=False,
                    doc="quadratic: linear up to V_flowLaminar, then quadratic through the nominal point; linear: "
                        "dp = dpNominal V / V_flowNominal everywhere"),
      "V_flowLaminar": P("m3/s", None, gt=0, doc="end of the laminar range (default 0.1 x V_flowNominal)"),
      "dpLaminar": P("Pa", None, gt=0, doc="pressure drop at V_flowLaminar (default 0.01 x dpNominal)"),
      "h_g": P("m", 0.0, doc="height of flowPort_b over flowPort_a"),
      "frictionLoss": P("1", 0.0, min=0, max=1, doc="part of the friction power that heats the medium")},
     {**TP_VARS, "T_q": "K", "pressureDrop": "Pa", "Q_friction": "W"}, TP_INIT,
     text={"T0": "293.15"}, mods={"V_flowLaminar": _vl, "dpLaminar": _dpl}, cond_ports={"heatPort": "useHeatPort"},
     linear=False, domain="fluid",
     doc="dp = friction(V_flow) + rho g h_g; m cv dT/dt = H_a + H_b + Q (heatPort, connected: heat exchange at the "
         "outlet temperature T)")
kind("valve", FHF + "Components.Valve", {**FAB_F, "y": "in"},
     {**MED, **MASS, "Kv1": P("m3/s", gt=0, doc="flow at full opening y1 under dp0"),
      "dp0": P("Pa", gt=0, doc="standard pressure drop of Kv1"),
      "rho0": P("kg/m3", None, gt=0, doc="standard density of Kv1 (default: the medium's)"),
      "y1": P("1", 1.0, gt=0, doc="full opening"), "kv0": P("1", 0.01, gt=0, max=0.999, doc="leakage flow / Kv1 at y = 0"),
      "LinearCharacteristic": P("1", True, type="bool", doc="true: Kv linear in y; false: exponential"),
      "frictionLoss": P("1", 0.0, min=0, max=1)},
     TP_VARS, TP_INIT, text={"T0": "293.15"},
     mods={"rho0": lambda p: p["rho0"] if p.get("rho0") is not None else medium_props(p["medium"])["rho"]},
     linear=False, domain="fluid",
     doc="dp = dp0 rho/rho0 (V_flow/Kv)|V_flow/Kv|, Kv = Kv1 (kv0 + (1 - kv0) y/y1) (linear); input y = opening")
kind("volume_flow", FHF + "Sources.VolumeFlow", FAB_F,
     {**MED, **MASS, "constantVolumeFlow": P("m3/s", doc="volume flow from flowPort_a to flowPort_b")},
     TP_VARS, TP_INIT, text={"T0": "293.15"}, domain="fluid", doc="an ideal pump: V_flow = constantVolumeFlow")
kind("signal_volume_flow", FHF + "Sources.VolumeFlow", {**FAB_F, "volumeFlow": "in"}, {**MED, **MASS},
     TP_VARS, TP_INIT, text={"T0": "293.15", "useVolumeFlowInput": "true"}, linear=False, domain="fluid",
     doc="an ideal pump: V_flow = input volumeFlow [m3/s]")
kind("pressure_increase", FHF + "Sources.PressureIncrease", FAB_F,
     {**MED, **MASS, "constantPressureIncrease": P("Pa", doc="p_b - p_a")},
     TP_VARS, TP_INIT, text={"T0": "293.15"}, domain="fluid", doc="an ideal pump: p_b - p_a = constantPressureIncrease")
kind("signal_pressure_increase", FHF + "Sources.PressureIncrease", {**FAB_F, "pressureIncrease": "in"}, {**MED, **MASS},
     TP_VARS, TP_INIT, text={"T0": "293.15", "usePressureIncreaseInput": "true"}, linear=False, domain="fluid",
     doc="an ideal pump: p_b - p_a = input pressureIncrease [Pa]")
kind("ideal_pump", FHF + "Sources.IdealPump", {**FAB_F, "flange_a": "flange_r"},
     {**MED, **MASS, "wNominal": P("rad/s", gt=0, doc="nominal speed"),
      "dp0": P("Pa", gt=0, doc="pressure rise at zero flow and wNominal"),
      "V_flow0": P("m3/s", gt=0, doc="flow at zero pressure rise and wNominal")},
     {**TP_VARS, "w": "rad/s"}, TP_INIT, text={"T0": "293.15"}, linear=False, domain="fluid",
     islands=[["flowPort_a", "flowPort_b"], ["flange_a"]],
     doc="driven by its flange: p_b - p_a = dp0 (w/wN)^2 (1 - V_flow/(V_flow0 w/wN)); flange torque from tau w = dp V")
kind("ambient", FHF + "Sources.Ambient", {"flowPort": "fluid"},
     {**MED, "constantAmbientPressure": P("Pa", 0.0, doc="pressure (the loop's reference)"),
      "constantAmbientTemperature": P("K", 293.15, gt=0, abs_T=True, doc="temperature of medium flowing in")},
     {"T_port": "K"}, domain="fluid",
     doc="an open boundary: fixes p; medium flows in at its temperature, out at the port's")
kind("pressure_reference", FHF + "Sources.AbsolutePressure", {"flowPort": "fluid"},
     {**MED, "p": P("Pa", 0.0, doc="pressure at the port")}, domain="fluid",
     doc="sets the pressure level of a closed loop (no flow, no heat)")
kind("fluid_temperature_sensor", FHF + "Sensors.TemperatureSensor", {"flowPort": "fluid", "y": "out"}, MED,
     domain="fluid", doc="y = temperature at the port [K] (h/cp; mixed at junctions)")
kind("fluid_pressure_sensor", FHF + "Sensors.PressureSensor", {"flowPort": "fluid", "y": "out"}, MED,
     domain="fluid", doc="y = pressure at the port [Pa]")
kind("volume_flow_sensor", FHF + "Sensors.VolumeFlowSensor", {**FAB_F, "y": "out"}, MED, {"V_flow": "m3/s"},
     domain="fluid", doc="y = volume flow from flowPort_a to flowPort_b [m3/s] (no pressure drop)")

# ------------------------------------------------------------------------------------------- signals
SIG = {"offset": P("*", 0.0), "startTime": P("s", 0.0)}
kind("constant", BL + "Sources.Constant", {"y": "out"}, {"k": P("*")}, doc="y = k")
kind("step", BL + "Sources.Step", {"y": "out"}, {"height": P("*", 1.0), **SIG}, doc="y = offset + (t < startTime ? 0 : height)")
kind("ramp", BL + "Sources.Ramp", {"y": "out"}, {"height": P("*", 1.0), "duration": P("s", min=0), **SIG},
     doc="y = offset + height*clip((t - startTime)/duration, 0, 1)")
kind("sine", BL + "Sources.Sine", {"y": "out"}, {"amplitude": P("*", 1.0), "f": P("Hz", gt=0), "phase": P("rad", 0.0), **SIG},
     doc="y = offset + (t < startTime ? 0 : amplitude*sin(2*pi*f*(t - startTime) + phase))")
kind("pulse", BL + "Sources.Pulse", {"y": "out"},
     {"amplitude": P("*", 1.0), "width": P("%", 50.0, gt=0, max=100), "period": P("s", gt=0),
      "nperiod": P("1", -1, type="int"), **SIG}, doc="y = offset + amplitude during the first width % of each period")
kind("table", BL + "Sources.CombiTimeTable", {"y": "out"},
     {"table": P("1", structural=True, type="table", doc="[[t, y], ...] with t increasing; linear between points, "
                                                           "held after the last"),
      "startTime": P("s", 0.0)},
     text={"smoothness": "Modelica.Blocks.Types.Smoothness.LinearSegments",
           "extrapolation": "Modelica.Blocks.Types.Extrapolation.HoldLastPoint"}, doc="piecewise linear in time")
kind("gain", BL + "Math.Gain", {"u": "in", "y": "out"}, {"k": P("1")}, doc="y = k*u")
kind("add", BL + "Math.Add", {"u1": "in", "u2": "in", "y": "out"}, {"k1": P("1", 1.0), "k2": P("1", 1.0)},
     doc="y = k1*u1 + k2*u2")
kind("feedback", BL + "Math.Feedback", {"u1": "in", "u2": "in", "y": "out"}, doc="y = u1 - u2")
kind("product", BL + "Math.Product", {"u1": "in", "u2": "in", "y": "out"}, linear=False, doc="y = u1*u2")
kind("division", BL + "Math.Division", {"u1": "in", "u2": "in", "y": "out"}, linear=False, doc="y = u1/u2")
kind("integrator", BL + "Continuous.Integrator", {"u": "in", "y": "out"}, {"k": P("1", 1.0), "y_start": P("*", 0.0)},
     text={"initType": INIT_STATE}, block_states={"y": "y_start"}, doc="dy/dt = k*u, y(0) = y_start")
kind("derivative", BL + "Continuous.Derivative", {"u": "in", "y": "out"}, {"k": P("1", 1.0), "T": P("s", gt=0)},
     text={"initType": INIT_STATE}, block_states={"x": 0.0}, doc="y = k*s/(T*s + 1)*u, internal state x(0) = 0")
kind("first_order", BL + "Continuous.FirstOrder", {"u": "in", "y": "out"},
     {"k": P("1", 1.0), "T": P("s", gt=0), "y_start": P("*", 0.0)}, text={"initType": INIT_STATE},
     block_states={"y": "y_start"}, doc="T*dy/dt = k*u - y")
kind("second_order", BL + "Continuous.SecondOrder", {"u": "in", "y": "out"},
     {"k": P("1", 1.0), "w": P("rad/s", gt=0), "D": P("1", min=0), "y_start": P("*", 0.0), "yd_start": P("*", 0.0)},
     text={"initType": INIT_STATE}, block_states={"y": "y_start", "yd": "yd_start"},
     doc="y = k/((s/w)^2 + 2*D*s/w + 1)*u")
kind("transfer_function", BL + "Continuous.TransferFunction", {"u": "in", "y": "out"},
     {"b": P("1", structural=True, type="array", doc="numerator, highest power of s first"),
      "a": P("1", structural=True, type="array", doc="denominator, highest power first; len(b) <= len(a)")},
     text={"initType": INIT_STATE}, block_states={"x": 0.0}, doc="y = (b[1]*s^n + ...)/(a[1]*s^n + ...)*u, states 0 at start")
kind("pi", BL + "Continuous.PI", {"u": "in", "y": "out"}, {"k": P("1"), "T": P("s", gt=0)},
     text={"initType": INIT_STATE}, block_states={"x": 0.0}, doc="y = k*(u + x), dx/dt = u/T (x(0) = 0)")
kind("pid", BL + "Continuous.PID", {"u": "in", "y": "out"},
     {"k": P("1"), "Ti": P("s", gt=0), "Td": P("s", 0.0, min=0), "Nd": P("1", 10.0, gt=0)},
     text={"initType": INIT_STATE}, block_states={"I.y": 0.0, "D.x": 0.0},
     doc="y = k*(u + 1/(Ti*s)*u + Td*s/(Td/Nd*s + 1)*u); integral and derivative states 0 at start")
kind("lim_pid", BL + "Continuous.LimPID", {"u_s": "in", "u_m": "in", "y": "out"},
     {"controllerType": P("1", "PID", structural=True, type="enum", choices=["P", "PI", "PD", "PID"], msl=False),
      "k": P("1", min=0), "Ti": P("s", 0.5, gt=0), "Td": P("s", 0.1, min=0), "yMax": P("*"), "yMin": P("*", None),
      "wp": P("1", 1.0, min=0), "wd": P("1", 0.0, min=0), "Ni": P("1", 0.9, gt=0), "Nd": P("1", 10.0, gt=0)},
     text={"initType": INIT_STATE}, linear=False,
     mods={"controllerType": lambda p: "Modelica.Blocks.Types.SimpleController." + p["controllerType"],
           "yMin": lambda p: -p["yMax"] if p.get("yMin") is None else p["yMin"]},
     doc="PID on wp*u_s - u_m (D on wd*u_s - u_m) with output limits yMin..yMax (default -yMax) and anti-windup Ni; "
         "k >= 0: give the error sign by wiring u_s (setpoint) and u_m (measurement)")
kind("limiter", BL + "Nonlinear.Limiter", {"u": "in", "y": "out"}, {"uMax": P("*"), "uMin": P("*", None)},
     linear=False, mods={"uMin": lambda p: -p["uMax"] if p.get("uMin") is None else p["uMin"]},
     doc="y = clip(u, uMin, uMax) (uMin default -uMax)")
kind("dead_zone", BL + "Nonlinear.DeadZone", {"u": "in", "y": "out"}, {"uMax": P("*"), "uMin": P("*", None)},
     linear=False, mods={"uMin": lambda p: -p["uMax"] if p.get("uMin") is None else p["uMin"]},
     doc="y = 0 between uMin and uMax, else u - limit")
kind("slew_rate_limiter", BL + "Nonlinear.SlewRateLimiter", {"u": "in", "y": "out"},
     {"Rising": P("1/s", 1.0, gt=0), "Falling": P("1/s", None), "Td": P("s", 0.001, gt=0), "y_start": P("*", 0.0)},
     text={"initType": INIT_STATE}, linear=False, block_states={"y": "y_start"},
     mods={"Falling": lambda p: -p["Rising"] if p.get("Falling") is None else p["Falling"]},
     doc="dy/dt = clip((u - y)/Td, Falling, Rising) (Falling default -Rising)")

# ------------------------------------------------------------------------------------------- three-phase (Polyphase, m = 3)
# A plug is three pins (pin[1..3]); connecting two plugs connects phase k to phase k. Values are given once and
# applied to every phase. Phase k of a source lags by (k - 1) 2 pi/3 (MSL symmetricOrientation).
PP = "Modelica.Electrical.Polyphase."
TWO_PLUG = {"plug_p": "plug", "plug_n": "plug"}
M3 = {"m": "3"}
SQ2 = 2 ** 0.5


VI3 = {**{f"v[{k}]": "V" for k in (1, 2, 3)}, **{f"i[{k}]": "A" for k in (1, 2, 3)}}


def _phases(x):
    return [float(x)] * 3


def _sym(phase):
    import math
    return [float(phase) - k * 2 * math.pi / 3 for k in range(3)]


kind("ac_voltage", PP + "Sources.SineVoltage", TWO_PLUG,
     {"V_rms": P("V", gt=0, msl=False, doc="RMS voltage per phase (plug_p to plug_n)"), "f": P("Hz", gt=0),
      "phase": P("rad", 0.0, doc="phase of phase 1 at t = 0 (sine)")},
     VI3, text=M3,
     mods={"V": lambda p: _phases(SQ2 * p["V_rms"]), "f": lambda p: _phases(p["f"]), "phase": lambda p: _sym(p["phase"])},
     doc="symmetric three-phase sine voltages v_k = sqrt(2) V_rms sin(2 pi f t + phase - (k-1) 2 pi/3); vars v[k], i[k] "
         "per phase")
kind("star", PP + "Basic.Star", {"plug_p": "plug", "pin_n": "pin"}, text=M3,
     islands=[["plug_p", "pin_n"]], doc="joins the three phases at pin_n (star point)")
kind("delta", PP + "Basic.Delta", TWO_PLUG, text=M3, islands=[["plug_p", "plug_n"]],
     doc="plug_n phase k to plug_p phase k+1 (delta connection)")
kind("plug_to_pin", PP + "Basic.PlugToPin_p", {"plug_p": "plug", "pin_p": "pin"},
     {"k": P("1", 1, type="int", structural=True, min=1, max=3, doc="the phase")}, text=M3, islands=[["plug_p", "pin_p"]],
     doc="phase k of the plug as a single pin (the other phases carry no current)")
kind("ac_resistor", PP + "Basic.Resistor", TWO_PLUG, {"R": P("Ohm", min=0, doc="per phase")}, VI3,
     text=M3, mods={"R": lambda p: _phases(p["R"])}, doc="v[k] = R i[k]")
kind("ac_inductor", PP + "Basic.Inductor", TWO_PLUG, {"L": P("H", gt=0, doc="per phase")}, VI3,
     {"i0": I("i", "A", 0.0, phases=3)}, text=M3, mods={"L": lambda p: _phases(p["L"])}, doc="v[k] = L di[k]/dt")
kind("ac_capacitor", PP + "Basic.Capacitor", TWO_PLUG, {"C": P("F", gt=0, doc="per phase")}, VI3,
     {"v0": I("v", "V", 0.0, phases=3)}, text=M3, mods={"C": lambda p: _phases(p["C"])}, doc="i[k] = C dv[k]/dt")
kind("ac_current_rms", PP + "Sensors.CurrentQuasiRMSSensor", {**TWO_PLUG, "I": "out"}, text=M3,
     linear=False, doc="I = sqrt(sum i[k]^2 / 3) (quasi RMS; the RMS current for a symmetric sine)")
kind("ac_voltage_rms", PP + "Sensors.VoltageQuasiRMSSensor", {**TWO_PLUG, "V": "out"}, text=M3,
     linear=False, doc="V = sqrt(sum v[k]^2 / 3), v = plug_p - plug_n (no current)")
kind("ac_power", MACH_ + "Sensors.ElectricalPowerSensor", {"plug_p": "plug", "plug_ni": "plug", "plug_nv": "plug",
                                                          "P": "out", "Q": "out"},
     linear=False, islands=[["plug_p", "plug_ni", "plug_nv"]],
     doc="current from plug_p to plug_ni, voltage to plug_nv (e.g. a star point): P = 3/2 (v_a i_a + v_b i_b), "
         "Q = 3/2 (v_b i_a - v_a i_b) (space phasors)")

# ------------------------------------------------------------------------------------------- machines
def _ref_or(p, name, pref, default):
    """A loss record's reference: the given one, else (when the loss is on) the MSL default as a literal."""
    if p.get(name) is not None:
        return p[name]
    return default if (p.get(pref) or 0) > 0 else None


def _dcpm_vi(p):
    from .network import dcpm_vi_nominal
    return dcpm_vi_nominal(p)


DCPM_HEAT = ["heatPortArmature", "heatPortCore", "heatPortStrayLoad", "heatPortFriction", "heatPortBrush",
             "heatPortPermanentMagnet"]
MACH = "Modelica.Electrical.Machines."
kind("dc_machine", MACH + "BasicMachines.DCMachines.DC_PermanentMagnet",
     {"pin_ap": "pin", "pin_an": "pin", "flange": "flange_r", **{h: "heat" for h in DCPM_HEAT}},
     {"VaNominal": P("V", gt=0, doc="nominal armature voltage"), "IaNominal": P("A", doc="nominal armature current (> 0 motor)"),
      "wNominal": P("rad/s", gt=0, doc="nominal speed"), "TaNominal": P("K", 293.15, gt=0, abs_T=True),
      "Ra": P("Ohm", gt=0, doc="armature resistance at TaRef"), "TaRef": P("K", 293.15, gt=0, abs_T=True),
      "alpha20a": P("1/K", 0.0, doc="temperature coefficient of Ra at 20 degC (copper 3.9e-3)"),
      "La": P("H", gt=0, doc="armature inductance"), "Jr": P("kg.m2", gt=0, doc="rotor inertia"),
      "TaOperational": P("K", 293.15, gt=0, abs_T=True, doc="armature temperature without heat ports"),
      # the loss records derive final parameters (tauRef, GcRef, wLinear, ...) that OpenModelica 1.27.1 folds at
      # compile time (mochi, dc_machine_losses): their inputs are structural, references written as literals
      "PRef_friction": P("W", 0.0, min=0, structural=True, msl="frictionParameters.PRef", doc="friction loss at wRef_friction"),
      "wRef_friction": P("rad/s", None, gt=0, structural=True, msl="frictionParameters.wRef", doc="default wNominal"),
      "power_w_friction": P("1", 2.0, gt=0, structural=True, msl="frictionParameters.power_w", doc="friction torque ~ w^(power_w - 1)"),
      "PRef_core": P("W", 0.0, min=0, structural=True, msl="coreParameters.PRef", doc="core loss at VRef_core"),
      "VRef_core": P("V", None, gt=0, structural=True, msl="coreParameters.VRef", doc="default the nominal induced voltage"),
      "PRef_strayLoad": P("W", 0.0, min=0, structural=True, msl="strayLoadParameters.PRef", doc="stray-load loss at IRef, wRef"),
      "IRef_strayLoad": P("A", None, gt=0, msl="strayLoadParameters.IRef", doc="default IaNominal"),
      "wRef_strayLoad": P("rad/s", None, gt=0, structural=True, msl="strayLoadParameters.wRef", doc="default wNominal"),
      "power_w_strayLoad": P("1", 1.0, gt=0, msl="strayLoadParameters.power_w"),
      "V_brush": P("V", 0.0, min=0, msl="brushParameters.V", doc="brush voltage drop above ILinear_brush"),
      "ILinear_brush": P("A", None, gt=0, msl="brushParameters.ILinear", doc="default 0.01 IaNominal")},
     {"va": "V", "ia": "A", "wMechanical": "rad/s", "phiMechanical": "rad", "tauElectrical": "N.m", "tauShaft": "N.m",
      "Ta": "K", "Ra_actual": "Ohm", "powerArmature": "W", "powerMechanical": "W", "lossPowerTotal": "W",
      "lossPowerArmature": "W", "lossPowerCore": "W", "lossPowerStrayLoad": "W", "lossPowerFriction": "W",
      "lossPowerBrush": "W"},
     {"ia0": I("ia", "A", 0.0), "w0": I("wMechanical", "rad/s", 0.0), "phi0": I("phiMechanical", "rad", 0.0)},
     cond_ports={h: "useThermalPort" for h in DCPM_HEAT}, port_groups={"heatPort": DCPM_HEAT},
     mods={"wRef_friction": lambda p: _ref_or(p, "wRef_friction", "PRef_friction", p["wNominal"]),
           "VRef_core": lambda p: _ref_or(p, "VRef_core", "PRef_core", _dcpm_vi(p)),
           "wRef_strayLoad": lambda p: _ref_or(p, "wRef_strayLoad", "PRef_strayLoad", p["wNominal"])},
     var_paths={"Ta": "ra.T_heatPort", "Ra_actual": "ra.R_actual",
                **{v: "powerBalance." + v for v in ("powerArmature", "powerMechanical", "lossPowerTotal",
                                                    "lossPowerArmature", "lossPowerCore", "lossPowerStrayLoad",
                                                    "lossPowerFriction", "lossPowerBrush")}},
     islands=[["pin_ap", "pin_an"], ["flange"], DCPM_HEAT], linear=False,
     doc="MSL DC_PermanentMagnet: k = (VaNominal - Ra IaNominal)/wNominal; losses 0 unless given; heatPort (all "
         "losses at one node) or all six heatPort* when connected (Ra then at the armature port's T), else Ta = TaOperational")

# ------------------------------------------------------------------------------------------- AC machines (m = 3)
# Electrical.Machines.BasicMachines: IM_SquirrelCage and SM_PermanentMagnet (space-phasor models, transient), each with
# a TerminalBox (Y or D) in a composite: ports plug (the supply), flange (the shaft) and heat ports when connected.
# Loss records are structural (their final parameters are compiled in: B18); references default to MSL's bindings
# and become literals when a loss is on.
def _w_sync(p):
    import math
    return 2 * math.pi * p["fsNominal"] / p["p"]


def _w_el(p):
    import math
    return 2 * math.pi * p["fsNominal"]


def _need(p, name, pref):
    """A reference that has no MSL default (only a start value): required when the loss is on (validated)."""
    return p.get(name) if (p.get(pref) or 0) > 0 else None


AC_STATOR = {
    "p": P("1", type="int", structural=True, min=1, doc="pole pairs"),
    "fsNominal": P("Hz", gt=0, doc="nominal frequency"),
    "Rs": P("Ohm", min=0, doc="stator resistance per phase at TsRef"), "TsRef": P("K", 293.15, gt=0, abs_T=True),
    "alpha20s": P("1/K", 0.0, doc="temperature coefficient of Rs at 20 degC"),
    "Lssigma": P("H", gt=0, doc="stator stray inductance per phase"),
    "Lszero": P("H", None, gt=0, doc="stator zero-sequence inductance (default Lssigma)"),
    "Jr": P("kg.m2", gt=0, doc="rotor inertia"),
    "TsOperational": P("K", 293.15, gt=0, abs_T=True, doc="stator winding temperature without heat ports"),
    "PRef_friction": P("W", 0.0, min=0, structural=True, msl="frictionParameters.PRef"),
    "wRef_friction": P("rad/s", None, gt=0, structural=True, msl="frictionParameters.wRef", doc="default 2 pi fsNominal/p"),
    "power_w_friction": P("1", 2.0, gt=0, structural=True, msl="frictionParameters.power_w"),
    "PRef_statorCore": P("W", 0.0, min=0, structural=True, msl="statorCoreParameters.PRef"),
    "VRef_statorCore": P("V", None, gt=0, structural=True, msl="statorCoreParameters.VRef",
                         doc="RMS phase voltage of PRef_statorCore (required with it)"),
    "wRef_statorCore": P("rad/s", None, gt=0, structural=True, msl="statorCoreParameters.wRef", doc="default 2 pi fsNominal"),
    "PRef_strayLoad": P("W", 0.0, min=0, structural=True, msl="strayLoadParameters.PRef"),
    "IRef_strayLoad": P("A", None, gt=0, structural=True, msl="strayLoadParameters.IRef",
                        doc="RMS current of PRef_strayLoad (required with it)"),
    "wRef_strayLoad": P("rad/s", None, gt=0, structural=True, msl="strayLoadParameters.wRef", doc="default 2 pi fsNominal/p"),
    "power_w_strayLoad": P("1", 1.0, gt=0, structural=True, msl="strayLoadParameters.power_w"),
}
AC_STATOR_MODS = {
    "wRef_friction": lambda p: _ref_or(p, "wRef_friction", "PRef_friction", _w_sync(p)),
    "wRef_statorCore": lambda p: _ref_or(p, "wRef_statorCore", "PRef_statorCore", _w_el(p)),
    "VRef_statorCore": lambda p: _need(p, "VRef_statorCore", "PRef_statorCore"),
    "IRef_strayLoad": lambda p: _need(p, "IRef_strayLoad", "PRef_strayLoad"),
    "wRef_strayLoad": lambda p: _ref_or(p, "wRef_strayLoad", "PRef_strayLoad", _w_sync(p)),
}
AC_VARS = {"wMechanical": "rad/s", "phiMechanical": "rad", "tauElectrical": "N.m", "tauShaft": "N.m",
           **{f"is[{k}]": "A" for k in (1, 2, 3)}, **{f"vs[{k}]": "V" for k in (1, 2, 3)},
           "powerStator": "W", "powerMechanical": "W", "lossPowerTotal": "W", "lossPowerStatorWinding": "W",
           "lossPowerStatorCore": "W", "lossPowerRotorCore": "W", "lossPowerStrayLoad": "W", "lossPowerFriction": "W"}
PB = ("powerStator", "powerMechanical", "lossPowerTotal", "lossPowerStatorWinding", "lossPowerStatorCore",
      "lossPowerRotorCore", "lossPowerStrayLoad", "lossPowerFriction", "lossPowerRotorWinding", "lossPowerPermanentMagnet")
AC_INITS = {"w0": I("wMechanical", "rad/s", 0.0), "phi0": I("phiMechanical", "rad", 0.0),
            "is0": I("is", "A", 0.0, phases=3)}
IM_HEAT = ["heatPortStatorWinding1", "heatPortStatorWinding2", "heatPortStatorWinding3", "heatPortStatorCore",
           "heatPortRotorCore", "heatPortStrayLoad", "heatPortFriction", "heatPortRotorWinding"]
PM_HEAT = [h for h in IM_HEAT if h != "heatPortRotorWinding"] + ["heatPortPermanentMagnet"]
PMD_HEAT = PM_HEAT + ["heatPortRotorWinding"]
MB = MACH_ + "BasicMachines."


def _heat_path(h):
    return "thermalPort." + (f"heatPortStatorWinding[{h[-1]}]" if h.startswith("heatPortStatorWinding") else h)


kind("im_core", MB + "InductionMachines.IM_SquirrelCage",
     {"plug_sp": "plug", "plug_sn": "plug", "flange": "flange_r", **{h: "heat" for h in IM_HEAT}},
     {**AC_STATOR, "Lm": P("H", gt=0, doc="main field inductance per phase"),
      "Lrsigma": P("H", gt=0, doc="rotor stray inductance per phase (stator side)"),
      "Rr": P("Ohm", min=0, doc="rotor resistance per phase (stator side) at TrRef"),
      "TrRef": P("K", 293.15, gt=0, abs_T=True), "alpha20r": P("1/K", 0.0),
      "TrOperational": P("K", 293.15, gt=0, abs_T=True)},
     {**AC_VARS, "lossPowerRotorWinding": "W", "ir[1]": "A", "ir[2]": "A"},
     {**AC_INITS, "ir0": I("ir", "A", 0.0, phases=2)},
     mods=AC_STATOR_MODS, cond_ports={h: "useThermalPort" for h in IM_HEAT}, hidden=True, linear=False,
     var_paths={v: "powerBalance." + v for v in PB}, islands=[["plug_sp", "plug_sn"], ["flange"], IM_HEAT])
for _n, _h, _dc in (("pmsm_core", PM_HEAT, False), ("pmsm_damper_core", PMD_HEAT, True)):
    kind(_n, MB + "SynchronousMachines.SM_PermanentMagnet",
         {"plug_sp": "plug", "plug_sn": "plug", "flange": "flange_r", **{h: "heat" for h in _h}},
         {**AC_STATOR, "VsOpenCircuit": P("V", gt=0, doc="open-circuit RMS voltage per phase at fsNominal"),
          "Lmd": P("H", gt=0, doc="main field inductance, d axis"), "Lmq": P("H", gt=0, doc="main field inductance, q axis"),
          "PRef_pm": P("W", 0.0, min=0, structural=True, msl="permanentMagnetLossParameters.PRef"),
          "IRef_pm": P("A", None, gt=0, structural=True, msl="permanentMagnetLossParameters.IRef"),
          "wRef_pm": P("rad/s", None, gt=0, structural=True, msl="permanentMagnetLossParameters.wRef"),
          **({"Lrsigmad": P("H", gt=0, doc="damper stray inductance, d axis"),
              "Lrsigmaq": P("H", None, gt=0, doc="q axis (default Lrsigmad)"),
              "Rrd": P("Ohm", gt=0, doc="damper resistance, d axis"), "Rrq": P("Ohm", None, gt=0, doc="default Rrd"),
              "TrRef": P("K", 293.15, gt=0, abs_T=True), "alpha20r": P("1/K", 0.0),
              "TrOperational": P("K", 293.15, gt=0, abs_T=True)} if _dc else {})},
         {**AC_VARS, "lossPowerPermanentMagnet": "W", **({"lossPowerRotorWinding": "W", "ir[1]": "A", "ir[2]": "A"} if _dc else {})},
         {**AC_INITS, **({"ir0": I("ir", "A", 0.0, phases=2)} if _dc else {})},
         text={"useDamperCage": "true" if _dc else "false"},
         mods={**AC_STATOR_MODS, "IRef_pm": lambda p: _need(p, "IRef_pm", "PRef_pm"),
               "wRef_pm": lambda p: _ref_or(p, "wRef_pm", "PRef_pm", _w_sync(p))},
         cond_ports={h: "useThermalPort" for h in _h}, hidden=True, linear=False,
         var_paths={v: "powerBalance." + v for v in PB}, islands=[["plug_sp", "plug_sn"], ["flange"], _h])
kind("terminal_box", MACH_ + "Utilities.TerminalBox", {"plug_sp": "plug", "plug_sn": "plug", "plugSupply": "plug",
                                                      "starpoint": "pin"},
     {"terminal": P("1", "Y", structural=True, type="enum", choices=["Y", "D"], msl=False)},
     mods={"terminalConnection": lambda p: '"' + p["terminal"] + '"'}, hidden=True,
     islands=[["plug_sp", "plug_sn", "plugSupply", "starpoint"]])
kind("vf_controller", MACH_ + "Utilities.VfController", {"u": "in", "y": "vout"},
     {"VNominal": P("V", gt=0), "fNominal": P("Hz", gt=0)}, {"x": "rad", "amplitude": "V"}, text={"m": "3"},
     hidden=True, linear=False)
kind("signal_ac_voltage", PP + "Sources.SignalVoltage", {**TWO_PLUG, "v": "vin"}, vars=VI3, text=M3, hidden=True)
kind("signal_ac_current", PP + "Sources.SignalCurrent", {**TWO_PLUG, "i": "vin"}, vars=VI3, text=M3, hidden=True)
kind("dq_to_three_phase", MACH_ + "Utilities.DQToThreePhase", {"d": "in", "q": "in", "phi": "in", "y": "vout"},
     {"p": P("1", type="int", structural=True, min=1)}, hidden=True, linear=False)


def _machine(name, core, heat, extra_doc):
    k = KINDS[core]
    params = {**{n: s for n, s in k["params"].items()}, "terminal": KINDS["terminal_box"]["params"]["terminal"]}
    hp = {h: f"m.{h}" for h in heat}
    composite(name, {"m": (core, {n: n for n in k["params"]}), "tb": ("terminal_box", {"terminal": "terminal"})},
              {"plug": "tb.plugSupply", "flange": "m.flange", "starpoint": "tb.starpoint", **hp}, params,
              {v: (f"m.{v}", u) for v, u in k["vars"].items()},
              {i: f"m.{i}" for i in k["inits"]},
              conns=[("m.plug_sp", "tb.plug_sp"), ("m.plug_sn", "tb.plug_sn")], port_groups={"heatPort": list(hp)},
              linear=False, domain="electrical",
              doc=f"{extra_doc}; plug = the supply (terminal Y or D), flange = shaft, starpoint (Y; may stay open; "
                  f"ground it with a current drive); heatPort (all losses) or the "
                  f"{len(heat)} heat ports when connected; currents start at 0 (is0, ir0 where free), w0, phi0")


_machine("induction_machine", "im_core", IM_HEAT,
         "MSL IM_SquirrelCage (space phasors): Rs, Lssigma, Lm, Lrsigma, Rr per phase (equivalent circuit), p pole pairs")
_machine("pmsm", "pmsm_core", PM_HEAT,
         "MSL SM_PermanentMagnet without damper cage (needs a drive): VsOpenCircuit, Lmd, Lmq; psiM = sqrt(2) "
         "VsOpenCircuit / (2 pi fsNominal)")
_machine("pmsm_damper", "pmsm_damper_core", PMD_HEAT,
         "MSL SM_PermanentMagnet with damper cage (line start): also Lrsigmad, Rrd (Lrsigmaq, Rrq)")
composite("three_phase_grid",
          {"src": ("ac_voltage", {"V_rms": "V_rms", "f": "f", "phase": "phase"}), "st": ("star", {}), "g": ("ground", {})},
          {"plug": "src.plug_p"}, {"V_rms": P("V", gt=0, doc="RMS phase voltage"), "f": P("Hz", gt=0),
                                   "phase": P("rad", 0.0)},
          {**{f"i[{k}]": (f"src.i[{k}]", "A") for k in (1, 2, 3)}, **{f"v[{k}]": (f"src.v[{k}]", "V") for k in (1, 2, 3)}},
          conns=[("src.plug_n", "st.plug_p"), ("st.pin_n", "g.p")], domain="electrical",
          doc="a symmetric grid: star point grounded; plug = the three phases")
composite("vf_inverter",
          {"vf": ("vf_controller", {"VNominal": "VNominal", "fNominal": "fNominal"}), "sv": ("signal_ac_voltage", {}),
           "st": ("star", {}), "g": ("ground", {})},
          {"plug": "sv.plug_p", "f": "vf.u"},
          {"VNominal": P("V", gt=0, doc="RMS phase voltage at fNominal"), "fNominal": P("Hz", gt=0)},
          {**{f"i[{k}]": (f"sv.i[{k}]", "A") for k in (1, 2, 3)}, **{f"v[{k}]": (f"sv.v[{k}]", "V") for k in (1, 2, 3)},
           "amplitude": ("vf.amplitude", "V")},
          conns=[("vf.y", "sv.v"), ("sv.plug_n", "st.plug_p"), ("st.pin_n", "g.p")], linear=False, domain="electrical",
          doc="an ideal V/f inverter: input f [Hz]; phase voltages sqrt(2) VNominal min(|f|/fNominal, 1) sin(2 pi "
              "integral f + orientation)")
composite("pmsm_current_drive",
          {"dq": ("dq_to_three_phase", {"p": "p"}), "sc": ("signal_ac_current", {}), "st": ("star", {}),
           "g": ("ground", {}), "ang": ("angle_sensor", {})},
          {"plug": "sc.plug_n", "id": "dq.d", "iq": "dq.q", "flange": "ang.flange"},
          {"p": P("1", type="int", structural=True, min=1, doc="pole pairs of the motor")},
          {**{f"i[{k}]": (f"sc.i[{k}]", "A") for k in (1, 2, 3)}, **{f"v[{k}]": (f"sc.v[{k}]", "V") for k in (1, 2, 3)}},
          conns=[("dq.y", "sc.i"), ("ang.phi", "dq.phi"), ("sc.plug_p", "st.plug_p"), ("st.pin_n", "g.p")],
          linear=False, domain="electrical",
          doc="ideal field-oriented current control (MSL SMPM_CurrentSource): inputs id, iq [A RMS, rotor frame]; "
              "flange = the motor shaft (angle); ground the motor's starpoint (current sources leave it floating); "
              "torque = 3/2 p psiM sqrt(2) iq (+ (Ld - Lq) id iq)")

# ------------------------------------------------------------------------------------------- quasi-static (V3.4)
# Electrical.QuasiStatic and Magnetic.QuasiStatic.FundamentalWave: complex RMS phasors at the frequency of the
# circuit's one source (its reference angle gamma); the machines' electrical side is algebraic, the mechanics are
# not. Port types qs_plug / qs_pin; QS and transient circuits never join.
QSP = "Modelica.Electrical.QuasiStatic.Polyphase."
QSS = "Modelica.Electrical.QuasiStatic.SinglePhase."
QFW = "Modelica.Magnetic.QuasiStatic.FundamentalWave."
QS_TWO = {"plug_p": "qs_plug", "plug_n": "qs_plug"}
QS_SRC_VARS = {"P_total": "W", "Q_total": "var"}
QS_SOURCES = ("qs_ac_voltage", "qs_variable_voltage", "qs_reference_current")     # Connections.root
QS_GAMMA0 = {"gamma0": I("gamma", "rad", 0.0)}
QS_DRIVE_RP = 1e6       # Ohm per phase across the QS current drive's source (MSL's example: 1e5)


def _qs_phi(phase):
    import math
    return [float(phase) - k * 2 * math.pi / 3 for k in range(3)]


kind("qs_ground", QSS + "Basic.Ground", {"pin": "qs_pin"},
     doc="pin.v = 0 (complex); every quasi-static circuit needs one")
kind("qs_star", QSP + "Basic.Star", {"plug_p": "qs_plug", "pin_n": "qs_pin"}, text=M3, islands=[["plug_p", "pin_n"]],
     doc="joins the three phases at pin_n (quasi-static)")
kind("qs_ac_voltage", QSP + "Sources.VoltageSource", QS_TWO,
     {"V_rms": P("V", gt=0, msl=False, doc="RMS voltage per phase (plug_p to plug_n)"), "f": P("Hz", gt=0),
      "phase": P("rad", 0.0, msl=False, doc="phase of phase 1 (cosine reference)")},
     QS_SRC_VARS, QS_GAMMA0, text=M3, mods={"V": lambda p: _phases(p["V_rms"]), "phi": lambda p: _qs_phi(p["phase"])},
     doc="symmetric QS source V_k = V_rms e^(j (phase - (k-1) 2 pi/3)) at f; sets the circuit's frequency; "
         "P_total, Q_total absorbed (< 0 delivering)")
kind("qs_variable_voltage", QSP + "Sources.VariableVoltageSource", {**QS_TWO, "f": "in", "V": "cvin"}, {},
     QS_SRC_VARS, QS_GAMMA0, text=M3, hidden=True)
kind("qs_reference_current", QSP + "Sources.ReferenceCurrentSource", {**QS_TWO, "gamma": "in", "I": "cvin"}, {},
     QS_SRC_VARS, text=M3, hidden=True)
kind("qs_resistor_core", QSP + "Basic.Resistor", QS_TWO, {"R": P("Ohm", gt=0, msl=False)}, {}, text=M3,
     mods={"R_ref": lambda p: _phases(p["R"])}, hidden=True)
kind("qs_vf_controller", QFW + "Utilities.VfController", {"u": "in", "y": "cvout"},
     {"VNominal": P("V", gt=0), "fNominal": P("Hz", gt=0)}, {"amplitude": "V"}, text=M3, hidden=True, linear=False)
kind("qs_current_controller", QFW + "Utilities.CurrentController",
     {"id_rms": "in", "iq_rms": "in", "phi": "in", "I": "cvout", "gamma": "out"},
     {"p": P("1", type="int", structural=True, min=1)}, text=M3, hidden=True, linear=False)
kind("qs_current_rms", QSP + "Sensors.CurrentQuasiRMSSensor", {**QS_TWO, "I": "out"}, text=M3, linear=False,
     doc="I = mean_k |I_k| (the RMS current of a symmetric system), current from plug_p to plug_n")
kind("qs_voltage_rms", QSP + "Sensors.VoltageQuasiRMSSensor", {**QS_TWO, "V": "out"}, text=M3, linear=False,
     doc="V = mean_k |V_k|, V = plug_p - plug_n (no current)")
kind("qs_power_core", QSP + "Sensors.PowerSensor",
     {"currentP": "qs_plug", "currentN": "qs_plug", "voltageP": "qs_plug", "voltageN": "qs_plug"}, {},
     {"P": "W", "Q": "var"}, text=M3, hidden=True, linear=False,
     var_paths={"P": "apparentPower.re", "Q": "apparentPower.im"},
     islands=[["currentP", "currentN", "voltageP", "voltageN"]])
composite("qs_power", {"s": ("qs_power_core", {})}, {"plug_p": "s.currentP", "plug_ni": "s.currentN",
                                                     "plug_nv": "s.voltageN"},
          {}, {"P": ("s.P", "W"), "Q": ("s.Q", "var")}, conns=[("s.voltageP", "s.currentP")], linear=False,
          domain="quasi-static",
          doc="current plug_p to plug_ni, voltage plug_p to plug_nv (a star): P + jQ = sum V_k conj(I_k)")

QS_STATOR = {k_: v_ for k_, v_ in AC_STATOR.items() if k_ != "Lszero"}
QS_VARS = {"wMechanical": "rad/s", "phiMechanical": "rad", "tauElectrical": "N.m", "tauShaft": "N.m",
           **{f"abs_is[{k}]": "A" for k in (1, 2, 3)}, **{f"abs_vs[{k}]": "V" for k in (1, 2, 3)},
           "Ps_total": "W", "Qs_total": "var",
           "powerStator": "W", "powerMechanical": "W", "lossPowerTotal": "W", "lossPowerStatorWinding": "W",
           "lossPowerStatorCore": "W", "lossPowerRotorCore": "W", "lossPowerStrayLoad": "W", "lossPowerFriction": "W"}
QS_INITS = {"w0": I("wMechanical", "rad/s", 0.0), "phi0": I("phiMechanical", "rad", 0.0)}
kind("qs_im_core", QFW + "BasicMachines.InductionMachines.IM_SquirrelCage",
     {"plug_sp": "qs_plug", "plug_sn": "qs_plug", "flange": "flange_r", **{h: "heat" for h in IM_HEAT}},
     {**QS_STATOR, "Lm": P("H", gt=0, doc="main field inductance per phase"),
      "Lrsigma": P("H", gt=0, doc="rotor stray inductance per phase (stator side)"),
      "Rr": P("Ohm", min=0, doc="rotor resistance per phase (stator side) at TrRef"),
      "TrRef": P("K", 293.15, gt=0, abs_T=True), "alpha20r": P("1/K", 0.0),
      "TrOperational": P("K", 293.15, gt=0, abs_T=True)},
     {**QS_VARS, "lossPowerRotorWinding": "W", **{f"abs_ir[{k}]": "A" for k in (1, 2, 3)}}, QS_INITS,
     mods=AC_STATOR_MODS, cond_ports={h: "useThermalPort" for h in IM_HEAT}, hidden=True, linear=False,
     var_paths={v: "powerBalance." + v for v in PB}, islands=[["plug_sp", "plug_sn"], ["flange"], IM_HEAT])
for _n, _h, _dc in (("qs_pmsm_core", PM_HEAT, False), ("qs_pmsm_damper_core", PMD_HEAT, True)):
    kind(_n, QFW + "BasicMachines.SynchronousMachines.SM_PermanentMagnet",
         {"plug_sp": "qs_plug", "plug_sn": "qs_plug", "flange": "flange_r", **{h: "heat" for h in _h}},
         {**QS_STATOR, **{k_: v_ for k_, v_ in KINDS["pmsm_damper_core" if _dc else "pmsm_core"]["params"].items()
                          if k_ not in AC_STATOR}},
         {**QS_VARS, "lossPowerPermanentMagnet": "W", **({"lossPowerRotorWinding": "W"} if _dc else {})}, QS_INITS,
         text={"useDamperCage": "true" if _dc else "false"},
         mods={**AC_STATOR_MODS, "IRef_pm": lambda p: _need(p, "IRef_pm", "PRef_pm"),
               "wRef_pm": lambda p: _ref_or(p, "wRef_pm", "PRef_pm", _w_sync(p))},
         cond_ports={h: "useThermalPort" for h in _h}, hidden=True, linear=False,
         var_paths={v: "powerBalance." + v for v in PB}, islands=[["plug_sp", "plug_sn"], ["flange"], _h])
kind("qs_terminal_box", QFW + "Utilities.TerminalBox", {"plug_sp": "qs_plug", "plug_sn": "qs_plug",
                                                       "plugSupply": "qs_plug", "starpoint": "qs_pin"},
     {"terminal": P("1", "Y", structural=True, type="enum", choices=["Y", "D"], msl=False)},
     mods={"terminalConnection": lambda p: '"' + p["terminal"] + '"'}, hidden=True,
     islands=[["plug_sp", "plug_sn", "plugSupply", "starpoint"]])


def _is_y(p):
    return p.get("terminal", "Y") == "Y"


def _qs_machine(name, core, heat, extra_doc):
    k = KINDS[core]
    params = {**{n: s for n, s in k["params"].items()}, "terminal": KINDS["qs_terminal_box"]["params"]["terminal"]}
    hp = {h: f"m.{h}" for h in heat}
    composite(name, {"m": (core, {n: n for n in k["params"]}), "tb": ("qs_terminal_box", {"terminal": "terminal"}),
                     "g": ("qs_ground", {}, _is_y)},
              {"plug": "tb.plugSupply", "flange": "m.flange", **hp}, params,
              {v: (f"m.{v}", u) for v, u in k["vars"].items()},
              {i: f"m.{i}" for i in k["inits"]},
              conns=[("m.plug_sp", "tb.plug_sp"), ("m.plug_sn", "tb.plug_sn"), ("tb.starpoint", "g.pin", _is_y)],
              port_groups={"heatPort": list(hp)}, linear=False, domain="quasi-static",
              doc=f"{extra_doc}; plug = QS supply (Y: star grounded inside, or D), flange = shaft; heatPort "
                  f"or the {len(heat)} heat ports")


KINDS_ALIAS_DOC = {}       # QS machine -> the transient machine whose parameters it shares (study_format lists them once)
_qs_machine("qs_induction_machine", "qs_im_core", IM_HEAT,
            "MSL QuasiStatic IM_SquirrelCage: Rs, Lssigma, Lm, Lrsigma, Rr per phase, p pole pairs")
_qs_machine("qs_pmsm", "qs_pmsm_core", PM_HEAT,
            "MSL QuasiStatic SM_PermanentMagnet without damper cage (needs a drive or a synchronous start): "
            "VsOpenCircuit, Lmd, Lmq")
_qs_machine("qs_pmsm_damper", "qs_pmsm_damper_core", PMD_HEAT,
            "MSL QuasiStatic SM_PermanentMagnet with damper cage: also Lrsigmad, Rrd (Lrsigmaq, Rrq)")
for _q, _t in (("qs_induction_machine", "induction_machine"), ("qs_pmsm", "pmsm"), ("qs_pmsm_damper", "pmsm_damper")):
    KINDS[_q]["params_as"] = _t
composite("qs_grid",
          {"src": ("qs_ac_voltage", {"V_rms": "V_rms", "f": "f", "phase": "phase"}), "st": ("qs_star", {}),
           "g": ("qs_ground", {})},
          {"plug": "src.plug_p"}, {"V_rms": P("V", gt=0, doc="RMS phase voltage"), "f": P("Hz", gt=0),
                                   "phase": P("rad", 0.0, doc="phase of phase 1 (cosine reference)")},
          {"P_total": ("src.P_total", "W"), "Q_total": ("src.Q_total", "var")}, {"gamma0": "src.gamma0"},
          conns=[("src.plug_n", "st.plug_p"), ("st.pin_n", "g.pin")], domain="quasi-static",
          doc="a symmetric quasi-static grid, star point grounded; plug = the three phases; P_total, Q_total "
              "absorbed by the source (negative when it delivers)")
composite("qs_vf_inverter",
          {"fin": ("gain", {"k": lambda p: 1.0}), "vf": ("qs_vf_controller", {"VNominal": "VNominal", "fNominal": "fNominal"}),
           "src": ("qs_variable_voltage", {}), "st": ("qs_star", {}), "g": ("qs_ground", {})},
          {"plug": "src.plug_p", "f": "fin.u"},
          {"VNominal": P("V", gt=0, doc="RMS phase voltage at fNominal"), "fNominal": P("Hz", gt=0)},
          {"P_total": ("src.P_total", "W"), "Q_total": ("src.Q_total", "var"), "amplitude": ("vf.amplitude", "V")},
          {"gamma0": "src.gamma0"},
          conns=[("fin.y", "vf.u"), ("fin.y", "src.f"), ("vf.y", "src.V"), ("src.plug_n", "st.plug_p"),
                 ("st.pin_n", "g.pin")], linear=False, domain="quasi-static",
          doc="an ideal quasi-static V/f inverter: input f [Hz] (the circuit's frequency); phase voltages VNominal "
              "min(|f|/fNominal, 1) (RMS)")
composite("qs_pmsm_current_drive",
          {"cc": ("qs_current_controller", {"p": "p"}), "src": ("qs_reference_current", {}), "st": ("qs_star", {}),
           "g": ("qs_ground", {}), "ang": ("angle_sensor", {}), "rp": ("qs_resistor_core", {"R": lambda p: QS_DRIVE_RP})},
          {"plug": "src.plug_n", "id": "cc.id_rms", "iq": "cc.iq_rms", "flange": "ang.flange"},
          {"p": P("1", type="int", structural=True, min=1, doc="pole pairs of the motor")},
          {"P_total": ("src.P_total", "W"), "Q_total": ("src.Q_total", "var")},
          conns=[("cc.I", "src.I"), ("cc.gamma", "src.gamma"), ("ang.phi", "cc.phi"), ("src.plug_p", "st.plug_p"),
                 ("st.pin_n", "g.pin"), ("rp.plug_p", "src.plug_p"), ("rp.plug_n", "src.plug_n")],
          linear=False, domain="quasi-static",
          doc="ideal field-oriented current feed (MSL QS SMPM_CurrentSource): inputs id, iq [A RMS, rotor frame]; "
              "flange = the motor shaft; torque = 3 p psiM iq (+ 3 p (Lmd - Lmq) id iq), psiM = VsOpenCircuit / "
              "(2 pi fsNominal); 1 MOhm per phase across the source (as MSL's example)")

# ------------------------------------------------------------------------------------------- composites
composite("dc_motor",
          {"Ra": ("resistor", {"R": "R"}), "La": ("inductor", {"L": "L"}), "emf": ("emf", {"k": "k"}),
           "rotor": ("inertia", {"J": "J"}), "fric": ("rot_damper", {"d": "d"}), "housing": ("rot_fixed", {})},
          {"p": "Ra.p", "n": "emf.n", "flange": "rotor.flange_b", "heatPort_R": "Ra.heatPort",
           "heatPort_friction": "fric.heatPort"},
          {"R": P("Ohm", gt=0, doc="armature resistance"), "L": P("H", gt=0, doc="armature inductance"),
           "k": P("N.m/A", gt=0, doc="torque constant = back-emf constant (V.s/rad)"),
           "J": P("kg.m2", gt=0, doc="rotor inertia"), "d": P("N.m.s/rad", 0.0, min=0, doc="viscous friction")},
          {"i": ("La.i", "A"), "w": ("rotor.w", "rad/s"), "phi": ("rotor.phi", "rad"), "v": ("Ra.v + La.v + emf.v", "V"),
           "tau": ("emf.tauElectrical", "N.m"), "LossPower": ("Ra.LossPower + fric.lossPower", "W")},
          {"i0": "La.i0", "w0": "rotor.w0", "phi0": "rotor.phi0"},
          conns=[("Ra.n", "La.p"), ("La.n", "emf.p"), ("emf.flange", "rotor.flange_a"),
                 ("fric.flange_a", "rotor.flange_a"), ("fric.flange_b", "housing.flange")],
          port_groups={"heatPort": ["heatPort_R", "heatPort_friction"]},
          doc="permanent-magnet DC motor: armature R + L + emf (k), rotor J, viscous friction d to the housing; "
              "flange = shaft; tau = electrical torque k*i; heatPort (or heatPort_R, heatPort_friction) takes the losses")
composite("convection",
          {"conv": ("convection_core", {}), "Gc": ("constant", {"k": lambda p: p["Gc"] if p.get("Gc") is not None else p["h"] * p["A"]})},
          {"solid": "conv.solid", "fluid": "conv.fluid"},
          {"Gc": P("W/K", min=0), "h": P("W/(m2.K)", gt=0), "A": P("m2", gt=0)},
          {"Q_flow": ("conv.Q_flow", "W"), "dT": ("conv.dT", "K")},
          conns=[("Gc.y", "conv.Gc")], alternatives=[["Gc"], ["h", "A"]],
          doc="Q_flow = Gc*(solid.T - fluid.T), Gc = h*A")


# units of signal outputs (sensors) and Modelica paths of ports that are not plain names
OUT_UNITS = {("voltage_sensor", "v"): "V", ("current_sensor", "i"): "A", ("potential_sensor", "phi"): "V",
             ("position_sensor", "s"): "m", ("speed_sensor", "v"): "m/s", ("acc_sensor", "a"): "m/s2",
             ("force_sensor", "f"): "N", ("angle_sensor", "phi"): "rad", ("rot_speed_sensor", "w"): "rad/s",
             ("rot_acc_sensor", "a"): "rad/s2", ("torque_sensor", "tau"): "N.m", ("temperature_sensor", "T"): "K",
             ("heat_flow_sensor", "Q_flow"): "W", ("fluid_temperature_sensor", "y"): "K",
             ("fluid_pressure_sensor", "y"): "Pa", ("volume_flow_sensor", "y"): "m3/s",
             ("ac_current_rms", "I"): "A", ("ac_voltage_rms", "V"): "V", ("ac_power", "P"): "W", ("ac_power", "Q"): "var",
             ("qs_current_rms", "I"): "A", ("qs_voltage_rms", "V"): "V"}
PORT_PATHS = {("table", "y"): "y[1]", **{("dc_machine", h): "thermalPort." + h for h in DCPM_HEAT},
              **{(k_, h): _heat_path(h) for k_, hs in (("im_core", IM_HEAT), ("pmsm_core", PM_HEAT),
                                                       ("pmsm_damper_core", PMD_HEAT), ("qs_im_core", IM_HEAT),
                                                       ("qs_pmsm_core", PM_HEAT), ("qs_pmsm_damper_core", PMD_HEAT))
                 for h in hs}}



# ------------------------------------------------------------------------------------------- Modelica.Fluid (V5)
# Probed on carbonio (OpenModelica 1.27.2, 2026-11-19; planning.md V5): every value below that is not structural
# changes at run time; values used inside a function or model modifier (pump curves, flow-model and heat-transfer
# data, a valve's constant opening) are "lifted" into top-level parameters omct_<element>_<name>, which the probes
# showed are changeable too. Media are packages: one per connected set (redeclare package Medium).
MF = "Modelica.Fluid."
FMEDIA = {          # name: (MSL package, class): liquid_const = constant density (the closed-loop rule applies)
    "water": ("Modelica.Media.Water.ConstantPropertyLiquidWater", "liquid_const"),
    "glycol47": ("Modelica.Media.Incompressible.Examples.Glycol47", "liquid"),
    "essotherm650": ("Modelica.Media.Incompressible.Examples.Essotherm650", "liquid"),
    "air": ("Modelica.Media.Air.DryAirNasa", "gas"),
    "simple_air": ("Modelica.Media.Air.SimpleAir", "gas"),
}
FMEDIUM_KEYS = {"rho": "d_const", "cp": "cp_const", "eta": "eta_const", "lambda": "lambda_const"}
FMEDIUM_EXTRA = {"p_vapour"}            # omct's own (NPSH, FL16): not a Modelica constant


def fmedium(v) -> dict:
    """{name, msl (the package text), class} of a Modelica.Fluid medium value (an FMEDIA name or a custom liquid
    {rho, cp[, eta, lambda]}: ConstantPropertyLiquidWater with those constants, probe P3)."""
    if isinstance(v, str):
        pkg, cls = FMEDIA[v]
        return {"name": v, "msl": pkg, "class": cls}
    d = dict(v)
    args = ", ".join(f"{FMEDIUM_KEYS[k]}={float(d[k])!r}" for k in sorted(d) if k in FMEDIUM_KEYS)
    if "cp" in d:     # cv_const would stay water's 4184 (carbonio 2026-11-19, fl_heated_tank): incompressible, cv = cp
        args += f", cv_const={float(d['cp'])!r}"
    return {"name": "custom", "msl": f"Modelica.Media.Water.ConstantPropertyLiquidWater({args})",
            "class": "liquid_const"}


def is_liquid(v) -> bool:
    return fmedium(v)["class"].startswith("liquid")


FMED = {"medium": P("1", None, structural=True, type="fmedium", msl=False,
                    doc="one of " + ", ".join(FMEDIA) + ", or a liquid {rho, cp, eta, lambda, p_vapour} (SI; p_vapour "
                        "only for NPSH, default water's); given on one component of a connected set, the others take "
                        "it (default water)")}
FAB_M = {"port_a": "mfluid", "port_b": "mfluid"}
TWO_VARS = {"m_flow": "kg/s", "dp": "Pa", "p_a": "Pa", "p_b": "Pa"}
TWO_PATHS = {"m_flow": "port_a.m_flow", "dp": [(1.0, "port_a.p"), (-1.0, "port_b.p")], "p_a": "port_a.p",
             "p_b": "port_b.p"}
DYN = {"fixed_initial": "FixedInitial", "steady_state_initial": "SteadyStateInitial",
       "free_initial": "DynamicFreeInitial"}
W_RPM = 60.0 / (2 * 3.141592653589793)          # rad/s -> rev/min (MSL pumps)

kind("fl_system", MF + "System", {},
     {"p_ambient": P("Pa", 101325.0, gt=0), "T_ambient": P("K", 293.15, gt=0, abs_T=True),
      "g": P("m/s2", 9.80665, min=0), "m_flow_small": P("kg/s", 0.01, gt=0, doc="regularisation of small flows"),
      "dp_small": P("Pa", 1.0, gt=0), "dynamics": P("1", "fixed_initial", type="enum", choices=list(DYN), msl=False,
      structural=True, doc="energy and mass balances: fixed_initial (each storage starts at its T_start, p_start, "
                           "level_start), steady_state_initial, free_initial"),
      "allowFlowReversal": P("1", True, type="bool", structural=True)},
     domain="mfluid",
     mods={"energyDynamics": lambda p: "Modelica.Fluid.Types.Dynamics." + DYN[p["dynamics"]],
           "massDynamics": lambda p: "Modelica.Fluid.Types.Dynamics." + DYN[p["dynamics"]]},
     doc="Modelica.Fluid's system settings; the component must be named 'system' (one per study; omct adds one "
         "with these defaults when absent)")
kind("fl_boundary", MF + "Sources.Boundary_pT", {"port": "mfluid", "p_in": "in", "T_in": "in"},
     {**FMED, "p": P("Pa", 101325.0, gt=0), "T": P("K", 293.15, gt=0, abs_T=True)},
     multi={"port": ("ports", "nPorts")}, cond_ports={"p_in": "use_p_in", "T_in": "use_T_in"}, domain="mfluid",
     fluid={"pressure_ref": True},
     doc="a reservoir at p and T (inputs p_in [Pa], T_in [K] when connected); its port takes any number of "
         "connections")
kind("fl_mass_flow", MF + "Sources.MassFlowSource_T", {"port": "mfluid", "m_flow_in": "in", "T_in": "in"},
     {**FMED, "m_flow": P("kg/s", 0.0, doc="into the network"), "T": P("K", 293.15, gt=0, abs_T=True)},
     multi={"port": ("ports", "nPorts")}, cond_ports={"m_flow_in": "use_m_flow_in", "T_in": "use_T_in"},
     domain="mfluid", doc="a prescribed mass flow into the network at T (inputs m_flow_in, T_in when connected)")


def _flow_model(p, e):
    f = p["friction"]
    if f == "detailed":
        return {}, {}
    if f == "turbulent":
        return {"FlowModel": "Modelica.Fluid.Pipes.BaseClasses.FlowModels.TurbulentPipeFlow"}, {}
    cls = {"nominal_laminar": "NominalLaminarFlow", "nominal_turbulent": "NominalTurbulentPipeFlow"}[f]
    return ({"FlowModel": f"Modelica.Fluid.Pipes.BaseClasses.FlowModels.{cls}(dp_nominal=omct_{e}_dp_nominal, "
                          f"m_flow_nominal=omct_{e}_m_flow_nominal)"},
            {"dp_nominal": p["dp_nominal"], "m_flow_nominal": p["m_flow_nominal"]})


PIPE = {"length": P("m", gt=0), "diameter": P("m", gt=0), "roughness": P("m", 2.5e-5, min=0),
        "height_ab": P("m", 0.0, doc="height of port_b over port_a"),
        "nParallel": P("1", 1.0, gt=0, doc="identical pipes in parallel"),
        "friction": P("1", "detailed", type="enum", structural=True, msl=False,
                      choices=["detailed", "turbulent", "nominal_laminar", "nominal_turbulent"],
                      doc="wall friction: detailed (laminar and turbulent, roughness; MSL's default), turbulent, or "
                          "nominal_* through {dp_nominal, m_flow_nominal}"),
        "dp_nominal": P("Pa", None, gt=0, msl=False), "m_flow_nominal": P("kg/s", None, gt=0, msl=False)}
kind("fl_pipe", MF + "Pipes.StaticPipe", FAB_M, {**FMED, **PIPE}, TWO_VARS, var_paths=TWO_PATHS, domain="mfluid",
     fluid={"build": _flow_model},
     doc="a pipe without storage: wall friction and static head (height_ab)")


def _dyn_pipe(p, e):
    mods, lifted = _flow_model(p, e)
    if p.get("_heat"):
        ht = p["heat_transfer"]
        if ht == "constant":
            mods["HeatTransfer"] = ("Modelica.Fluid.Pipes.BaseClasses.HeatTransfer.ConstantFlowHeatTransfer("
                                    f"alpha0=omct_{e}_alpha)")
            lifted["alpha"] = p["alpha"]
        elif ht == "local":
            mods["HeatTransfer"] = "Modelica.Fluid.Pipes.BaseClasses.HeatTransfer.LocalPipeFlowHeatTransfer"
    if p.get("_structure"):
        mods["modelStructure"] = "Modelica.Fluid.Types.ModelStructure." + p["_structure"]
    return mods, lifted


# a DynamicPipe's length and height_ab are fixed when OpenModelica builds it (carbonio 2026-11-19, run_fl2_generated1:
# "pipe.length, pipe.height_ab: OpenModelica fixed it when building"): structural here (a StaticPipe's change at run time)
DPIPE = {**PIPE, "length": P("m", gt=0, structural=True), "height_ab": P("m", 0.0, structural=True,
                                                                         doc="height of port_b over port_a")}
kind("fl_dynamic_pipe", MF + "Pipes.DynamicPipe", {**FAB_M, "heat": "heat"},
     {**FMED, **DPIPE, "nNodes": P("1", 2, type="int", structural=True, min=1, doc="volumes along the pipe"),
      "heat_transfer": P("1", "ideal", type="enum", structural=True, msl=False, choices=["ideal", "constant", "local"],
                         doc="with heat connected: ideal (wall = fluid temperature), constant {alpha}, local "
                             "(Dittus-Boelter / laminar from the flow)"),
      "alpha": P("W/(m2.K)", 1000.0, gt=0, msl=False, doc="heat transfer coefficient (heat_transfer constant)"),
      "T_start": P("K", None, gt=0, abs_T=True), "p_a_start": P("Pa", None, gt=0), "p_b_start": P("Pa", None, gt=0)},
     TWO_VARS, var_paths=TWO_PATHS, cond_ports={"heat": "use_HeatTransfer"}, domain="mfluid",
     fluid={"build": _dyn_pipe, "volume": True, "collector": ("heat", "heatPorts", "nNodes")},
     doc="a pipe with nNodes volumes (mass and energy storage); heat: one port joined to every volume's wall "
         "(a ThermalCollector)")
kind("fl_resistance", MF + "Fittings.GenericResistances.VolumeFlowRate", FAB_M,
     {**FMED, "a": P("Pa.s2/m6", gt=0, doc="dp = a V_flow^2 + b V_flow; a > 0 (MSL 4.0.0 gives m_flow = rho b dp "
                                          "for a = 0, not rho dp / b: for a linear law use fl_valve_linear)"),
      "b": P("Pa.s/m3", 0.0, min=0)},
     {**TWO_VARS, "V_flow": "m3/s"}, var_paths=TWO_PATHS, domain="mfluid", fluid={"cfdt": "resistance"},
     doc="a generic resistance dp = a V_flow|V_flow| + b V_flow (a fitting, a filter, a heat exchanger's side); "
         "\"cfdt\": [cfd-translator results] fits a, b to their total pressure drops")
kind("fl_orifice", MF + "Fittings.SimpleGenericOrifice", FAB_M,
     {**FMED, "diameter": P("m", gt=0), "zeta": P("1", gt=0, doc="loss factor: dp = zeta rho v^2 / 2")},
     {**TWO_VARS, "V_flow": "m3/s"}, var_paths=TWO_PATHS, domain="mfluid", doc="a loss coefficient on a diameter")


def _valve(p, e):
    mods, lifted = {}, {}
    given = [k for k in ("Kv", "Cv", "Av") if p.get(k) is not None]
    mods["CvData"] = "Modelica.Fluid.Types.CvTypes." + (given[0] if given else "OpPoint")
    ch = p["characteristic"]
    if ch != "linear":
        fn = {"quadratic": "quadratic", "equal_percentage": "equalPercentage(rangeability=omct_%s_rangeability)" % e}[ch]
        mods["valveCharacteristic"] = "Modelica.Fluid.Valves.BaseClasses.ValveCharacteristics." + fn
        if ch == "equal_percentage":
            lifted["rangeability"] = p["rangeability"]
    if not p.get("_opening_connected"):
        mods["opening"] = f"omct_{e}_opening"
        lifted["opening"] = p["opening"]
    return mods, lifted


VALVE = {"Kv": P("1", None, gt=0, doc="metric Kv [m3/h] (a number, as on data sheets)"),
         "Cv": P("1", None, gt=0, doc="US Cv [USG/min]"), "Av": P("m2", None, gt=0),
         "dp_nominal": P("Pa", gt=0, doc="the nominal operating point (sizing without Kv/Cv/Av, regularisation)"),
         "m_flow_nominal": P("kg/s", gt=0),
         "characteristic": P("1", "linear", type="enum", structural=True, msl=False,
                             choices=["linear", "quadratic", "equal_percentage"]),
         "rangeability": P("1", 20.0, gt=1, msl=False),
         "opening": P("1", 1.0, min=0, max=1, msl=False, doc="constant opening when the opening input is not connected"),
         "checkValve": P("1", False, type="bool", structural=True)}
kind("fl_valve", MF + "Valves.ValveIncompressible", {**FAB_M, "opening": "in"}, {**FMED, **VALVE},
     {**TWO_VARS, "V_flow": "m3/s"}, var_paths=TWO_PATHS, cond_ports={"opening": None}, domain="mfluid",
     fluid={"build": _valve, "liquid_only": True},
     doc="a valve for liquids (IEC 534): sized by one of Kv, Cv, Av, else by its op point; opening 0-1 (input or "
         "constant)")
kind("fl_valve_gas", MF + "Valves.ValveCompressible", {**FAB_M, "opening": "in"},
     {**FMED, **VALVE, "p_nominal": P("Pa", gt=0, doc="nominal inlet pressure"),
      "Fxt_full": P("1", 0.5, gt=0, doc="pressure-drop ratio factor at full opening")},
     {**TWO_VARS, "V_flow": "m3/s"}, var_paths=TWO_PATHS, cond_ports={"opening": None}, domain="mfluid",
     fluid={"build": _valve, "gas_only": True}, doc="a valve for gases (IEC 534, choked flow)")
kind("fl_valve_linear", MF + "Valves.ValveLinear", {**FAB_M, "opening": "in"},
     {**FMED, "dp_nominal": P("Pa", gt=0), "m_flow_nominal": P("kg/s", gt=0),
      "opening": P("1", 1.0, min=0, max=1, msl=False)},
     {**TWO_VARS, "V_flow": "m3/s"}, var_paths=TWO_PATHS, cond_ports={"opening": None}, domain="mfluid",
     fluid={"build": lambda p, e: (({} if p.get("_opening_connected") else {"opening": f"omct_{e}_opening"}),
                                   ({} if p.get("_opening_connected") else {"opening": p["opening"]}))},
     doc="m_flow = m_flow_nominal opening dp / dp_nominal")


def _pump_start(p, v):
    """m_flow_start text for a pump: the medium's (or rho_power's) density x a flow x nParallel."""
    med = p.get("medium")
    rho = p.get("rho_power") or (med["rho"] if isinstance(med, dict) else 1000.0)
    return repr(float(rho * v * (p.get("nParallel") or 1)))


def _pump(p, e):
    pts = p["curve"]
    n = len(pts)
    fn = {2: "linearFlow", 3: "quadraticFlow"}.get(n, "polynomialFlow")
    mods = {"flowCharacteristic": f"Modelica.Fluid.Machines.BaseClasses.PumpCharacteristics.{fn}("
                                  f"V_flow_nominal=omct_{e}_V_curve, head_nominal=omct_{e}_head_curve)",
            "efficiencyCharacteristic": "Modelica.Fluid.Machines.BaseClasses.PumpCharacteristics.constantEfficiency("
                                        f"eta_nominal=omct_{e}_eta)"}
    lifted = {"V_curve": [r[0] for r in pts], "head_curve": [r[1] for r in pts], "eta": p["eta"]}
    if p.get("power") is not None:        # a power curve (FL5): MSL's quadraticPower, eta = dp V / W
        from .chains import quad_points
        pw, _ = quad_points(p["power"])
        mods["use_powerCharacteristic"] = "true"
        mods["powerCharacteristic"] = ("Modelica.Fluid.Machines.BaseClasses.PumpCharacteristics.quadraticPower("
                                       f"V_flow_nominal=omct_{e}_V_power, W_nominal=omct_{e}_W_power)")
        lifted.update({"V_power": [r[0] for r in pw], "W_power": [r[1] for r in pw]})
        del lifted["eta"], mods["efficiencyCharacteristic"]
        if p.get("m_flow_start") is None:
            # MSL's homotopy for the power divides by V_flow_single_init = m_flow_start / rho_nominal / nParallel:
            # 0 by default (system.m_flow_start), so initialisation fails with a division by zero (carbonio
            # 2026-11-19, run_fl5_power_probe1). A start at the power curve's middle flow.
            mods["m_flow_start"] = _pump_start(p, pw[1][0] if pw[1][0] > 0 else max(r[0] for r in pw))
    if float(p.get("V") or 0) > 0:
        # a pump volume stores energy (the system's energyDynamics; MSL's pump default is SteadyState): with
        # SteadyState and no flow (a closed check valve) its enthalpy has no equation and OpenModelica divides by
        # the zero flow (carbonio 2026-11-19, run_fl7_probe2-5); with V > 0 MSL's initial homotopy failed from
        # m_flow_start 0 (run_fl7_probe3), so a start at the curve's middle flow unless given
        mods["energyDynamics"] = "system.energyDynamics"
        if p.get("m_flow_start") is None and "m_flow_start" not in mods:
            v = pts[len(pts) // 2][0]
            mods["m_flow_start"] = _pump_start(p, v if v > 0 else max(r[0] for r in pts))
    if p.get("rho_power") is not None:
        mods["rho_nominal"] = repr(float(p["rho_power"]))
    if p.get("_w_in"):
        mods["use_N_in"] = "true"
    return mods, lifted


PUMP = {"curve": P("1", type="table", structural=False, msl=False,
                   doc="head curve [[V_flow (m3/s), head (m)], ...] at N_nominal: 2 points linear, 3 quadratic, "
                       "more polynomial; head falling with flow"),
        "N_nominal": P("rad/s", gt=0, doc="speed of the curve (units: '1450 rpm')"),
        "eta": P("1", 0.8, gt=0, max=1, msl=False, doc="constant efficiency (power = rho g V head / eta)"),
        "nParallel": P("1", 1, type="int", structural=True, min=1),
        "V": P("m3", 0.0, min=0, structural=True, doc="volume inside (0: none)"),
        "checkValve": P("1", False, type="bool", structural=True,
                        doc="no reverse flow (needs V > 0: a closed pump keeps its enthalpy only in a volume)"),
        "m_flow_start": P("kg/s", None, doc="start guess of the flow"), "T_start": P("K", None, gt=0, abs_T=True),
        "power": P("1", None, type="table", structural=False, msl=False,
                   doc="shaft power [[V_flow (m3/s), W (W)], ...] at N_nominal: a quadratic by least squares (MSL "
                       "quadraticPower, scaled (N/N_nominal)^3 rho/rho_power); eta = dp V / W; instead of eta"),
        "rho_power": P("kg/m3", None, gt=0, structural=True, msl=False,
                       doc="the density the power curve is for (MSL rho_nominal; default the medium's)")}
PUMP_VARS = {"m_flow": "kg/s", "V_flow": "m3/s", "head": "m", "dp": "Pa", "W": "W", "eta": "1", "w": "rad/s"}
PUMP_PATHS = {"dp": "dp_pump", "W": "W_total", "w": [(1.0 / W_RPM, "N")]}
NPSH = {"npsh_required": P("m", None, type="npsh", msl=False,
                           doc="NPSH3 (3 % head drop, m): a number (every flow), or [[V_flow (m3/s), NPSH3 (m)], ...] "
                               "at N_nominal; with it each run reports the pump's NPSH ratio and status (FL16, FL17)"),
        "npsh3_below": P("m", None, type="npsh", msl=False,
                         doc="NPSH3 known only as an upper bound ('below 2 m at 20 m3/h'): a number or [[V_flow, m], "
                             "...] at N_nominal; with npsh_required at other flows; a ratio against it is a lower bound"),
        "npsh_flow_exponent": P("1", 2.0, gt=0, msl=False,
                                doc="NPSH3 above the highest flow given: x (V/V_max)^npsh_flow_exponent (linear between "
                                    "the flows given, held below the lowest; speeds: x (N/N_nominal)^2)"),
        "npsh_margin": P("1", 1.5, gt=1, msl=False,
                         doc="'approaching' below npsh_margin x NPSH3 ('below' under 1 x): inception comes well before "
                             "the 3 % drop (ANSI/HI 9.6.1 margins 1.1-2.0)"),
        "inlet_diameter": P("m", None, gt=0, msl=False,
                            doc="suction port diameter: adds the velocity head v^2/2g to NPSH available"),
        "eye_dip": P("Pa", None, min=0, msl=False,
                     doc="the MEAN dip: the inlet's mean static pressure less the eye's mean (cfd-translator "
                         "free_flow); the eye's lowest pressure p_in - dip - pulse_amplitude is compared with the "
                         "vapour pressure (cavitation onset) and the air-saturation pressure (air release). A dip "
                         "already measured to the eye's minimum (an 'eye dip below inlet' that runs to the minimum): "
                         "give it with pulse_amplitude 0"),
        "pulse_amplitude": P("Pa", None, min=0, msl=False,
                             doc="the eye's pressure pulsation amplitude (half peak-to-peak, jitter filtered) at "
                                 "onset_N; 0 when eye_dip already runs to the minimum"),
        "onset_jitter": P("Pa", None, min=0, msl=False,
                          doc="onset trips within this of their threshold are flagged 'possibly jitter' (default "
                              "3000 Pa when pulse_amplitude came from raw, unfiltered cfd-translator extremes, else "
                              "none)"),
        "onset_N": P("rad/s", None, gt=0, msl=False,
                     doc="the speed eye_dip and pulse_amplitude were found at (default N_nominal); both ~ N^2"),
        "onset_V_flow": P("m3/s", None, gt=0, msl=False,
                          doc="the flow they were found at: a warning when the run leaves it by more than 10 %"),
        "air_saturation_pressure": P("Pa", None, gt=0, msl=False,
                                     doc="the pressure the liquid's dissolved air is saturated at (absolute; default "
                                         "the system's p_ambient: a liquid in contact with air at the free surface)")}
kind("fl_pump", MF + "Machines.PrescribedPump", {**FAB_M, "w_in": "in"},
     {**FMED, **PUMP, **NPSH, "N": P("rad/s", None, gt=0, msl=False, doc="constant speed (default N_nominal)")},
     PUMP_VARS, var_paths=PUMP_PATHS, cond_ports={"w_in": None}, domain="mfluid",
     mods={"N_nominal": lambda p: p["N_nominal"] * W_RPM,
           "N_const": lambda p: (p["N"] if p.get("N") is not None else p["N_nominal"]) * W_RPM},
     fluid={"build": _pump, "pump": True, "speed_in": ("w_in", "N_in", W_RPM), "cfdt": "pump"},
     doc="a centrifugal pump at a prescribed speed (constant N, or the input w_in [rad/s]); affinity laws off the "
         "nominal speed; \"cfdt\": [cfd-translator pump results] gives curve, N_nominal, power, rho_power; "
         "\"npsh_cfdt\": [cfd-translator cavitation sweep results] gives npsh_required (NPSH3 per flow) and npsh3_below "
         "(sweeps without a 3 % drop: below their lowest NPSH); \"onset_cfdt\": {result: <cfd-translator free_flow "
         "result>, eye_probe, inlet} gives eye_dip (the inlet's static mean less the eye's mean), pulse_amplitude "
         "(the eye's filtered min_f/max_f; raw min/max without them: an upper bound, warned), onset_N and "
         "onset_V_flow")
kind("fl_pump_shaft", MF + "Machines.Pump", {**FAB_M, "shaft": "flange_r"}, {**FMED, **PUMP},
     PUMP_VARS, var_paths=PUMP_PATHS, domain="mfluid", mods={"N_nominal": lambda p: p["N_nominal"] * W_RPM},
     fluid={"build": _pump, "pump": True, "cfdt": "pump"},
     doc="a centrifugal pump driven by its shaft (a motor or a speed source): torque = W / w; \"cfdt\" as fl_pump")


def _tank(p, e):
    mods = {}
    n = p.get("_n_ports") or 0
    if p.get("port_diameter") is not None and n:
        mods["portsData"] = "{" + ", ".join(
            f"Modelica.Fluid.Vessels.BaseClasses.VesselPortsData(diameter={p['port_diameter']!r})" for _ in range(n)) + "}"
        mods["use_portsData"] = "true"
    else:
        mods["use_portsData"] = "false"
    return mods, {}


kind("fl_tank", MF + "Vessels.OpenTank", {"port": "mfluid", "heatPort": "heat"},
     {**FMED, "height": P("m", gt=0), "crossArea": P("m2", gt=0), "level_start": P("m", min=0),
      "T_start": P("K", None, gt=0, abs_T=True),
      "port_diameter": P("m", None, gt=0, msl=False, doc="diameter of every port (losses at the ports; none: no "
                                                          "port losses)")},
     {"level": "m", "m": "kg", "V": "m3", "T": "K"}, var_paths={"T": "medium.T"},
     multi={"port": ("ports", "nPorts")}, cond_ports={"heatPort": "use_HeatTransfer"}, domain="mfluid",
     fluid={"build": _tank, "pressure_ref": True, "vessel": True},
     doc="an open tank at the system's p_ambient: level from the mass balance; ports at the bottom")
kind("fl_volume", MF + "Vessels.ClosedVolume", {"port": "mfluid", "heatPort": "heat"},
     {**FMED, "V": P("m3", gt=0), "p_start": P("Pa", None, gt=0), "T_start": P("K", None, gt=0, abs_T=True)},
     {"p": "Pa", "T": "K", "m": "kg"}, var_paths={"p": "medium.p", "T": "medium.T"},
     multi={"port": ("ports", "nPorts")}, cond_ports={"heatPort": "use_HeatTransfer"}, domain="mfluid",
     text={"use_portsData": "false"}, fluid={"volume": True, "vessel": True},
     doc="a closed volume (ideally mixed); compressible media store mass, liquids only energy")
kind("fl_pressure", MF + "Sensors.Pressure", {"port": "mfluid", "p": "out"}, FMED, domain="mfluid",
     doc="p = the port's pressure [Pa]")
kind("fl_temperature", MF + "Sensors.TemperatureTwoPort", {**FAB_M, "T": "out"}, FMED, domain="mfluid",
     doc="T = the temperature of the flow through it [K]")
kind("fl_mass_flow_sensor", MF + "Sensors.MassFlowRate", {**FAB_M, "m_flow": "out"}, FMED, domain="mfluid",
     doc="m_flow from port_a to port_b [kg/s]")
kind("fl_volume_flow_sensor", MF + "Sensors.VolumeFlowRate", {**FAB_M, "V_flow": "out"}, FMED, domain="mfluid",
     doc="V_flow from port_a to port_b [m3/s]")
kind("fl_relative_pressure", MF + "Sensors.RelativePressure", {**FAB_M, "p_rel": "out"}, FMED, domain="mfluid",
     doc="p_rel = port_a.p - port_b.p [Pa]")
FL_OUT_UNITS = {("fl_pressure", "p"): "Pa", ("fl_temperature", "T"): "K", ("fl_mass_flow_sensor", "m_flow"): "kg/s",
                ("fl_volume_flow_sensor", "V_flow"): "m3/s", ("fl_relative_pressure", "p_rel"): "Pa"}

OUT_UNITS.update(FL_OUT_UNITS)


# ------------------------------------------------------------------------------------------- helpers
def public_kinds() -> list[str]:
    return sorted(k for k, v in KINDS.items() if not v.get("hidden"))


def msl_to_kind() -> dict:
    """Full MSL class path -> kind (for "msl": ... in a study)."""
    out = {}
    for k, v in KINDS.items():
        if v.get("msl") and not v.get("hidden"):
            out.setdefault(v["msl"], k)
    return out


def domain_of(port_type: str) -> str:
    return PORT_TYPES[port_type]["domain"]


def var_path(kind_name: str, v: str) -> str:
    """The Modelica path of a variable inside a component (most are the name itself)."""
    return KINDS[kind_name].get("var_paths", {}).get(v, v)


def phases(port_type: str) -> int:
    """Pins per port: 3 for a plug (transient or quasi-static), else 1."""
    return PORT_TYPES.get(port_type, {}).get("phases", 1)


def is_complex(port_type: str) -> bool:
    """Quasi-static ports and complex signals: each value is a pair (re, im)."""
    return bool(PORT_TYPES.get(port_type, {}).get("complex"))


def part_kind(spec) -> str:
    """The kind of a composite's part spec (kind, sub[, condition])."""
    return spec[0]


def part_on(spec, params) -> bool:
    """Whether a composite's part or connection (a tuple whose third item, when present, is its condition) exists."""
    return len(spec) < 3 or bool(spec[2](params))


def port_types(kind_name: str) -> dict:
    """{port: port type} for elementary and composite kinds (port groups: the type of their members)."""
    k = KINDS[kind_name]
    if k.get("parts") is None:
        out = dict(k["ports"])
        for g, members in k.get("port_groups", {}).items():
            out[g] = k["ports"][members[0]]
        return out
    out = {}
    for port, target in k["ports"].items():
        part, p = target.split(".", 1)
        out[port] = KINDS[k["parts"][part][0]]["ports"][p]
    for g, members in k.get("port_groups", {}).items():
        out[g] = out[members[0]]
    return out


def is_composite(kind_name: str) -> bool:
    return KINDS[kind_name].get("parts") is not None
