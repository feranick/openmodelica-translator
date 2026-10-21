"""mcp_server.py — MCP server: lets LLM-based tools simulate systems (circuits, drives, heat paths, controllers)
with OpenModelica and the Modelica Standard Library.
Version: 2026.10.21.7

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
  get_image         a PNG (time plots, Bode plots) as an image
  render_images     more pictures of a finished study, without running again
  get_file          a written file as base64 (for callers on another machine)

Run:  omct-mcp                       (stdio)
      omct-mcp --http 8770 [--host 0.0.0.0]
"""
from __future__ import annotations

import argparse

from . import api

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server
    _V2 = True
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp import Image
    _V2 = False

INSTRUCTIONS = """openmodelica-translator simulates whole systems over time with OpenModelica and the Modelica
Standard Library 4.0.0: circuits (R, L, C, sources, op-amps, transformers), translational and rotational
mechanics (masses, inertias, springs, dampers, gears, force/torque sources), DC motors, heat transfer
(capacitors, conductors, convection, radiation, boundaries) and control blocks (PI, PID, LimPID, filters,
limiters, tables). A study lists components (catalog kinds with parameters), connections between their ports,
outputs, and an analysis: transient, steady or linearize (eigenvalues, modes, Bode); derived quantities (step
metrics, crossing times, window statistics) and expect checks. It takes other translators' results as values.
Workflow: call study_format once and follow its conventions; validate_studies and fix every error;
run_studies (studies that take longer come back 'pending': collect them with study_status). Before trusting
numbers check warnings and checks: balance, initialization, solver, events, overrides (every value read back),
energy (the energy balance), reference (an independent solver on linear networks); if 'reliable' is false, do
not report the numbers as results (fix what 'unreliable_because' says). For accuracy add "converge": true (a
second run at tolerance/10). Units: SI everywhere (K, rad/s, m, kg, N.m); values may be strings with units
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
Pictures: "images": true in a study; results.images.<name>.path, then get_image shows each; render_images makes
them again for a finished study. Results: outputs.<name>.{final, min, max, ...}, derived.<name>.*,
steady.<name>.*, linearization.{eigenvalues, modes, natural_frequencies_Hz, damping_ratios, dc_gain, bode},
sweep.table.<i>.{params, values, reliable}."""

server = _Server("openmodelica-translator", instructions=INSTRUCTIONS)


@server.tool()
def study_format() -> dict:
    """The study format: component kinds by domain (ports, parameters with SI units and defaults, variables,
    initial values), analyses, derived quantities, conventions, an example; and whether omc and MSL are found."""
    return api.study_format()


@server.tool()
def validate_studies(studies: dict) -> dict:
    """Static checks of studies given as {name: study}. Returns per study ok, errors (each says the fix), warnings."""
    return api.validate(studies)


@server.tool()
def run_studies(studies: dict, solver: str = "auto", wait_s: float = 45) -> dict:
    """Builds (or reuses) the Modelica model of each study, runs it with OpenModelica and checks it. Per study:
    outputs, derived quantities, steady values or the linearisation, checks (balance, initialisation, solver,
    events, overrides read back, energy, reference), reliable, expect, warnings and files. Returns after at most
    wait_s seconds: studies still running are listed in 'pending'; collect them with study_status."""
    return api.run(studies, solver, wait=min(max(float(wait_s), 0.0), 50.0))


@server.tool()
def study_status(request: str, wait_s: float = 45) -> dict:
    """The studies of a run_studies request (its 'request' path): finished ones with their results, the others in
    'pending' with their stage. Waits up to wait_s seconds (at most 50)."""
    return api.status(request, min(max(float(wait_s), 0.0), 50.0))


@server.tool()
def get_model(study: dict, name: str = "study") -> dict:
    """The generated Modelica model (model.mo), the omc build script (build.mos) and the values applied at run
    time (params.txt) of one study, and which initial values are fixed; nothing runs."""
    return api.model(study, name)


@server.tool()
def run_benchmarks(names: list[str] | None = None, solver: str = "auto", wait_s: float = 30) -> dict:
    """Runs the benchmark studies with closed-form answers (RC, RLC step and poles, mass-spring-damper, inertia,
    DC motor, thermal RC and steady state, two-mass drive, PI loop) and reports pass or fail per check. Returns
    after at most wait_s seconds; collect the rest with bench_status(bench)."""
    return api.bench(names, solver, wait_s=min(max(float(wait_s), 0.0), 50.0))


@server.tool()
def bench_status(bench: str, wait_s: float = 45) -> dict:
    """Pass/fail of benchmarks started by run_benchmarks (its 'bench' folder): finished cases with their checks,
    the others pending."""
    return api.bench_status(bench, min(max(float(wait_s), 0.0), 50.0))


@server.tool()
def jobs(limit: int = 20, active: bool = False) -> dict:
    """Recent requests on this machine (from any conversation) with each study's state; active=True: only
    requests with studies still running."""
    return api.jobs(int(limit), bool(active))


@server.tool()
def cancel(request: str, names: list[str] | None = None) -> dict:
    """Stops a request's studies (or the named ones) with a cancel flag; returns their states."""
    return api.cancel(request, names)


@server.tool()
def get_image(path: str) -> Image:
    """A PNG written by run_studies (results' images.<name>.path) or render_images, as an image you can see and
    show: time plots of the outputs (one panel per unit) or Bode plots."""
    return Image(path=str(api.image_path(path)))


@server.tool()
def render_images(study_folder: str, images: list | bool = True) -> dict:
    """New pictures of a finished study without running again: study_folder is <request>/<study name> (the folder
    holding <name>.json). images: true (the defaults) or [{name, outputs: [...], window: [t0, t1]} | {name,
    bode: true}]. Then get_image each path."""
    return api.render_images(study_folder, images)


@server.tool()
def get_file(path: str, max_mb: float = 20) -> dict:
    """A file written by run_studies (a path from its result: model.mo, result.mat, sim.log, ...) as base64."""
    return api.read_file(path, max_mb)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, help="serve MCP over streamable HTTP on this port (default: stdio)")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if a.http is None:
        server.run()
    elif _V2:
        server.run(transport="streamable-http", host=a.host, port=a.http)
    else:
        server.settings.host, server.settings.port = a.host, a.http
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
