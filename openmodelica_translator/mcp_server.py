"""mcp_server.py — MCP server: lets LLM-based tools simulate systems (circuits, drives, heat paths, controllers)
with OpenModelica and the Modelica Standard Library.
Version: 2026.10.26.1

Tools (all JSON):
  study_format      the study format: component kinds, ports, parameters, analyses, conventions. Read it first.
  validate_studies  static checks of {name: study} (fast; fix errors before running)
  run_studies       builds, runs and checks {name: study} (returns within ~45 s; longer studies stay pending)
  study_status      collects the pending studies of a run_studies request
  get_model         the generated Modelica model, omc script and override file of one study, without running
  run_benchmarks    the benchmark studies with closed-form answers (is the toolchain right?)
  bench_status      collects benchmarks still running
  jobs              recent requests and their states
  cancel            stops a request's studies
  get_image         a PNG (time plots, Bode plots) as an image (seen by the model; users chart outputs.*.series)
  render_images     more pictures of a finished study, without running again
  get_file          a written file as base64 (for callers on another machine)

Log:  $OMCT_OUT/.server/<time>_<host>_<pid>.log: every tool call (start, end, duration, failure with traceback), signals,
      fatal errors (faulthandler), exit; the last 20 servers' logs are kept.
Stale: a server keeps the code it started with. When a newer release is installed (shared install: <prefix>/current
      points elsewhere; checkout: __init__.py has another version), a call that finds the server idle is refused
      with STALE_SERVER (the router repeats it on a new server) and the server exits; an idle server exits within
      20 s by itself. Busy servers answer (old code, with a server_stale warning) and exit once idle.
      OMCT_EXIT_WHEN_STALE=1 always, 0 never; default: shared installs only.
Run:  omct-mcp                       (stdio)
      omct-mcp --http 8770 [--host 0.0.0.0]
"""
from __future__ import annotations

import argparse
import atexit
import faulthandler
import functools
import os
import resource
import re
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

import anyio.to_thread

from . import api

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
    _V2 = True
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp import Image
    from mcp.server.fastmcp.exceptions import ToolError
    _V2 = False

INSTRUCTIONS = """openmodelica-translator simulates whole systems over time with OpenModelica and the Modelica
Standard Library 4.0.0: circuits (R, L, C, sources, op-amps, transformers), translational and rotational
mechanics (masses, inertias, springs, dampers, gears, force/torque sources), DC and AC machines (induction,
PMSM; transient or quasi-static phasor models; losses, heat ports) with grids and drives, heat, coolant loops (pumps, pipes, valves;
Thermal.FluidHeatFlow) and control blocks (PI, PID, LimPID, limiters, tables). A study lists components (catalog kinds with parameters), connections between their ports,
outputs, and an analysis: transient, steady or linearize (eigenvalues, modes, Bode); derived quantities (step
metrics, crossing times, window statistics) and expect checks. It takes other translators' results as values
(a build123d-translator part result + density gives a mass or a moment of inertia: see study_format).
Workflow: call study_format once and follow its conventions; validate_studies and fix every error;
run_studies (studies that take longer come back 'pending': collect them with study_status). Before trusting
numbers check warnings and checks: balance, initialization, solver, events, overrides (every value read back),
energy (the energy balance), reference (an independent solver; custom models: cross_solver, another integrator;
declare "invariants" for balances); if 'reliable' is false, do not report the numbers as results (fix what
'unreliable_because' says). For accuracy add "converge": true (a second run at tolerance/10). Units: SI everywhere (K, rad/s, m, kg, N.m); values may be strings with units
("10 mH", "80 degC", "3000 rpm") or {value, unit, from} for chained values. Initial values (v0, i0, s0, w0, T0)
are fixed only where they are free (a capacitor across a source follows it). Every electrical circuit needs a
ground. Values change without recompiling: "sweep": {params: {"comp.param": [values]}, mode: grid | zip,
report: [paths]} runs every point on one build and returns sweep.table (run.procs points at a time). Polling costs tokens: when a run needs more than about 5 minutes, tell the user when
it should finish and stop; collect it later with study_status.
Long studies (big sweeps) run as SLURM jobs on the machine (when OMCT_EXECUTOR=slurm): run_studies returns them
pending with job ids. They do not depend on this conversation. Report the expected finish time and stop; do not
poll. Later, study_status(request) collects them; jobs() lists recent and active runs (also those started in
other conversations); cancel(request) stops them. A study's "run": {timeout_s, mem, procs, queue} overrides the
machine's defaults for that study (procs: sweep points at once), within the caps in study_format's status.
Pictures: the user usually does not see images inside tool results (get_image shows them to you). To show the
user a plot, draw outputs.<name>.series {t, y} (in every result) with your own charting or artifact tools, and never
say "the image above". "images": true + get_image(results.images.<name>.path) lets you check a run by eye;
render_images makes others for a finished study. Results: outputs.<name>.{final, min, max, ...}, derived.<name>.*,
steady.<name>.*, linearization.{eigenvalues, modes, natural_frequencies_Hz, damping_ratios, dc_gain, bode},
sweep.table.<i>.{params, values, reliable}."""

server = _Server("openmodelica-translator", instructions=INSTRUCTIONS)


# ---- the server's own log: what it did before a disconnect (behind the router its stderr is not kept) --------
def _log_path():
    import socket
    d = api.out_root() / ".server"
    d.mkdir(parents=True, exist_ok=True)
    logs = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime)
    for old in logs[:-20]:                               # keep the last 20 servers' logs
        old.unlink(missing_ok=True)
    return d / f"{time.strftime('%Y%m%d_%H%M%S')}_{socket.gethostname().split('.')[0]}_{os.getpid()}.log"


_LOG = {"file": None}


def slog(msg: str) -> None:
    f = _LOG["file"]
    if f is None:
        return
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} maxrss {rss} {msg}\n")
        f.flush()
    except Exception:  # noqa: BLE001 - logging never breaks a call
        pass


def _summary(kwargs: dict) -> str:
    out = []
    for k, v in kwargs.items():
        if isinstance(v, dict):
            names = list(v)[:6]
            flags = sorted({f for st in v.values() if isinstance(st, dict) for f in ("sweep", "converge", "modelica", "run")
                            if st.get(f)})
            out.append(f"{k}={names}{' ' + ','.join(flags) if flags else ''}")
        else:
            out.append(f"{k}={str(v)[:80]}")
    return " ".join(out)


# ---- a stale server: a newer version installed since this process started ----------------------------------
# A server keeps the code it started with. The router keeps its servers for hours, so after a deploy requests
# still ran the old release (carbonio, 2026-10-23: 2026.10.23.2 installed, 2026.10.22.1 running). Now a stale
# server that has nothing running exits: a call that finds it stale gets STALE_MARK (the router then opens a new
# server, which runs the installed release, and repeats the call there), and a watcher exits it when idle.
STALE_MARK = "STALE_SERVER"
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)
_STATE = {"inflight": 0, "exiting": False, "checked": 0.0, "why": None, "shared": False, "serving": False}
_LOCK = threading.Lock()


def stale_reason(pkg_dir: Path, running: str) -> tuple[str | None, bool]:
    """(why this process runs old code or None, shared install?). Shared install (a release under
    <prefix>/releases with RELEASE.json): <prefix>/current now points to another release. Otherwise: the
    package's __init__.py on disk carries another version (a checkout or editable install updated in place)."""
    rel = pkg_dir.parent.parent
    if (rel / "RELEASE.json").is_file() and rel.parent.name == "releases":
        cur = rel.parent.parent / "current"
        try:
            if cur.exists() and cur.resolve() != rel.resolve():
                return f"release {cur.resolve().name} is installed; this server runs {rel.name} ({running})", True
        except OSError:
            pass
        return None, True
    try:
        m = VERSION_RE.search((pkg_dir / "__init__.py").read_text())
    except OSError:
        return None, False
    if m and m.group(1) != running:
        return f"version {m.group(1)} is installed; this server runs {running}", False
    return None, False


def _stale() -> str | None:
    """stale_reason for this process, checked at most every 2 s."""
    now = time.time()
    if now - _STATE["checked"] > 2.0:
        _STATE["why"], _STATE["shared"] = stale_reason(Path(api.__file__).resolve().parent, api.__version__)
        _STATE["checked"] = now
    return _STATE["why"]


def _may_exit() -> bool:
    """Only a server started by main() (never the tools called in-process: tests, scripts). Then
    OMCT_EXIT_WHEN_STALE: 1 always, 0 never; default: in a shared install (its servers are the router's).
    (2026.10.23.3 had no 'serving': llmt deploy --test runs the new release's tests before switching current to it,
    so the tests saw a 'stale' install, were refused and os._exit(0) ended pytest with exit code 0.)"""
    if not _STATE["serving"]:
        return False
    v = os.environ.get("OMCT_EXIT_WHEN_STALE", "").strip()
    return v == "1" or (v != "0" and _STATE["shared"])


def _pool_busy() -> bool:
    """Studies still running in this process's worker pool (they would die with it)."""
    return any(not r.ready() for jobs in api._POOL.jobs.values() for r in jobs.values())


def _exit_soon(why: str, delay: float) -> None:
    """Exits in `delay` s (an answer in flight reaches the caller first). Caller holds _LOCK."""
    if _STATE["exiting"]:
        return
    _STATE["exiting"] = True
    slog(f"exit: {why}")
    sys.stderr.write(f"omct: {STALE_MARK}: exiting to pick up the new code: {why}\n")
    sys.stderr.flush()

    def go():
        time.sleep(delay)
        api._close_pool()
        os._exit(0)
    threading.Thread(target=go, daemon=True, name="omct-exit").start()


def _enter_call() -> str | None:
    """Counts a call in. Returns a STALE_MARK message (the call is refused) when this server is stale, may exit
    and has nothing else running; otherwise None."""
    with _LOCK:
        why = _stale()
        if why and _may_exit() and (_STATE["exiting"] or (not _STATE["inflight"] and not _pool_busy())):
            _exit_soon(why, 1.0)
            return (f"{STALE_MARK}: {why}. This server is exiting so that the next one runs the installed version: "
                    f"call again.")
        _STATE["inflight"] += 1
        return None


def _leave_call() -> None:
    with _LOCK:
        _STATE["inflight"] -= 1


def _watch_install(period: float = 20.0) -> None:
    """Exits this server when it is stale, may exit and is idle (no calls, no pool studies)."""
    def loop():
        while True:
            time.sleep(period)
            with _LOCK:
                if _stale() and _may_exit() and not _STATE["inflight"] and not _pool_busy():
                    _exit_soon(_STATE["why"], 0.0)
    threading.Thread(target=loop, daemon=True, name="omct-watch-install").start()


def _logged(fn):
    """Logs the call, and runs the (blocking) tool in a worker thread: run_studies and study_status wait up to 50 s,
    and on an event loop they would block every other request and ping of the connection meanwhile (MCP SDK
    versions differ in whether they do this themselves)."""
    @functools.wraps(fn)
    async def wrap(*args, **kwargs):
        t0 = time.time()
        slog(f"call {fn.__name__} {_summary(kwargs)}")
        refused = _enter_call()
        if refused:
            slog(f"refused {fn.__name__}: {refused}")
            raise ToolError(refused)
        try:
            r = await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
        except Exception as exc:                         # noqa: BLE001 - logged, then raised as a ToolError
            slog(f"FAIL {fn.__name__} after {time.time() - t0:.1f} s: {type(exc).__name__}: {exc}\n"
                 + traceback.format_exc())
            if isinstance(exc, ToolError):
                raise
            # as a ToolError the message reaches the caller (SDK 2.x hides the text of any other exception)
            where = f" (traceback: {_LOG['path']})" if _LOG.get("path") else ""
            raise ToolError(f"{type(exc).__name__}: {exc}{where}") from exc
        except BaseException as exc:                     # noqa: BLE001 - cancellation, exit: logged, re-raised
            slog(f"FAIL {fn.__name__} after {time.time() - t0:.1f} s: {type(exc).__name__}")
            raise
        finally:
            _leave_call()
        slog(f"done {fn.__name__} {time.time() - t0:.1f} s")
        if _STATE["why"] and isinstance(r, dict):
            r = {**r, "server_stale": f"{_STATE['why']}: this answer comes from the old code"
                                      + ("; the server restarts when idle" if _may_exit() else
                                         "; restart the MCP server")}
        return r
    return wrap


def _start_log() -> None:
    try:
        p = _log_path()
        _LOG["file"] = open(p, "a", buffering=1)
        _LOG["path"] = str(p)
    except OSError:
        return
    faulthandler.enable(file=_LOG["file"])              # a fatal signal (segfault, abort) dumps every thread here
    slog(f"start omct {api.__version__} argv {sys.argv[1:]} python {sys.version.split()[0]}")
    atexit.register(lambda: slog("exit"))
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        def handler(signum, frame, prev=signal.getsignal(sig)):
            slog(f"signal {signal.Signals(signum).name} received")
            signal.signal(signum, prev if callable(prev) or prev in (signal.SIG_DFL, signal.SIG_IGN) else signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        signal.signal(sig, handler)
    old_hook = sys.excepthook

    def hook(t, v, tb):
        slog("uncaught " + "".join(traceback.format_exception(t, v, tb)))
        old_hook(t, v, tb)
    sys.excepthook = hook


@server.tool()
@_logged
def study_format() -> dict:
    """The study format: component kinds by domain (ports, parameters with SI units and defaults, variables,
    initial values), analyses, derived quantities, conventions, an example; and whether omc and MSL are found."""
    return api.study_format()


@server.tool()
@_logged
def validate_studies(studies: dict) -> dict:
    """Static checks of studies given as {name: study}. Returns per study ok, errors (each says the fix), warnings,
    and for a valid study estimate {minutes (upper bound), procs, mem_mb}: what its run would ask for."""
    return api.validate(studies)


@server.tool()
@_logged
def run_studies(studies: dict, solver: str = "auto", wait_s: float = 45) -> dict:
    """Builds (or reuses) the Modelica model of each study, runs it with OpenModelica and checks it. Per study:
    outputs, derived quantities, steady values or the linearisation, checks (balance, initialisation, solver,
    events, overrides read back, energy, reference), reliable, expect, warnings and files. Returns after at most
    wait_s seconds: studies still running are listed in 'pending'; collect them with study_status."""
    return api.run(studies, solver, wait=min(max(float(wait_s), 0.0), 50.0))


@server.tool()
@_logged
def study_status(request: str, wait_s: float = 45) -> dict:
    """The studies of a run_studies request (its 'request' path): finished ones with their results, the others in
    'pending' with their stage. Waits up to wait_s seconds (at most 50)."""
    return api.status(request, min(max(float(wait_s), 0.0), 50.0))


@server.tool()
@_logged
def get_model(study: dict, name: str = "study") -> dict:
    """The generated Modelica model (model.mo), the omc build script (build.mos) and the values applied at run
    time (params.txt) of one study, and which initial values are fixed; nothing runs."""
    return api.model(study, name)


@server.tool()
@_logged
def run_benchmarks(names: list[str] | None = None, solver: str = "auto", wait_s: float = 30) -> dict:
    """Runs the benchmark studies with closed-form answers (RC, RLC step and poles, mass-spring-damper, inertia,
    DC motor, thermal RC and steady state, two-mass drive, PI loop, a sweep) and reports pass or fail per check;
    by name also the chains chain_feat_modal (runs fea-translator first), chain_b3dt_mass and chain_b3dt_inertia
    (build123d-translator). Returns after at most wait_s seconds; collect the rest with bench_status(bench)."""
    return api.bench(names, solver, wait_s=min(max(float(wait_s), 0.0), 50.0))


@server.tool()
@_logged
def bench_status(bench: str, wait_s: float = 45) -> dict:
    """Pass/fail of benchmarks started by run_benchmarks (its 'bench' folder): finished cases with their checks,
    the others pending."""
    return api.bench_status(bench, min(max(float(wait_s), 0.0), 50.0))


@server.tool()
@_logged
def jobs(limit: int = 20, active: bool = False) -> dict:
    """Recent requests on this machine (from any conversation) with each study's state; active=True: only
    requests with studies still running."""
    return api.jobs(int(limit), bool(active))


@server.tool()
@_logged
def cancel(request: str, names: list[str] | None = None) -> dict:
    """Stops a request's studies (or the named ones) with a cancel flag; returns their states."""
    return api.cancel(request, names)


@server.tool()
@_logged
def get_image(path: str) -> Image:
    """A PNG written by run_studies (results' images.<name>.path) or render_images: time plots of the outputs (one
    panel per unit) or Bode plots. You see it, but the user usually does not (tool results are hidden or collapsed
    in their app): to show the user a plot, chart outputs.<name>.series yourself; describe what this one shows."""
    return Image(path=str(api.image_path(path)))


@server.tool()
@_logged
def render_images(study_folder: str, images: list | bool = True) -> dict:
    """New pictures of a finished study without running again: study_folder is <request>/<study name> (the folder
    holding <name>.json). images: true (the defaults) or [{name, outputs: [...], window: [t0, t1]} | {name,
    bode: true}]. Then get_image each path."""
    return api.render_images(study_folder, images)


@server.tool()
@_logged
def get_file(path: str, max_mb: float = 20) -> dict:
    """A file written by run_studies (a path from its result: model.mo, result.mat, sim.log, ...) as base64."""
    return api.read_file(path, max_mb)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, help="serve MCP over streamable HTTP on this port (default: stdio)")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    _start_log()
    _STATE["serving"] = True
    _watch_install()
    if a.http is None:
        server.run()
    elif _V2:
        server.run(transport="streamable-http", host=a.host, port=a.http)
    else:
        server.settings.host, server.settings.port = a.host, a.http
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
