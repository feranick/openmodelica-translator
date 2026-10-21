"""modelica.py — a study -> the Modelica model, the omc build script, and the values applied at run time.
Version: 2026.10.21.19

translate(study, name) -> Translation with
  text        model.mo: `model omct_model` with one declaration per element (sorted), every value in SI with
              full precision, connect equations sorted, top-level RealInput/RealOutput for linearize, and an
              experiment annotation. Stable: the same study gives the same text.
  structure   the same text with every overridable value replaced by '?': what the build cache hashes, so a
              study that differs only in values reuses the compiled model.
  overrides   {Modelica path: value} for every overridable value (parameters and fixed start values): written
              to the -overrideFile on every run, even right after a build, and read back from the results.
  outputs     {output name: [(coef, Modelica path)], unit}
  fixed       the initial values that get fixed=true (chosen by ref.Ref.init_selection)
build_mos(...) -> the .mos script for omc; override_file(overrides) -> its text.
"""
from __future__ import annotations

import hashlib

from . import __version__
from . import catalog as C
from .network import build

MODEL = "omct_model"
PLACEHOLDER = "?"
BUILD_FLAGS = "--linearizationDumpLanguage=modelica"


def fmt(v) -> str:
    """A value as a Modelica literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        r = repr(v)
        return r if r not in ("inf", "-inf", "nan") else "0.0"
    if isinstance(v, list):
        if v and isinstance(v[0], list):
            return "[" + "; ".join(", ".join(fmt(float(x)) for x in row) for row in v) + "]"
        return "{" + ", ".join(fmt(float(x)) for x in v) + "}"
    return str(v)


def override_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return repr(v) if isinstance(v, float) else str(v)


class Translation:
    pass


def media(net) -> dict:
    """{repr(medium value): top-level parameter name}: one Medium record per distinct medium (coolant loops)."""
    if getattr(net, "media_names", None) is None:
        vals = sorted({repr(d["params"]["medium"]) for d in net.elems.values() if "medium" in C.KINDS[d["kind"]]["params"]
                       and d["params"].get("medium") is not None})
        net.media_names = {v: f"omct_medium_{i + 1}" for i, v in enumerate(vals)}
        net.media_values = {}
        for d in net.elems.values():
            if "medium" in C.KINDS[d["kind"]]["params"] and d["params"].get("medium") is not None:
                net.media_values[repr(d["params"]["medium"])] = d["params"]["medium"]
    return net.media_names


def element_values(net, e: str, selected: set):
    """(structural modifiers {name: text}, overridable modifiers {name: value}, start modifiers {var: (value,
    fixed)}) of one element."""
    d = net.elems[e]
    k = C.KINDS[d["kind"]]
    p = d["params"]
    structural, values = {}, {}
    for name, spec in k["params"].items():
        if spec.get("msl") is False:
            continue
        mname = spec.get("msl") or name
        v = p.get(name)
        if v is None or name in k["mods"]:
            continue
        if spec["structural"]:
            structural[mname] = fmt(v)
        else:
            values[mname] = float(v) if spec["type"] == "real" else v
    for key, fn in k["mods"].items():
        v = fn(p)
        if v is None:
            continue
        spec = k["params"].get(key)
        mname = (spec.get("msl") or key) if spec is not None and spec.get("msl") is not False else key
        if spec is not None and spec["structural"]:
            structural[mname] = fmt(float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
        elif isinstance(v, str):
            structural[mname] = v
        elif isinstance(v, list):
            values[mname] = [float(x) for x in v]    # one value per phase: name[1], name[2], ... at run time
        else:
            values[mname] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    for mname, text in k["text"].items():
        structural[mname] = text
    if "medium" in k["params"] and p.get("medium") is not None:
        structural["medium"] = media(net)[repr(p["medium"])]
    for port, flag in k.get("cond_ports", {}).items():
        if port in d.get("enabled", ()):
            structural[flag] = "true"
    starts = {}
    for iname, spec in k["inits"].items():
        value, _ = d["inits"][iname]
        if spec.get("phases"):
            vals = value if isinstance(value, list) else [value] * spec["phases"]
            fixed = [(e, f"{iname}[{j + 1}]") in selected for j in range(spec["phases"])]
            if any(fixed):
                starts[spec["var"]] = ([float(x) for x in vals], fixed)
        elif (e, iname) in selected:
            starts[spec["var"]] = float(value)
    return structural, values, starts


def _vtext(v, placeholder):
    if isinstance(v, list):
        return "{" + ", ".join(PLACEHOLDER if placeholder else fmt(float(x)) for x in v) + "}"
    return PLACEHOLDER if placeholder else fmt(v)


def _decl(net, e, selected, placeholder: bool):
    d = net.elems[e]
    k = C.KINDS[d["kind"]]
    structural, values, starts = element_values(net, e, selected)
    mods, nested = [], {}
    for name in sorted(set(structural) | set(values)):
        text = structural[name] if name in structural else _vtext(values[name], placeholder)
        if "." in name:                        # a record's field: frictionParameters(PRef=...)
            rec, field = name.split(".", 1)
            nested.setdefault(rec, []).append(f"{field}={text}")
        else:
            mods.append(f"{name}={text}")
    for rec in sorted(nested):
        mods.append(f"{rec}({', '.join(nested[rec])})")
    mods.sort(key=lambda m: m.split("(")[0].split("=")[0])
    for var in sorted(starts):
        st = starts[var]
        if isinstance(st, tuple):              # per phase: start for all, fixed where free
            vals, fixed = st
            mods.append(f"{var}(start={_vtext(vals, placeholder)}, fixed={{{', '.join('true' if f else 'false' for f in fixed)}}})")
        else:
            mods.append(f"{var}(start={_vtext(st, placeholder)}, fixed=true)")
    m = "(" + ", ".join(mods) + ")" if mods else ""
    comment = f' "{d["comp"]}: {d["kind"]}' + (f' ({d["part"]})' if d["part"] else "") + '"'
    return f"  {k['msl']} {e}{m}{comment};"


def translate(study: dict, name: str = "study", ref=None) -> Translation:
    """The Modelica model of a valid study (validate first). ref: a ref.Ref of the same network (built here
    when not given) for the choice of fixed initial values."""
    from .ref import Ref

    net = build(study)
    if net.errors:
        raise ValueError("invalid study: " + "; ".join(net.errors[:5]))
    if ref is None:
        ref = Ref(net)
    sel, dep, missing = ref.init_selection()
    selected = {(cd["elem"], cd["name"]) for cd in sel if not cd["forced"]}
    an = study.get("analysis") or {}
    lin = an.get("kind") == "linearize"

    tr = Translation()
    tr.model = MODEL
    tr.net, tr.ref, tr.selected = net, ref, selected
    tr.outputs = {}
    for oname, oref in (study.get("outputs") or {}).items():
        tr.outputs[oname] = net.var_expr(oref)

    def body(placeholder: bool):
        lines = []
        for rv, mname in sorted(media(net).items(), key=lambda x: x[1]):
            lines.append(f"  parameter Modelica.Thermal.FluidHeatFlow.Media.Medium {mname} = "
                         f"{C.medium_props(net.media_values[rv])['msl']} \"coolant medium\";")
        for e in sorted(net.elems):
            lines.append(_decl(net, e, selected, placeholder))
        if lin:
            for iname in sorted(net.free_inputs):
                lines.append(f"  Modelica.Blocks.Interfaces.RealInput omct_u_{iname} \"linearisation input\";")
            for oname in sorted(tr.outputs):
                lines.append(f"  Modelica.Blocks.Interfaces.RealOutput omct_y_{oname} \"output {oname}\";")
        lines.append("equation")
        pairs = sorted(tuple(sorted((net.mpath(a), net.mpath(b)))) for a, b in net.conns)
        for a, b in pairs:
            lines.append(f"  connect({a}, {b});")
        if lin:
            for iname in sorted(net.free_inputs):
                lines.append(f"  connect(omct_u_{iname}, {net.mpath(net.free_inputs[iname])});")
            for oname in sorted(tr.outputs):
                terms, _ = tr.outputs[oname]
                expr = " + ".join((f"{fmt(c)}*" if c != 1.0 else "") + p for c, p in terms)
                lines.append(f"  omct_y_{oname} = {expr};")
        return lines

    t0 = float(an.get("start_time", 0.0))
    t1 = float(an.get("stop_time", an.get("max_time", an.get("at", 0.0) or 1.0)) or 1.0)
    n = int(an.get("intervals", 500))
    tol = float(an.get("tolerance", 1e-6))
    exp = (f"  annotation(experiment(StartTime={fmt(t0)}, StopTime={fmt(t1)}, Interval={fmt((t1 - t0) / n)}, "
           f"Tolerance={fmt(tol)}));")
    head = [f"// Generated by openmodelica-translator {__version__} from study '{name}' (MSL 4.0.0). Do not edit:",
            "// every value below is also applied at run time from the override file."]
    tr.text = "\n".join(head + [f"model {MODEL}"] + body(False) + [exp, f"end {MODEL};", ""])
    tr.structure = "\n".join([f"model {MODEL}"] + body(True) + [f"end {MODEL};", ""])
    ov = {}
    for e in sorted(net.elems):
        _, values, starts = element_values(net, e, selected)
        for mname, v in values.items():
            if isinstance(v, list):
                for j, x in enumerate(v):
                    ov[f"{e}.{mname}[{j + 1}]"] = x
            else:
                ov[f"{e}.{mname}"] = v
        for var, v in starts.items():
            if isinstance(v, tuple):
                for j, (x, f) in enumerate(zip(*v)):
                    if f:
                        ov[f"{e}.{var}[{j + 1}]"] = x
            else:
                ov[f"{e}.{var}"] = v
    tr.overrides = ov
    tr.fixed = sorted(f"{net.elems[e]['comp']}.{i}" if net.elems[e]["part"] is None else f"{e}.{i}" for e, i in selected)
    # (per-phase initial values appear as name[k])
    tr.dependent = dep
    tr.missing = missing
    return tr


def escape_translation(study: dict) -> Translation:
    """The escape hatch: the study's own Modelica text, its outputs as Modelica paths."""
    tr = Translation()
    m = study["modelica"]
    tr.model, tr.text = m["model"], m["text"]
    tr.structure = m["model"] + "\n" + m["text"]
    tr.outputs = {k: ([(1.0, v)], "") for k, v in study["outputs"].items()}
    tr.overrides, tr.fixed, tr.dependent, tr.missing, tr.net, tr.ref = {}, [], [], 0, None, None
    return tr


def structure_hash(structure: str, omc_version: str, msl_version: str, flags: str = BUILD_FLAGS) -> str:
    h = hashlib.sha256()
    for part in (structure, omc_version, msl_version, flags):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()[:20]


def build_mos(model_file: str = "model.mo", msl_version: str = "4.0.0", stop_time: float = 1.0,
              intervals: int = 500, tolerance: float = 1e-6, method: str = "dassl", model: str = MODEL) -> str:
    """The omc script: load MSL (pinned), the model, check it, build it. getErrorString() after every call.
    It prints 11 values (omc.interpret_build reads them in this order). The executable is always omct_model."""
    return "\n".join([
        f'setCommandLineOptions("{BUILD_FLAGS}"); getErrorString();',
        f'loadModel(Modelica, {{"{msl_version}"}}); getErrorString();',
        'getVersion(Modelica);',
        f'loadFile("{model_file}"); getErrorString();',
        f'checkModel({model}); getErrorString();',
        f'buildModel({model}, startTime=0.0, stopTime={fmt(float(stop_time))}, numberOfIntervals={int(intervals)}, '
        f'tolerance={fmt(float(tolerance))}, method="{method}", fileNamePrefix="{MODEL}", outputFormat="mat", '
        f'variableFilter=".*"); getErrorString();',
        ""])


def override_file(overrides: dict) -> str:
    return "".join(f"{k}={override_value(v)}\n" for k, v in sorted(overrides.items()))


def variable_filter(tr: Translation, extra=()) -> str:
    """Regex of what a run records: the outputs' variables and every overridden value (to read them back)."""
    import re
    names = {p for terms, _ in tr.outputs.values() for _, p in terms} | set(tr.overrides) | set(extra)
    return "|".join(re.escape(n) for n in sorted(names))
