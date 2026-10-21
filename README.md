# openmodelica-translator

System-level dynamics for LLMs, with OpenModelica and the Modelica Standard Library (MSL 4.0.0): a deterministic **translator plus checker**.

- **The LLM writes a study:** components from a curated MSL catalog with their parameters, the connections between their ports, the outputs wanted, the analysis and expected values.
- **The translator does the rest:** writes the Modelica model, checks it, compiles it once (cached by structure), runs it with every value applied from an override file (sweeps never recompile), reads the results, computes engineering quantities and checks everything.
- **Errors come back as fixes.**

It is part of [llm-translators](https://github.com/feranick/llm-translators), with build123d-translator (`b3dt`, CAD), fea-translator (`feat`, FEA), cfd-translator (`cfdt`, CFD) and lammps-translator (`lmpt`, atoms). They compute fields in one part; this one simulates the whole system over time (motors, drives, circuits, heat paths, controllers) and takes their results as lumped parameters (stiffness, mass, conductance).

| Thing | Value |
|---|---|
| Key = CLI | `omct` |
| MCP server | `omct-mcp` (port 8770 over HTTP) |
| Backend | OpenModelica 1.27.1 (`omc`), MSL 4.0.0 |
| Settings prefix | `OMCT_` |
| Output folder | `~/omct_runs` (`OMCT_OUT`) |

**Status (2026.10.21.3, milestone 2 of 8):** the study format, validation, Modelica generation and the reference implementation (M1), and the OpenModelica driver (M2: build cache, runs with override files, log and result reading, every value read back, linearisation) work; 187 tests pass without OpenModelica (a fake omc replays outputs recorded on mochi), and two more run with it. Next: derived quantities, the energy balance, the reference comparison and benchmarks (M3), the MCP server (M4), sweeps (M5), SLURM (M6), chains (M7); see `planning.md`.

## Install

OpenModelica and MSL 4.0.0 on the machine that runs the studies (mochi): the official apt repository, then MSL with OpenModelica's package manager (details in llm-translators' `BACKENDS.md` once it joins the umbrella). The translator itself: `llmt install omct` in llm-translators, or for development `pip install -e .[mcp,test]`.

## Use

```bash
omct format                              # the study format (kinds, parameters, analyses)
omct validate examples/studies.json      # static checks: every error says the fix
omct model examples/studies.json rlc_square_wave    # the generated model.mo, build.mos and params.txt
omct status                              # omc, MSL, the C compiler, the build cache
omct run examples/studies.json rlc_square_wave [--solver omc|ref]   # runs now; results in ~/omct_runs/run_*/
```

A run writes `<OMCT_OUT>/run_<date>_<time>_<id>/<study>/`: `model.mo`, `params.txt` (every value), `sim.log`, `result.mat` (MAT v4), `linearized_model.mo` (linearize), and `<study>.json` (the result). Compiled models live in the build cache, `<OMCT_OUT>/.cache/<hash>/`, keyed by the model's structure: a study that changes only values reuses the build (one compile of ~1-2 s on mochi; a run of a small model takes ~0.1 s).

## A study

```json
{
 "components": {
   "V":   {"kind": "step_voltage", "V": 12, "startTime": 0.001},
   "R":   {"kind": "resistor", "R": 10},
   "L":   {"kind": "inductor", "L": "10 mH"},
   "C":   {"kind": "capacitor", "C": "100 uF", "v0": 0},
   "gnd": {"kind": "ground"}
 },
 "connect": [["V.p", "R.p"], ["R.n", "L.p"], ["L.n", "C.p"], ["C.n", "gnd"], ["V.n", "gnd"]],
 "outputs": {"v_C": "C.v", "i": "L.i"},
 "analysis": {"kind": "transient", "stop_time": 0.05, "intervals": 2000},
 "derived": [{"kind": "step", "signal": "v_C", "t0": 0.001}],
 "expect": [{"quantity": "derived.v_C.final", "value": 12, "tol": 1e-3}]
}
```

- **Components** are catalog kinds (`omct format` lists them with ports, parameters, units, defaults, variables and initial values) or `"msl": "<class path>"` for a class in the catalog. Names are identifiers. Composite kinds are built from MSL classes: `dc_motor` (armature R + L + `RotationalEMF` k + rotor inertia J + viscous friction d) and `convection` (`Convection` with a constant `Gc` = h·A).
- **Units:** SI. A value may also be `"10 mH"`, `"80 degC"`, `"3000 rpm"`, or `{"value": 1.85, "unit": "N/mm", "from": "feat", "what": "tip stiffness"}` for chained values; every conversion is recorded. Hz and rad/s are never converted into each other. Signal values (step heights, limits) take any unit.
- **Connections:** physical ports of the same type (pin, translational flange, rotational flange, heat port); a signal output to inputs. A component with one port of the right type may be named alone.
- **Initial values** (`v0`, `i0`, `s0`, `v0`, `phi0`, `w0`, `T0`) default to 0 (T0 to 293.15 K). Only the free ones become `fixed=true`: a capacitor across a source or an inertia rigidly joined to another follows the others, and giving it a value is an error. Blocks start from `y_start` with their states at 0 (`initType = InitialState`).
- **Analyses:** `transient`, `steady`, `linearize` (A, B, C, D, eigenvalues, modes, Bode); `sweep` on any of them; `derived` quantities: `step` (rise, overshoot, settling), `crossing`, `window`, `steady`; `converge`; `expect`; `run`; `images`.
- **Escape hatch:** `"modelica": {"text": ..., "model": ...}`, checked but without catalog checks or reference.

### Interface (read by other tools; kept stable)

`outputs.<name>.{final, min, max, initial, t_min, t_max}`, `derived.<name>.*`, `steady.<name>.*`, `linearization.{eigenvalues, modes, dc_gain, bode}`, `sweep.table`.

## The checker

- **Validation** before anything runs: kinds, parameters and ranges, units, ports and connector types, a ground in every circuit, a reference or inertia in every mechanical system, a capacitor or a temperature boundary in every thermal network, every signal input driven once, outputs, analysis, sweep (only values that can be overridden), `expect`, `run` within the machine's caps; then the reference system finds singular models and initial values that are not free.
- **Reference implementation** (`ref.py`), independent of the generated Modelica: it writes the MSL equations of each kind into a descriptor system A z + B ż + G F(t) = 0, reduces its index (shuffle algorithm), and integrates the consistent states between the sources' breakpoints (Radau, rtol 1e-10). It gives trajectories, steady states, eigenvalues and frequency responses of linear networks (R, L, C, transformers, op-amps, sources, sensors, masses, springs, dampers, inertias, gears, the DC motor, thermal capacitors, conductors and convection, linear blocks and PI/PID), and it chooses which initial values are fixed in the model. Tested against closed forms: RC (1e-9), RLC step and poles (1e-8, 1e-10), mass–spring–damper (1e-10), thermal RC (1e-7), DC motor speed and poles, PI on a first-order plant, two-mass eigenvalues and frequency response, index-2 networks.
- **On every run** (M2): balance (omc's equation and variable counts; omc itself accepts an unbalanced model), initialisation (homotopy noted), the solver (assertions, exit code, statistics), every value refused or unknown to the compiled model (OpenModelica only warns and runs on), every applied value read back from the result file, events (chattering), finite outputs; `reliable` / `unreliable_because`. From M3: the energy balance and the reference comparison.

### Assumptions about OpenModelica

Listed in `planning.md` (A1–A19), each with the probe that checks it (`tools/m0_probe.sh`, run on mochi).

## How it works

| Module | Role |
|---|---|
| `catalog.py` | the curated MSL 4.0.0 kinds: classes, ports, parameters (SI), variables, initial values; checked against the MSL sources by `tools/check_catalog_msl.py` |
| `units.py` | values with units to SI, recorded |
| `network.py` | a study resolved: values, composites expanded into elements, ports to nodes, signal nets, ground/reference checks |
| `format.py` | `study_format()`, `validate()` |
| `modelica.py` | the model text (stable, sorted), its structure (values as placeholders: the build cache key), the override file, the omc script |
| `ref.py` | the reference implementation and the choice of fixed initial values |
| `omc.py` | the only module running omc: version, MSL, the build script and its printed values, errors mapped to declarations, the build cache (flock, atomic rename, pruned to 6 files) |
| `simrun.py` | runs a cached model: override file, flags, process group, timeout and cancel flag, `OMP_NUM_THREADS=1`; reads the log (refused overrides, assertions, chattering, statistics) and reads values back |
| `matfile.py`, `linear.py` | the MAT v4 result file; `linearized_model.mo` → A, B, C, D, modes, DC gain, Bode |
| `post.py`, `runner.py`, `api.py`, `proc.py` | output statistics and downsampled series; one study end to end; the API; process groups |
| `cli.py` | `omct status | format | validate | model | run` |

## Files

`examples/studies.json` (the typical requests: DC motor with PI speed control, heated aluminium block, RLC filter Bode and square wave, two-mass drive, PI gain sweep, a chained bracket), `tests/`, `tools/m0_probe.sh` (OpenModelica facts on mochi), `tools/check_catalog_msl.py`, `planning.md` (session context).

## Versions

| Version | Changes |
|---|---|
| 2026.10.21.3 | First runs on mochi (RC matches its closed form through OpenModelica); a value OpenModelica cannot change at run time (a mass's `v` became an alias of a spring-damper's `v_rel`) is now compiled in: the same value reuses the build, another value builds a variant |
| 2026.10.21.2 | M2: the OpenModelica driver (build cache, runs, logs, MAT results, read-back, linearisation), `omct status` and `omct run`, the mochi probe's answers in `planning.md`; 187 tests (+2 with omc) |
| 2026.10.21.1 | M1: catalog (82 kinds), units, validation, Modelica generation with structure hash and override file, reference implementation, CLI `format`/`validate`/`model`; 173 tests (Modelica syntax checked with modparc) |
| 2026.10.20.1 | M0: research and plan (`planning.md`, `tools/m0_probe.sh`) |

Author: Nicola Ferralis <ferralis@generalflash.com>. License: MIT.
