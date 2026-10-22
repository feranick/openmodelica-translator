"""refqs.py — the reference's quasi-static (phasor) parts (V3.4): Electrical.QuasiStatic sources, star, ground,
sensors, the QS terminal box and drives' blocks, and the Magnetic.QuasiStatic.FundamentalWave machines.
Version: 2026.10.21.25

Every complex quantity is a pair of real variables (name#re, name#im); a QS node's potential n<id>#re/#im, a port's
current <elem>.<port>#flow#re/#im (into the element). Phasors are RMS and refer to the circuit's reference angle
gamma (one per QS circuit: qsg<i>, its derivative omega qsw<i>), set by the circuit's one source.

The machines are written independently of MSL's magnetic network (derivation in planning.md, V3.4): per phase
E_k = V_k - R_k I_k; the converter forces the zero- and negative-sequence stator currents to 0; with the positive
sequences I_p, E_p (1/3 sum x_k e^(j phi_k)) and the stray current I_L = I_p - Gc E_p:
    E_p = j omega_s (Lssigma I_L + psi)
    IM:   psi = Lm (I_L + I_r),            Rr I_r + j omega_r (Lrsigma I_r + psi) = 0
    PMSM: psi = R(-gammar) [Lmd ((I_L + I_D') e^(j gammar))_d + Lmd Ie, Lmq ((I_L + I_D') e^(j gammar))_q],
          damper (Rrd + Rrq)/2 I_D' + j omega_r ((Lrsigmad, Lrsigmaq) . I_D' + psi) = 0
    tau = 3 p (psi_re I_L,im - psi_im I_L,re), omega_r = omega_s - p w, gammar = gamma_s - p phi.
The products with omega, the rotation and the torque are nonlinear terms (the V3.3 integrator); the electrical
side is algebraic: the states are the sources' angles, the shafts and the temperatures.
"""
from __future__ import annotations

import math

import numpy as np

from . import catalog as C

SQ2 = math.sqrt(2.0)
PHI3 = [k * 2 * math.pi / 3 for k in range(3)]
QS_PARTS = {"qs_ground", "qs_star", "qs_resistor_core", "qs_ac_voltage", "qs_variable_voltage", "qs_reference_current", "qs_vf_controller",
            "qs_current_controller", "qs_current_rms", "qs_voltage_rms", "qs_power_core", "qs_terminal_box",
            "qs_im_core", "qs_pmsm_core", "qs_pmsm_damper_core"}


# ---- complex helpers ---------------------------------------------------------------------------------
def qpot(c, port):
    base = f"n{c.net.node_of[f'{c.e}.{port}']}"
    return c.s.v(base + "#re"), c.s.v(base + "#im")


def qflow(c, port):
    return c.s.v(f"{c.e}.{port}#flow#re"), c.s.v(f"{c.e}.{port}#flow#im")


def cvar(c, name):
    return c.var(name + "#re"), c.var(name + "#im")


def csig(c, port):
    return c.sig(port + ".re"), c.sig(port + ".im")


def ceq(c, terms, const=0j):
    """sum a_k z_k + const = 0 for complex coefficients a_k and complex variables z_k = (re, im): two rows."""
    from .ref import Const
    re_, im_ = {}, {}
    for a, (zr, zi) in terms:
        a = complex(a)
        re_[zr] = re_.get(zr, 0.0) + a.real
        re_[zi] = re_.get(zi, 0.0) - a.imag
        im_[zi] = im_.get(zi, 0.0) + a.real
        im_[zr] = im_.get(zr, 0.0) + a.imag
    const = complex(const)
    c.eq({k: v for k, v in re_.items() if v != 0.0}, None, [(const.real, Const(1.0))] if const.real else None)
    c.eq({k: v for k, v in im_.items() if v != 0.0}, None, [(const.imag, Const(1.0))] if const.imag else None)


def _abs_fn(*cols):
    return np.sqrt(cols[0] ** 2 + cols[1] ** 2)


def _mean_abs(*cols):
    return sum(np.sqrt(cols[2 * k] ** 2 + cols[2 * k + 1] ** 2) for k in range(len(cols) // 2)) / (len(cols) // 2)


def _ph(port, k):
    return f"{port}.pin[{k}]"


# ---- circuits: one reference angle per quasi-static circuit -------------------------------------------
def islands(net) -> dict:
    """{element: circuit index} for the elements with quasi-static ports (joined by connections and inside
    each element: every QS port of an element shares its reference)."""
    if getattr(net, "_qs_islands", None) is not None:
        return net._qs_islands
    par = {}

    def find(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            par[max(ra, rb)] = min(ra, rb)
    elem_nodes = {}
    for ep, nid in net.node_of.items():
        if net.port_type(ep) == "qs_pin":
            elem_nodes.setdefault(ep.split(".")[0], []).append(nid)
    for e, ns in elem_nodes.items():
        for x in ns[1:]:
            union(ns[0], x)
    roots = sorted({find(ns[0]) for ns in elem_nodes.values()})
    rid = {r: i for i, r in enumerate(roots)}
    net._qs_islands = {e: rid[find(ns[0])] for e, ns in elem_nodes.items()}
    return net._qs_islands


def circuit(c):
    """(gamma, omega) variables of the element's quasi-static circuit."""
    i = islands(c.net)[c.e]
    return c.s.v(f"qsg{i}"), c.s.v(f"qsw{i}")


def _one_port(c):
    """The polyphase OnePort: v_k = p - n, i_k = flow into p, flows p + n = 0. Returns [(v, i)] per phase."""
    out = []
    for k in range(1, 4):
        v, i = cvar(c, f"v[{k}]"), cvar(c, f"i[{k}]")
        ceq(c, [(1, v), (-1, qpot(c, _ph("plug_p", k))), (1, qpot(c, _ph("plug_n", k)))])
        ceq(c, [(1, i), (-1, qflow(c, _ph("plug_p", k)))])
        ceq(c, [(1, qflow(c, _ph("plug_p", k))), (1, qflow(c, _ph("plug_n", k)))])
        out.append((v, i))
    pr = c.s.products
    pr[f"{c.e}.P_total"] = [x for v, i in out for x in ((1.0, {v[0]: 1.0}, {i[0]: 1.0}), (1.0, {v[1]: 1.0}, {i[1]: 1.0}))]
    pr[f"{c.e}.Q_total"] = [x for v, i in out for x in ((1.0, {v[1]: 1.0}, {i[0]: 1.0}), (-1.0, {v[0]: 1.0}, {i[1]: 1.0}))]
    return out


def _gamma_state(c, g, w):
    """A root source with its own angle: der(gamma) = omega, gamma(t0) = gamma0 (fixed)."""
    from .ref import _init
    c.eq({w: 1}, {g: -1})
    _init(c, "gamma0", {g: 1.0})


# ---- the parts -----------------------------------------------------------------------------------------
def stamp(c):
    from .ref import Const
    kn, p = c.d["kind"], c.p
    if kn == "qs_ground":
        ceq(c, [(1, qpot(c, "pin"))])
    elif kn == "qs_star":
        for k in range(1, 4):
            ceq(c, [(1, qpot(c, _ph("plug_p", k))), (-1, qpot(c, "pin_n"))])
        ceq(c, [(1, qflow(c, "pin_n"))] + [(1, qflow(c, _ph("plug_p", k))) for k in range(1, 4)])
    elif kn == "qs_resistor_core":
        for v, i in _one_port(c):
            ceq(c, [(1, v), (-p["R"], i)])
    elif kn == "qs_ac_voltage":
        g, w = circuit(c)
        for (v, _), phi in zip(_one_port(c), C.KINDS[kn]["mods"]["phi"](p)):
            ceq(c, [(1, v)], -p["V_rms"] * complex(math.cos(phi), math.sin(phi)))
        c.eq({w: 1}, None, [(-2 * math.pi * p["f"], Const(1.0))])
        _gamma_state(c, g, w)
    elif kn == "qs_variable_voltage":
        g, w = circuit(c)
        for k, (v, _) in enumerate(_one_port(c), 1):
            ceq(c, [(1, v), (-1, csig(c, f"V[{k}]"))])
        c.eq({w: 1, c.sig("f"): -2 * math.pi})
        _gamma_state(c, g, w)
    elif kn == "qs_reference_current":
        g, w = circuit(c)
        for k, (_, i) in enumerate(_one_port(c), 1):
            ceq(c, [(1, i), (-1, csig(c, f"I[{k}]"))])
        c.eq({g: 1, c.sig("gamma"): -1})
        c.eq({w: 1}, {g: -1})
    elif kn == "qs_vf_controller":
        # amplitude = VNominal (|u| < fNominal ? |u|/fNominal : 1); y_k = amplitude e^(j (-phi_k - pi/2))
        am = c.var("amplitude")
        VN, fN = p["VNominal"], p["fNominal"]
        c.eq({am: 1}, None, None, (lambda v, VN=VN, fN=fN: -VN * (abs(v[0]) / fN if abs(v[0]) < fN else 1.0), [c.sig("u")]))
        for k in range(1, 4):
            th = -PHI3[k - 1] - math.pi / 2
            yr, yi = csig(c, f"y[{k}]")
            c.eq({yr: 1, am: -math.cos(th)})
            c.eq({yi: 1, am: -math.sin(th)})
        c.path("amplitude", {am: 1.0})
    elif kn == "qs_current_controller":
        # gamma = p phi (+ gamma0 = 0); I_k = (id + j iq) e^(-j phi_k)
        pp = p["p"]
        c.eq({c.sig("gamma"): 1, c.sig("phi"): -pp})
        d, q = c.sig("id_rms"), c.sig("iq_rms")
        for k in range(1, 4):
            o = PHI3[k - 1]
            yr, yi = csig(c, f"I[{k}]")
            c.eq({yr: 1, d: -math.cos(o), q: -math.sin(o)})
            c.eq({yi: 1, q: -math.cos(o), d: math.sin(o)})
    elif kn in ("qs_current_rms", "qs_voltage_rms"):
        out = "I" if kn == "qs_current_rms" else "V"
        cols = []
        for k in range(1, 4):
            a, b = _ph("plug_p", k), _ph("plug_n", k)
            if out == "I":
                ceq(c, [(1, qpot(c, a)), (-1, qpot(c, b))])
                ceq(c, [(1, qflow(c, a)), (1, qflow(c, b))])
                cols += list(qflow(c, a))
            else:
                ceq(c, [(1, qflow(c, a))])
                ceq(c, [(1, qflow(c, b))])
                x = cvar(c, f"v[{k}]")
                ceq(c, [(1, x), (-1, qpot(c, a)), (1, qpot(c, b))])
                cols += list(x)
        _rms_out(c, out, cols)
    elif kn == "qs_power_core":
        vs, is_ = [], []
        for k in range(1, 4):
            cp, cn, vp, vn = (_ph(x, k) for x in ("currentP", "currentN", "voltageP", "voltageN"))
            ceq(c, [(1, qpot(c, cp)), (-1, qpot(c, cn))])
            ceq(c, [(1, qflow(c, cp)), (1, qflow(c, cn))])
            ceq(c, [(1, qflow(c, vp))])
            ceq(c, [(1, qflow(c, vn))])
            v = cvar(c, f"v[{k}]")
            ceq(c, [(1, v), (-1, qpot(c, vp)), (1, qpot(c, vn))])
            vs.append(v)
            is_.append(qflow(c, cp))
        pr = c.s.products
        pr[f"{c.e}.apparentPower.re"] = [x for v, i in zip(vs, is_) for x in ((1.0, {v[0]: 1.0}, {i[0]: 1.0}),
                                                                             (1.0, {v[1]: 1.0}, {i[1]: 1.0}))]
        pr[f"{c.e}.apparentPower.im"] = [x for v, i in zip(vs, is_) for x in ((1.0, {v[1]: 1.0}, {i[0]: 1.0}),
                                                                             (-1.0, {v[0]: 1.0}, {i[1]: 1.0}))]
    elif kn == "qs_terminal_box":
        for k in range(1, 4):
            sp, su, sn = _ph("plug_sp", k), _ph("plugSupply", k), _ph("plug_sn", k)
            ceq(c, [(1, qpot(c, sp)), (-1, qpot(c, su))])
            if p["terminal"] == "Y":
                ceq(c, [(1, qflow(c, sp)), (1, qflow(c, su))])
                ceq(c, [(1, qpot(c, sn)), (-1, qpot(c, "starpoint"))])
            else:
                prev = _ph("plug_sn", (k - 2) % 3 + 1)        # delta: plug_sn[j] joins plug_sp[j + 1]
                ceq(c, [(1, qpot(c, prev)), (-1, qpot(c, sp))])
                ceq(c, [(1, qflow(c, sp)), (1, qflow(c, su)), (1, qflow(c, prev))])
        if p["terminal"] == "Y":
            ceq(c, [(1, qflow(c, "starpoint"))] + [(1, qflow(c, _ph("plug_sn", k))) for k in range(1, 4)])
        else:
            ceq(c, [(1, qpot(c, "starpoint"))])          # no star point (its node: no current)
    else:
        _stamp_machine(c)


def _rms_out(c, out, cols):
    """A sensor's mean |x_k|: a nonlinear term when it drives blocks, else computed afterwards."""
    from .ref import _sig_used
    s = c.sig(out)
    if _sig_used(c, out):
        c.eq({s: 1}, None, None, (lambda v: -float(_mean_abs(*v)), cols))
    else:
        c.eq({s: 1})                                 # stand-in: the output is computed afterwards
        c.s.funcs[c.net.mpath(f"{c.e}.{out}")] = ("call", _mean_abs, cols)


def _cmul(a, b):
    return (a[0] * b[0] - a[1] * b[1], a[0] * b[1] + a[1] * b[0])


def _stamp_machine(c):
    from .ref import _alpha, _friction_torque, _init, _loss_torque
    kn, p = c.d["kind"], c.p
    pp = p["p"]
    th = c.d.get("enabled", set())
    gs, ws = circuit(c)
    a_s = _alpha(p["alpha20s"], p["TsRef"])
    w_el = 2 * math.pi * p["fsNominal"]
    phi, w, tau = c.var("phiMechanical"), c.var("wMechanical"), c.var("tauElectrical")
    gr, wr = c.var("gammar"), c.var("omegar")
    c.eq({gr: 1, gs: -1, phi: pp})
    c.eq({wr: 1, ws: -1, w: pp})
    # stator phases
    Vs, Is, Es = [], [], []
    for k in range(1, 4):
        sp, sn = _ph("plug_sp", k), _ph("plug_sn", k)
        V, I_, E = cvar(c, f"vs[{k}]"), cvar(c, f"is[{k}]"), cvar(c, f"e[{k}]")
        ceq(c, [(1, V), (-1, qpot(c, sp)), (1, qpot(c, sn))])
        ceq(c, [(1, I_), (-1, qflow(c, sp))])
        ceq(c, [(1, qflow(c, sp)), (1, qflow(c, sn))])
        hp = f"heatPortStatorWinding{k}"
        if hp in th and a_s != 0:
            T = c.pot(hp)
            for comp in (0, 1):
                c.eq({E[comp]: 1, V[comp]: -1, I_[comp]: p["Rs"]}, None, None,
                     (lambda v, R=p["Rs"], a_=a_s, Tr=p["TsRef"]: R * a_ * (v[1] - Tr) * v[0], [I_[comp], T]))
        else:
            R = p["Rs"] * (1 + a_s * (p["TsOperational"] - p["TsRef"])) if hp not in th else p["Rs"]
            ceq(c, [(1, E), (-1, V), (R, I_)])
        Vs.append(V)
        Is.append(I_)
        Es.append(E)
    # the converter: no zero- or negative-sequence current; positive sequences of I and E
    ceq(c, [(1, x) for x in Is])
    ceq(c, [(complex(math.cos(2 * o), math.sin(2 * o)), x) for o, x in zip(PHI3, Is)])
    Ip, Ep, IL, psi = cvar(c, "I_pos"), cvar(c, "E_pos"), cvar(c, "airGap.i_ss"), cvar(c, "psi")
    ceq(c, [(-3, Ip)] + [(complex(math.cos(o), math.sin(o)), x) for o, x in zip(PHI3, Is)])
    ceq(c, [(-3, Ep)] + [(complex(math.cos(o), math.sin(o)), x) for o, x in zip(PHI3, Es)])
    Gc = p["PRef_statorCore"] / (3 * p["VRef_statorCore"] ** 2) if (p.get("PRef_statorCore") or 0) > 0 else 0.0
    ceq(c, [(1, IL), (-1, Ip), (Gc, Ep)])
    Ls = p["Lssigma"]
    # E_p = j omega_s (Lssigma I_L + psi): re: E_re + omega_s (Ls IL_im + psi_im) = 0
    deps = [ws, IL[0], IL[1], psi[0], psi[1]]
    c.eq({Ep[0]: 1}, None, None, (lambda v, Ls=Ls: v[0] * (Ls * v[2] + v[4]), deps))
    c.eq({Ep[1]: 1}, None, None, (lambda v, Ls=Ls: -v[0] * (Ls * v[1] + v[3]), deps))
    rot_loss, rot_cols = None, []
    if kn == "qs_im_core":
        Lm, Lr = p["Lm"], p["Lrsigma"]
        a_r = _alpha(p["alpha20r"], p["TrRef"])
        Ir = cvar(c, "ir")
        ceq(c, [(1, psi), (-Lm, IL), (-Lm, Ir)])
        rot_hp = "heatPortRotorWinding" in th
        Rr = p["Rr"] * (1 + a_r * (p["TrOperational"] - p["TrRef"])) if not rot_hp else p["Rr"]
        Trw = c.pot("heatPortRotorWinding") if rot_hp and a_r != 0 else None
        # Rr I_r + j omega_r (Lrsigma I_r + psi) = 0
        rdeps = [wr, Ir[0], Ir[1], psi[0], psi[1]] + ([Trw] if Trw else [])

        def rotor(v, comp, Rr=Rr, a_r=a_r, Tr=p["TrRef"], Lr=Lr):
            dR = Rr * a_r * (v[5] - Tr) if len(v) > 5 else 0.0      # R(T) - Rr (the linear part carries Rr)
            if comp == 0:
                return dR * v[1] - v[0] * (Lr * v[2] + v[4])
            return dR * v[2] + v[0] * (Lr * v[1] + v[3])
        c.eq({Ir[0]: Rr}, None, None, (lambda v: rotor(v, 0), rdeps))
        c.eq({Ir[1]: Rr}, None, None, (lambda v: rotor(v, 1), rdeps))
        for k in range(1, 4):
            c.func(f"abs_ir[{k}]", _abs_fn, list(Ir))
        rot_loss, rot_cols = (lambda a, b, R_=Rr: 3 * R_ * (a ** 2 + b ** 2)), list(Ir)
        if Trw:
            rot_loss, rot_cols = (lambda a, b, T, R_=p["Rr"], a_=a_r, Tr=p["TrRef"]:
                                  3 * R_ * (1 + a_ * (T - Tr)) * (a ** 2 + b ** 2)), list(Ir) + [Trw]
    else:
        Lmd, Lmq = p["Lmd"], p["Lmq"]
        Ie = p["VsOpenCircuit"] / (Lmd * w_el)
        dmp = kn == "qs_pmsm_damper_core"
        ID = cvar(c, "iD") if dmp else None
        pdeps = [gr, IL[0], IL[1]] + (list(ID) if dmp else [])

        def flux(v, comp, Lmd=Lmd, Lmq=Lmq, Ie=Ie, dmp=dmp):
            g_ = v[0]
            x = (v[1] + (v[3] if dmp else 0.0), v[2] + (v[4] if dmp else 0.0))
            cg, sg = math.cos(g_), math.sin(g_)
            xr = (cg * x[0] - sg * x[1], sg * x[0] + cg * x[1])          # x e^(j gammar)
            pr_ = (Lmd * (xr[0] + Ie), Lmq * xr[1])
            ps = (cg * pr_[0] + sg * pr_[1], -sg * pr_[0] + cg * pr_[1])  # back: e^(-j gammar)
            return ps[comp]
        c.eq({psi[0]: 1}, None, None, (lambda v: -flux(v, 0), pdeps))
        c.eq({psi[1]: 1}, None, None, (lambda v: -flux(v, 1), pdeps))
        if dmp:
            Rd, Rq = p["Rrd"], p.get("Rrq") or p["Rrd"]
            Ld, Lq = p["Lrsigmad"], p.get("Lrsigmaq") or p["Lrsigmad"]
            a_r = _alpha(p["alpha20r"], p["TrRef"])
            rot_hp = "heatPortRotorWinding" in th
            fr = 1 + a_r * (p["TrOperational"] - p["TrRef"]) if not rot_hp else 1.0
            Trw = c.pot("heatPortRotorWinding") if rot_hp and a_r != 0 else None
            Ravg = 0.5 * (Rd + Rq) * fr
            ddeps = [wr, ID[0], ID[1], psi[0], psi[1]] + ([Trw] if Trw else [])

            def damper(v, comp, Ravg=Ravg, Ld=Ld, Lq=Lq, a_r=a_r, Tr=p["TrRef"]):
                dR = Ravg * a_r * (v[5] - Tr) if len(v) > 5 else 0.0
                if comp == 0:
                    return dR * v[1] - v[0] * (Lq * v[2] + v[4])
                return dR * v[2] + v[0] * (Ld * v[1] + v[3])
            c.eq({ID[0]: Ravg}, None, None, (lambda v: damper(v, 0), ddeps))
            c.eq({ID[1]: Ravg}, None, None, (lambda v: damper(v, 1), ddeps))
            rot_loss, rot_cols = (lambda a, b, R_=Ravg: 3 * R_ * (a ** 2 + b ** 2)), list(ID)
            if Trw:
                rot_loss, rot_cols = (lambda a, b, T, R_=0.5 * (Rd + Rq), a_=a_r, Tr=p["TrRef"]:
                                      3 * R_ * (1 + a_ * (T - Tr)) * (a ** 2 + b ** 2)), list(ID) + [Trw]
    # torque 3 p (psi_re IL_im - psi_im IL_re)
    c.eq({tau: 1}, None, None, (lambda v, pp=pp: -3 * pp * (v[2] * v[1] - v[3] * v[0]), [IL[0], IL[1], psi[0], psi[1]]))
    # mechanics: Jr dw/dt = tau + flange.tau - friction - stray load - magnet losses (iRMS = mean |I_k|)
    c.eq({phi: 1, c.pot("flange"): -1})
    c.eq({w: 1}, {phi: -1})
    fr_ = (p["PRef_friction"], p.get("wRef_friction") or w_el / pp, p["power_w_friction"])
    st_ = (p.get("PRef_strayLoad") or 0.0, p.get("IRef_strayLoad") or 1.0, p.get("wRef_strayLoad") or w_el / pp,
           p.get("power_w_strayLoad") or 1.0)
    pm_ = (p.get("PRef_pm") or 0.0, p.get("IRef_pm") or 1.0, p.get("wRef_pm") or w_el / pp)
    icols = [x for I_ in Is for x in I_]

    def irms(v):
        return sum(math.hypot(v[2 * k], v[2 * k + 1]) for k in range(3)) / 3

    def losses_t(w_, ir_):
        tf = _friction_torque(fr_[0], fr_[1], fr_[2], w_)
        ts = _loss_torque(st_[0], st_[2], st_[3], w_, (ir_ / st_[1]) ** 2)
        tp = _loss_torque(pm_[0], pm_[2], 1.0, w_, (ir_ / pm_[1]) ** 2) if pm_[0] > 0 and kn != "qs_im_core" else 0.0
        return tf, ts, tp
    c.eq({tau: -1, c.flow("flange"): -1}, {w: p["Jr"]}, None,
         (lambda v: sum(losses_t(v[0], irms(v[1:]))), [w] + icols))
    c.path("phiMechanical", {phi: 1.0})
    c.path("wMechanical", {w: 1.0})
    c.path("tauElectrical", {tau: 1.0})
    c.path("tauShaft", {c.flow("flange"): -1.0})
    _init(c, "w0", {w: 1.0})
    _init(c, "phi0", {phi: 1.0})
    # outputs
    for k in range(1, 4):
        c.func(f"abs_is[{k}]", _abs_fn, list(Is[k - 1]))
        c.func(f"abs_vs[{k}]", _abs_fn, list(Vs[k - 1]))
    pr = c.s.products
    ps_terms = [x for V, I_ in zip(Vs, Is) for x in ((1.0, {V[0]: 1.0}, {I_[0]: 1.0}), (1.0, {V[1]: 1.0}, {I_[1]: 1.0}))]
    pr[f"{c.e}.Ps_total"] = ps_terms
    pr[f"{c.e}.Qs_total"] = [x for V, I_ in zip(Vs, Is) for x in ((1.0, {V[1]: 1.0}, {I_[0]: 1.0}),
                                                                  (-1.0, {V[0]: 1.0}, {I_[1]: 1.0}))]
    pr[f"{c.e}.powerBalance.powerStator"] = ps_terms
    pr[f"{c.e}.powerBalance.powerMechanical"] = [(-1.0, {w: 1.0}, {c.flow("flange"): 1.0})]
    pr[f"{c.e}.powerBalance.lossPowerStatorCore"] = [(3 * Gc, {Ep[0]: 1.0}, {Ep[0]: 1.0}), (3 * Gc, {Ep[1]: 1.0}, {Ep[1]: 1.0})]
    pr[f"{c.e}.powerBalance.lossPowerRotorCore"] = [(0.0, {w: 1.0}, {w: 1.0})]
    Rs_op = p["Rs"] * (1 + a_s * (p["TsOperational"] - p["TsRef"]))
    tcols = []
    if any(f"heatPortStatorWinding{k}" in th for k in range(1, 4)) and a_s != 0:
        tcols = [c.pot(f"heatPortStatorWinding{k}") for k in range(1, 4)]

        def sw(*v, R=p["Rs"], a_=a_s, Tr=p["TsRef"]):
            return sum(R * (1 + a_ * (v[6 + k] - Tr)) * (v[2 * k] ** 2 + v[2 * k + 1] ** 2) for k in range(3))
    else:
        R_sw = p["Rs"] if any(f"heatPortStatorWinding{k}" in th for k in range(1, 4)) else Rs_op

        def sw(*v, R=R_sw):
            return sum(R * (v[2 * k] ** 2 + v[2 * k + 1] ** 2) for k in range(3))
    c.func("powerBalance.lossPowerStatorWinding", sw, icols + tcols)

    def irms_cols(cols):
        return sum(np.sqrt(cols[2 * k] ** 2 + cols[2 * k + 1] ** 2) for k in range(3)) / 3
    c.func("powerBalance.lossPowerFriction", lambda w_: np.vectorize(lambda x: _friction_torque(*fr_, x) * x)(w_), [w])
    c.func("powerBalance.lossPowerStrayLoad",
           lambda w_, *ic: np.vectorize(lambda x, r: _loss_torque(st_[0], st_[2], st_[3], x, (r / st_[1]) ** 2) * x)(
               w_, irms_cols(ic)), [w] + icols)
    if kn != "qs_im_core":
        c.func("powerBalance.lossPowerPermanentMagnet",
               lambda w_, *ic: np.vectorize(lambda x, r: (_loss_torque(pm_[0], pm_[2], 1.0, x, (r / pm_[1]) ** 2)
                                                          if pm_[0] > 0 else 0.0) * x)(w_, irms_cols(ic)), [w] + icols)
    if rot_loss is not None:
        c.func("powerBalance.lossPowerRotorWinding", rot_loss, rot_cols)
    allc = [w] + icols + list(Ep) + tcols + rot_cols
    nI, nE, nT = len(icols), 2, len(tcols)

    def total(*v):
        w_ = v[0]
        ic = v[1:1 + nI]
        ep = v[1 + nI:3 + nI]
        tc = v[3 + nI:3 + nI + nT]
        rc = v[3 + nI + nT:]
        out = sw(*(list(ic) + list(tc)))
        out = out + 3 * Gc * (ep[0] ** 2 + ep[1] ** 2)
        out = out + np.vectorize(lambda x: _friction_torque(*fr_, x) * x)(w_)
        r_ = irms_cols(ic)
        out = out + np.vectorize(lambda x, r: _loss_torque(st_[0], st_[2], st_[3], x, (r / st_[1]) ** 2) * x)(w_, r_)
        if kn != "qs_im_core" and pm_[0] > 0:
            out = out + np.vectorize(lambda x, r: _loss_torque(pm_[0], pm_[2], 1.0, x, (r / pm_[1]) ** 2) * x)(w_, r_)
        if rot_loss is not None:
            out = out + rot_loss(*rc)
        return out
    c.func("powerBalance.lossPowerTotal", total, allc)
    # heat ports: the losses leave as heat (nonlinear terms)
    if th:
        for k, I_ in zip(range(1, 4), Is):
            hp = f"heatPortStatorWinding{k}"
            if a_s != 0:
                c.eq({c.flow(hp): 1}, None, None,
                     (lambda v, R=p["Rs"], a_=a_s, Tr=p["TsRef"]: R * (1 + a_ * (v[2] - Tr)) * (v[0] ** 2 + v[1] ** 2),
                      list(I_) + [c.pot(hp)]))
            else:
                c.eq({c.flow(hp): 1}, None, None, (lambda v, R=p["Rs"]: R * (v[0] ** 2 + v[1] ** 2), list(I_)))
        c.eq({c.flow("heatPortStatorCore"): 1}, None, None, (lambda v, G=Gc: 3 * G * (v[0] ** 2 + v[1] ** 2), list(Ep)))
        c.eq({c.flow("heatPortRotorCore"): 1})
        c.eq({c.flow("heatPortFriction"): 1}, None, None, (lambda v: _friction_torque(*fr_, v[0]) * v[0], [w]))
        c.eq({c.flow("heatPortStrayLoad"): 1}, None, None,
             (lambda v: _loss_torque(st_[0], st_[2], st_[3], v[0], (irms(v[1:]) / st_[1]) ** 2) * v[0], [w] + icols))
        if "heatPortPermanentMagnet" in c.k["ports"]:
            c.eq({c.flow("heatPortPermanentMagnet"): 1}, None, None,
                 (lambda v: (_loss_torque(pm_[0], pm_[2], 1.0, v[0], (irms(v[1:]) / pm_[1]) ** 2)
                             if pm_[0] > 0 else 0.0) * v[0], [w] + icols))
        if "heatPortRotorWinding" in c.k["ports"]:
            c.eq({c.flow("heatPortRotorWinding"): 1}, None, None, (lambda v: float(rot_loss(*v)), rot_cols))
