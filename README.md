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

**Status (2026.10.21.7, milestone 6 of 8):** format, validation, Modelica generation, reference implementation (M1); the OpenModelica driver (M2); checks and benchmarks (M3); API, worker pool, MCP server, pictures (M4); sweeps on one build (M5), all checked on mochi; SLURM execution per llm-translators' docs/EXECUTION.md (M6, tested against a fake SLURM). 225 tests pass without OpenModelica. Next: chains (M7), joining llm-translators (M8); see `planning.md`.

## Install

OpenModelica and MSL 4.0.0 on the machine that runs the studies (mochi): the official apt repository, then MSL with OpenModelica's package manager (details in llm-translators' `BACKENDS.md` once it joins the umbrella). The translator itself: `llmt install omct` in llm-translators, or for development `pip install -e .[mcp,test]`.

## Use

```bash
omct format                              # the study format (kinds, parameters, analyses)
omct validate examples/studies.json      # static checks: every error says the fix
omct model examples/studies.json rlc_square_wave    # the generated model.mo, build.mos and params.txt
omct status                              # omc, MSL, the C compiler, the build cache
omct run examples/studies.json rlc_square_wave [--solver omc|ref]   # runs now; results in ~/omct_runs/run_*/
omct bench [NAMES...] [--solver omc|ref]  # the benchmarks with closed-form answers
omct status REQUEST | jobs [--active] | cancel REQUEST [NAMES]
omct images STUDY_FOLDER                  # pictures of a finished study again
```

A run writes `<OMCT_OUT>/run_<date>_<time>_<id>/<study>/`: `model.mo`, `params.txt` (every value), `sim.log`, `result.mat` (MAT v4), `linearized_model.mo` (linearize), and `<study>.json` (the result). Compiled models live in the build cache, `<OMCT_OUT>/.cache/<hash>/`, keyed by the model's structure: a study that changes only values reuses the build (one compile of ~1-2 s on mochi; a run of a small model takes ~0.1 s).

## Running on SLURM

With `OMCT_EXECUTOR=slurm` (llm-translators' docs/EXECUTION.md), a study whose estimate is at least `OMCT_SLURM_MIN_MINUTES` (1) becomes one SLURM job; most single studies take seconds and stay in the local pool, big sweeps go to the queue. A study's `run.queue` true/false forces either way.

- **Sizing** (`executor.py`, kind *threads*): `--ntasks=1 --cpus-per-task=N`, N = the sweep points it can run at once (≤ points, `run.procs` or `OMCT_PROCS`), at least `OMCT_COMPILE_PROCS` (4) when the model is not in the build cache yet. Inside the job `SLURM_CPUS_PER_TASK` sets the sweep's threads; each simulation is single-threaded (`OMP_NUM_THREADS=1`).
- **Memory:** `OMCT_SLURM_MEM` auto = 1 GB + 256 MB per CPU (or a size, or none); `run.mem` wins.
- **Time:** the job's limit is the timeout + 10 min; with `OMCT_SLURM_TIME=estimate`, max(`OMCT_SLURM_TIME_MIN` 20, 3 × estimate + 10). Estimates: the build (0 on a cache hit) plus every point at the cache's measured seconds per interval (`runs.json`), else a cautious 2e-5 s per interval.
- **Per-study `run`:** `{timeout_s, mem, procs, queue}` within `OMCT_MAX_TIMEOUT`, `OMCT_MAX_MEM`, `OMCT_MAX_PROCS` (validation refuses more, naming the variable).
- **What a submission writes:** `<request>/<study>/job.sh` (a complete script: `sbatch job.sh` reruns it), `job.json` (id, CPUs, time, memory, estimate), `job.log`; the job runs `omct run-study REQUEST NAME`.
- **States** from the files and one `squeue` per request: queued (reason, ahead, expected start), running (stage, points done, elapsed), done, and the final ones from `sacct` or `job.log` without accounting: timed_out, out_of_memory, cancelled, failed (written as the study's result). `could not submit` when `sbatch` fails: never a silent local fallback.
- **Tools:** `jobs` (recent requests from any conversation, the queue of `omct:` jobs, the machine's CPUs), `cancel` (scancel + flag files), `run_benchmarks` returns at once and `bench_status` collects.
- **Run record** in every result: `execution: {executor: slurm | local | by hand, slurm_job, procs_requested, procs_used, mpi: null, limits: {timeout_s, mem, from_study}}`.

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
- **On every run:** balance (omc's equation and variable counts; omc itself accepts an unbalanced model), initialisation (homotopy noted), the solver (assertions, exit code, statistics), every value refused or unknown to the compiled model (OpenModelica only warns and runs on), every applied value read back from the result file, events (chattering), finite outputs, and:
  - **the energy balance:** work in at the boundaries (sources −∫v·i dt, force and torque sources ∫f ds and ∫τ dφ, temperature and heat-flow boundaries −∫Q dt, op-amp supplies) = change of stored energy (½Cv², ½Li², ½mv², ½Jω², ½c·Δs², C·T) + dissipation (LossPower, lossPower), trapezoids on the output grid, judged at max(1 %, 3 × the Richardson estimate of the integration error);
  - **the reference comparison** (linear networks): the reference implementation on the same time points (events and source steps left out), max error / range ≤ max(1e-4, 100 × tolerance); for `linearize`, eigenvalues within 1e-5 of the largest;
  - **`converge: true`:** a second run at tolerance / 10; outputs change by ≤ max(1e-6, 100 × tolerance) of their range.
  `reliable` is false when any check fails (`unreliable_because` lists them); `expect` sets `passed`, not `reliable`.

### Benchmarks (`omct bench`)

| Case | Closed form | Tolerance |
|---|---|---|
| `rc_step` | v(τ) = 1 − 1/e at τ = RC; v(5τ) | 1e-4 |
| `rlc_step` | overshoot exp(−πζ/√(1−ζ²)) (ζ = 0.5), peak at π/ω_d | 2e-3 (grid) |
| `rlc_poles_bode` | f_n = 1/(2π√LC), ζ = R/2·√(C/L), \|H\| at f_n and 1 kHz | 1e-5; 1e-3 dB |
| `msd_modes` | ω_n = √(c/m), ζ = d/(2√(cm)), static compliance 1/c | 1e-5 |
| `msd_free` | first zero of the free vibration | 1e-4 |
| `inertia_torque_step` | ω = τ(t − t0)/J, φ = τ(t − t0)²/(2J) | 1e-6 |
| `dc_motor_steady` | ω = kV/(k² + Rd), i = dω/k | 1e-5 |
| `thermal_rc` | time to 80 °C, T(t) | 1e-4, 1e-6 |
| `thermal_steady` | T∞ = T_a + Q/G (steady analysis) | 1e-7 |
| `two_mass` | ω² = c(J1 + J2)/(J1 J2) | 1e-5 |
| `pi_first_order` | PI on a first-order plant: overshoot and peak time (scipy.signal) | 2e-3 |
| `sweep_rc` | τ = RC at R = 500, 1000, 2000 Ω; one build for the three points; all reliable | 1e-4 |

A case passes when its run is ok and reliable (energy, reference) and every check holds. All pass with `--solver ref`; with OpenModelica: see `planning.md` (State).

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
| `post.py`, `energy.py`, `bench.py` | outputs, derived quantities (step, crossing, window, steady), comparisons, expect; the energy balance; the benchmarks |
| `runner.py`, `api.py`, `proc.py` | one study end to end with every check; the API (pool, request folders, status from files, cancel, jobs, benchmarks); process groups |
| `render.py`, `mcp_server.py`, `sweep.py` | time and Bode plots; the MCP server; sweeps |
| `executor.py` | the local pool or one SLURM job per study: sizing, estimates, job.sh/job.json, states, jobs, cancel, run-study |
| `cli.py` | `omct status [REQUEST] | format | validate | model | run | bench | jobs | cancel | images | run-study` |

### Sweeps

`"sweep": {"params": {"pi.k": [0.5, 1, 2], "pi.T": [0.1, 0.2]}, "mode": "grid" | "zip", "report": ["derived.y.overshoot_pct"]}` on any analysis. Each point is the study with those values (parameters or initial values, with units allowed), run in `points/pNNNN/` with every check; the first point builds the model, the others reuse it and run `run.procs` (or `OMCT_PROCS`) at a time. Values compiled into the model (see the run warnings) make variant builds. Result: `sweep.table[i] = {point, params, params_si, values, ok, reliable, why}`, `columns`, `builds`, `ok_points`, `reliable_points`, `failed`; with `images`, `images/sweep.png` (each column against the first parameter). Default report: `outputs.*.final` (`steady.*.value`; `linearization.natural_frequencies_Hz`, `damping_ratios`, `time_constants_s`) and every numeric derived quantity.

## For LLM tools: the MCP server

`omct-mcp` (stdio; `--http 8770` for HTTP). Tools: `study_format`, `validate_studies`, `run_studies`, `study_status`, `get_model`, `run_benchmarks`, `bench_status`, `jobs`, `cancel`, `get_image` (a PNG as an MCP image), `render_images`, `get_file`. `run_studies` and `study_status` return within 50 s with the rest `pending` (stage, elapsed, the time limit left); studies run in a worker pool (`OMCT_WORKERS`, 2) that lives across calls, and every state is read from the request folder, so another server process sees the results. Polling costs tokens: the instructions tell the model to report when a long run should finish and stop. `tools/mcp_smoke.py` runs the server as the Claude app would and simulates one example.

Pictures: `"images": true` in a study writes `images/outputs.png` (one panel per unit) or `images/bode.png` (linearize with `bode`); `render_images` draws other selections (`{name, outputs, window}`) from the saved `series.npz`.

## Files

`examples/studies.json` (the typical requests: DC motor with PI speed control, heated aluminium block, RLC filter Bode and square wave, two-mass drive, PI gain sweep, a chained bracket), `tests/`, `tools/m0_probe.sh` (OpenModelica facts on mochi), `tools/check_catalog_msl.py`, `planning.md` (session context).

## Versions

| Version | Changes |
|---|---|
| 2026.10.21.7 | M6: SLURM (`executor.py` from cfdt, kind threads: CPUs from sweep points and cache misses, memory, time estimates from measured runs, states, jobs, cancel, run-study, bench through SLURM), fake-SLURM tests (13), `tools/m6_slurm_test.py`; on mochi the 12-point PI sweep ran on one build in 5.3 s, every point reliable |
| 2026.10.21.6 | M5: sweeps (grid/zip on one build, threads, table, plot, progress), `get_path` with dotted keys, benchmark `sweep_rc`; on mochi the MCP smoke test passed with OpenModelica (DC motor + LimPID: reliable, 5.9 % overshoot) |
| 2026.10.21.5 | M4: worker pool and request folders (`run` with `wait`, `start`, `status`, `jobs`, `cancel`), the MCP server (12 tools, SDK 1.x and 2.x), pictures (`render.py`), CLI `status REQUEST`, `jobs`, `cancel`, `images`, `run-study`; on mochi all 11 benchmarks pass with OpenModelica |
| 2026.10.21.4 | M3: derived quantities, steady values, energy balance, reference comparison, converge, expect, 11 benchmarks (`omct bench`); the reference's square-wave edges and end-of-run steps fixed (found by the energy balance) |
| 2026.10.21.3 | First runs on mochi (RC matches its closed form through OpenModelica); a value OpenModelica cannot change at run time (a mass's `v` became an alias of a spring-damper's `v_rel`) is now compiled in: the same value reuses the build, another value builds a variant |
| 2026.10.21.2 | M2: the OpenModelica driver (build cache, runs, logs, MAT results, read-back, linearisation), `omct status` and `omct run`, the mochi probe's answers in `planning.md`; 187 tests (+2 with omc) |
| 2026.10.21.1 | M1: catalog (82 kinds), units, validation, Modelica generation with structure hash and override file, reference implementation, CLI `format`/`validate`/`model`; 173 tests (Modelica syntax checked with modparc) |
| 2026.10.20.1 | M0: research and plan (`planning.md`, `tools/m0_probe.sh`) |

Author: Nicola Ferralis <ferralis@generalflash.com>. License: MIT.
