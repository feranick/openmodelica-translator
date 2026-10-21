"""network.py — a study's components, values and connections, resolved: the common ground of validation, the
Modelica generator and the reference implementation.
Version: 2026.10.21.1

build(study) -> Net, with net.errors (each says the fix) and net.warnings. It
  - resolves kinds (or "msl" class paths) and parameter values to SI (units.to_si), with defaults;
  - expands composite kinds into elementary ones: component X, part p -> element "X__p";
  - maps study ports ("X.p"), variables ("X.i", "X.p.v") and initial values to elements and Modelica paths;
  - joins physical ports into nodes (union-find; rigid pairs such as a mass's two flanges join too) and signal
    outputs to their inputs.
Nothing here depends on OpenModelica.
"""
from __future__ import annotations

import math
import re

from . import catalog as C
from .units import UnitError, to_si

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
KEYWORDS = set("""algorithm and annotation block break class connect connector constant constrainedby der discrete
each else elseif elsewhen encapsulated end enumeration equation expandable extends external false final flow for
function if import impure in initial inner input loop model not operator or outer output package parameter partial
protected public pure record redeclare replaceable return stream then true type when while within time Modelica
Real Integer Boolean String""".split())


class Net:
    def __init__(self):
        self.comps: dict[str, dict] = {}       # study name -> {kind, params (SI), given, inits (SI), records}
        self.elems: dict[str, dict] = {}       # element name -> {kind, params (SI), inits, comp, part}
        self.conns: list[tuple[str, str]] = []  # element ports "E.port", in study order then internal ones
        self.study_conns: list[tuple[str, str]] = []
        self.inputs: dict[str, dict] = {}      # unit conversions: "X.param" -> record
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.node_of: dict[str, int] = {}      # physical element port -> node id
        self.signal_of: dict[str, int] = {}    # signal element port -> net id
        self.free_inputs: dict[str, str] = {}  # linearisation input name -> element port

    # ---- ports and variables ------------------------------------------------------------------------
    def elem_port(self, comp: str, port: str) -> str:
        """Study port -> element port 'E.port'."""
        k = C.KINDS[self.comps[comp]["kind"]]
        if k.get("parts") is not None:
            part, p = k["ports"][port].split(".", 1)
            return f"{comp}__{part}.{p}"
        return f"{comp}.{port}"

    def port_type(self, eport: str) -> str:
        e, p = eport.split(".", 1)
        return C.KINDS[self.elems[e]["kind"]]["ports"][p]

    def mpath(self, eport: str) -> str:
        """Element port -> Modelica path (table.y -> table.y[1])."""
        e, p = eport.split(".", 1)
        return f"{e}." + C.PORT_PATHS.get((self.elems[e]["kind"], p), p)

    def var_expr(self, ref: str):
        """'X.var', 'X.port' (signal) or 'X.port.v' -> (list of (coef, Modelica path), unit), or raises KeyError
        with a message."""
        parts = ref.split(".")
        comp = parts[0]
        if comp not in self.comps:
            raise KeyError(f"no component '{comp}'")
        kname = self.comps[comp]["kind"]
        k = C.KINDS[kname]
        pts = C.port_types(kname)
        if len(parts) == 2:
            v = parts[1]
            if v in k["vars"]:
                if k.get("parts") is not None:
                    expr, unit = k["vars"][v]
                    terms = []
                    for t in expr.split("+"):
                        part, pv = t.strip().split(".", 1)
                        terms += self.var_expr_elem(f"{comp}__{part}", pv)[0]
                    return terms, unit
                return [(1.0, f"{comp}.{v}")], k["vars"][v]
            if v in pts and pts[v] in ("in", "out"):
                ep = self.elem_port(comp, v)
                e, p = ep.split(".", 1)
                return [(1.0, self.mpath(ep))], C.OUT_UNITS.get((self.elems[e]["kind"], p), "1")
        if len(parts) == 3 and parts[1] in pts and pts[parts[1]] not in ("in", "out"):
            pt = C.PORT_TYPES[pts[parts[1]]]
            if parts[2] in (pt["potential"], pt["flow"]):
                ep = self.elem_port(comp, parts[1])
                return [(1.0, self.mpath(ep) + "." + parts[2])], pt["units"][parts[2]]
        raise KeyError(f"'{ref}': {kname} has variables {sorted(k['vars'])}, signal ports "
                       f"{sorted(p for p, t in pts.items() if t in ('in', 'out'))} and physical ports "
                       f"{sorted(p for p, t in pts.items() if t not in ('in', 'out'))} (port.v / port.i, "
                       f"port.s / port.f, port.phi / port.tau, port.T / port.Q_flow)")

    def var_expr_elem(self, elem: str, v: str):
        k = C.KINDS[self.elems[elem]["kind"]]
        if v in k["vars"]:
            return [(1.0, f"{elem}.{v}")], k["vars"][v]
        if "." in v:
            p, q = v.split(".", 1)
            return [(1.0, self.mpath(f"{elem}.{p}") + "." + q)], ""
        raise KeyError(f"{elem}.{v}")


def _value(spec, v, where, rec_key, net):
    typ = spec["type"]
    if typ == "bool":
        if not isinstance(v, bool):
            raise UnitError(f"{where}: true or false")
        return v
    if typ == "int":
        if not isinstance(v, int) or isinstance(v, bool):
            raise UnitError(f"{where}: an integer")
        return v
    if typ == "enum":
        if v not in spec["choices"]:
            raise UnitError(f"{where}: one of {spec['choices']}")
        return v
    if typ == "array":
        if not isinstance(v, list) or not v or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            raise UnitError(f"{where}: a non-empty list of numbers")
        return [float(x) for x in v]
    if typ == "table":
        ok = isinstance(v, list) and len(v) >= 1 and all(isinstance(r, list) and len(r) == 2 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in r) for r in v)
        if not ok:
            raise UnitError(f"{where}: a list of [t, y] pairs, e.g. [[0, 0], [1, 2], [3, 2]]")
        ts = [r[0] for r in v]
        if any(b <= a for a, b in zip(ts, ts[1:])):
            raise UnitError(f"{where}: times must increase strictly")
        return [[float(a), float(b)] for a, b in v]
    si, rec = to_si(v, spec["unit"], absolute_temperature=spec.get("abs_T", False), where=where)
    if rec is not None and not isinstance(v, (int, float)):
        net.inputs[rec_key] = rec
    if not math.isfinite(si):
        raise UnitError(f"{where}: not a finite number")
    return si


def _check_range(spec, v, where, errors):
    if not isinstance(v, float):
        return
    if spec["min"] is not None and v < spec["min"]:
        errors.append(f"{where} = {v:g}: must be >= {spec['min']:g}")
    if spec["gt"] is not None and v <= spec["gt"]:
        errors.append(f"{where} = {v:g}: must be > {spec['gt']:g}")
    if spec["max"] is not None and v > spec["max"]:
        errors.append(f"{where} = {v:g}: must be <= {spec['max']:g}")


def resolve_params(kname: str, given: dict, where: str, net: Net, rec_prefix: str):
    """Study parameters of one component -> SI values (defaults filled; None for alternatives not given)."""
    k = C.KINDS[kname]
    out = {}
    alt_members = {p for grp in k["alternatives"] for p in grp}
    for p, spec in k["params"].items():
        if p in given:
            try:
                out[p] = _value(spec, given[p], f"{where}.{p}", f"{rec_prefix}.{p}", net)
            except UnitError as exc:
                net.errors.append(str(exc))
                out[p] = None
                continue
            _check_range(spec, out[p], f"{where}.{p}", net.errors)
        elif spec["default"] is C.REQ:
            if p not in alt_members:
                net.errors.append(f"{where}: missing parameter '{p}' ({spec['unit']}{'; ' + spec['doc'] if spec['doc'] else ''})")
            out[p] = None
        else:
            out[p] = spec["default"]
    if k["alternatives"]:
        some = {p for p in alt_members if p in given}
        if not any(some == set(grp) for grp in k["alternatives"]):
            opts = " or ".join(" + ".join(g) for g in k["alternatives"])
            net.errors.append(f"{where}: give exactly one of {opts} (given: {sorted(some) or 'none'})")
    return out


def resolve_inits(kname: str, given: dict, params: dict, where: str, net: Net, rec_prefix: str):
    k = C.KINDS[kname]
    out = {}
    for name, spec in k["inits"].items():
        if name in given:
            try:
                si, rec = to_si(given[name], spec["unit"], absolute_temperature=spec["abs_T"], where=f"{where}.{name}")
                if rec is not None and not isinstance(given[name], (int, float)):
                    net.inputs[f"{rec_prefix}.{name}"] = rec
                out[name] = (si, True)
            except UnitError as exc:
                net.errors.append(str(exc))
        else:
            d = spec["default"]
            out[name] = (params.get(d, 0.0) if isinstance(d, str) else d, False)
    return out


def _elementary_params(kname: str, sub: dict, comp_params: dict):
    """Part parameters of a composite from the composite's values."""
    out = {}
    for pp, src in sub.items():
        out[pp] = src(comp_params) if callable(src) else comp_params.get(src)
    return out


def build(study: dict) -> Net:
    net = Net()
    comps = study.get("components")
    if not isinstance(comps, dict) or not comps:
        net.errors.append("components: a non-empty object {name: {kind, parameters...}}")
        return net
    msl_map = C.msl_to_kind()
    for name in sorted(comps):
        spec = comps[name]
        w = f"components.{name}"
        if not NAME.match(name) or name in KEYWORDS or "__" in name or name.lower().startswith("omct"):
            net.errors.append(f"{w}: names are identifiers (a letter, then letters, digits, '_'; at most 40; no '__', "
                              f"no Modelica keyword, not starting with 'omct')")
            continue
        if not isinstance(spec, dict):
            net.errors.append(f"{w}: an object with 'kind' and parameters")
            continue
        kname = spec.get("kind")
        if kname is None and "msl" in spec:
            kname = msl_map.get(spec["msl"])
            if kname is None:
                net.errors.append(f"{w}: msl class '{spec['msl']}' is not in the catalog; use a kind (study_format lists them)")
                continue
        if kname not in C.KINDS or C.KINDS[kname].get("hidden"):
            net.errors.append(f"{w}: unknown kind '{kname}' (kinds: {', '.join(C.public_kinds())})")
            continue
        k = C.KINDS[kname]
        known = set(k["params"]) | set(k["inits"]) | {"kind", "msl", "comment"}
        unknown = sorted(set(spec) - known)
        if unknown:
            net.errors.append(f"{w}: unknown keys {unknown} for {kname} (parameters: {sorted(k['params'])}; "
                              f"initial values: {sorted(k['inits']) or 'none'})")
        params = resolve_params(kname, spec, w, net, name)
        if k.get("parts") is not None:
            inits_given = {i: spec[i] for i in k["inits"] if i in spec}
            net.comps[name] = {"kind": kname, "params": params, "given": set(spec) & set(k["params"]),
                               "inits_given": inits_given}
            for part, (pk, sub) in k["parts"].items():
                try:
                    ep = _elementary_params(pk, sub, params)
                except (TypeError, KeyError):      # a missing value, already reported
                    ep = {}
                ename = f"{name}__{part}"
                pk_params = {p: (ep[p] if p in ep else s["default"] if s["default"] is not C.REQ else None)
                             for p, s in C.KINDS[pk]["params"].items()}
                pinit_given = {}
                for ci, target in k["inits"].items():
                    tp, ti = target.split(".")
                    if tp == part and ci in inits_given:
                        pinit_given[ti] = inits_given[ci]
                inits = resolve_inits(pk, pinit_given, pk_params, f"{w}.{part}", net, name)
                net.elems[ename] = {"kind": pk, "params": pk_params, "inits": inits, "comp": name, "part": part}
            for a, b in k["conns"]:
                pa, qa = a.split(".", 1)
                pb, qb = b.split(".", 1)
                net.conns.append((f"{name}__{pa}.{qa}", f"{name}__{pb}.{qb}"))
        else:
            inits = resolve_inits(kname, spec, params, w, net, name)
            net.comps[name] = {"kind": kname, "params": params, "given": set(spec) & set(k["params"])}
            net.elems[name] = {"kind": kname, "params": params, "inits": inits, "comp": name, "part": None}
    _connections(study, net)
    if not net.errors:
        _topology(study, net)
    return net


def _connections(study, net):
    conns = study.get("connect", [])
    if not isinstance(conns, list):
        net.errors.append("connect: a list of pairs [\"A.port\", \"B.port\"]")
        return
    for i, pair in enumerate(conns):
        w = f"connect[{i}]"
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair)):
            net.errors.append(f"{w}: a pair [\"A.port\", \"B.port\"]")
            continue
        ends = []
        for x in pair:
            comp, _, port = x.partition(".")
            if comp not in net.comps:
                if comp in (study.get("components") or {}):
                    ends.append(None)       # already reported
                else:
                    net.errors.append(f"{w}: no component '{comp}'")
                    ends.append(None)
                continue
            pts = C.port_types(net.comps[comp]["kind"])
            if port and port not in pts:
                net.errors.append(f"{w}: {comp} ({net.comps[comp]['kind']}) has no port '{port}' (ports: {sorted(pts)})")
                ends.append(None)
                continue
            ends.append((comp, port or None))
        if None in ends:
            continue
        # a bare component name: its only port of a compatible type
        for j in (0, 1):
            comp, port = ends[j]
            if port is None:
                other = ends[1 - j]
                pts = C.port_types(net.comps[comp]["kind"])
                if other[1] is not None:
                    ot = C.port_types(net.comps[other[0]]["kind"])[other[1]]
                    want = {"in": ("out",), "out": ("in",)}.get(ot, (ot,))
                    cands = [p for p, t in pts.items() if t in want]
                else:
                    cands = list(pts)
                if len(cands) != 1:
                    net.errors.append(f"{w}: say which port of {comp}: {sorted(pts)}")
                    ends[j] = None
                    break
                ends[j] = (comp, cands[0])
        if None in ends:
            continue
        (ca, pa), (cb, pb) = ends
        ta = C.port_types(net.comps[ca]["kind"])[pa]
        tb = C.port_types(net.comps[cb]["kind"])[pb]
        if {ta, tb} == {"in", "out"}:
            pass
        elif ta in ("in", "out") or tb in ("in", "out"):
            net.errors.append(f"{w}: {ca}.{pa} ({ta}) and {cb}.{pb} ({tb}): a signal output connects to signal inputs only"
                              + (" (two inputs: drive both from one output)" if ta == tb == "in" else "")
                              + (" (two outputs cannot be connected)" if ta == tb == "out" else ""))
            continue
        elif ta != tb:
            net.errors.append(f"{w}: {ca}.{pa} is a {C.domain_of(ta)} port ({ta}), {cb}.{pb} a {C.domain_of(tb)} port ({tb})")
            continue
        if (ca, pa) == (cb, pb):
            net.errors.append(f"{w}: a port connected to itself")
            continue
        a, b = net.elem_port(ca, pa), net.elem_port(cb, pb)
        net.study_conns.append((f"{ca}.{pa}", f"{cb}.{pb}"))
        net.conns.append((a, b))


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _topology(study, net):
    """Nodes, signal nets, and the checks that need them: inputs driven once, grounds, references."""
    uf = _UF()
    phys, sig_in, sig_out = [], [], []
    for e, d in net.elems.items():
        k = C.KINDS[d["kind"]]
        for p, t in k["ports"].items():
            ep = f"{e}.{p}"
            (sig_in if t == "in" else sig_out if t == "out" else phys).append(ep)
            uf.find(ep)
    drivers = {}
    for a, b in net.conns:
        uf.union(a, b)
    roots = sorted({uf.find(x) for x in phys})
    rid = {r: i for i, r in enumerate(roots)}
    net.node_of = {x: rid[uf.find(x)] for x in phys}
    # signals: each input driven by one output (or a linearisation input)
    lin_inputs = {}
    an = study.get("analysis") or {}
    if isinstance(an, dict) and an.get("kind") == "linearize" and isinstance(an.get("inputs"), dict):
        for nm, ref in an["inputs"].items():
            comp, _, port = str(ref).partition(".")
            if comp in net.comps and C.port_types(net.comps[comp]["kind"]).get(port) == "in":
                lin_inputs[net.elem_port(comp, port)] = nm
    for a, b in net.conns:
        ta, tb = net.port_type(a), net.port_type(b)
        if ta == "out" and tb == "in":
            drivers.setdefault(b, []).append(a)
        elif ta == "in" and tb == "out":
            drivers.setdefault(a, []).append(b)
    sig = _UF()
    for x in sig_in + sig_out:
        sig.find(x)
    for inp, outs in drivers.items():
        for o in outs:
            sig.union(inp, o)
    for inp in sig_in:
        e = inp.split(".")[0]
        study_ref = _study_port(net, inp)
        n = len(drivers.get(inp, []))
        if inp in lin_inputs:
            if n:
                net.errors.append(f"analysis.inputs.{lin_inputs[inp]}: {study_ref} is connected; a linearisation input must be a free signal input")
            net.free_inputs[lin_inputs[inp]] = inp
        elif n == 0:
            net.errors.append(f"{study_ref} (signal input) is not connected: connect a signal output to it "
                              f"(e.g. a constant or step block){' or list it in analysis.inputs' if an.get('kind') == 'linearize' else ''}")
        elif n > 1:
            net.errors.append(f"{study_ref} (signal input) is driven by {n} outputs: only one")
    sroots = sorted({sig.find(x) for x in sig_in + sig_out})
    sid = {r: i for i, r in enumerate(sroots)}
    net.signal_of = {x: sid[sig.find(x)] for x in sig_in + sig_out}
    # islands: nodes joined through the components (for the ground / reference rules)
    isl = _UF()
    for x in phys:
        isl.find(net.node_of[x])
    for e, d in net.elems.items():
        k = C.KINDS[d["kind"]]
        groups = k["islands"]
        if groups is None:
            by_dom = {}
            for p, t in k["ports"].items():
                if t not in ("in", "out"):
                    by_dom.setdefault(C.domain_of(t), []).append(p)
            groups = list(by_dom.values())
        for g in groups:
            for p in g[1:]:
                isl.union(net.node_of[f"{e}.{g[0]}"], net.node_of[f"{e}.{p}"])
    members = {}
    for x in phys:
        members.setdefault(isl.find(net.node_of[x]), set()).add(x)
    for ports in members.values():
        kinds = {net.elems[x.split(".")[0]]["kind"] for x in ports}
        dom = C.domain_of(net.port_type(next(iter(ports))))
        comps = sorted({_study_comp(net, x) for x in ports})
        label = ", ".join(comps[:6]) + (" ..." if len(comps) > 6 else "")
        if dom == "electrical" and "ground" not in kinds:
            net.errors.append(f"the circuit of {label} has no ground: add {{\"kind\": \"ground\"}} and connect it to one node "
                              f"of this circuit (each separate circuit, e.g. a transformer's secondary, needs its own)")
        if dom == "translational" and not kinds & {"fixed", "mass"}:
            net.errors.append(f"the translational system of {label} has neither a mass nor a fixed point: its position "
                              f"is undetermined; add a fixed (or a mass)")
        if dom == "rotational" and not kinds & {"rot_fixed", "inertia"}:
            net.errors.append(f"the rotational system of {label} has neither an inertia nor a rot_fixed: its angle is "
                              f"undetermined; add a rot_fixed (or an inertia)")
        if dom == "thermal" and not kinds & {"fixed_temperature", "prescribed_temperature", "heat_capacitor"}:
            net.errors.append(f"the thermal network of {label} has no heat capacitor and no fixed or prescribed "
                              f"temperature: its temperatures are undetermined")
    # unconnected physical ports
    connected = {x for c in net.conns for x in c}
    for x in phys:
        if x not in connected and not _rigid_partner_connected(net, x, connected):
            e = x.split(".")[0]
            kname = net.elems[e]["kind"]
            t = net.port_type(x)
            ref = _study_port(net, x)
            if t == "pin":
                net.errors.append(f"{ref} is not connected (an open terminal carries no current): connect it")
            elif t == "heat" and kname not in ("heat_capacitor",):
                net.warnings.append(f"{ref} is not connected: no heat flows through it (adiabatic)")
            elif t in ("flange_t", "flange_r") and kname not in ("mass", "inertia"):
                net.warnings.append(f"{ref} is not connected: a free end (no force or torque on it)")


def _rigid_partner_connected(net, x, connected):
    e, p = x.split(".", 1)
    for a, b in C.KINDS[net.elems[e]["kind"]]["rigid"]:
        if p in (a, b):
            return True          # a mass or inertia needs only one flange connected
    return False


def _study_comp(net, eport):
    return net.elems[eport.split(".")[0]]["comp"]


def _study_port(net, eport):
    e, p = eport.split(".", 1)
    d = net.elems[e]
    if d["part"] is None:
        return f"{e}.{p}"
    k = C.KINDS[net.comps[d["comp"]]["kind"]]
    for sp, target in k["ports"].items():
        if target == f"{d['part']}.{p}":
            return f"{d['comp']}.{sp}"
    return f"{d['comp']} (inside: {d['part']}.{p})"
