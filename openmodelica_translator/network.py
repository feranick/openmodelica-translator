"""network.py — a study's components, values and connections, resolved: the common ground of validation, the
Modelica generator and the reference implementation.
Version: 2026.11.20.1

build(study) -> Net, with net.errors (each says the fix) and net.warnings. It
  - resolves kinds (or "msl" class paths) and parameter values to SI (units.to_si), with defaults;
  - expands composite kinds into elementary ones: component X, part p -> element "X__p";
  - maps study ports ("X.p"), variables ("X.i", "X.p.v") and initial values to elements and Modelica paths;
  - joins physical ports into nodes (union-find; rigid pairs such as a mass's two flanges join too) and signal
    outputs to their inputs;
  - switches on conditional ports that are connected (elems[e]["enabled"]: a pipe's or a resistor's heatPort);
  - checks each network's reference (ground, fixed point, temperature, a coolant loop's pressure) and that every
    fluid port of a loop has the same medium.
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
        k = C.KINDS[self.elems[e]["kind"]]
        ports = k["ports"]
        if "[" in p and p.split("[", 1)[0] in k.get("multi", {}):      # one connection of a multi port
            return ports[p.split("[", 1)[0]]
        if ".pin[" in p:                      # a phase of a plug
            return "qs_pin" if ports[p.split(".", 1)[0]] == "qs_plug" else "pin"
        if p.endswith((".re", ".im")) and "[" in p:      # a part of an element of a complex vector signal
            return {"cvin": "in", "cvout": "out"}[ports[p.split("[")[0]]]
        if p.endswith("]") and "[" in p:      # an element of a vector signal
            return {"vin": "in", "vout": "out"}[ports[p.split("[")[0]]]
        return ports[p]

    def mpath(self, eport: str) -> str:
        """Element port -> Modelica path (table.y -> table.y[1]; a plug's phase: plug_p.pin[k])."""
        e, p = eport.split(".", 1)
        if ".pin[" in p:
            q, k = p.split(".", 1)
            return f"{e}." + C.PORT_PATHS.get((self.elems[e]["kind"], q), q) + "." + k
        kd = C.KINDS[self.elems[e]["kind"]]
        if "[" in p and p.split("[", 1)[0] in kd.get("multi", {}):    # ports[k] of a Modelica.Fluid vessel
            base, idx = p.split("[", 1)
            return f"{e}.{kd['multi'][base][0]}[{idx}"
        redirect = self.elems[e].get("redirect", {})
        if p in redirect:                    # a port of a helper the generator adds (a collector, a gain)
            return redirect[p]
        return f"{e}." + C.PORT_PATHS.get((self.elems[e]["kind"], p), p)

    def pins(self, eport: str) -> list:
        """The electrical pins of an element port: a plug's three phases, else the port itself."""
        n = C.phases(self.port_type(eport))
        return [f"{eport}.pin[{k}]" for k in range(1, n + 1)] if n > 1 else [eport]

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
                vp = C.var_path(kname, v)
                if isinstance(vp, list):            # a sum of variables (a pipe's dp)
                    return [(float(c), f"{comp}.{q}") for c, q in vp], k["vars"][v]
                return [(1.0, f"{comp}.{vp}")], k["vars"][v]
            if v in pts and pts[v] in ("in", "out"):
                ep = self.elem_port(comp, v)
                e, p = ep.split(".", 1)
                return [(1.0, self.mpath(ep))], C.OUT_UNITS.get((self.elems[e]["kind"], p), "1")
        if len(parts) >= 3 and parts[1] in pts and C.is_complex(pts[parts[1]]):
            raise KeyError(f"'{ref}': {parts[1]} is a quasi-static port (complex phasors): measure it with "
                           f"qs_current_rms, qs_voltage_rms or qs_power, or use the machine's abs_is[k], Ps_total, ...")
        if len(parts) == 4 and parts[1] in pts and pts[parts[1]] == "plug" and re.fullmatch(r"pin\[[123]\]", parts[2]) \
                and parts[3] in ("v", "i"):
            ep = self.elem_port(comp, parts[1])
            return [(1.0, self.mpath(ep) + "." + parts[2] + "." + parts[3])], C.PORT_TYPES["pin"]["units"][parts[3]]
        mm = re.fullmatch(r"(\w+)(?:\[(\d+)\])?", parts[1]) if len(parts) == 3 else None
        if mm and mm.group(1) in k.get("multi", {}) and comp in self.elems:
            # a port taking any number of connections (Modelica.Fluid's ports[nPorts]): 'X.port[k].var', or
            # 'X.port.var': m_flow / H_flow summed over the connections, p / h of the only one
            base, idx = mm.group(1), mm.group(2)
            pt = C.PORT_TYPES[pts[base]]
            n = self.elems[comp].get("multi_n", {}).get(base, 0)
            var = parts[2]
            if var not in (pt["potential"], pt["flow"], *pt.get("extra", ())):
                raise KeyError(f"'{ref}': a {pts[base]} port has {pt['potential']}, {pt['flow']}"
                               f"{', ' + ', '.join(pt.get('extra', ())) if pt.get('extra') else ''}")
            if idx is not None:
                if not 1 <= int(idx) <= n:
                    raise KeyError(f"'{ref}': {comp}.{base} has {n} connection(s): {base}[1] .. {base}[{n}]")
                return [(1.0, self.mpath(f"{comp}.{base}[{idx}]") + "." + var)], pt["units"][var]
            if n == 0:
                raise KeyError(f"'{ref}': {comp}.{base} is not connected")
            if var in (pt["flow"], "H_flow"):
                return [(1.0, self.mpath(f"{comp}.{base}[{j}]") + "." + var) for j in range(1, n + 1)], \
                    pt["units"][var]
            if n > 1:
                raise KeyError(f"'{ref}': {comp}.{base} has {n} connections: say which ({base}[1] .. {base}[{n}])")
            return [(1.0, self.mpath(f"{comp}.{base}[1]") + "." + var)], pt["units"][var]
        if len(parts) == 3 and parts[1] in pts and pts[parts[1]] not in ("in", "out"):
            pt = C.PORT_TYPES[pts[parts[1]]]
            if parts[2] in (pt["potential"], pt["flow"], *pt.get("extra", ())):
                ep = self.elem_port(comp, parts[1])
                return [(1.0, self.mpath(ep) + "." + parts[2])], pt["units"][parts[2]]
        raise KeyError(f"'{ref}': {kname} has variables {sorted(k['vars'])}, signal ports "
                       f"{sorted(p for p, t in pts.items() if t in ('in', 'out'))} and physical ports "
                       f"{sorted(p for p, t in pts.items() if t not in ('in', 'out'))} (port.v / port.i, "
                       f"port.s / port.f, port.phi / port.tau, port.T / port.Q_flow, port.p / port.m_flow / port.h / port.H_flow, "
                       f"plug.pin[k].v / .i)")

    def var_expr_elem(self, elem: str, v: str):
        k = C.KINDS[self.elems[elem]["kind"]]
        if v in k["vars"]:
            return [(1.0, f"{elem}.{C.var_path(self.elems[elem]['kind'], v)}")], k["vars"][v]
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
    if typ == "medium":
        if isinstance(v, str):
            if v not in C.MEDIA:
                raise UnitError(f"{where}: unknown medium '{v}' (media: {', '.join(C.MEDIA)}, or {{rho, cp, cv, lambda, nu}})")
            return v
        if isinstance(v, dict):
            bad = set(v) - set(C.MEDIUM_KEYS)
            if bad or not {"rho", "cp"} <= set(v):
                raise UnitError(f"{where}: a medium object has rho [kg/m3] and cp [J/(kg.K)], optionally cv, lambda, nu "
                                f"(SI numbers){'; unknown ' + str(sorted(bad)) if bad else ''}")
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in v.values()):
                raise UnitError(f"{where}: medium properties are positive numbers (SI)")
            return {k: float(x) for k, x in sorted(v.items())}
        raise UnitError(f"{where}: a medium name ({', '.join(C.MEDIA)}) or {{rho, cp, cv, lambda, nu}}")
    if typ == "fmedium":
        if isinstance(v, str):
            if v not in C.FMEDIA:
                raise UnitError(f"{where}: unknown Modelica.Fluid medium '{v}' (media: {', '.join(C.FMEDIA)}, or a "
                                f"liquid {{rho, cp, eta, lambda}})")
            return v
        if isinstance(v, dict):
            bad = set(v) - set(C.FMEDIUM_KEYS) - C.FMEDIUM_EXTRA
            if bad or not {"rho", "cp"} <= set(v):
                raise UnitError(f"{where}: a custom liquid has rho [kg/m3] and cp [J/(kg.K)], optionally eta [Pa.s], "
                                f"lambda [W/(m.K)] and p_vapour [Pa] (SI numbers)"
                                f"{'; unknown ' + str(sorted(bad)) if bad else ''}")
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in v.values()):
                raise UnitError(f"{where}: medium properties are positive numbers (SI)")
            return {k: float(x) for k, x in sorted(v.items())}
        raise UnitError(f"{where}: a medium name ({', '.join(C.FMEDIA)}) or a liquid {{rho, cp, eta, lambda}}")
    if typ == "npsh":
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return [[0.0, float(v)]]
        ok = isinstance(v, list) and len(v) >= 1 and all(isinstance(r, list) and len(r) == 2 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in r) and r[0] >= 0 and r[1] > 0 for r in v)
        if not ok or any(b[0] <= a[0] for a, b in zip(v, v[1:])):
            raise UnitError(f"{where}: NPSHr in m (> 0), or [[V_flow (m3/s), NPSHr (m)], ...] with the flows rising")
        return [[float(a), float(b)] for a, b in v]
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
                gv = given[name]
                if spec.get("phases") and isinstance(gv, list):
                    if len(gv) != spec["phases"]:
                        raise UnitError(f"{where}.{name}: one value or a list of {spec['phases']} (one per phase)")
                    si = [to_si(x, spec["unit"], where=f"{where}.{name}[{j + 1}]")[0] for j, x in enumerate(gv)]
                    out[name] = (si, True)
                    continue
                si, rec = to_si(gv, spec["unit"], absolute_temperature=spec["abs_T"], where=f"{where}.{name}")
                if rec is not None and not isinstance(gv, (int, float)):
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
        cfdt_kind = (k.get("fluid") or {}).get("cfdt")
        known = set(k["params"]) | set(k["inits"]) | {"kind", "msl", "comment"} | ({"cfdt"} if cfdt_kind else set()) | \
            ({"npsh_cfdt", "onset_cfdt"} if kname == "fl_pump" else set())
        unknown = sorted(set(spec) - known)
        if unknown:
            net.errors.append(f"{w}: unknown keys {unknown} for {kname} (parameters: {sorted(k['params'])}; "
                              f"initial values: {sorted(k['inits']) or 'none'})")
        given_keys = set(spec)
        cfdt_scale = None
        if cfdt_kind and "cfdt" in spec:
            spec, cfdt_scale = _from_cfdt(cfdt_kind, spec, w, net, name)
        npsh_from = onset_from = None
        if kname == "fl_pump" and "npsh_cfdt" in spec:
            spec, npsh_from = _npsh_from_cfdt(spec, w, net, name)
        if kname == "fl_pump" and "onset_cfdt" in spec:
            spec, onset_from = _onset_from_cfdt(spec, w, net, name)
        params = resolve_params(kname, spec, w, net, name)
        if npsh_from:
            params["_npsh_from"] = npsh_from
        if onset_from:
            params["_onset_from"], params["_onset_raw"] = onset_from
        if kname == "fl_pump":
            _npsh_curve(params, w, net)
        if k.get("parts") is not None:
            inits_given = {i: spec[i] for i in k["inits"] if i in spec}
            net.comps[name] = {"kind": kname, "params": params, "given": set(spec) & set(k["params"]),
                               "inits_given": inits_given}
            for part, pspec in k["parts"].items():
                pk, sub = pspec[0], pspec[1]
                if not C.part_on(pspec, params):
                    continue
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
                net.elems[ename] = {"kind": pk, "params": pk_params, "inits": inits, "comp": name, "part": part,
                                    "enabled": set()}
            for cn in k["conns"]:
                if not C.part_on(cn, params):
                    continue
                a, b = cn[0], cn[1]
                pa, qa = a.split(".", 1)
                pb, qb = b.split(".", 1)
                net.conns.append((f"{name}__{pa}.{qa}", f"{name}__{pb}.{qb}"))
        else:
            inits = resolve_inits(kname, spec, params, w, net, name)
            net.comps[name] = {"kind": kname, "params": params, "given": given_keys & set(k["params"])}
            net.elems[name] = {"kind": kname, "params": params, "inits": inits, "comp": name, "part": None,
                               "enabled": set()}
            if cfdt_scale:
                net.elems[name]["cfdt"] = cfdt_scale
    _kind_checks(net)
    _connections(study, net)
    for name, d in net.comps.items():
        if d["kind"] in ("induction_machine", "pmsm", "pmsm_damper") and d["params"].get("terminal") == "D":
            for a_, b_ in study_conns_of(net, name, "starpoint"):
                net.errors.append(f"components.{name}: a delta-connected machine has no starpoint ({a_} - {b_})")
    if not net.errors:
        _topology(study, net)
    return net


def _npsh_curve(params, where, net):
    """params['_npsh_curve'] = [[V, NPSH3, upper bound?], ...] from npsh_required and npsh3_below (FL17)."""
    from .npsh import NpshError, merge_curve
    known, below = params.get("npsh_required"), params.get("npsh3_below")
    if not known and not below:
        return
    plain = [x for x in (known, below) if x and len(x) == 1 and x[0][0] == 0.0]
    if plain and known and below:
        net.errors.append(f"{where}: npsh_required and npsh3_below together need flows ([[V_flow, m], ...]): a plain "
                          f"number applies at every flow")
        return
    try:
        params["_npsh_curve"] = merge_curve(known, below)
    except NpshError as exc:
        net.errors.append(f"{where}: {exc}")


def _onset_from_cfdt(spec, where, net, name):
    """The "onset_cfdt" key of fl_pump (FL17, FL18): eye_dip (the mean dip), pulse_amplitude (from min_f/max_f, or
    the raw min/max: an upper bound, flagged), onset_N, onset_V_flow from a cfd-translator free-flow run; given values
    win. Returns (spec, (where from, amplitude raw?) or None)."""
    from .npsh import NpshError, onset_from_cfdt
    spec = dict(spec)
    raw = spec.pop("onset_cfdt")
    try:
        r = onset_from_cfdt(raw, f"{where}.onset_cfdt")
    except NpshError as exc:
        net.errors.append(str(exc))
        return spec, None
    vals = {"eye_dip": r["eye_dip"], "pulse_amplitude": r["pulse_amplitude"], "onset_N": r["rpm"] * 2 * math.pi / 60}
    if r["V_flow"]:
        vals["onset_V_flow"] = r["V_flow"]
    taken = [k for k in vals if k not in spec]
    net.inputs[f"{name}.onset_cfdt"] = dict(r["record"], parameters=taken)
    for k in taken:
        spec[k] = vals[k]
    if not taken:
        return spec, None
    return spec, (f"cfd-translator free_flow ({r['record']['result']})", r["raw"] and "pulse_amplitude" in taken)


def _npsh_from_cfdt(spec, where, net, name):
    """The "npsh_cfdt" key of fl_pump (FL16, FL17): npsh_required (NPSH3 per flow) and npsh3_below (sweeps without a
    3 % drop: their lowest NPSH, an upper bound) from cfd-translator cavitation sweeps, moved to N_nominal; a given
    npsh_required or npsh3_below wins over its part. Returns (spec, where the values came from)."""
    from .npsh import NpshError, from_cfdt
    spec = dict(spec)
    raw = spec.pop("npsh_cfdt")
    N0 = None
    if spec.get("N_nominal") is not None:
        try:
            N0 = spec["N_nominal"] if isinstance(spec["N_nominal"], (int, float)) else \
                to_si(spec["N_nominal"], "rad/s", where=f"{where}.N_nominal")[0]
        except UnitError as exc:
            net.errors.append(str(exc))
            return spec, None
    try:
        r = from_cfdt(raw, f"{where}.npsh_cfdt", N0)
    except NpshError as exc:
        net.errors.append(str(exc))
        return spec, None
    taken = [k for k, v in (("npsh_required", r["known"]), ("npsh3_below", r["below"])) if v and k not in spec]
    net.inputs[f"{name}.npsh_cfdt"] = dict(r["record"], parameters=taken)
    for k in taken:
        spec[k] = r["known"] if k == "npsh_required" else r["below"]
    n = len(r["record"]["sweeps"])
    return spec, f"cfd-translator NPSH3 ({n} sweep(s), {len(r['below'])} an upper bound)" if taken else None


def _from_cfdt(how, spec, where, net, name):
    """The "cfdt" key of fl_pump / fl_pump_shaft / fl_resistance (V5 FL5): values from cfd-translator results for
    the parameters the study does not give (given ones win), each recorded in net.inputs. Returns (spec with them,
    what the circuit's medium will rescale: {rho, mu, a, b} for a resistance)."""
    from .chains import ChainError, cfdt_pump, cfdt_resistance
    w = f"{where}.cfdt"
    spec = dict(spec)
    raw = spec.pop("cfdt")
    try:
        if how == "pump":
            N0 = None
            if "N_nominal" in spec:
                try:
                    N0 = to_si(spec["N_nominal"], "rad/s", where=f"{where}.N_nominal")[0]
                except UnitError as exc:
                    net.errors.append(str(exc))
                    return spec, None
            r = cfdt_pump(raw, w, N0)
            take = {"curve": r["curve"], "N_nominal": r["N_nominal"], "rho_power": r["rho_power"]}
            if "eta" not in spec:                  # a given constant efficiency wins over the power curve
                take["power"] = r["power"]
        else:
            r = cfdt_resistance(raw, w)
            take = {"a": r["a"], "b": r["b"]}
    except ChainError as exc:
        net.errors.append(str(exc))
        return spec, None
    taken = sorted(p for p in take if p not in spec)
    for p in taken:
        spec[p] = take[p]
    rec = dict(r["record"], parameters=taken)
    net.inputs[f"{name}.cfdt"] = rec
    if how == "resistance":
        return spec, {"rho": r["rho"], "mu": r["mu"], "a": "a" in taken, "b": "b" in taken and r["b"] > 0,
                      "record": rec}
    return spec, None


def _cfdt_rescale(net, e, d, medium):
    """A resistance from cfd-translator, to the circuit's medium: a (dp ~ rho V^2) x rho/rho_cfdt, b (laminar,
    dp ~ mu V) x mu/mu_cfdt when cfdt.mu was given; a medium omct does not know the constants of: a warning."""
    sc = d["cfdt"]
    rec = sc["record"]
    m = C.fmedium(medium)
    if m["class"] != "liquid_const":
        if sc["a"] or sc["b"]:
            net.warnings.append(f"components.{d['comp']}: a, b from cfd-translator at {sc['rho']:g} kg/m3 are used "
                                f"as they are with {m['name']} (omct knows no constant density for it)")
        rec["scaled"] = "no (medium without constant properties)"
        return
    rho, mu = (995.586, 1.0e-3) if not isinstance(medium, dict) else (medium["rho"], medium.get("eta", 1.0e-3))
    p = d["params"]
    done = {}
    if sc["a"] and abs(rho / sc["rho"] - 1) > 1e-12:
        p["a"] = p["a"] * rho / sc["rho"]
        done["a"] = rho / sc["rho"]
    if sc["b"]:
        if sc["mu"] is not None:
            if abs(mu / sc["mu"] - 1) > 1e-12:
                p["b"] = p["b"] * mu / sc["mu"]
                done["b"] = mu / sc["mu"]
        elif p["b"] > 0:
            rec["b_not_scaled"] = "cfd-translator's results do not give its viscosity: cfdt.mu (Pa.s) scales b"
    rec["scaled"] = done or "no (same density)"
    if done:
        rec["a_used"], rec["b_used"] = p["a"], p["b"]


def study_conns_of(net, comp, port):
    return [(a, b) for a, b in net.study_conns if f"{comp}.{port}" in (a, b)]


def dcpm_vi_nominal(p) -> float:
    """MSL DC_PermanentMagnet's ViNominal = VaNominal - Ra(TaNominal) IaNominal - brush drop(IaNominal)."""
    a20 = p.get("alpha20a") or 0.0
    alpha = a20 / (1 + a20 * (p["TaRef"] - 293.15))
    ra = p["Ra"] * (1 + alpha * (p["TaNominal"] - p["TaRef"]))
    ia = p["IaNominal"]
    V, IL = p.get("V_brush") or 0.0, p.get("ILinear_brush") or 0.01 * abs(ia)
    brush = 0.0 if V <= 0 else (V if ia > IL else -V if ia < -IL else V * ia / IL)
    return p["VaNominal"] - ra * ia - brush


def _kind_checks(net):
    """Parameter combinations the MSL classes assert on (at initialisation): caught here with the fix."""
    for name, d in net.comps.items():
        p = d["params"]
        if d["kind"] in ("induction_machine", "pmsm", "pmsm_damper"):
            for pref, ref_ in (("PRef_statorCore", "VRef_statorCore"), ("PRef_strayLoad", "IRef_strayLoad"),
                               ("PRef_pm", "IRef_pm")):
                if (p.get(pref) or 0) > 0 and p.get(ref_) is None:
                    net.errors.append(f"components.{name}: {pref} needs {ref_} (the RMS value it refers to)")
        if d["kind"] == "dc_machine" and None not in (p.get("VaNominal"), p.get("IaNominal"), p.get("Ra")):
            vi = dcpm_vi_nominal(p)
            if vi <= 0:
                net.errors.append(f"components.{name}: VaNominal must exceed the armature and brush drops at "
                                  f"IaNominal (induced voltage {vi:g} V <= 0)")
        if d["kind"] == "pipe" and None not in (p.get("V_flowNominal"), p.get("dpNominal")):
            VL, dpL = C.KINDS["pipe"]["mods"]["V_flowLaminar"](p), C.KINDS["pipe"]["mods"]["dpLaminar"](p)
            if not VL < p["V_flowNominal"]:
                net.errors.append(f"components.{name}: V_flowLaminar ({VL:g}) must be < V_flowNominal "
                                  f"({p['V_flowNominal']:g})")
            elif p["dpNominal"] < dpL / VL * p["V_flowNominal"] * (1 - 1e-12):
                net.errors.append(f"components.{name}: dpNominal ({p['dpNominal']:g} Pa) must be >= dpLaminar / "
                                  f"V_flowLaminar x V_flowNominal ({dpL / VL * p['V_flowNominal']:g} Pa): the curve "
                                  f"cannot fall below the laminar line; lower dpLaminar or raise V_flowLaminar")


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
            net.errors.append(f"{w}: {ca}.{pa} is a {C.domain_of(ta)} port ({ta}), {cb}.{pb} a {C.domain_of(tb)} port ({tb})"
                              + (": quasi-static (phasor) circuits connect only to quasi-static ports (qs_plug, "
                                 "qs_pin); a transient and a quasi-static circuit cannot be joined (they meet through "
                                 "shafts, heat and signals)" if C.domain_of(ta) == "quasi-static" or C.domain_of(tb) == "quasi-static"
                                 else ": a plug (three phases) connects to plugs; for one phase use plug_to_pin, for a star "
                                 "point a star" if "plug" in (ta, tb) else ""))
            continue
        if (ca, pa) == (cb, pb):
            net.errors.append(f"{w}: a port connected to itself")
            continue
        ga = C.KINDS[net.comps[ca]["kind"]].get("port_groups", {}).get(pa)
        gb = C.KINDS[net.comps[cb]["kind"]].get("port_groups", {}).get(pb)
        if ga and gb:
            net.errors.append(f"{w}: connect the port group {ca}.{pa} to one port, not to another group")
            continue
        net.study_conns.append((f"{ca}.{pa}", f"{cb}.{pb}"))
        pa_, pb_ = _multi_index(net, ca, pa), _multi_index(net, cb, pb)
        pairs = [(net.elem_port(ca, m), net.elem_port(cb, pb)) for m in ga] if ga else \
            [(net.elem_port(ca, pa), net.elem_port(cb, m)) for m in gb] if gb else [(net.elem_port(ca, pa_), net.elem_port(cb, pb_))]
        for a, b in pairs:
            net.conns.append((a, b))
            for ep in (a, b):
                e, p = ep.split(".", 1)
                if p in C.KINDS[net.elems[e]["kind"]]["cond_ports"]:
                    net.elems[e]["enabled"].add(p)
    # conditional ports that share one switch go together (a machine's heat ports)
    for e, d in net.elems.items():
        k = C.KINDS[d["kind"]]
        flags = {}
        for port, flag in k["cond_ports"].items():
            flags.setdefault(flag, []).append(port)
        for flag, ports in flags.items():
            if flag is None:                   # optional ports without an MSL switch (a valve's opening input)
                continue
            on = [p for p in ports if p in d["enabled"]]
            if on and len(on) < len(ports):
                grp = next((g for g, m in k.get("port_groups", {}).items() if set(m) == set(ports)), None)
                net.errors.append(f"{d['comp']}: heat ports {sorted(set(ports) - set(on))} are not connected: connect "
                                  + (f"{d['comp']}.{grp} (every loss into one node) or " if grp else "") +
                                  f"all of {ports}")


def _multi_index(net, comp, port):
    """A multi port (Modelica.Fluid's ports[nPorts]): each connection its own element, 'port[k]'."""
    d = net.elems.get(comp)
    if d is None or port not in C.KINDS[d["kind"]].get("multi", {}):
        return port
    n = d.setdefault("multi_n", {}).get(port, 0) + 1
    d["multi_n"][port] = n
    return f"{port}[{n}]"


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
            if p in k["cond_ports"] and p not in d["enabled"]:
                continue
            ep = f"{e}.{p}"
            if p in k.get("multi", {}):
                n = d.get("multi_n", {}).get(p, 0)
                if n == 0:
                    net.errors.append(f"{_study_port(net, ep)} is not connected: connect it (it takes any number of "
                                      f"connections)")
                phys += [f"{ep}[{j}]" for j in range(1, n + 1)]
                continue
            if t == "in":
                sig_in.append(ep)
            elif t == "out":
                sig_out.append(ep)
            elif t in ("vin", "vout"):              # vector signals: one net per element
                (sig_in if t == "vin" else sig_out).extend(f"{ep}[{j}]" for j in (1, 2, 3))
            elif t in ("cvin", "cvout"):            # complex vector signals: one net per element and part
                (sig_in if t == "cvin" else sig_out).extend(f"{ep}[{j}].{r}" for j in (1, 2, 3) for r in ("re", "im"))
            else:
                phys += net.pins(ep)
    for x in phys:
        uf.find(x)
    drivers = {}
    for a, b in net.conns:
        if net.port_type(a) in ("in", "out", "vin", "vout", "cvin", "cvout"):
            continue
        for x, y in zip(net.pins(a), net.pins(b)):
            uf.union(x, y)
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
        elif {ta, tb} == {"vin", "vout"}:
            o, i_ = (a, b) if ta == "vout" else (b, a)
            for j in (1, 2, 3):
                drivers.setdefault(f"{i_}[{j}]", []).append(f"{o}[{j}]")
        elif {ta, tb} == {"cvin", "cvout"}:
            o, i_ = (a, b) if ta == "cvout" else (b, a)
            for j in (1, 2, 3):
                for r in ("re", "im"):
                    drivers.setdefault(f"{i_}[{j}].{r}", []).append(f"{o}[{j}].{r}")
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
                if C.domain_of(t) != "signal":
                    by_dom.setdefault(C.domain_of(t), []).append(p)
            groups = list(by_dom.values())
        multi_n = d.get("multi_n", {})

        def expand(q):
            if q in k.get("multi", {}):
                return [f"{e}.{q}[{j}]" for j in range(1, multi_n.get(q, 0) + 1)]
            return net.pins(f"{e}.{q}") if (q not in k["cond_ports"] or q in d["enabled"]) else []
        groups = [[x for q in g for x in expand(q) if x in net.node_of] for g in groups]
        for g in groups:
            for x in g[1:]:
                isl.union(net.node_of[g[0]], net.node_of[x])
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
        if dom == "quasi-static":
            if "qs_ground" not in kinds:
                net.errors.append(f"the quasi-static circuit of {label} has no ground: add {{\"kind\": \"qs_ground\"}} "
                                  f"and connect it to one node (qs_grid and the QS drives have one inside)")
            roots = sorted({_study_comp(net, x) for x in ports if net.elems[x.split(".")[0]]["kind"] in C.QS_SOURCES})
            if len(roots) != 1:
                net.errors.append(f"the quasi-static circuit of {label} has {len(roots)} quasi-static sources "
                                  f"({', '.join(roots) or 'none'}): it needs exactly one (qs_grid, qs_ac_voltage, "
                                  f"qs_vf_inverter or qs_pmsm_current_drive), which sets its frequency")
        if dom == "translational" and not kinds & {"fixed", "mass"}:
            net.errors.append(f"the translational system of {label} has neither a mass nor a fixed point: its position "
                              f"is undetermined; add a fixed (or a mass)")
        if dom == "rotational" and not kinds & {"rot_fixed", "inertia", "dc_machine", "im_core", "pmsm_core",
                                                "pmsm_damper_core", "qs_im_core", "qs_pmsm_core", "qs_pmsm_damper_core"}:
            net.errors.append(f"the rotational system of {label} has neither an inertia nor a rot_fixed: its angle is "
                              f"undetermined; add a rot_fixed (or an inertia)")
        # a Modelica.Fluid storage's heat port (tank, volume, dynamic pipe) sets the temperature too (FL8)
        if dom == "thermal" and not kinds & {"fixed_temperature", "prescribed_temperature", "heat_capacitor", "pipe",
                                             "fl_tank", "fl_volume", "fl_dynamic_pipe"}:
            net.errors.append(f"the thermal network of {label} has no heat capacitor, no fixed or prescribed "
                              f"temperature and no coolant pipe: its temperatures are undetermined")
        if dom == "fluid":
            if not kinds & {"ambient", "pressure_reference"}:
                net.errors.append(f"the coolant loop of {label} has no pressure reference: add a pressure_reference "
                                  f"(closed loop) or ambients (open ends) and connect it")
            media = {}
            for x in ports:
                e = x.split(".")[0]
                mv = net.elems[e]["params"].get("medium")
                media.setdefault(repr(mv), []).append(net.elems[e]["comp"])
            if len(media) > 1:
                desc = "; ".join(f"{m} ({', '.join(sorted(set(c))[:4])})" for m, c in media.items())
                net.errors.append(f"the coolant loop of {label} mixes media ({desc}): give every component of a loop "
                                  f"the same medium")
    _fluid_sets(net, members)
    # unconnected physical ports
    connected = {y for c in net.conns for x in c for y in (net.pins(x) if C.domain_of(net.port_type(x)) != "signal" else [x])}
    reported = set()
    for x in phys:
        if ".pin[" in x:
            x = x.rsplit(".pin[", 1)[0]
            if x in reported:
                continue
            reported.add(x)
            if all(y in connected for y in net.pins(x)):
                continue
        if x not in connected and not _rigid_partner_connected(net, x, connected):
            e = x.split(".")[0]
            kname = net.elems[e]["kind"]
            t = net.port_type(x)
            ref = _study_port(net, x)
            if t == "pin" and (kname == "star" or (kname == "terminal_box" and x.endswith(".starpoint"))):
                pass                    # a floating star point: the phase currents sum to zero
            elif kname == "qs_terminal_box" and x.endswith(".starpoint"):
                pass                    # terminal D: the star point does not exist
            elif t in ("pin", "plug", "qs_pin", "qs_plug"):
                net.errors.append(f"{ref} is not connected (an open terminal carries no current): connect it")
            elif t == "fluid":
                net.errors.append(f"{ref} is not connected (an open fluid port is a closed end: no flow): connect it, "
                                  f"or end the line in an ambient")
            elif t == "mfluid":
                net.errors.append(f"{ref} is not connected (a closed end: no flow): connect it, or end the line in an "
                                  f"fl_boundary")
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


# ---- Modelica.Fluid (V5) ---------------------------------------------------------------------------------------
FL_VOLUMES = ("fl_dynamic_pipe", "fl_volume")


def _fluid_sets(net, members):
    """Modelica.Fluid's connected sets: one medium each (given on any component, default water), a pressure
    reference, valves for the medium's phase, the closed-loop rule for constant-density liquids (probed on carbonio
    2026-11-19, planning.md V5: a volume on a hydraulic cycle does not build in OpenModelica 1.27.2, except a dynamic
    pipe right after one pump), the system component, and the flags the generator needs (_n_ports, ...)."""
    fl = [e for e, d in net.elems.items() if C.KINDS[d["kind"]].get("domain") == "mfluid"]
    if not fl:
        return
    systems = [e for e in fl if net.elems[e]["kind"] == "fl_system"]
    if len(systems) > 1:
        net.errors.append(f"fl_system: one per study ({', '.join(sorted(systems))})")
    for e in systems:
        if e != "system":
            net.errors.append(f"components.{e}: an fl_system must be named 'system' (Modelica.Fluid's inner system)")
    if "system" in net.elems and net.elems["system"]["kind"] != "fl_system":
        net.errors.append("components.system: with Modelica.Fluid parts the name 'system' is taken by fl_system "
                          "(Modelica.Fluid's inner system): rename it")
    for e in fl:
        d = net.elems[e]
        k = C.KINDS[d["kind"]]
        d["params"]["_n_ports"] = sum(d.get("multi_n", {}).values())
        d["params"]["_opening_connected"] = "opening" in d.get("enabled", ())
        d["params"]["_w_in"] = "w_in" in d.get("enabled", ())
        d["params"]["_heat"] = "heat" in d.get("enabled", ())
        fk = k.get("fluid") or {}
        if fk.get("collector") and fk["collector"][0] in d.get("enabled", ()):     # see modelica.helpers
            d.setdefault("redirect", {})[fk["collector"][0]] = f"omct_{e}_coll.port_b"
        if fk.get("speed_in") and fk["speed_in"][0] in d.get("enabled", ()):
            d.setdefault("redirect", {})[fk["speed_in"][0]] = f"omct_{e}_rpm.u"
        if d["kind"] == "fl_dynamic_pipe" and d["params"].get("heat_transfer") not in (None, "ideal") \
                and not d["params"]["_heat"]:
            net.warnings.append(f"components.{e}: heat_transfer is set but heat is not connected (adiabatic)")
        if (k.get("fluid") or {}).get("speed_in") and "N" in d["params"] and d["params"].get("N") is not None \
                and d["params"]["_w_in"]:
            net.errors.append(f"components.{e}: give the speed as N or through w_in, not both")
        if d["kind"] in ("fl_valve", "fl_valve_gas", "fl_valve_linear") and d["params"]["_opening_connected"] \
                and "opening" in (net.comps.get(e, {}).get("given") or set()):
            net.errors.append(f"components.{e}: opening is given and its input connected: one of the two")
        if d["kind"] in ("fl_valve", "fl_valve_gas"):
            given = [x for x in ("Kv", "Cv", "Av") if d["params"].get(x) is not None]
            if len(given) > 1:
                net.errors.append(f"components.{e}: size it by one of Kv, Cv, Av ({', '.join(given)} given)")
        if d["kind"] in ("fl_pump", "fl_pump_shaft") and d["params"].get("checkValve") \
                and not float(d["params"].get("V") or 0) > 0:
            net.errors.append(f"components.{e}: a pump with checkValve needs a volume V > 0 (e.g. 0.001 m3): with "
                              f"V = 0 and the valve closed its enthalpy has no equation (OpenModelica stops with a "
                              f"division by the zero flow)")
        given_c = net.comps.get(d["comp"], {}).get("given") or set()
        if d["kind"] in ("fl_pump", "fl_pump_shaft") and "eta" in given_c and "power" in given_c:
            net.errors.append(f"components.{e}: give eta (constant) or power (a curve), not both")
        if d["kind"] in ("fl_pump", "fl_pump_shaft") and d["params"].get("power") is not None \
                and len(d["params"]["power"]) < 2:
            net.errors.append(f"components.{e}.power: at least 2 points [[V_flow, W], ...]")
        if d["kind"] in ("fl_pump", "fl_pump_shaft") and d["params"].get("curve") is not None:
            pts = d["params"]["curve"]
            if len(pts) < 2:
                net.errors.append(f"components.{e}.curve: at least 2 points [[V_flow, head], ...]")
            elif any(b[1] >= a[1] for a, b in zip(pts, pts[1:])):
                net.errors.append(f"components.{e}.curve: head must fall as the flow rises")
    # the connected sets (islands of mfluid ports)
    seen = set()
    for ports in members.values():
        if C.domain_of(net.port_type(next(iter(ports)))) != "mfluid":
            continue
        elems = sorted({x.split(".")[0] for x in ports})
        seen |= set(elems)
        label = ", ".join(sorted({net.elems[x]["comp"] for x in elems})[:6])
        given = {}
        for x in elems:
            mv = net.elems[x]["params"].get("medium")
            if mv is not None:
                given.setdefault(repr(mv), (mv, []))[1].append(net.elems[x]["comp"])
        if len(given) > 1:
            desc = "; ".join(f"{m} ({', '.join(sorted(c)[:4])})" for m, (_, c) in given.items())
            net.errors.append(f"the fluid circuit of {label} mixes media ({desc}): one medium per connected circuit")
            continue
        medium = next(iter(given.values()))[0] if given else "water"
        for x in elems:
            net.elems[x]["params"]["medium"] = medium
            if net.elems[x].get("cfdt"):
                _cfdt_rescale(net, x, net.elems[x], medium)
        liquid = C.is_liquid(medium)
        kinds = {net.elems[x]["kind"] for x in elems}
        if not any((C.KINDS[kk].get("fluid") or {}).get("pressure_ref") for kk in kinds) and \
                not (not liquid and "fl_volume" in kinds):
            net.errors.append(f"the fluid circuit of {label} has nothing that sets its pressure: add an fl_boundary "
                              f"or an fl_tank (a gas: also an fl_volume)")
        for x in elems:
            fk = C.KINDS[net.elems[x]["kind"]].get("fluid") or {}
            if fk.get("liquid_only") and not liquid:
                net.errors.append(f"components.{net.elems[x]['comp']}: fl_valve is for liquids; with {medium} use "
                                  f"fl_valve_gas")
            if fk.get("gas_only") and liquid:
                net.errors.append(f"components.{net.elems[x]['comp']}: fl_valve_gas is for gases; with a liquid use "
                                  f"fl_valve")
        if C.fmedium(medium)["class"] == "liquid_const":
            _closed_loops(net, elems, label)
    for e in fl:                                       # parts on no circuit (fl_system) keep the default
        if e not in seen and "medium" in C.KINDS[net.elems[e]["kind"]]["params"]:
            net.elems[e]["params"]["medium"] = net.elems[e]["params"].get("medium") or "water"
    for e in fl:
        d = net.elems[e]
        if d["kind"] == "fl_dynamic_pipe" and int(d["params"].get("nNodes") or 2) < 2 \
                and (d["params"].get("_structure") or "av_vb") == "av_vb":
            # MSL's default modelStructure av_vb has nNodes - 1 flow segments (carbonio 2026-11-19,
            # run_fl11_probe1: 'Type mismatch in equation pipe.pathLengths[1:pipe.n - 1] = ...')
            net.errors.append(f"components.{d['comp']}.nNodes: at least 2 (with one volume MSL's DynamicPipe has no "
                              f"flow segment: OpenModelica does not build it)")


def _closed_loops(net, elems, label):
    """Constant density: a dynamic pipe on a hydraulic cycle whose inlet meets only one pump's outlet gets
    modelStructure av_b, the others MSL's av_vb (volumes on loops were refused before 2026.11.19.20).
    Cycles: an undirected graph of the connection nodes, two-port components as edges, a vessel (tank, volume) as a
    hub joined to each of its ports; boundaries and mass-flow sources cut it (their pressure or flow is set)."""
    adj, edges = {}, []

    def add(u, v, e):
        edges.append((u, v, e))
        adj.setdefault(u, []).append((v, len(edges) - 1))
        adj.setdefault(v, []).append((u, len(edges) - 1))

    for x in elems:
        d = net.elems[x]
        kd = C.KINDS[d["kind"]]
        if (kd.get("fluid") or {}).get("vessel"):
            hub = f"hub:{x}"
            for base in kd.get("multi", {}):
                for j in range(1, d.get("multi_n", {}).get(base, 0) + 1):
                    add(hub, net.node_of[f"{x}.{base}[{j}]"], x)
        elif "port_a" in kd["ports"] and "port_b" in kd["ports"] and kd["ports"]["port_a"] == "mfluid":
            add(net.node_of[f"{x}.port_a"], net.node_of[f"{x}.port_b"], x)
    on_cycle = _cycle_edges(adj, edges)
    by_node = {}
    for ep, nid in net.node_of.items():
        if net.port_type(ep) == "mfluid":
            by_node.setdefault(nid, []).append(ep)
    for x in elems:
        d = net.elems[x]
        if d["kind"] != "fl_dynamic_pipe":
            continue
        # closed loops of a constant-density liquid build since the volumes do not prefer p as a state
        # (modelica.fluid_parts, run_pv_matrix5: fl_volume and a dynamic pipe after a valve on a loop); a dynamic
        # pipe right after one pump keeps av_b (FL2, probed then), the others MSL's av_vb
        cyc = any(edges[i][2] == x for i in on_cycle)
        mates = [ep for ep in by_node.get(net.node_of[f"{x}.port_a"], []) if ep != f"{x}.port_a"]
        after_pump = len(mates) == 1 and mates[0].endswith(".port_b") and \
            net.elems[mates[0].split(".")[0]]["kind"] in ("fl_pump", "fl_pump_shaft")
        d["params"]["_structure"] = "av_b" if (cyc and after_pump) else "av_vb"


def _cycle_edges(adj, edges) -> set:
    """Indices of the edges on a cycle (not bridges; parallel edges are on a cycle), Tarjan's bridges."""
    disc, low, bridges, t = {}, {}, set(), [0]
    for root in adj:
        if root in disc:
            continue
        stack = [(root, None, iter(adj[root]))]
        disc[root] = low[root] = t[0]
        t[0] += 1
        while stack:
            u, pe, it = stack[-1]
            for v, ei in it:
                if ei == pe:
                    continue
                if v not in disc:
                    disc[v] = low[v] = t[0]
                    t[0] += 1
                    stack.append((v, ei, iter(adj[v])))
                    break
                low[u] = min(low[u], disc[v])
            else:
                stack.pop()
                if stack:
                    w = stack[-1][0]
                    low[w] = min(low[w], low[u])
                    if low[u] > disc[w]:
                        bridges.add(pe)
    return {i for i in range(len(edges)) if i not in bridges}
