"""studies.py — studies shared by the tests (closed forms in test_ref, translation in test_modelica).
Version: 2026.10.21.1
"""
import copy

RC = {"components": {"V": {"kind": "step_voltage", "V": 1}, "R": {"kind": "resistor", "R": 1000},
                     "C": {"kind": "capacitor", "C": "1 uF"}, "g": {"kind": "ground"}},
      "connect": [["V.p", "R.p"], ["R.n", "C.p"], ["C.n", "g"], ["V.n", "g"]],
      "outputs": {"v": "C.v"}, "analysis": {"kind": "transient", "stop_time": 0.005}}

RLC = {"components": {"V": {"kind": "step_voltage", "V": 12, "startTime": 0.001}, "R": {"kind": "resistor", "R": 10},
                      "L": {"kind": "inductor", "L": "10 mH"}, "C": {"kind": "capacitor", "C": "100 uF"},
                      "gnd": {"kind": "ground"}},
       "connect": [["V.p", "R.p"], ["R.n", "L.p"], ["L.n", "C.p"], ["C.n", "gnd"], ["V.n", "gnd"]],
       "outputs": {"v_C": "C.v", "i": "L.i"},
       "analysis": {"kind": "transient", "stop_time": 0.05, "intervals": 2000}}

MSD = {"components": {"m": {"kind": "mass", "m": 2, "s0": 0.1}, "k": {"kind": "spring", "c": 800},
                      "d": {"kind": "damper", "d": 4}, "f": {"kind": "fixed"}},
       "connect": [["f.flange", "k.flange_a"], ["k.flange_b", "m.flange_a"], ["d.flange_a", "f.flange"],
                   ["d.flange_b", "m.flange_a"]],
       "outputs": {"s": "m.s"}, "analysis": {"kind": "transient", "stop_time": 2, "intervals": 400}}

THERMAL = {"components": {"blk": {"kind": "heat_capacitor", "m": 2, "cp": 900, "T0": "20 degC"},
                          "q": {"kind": "fixed_heat_flow", "Q_flow": 50},
                          "conv": {"kind": "convection", "h": 10, "A": 0.05},
                          "amb": {"kind": "fixed_temperature", "T": "20 degC"}},
           "connect": [["q.port", "blk.port"], ["blk.port", "conv.solid"], ["conv.fluid", "amb.port"]],
           "outputs": {"T": "blk.T"}, "analysis": {"kind": "transient", "stop_time": 20000, "intervals": 200},
           "derived": [{"kind": "crossing", "signal": "T", "level": "80 degC"}]}

DC_PI = {"components": {"M": {"kind": "dc_motor", "R": 1.0, "L": 0.5e-3, "k": 0.05, "J": 1e-4, "d": 1e-5},
                        "load": {"kind": "inertia", "J": 2e-4}, "Vs": {"kind": "signal_voltage"},
                        "g": {"kind": "ground"}, "ref": {"kind": "step", "height": 100, "startTime": 0.01},
                        "fb": {"kind": "feedback"}, "pi": {"kind": "pi", "k": 0.5, "T": 0.02},
                        "spd": {"kind": "rot_speed_sensor"}},
         "connect": [["Vs.p", "M.p"], ["M.n", "g"], ["Vs.n", "g"], ["M.flange", "load.flange_a"],
                     ["spd.flange", "load.flange_b"], ["ref.y", "fb.u1"], ["spd.w", "fb.u2"], ["fb.y", "pi.u"],
                     ["pi.y", "Vs.v"]],
         "outputs": {"w": "load.w", "i": "M.i"},
         "analysis": {"kind": "transient", "stop_time": 0.2, "intervals": 2000},
         "derived": [{"kind": "step", "signal": "w", "t0": 0.01}]}

TWO_MASS = {"components": {"J1": {"kind": "inertia", "J": 0.01}, "J2": {"kind": "inertia", "J": 0.04},
                           "sh": {"kind": "rot_spring_damper", "c": 1000, "d": 0.0},
                           "T": {"kind": "torque"}},
            "connect": [["T.flange", "J1.flange_a"], ["J1.flange_b", "sh.flange_a"], ["sh.flange_b", "J2.flange_a"]],
            "outputs": {"w2": "J2.w"},
            "analysis": {"kind": "linearize", "inputs": {"tau": "T.tau"}}}


def study(base, **changes):
    s = copy.deepcopy(base)
    s.update(changes)
    return s
