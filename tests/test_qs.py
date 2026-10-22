"""test_qs.py — quasi-static machines and circuits (V3.4): the reference's compact phasor equations against MSL's
own component equations solved literally (an oracle written from the MSL 4.0.0 sources: windings, converters, stray
permeances and reluctances, eddy-current core, salient air gap, cage and damper, permanent magnet, magnetic grounds),
validation rules, the delta connection, the benchmarks' closed forms.
Version: 2026.10.21.25
"""
import copy
import math

import numpy as np
import pytest

from openmodelica_translator import bench, network
from openmodelica_translator.format import validate_study
from openmodelica_translator.ref import Ref

W50 = 2 * math.pi * 50
O = [k * 2 * math.pi / 3 for k in range(3)]
J = 1j


# ---- the oracle: MSL's equations, literally -----------------------------------------------------------
class Lin:
    """Complex unknowns, real-linear equations (re and im written separately where MSL splits d and q)."""

    def __init__(self, names):
        self.names = list(names)
        self.ix = {n: i for i, n in enumerate(self.names)}

    def solve(self, residual):
        n = 2 * len(self.names)

        def r(x):
            z = {nm: complex(x[2 * i], x[2 * i + 1]) for i, nm in enumerate(self.names)}
            return np.array(residual(z), float)
        r0 = r(np.zeros(n))
        A = np.column_stack([r(np.eye(n)[k]) - r0 for k in range(n)])
        assert A.shape[0] == n, (A.shape, n)
        x = np.linalg.solve(A, -r0)
        return {nm: complex(x[2 * i], x[2 * i + 1]) for i, nm in enumerate(self.names)}


def ri(c):
    return [c.real, c.imag]


def stator_eqs(z, V, p, w_s, Gc):
    """SymmetricPolyphaseWinding (N = 1) between magnetic nodes B (port_p) and A (port_n, = air gap port_sp,
    grounded): resistor, converter, core (EddyCurrent G = 3/2 Gc) between X and A, stray permeance B-A."""
    out = []
    for k in range(3):
        out += ri(V[k] - z[f"U{k}"] - p["Rs"] * z[f"I{k}"])
    Ipos = sum(z[f"I{k}"] * np.exp(J * O[k]) for k in range(3)) / 3
    out += ri(sum(z[f"I{k}"] * np.exp(2j * O[k]) for k in range(3)) / 3)           # negative sequence
    out += ri(sum(z[f"I{k}"] for k in range(3)) / 3)                                # zero sequence
    out += ri(z["VB"] - z["VX"] - math.sqrt(2) * (2 / math.pi) * 1.5 * Ipos)        # converter MMF
    Upos = sum(z[f"U{k}"] * np.exp(J * O[k]) for k in range(3)) / 3
    out += ri(-math.sqrt(2) * Upos - J * w_s * z["Pc"])                            # induced voltage
    out += ri((math.pi / 2) * z["VX"] - J * w_s * 1.5 * Gc * z["Pc"])               # eddy current core
    Gm = 2 * p["Lssigma"] / 3
    out += ri((math.pi / 2) * Gm * z["VB"] - z["Ps"])                               # stray permeance
    out += ri(z["Pc"] + z["Ps"] - z["Pag"])                                         # node B (air gap port_sn: -Pag)
    return out


STATOR = ["I0", "I1", "I2", "U0", "U1", "U2", "VB", "VX", "Pc", "Ps", "Pag"]


def msl_im(p, V, w_s, w, Gc=0.0):
    """IM_SquirrelCage: air gap (R = 3/(2 Lm)), cage between rn (grounded) and rp."""
    pp = p["p"]
    w_r = w_s - pp * w
    L = Lin(STATOR + ["VD", "Pcr", "Psr", "Ir0", "Ir1", "Ir2", "Ur0", "Ur1", "Ur2"])

    def res(z):
        out = stator_eqs(z, V, p, w_s, Gc)
        Vms, Vmr = -z["VB"], z["VD"]                       # sp grounded; rn grounded
        out += ri((math.pi / 2) * (Vms + Vmr) - 3 / (2 * p["Lm"]) * z["Pag"])
        # cage: port_p = rn (0), port_n = rp (VD): converter MMF V_C - V_D = -VD
        Ipos = sum(z[f"Ir{k}"] * np.exp(J * O[k]) for k in range(3)) / 3
        out += ri(-z["VD"] - math.sqrt(2) * (2 / math.pi) * 1.5 * Ipos)
        out += ri(sum(z[f"Ir{k}"] * np.exp(2j * O[k]) for k in range(3)) / 3)
        out += ri(sum(z[f"Ir{k}"] for k in range(3)) / 3)
        Upos = sum(z[f"Ur{k}"] * np.exp(J * O[k]) for k in range(3)) / 3
        out += ri(-math.sqrt(2) * Upos - J * w_r * z["Pcr"])
        for k in range(3):
            out += ri(z[f"Ur{k}"] + p["Rr"] * z[f"Ir{k}"])            # converter parallel to the resistor
        out += ri((math.pi / 2) * (-z["VD"]) - 3 / (2 * p["Lrsigma"]) * z["Psr"])
        out += ri(z["Pcr"] + z["Psr"] - z["Pag"])                       # node rp: cage flux = rotor flux
        return out
    s = L.solve(res)
    tau = -(math.pi * pp / 2) * (s["Pag"].imag * (-s["VB"]).real - s["Pag"].real * (-s["VB"]).imag)
    return s, tau


def msl_pmsm(p, V, w_s, w, gr, damper=True):
    """SM_PermanentMagnet: salient air gap (R_d,q = 3/(2 Lmd,q)) rotated by gammar; magnet MMF V_mPM e^(-j gammar)
    between rn (grounded, its port_p) and E; the damper (SaliencyCageWinding, m = 2, N = sqrt(3/2)) between E and
    rp (or a short)."""
    pp = p["p"]
    w_r = w_s - pp * w
    N = math.sqrt(1.5)
    VmPM = (2 / math.pi) * math.sqrt(2) * 1.5 * p["VsOpenCircuit"] / (p["Lmd"] * W50)
    names = STATOR + ["VD", "VE"] + (["PcD", "PsD", "D0", "D1", "UD0", "UD1"] if damper else [])
    L = Lin(names)
    e = np.exp(J * gr)

    def res(z):
        out = stator_eqs(z, V, p, w_s, 0.0)
        Vms, Vmr = -z["VB"], z["VD"]
        lhs = (math.pi / 2) * (Vms + Vmr) * e
        Prr = z["Pag"] * e
        out += [lhs.real - 3 / (2 * p["Lmd"]) * Prr.real, lhs.imag - 3 / (2 * p["Lmq"]) * Prr.imag]
        out += ri(0.0 - z["VE"] - VmPM / e)                              # magnet: V_rn - V_E = V_mPM e^(-j gr)
        if damper:
            Dpos = (z["D0"] + J * z["D1"]) / 2
            Vm = z["VE"] - z["VD"]
            out += ri(Vm - math.sqrt(2) * (2 / math.pi) * 1.0 * N * Dpos)
            out += ri((z["D0"] - J * z["D1"]) / 2)
            Upos = (z["UD0"] + J * z["UD1"]) / 2
            out += ri(-math.sqrt(2) * Upos - N * J * w_r * z["PcD"])
            out += ri(z["UD0"] + p["Rrd"] * z["D0"])
            out += ri(z["UD1"] + p["Rrq"] * z["D1"])
            Rd, Rq = N ** 2 / p["Lrsigmad"], N ** 2 / p["Lrsigmaq"]
            out += [(math.pi / 2) * Vm.real - Rd * z["PsD"].real, (math.pi / 2) * Vm.imag - Rq * z["PsD"].imag]
            out += ri(z["PcD"] + z["PsD"] - z["Pag"])                    # node rp
        else:
            out += ri(z["VE"] - z["VD"])                                 # short
        return out
    s = L.solve(res)
    tau = -(math.pi * pp / 2) * (s["Pag"].imag * (-s["VB"]).real - s["Pag"].real * (-s["VB"]).imag)
    return s, tau


def ref_at_start(machine, w0, phi0, V_rms=100.0):
    """The reference's algebraic state at t = 0 (machine on a QS grid at speed w0, angle phi0)."""
    st = {"components": {"g": {"kind": "qs_grid", "V_rms": V_rms, "f": 50}, "M": dict(machine, w0=w0, phi0=phi0),
                         "load": {"kind": "inertia", "J": 1.0}},
          "connect": [["g.plug", "M.plug"], ["M.flange", "load.flange_a"]]}
    net = network.build(st)
    assert not net.errors, net.errors
    r = Ref(net)
    r.simulate(np.array([0.0, 1e-9]))
    z = r.Z[0]
    g = lambda nm: z[r.sys.idx[nm]]
    I = [complex(g(f"M__m.is[{k}]#re"), g(f"M__m.is[{k}]#im")) for k in (1, 2, 3)]
    return I, g("M__m.tauElectrical")


IM = dict(bench.QS_IM)


@pytest.mark.parametrize("slip,Gc", [(0.03, 0.0), (0.2, 0.0), (0.05, 1 / 3)])
def test_im_against_msl_equations(slip, Gc):
    p = dict(IM)
    machine = dict(IM)
    if Gc:
        machine.update({"PRef_statorCore": Gc * 3 * 100.0 ** 2, "VRef_statorCore": 100.0})   # GcRef = PRef/(3 VRef^2)
    Gc_eff = Gc
    w = W50 / 2 * (1 - slip)
    V = [100 * np.exp(-J * o) for o in O]
    s, tau = msl_im(p, V, W50, w, Gc_eff)
    I, tau_ref = ref_at_start(machine, w, 0.3)
    for k in range(3):
        assert abs(I[k] - s[f"I{k}"]) < 1e-9 * abs(s["I0"]), (k, I[k], s[f"I{k}"])
    assert abs(tau_ref - tau) < 1e-9 * max(1.0, abs(tau))


@pytest.mark.parametrize("damper,w_frac,phi0", [(False, 1.0, 0.2), (True, 1.0, -0.4), (True, 0.97, 0.7)])
def test_pmsm_against_msl_equations(damper, w_frac, phi0):
    p = dict(bench.PM_DATA, kind="qs_pmsm_damper" if damper else "qs_pmsm", Lmq=0.25 / W50)   # salient
    if damper:
        p.update({"Lrsigmad": 0.05 / W50, "Lrsigmaq": 0.08 / W50, "Rrd": 0.04, "Rrq": 0.07})
    w = W50 / 2 * w_frac
    V = [112.3 * np.exp(-J * o) for o in O]
    gr = 0.0 - p["p"] * phi0
    s, tau = msl_pmsm(p, V, W50, w, gr, damper)
    I, tau_ref = ref_at_start(p, w, phi0, V_rms=112.3)
    for k in range(3):
        assert abs(I[k] - s[f"I{k}"]) < 1e-9 * abs(s["I0"]), (k, I[k], s[f"I{k}"])
    assert abs(tau_ref - tau) < 1e-9 * max(1.0, abs(tau))


# ---- circuits and rules -----------------------------------------------------------------------------------
def test_delta_equals_star_at_sqrt3():
    """An induction machine in delta on a grid of V/sqrt(3) sees the star machine's winding voltage: same torque."""
    w = W50 / 2 * 0.97
    _, tau_y = ref_at_start(dict(IM), w, 0.0, V_rms=100.0)
    _, tau_d = ref_at_start(dict(IM, terminal="D"), w, 0.0, V_rms=100.0 / math.sqrt(3))
    assert abs(tau_d - tau_y) < 1e-9 * abs(tau_y)


def _grid_machine(**extra):
    st = {"components": {"g": {"kind": "qs_grid", "V_rms": 100, "f": 50}, "M": dict(IM),
                         "load": {"kind": "inertia", "J": 1.0}},
          "connect": [["g.plug", "M.plug"], ["M.flange", "load.flange_a"]]}
    st["components"].update(extra.get("comps", {}))
    st["connect"] += extra.get("conns", [])
    return st


def test_mixing_transient_and_qs_plugs_is_an_error():
    st = _grid_machine(comps={"r": {"kind": "ac_resistor", "R": 1.0}}, conns=[["g.plug", "r.plug_p"]])
    errs = validate_study(st)["errors"]
    assert any("quasi-static" in e and "cannot be joined" in e for e in errs), errs


def test_qs_circuit_needs_exactly_one_source():
    st = _grid_machine(comps={"g2": {"kind": "qs_grid", "V_rms": 100, "f": 50}}, conns=[["g2.plug", "M.plug"]])
    errs = validate_study(st)["errors"]
    assert any("2 quasi-static sources" in e for e in errs), errs


def test_qs_port_values_are_not_outputs():
    st = _grid_machine()
    st["outputs"] = {"v": "M.plug.pin[1].v"}
    errs = validate_study(st)["errors"]
    assert any("quasi-static port" in e for e in errs), errs


def test_energy_not_covered_for_synchronous_machine_on_a_grid():
    from openmodelica_translator import energy
    st = bench.cases()["qs_pmsm_damper_grid"]["study"]
    pl = energy.plan(network.build(st))
    assert not pl["covered"] and "slips" in pl["why"]
    st = bench.cases()["qs_pmsm_current_torque"]["study"]
    assert energy.plan(network.build(st))["covered"]


@pytest.mark.parametrize("name", ["qs_im_grid_slip", "qs_pmsm_current_torque", "qs_im_vf_start", "qs_pmsm_damper_grid"])
def test_qs_benchmarks_with_the_reference(name, tmp_path):
    r = bench.run("ref", [name], tmp_path)[name]
    assert r["passed"], r
