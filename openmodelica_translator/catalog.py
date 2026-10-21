"""catalog.py — the curated MSL 4.0.0 components: kinds, ports, parameters (SI), variables, initial values.
Version: 2026.10.21.1

Every name below was checked against the MSL 4.0.0 sources (tag v4.0.0): class paths, port names, parameter
names and defaults. A kind is either elementary (one MSL class) or composite (parts that are elementary kinds,
connected inside; e.g. dc_motor). The Modelica generator, the validation and the reference implementation all
read this table.

Port types: pin (electrical: v, i), flange_t (translational: s, f), flange_r (rotational: phi, tau),
heat (thermal: T, Q_flow), in / out (Real signals). Flow into a component is positive (Modelica).

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
    "in": {"domain": "signal"},
    "out": {"domain": "signal"},
}


def P(unit, default=REQ, *, min=None, max=None, gt=None, structural=False, doc="", type="real", choices=None,
      abs_T=False, msl=None):
    """A parameter. msl: the MSL parameter name when it differs (None = same name; False = not an MSL parameter
    itself, used by a modifier function)."""
    return {"unit": unit, "default": default, "min": min, "max": max, "gt": gt, "structural": structural,
            "doc": doc, "type": type, "choices": choices, "abs_T": abs_T, "msl": msl}


def I(var, unit, default, priority=1, abs_T=False):
    return {"var": var, "unit": unit, "default": default, "priority": priority, "abs_T": abs_T}


INIT_STATE = "Modelica.Blocks.Types.Init.InitialState"
KINDS: dict[str, dict] = {}


def kind(name, msl, ports, params=None, vars=None, inits=None, *, doc="", text=None, mods=None, rigid=(),
         linear=True, islands=None, hidden=False, block_states=None, alternatives=None, domain=None):
    """text: fixed (structural) modifier text; mods: {msl_param: function(params) -> value} for parameters
    computed from the study's (alternatives such as m + cp -> C); rigid: port pairs at the same point
    (Mass, Inertia: L = 0); islands: groups of ports linked through the component (for the ground and
    reference checks; default: all physical ports of one domain together)."""
    KINDS[name] = {"name": name, "msl": msl, "ports": ports, "params": params or {}, "vars": vars or {},
                   "inits": inits or {}, "doc": doc, "text": text or {}, "mods": mods or {}, "rigid": list(rigid),
                   "linear": linear, "islands": islands, "hidden": hidden, "parts": None,
                   "block_states": block_states or {}, "alternatives": alternatives or [], "domain": domain}


def composite(name, parts, ports, params, vars, inits=None, *, conns=(), doc="", linear=True, alternatives=None,
              domain=None):
    """parts: {part: (kind, {part_param: study_param name | function(params)})}; ports: {port: "part.port"};
    vars: {var: (expression over "part.var" names, unit)}; inits: {init: "part.init"}."""
    KINDS[name] = {"name": name, "msl": None, "parts": parts, "ports": ports, "params": params, "vars": vars,
                   "inits": inits or {}, "conns": list(conns), "doc": doc, "linear": linear, "hidden": False,
                   "alternatives": alternatives or [], "domain": domain}


EA = "Modelica.Electrical.Analog."
MT = "Modelica.Mechanics.Translational."
MR = "Modelica.Mechanics.Rotational."
TH = "Modelica.Thermal.HeatTransfer."
BL = "Modelica.Blocks."
PN = {"p": "pin", "n": "pin"}
FAB_T = {"flange_a": "flange_t", "flange_b": "flange_t"}
FAB_R = {"flange_a": "flange_r", "flange_b": "flange_r"}

# ------------------------------------------------------------------------------------------- electrical
kind("resistor", EA + "Basic.Resistor", PN, {"R": P("Ohm", min=0)},
     {"v": "V", "i": "A", "LossPower": "W"}, doc="v = R*i")
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
kind("ideal_diode", EA + "Ideal.IdealDiode", PN,
     {"Ron": P("Ohm", 1e-5, min=0), "Goff": P("S", 1e-5, min=0), "Vknee": P("V", 0.0, min=0)},
     {"v": "V", "i": "A", "LossPower": "W"}, linear=False, doc="piecewise linear: Ron when conducting, Goff when blocking")
kind("opamp", EA + "Ideal.IdealOpAmp3Pin", {"in_p": "pin", "in_n": "pin", "out": "pin"},
     islands=[["in_p", "in_n", "out"]], doc="ideal (nullor): in_p.v = in_n.v, no input currents; output referenced to ground")

# ------------------------------------------------------------------------------------------- translational
kind("mass", MT + "Components.Mass", FAB_T, {"m": P("kg", gt=0)}, {"s": "m", "v": "m/s", "a": "m/s2"},
     {"s0": I("s", "m", 0.0), "v0": I("v", "m/s", 0.0)}, rigid=[("flange_a", "flange_b")],
     doc="m*a = flange_a.f + flange_b.f (length 0: both flanges at s)")
kind("spring", MT + "Components.Spring", FAB_T, {"c": P("N/m", min=0), "s_rel0": P("m", 0.0)},
     {"s_rel": "m", "f": "N"}, {"s_rel_init": I("s_rel", "m", "s_rel0", 2)}, doc="f = c*(s_rel - s_rel0), s_rel = flange_b.s - flange_a.s")
kind("damper", MT + "Components.Damper", FAB_T, {"d": P("N.s/m", min=0)},
     {"s_rel": "m", "v_rel": "m/s", "f": "N", "lossPower": "W"}, {"s_rel_init": I("s_rel", "m", 0.0, 2)},
     doc="f = d*v_rel")
kind("spring_damper", MT + "Components.SpringDamper", FAB_T,
     {"c": P("N/m", min=0), "d": P("N.s/m", min=0), "s_rel0": P("m", 0.0)},
     {"s_rel": "m", "v_rel": "m/s", "f": "N", "lossPower": "W"}, {"s_rel_init": I("s_rel", "m", "s_rel0", 2)},
     doc="f = c*(s_rel - s_rel0) + d*v_rel")
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
kind("rot_damper", MR + "Components.Damper", FAB_R, {"d": P("N.m.s/rad", min=0)},
     {"phi_rel": "rad", "w_rel": "rad/s", "tau": "N.m", "lossPower": "W"}, {"phi_rel_init": I("phi_rel", "rad", 0.0, 2)},
     doc="tau = d*w_rel")
kind("rot_spring_damper", MR + "Components.SpringDamper", FAB_R,
     {"c": P("N.m/rad", min=0), "d": P("N.m.s/rad", min=0), "phi_rel0": P("rad", 0.0)},
     {"phi_rel": "rad", "w_rel": "rad/s", "tau": "N.m", "lossPower": "W"},
     {"phi_rel_init": I("phi_rel", "rad", "phi_rel0", 2)}, doc="tau = c*(phi_rel - phi_rel0) + d*w_rel")
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
             f"a load opposing motion in either direction needs tau_nominal < 0); linear for the reference only with "
             f"TorqueDirection false")
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

# ------------------------------------------------------------------------------------------- composites
composite("dc_motor",
          {"Ra": ("resistor", {"R": "R"}), "La": ("inductor", {"L": "L"}), "emf": ("emf", {"k": "k"}),
           "rotor": ("inertia", {"J": "J"}), "fric": ("rot_damper", {"d": "d"}), "housing": ("rot_fixed", {})},
          {"p": "Ra.p", "n": "emf.n", "flange": "rotor.flange_b"},
          {"R": P("Ohm", gt=0, doc="armature resistance"), "L": P("H", gt=0, doc="armature inductance"),
           "k": P("N.m/A", gt=0, doc="torque constant = back-emf constant (V.s/rad)"),
           "J": P("kg.m2", gt=0, doc="rotor inertia"), "d": P("N.m.s/rad", 0.0, min=0, doc="viscous friction")},
          {"i": ("La.i", "A"), "w": ("rotor.w", "rad/s"), "phi": ("rotor.phi", "rad"), "v": ("Ra.v + La.v + emf.v", "V"),
           "tau": ("emf.tauElectrical", "N.m"), "LossPower": ("Ra.LossPower + fric.lossPower", "W")},
          {"i0": "La.i0", "w0": "rotor.w0", "phi0": "rotor.phi0"},
          conns=[("Ra.n", "La.p"), ("La.n", "emf.p"), ("emf.flange", "rotor.flange_a"),
                 ("fric.flange_a", "rotor.flange_a"), ("fric.flange_b", "housing.flange")],
          doc="permanent-magnet DC motor: armature R + L + emf (k), rotor J, viscous friction d to the housing; "
              "flange = shaft; tau = electrical torque k*i")
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
             ("heat_flow_sensor", "Q_flow"): "W"}
PORT_PATHS = {("table", "y"): "y[1]"}


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


def port_types(kind_name: str) -> dict:
    """{port: port type} for elementary and composite kinds."""
    k = KINDS[kind_name]
    if k.get("parts") is None:
        return dict(k["ports"])
    out = {}
    for port, target in k["ports"].items():
        part, p = target.split(".", 1)
        out[port] = KINDS[k["parts"][part][0]]["ports"][p]
    return out


def is_composite(kind_name: str) -> bool:
    return KINDS[kind_name].get("parts") is not None
