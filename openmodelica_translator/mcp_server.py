"""mcp_server.py — MCP server: lets LLM-based tools simulate systems (circuits, drives, heat paths, controllers)
with OpenModelica and the Modelica Standard Library.
Version: 2026.11.09.3

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

From 2026.11.09.3 (phase 3 of llmt-runtime) the common server code is llmt-runtime's (llmt_runtime.server.Server: GUARD
below): the stale check, refusing and exiting, the tool wrapper, the log and the signal handlers.
Log:  $OMCT_OUT/.server/<time>_<host>_<pid>.log: every tool call (call, done with its duration, FAIL with the
      traceback), refusals, signals, fatal errors (faulthandler), exit; the newest 20 servers' logs are kept.
Time: every tool runs in a worker thread under a deadline of 48 s (llmt_runtime.server.BUDGET_S): SLURM commands and
      study_status keep to it, so each tool answers within its wait_s (at most 50 s; waits end 3 s before the deadline).
Stale: a server keeps the code it started with. When a newer release is installed (shared install: <prefix>/current
      points elsewhere; checkout: omct's or llmt-runtime's __init__.py has another version), a call that finds the
      server idle is refused with STALE_SERVER (the router repeats it on a new server) and the server exits; an idle
      server exits within about two periods by itself (OMCT_STALE_CHECK_S, 10 s). Busy servers answer (old code, with
      a server_stale warning) and exit once idle; the exit stops the pool for at most 10 s, then exits anyway.
      OMCT_EXIT_WHEN_STALE=1 always, 0 never; default: shared installs only.
Run:  omct-mcp                       (stdio)
      omct-mcp --http 8770 [--host 0.0.0.0]
"""
from __future__ import annotations

import argparse

from llmt_runtime.server import STALE_MARK, Server  # noqa: F401 (STALE_MARK: omct's name, tests use it)
from llmt_runtime.server import stale_reason  # noqa: F401 (omct's name: (why, shared) for a package folder)

from . import api
from .runtime import RT

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


# ---- a stale server: a newer version installed since this process started ----------------------------------
# A server keeps the code it started with. The router keeps its servers for hours, so after a deploy requests
# still ran the old release (carbonio, 2026-10-23: 2026.10.23.2 installed, 2026.10.22.1 running). Now a stale
# server that has nothing running exits: a call that finds it stale gets STALE_MARK (the router then opens a new
# server, which runs the installed release, and repeats the call there), and a watcher exits it when idle.
# Only a server started by main() (GUARD.serve()) refuses or exits: 2026.10.23.3 had no such rule, and llmt deploy
# --test, which runs the new release's tests before switching current to it, saw a 'stale' install, was refused and
# os._exit(0) ended pytest with exit code 0.

def _pool_busy() -> bool:
    """Studies still running in this process's worker pool (they would die with it)."""
    return any(not r.ready() for jobs in api._POOL.jobs.values() for r in jobs.values())


# The server's common state (llmt-runtime): stale checks every OMCT_STALE_CHECK_S (10 s), calls in flight, the exit
# (api._close_pool, bounded by llmt_runtime.server.SHUTDOWN_S), the log, the tool wrapper with its 48 s deadline.
GUARD = Server(RT, api.__file__, busy=_pool_busy, shutdown=api._close_pool)

# omct's names before 2026.11.09.3, kept as thin wrappers over GUARD. GUARD calls its own methods, not these: a test
# that changes what GUARD does patches GUARD (monkeypatch.setattr(S.GUARD, "stale", ...)), not these names.
_STATE = GUARD.state           # the same dict: inflight, background, exiting, checked, why, shared, serving
_LOCK = GUARD.lock


def slog(msg: str) -> None:
    """A line in the server's log (GUARD.log; nothing before main() opened it)."""
    GUARD.log(msg)


def _stale() -> str | None:
    """stale_reason for this process, checked at most every 2 s (GUARD.stale)."""
    return GUARD.stale()


def _may_exit() -> bool:
    """Only a server started by main(); then OMCT_EXIT_WHEN_STALE: 1 always, 0 never; default: a shared install."""
    return GUARD.may_exit()


def _exit_soon(why: str, delay: float) -> None:
    """Exits in `delay` s after stopping the pool (GUARD.exit_soon). Caller holds _LOCK."""
    GUARD.exit_soon(why, delay)


def _enter_call() -> str | None:
    """Counts a call in; a STALE_MARK message when the call is refused (GUARD.enter_call)."""
    return GUARD.enter_call()


def _leave_call() -> None:
    GUARD.leave_call()


def _watch_install(period: float | None = None) -> None:
    """Exits this server when it is stale, may exit and is idle (GUARD.watch; period: GUARD.period, 10 s)."""
    if period is not None:
        GUARD.period = float(period)
    GUARD.watch()


@server.tool()
@GUARD.tool(ToolError)
def study_format() -> dict:
    """The study format: component kinds by domain (ports, parameters with SI units and defaults, variables,
    initial values), analyses, derived quantities, conventions, an example; and whether omc and MSL are found."""
    return api.study_format()


@server.tool()
@GUARD.tool(ToolError)
def validate_studies(studies: dict) -> dict:
    """Static checks of studies given as {name: study}. Returns per study ok, errors (each says the fix), warnings,
    and for a valid study estimate {minutes (upper bound), procs, mem_mb}: what its run would ask for."""
    return api.validate(studies)


@server.tool()
@GUARD.tool(ToolError)
def run_studies(studies: dict, solver: str = "auto", wait_s: float = 45, request_name: str | None = None) -> dict:
    """Builds (or reuses) the Modelica model of each study, runs it with OpenModelica and checks it. Per study:
    outputs, derived quantities, steady values or the linearisation, checks (balance, initialisation, solver,
    events, overrides read back, energy, reference), reliable, expect, warnings and files. Returns after at most
    wait_s seconds: studies still running are listed in 'pending'; collect them with study_status.
    request_name ('run_<id>', optional): the request folder's name, so a caller knows the path before the answer;
    the same name again returns that request's status without submitting twice."""
    return api.run(studies, solver, wait=min(max(float(wait_s), 0.0), 50.0), request_name=request_name)


@server.tool()
@GUARD.tool(ToolError)
def study_status(request: str, wait_s: float = 45) -> dict:
    """The studies of a run_studies request (its 'request' path): finished ones with their results, the others in
    'pending' with their stage. Waits up to wait_s seconds (at most 50)."""
    return api.status(request, min(max(float(wait_s), 0.0), 50.0))


@server.tool()
@GUARD.tool(ToolError)
def get_model(study: dict, name: str = "study") -> dict:
    """The generated Modelica model (model.mo), the omc build script (build.mos) and the values applied at run
    time (params.txt) of one study, and which initial values are fixed; nothing runs."""
    return api.model(study, name)


@server.tool()
@GUARD.tool(ToolError)
def run_benchmarks(names: list[str] | None = None, solver: str = "auto", wait_s: float = 30) -> dict:
    """Runs the benchmark studies with closed-form answers (RC, RLC step and poles, mass-spring-damper, inertia,
    DC motor, thermal RC and steady state, two-mass drive, PI loop, a sweep) and reports pass or fail per check;
    by name also the chains chain_feat_modal (runs fea-translator first), chain_b3dt_mass and chain_b3dt_inertia
    (build123d-translator). Returns after at most wait_s seconds; collect the rest with bench_status(bench)."""
    return api.bench(names, solver, wait_s=min(max(float(wait_s), 0.0), 50.0))


@server.tool()
@GUARD.tool(ToolError)
def bench_status(bench: str, wait_s: float = 45) -> dict:
    """Pass/fail of benchmarks started by run_benchmarks (its 'bench' folder): finished cases with their checks,
    the others pending."""
    return api.bench_status(bench, min(max(float(wait_s), 0.0), 50.0))


@server.tool()
@GUARD.tool(ToolError)
def jobs(limit: int = 20, active: bool = False) -> dict:
    """Recent requests on this machine (from any conversation) with each study's state; active=True: only
    requests with studies still running."""
    return api.jobs(int(limit), bool(active))


@server.tool()
@GUARD.tool(ToolError)
def cancel(request: str, names: list[str] | None = None) -> dict:
    """Stops a request's studies (or the named ones) with a cancel flag; returns their states."""
    return api.cancel(request, names)


@server.tool()
@GUARD.tool(ToolError)
def get_image(path: str) -> Image:
    """A PNG written by run_studies (results' images.<name>.path) or render_images: time plots of the outputs (one
    panel per unit) or Bode plots. You see it, but the user usually does not (tool results are hidden or collapsed
    in their app): to show the user a plot, chart outputs.<name>.series yourself; describe what this one shows."""
    return Image(path=str(api.image_path(path)))


@server.tool()
@GUARD.tool(ToolError)
def render_images(study_folder: str, images: list | bool = True) -> dict:
    """New pictures of a finished study without running again: study_folder is <request>/<study name> (the folder
    holding <name>.json). images: true (the defaults) or [{name, outputs: [...], window: [t0, t1]} | {name,
    bode: true}]. Then get_image each path."""
    return api.render_images(study_folder, images)


@server.tool()
@GUARD.tool(ToolError)
def get_file(path: str, max_mb: float = 20) -> dict:
    """A file written by run_studies (a path from its result: model.mo, result.mat, sim.log, ...) as base64."""
    return api.read_file(path, max_mb)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, help="serve MCP over streamable HTTP on this port (default: stdio)")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    # llmt-runtime: may now refuse stale calls and exit; the log ($OMCT_OUT/.server); SIGTERM/SIGHUP/SIGINT stop the
    # pool first (re-raising skips atexit: llmt servers --stop); the install watch
    GUARD.serve()
    if a.http is None:
        server.run()
    elif _V2:
        server.run(transport="streamable-http", host=a.host, port=a.http)
    else:
        server.settings.host, server.settings.port = a.host, a.http
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
