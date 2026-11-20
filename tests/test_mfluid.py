"""test_mfluid.py — Modelica.Fluid kinds (V5 FL2): validation, the closed-loop rule, the generated Modelica.
Version: 2026.11.19.12

Runs of the generated models are checked on the machines (planning.md V5); here: what omct writes and refuses.
"""
import copy

import pytest

from openmodelica_translator.format import validate_study
from openmodelica_translator.modelica import translate
from openmodelica_translator.network import build

PUMP = {"kind": "fl_pump", "curve": [[0, 20], [0.005, 15], [0.01, 0]], "N_nominal": "1500 rpm"}
LOOP = {"components": {"tank": {"kind": "fl_tank", "height": 5, "crossArea": 2, "level_start": 1, "port_diameter": 0.05},
                       "pump": dict(PUMP),
                       "pipe": {"kind": "fl_pipe", "length": 20, "diameter": 0.05},
                       "v": {"kind": "fl_valve", "Kv": 20, "dp_nominal": 1e5, "m_flow_nominal": 5, "opening": 0.8}},
        "connect": [["tank.port", "pump.port_a"], ["pump.port_b", "pipe.port_a"], ["pipe.port_b", "v.port_a"],
                    ["v.port_b", "tank.port"]],
        "outputs": {"m": "pump.m_flow", "level": "tank.level", "dp": "pipe.dp"},
        "analysis": {"kind": "transient", "stop_time": 100}}


def loop(**changes):
    st = copy.deepcopy(LOOP)
    for k, v in changes.items():
        st["components"][k] = v
    return st


def errors(st):
    return " | ".join(validate_study(st)["errors"])


def test_loop_is_valid_and_translates():
    v = validate_study(LOOP)
    assert v["ok"], v["errors"]
    tr = translate(LOOP)
    t = tr.text
    assert "package omct_fmedium_1 = Modelica.Media.Water.ConstantPropertyLiquidWater" in t
    assert "redeclare package Medium = omct_fmedium_1" in t
    assert "inner Modelica.Fluid.System system(energyDynamics=Modelica.Fluid.Types.Dynamics.FixedInitial" in t
    assert "nPorts=2" in t and "connect(pump.port_a, tank.ports[1]);" in t and "connect(tank.ports[2], v.port_b);" in t
    assert "parameter Real omct_pump_head_curve[3] = {20.0, 15.0, 0.0};" in t
    assert "redeclare function flowCharacteristic = Modelica.Fluid.Machines.BaseClasses.PumpCharacteristics." \
           "quadraticFlow(V_flow_nominal=omct_pump_V_curve, head_nominal=omct_pump_head_curve)" in t
    assert "opening=omct_v_opening" in t and "CvData=Modelica.Fluid.Types.CvTypes.Kv" in t
    assert tr.overrides["omct_pump_head_curve[2]"] == 15.0 and tr.overrides["omct_v_opening"] == 0.8
    assert tr.overrides["pump.N_nominal"] == pytest.approx(1500.0)          # rad/s in the study, rev/min in MSL
    assert tr.outputs["dp"] == ([(1.0, "pipe.port_a.p"), (-1.0, "pipe.port_b.p")], "Pa")
    assert "?" in tr.structure and "omct_pump_head_curve[3] = {?, ?, ?}" in tr.structure  # values not in the key
    assert tr.ref.exact and getattr(tr.ref, "fluid", False)        # tank port losses are in the reference (FL7)


def test_medium_given_once_spreads():
    st = loop(pump={**PUMP, "medium": {"rho": 1050, "cp": 3800, "eta": 0.004}})
    net = build(st)
    assert not net.errors
    assert {repr(d["params"]["medium"]) for d in net.elems.values()} == {repr({"cp": 3800.0, "eta": 0.004, "rho": 1050.0})}
    t = translate(st).text
    assert "ConstantPropertyLiquidWater(cp_const=3800.0, eta_const=0.004, d_const=1050.0, cv_const=3800.0)" in t


@pytest.mark.parametrize("change, msg", [
    (lambda s: (s["components"]["pump"].update(medium="air"), s["components"]["tank"].update(medium="water")),
     "mixes media"),
    (lambda s: s["components"].update(tank={"kind": "fl_volume", "V": 0.01}), "nothing that sets its pressure"),
    (lambda s: s["components"]["v"].update(Cv=3), "one of Kv, Cv, Av"),
    (lambda s: s["components"]["pump"].update(curve=[[0, 10], [0.01, 12]]), "head must fall"),
    (lambda s: s["components"]["pump"].update(medium="oil"), "unknown Modelica.Fluid medium"),
    (lambda s: s["components"].update(system={"kind": "ground"}), "taken by fl_system"),
    (lambda s: s["components"].update(sys={"kind": "fl_system"}), "must be named 'system'"),
    (lambda s: s["components"].update(p={"kind": "fl_pressure"}), "p.port is not connected"),
    (lambda s: s["components"].update(b={"kind": "fl_boundary"}), "b.port is not connected"),
])
def test_validation(change, msg):
    st = copy.deepcopy(LOOP)
    change(st)
    assert msg in errors(st)


def test_valves_follow_the_phase():
    st = loop()
    st["components"]["tank"]["medium"] = "air"
    st["components"]["pump"] = {"kind": "fl_mass_flow", "m_flow": 0.01}
    st["connect"] = [["pump.port", "pipe.port_a"], ["pipe.port_b", "v.port_a"], ["v.port_b", "tank.port"]]
    st["components"]["tank"] = {"kind": "fl_boundary", "medium": "air"}
    st["outputs"] = {"m": "v.m_flow", "dp": "pipe.dp"}
    assert "fl_valve is for liquids" in errors(st)
    st["components"]["v"] = {"kind": "fl_valve_gas", "Kv": 20, "dp_nominal": 1e5, "m_flow_nominal": 5,
                             "p_nominal": 2e5}
    assert validate_study(st)["ok"], errors(st)
    assert "Modelica.Media.Air.DryAirNasa" in translate(st).text


def test_closed_loop_rule():
    # a dynamic pipe right after the pump: allowed, av_b (carbonio: the only volume that builds on such a loop)
    st = loop(pipe={"kind": "fl_dynamic_pipe", "length": 20, "diameter": 0.05, "nNodes": 3})
    assert validate_study(st)["ok"], errors(st)
    assert "modelStructure=Modelica.Fluid.Types.ModelStructure.av_b" in translate(st).text
    # after the valve (on the loop, not at the pump): refused with the fix
    st["connect"] = [["tank.port", "pump.port_a"], ["pump.port_b", "v.port_a"], ["v.port_b", "pipe.port_a"],
                     ["pipe.port_b", "tank.port"]]
    assert "closed loop of a constant-density liquid" in errors(st)
    # a closed volume on the loop
    st = loop()
    st["components"]["cv"] = {"kind": "fl_volume", "V": 0.01}
    st["connect"] = [["tank.port", "pump.port_a"], ["pump.port_b", "cv.port"], ["cv.port", "pipe.port_a"],
                     ["pipe.port_b", "v.port_a"], ["v.port_b", "tank.port"]]
    assert "fl_volume on a closed loop" in errors(st)
    # open circuit: no restriction, MSL's av_vb
    st = loop(pipe={"kind": "fl_dynamic_pipe", "length": 20, "diameter": 0.05})
    st["components"]["out"] = {"kind": "fl_boundary"}
    st["connect"] = [["tank.port", "pump.port_a"], ["pump.port_b", "v.port_a"], ["v.port_b", "pipe.port_a"],
                     ["pipe.port_b", "out.port"]]
    assert validate_study(st)["ok"], errors(st)
    assert "ModelStructure.av_vb" in translate(st).text


def test_heat_port_collector_and_speed_input():
    st = loop(pipe={"kind": "fl_dynamic_pipe", "length": 20, "diameter": 0.05, "nNodes": 4,
                    "heat_transfer": "constant", "alpha": 500},
              pump={**PUMP})
    st["components"]["wall"] = {"kind": "fixed_temperature", "T": 330}
    st["components"]["w"] = {"kind": "constant", "k": 150}
    st["connect"] += [["pipe.heat", "wall.port"], ["w.y", "pump.w_in"]]
    st["outputs"]["Q"] = "pipe.heat.Q_flow"
    st["outputs"]["speed"] = "pump.w"
    assert validate_study(st)["ok"], errors(st)
    tr = translate(st)
    t = tr.text
    assert "ThermalCollector omct_pipe_coll(m=4)" in t and "connect(omct_pipe_coll.port_a, pipe.heatPorts);" in t
    assert "connect(omct_pipe_coll.port_b, wall.port);" in t and "use_HeatTransfer=true" in t
    assert "ConstantFlowHeatTransfer(alpha0=omct_pipe_alpha)" in t
    assert "Gain omct_pump_rpm(k=9.549" in t and "connect(omct_pump_rpm.y, pump.N_in);" in t
    assert "connect(omct_pump_rpm.u, w.y);" in t and "use_N_in=true" in t
    assert tr.outputs["Q"][0] == [(1.0, "omct_pipe_coll.port_b.Q_flow")]
    assert tr.outputs["speed"][0][0][1] == "pump.N"
    st["components"]["pump"]["N"] = 100
    assert "N or through w_in, not both" in errors(st)


def test_fl_system_and_valve_input():
    st = loop()
    st["components"]["system"] = {"kind": "fl_system", "dynamics": "steady_state_initial", "T_ambient": "25 degC"}
    st["components"]["op"] = {"kind": "step", "height": 0.5, "offset": 0.3}
    st["components"]["v"] = {"kind": "fl_valve", "Kv": 20, "dp_nominal": 1e5, "m_flow_nominal": 5}
    st["connect"].append(["op.y", "v.opening"])
    assert validate_study(st)["ok"], errors(st)
    t = translate(st).text
    assert "inner Modelica.Fluid.System system(" in t and "SteadyStateInitial" in t
    assert t.count("Modelica.Fluid.System") == 1 and "omct_v_opening" not in t
    st["components"]["v"]["opening"] = 0.4
    assert "opening is given and its input connected" in errors(st)


def test_fluid_topic_lists_the_kinds():
    from openmodelica_translator.format import study_format
    f = study_format("fluid")
    assert {"fl_pipe", "fl_pump", "fl_tank", "fl_valve", "fl_boundary"} <= set(f["kinds"])
    assert "fl_pipe" not in str(study_format()["kinds"])


# ---- FL3: the checks Modelica.Fluid studies get ---------------------------------------------------------------------
def test_cross_solver_and_mass_balance_for_fluid_studies():
    import numpy as np

    from openmodelica_translator.format import cross_checked, has_fluid
    from openmodelica_translator.runner import _fluid_mass, fluid_mass_terms
    glycol = loop(tank={**LOOP["components"]["tank"], "medium": "glycol47"})      # not in the reference
    assert has_fluid(LOOP) and cross_checked(glycol) and not cross_checked(LOOP)
    assert not cross_checked({**LOOP, "cross_check": False})
    assert validate_study({**LOOP, "cross_check": False})["ok"]
    st = {"components": {"tank": {"kind": "fl_tank", "height": 3, "crossArea": 0.5, "level_start": 2},
                         "o": {"kind": "fl_orifice", "diameter": 0.03, "zeta": 1.0}, "out": {"kind": "fl_boundary"}},
          "connect": [["tank.port", "o.port_a"], ["o.port_b", "out.port"]], "outputs": {"l": "tank.level"},
          "analysis": {"kind": "transient", "stop_time": 200}}
    tr = translate(st)
    assert fluid_mass_terms(tr) == ([(-1.0, "out.ports[1].m_flow")], ["tank.m"])
    t = np.linspace(0, 200, 41)
    k = np.pi * 0.03 ** 2 / 4 / (2 * 0.5) * np.sqrt(2 * 9.80665)
    h = (np.sqrt(2) - k * t) ** 2
    m, mf = 1000 * 0.5 * h, 1000 * np.pi * 0.03 ** 2 / 4 * np.sqrt(2 * 9.80665 * h)

    class R:
        time = t

        def __init__(self, f=1.0):
            self.f = f

        def series(self, p):
            return {"out.ports[1].m_flow": mf * self.f, "tank.m": m}[p]
    out = {"checks": {}}
    _fluid_mass(out, tr, R())
    assert out["checks"]["mass"]["ok"] and out["checks"]["mass"]["error_rel"] < 1e-9
    out = {"checks": {}}
    _fluid_mass(out, tr, R(1.001))                       # 0.1 % of the flow missing
    assert not out["checks"]["mass"]["ok"]


def test_msl_notes_are_not_warnings():
    from openmodelica_translator.runner import OM_NOTES
    assert any("delta_head_init" in x for x in OM_NOTES)


def test_pump_volume_on_a_closed_liquid_loop_is_refused():
    """carbonio run_fl7_probe6: structurally singular, as fl_volume there (a check valve needs V > 0)."""
    st = loop(pump=dict(PUMP, V=0.001, checkValve=True))
    assert "a pump with a volume (V > 0, which checkValve needs)" in errors(st)
    st = loop(pump=dict(PUMP, V=0.001))
    assert "a pump with a volume (V > 0)" in errors(st)


def test_fluid_energy_balance():
    """FL8: d/dt sum(U) = H in at the boundaries + sum(Qb + Wb) (carbonio run_fl8_probe3: the same terms close to
    2e-7 in OpenModelica's own integration)."""
    import numpy as np

    from openmodelica_translator.runner import _fluid_energy, boundary_enthalpy_in, fluid_energy_paths, \
        fluid_energy_terms
    st = {"components": {"a": {"kind": "fl_boundary", "p": 2e5, "T": 300.0},
                         "o": {"kind": "fl_orifice", "diameter": 0.03, "zeta": 1.0},
                         "tank": {"kind": "fl_tank", "height": 5, "crossArea": 0.5, "level_start": 1},
                         "heater": {"kind": "fixed_heat_flow", "Q_flow": 1000.0}},
          "connect": [["a.port", "o.port_a"], ["o.port_b", "tank.port"], ["tank.heatPort", "heater.port"]],
          "outputs": {"l": "tank.level"}, "analysis": {"kind": "transient", "stop_time": 100}}
    assert validate_study(st)["ok"], validate_study(st)["errors"]
    tr = translate(st)
    bnd, src, store = fluid_energy_terms(tr)
    assert list(bnd) == ["a.ports[1]"] and bnd["a.ports[1]"][2] == [("o.port_a.m_flow", "o.port_a.h_outflow")]
    assert src == ["tank.Qb_flow", "tank.Wb_flow", "tank.ports_E_flow[1]"] and store == ["tank.U"]
    assert "o.port_a.h_outflow" in fluid_energy_paths(tr)
    t = np.linspace(0, 100, 101)
    m = 2.0 * np.exp(-t / 50)                       # out of the boundary into the tank: a.ports[1].m_flow = -m
    h = 1.2e5
    Q = np.full_like(t, 1000.0)
    U = 4e7 + h * 100 * (1 - np.exp(-t / 50)) + 1000 * t

    class R:
        time = t

        def __init__(self, f=1.0):
            self.f = f

        def series(self, p):
            return {"a.ports[1].m_flow": -m, "a.ports[1].h_outflow": np.full_like(t, h),
                    "o.port_a.m_flow": m, "o.port_a.h_outflow": np.full_like(t, 9e4),
                    "tank.Qb_flow": Q * self.f, "tank.Wb_flow": 0 * t, "tank.ports_E_flow[1]": 0 * t,
                    "tank.U": U}[p]
    out = {"checks": {}}
    _fluid_energy(out, tr, R())
    assert out["checks"]["fluid_energy"]["ok"], out["checks"]["fluid_energy"]
    out = {"checks": {}}
    _fluid_energy(out, tr, R(1.5))                  # half the heat missing from the balance
    assert not out["checks"]["fluid_energy"]["ok"]
    # receiving: inStream from the other ports, mixed by their outflows
    H = boundary_enthalpy_in(np.array([2.0]), np.array([1.0]), [(np.array([-1.0]), np.array([10.0])),
                                                                (np.array([-3.0]), np.array([30.0])),
                                                                (np.array([2.0]), np.array([99.0]))])
    assert H[0] == pytest.approx(-2.0 * 25.0)
