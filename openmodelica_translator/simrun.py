"""simrun.py — run a compiled model from the cache, read its log, check the overrides.
Version: 2026.10.21.2

simulate(build_dir, run_dir, overrides, ...) -> {ok, exit, seconds, log, stats, result, linear, errors, warnings}
  - writes params.txt (every value, modelica.override_file) and runs
    <cache>/omct_model -inputPath=<cache> -outputPath=<run> -overrideFile=params.txt -r=result.mat
        -startTime -stopTime -stepSize -tolerance -s=<method> -lv=LOG_STATS -alarm=<s> [-variableFilter] [-l=<t>]
    in its own process group, with OMP_NUM_THREADS=OMCT_OMP_THREADS (1: measured on mochi, OpenMP threads only
    slow a simulation down);
  - an override the model refuses ("It is not possible to override ...") or does not know ("override variable
    name not found") is an error: the run exits 0 and reports success in both cases (measured);
  - parse_log(): LOG_SUCCESS initialisation (homotopy steps), simulation finished, LOG_ASSERT, chattering,
    steady state, warnings, and the LOG_STATS block (events, steps, test failures).
read_back(result, overrides) compares every value with the one the result file recorded.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .modelica import MODEL, override_file
from .proc import run as run_proc

LINE = re.compile(r"^(LOG_[A-Z_]+)\s*\|\s*(\w+)\s*\|\s?(.*)$")
CONT = re.compile(r"^\|\s+\|\s*\|\s*\|\s?(.*)$")


def omp_threads() -> int:
    try:
        return max(1, int(os.environ.get("OMCT_OMP_THREADS", "1")))
    except ValueError:
        return 1


def parse_log(text: str) -> dict:
    """The simulation executable's stdout -> messages and facts."""
    msgs = []
    for raw in text.splitlines():
        m = LINE.match(raw)
        if m:
            msgs.append({"stream": m.group(1), "level": m.group(2), "text": m.group(3).rstrip()})
            continue
        c = CONT.match(raw)
        if c and msgs:
            msgs[-1]["text"] += "\n" + c.group(1).rstrip()
    out = {"messages": msgs, "init_ok": False, "homotopy_steps": None, "finished": False, "asserts": [],
           "chattering": None, "steady_state_time": None, "override_refused": [], "override_unknown": [],
           "warnings": [], "stats": {}, "linear_model": None}
    for m in msgs:
        t, s, lv = m["text"], m["stream"], m["level"]
        if s == "LOG_SUCCESS" and "initialization finished successfully" in t:
            out["init_ok"] = True
            h = re.search(r"with (\d+) (?:local )?homotopy steps", t)
            out["homotopy_steps"] = int(h.group(1)) if h else 0
        elif s == "LOG_SUCCESS" and "simulation finished successfully" in t:
            out["finished"] = True
        elif s == "LOG_ASSERT" and lv in ("error", "info", "warning") and (lv == "error" or "assert" in t.lower()
                                                                             or "violated" in t):
            out["asserts"].append(t)
        elif s == "LOG_ASSERT" and "terminated by an assertion" in t:
            out["asserts"].append(t)
        elif s == "LOG_STATS" and t.startswith("### STATISTICS"):
            out["stats"] = _stats(t)
        elif "It is not possible to override the following quantity:" in t:
            out["override_refused"].append(t.split("quantity:", 1)[1].split("\n")[0].strip())
        elif "override variable name not found in model:" in t:
            out["override_unknown"].append(t.split("model:", 1)[1].split("\n")[0].strip())
        elif "Chattering detected around time" in t:
            ch = re.search(r"around time ([-+0-9.eE]+)\.\.([-+0-9.eE]+)", t)
            out["chattering"] = {"t0": float(ch.group(1)), "t1": float(ch.group(2))} if ch else {"text": t[:200]}
        elif "steady state reached at time" in t:
            out["steady_state_time"] = float(re.search(r"time = ([-+0-9.eE]+)", t).group(1))
        elif "Linear model is created at" in t:
            out["linear_model"] = t.split("created at", 1)[1].strip()
        elif "You are overriding" in t and "small value or zero" in t:
            pass                       # every 0.0 we write (offsets, start times) would say so
        elif lv == "warning":
            out["warnings"].append(t.replace("\n", " ")[:300])
        if "terminated by an assertion" in t and t not in out["asserts"]:
            out["asserts"].append(t)
        if "Error in initialization" in t:
            out["asserts"].append(t.replace("\n", " "))
    return out


def _stats(t: str) -> dict:
    st = {}
    for key, pat in (("state_events", r"(\d+) state events"), ("time_events", r"(\d+) time events"),
                     ("steps", r"(\d+) steps taken"), ("rhs_calls", r"(\d+) calls of functionODE"),
                     ("jacobians", r"(\d+) evaluations of jacobian"), ("error_test_failures", r"(\d+) error test failures"),
                     ("convergence_test_failures", r"(\d+) convergence test failures")):
        m = re.search(pat, t)
        if m:
            st[key] = int(m.group(1))
    m = re.search(r"solver: (\w+)", t)
    if m:
        st["solver"] = m.group(1)
    m = re.search(r"([0-9.eE+-]+)s \[100\.0%\] total", t)
    if m:
        st["total_s"] = float(m.group(1))
    return st


def simulate(build_dir, run_dir, overrides: dict, *, start: float, stop: float, intervals: int, tolerance: float,
             method: str = "dassl", variable_filter: str | None = None, timeout: float = 600,
             linearize_at: float | None = None, cancel: Path | None = None, tag: str = "") -> dict:
    build_dir, run_dir = Path(build_dir), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    pf = run_dir / f"params{tag}.txt"
    pf.write_text(override_file(overrides))
    res = f"result{tag}.mat"
    stop_ = stop if linearize_at is None else max(linearize_at, start)
    if stop_ <= start:
        stop_ = start + 1e-6              # linearising at the start: a minimal run
    cmd = [build_dir / MODEL, f"-inputPath={build_dir}", f"-outputPath={run_dir}", f"-overrideFile={pf}", f"-r={res}",
           f"-startTime={start!r}", f"-stopTime={stop_!r}", f"-stepSize={(stop_ - start) / max(intervals, 1)!r}",
           f"-tolerance={tolerance!r}", f"-s={method}", "-lv=LOG_STATS", f"-alarm={max(1, int(timeout))}"]
    if variable_filter:
        cmd.append(f"-variableFilter={variable_filter}")
    if linearize_at is not None:
        cmd.append(f"-l={linearize_at!r}")
    log = run_dir / f"sim{tag}.log"
    p = run_proc(cmd, run_dir, timeout + 5, log, cancel, env={"OMP_NUM_THREADS": str(omp_threads()),
                                                             "OPENBLAS_NUM_THREADS": "1"})
    info = parse_log(log.read_text(errors="replace"))
    errs, warns = [], list(info["warnings"])
    if p["timed_out"] or p["exit"] == 14 or p["exit"] == -14:           # 14: SIGALRM from -alarm
        errs.append(f"timed out after {timeout:g} s (raise run.timeout_s, or shorten the stop time)")
    if p["cancelled"]:
        errs.append("cancelled")
    for n in info["override_refused"]:
        errs.append(f"OpenModelica refused to set {n} (it is structural, final or bound in the compiled model)")
    for n in info["override_unknown"]:
        errs.append(f"OpenModelica does not know {n} (a translator bug: the override names a missing variable)")
    if info["asserts"]:
        errs.append("simulation stopped by an assertion: " + " | ".join(a.replace("\n", " ")[:300] for a in info["asserts"][:3]))
    if not info["init_ok"] and not errs:
        errs.append("initialisation failed (see sim.log)")
    if not info["finished"] and not errs:
        errs.append(f"the simulation did not finish (exit {p['exit']}; see sim.log)")
    if p["exit"] not in (0, None) and not errs:
        errs.append(f"the simulation exited with {p['exit']} (see sim.log)")
    if info["homotopy_steps"]:
        warns.append(f"initialisation needed {info['homotopy_steps']} homotopy steps")
    if info["chattering"]:
        warns.append(f"chattering (many events in a row) around t = {info['chattering'].get('t0')}")
    out = {"ok": not errs, "exit": p["exit"], "seconds": p["seconds"], "stats": info["stats"],
           "init_homotopy_steps": info["homotopy_steps"], "chattering": info["chattering"],
           "steady_state_time": info["steady_state_time"], "errors": errs, "warnings": warns,
           "files": {"params": str(pf), "log": str(log)}}
    if (run_dir / res).is_file():
        out["result"] = str(run_dir / res)
        out["files"]["result"] = str(run_dir / res)
    lm = run_dir / "linearized_model.mo"
    if linearize_at is not None:
        if lm.is_file():
            out["linear"] = str(lm)
            out["files"]["linear"] = str(lm)
        elif out["ok"]:
            out["ok"] = False
            out["errors"].append("no linearized_model.mo was written (see sim.log)")
    return out


def read_back(result, overrides: dict, rel: float = 1e-12) -> dict:
    """Every applied value against the result file: parameters from data_1, start values from the first point."""
    bad, missing = [], []
    for name, want in sorted(overrides.items()):
        if name not in result:
            missing.append(name)
            continue
        w = (1.0 if want else 0.0) if isinstance(want, bool) else float(want)
        got = result.value(name) if result.kind(name) == 1 else float(result.series(name)[0])
        if abs(got - w) > rel * max(1.0, abs(w)) + 1e-300:
            bad.append({"name": name, "set": w, "got": got})
    return {"ok": not bad and not missing, "checked": len(overrides) - len(missing), "mismatch": bad,
            "not_recorded": missing}
