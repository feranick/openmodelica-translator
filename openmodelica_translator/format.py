"""format.py — the study format (study_format) and the static checks (validate, validate_study).
Version: 2026.10.21.6

validate_study(study) -> {ok, errors, warnings}: nothing runs (no omc). It resolves the network (network.build),
checks outputs, analysis, derived quantities, sweep, expect, run (against the OMCT_MAX_* caps) and images, then
builds the reference system (ref.Ref) to find singular systems and initial values that other elements already
fix. Every error says the fix.
"""
from __future__ import annotations

import os
import re

from . import catalog as C
from .network import NAME, build
from .units import UnitError, to_si

STUDY_KEYS = {
    "components": "{name: {kind, parameters, initial values}} (names: identifiers; no '__'; not starting with omct)",
    "connect": "[[\"A.port\", \"B.port\"], ...]: physical ports of the same type (pin, flange_t, flange_r, heat); "
               "signal output -> input. A component with one port of the needed type may be named alone ('gnd').",
    "outputs": "{name: 'comp.var' | 'comp.signal_port' | 'comp.port.v'}: what to record and report",
    "analysis": "{kind: transient | steady | linearize, ...} (below)",
    "derived": "[{kind: step | crossing | window | steady, signal: <output name>, name?, ...}] (below)",
    "sweep": "{params: {'comp.param' or 'comp.v0': [values]}, mode: grid | zip (grid), report: [result paths] "
             "(default: outputs.*.final and derived.*)}: one build, one run per point (run.procs at a time) -> "
             "sweep.table[i].{params, params_si, values, ok, reliable}",
    "converge": "true: rerun at tolerance/10; outputs that change more than 10 x tolerance x range -> unreliable",
    "expect": "[{quantity: dotted result path, value, tol (relative, 0.02) | abs}]",
    "run": "{timeout_s, mem, procs (parallel sweep points), queue}: within this machine's caps (status.executor.caps)",
    "images": "true: time plots of the outputs (+ Bode for linearize, a sweep plot); false: none",
    "modelica": "escape hatch {text: Modelica source, model: name}: outputs are then Modelica paths; checked, but no "
                "catalog checks and no reference",
}
ANALYSES = {
    "transient": "{stop_time (s), start_time (0), intervals (500), tolerance (1e-6), method: dassl | ida}",
    "steady": "{max_time (s): run until then, band (1e-4): an output is steady once it stays within band x its range "
              "of its final value; tolerance, intervals as transient} -> steady.<output>: value, t_steady_s, reached",
    "linearize": "{at (s, 0 = after initialisation), inputs: {name: 'comp.signal_input'} (free inputs: leave them "
                 "unconnected), bode: {f_min, f_max (Hz), points} | [Hz, ...]} -> linearization: A, B, C, D (states "
                 "named), eigenvalues, modes (f_n_Hz, zeta, tau_s), dc_gain, bode; the study's outputs are the outputs",
}
DERIVED = {
    "step": "{signal, t0 (the step time; default start_time), initial (value before), final (default: last value), "
            "band (0.02), rise: [0.1, 0.9], target} -> rise_time_s, overshoot_pct, peak, peak_time_s, "
            "settling_time_s, final, initial, steady_state_error (with target)",
    "crossing": "{signal, level (a value; units allowed: '80 degC'), direction: up | down | any} -> crossing_time_s "
                "(null if it never crosses)",
    "window": "{signal, t0, t1} -> mean, rms, ac_rms, min, max, p2p over [t0, t1]",
    "steady": "{signal, band (1e-3)} -> value, t_steady_s, reached",
}
CONVENTIONS = [
    "Units: SI everywhere (K, rad/s, m, kg, N.m, W, F, H). A value may be a number (SI), a string '10 mH', "
    "'80 degC', '3000 rpm', '50 %', or {value, unit, from, what} (chained values from other translators, e.g. "
    "{\"value\": 1200, \"unit\": \"N/mm\", \"from\": \"feat\"}); every conversion is listed in the result's inputs. "
    "Hz and rad/s are never converted into each other.",
    "Sign conventions (MSL): flow into a component's port is positive; a current source drives i from p through "
    "the source to n; force/torque sources push their flange in the positive direction.",
    "Initial values (v0, i0, s0, v0, phi0, w0, T0) default to 0 (T0 to 293.15 K) and are fixed only where they are "
    "free: a capacitor across a source, or a second inertia rigidly joined to the first, follows the others; giving "
    "such a value is an error. Positions default to 0 and springs' rest lengths to 0, so springs start relaxed. "
    "Blocks start from their y_start (states 0).",
    "Each electrical circuit needs a ground; each translational/rotational system a fixed point or a mass/inertia; "
    "each thermal network a capacitor or a fixed/prescribed temperature. Every signal input is driven by one output.",
    "Results: outputs.<name>: unit, initial, final, min, max, t_min, t_max, series {t, y} (<= 400 points); "
    "derived.<name>.*; steady.<name>.*; linearization.*; sweep.table; checks (balance, initialization, solver, "
    "overrides read back, events, finite, energy, reference); reliable / unreliable_because: if reliable is false, "
    "do not report the numbers as results.",
    "Values are applied to one compiled model with OpenModelica's override file (no recompiling): sweeps cost one "
    "build. Structural values (marked *) change the model and rebuild it.",
]
EXAMPLE = {"rlc_step": {
    "components": {"V": {"kind": "step_voltage", "V": 12, "startTime": 0.001}, "R": {"kind": "resistor", "R": 10},
                   "L": {"kind": "inductor", "L": "10 mH"}, "C": {"kind": "capacitor", "C": "100 uF"},
                   "gnd": {"kind": "ground"}},
    "connect": [["V.p", "R.p"], ["R.n", "L.p"], ["L.n", "C.p"], ["C.n", "gnd"], ["V.n", "gnd"]],
    "outputs": {"v_C": "C.v", "i": "L.i"},
    "analysis": {"kind": "transient", "stop_time": 0.05, "intervals": 2000},
    "derived": [{"kind": "step", "signal": "v_C", "t0": 0.001}],
    "expect": [{"quantity": "derived.v_C.final", "value": 12, "tol": 1e-3}]}}

MAX_POINTS = 2000
MAX_INTERVALS = 1_000_000


def _kind_line(name: str) -> str:
    k = C.KINDS[name]
    pts = C.port_types(name)
    ports = ",".join(f"{p}" + ("(in)" if t == "in" else "(out)" if t == "out" else "") for p, t in pts.items())
    ps = []
    for p, s in k["params"].items():
        d = s["default"]
        tag = "*" if s["structural"] else ""
        if s["type"] in ("enum",):
            ps.append(f"{p}{tag}={'|'.join(s['choices'])}" + (f"({d})" if d is not C.REQ and d is not None else ""))
        elif d is C.REQ:
            ps.append(f"{p}{tag}[{s['unit']}]")
        elif d is None:
            ps.append(f"{p}{tag}[{s['unit']}]=auto")
        else:
            ps.append(f"{p}{tag}[{s['unit']}]={d!r}" if not isinstance(d, float) else f"{p}{tag}[{s['unit']}]={d:g}")
    alts = " or ".join("+".join(g) for g in k["alternatives"])
    vs = ",".join(k["vars"])
    ins = ",".join(k["inits"])
    out = f"{ports} | {' '.join(ps) or '-'}"
    if alts:
        out += f" (give {alts})"
    if vs:
        out += f" | vars {vs}"
    if ins:
        out += f" | init {ins}"
    if k.get("doc"):
        out += f" | {k['doc']}"
    return out


def study_format() -> dict:
    domains = {
        "electrical": [], "translational": [], "rotational": [], "thermal": [], "signals": []}
    for name in C.public_kinds():
        pts = C.port_types(name).values()
        doms = {C.domain_of(t) for t in pts}
        d = next((x for x in ("electrical", "translational", "rotational", "thermal") if x in doms), "signals")
        if name == "dc_motor":
            d = "electrical"
        domains[d].append(name)
    kinds = {d: {n: _kind_line(n) for n in names} for d, names in domains.items()}
    return {"format": "system-dynamics study (openmodelica-translator, MSL 4.0.0)",
            "study_keys": STUDY_KEYS, "analyses": ANALYSES, "derived": DERIVED, "conventions": CONVENTIONS,
            "kinds": kinds,
            "kinds_legend": "ports (in/out = signals) | parameters [SI unit]=default (no default: required; "
                            "name* structural; [*] a signal value: any unit, converted to SI) | vars (outputs: comp.var; ports: comp.port.v/i, .s/.f, .phi/.tau, "
                            ".T/.Q_flow) | init (initial values) | equation",
            "example": EXAMPLE}


# ------------------------------------------------------------------------------------------- run caps
RUN_KEYS = {"timeout_s", "mem", "procs", "queue"}
_MEM = re.compile(r"^(\d+(?:\.\d+)?)([KMGT]?)B?$", re.I)


def mem_mb(text):
    m = _MEM.match(str(text).strip())
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 ** 2}[m.group(2).upper()]


def caps() -> dict:
    out = {}
    for k, env in (("timeout_s", "OMCT_MAX_TIMEOUT"), ("mem", "OMCT_MAX_MEM"), ("procs", "OMCT_MAX_PROCS")):
        v = os.environ.get(env, "").strip()
        if v:
            out[k] = v
    return out


def run_errors(st: dict) -> list[str]:
    r = st.get("run")
    if r is None:
        return []
    if not isinstance(r, dict):
        return ["run: {timeout_s, mem, procs, queue}"]
    errs = []
    bad = set(r) - RUN_KEYS
    if bad:
        errs.append(f"run: unknown keys {sorted(bad)} (timeout_s, mem, procs, queue)")
    cap = caps()
    t = r.get("timeout_s")
    if t is not None:
        if not isinstance(t, (int, float)) or isinstance(t, bool) or t <= 0:
            errs.append("run.timeout_s: seconds (> 0)")
        elif "timeout_s" in cap and t > float(cap["timeout_s"]):
            errs.append(f"run.timeout_s: at most {float(cap['timeout_s']):g} s on this machine (OMCT_MAX_TIMEOUT)")
    m = r.get("mem")
    if m is not None:
        mb = mem_mb(m) if isinstance(m, str) else None
        if not mb:
            errs.append("run.mem: SLURM memory per job, e.g. '2G' or '500M'")
        elif "mem" in cap and mem_mb(cap["mem"]) and mb > mem_mb(cap["mem"]):
            errs.append(f"run.mem: at most {cap['mem']} on this machine (OMCT_MAX_MEM)")
    n = r.get("procs")
    if n is not None:
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            errs.append("run.procs: parallel sweep points (integer >= 1)")
        elif "procs" in cap and n > int(cap["procs"]):
            errs.append(f"run.procs: at most {cap['procs']} on this machine (OMCT_MAX_PROCS)")
    q = r.get("queue")
    if q is not None and not isinstance(q, bool):
        errs.append("run.queue: true (always a SLURM job) or false (always local)")
    return errs


# ------------------------------------------------------------------------------------------- validation
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _analysis_errors(an, errs, warns):
    if not isinstance(an, dict) or an.get("kind") not in ANALYSES:
        errs.append(f"analysis: {{kind: {' | '.join(ANALYSES)}, ...}}")
        return
    kind = an["kind"]
    allowed = {"transient": {"stop_time", "start_time", "intervals", "tolerance", "method"},
               "steady": {"max_time", "start_time", "intervals", "tolerance", "method", "band"},
               "linearize": {"at", "inputs", "bode", "tolerance"}}[kind] | {"kind"}
    bad = set(an) - allowed
    if bad:
        errs.append(f"analysis ({kind}): unknown keys {sorted(bad)} (allowed: {sorted(allowed - {'kind'})})")
    end = "stop_time" if kind == "transient" else "max_time" if kind == "steady" else None
    t0 = an.get("start_time", 0.0)
    if not _num(t0):
        errs.append("analysis.start_time: seconds")
        t0 = 0.0
    if end:
        if not _num(an.get(end)) or an.get(end) <= t0:
            errs.append(f"analysis.{end}: seconds, > start_time ({t0:g})")
        n = an.get("intervals", 500)
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_INTERVALS:
            errs.append(f"analysis.intervals: an integer 1..{MAX_INTERVALS}")
        if an.get("method", "dassl") not in ("dassl", "ida"):
            errs.append("analysis.method: dassl | ida")
    tol = an.get("tolerance", 1e-6)
    if not _num(tol) or not 1e-12 <= tol <= 1e-2:
        errs.append("analysis.tolerance: relative tolerance 1e-12..1e-2 (1e-6)")
    if kind == "steady":
        b = an.get("band", 1e-4)
        if not _num(b) or not 0 < b < 0.5:
            errs.append("analysis.band: relative band 0 < band < 0.5 (1e-4)")
    if kind == "linearize":
        if not _num(an.get("at", 0.0)) or an.get("at", 0.0) < 0:
            errs.append("analysis.at: the time to linearise at (s, >= 0)")
        ins = an.get("inputs")
        if not isinstance(ins, dict) or not ins:
            errs.append("analysis.inputs: {name: 'comp.signal_input'} with at least one free signal input (e.g. "
                        "{\"u\": \"Vs.v\"} for a signal_voltage Vs, {\"tau\": \"T.tau\"} for a torque T)")
        else:
            for nm in ins:
                if not NAME.match(nm):
                    errs.append(f"analysis.inputs: '{nm}' is not an identifier")
        bode = an.get("bode")
        if bode is not None:
            if isinstance(bode, list):
                if not bode or not all(_num(f) and f > 0 for f in bode):
                    errs.append("analysis.bode: a list of frequencies > 0 (Hz)")
            elif isinstance(bode, dict):
                f0, f1, n = bode.get("f_min"), bode.get("f_max"), bode.get("points", 50)
                if not (_num(f0) and _num(f1) and 0 < f0 < f1) or not isinstance(n, int) or not 2 <= n <= 2000:
                    errs.append("analysis.bode: {f_min, f_max (Hz, 0 < f_min < f_max), points (2..2000)}")
            else:
                errs.append("analysis.bode: {f_min, f_max, points} or a list of Hz")


def _derived_errors(st, outputs, units, errs):
    der = st.get("derived", [])
    if not isinstance(der, list):
        errs.append("derived: a list of {kind, signal, ...}")
        return
    names = set()
    for i, d in enumerate(der):
        w = f"derived[{i}]"
        if not isinstance(d, dict) or d.get("kind") not in DERIVED:
            errs.append(f"{w}: {{kind: {' | '.join(DERIVED)}, signal, ...}}")
            continue
        sig = d.get("signal")
        if sig not in outputs:
            errs.append(f"{w}.signal: one of the outputs' names {sorted(outputs)}")
            continue
        nm = d.get("name", sig)
        if not isinstance(nm, str) or not NAME.match(nm):
            errs.append(f"{w}.name: an identifier")
        if (nm, d["kind"]) in names:
            errs.append(f"{w}: a second {d['kind']} named '{nm}': give it a different name")
        names.add((nm, d["kind"]))
        allowed = {"step": {"t0", "initial", "final", "band", "rise", "target"}, "crossing": {"level", "direction"},
                   "window": {"t0", "t1"}, "steady": {"band"}}[d["kind"]] | {"kind", "signal", "name"}
        bad = set(d) - allowed
        if bad:
            errs.append(f"{w}: unknown keys {sorted(bad)} for {d['kind']} ({sorted(allowed)})")
        for key in ("t0", "t1", "initial", "final", "target"):
            if key in d and not _num(d[key]):
                errs.append(f"{w}.{key}: a number")
        if d["kind"] == "step":
            b = d.get("band", 0.02)
            if not _num(b) or not 0 < b < 1:
                errs.append(f"{w}.band: 0 < band < 1 (0.02)")
            r = d.get("rise", [0.1, 0.9])
            if not (isinstance(r, list) and len(r) == 2 and all(_num(x) for x in r) and 0 <= r[0] < r[1] <= 1):
                errs.append(f"{w}.rise: [low, high] fractions, e.g. [0.1, 0.9]")
        if d["kind"] == "crossing":
            if "level" not in d:
                errs.append(f"{w}.level: the level to cross")
            else:
                try:
                    to_si(d["level"], units.get(sig, "1") or "1", absolute_temperature=units.get(sig) == "K", where=f"{w}.level")
                except UnitError as exc:
                    errs.append(str(exc))
            if d.get("direction", "any") not in ("up", "down", "any"):
                errs.append(f"{w}.direction: up | down | any")
        if d["kind"] == "window" and _num(d.get("t0")) and _num(d.get("t1")) and d["t1"] <= d["t0"]:
            errs.append(f"{w}: t1 > t0")
        if d["kind"] == "steady":
            b = d.get("band", 1e-3)
            if not _num(b) or not 0 < b < 1:
                errs.append(f"{w}.band: 0 < band < 1 (1e-3)")


def sweep_points(sw: dict) -> list[dict]:
    keys = list(sw["params"])
    vals = [sw["params"][k] for k in keys]
    if sw.get("mode", "grid") == "zip":
        return [dict(zip(keys, row)) for row in zip(*vals)]
    pts = [{}]
    for k, vs in zip(keys, vals):
        pts = [{**p, k: v} for p in pts for v in vs]
    return pts


def _sweep_errors(st, net, errs):
    sw = st.get("sweep")
    if sw is None:
        return
    if not isinstance(sw, dict) or not isinstance(sw.get("params"), dict) or not sw["params"]:
        errs.append("sweep: {params: {'comp.param': [values]}, mode: grid | zip, report: [...]}")
        return
    bad = set(sw) - {"params", "mode", "report"}
    if bad:
        errs.append(f"sweep: unknown keys {sorted(bad)}")
    if sw.get("mode", "grid") not in ("grid", "zip"):
        errs.append("sweep.mode: grid | zip")
    for key, vals in sw["params"].items():
        w = f"sweep.params['{key}']"
        comp, _, p = key.partition(".")
        if comp not in net.comps:
            errs.append(f"{w}: no component '{comp}'")
            continue
        kname = net.comps[comp]["kind"]
        k = C.KINDS[kname]
        if not isinstance(vals, list) or not vals:
            errs.append(f"{w}: a non-empty list of values")
            continue
        if p in k["params"]:
            spec = k["params"][p]
            if spec["structural"]:
                errs.append(f"{w}: {p} is structural (it changes the model); sweep it with one study per value")
                continue
            if spec["type"] not in ("real", "int", "bool"):
                errs.append(f"{w}: only numbers can be swept")
                continue
            for v in vals:
                try:
                    if spec["type"] == "real":
                        si, _ = to_si(v, spec["unit"], absolute_temperature=spec["abs_T"], where=w)
                        lo, gt, hi = spec["min"], spec["gt"], spec["max"]
                        if (lo is not None and si < lo) or (gt is not None and si <= gt) or (hi is not None and si > hi):
                            errs.append(f"{w}: {v!r} is out of range")
                except UnitError as exc:
                    errs.append(str(exc))
        elif p in k["inits"]:
            for v in vals:
                try:
                    to_si(v, k["inits"][p]["unit"], absolute_temperature=k["inits"][p]["abs_T"], where=w)
                except UnitError as exc:
                    errs.append(str(exc))
        else:
            errs.append(f"{w}: {kname} has parameters {sorted(k['params'])} and initial values {sorted(k['inits'])}")
    if sw.get("mode", "grid") == "zip":
        lens = {len(v) for v in sw["params"].values() if isinstance(v, list)}
        if len(lens) > 1:
            errs.append("sweep: mode zip needs lists of the same length")
    try:
        n = len(sweep_points(sw)) if not errs else 0
    except Exception:  # noqa: BLE001
        n = 0
    if n > MAX_POINTS:
        errs.append(f"sweep: {n} points; at most {MAX_POINTS} per study (split it)")
    rep = sw.get("report")
    if rep is not None and (not isinstance(rep, list) or not all(isinstance(x, str) for x in rep)):
        errs.append("sweep.report: a list of result paths (e.g. 'derived.w.overshoot_pct', 'outputs.i.max')")


def _expect_errors(st, errs):
    ex = st.get("expect", [])
    if not isinstance(ex, list):
        errs.append("expect: a list")
        return
    for i, e in enumerate(ex):
        if not isinstance(e, dict) or not isinstance(e.get("quantity"), str) or not _num(e.get("value")):
            errs.append(f"expect[{i}]: {{quantity: dotted path, value: number, tol (relative) or abs}}")
            continue
        for k in ("tol", "abs"):
            if k in e and (not _num(e[k]) or e[k] <= 0):
                errs.append(f"expect[{i}]: {k} is positive")
        bad = set(e) - {"quantity", "value", "tol", "abs"}
        if bad:
            errs.append(f"expect[{i}]: unknown keys {sorted(bad)}")


def _images_errors(st, errs):
    im = st.get("images")
    if im is not None and not isinstance(im, bool):
        errs.append("images: true or false")


def validate_study(st: dict) -> dict:
    errs, warns = [], []
    if not isinstance(st, dict):
        return {"ok": False, "errors": ["a study is an object (study_format)"], "warnings": []}
    bad = set(st) - set(STUDY_KEYS) - {"comment", "description"}
    if bad:
        errs.append(f"unknown keys {sorted(bad)} (keys: {sorted(STUDY_KEYS)})")
    if "modelica" in st:
        return _validate_escape(st, errs, warns)
    net = build(st)
    errs += net.errors
    warns += net.warnings
    outs = st.get("outputs")
    units = {}
    if not isinstance(outs, dict) or not outs:
        errs.append("outputs: {name: 'comp.var'} with at least one output")
        outs = {}
    for nm, ref in outs.items():
        if not NAME.match(nm):
            errs.append(f"outputs: '{nm}' is not an identifier")
            continue
        if not isinstance(ref, str):
            errs.append(f"outputs.{nm}: 'comp.var'")
            continue
        if ref.split(".")[0] not in net.comps:
            if ref.split(".")[0] not in (st.get("components") or {}):
                errs.append(f"outputs.{nm}: no component '{ref.split('.')[0]}'")
            continue
        try:
            _, units[nm] = net.var_expr(ref)
        except KeyError as exc:
            errs.append(f"outputs.{nm}: {exc.args[0]}")
    _analysis_errors(st.get("analysis"), errs, warns)
    _derived_errors(st, outs, units, errs)
    _sweep_errors(st, net, errs)
    _expect_errors(st, errs)
    errs += run_errors(st)
    _images_errors(st, errs)
    if "converge" in st and not isinstance(st["converge"], bool):
        errs.append("converge: true or false")
    if not errs:
        errs += _reference_errors(st, net, warns)
    return {"ok": not errs, "errors": errs, "warnings": warns}


def _reference_errors(st, net, warns) -> list[str]:
    """Singular systems and initial values that are not free, from the reference system."""
    from .ref import Ref, RefError
    try:
        r = Ref(net)
    except RefError as exc:
        return [f"the equations have no unique solution: {exc}"]
    except (ValueError, ArithmeticError) as exc:     # numerical trouble: leave it to OpenModelica
        warns.append(f"reference system not built ({type(exc).__name__}: {exc}); OpenModelica will judge the model")
        return []
    _, dep, missing = r.init_selection()
    errs = []
    for cd in dep:
        comp = net.elems[cd["elem"]]["comp"]
        part = net.elems[cd["elem"]]["part"]
        init = cd["name"] if part is None else _composite_init_name(net, comp, part, cd["name"])
        errs.append(f"components.{comp}.{init}: this initial value is not free (other elements fix it: a capacitor "
                    f"across a source or in a loop of capacitors, an inductor in series with a current source, "
                    f"an inertia or mass rigidly joined to another, ...): remove it")
    if missing > 0:
        errs.append(f"{missing} state(s) have no initial value the translator can set: add a mass/inertia or a "
                    f"fixed point to massless junctions, or simplify the model")
    if not r.exact:
        warns.append("reference implementation: not covered (nonlinear: " + "; ".join(r.not_exact[:4]) + ")")
    return errs


def _composite_init_name(net, comp, part, name):
    k = C.KINDS[net.comps[comp]["kind"]]
    for ci, target in k["inits"].items():
        if target == f"{part}.{name}":
            return ci
    return f"{part}.{name}"


def _validate_escape(st, errs, warns):
    m = st["modelica"]
    if not isinstance(m, dict) or not isinstance(m.get("text"), str) or not isinstance(m.get("model"), str):
        errs.append("modelica: {text: Modelica source, model: the model's name}")
    elif not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", m["model"]):
        errs.append("modelica.model: a Modelica class name")
    if "components" in st or "connect" in st:
        errs.append("modelica: give either components/connect or modelica, not both")
    outs = st.get("outputs")
    if not isinstance(outs, dict) or not outs or not all(isinstance(v, str) for v in outs.values()):
        errs.append("outputs: {name: 'Modelica.path.of.variable'} (with the escape hatch, Modelica paths)")
    if st.get("sweep") is not None:
        sw = st["sweep"]
        if not isinstance(sw, dict) or not isinstance(sw.get("params"), dict):
            errs.append("sweep: {params: {'modelica.parameter.path': [values]}}")
    _analysis_errors(st.get("analysis"), errs, warns)
    _derived_errors(st, outs if isinstance(outs, dict) else {}, {}, errs)
    _expect_errors(st, errs)
    errs += run_errors(st)
    warns.append("escape hatch: no catalog checks and no reference implementation")
    return {"ok": not errs, "errors": errs, "warnings": warns}


def validate(studies: dict) -> dict:
    if not isinstance(studies, dict) or not studies:
        return {"ok": False, "studies": {}, "errors": ["studies: {name: study}"]}
    out = {}
    errs = []
    for n, st in studies.items():
        if not isinstance(n, str) or not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$", n):
            errs.append(f"study name '{n}': letters, digits, '_', '-', '.' (at most 64)")
            continue
        out[n] = validate_study(st)
    return {"ok": all(v["ok"] for v in out.values()) and not errs, "studies": out, "errors": errs}
