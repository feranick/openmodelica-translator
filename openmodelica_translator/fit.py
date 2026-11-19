"""fit.py — parameter estimation (V4): adjust a study's run-time values to measured data.
Version: 2026.11.19.6

A study's `fit` key (study_format): params {path: {initial, min, max, scale}}, data {output: {t, y, sigma, unit,
t_unit, weight} | {csv, t, y, ...}}, steady [{set, values, sigma}], starts, max_evaluations. The model is evaluated
for a parameter vector without the checks (fast), the residuals go to scipy's bounded least squares (trf), and the
best values get one full, checked run, which is the result's outputs and checks; `fit` holds the values, standard
errors, intervals, correlations, identifiability, the fit's quality and every start.

    fit_errors(st, units)      validation (format.validate_study calls it)
    load(st, units)            the fit in SI: Param list, Series list, SteadyPoint list (raises FitError)
    run_fit(...)               runner._run calls it for a study with `fit`

Evaluators: the reference solver (solver 'ref': catalog studies, in the sandbox too); OpenModelica (F2: one build,
fast runs, the Jacobian's runs in parallel).
"""
from __future__ import annotations

import copy
import csv
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

KEYS = {"params", "data", "steady", "starts", "max_evaluations"}
PARAM_KEYS = {"initial", "min", "max", "scale"}
DATA_KEYS = {"t", "y", "sigma", "unit", "t_unit", "weight", "csv"}
STEADY_KEYS = {"set", "values", "sigma"}
MAX_INLINE = 5000              # points per output inline (an MCP payload); larger data: a CSV on the machine
FAILED = 1e3                   # each residual of a failed evaluation (the optimizer steps back)
CORR_LIMIT = 0.98              # |correlation| above which two parameters are reported as not separable


class FitError(ValueError):
    pass


@dataclass
class Param:
    key: str                   # 'comp.param' / 'comp.v0' (or a Modelica path for custom models)
    initial: float
    lo: float
    hi: float
    log: bool
    given: object = None       # as the study gave the initial value

    def to_x(self, v: float) -> float:
        return math.log(v) if self.log else v

    def from_x(self, x: float) -> float:
        return math.exp(x) if self.log else x


@dataclass
class Series:
    output: str
    t: np.ndarray
    y: np.ndarray              # SI
    scale: float               # sigma (SI) or the data's range
    weight: float = 1.0
    has_sigma: bool = False


@dataclass
class SteadyPoint:
    set: dict                  # {path: value as given}
    values: dict               # {output: SI}
    scales: dict = field(default_factory=dict)
    has_sigma: bool = False


# ---- units ---------------------------------------------------------------------------------------------------
def _linear_map(unit: str | None, si_unit: str):
    """(offset, factor): SI = offset + factor x value, for values in `unit` (None: already SI)."""
    from .units import to_si
    if not unit:
        return 0.0, 1.0
    abs_t = si_unit == "K"
    a = to_si(f"0 {unit}", si_unit or "1", absolute_temperature=abs_t, where="fit")[0]
    b = to_si(f"1 {unit}", si_unit or "1", absolute_temperature=abs_t, where="fit")[0]
    return a, b - a


def _time_factor(unit: str | None) -> float:
    from .units import parse_unit
    if not unit:
        return 1.0
    f, _ = parse_unit(unit)
    return float(f)


# ---- what the study asks -----------------------------------------------------------------------------------------
def _value_spec(st: dict, key: str):
    """(SI unit, absolute temperature?, lower, upper, kind) of a value the fit may change, by the sweep's rules: a
    catalog parameter that is not structural, or an initial value; for a custom model, a Modelica path (SI)."""
    from . import catalog as C
    if "modelica" in st:
        return "1", False, None, None, "path"
    comp, _, p = key.partition(".")
    comps = st.get("components") or {}
    if comp not in comps or not isinstance(comps[comp], dict):
        raise FitError(f"fit.params['{key}']: no component '{comp}'")
    kname = comps[comp].get("kind")
    k = C.KINDS.get(kname)
    if k is None:
        raise FitError(f"fit.params['{key}']: component '{comp}' has no known kind")
    if p in k["params"]:
        spec = k["params"][p]
        if spec["structural"]:
            raise FitError(f"fit.params['{key}']: {p} is structural (it changes the model): it cannot be fitted")
        if spec["type"] != "real":
            raise FitError(f"fit.params['{key}']: only real values can be fitted")
        if spec["gt"] is not None:
            return spec["unit"], spec["abs_T"], ("gt", spec["gt"]), spec["max"], "param"
        return spec["unit"], spec["abs_T"], None if spec["min"] is None else ("min", spec["min"]), spec["max"], "param"
    if p in k["inits"]:
        i = k["inits"][p]
        return i["unit"], i["abs_T"], None, None, "init"
    raise FitError(f"fit.params['{key}']: {kname} has parameters {sorted(k['params'])} and initial values "
                   f"{sorted(k['inits'])}")


def _si(v, unit: str, abs_t: bool, where: str) -> float:
    from .units import UnitError, to_si
    try:
        return float(to_si(v, unit or "1", absolute_temperature=abs_t, where=where)[0])
    except UnitError as exc:
        raise FitError(str(exc)) from None


def _params(st: dict) -> list[Param]:
    ps = (st.get("fit") or {}).get("params")
    if not isinstance(ps, dict) or not ps:
        raise FitError("fit.params: {'comp.param': {initial, min, max, scale}} with at least one value to fit")
    out = []
    for key, d in ps.items():
        w = f"fit.params['{key}']"
        if not isinstance(d, dict) or not {"initial", "min", "max"} <= set(d):
            raise FitError(f"{w}: {{initial, min, max, scale: lin | log}} (values with units allowed)")
        bad = set(d) - PARAM_KEYS
        if bad:
            raise FitError(f"{w}: unknown keys {sorted(bad)} ({sorted(PARAM_KEYS)})")
        unit, abs_t, lo_c, hi_c, _ = _value_spec(st, key)
        ini, lo, hi = (_si(d[k], unit, abs_t, f"{w}.{k}") for k in ("initial", "min", "max"))
        if not lo < hi:
            raise FitError(f"{w}: min must be below max")
        if not lo <= ini <= hi:
            raise FitError(f"{w}: initial must be within [min, max]")
        if lo_c is not None and (lo <= lo_c[1] if lo_c[0] == "gt" else lo < lo_c[1]):
            raise FitError(f"{w}: min outside the parameter's own range ({'>' if lo_c[0] == 'gt' else '>='} "
                           f"{lo_c[1]:g})")
        if hi_c is not None and hi > hi_c:
            raise FitError(f"{w}: max above the parameter's own range (<= {hi_c:g})")
        scale = d.get("scale", "log" if lo > 0 else "lin")
        if scale not in ("lin", "log"):
            raise FitError(f"{w}.scale: lin | log")
        if scale == "log" and lo <= 0:
            raise FitError(f"{w}.scale: log needs min > 0")
        out.append(Param(key, ini, lo, hi, scale == "log", d["initial"]))
    return out


def _read_csv(path: str, tcol: str, ycol: str, where: str):
    p = Path(path).expanduser()
    if not p.is_file():
        raise FitError(f"{where}.csv: no file {p} on this machine (a study with a CSV needs run.host: the machine "
                       "that has the file)")
    with open(p, newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise FitError(f"{where}.csv: {p} is empty")
    head = [h.strip() for h in rows[0]]
    for c in (tcol, ycol):
        if c not in head:
            raise FitError(f"{where}: no column '{c}' in {p} (columns {head})")
    it, iy = head.index(tcol), head.index(ycol)
    t, y = [], []
    for row in rows[1:]:
        try:
            tv, yv = float(row[it]), float(row[iy])
        except (ValueError, IndexError):
            continue                       # a unit row, a blank line or a missing value
        t.append(tv)
        y.append(yv)
    if len(t) < 2:
        raise FitError(f"{where}: fewer than 2 numeric rows in {p}")
    return np.asarray(t, float), np.asarray(y, float)


def _series(st: dict, units: dict) -> list[Series]:
    data = (st.get("fit") or {}).get("data") or {}
    if not isinstance(data, dict):
        raise FitError("fit.data: {output: {t, y, sigma, unit, t_unit} | {csv, t, y, ...}}")
    an = st.get("analysis") or {}
    t0 = float(an.get("start_time", 0.0))
    t1 = float(an.get("stop_time", an.get("max_time", 0.0)) or 0.0)
    out = []
    for o, d in data.items():
        w = f"fit.data['{o}']"
        if o not in units:
            raise FitError(f"{w}: not an output of the study ({sorted(units)})")
        if not isinstance(d, dict):
            raise FitError(f"{w}: {{t, y, sigma, unit, t_unit}} or {{csv, t, y, ...}}")
        bad = set(d) - DATA_KEYS
        if bad:
            raise FitError(f"{w}: unknown keys {sorted(bad)} ({sorted(DATA_KEYS)})")
        if "csv" in d:
            if not isinstance(d.get("t"), str) or not isinstance(d.get("y"), str):
                raise FitError(f"{w}: with csv, t and y are column names")
            t, y = _read_csv(str(d["csv"]), d["t"], d["y"], w)
        else:
            t, y = d.get("t"), d.get("y")
            if not isinstance(t, list) or not isinstance(y, list) or len(t) != len(y) or len(t) < 2:
                raise FitError(f"{w}: t and y, lists of numbers of the same length (at least 2)")
            if len(t) > MAX_INLINE:
                raise FitError(f"{w}: {len(t)} points inline (at most {MAX_INLINE}): give a CSV on the machine")
            try:
                t, y = np.asarray(t, float), np.asarray(y, float)
            except (TypeError, ValueError):
                raise FitError(f"{w}: t and y must be numbers (units in 'unit' and 't_unit')") from None
        try:
            t = t * _time_factor(d.get("t_unit"))
            off, fac = _linear_map(d.get("unit"), units[o] or "1")
        except Exception as exc:  # noqa: BLE001 (a unit the table does not know)
            raise FitError(f"{w}: {exc}") from None
        y = off + fac * y
        if not (np.all(np.isfinite(t)) and np.all(np.isfinite(y))):
            raise FitError(f"{w}: t and y must be finite")
        order = np.argsort(t, kind="stable")
        t, y = t[order], y[order]
        if t[0] < t0 - 1e-12 * max(1.0, abs(t0)) or (t1 and t[-1] > t1 * (1 + 1e-12) + 1e-12):
            raise FitError(f"{w}: data times [{t[0]:g}, {t[-1]:g}] s must lie within the analysis' "
                           f"[{t0:g}, {t1:g}] s")
        has_sigma = "sigma" in d
        if has_sigma:
            try:
                sigma = abs(float(d["sigma"])) * abs(fac)
            except (TypeError, ValueError):
                raise FitError(f"{w}.sigma: a number in the data's unit") from None
            if not sigma > 0:
                raise FitError(f"{w}.sigma: must be > 0")
            scale = sigma
        else:
            rng = float(np.ptp(y))
            scale = rng if rng > 0 else max(float(np.max(np.abs(y))), 1e-300)
        weight = float(d.get("weight", 1.0))
        if not weight > 0:
            raise FitError(f"{w}.weight: must be > 0")
        out.append(Series(o, t, y, scale, weight, has_sigma))
    return out


def _steady(st: dict, units: dict) -> list[SteadyPoint]:
    pts = (st.get("fit") or {}).get("steady") or []
    if not isinstance(pts, list):
        raise FitError("fit.steady: [{set: {path: value}, values: {output: value}, sigma}]")
    out = []
    for i, d in enumerate(pts):
        w = f"fit.steady[{i}]"
        if not isinstance(d, dict) or not isinstance(d.get("values"), dict) or not d["values"]:
            raise FitError(f"{w}: {{set: {{path: value}}, values: {{output: measured value}}, sigma}}")
        bad = set(d) - STEADY_KEYS
        if bad:
            raise FitError(f"{w}: unknown keys {sorted(bad)} ({sorted(STEADY_KEYS)})")
        sets = d.get("set") or {}
        if not isinstance(sets, dict):
            raise FitError(f"{w}.set: {{path: value}} (as a sweep point)")
        for key in sets:
            _value_spec(st, key)
        vals = {}
        for o, v in d["values"].items():
            if o not in units:
                raise FitError(f"{w}.values['{o}']: not an output of the study ({sorted(units)})")
            vals[o] = _si(v, units[o] or "1", units[o] == "K", f"{w}.values['{o}']")
        out.append(SteadyPoint(dict(sets), vals, has_sigma="sigma" in d))
        if "sigma" in d:
            for o in vals:                          # sigma: a difference in the outputs' units
                out[-1].scales[o] = abs(float(d["sigma"]))
    # without sigma: an output's scale is the spread of its measured values over the points (or its size)
    allv: dict = {}
    for p in out:
        for o, v in p.values.items():
            allv.setdefault(o, []).append(v)
    for p in out:
        for o in p.values:
            if o not in p.scales:
                arr = np.asarray(allv[o])
                rng = float(np.ptp(arr))
                p.scales[o] = rng if rng > 0 else max(float(np.max(np.abs(arr))), 1e-300)
    return out


def load(st: dict, units: dict):
    f = st.get("fit")
    if not isinstance(f, dict):
        raise FitError("fit: {params, data, steady, starts, max_evaluations}")
    bad = set(f) - KEYS
    if bad:
        raise FitError(f"fit: unknown keys {sorted(bad)} ({sorted(KEYS)})")
    params = _params(st)
    series = _series(st, units)
    steady = _steady(st, units)
    if not series and not steady:
        raise FitError("fit: give data (time series) or steady (operating points), or both")
    starts = f.get("starts", 1)
    if not isinstance(starts, int) or isinstance(starts, bool) or not 1 <= starts <= 64:
        raise FitError("fit.starts: an integer 1-64")
    mx = f.get("max_evaluations", 400)
    if not isinstance(mx, int) or isinstance(mx, bool) or mx < 10:
        raise FitError("fit.max_evaluations: an integer >= 10")
    n_res = sum(len(s.t) for s in series) + sum(len(p.values) for p in steady)
    if n_res < len(params):
        raise FitError(f"fit: {n_res} measured values for {len(params)} parameters: at least as many values as "
                       "parameters are needed")
    return params, series, steady


def fit_errors(st: dict, units: dict) -> list[str]:
    """Validation of the `fit` key (format.validate_study): [] when it is fine."""
    if "fit" not in st:
        return []
    errs = []
    for other in ("sweep", "converge"):
        if st.get(other):
            errs.append(f"fit: not together with {other} in one study (a fit runs the model many times itself)")
    if (st.get("analysis") or {}).get("kind") == "linearize":
        errs.append("fit: transient or steady analyses only")
    try:
        load(st, units)
    except FitError as exc:
        errs.append(str(exc))
    return errs


# ---- the model at a parameter vector -------------------------------------------------------------------------------
def with_values(study: dict, values: dict) -> dict:
    """The study with `values` ({path: SI or as given}) set, without fit / images / expect (a run of the model)."""
    from .sweep import point_study
    st = point_study(study, values)
    st.pop("fit", None)
    return st


class RefEvaluator:
    """The model through the reference solver (catalog studies; exact for what the reference covers)."""

    def __init__(self, study: dict, series: list[Series], steady: list[SteadyPoint]):
        self.study, self.series, self.steady = study, series, steady
        an = study["analysis"]
        self.t0 = float(an.get("start_time", 0.0))
        self.t1 = float(an.get("stop_time", an.get("max_time", 0.0)))
        self.times = np.unique(np.concatenate([[self.t0]] + [s.t for s in series])) if series else None
        self.runs = 0

    def _sim(self, st: dict, t: np.ndarray) -> dict:
        from . import modelica
        tr = modelica.translate(st)
        r = tr.ref
        if r is None or not r.exact:
            raise FitError("the reference solver does not cover this study: fit it with OpenModelica")
        v = r.simulate(t, t0=self.t0)
        self.runs += 1
        return {o: np.asarray(sum(c * v[p] for c, p in terms), float) for o, (terms, _) in tr.outputs.items()}

    def __call__(self, values: dict):
        """({output: model at the data's times}, [{output: steady value} per steady point])."""
        series = {}
        if self.series:
            y = self._sim(with_values(self.study, values), self.times)
            for s in self.series:
                series[s.output] = np.interp(s.t, self.times, y[s.output])
        steady = [{o: y[o] for o in p.values} for p, y in
                  ((p, self._steady(with_values(self.study, {**values, **p.set}))) for p in self.steady)]
        return series, steady

    def _steady(self, st: dict) -> dict:
        """The steady state with the sources at their values at the analysis' end: exact (derivatives 0) where the
        reference has it, else the run's end once it has settled (the change over the last fifth of the run below
        1e-6 of the output's range; otherwise the run is too short and the evaluation fails, with that reason)."""
        from . import modelica
        from .ref import RefError
        tr = modelica.translate(st)
        r = tr.ref
        if r is None or not r.exact:
            raise FitError("the reference solver does not cover this study: fit it with OpenModelica")
        try:
            v = r.steady(self.t1)
            self.runs += 1
            return {o: float(sum(c * v[p] for c, p in terms)) for o, (terms, _) in tr.outputs.items()}
        except RefError:
            pass
        tt = np.linspace(self.t0, self.t1, 501)
        v = r.simulate(tt, t0=self.t0)
        self.runs += 1
        out = {}
        for o, (terms, _) in tr.outputs.items():
            y = np.asarray(sum(c * v[p] for c, p in terms), float)
            tail = y[400:]
            if float(np.ptp(tail)) > 1e-6 * max(float(np.ptp(y)), 1e-300) and float(np.ptp(y)) > 0:
                raise FitError(f"steady point: {o} still changes at the end of the run: lengthen the analysis")
            out[o] = float(y[-1])
        return out


# ---- residuals, optimizer, uncertainty -----------------------------------------------------------------------------
def residuals(series: list[Series], steady: list[SteadyPoint], model) -> np.ndarray:
    """Weighted, scaled residuals: each output's series counts as one (divided by sqrt(n)), each steady value as one."""
    ms, mp = model
    parts = []
    for s in series:
        parts.append(s.weight * (ms[s.output] - s.y) / s.scale / math.sqrt(len(s.t)))
    for p, m in zip(steady, mp):
        parts.append(np.array([(m[o] - v) / p.scales[o] for o, v in p.values.items()]))
    return np.concatenate(parts) if parts else np.zeros(0)


def _starts(params: list[Param], n: int) -> list[np.ndarray]:
    """The initial guess, then n - 1 points spread over the bounds (scrambled Sobol, fixed seed: reproducible)."""
    x0 = np.array([p.to_x(p.initial) for p in params])
    if n <= 1:
        return [x0]
    from scipy.stats import qmc
    lo = np.array([p.to_x(p.lo) for p in params])
    hi = np.array([p.to_x(p.hi) for p in params])
    u = qmc.Sobol(d=len(params), scramble=True, seed=12345).random_base2(max(0, math.ceil(math.log2(n - 1))))[:n - 1]
    return [x0] + [lo + (hi - lo) * row for row in u]


def uncertainty(params: list[Param], x: np.ndarray, jac: np.ndarray, r: np.ndarray, absolute: bool) -> dict:
    """Standard errors, 95 % intervals and correlations from the Jacobian at the optimum (in the fit's coordinates,
    mapped back; log parameters by the delta method, their intervals exp(x +- 1.96 se))."""
    m, n = jac.shape
    jtj = jac.T @ jac
    s2 = 1.0 if absolute else (float(r @ r) / max(m - n, 1))
    try:
        cond = float(np.linalg.cond(jtj))
    except np.linalg.LinAlgError:
        cond = float("inf")
    cov = s2 * np.linalg.pinv(jtj, rcond=1e-14)
    se_x = np.sqrt(np.clip(np.diag(cov), 0, None))
    d = np.where(se_x > 0, se_x, 1.0)
    corr = cov / np.outer(d, d)
    out, pairs = {}, []
    for i, p in enumerate(params):
        v = p.from_x(x[i])
        if p.log:
            se, ci = v * se_x[i], [math.exp(x[i] - 1.96 * se_x[i]), math.exp(x[i] + 1.96 * se_x[i])]
        else:
            se, ci = se_x[i], [v - 1.96 * se_x[i], v + 1.96 * se_x[i]]
        out[p.key] = {"value": float(v), "stderr": float(se), "ci95": [float(c) for c in ci]}
    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr[i, j]) > CORR_LIMIT:
                pairs.append([params[i].key, params[j].key, round(float(corr[i, j]), 4)])
    ident = not pairs and cond < 1e12
    for i, p in enumerate(params):
        out[p.key]["separable"] = not any(p.key in pr[:2] for pr in pairs)
    return {"params": out, "correlation": {"names": [p.key for p in params],
                                           "matrix": [[round(float(c), 4) for c in row] for row in corr]},
            "identifiable": ident, "not_separable": pairs, "condition": cond,
            "sigma_from": "the data's sigma" if absolute else "the residuals (reduced chi-square)",
            "residual_variance": s2}


def optimize(params, series, steady, evaluate, starts: int, max_evaluations: int, diff_step: float,
             cancel: Path | None = None):
    """Bounded least squares from each start; the best by cost. evaluate(values) -> (series, steady) or raises."""
    from scipy.optimize import least_squares
    lo = np.array([p.to_x(p.lo) for p in params])
    hi = np.array([p.to_x(p.hi) for p in params])
    m = sum(len(s.t) for s in series) + sum(len(p.values) for p in steady)
    state = {"evaluations": 0, "failed": 0, "last_error": None}

    def fun(x):
        if cancel is not None and Path(cancel).exists():
            raise KeyboardInterrupt("cancelled")
        state["evaluations"] += 1
        vals = {p.key: p.from_x(float(xi)) for p, xi in zip(params, x)}
        try:
            r = residuals(series, steady, evaluate(vals))
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 (a run that fails: a large residual, the optimizer steps back)
            state["failed"] += 1
            state["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
            return np.full(m, FAILED)
        return r if np.all(np.isfinite(r)) else np.full(m, FAILED)

    runs = []
    per = max(10, max_evaluations // max(starts, 1))
    for i, x0 in enumerate(_starts(params, starts)):
        x0 = np.clip(x0, lo, hi)
        t = time.time()
        res = least_squares(fun, x0, bounds=(lo, hi), method="trf", x_scale="jac", diff_step=diff_step,
                            max_nfev=per, xtol=1e-10, ftol=1e-10, gtol=1e-10)
        runs.append({"start": i, "x0": x0, "res": res, "seconds": time.time() - t})
    best = min(runs, key=lambda r: r["res"].cost)
    return best, runs, state


def _status(res) -> str:
    if res.status > 0:
        return "converged"
    return "max_evaluations" if res.status == 0 else "failed"


def quality(series, steady, model) -> dict:
    ms, mp = model
    out = {}
    for s in series:
        e = ms[s.output] - s.y
        sst = float(np.sum((s.y - s.y.mean()) ** 2))
        out[s.output] = {"rms": float(np.sqrt(np.mean(e ** 2))), "max_abs": float(np.max(np.abs(e))),
                         "r2": (1 - float(e @ e) / sst) if sst > 0 else None, "points": int(len(s.t))}
    if steady:
        out["steady"] = [{"set": p.set, "values": {o: {"data": v, "model": m[o], "error": m[o] - v}
                                                   for o, v in p.values.items()}} for p, m in zip(steady, mp)]
    return out


# ---- the study -----------------------------------------------------------------------------------------------------
def run_fit(name, study, folder: Path, solver: str, timeout: float, cancel: Path, run_study) -> dict:
    """The fit, then one full, checked run at the best values (its result is this study's, with `fit` added)."""
    from .runner import _times
    t_start = time.time()
    tr_units = _units(study)
    params, series, steady = load(study, tr_units)
    if solver != "ref":
        return {"ok": False, "why": "fitting with OpenModelica comes with V4 milestone F2: use solver 'ref' (the "
                                    "reference solver) for catalog studies until then",
                "reliable": False, "unreliable_because": ["not yet"]}
    evaluate = RefEvaluator(study, series, steady)
    tol = 1e-10                                   # the reference's own (Radau rtol 1e-10)
    diff_step = max(1e-7, math.sqrt(tol))
    f = study["fit"]
    try:
        best, runs, state = optimize(params, series, steady, evaluate, f.get("starts", 1),
                                     f.get("max_evaluations", 400), diff_step, cancel)
    except KeyboardInterrupt:
        return {"ok": False, "state": "cancelled", "why": "cancelled", "reliable": False}
    res = best["res"]
    x = res.x
    values = {p.key: p.from_x(float(xi)) for p, xi in zip(params, x)}
    unc = uncertainty(params, x, res.jac, res.fun, all(s.has_sigma for s in series) and
                      all(p.has_sigma for p in steady))
    model = evaluate(values)
    for p in params:
        u = unc["params"][p.key]
        u.update({"initial": p.initial, "given": p.given, "min": p.lo, "max": p.hi,
                  "scale": "log" if p.log else "lin",
                  "at_bound": bool(abs(p.to_x(u["value"]) - p.to_x(p.lo)) < 1e-6 * max(1, abs(p.to_x(p.lo))) or
                                   abs(p.to_x(u["value"]) - p.to_x(p.hi)) < 1e-6 * max(1, abs(p.to_x(p.hi))))})
    fit = {"status": _status(res), "message": res.message, "cost": float(res.cost), "evaluations": state["evaluations"],
           "failed_evaluations": state["failed"], "solver": solver, **unc, "quality": quality(series, steady, model),
           "residuals": {s.output: {"t": s.t.tolist(), "data": s.y.tolist(), "model": model[0][s.output].tolist()}
                         for s in series},
           "starts": [{"start": r["start"], "initial": {p.key: (p.initial if r["start"] == 0 else p.from_x(float(v)))
                                                        for p, v in zip(params, r["x0"])},
                       "end": {p.key: p.from_x(float(v)) for p, v in zip(params, r["res"].x)},
                       "cost": float(r["res"].cost), "status": _status(r["res"])} for r in runs],
           "seconds": round(time.time() - t_start, 2)}
    if state["last_error"]:
        fit["last_failed_evaluation"] = state["last_error"]
    # the best values, run with every check: the study's outputs and checks
    final = run_study(name, with_values(study, values), Path(folder) / "final", solver, timeout, cancel)
    final.pop("trace", None)
    out = dict(final)
    out["fit"] = fit
    why = list(out.get("unreliable_because") or [])
    if fit["status"] == "failed":
        why.append("fit: the optimizer failed")
    if why:
        out["reliable"] = False
        out["unreliable_because"] = why
    warns = list(out.get("warnings") or [])
    if not unc["identifiable"]:
        warns.append("fit: parameters not separable from this data "
                     + (", ".join(f"{a} and {b} (correlation {c})" for a, b, c in unc["not_separable"])
                        or f"(condition number {unc['condition']:.3g})")
                     + ": fix one of them, or add data that tells them apart")
    for k, u in unc["params"].items():
        if u["at_bound"]:
            warns.append(f"fit: {k} ended at a bound ({u['value']:g}): widen min/max if that is not physical")
    if fit["status"] == "max_evaluations":
        warns.append("fit: stopped at max_evaluations before converging: raise it, or start nearer")
    out["warnings"] = warns
    return out


def _units(study: dict) -> dict:
    """{output: SI unit} of the study's outputs (catalog studies: from the network; custom models: SI, '1')."""
    if "modelica" in study:
        return {o: "1" for o in (study.get("outputs") or {})}
    from .network import build
    net = build(with_values(study, {}))
    out = {}
    for o, ref in (study.get("outputs") or {}).items():
        try:
            out[o] = net.var_expr(ref)[1]
        except KeyError:
            out[o] = "1"
    return out
