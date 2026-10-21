# openmodelica-translator: context for a new session

Session context for Claude (the project documentation will be README.md). Start a chat with: "Read planning.md and start."

Version: 2026.10.21.11 (all eight milestones done; omct is part of llm-translators).

## What this is

A deterministic **translator + checker** for system-level dynamics with OpenModelica and the Modelica Standard Library (MSL 4.0.0):

1. **The LLM writes a study:** components from a curated MSL catalog with parameters, the connections between their ports, the outputs wanted, the analysis and expected values.
2. **The translator does the rest:** writes the Modelica model, checks it, compiles it once (cached by structure), runs it with parameters set by override files (no recompiling), reads the results, computes engineering quantities, and checks everything.
3. **A checker confirms each run:** balance, initialisation, solver, overrides read back, energy balance, an independent reference implementation for linear networks (`ref.py`), optional solver convergence, `expect`.
4. **Errors come back as fixes.**

It complements the other translators: they compute fields in one part; this one simulates a whole system over time (motors, drives, circuits, heat paths, controllers) and takes their results as lumped parameters.

| Key | Repository | Backend |
|---|---|---|
| `b3dt` | build123d-translator | build123d (OpenCascade) |
| `feat` | fea-translator | Gmsh + CalculiX |
| `cfdt` | cfd-translator | Gmsh + OpenFOAM |
| `lmpt` | lammps-translator | LAMMPS |
| **`omct`** | **openmodelica-translator** | **OpenModelica 1.27.1 + MSL 4.0.0** |

### Names

| Thing | Value |
|---|---|
| Repository | `openmodelica-translator` (github.com/feranick/openmodelica-translator) |
| Package | `openmodelica_translator` |
| Key = CLI | `omct` |
| MCP server | `omct-mcp` |
| Settings prefix | `OMCT_` |
| HTTP port | 8770 |
| Output folder | `~/omct_runs` (`OMCT_OUT`) |
| Build cache | `~/omct_runs/.cache/<hash>/` (`OMCT_CACHE`) |

## Interconnections

- **From feat:** static stiffness k = applied force / displacement of a probe or face (feat reports `applied_force` N and `probes.<p>.mean_u.<c>` mm, so k [N/m] = F / (u · 1e-3)); modal `frequencies` (Hz) and `mass_kg`; thermal conductance G = `heat_flow.<face>` / ΔT and capacitance m · c.
- **From b3dt:** mass = volume (mm³ · 1e-9) × density. Inertia tensors: build123d has `matrix_of_inertia` on solids (unverified for b3dt's output; check in M7).
- **From cfdt:** Δp against Q (hydraulic resistance, or a table) and pump head curves: version 2 fluid networks, or `table` blocks in version 1.
- **From lmpt:** material properties (`feat_material`: E, nu, density, alpha).
- **Chain inputs are values, never paths:** `{"value": 1234, "unit": "N/mm", "from": "feat", "what": "bracket tip stiffness"}`. The conversion and provenance go into the result (`inputs`).
- **Its own interface keys** (stable, listed in the README): `outputs.<name>.{final,min,max,...}`, `derived.<name>.*`, `steady.<name>`, `linearization.{eigenvalues, modes, dc_gain, bode}`, `sweep.table`.
- Imports of other translators are optional: a warning in validation, tests skip when missing. Servers may run on different machines: tools pass specs, `get_file` returns base64.

## Conventions

- Versions `YYYY.MM.DD.x` in `__init__.py`, `pyproject.toml`, every module docstring (`Version: ...`) and the README's version table. First version 2026.10.20.1.
- Author: Nicola Ferralis <ferralis@hotmail.com>. Nicola commits and pushes from the Mac; mochi pulls through llm-translators (`llmt pin omct --commit --push`, then `llmt update` on mochi).
- **Units: SI, as Modelica** (K for temperatures, rad/s, m, kg, N·m). This differs from feat/cfdt (mm, MPa, °C), so every non-SI value is converted explicitly and recorded (see "Units" in the format).
- Dotted result paths; JSON in/out; result files written atomically.
- Python ≥ 3.10; dependencies numpy, scipy, matplotlib; extras `[mcp, test]`; entry points `omct`, `omct-mcp`.
- Answers concise, commands ready to paste with how long they take. Don't guess OpenModelica's behaviour: mark it unverified, give a mochi command, record the outcome here.
- Git: avoid `git` in the connected folders from the sandbox (it leaves `.git/index.lock`).

## Environment

- **Mac** (Apple Silicon): development, Claude desktop app, commits. No OpenModelica, and there won't be.
- **mochi** (Ubuntu, 40 hardware threads, 61.5 GB, SLURM on one node): the only machine with OpenModelica. The MCP server runs there; the Mac reaches it with `ssh nicola@mochi.mit.edu ~/Software/llm-translators/bin/mcp omct`. One shared venv `~/Software/llm-translators/.venv`.
- **Sandbox** (aarch64 Ubuntu 22.04, no sudo): no OpenModelica (build.openmodelica.org is blocked by the proxy; no root for apt anyway). PyPI and github.com work: the MSL 4.0.0 sources (tag v4.0.0) and the OpenModelica v1.27.1 sources (sparse checkout) were read here for M0. Background processes die after ~3 min. So: the backend driver is developed against stored mochi output (fixtures) and fakes; `ref.py` runs here; long things run on mochi.

## Measured facts (mochi, 2026-10-20, from Nicola)

- `omc --version`: `OpenModelica 1.27.1~2-g6db4671`, official apt repository. gcc and clang in `/usr/bin`; omc compiles headless over ssh.
- Libraries in `~/.openmodelica/libraries` (package manager, with `index.json`): `Modelica 3.2.3+maint.om`, `Modelica 4.0.0+maint.om`, `ModelicaServices 4.0.0+maint.om`, `Complex 4.0.0+maint.om`, `ModelicaReference 4.0.0+maint.om`, `ObsoleteModelica4 4.0.0+maint.om`. `/usr/lib/omlibrary` empty or absent. Plain `loadModel(Modelica)` loaded 4.0.0; pin it anyway: `loadModel(Modelica, {"4.0.0"})`.
- `simulate(ChuaCircuit, stopTime=1e4, outputFormat="csv")`: a `SimulationResult` record with `resultFile`, `simulationOptions` (numberOfIntervals 50000, tolerance 1e-6, dassl, variableFilter `.*`), `messages` (`LOG_SUCCESS | info | The initialization finished successfully without homotopy method.` …), times: frontend 0.010, backend 0.021, simcode 0.005, templates 0.007, **compile 1.48**, **simulation 1.11**, total 2.63 s. Wall 4.3 s, user CPU 15.4 s (parallel C compile). ~60 files in the working folder. CSV with every variable, 50,043 lines (events add rows).
- Rerun `./<Model> -override=stopTime=5000,L.L=20 -r=/tmp/chua_L20.csv`: 1.06 s wall, 3.8 s CPU (the simulation uses more than one core). `stopTime` in `-override` → `warning | simulation_input_xml.c: override variable name not found in model: stopTime`, run continues with the old stop time and reports success. `L.L=20` took effect (C1.v at t=398.8: −1.94377 → −2.68277).
- The executable's `-help` lists `-override`, `-overrideFile`, `-stopTime`, `-tolerance`, `-stepSize`, `-outputPath`, `-inputPath`, `-iif`, `-steadyStateTol`, `-steps`, `-initialStepSize`, `-maxStepSize`, solver and homotopy options.

## Found in the OpenModelica 1.27.1 and MSL 4.0.0 sources (sandbox, 2026-10-20)

Read from `github.com/OpenModelica/OpenModelica` tag `v1.27.1` and `github.com/modelica/ModelicaStandardLibrary` tag `v4.0.0`. The mochi probe (2026-10-21, `tools/m0_report_mochi.txt`) confirmed them; see the assumptions table.

- **Overrides** (`SimulationRuntime/c/simulation/simulation_input_xml.c`, `doOverride`/`singleOverride`): a name not in the model → `warning | simulation_input_xml.c: override variable name not found in model: X`. A variable whose `isValueChangeable` is false in `_init.xml` → `warning | It is not possible to override the following quantity: X / It seems to be structural, final, protected or evaluated or has a non-constant binding.` Both are only warnings: we treat them as errors. `-overrideFile` lines are `var=value`; it cannot be combined with `-override`. A value below 1e-6 for a Real parameter gives a "small value or zero" warning. So **`_init.xml`'s `isValueChangeable` tells before running which parameters can be overridden** (and parameters with a binding to other parameters cannot).
- **`-steadyState`** (`perform_simulation.c.inc`): stops when max_i |dx_i/dt| / nominal(x_i) < `-steadyStateTol` (default 1e-3), logs `steady state reached at time = …`, else warns `Steady state has not been reached.` The test is in 1/s, not relative to the time scale: a 2 kg aluminium block heated by 50 W starts at dT/dt ≈ 0.028 K/s, /nominal(T) = 300 → 9e-5 < 1e-3, so it would stop at once. **We detect steady state ourselves** (and may pass a tolerance scaled by the time constant). Probe A12.
- **Linearisation** (`linearization/linearize.cpp`, `Template/CodegenC.tpl`): the executable linearises with `-l=<time>` and writes `linearized_model.<ext>` into `-outputPath` (the file name no longer carries the model name). Modelica format: `parameter Integer n, m, p`, `x0`, `u0`, `A`, `B`, `C`, `D`, then `Real 'x_<state>' = x[i]`, `'u_<input>'`, `'y_<output>'`. Inputs and outputs are the model's top-level `input`/`output` variables. **In 1.27.1 `--linearizationDumpLanguage` defaults to `none`** (Flags.mo), and the `linearize()` API sets it to `modelica` before building: so we always build with `--linearizationDumpLanguage=modelica`, and any cached build can linearise without a rebuild. Size limit `--maxSizeLinearization` 1000 (states + inputs + outputs + algebraic variables); raise it for larger models. A symbolic Jacobian is used if generated, else numerical differences. Probe A13/A14.
- **checkModel** (`CevalScriptBackend.mo`): `Check of <C> completed successfully.\nClass <C> has <N> equation(s) and <M> variable(s).\n<K> of these are trivial equation(s).` Under/over-determined: `Too few equations, under-determined system. The model has %s equation(s) and %s variable(s).` (Error.mo). `getMessagesStringInternal()` returns machine-readable error records (file, line, message, kind, level, id): better than parsing `getErrorString()`.
- **buildModel** (ModelicaBuiltin.mo): `buildModel(className, startTime, stopTime, numberOfIntervals, tolerance, method, fileNamePrefix, options, outputFormat="mat", variableFilter=".*", cflags, simflags)` → `{executable, init.xml}`. `-n=N` / `--numProcs` (0 = auto) sets the parallel C compile.
- **The executable's log** (`solver_main.c`): `The initialization finished successfully without homotopy method.` or `… with N [local ]homotopy steps.`; on failure `Error in initialization. Storing results and exiting.` and `simulation terminated by an assertion at initialization`; `Chattering detected around time …` (events.c); with `-lv=LOG_STATS` a `### STATISTICS ###` block with timers, `N state events`, `N time events`, `N steps taken`, error/convergence test failures. Runtime flags also exist for `-outputFormat`, `-variableFilter`, `-alarm=<s>`, `-emit_protected`, `-noEventEmit`, `-jacobianThreads`.
- **MAT v4 results** (UsersGuide `technical_details.rst`, `simulation_result_mat4.cpp`): matrices `Aclass` (`binTrans` = stored transposed), `name`, `description`, `dataInfo` (n × 4: matrix 0/1/2, signed 1-based column, negative = negated alias), `data_1` (parameters: start and stop values), `data_2` (time series, time first). The variable filter applies to parameters too, so the filter must name every parameter we override (to read it back). A pure-Python reader of this layout is in `tools/m0_probe.sh` (tested on a synthetic binTrans file).
- **MSL 4.0.0 names** checked against the sources: all classes of the catalog below exist; `Basic.EMF` is now `Basic.RotationalEMF` (k, useSupport); `Basic.HeatingResistor` is gone (Resistor has `useHeatPort`); there is no `SquareVoltage` (PulseVoltage with width 50 %); `Capacitor` has no `v0` (we write `v(start=…, fixed=true)`); `Thermal.HeatTransfer.Components.Convection` takes `Gc` as a **signal input**, not a parameter; `Blocks.Continuous.PI` uses `T` (not `Ti`); LimPID's `controllerType`, `withFeedForward`, `initType` and the blocks' `initType` are `Evaluate=true` (structural).

## Assumptions about OpenModelica (each with the probe that catches it)

`tools/m0_probe.sh` answered them on mochi on 2026-10-21 (`tools/m0_report_mochi.txt`). Status: S = read in the source, M = measured on mochi, ? = unverified.

| # | Assumption | Status | Caught by |
|---|---|---|---|
| A1 | `loadModel(Modelica, {"4.0.0"})` loads `4.0.0+maint.om` | **M ✓** | returns true; `getVersion(Modelica)` = "4.0.0" |
| A2 | `getLoadedLibraries()` gives the MSL path | **M ✓** | `{{"Modelica", "/home/nicola/.openmodelica/libraries/Modelica 4.0.0+maint.om"}, {"Complex", ...}, {"ModelicaServices", ...}}` |
| A3 | checkModel's message format | **M ✓**, but: **checkModel does not fail on an unbalanced model** (`Under`: "completed successfully ... 1 equation(s) and 2 variable(s)"): we compare the counts ourselves | probe A3; fixtures |
| A4 | Error formats | **M ✓** `getErrorString()`: `[<file>:<l0>:<c0>-<l1>:<c1>:writable] Error: Class ... not found in scope BadClass.`; an unknown modifier makes checkModel return "" with `Modified element Rx not found in class Resistor.` (id 214). `getMessagesStringInternal()` consumes the messages (getErrorString is then ""). We use getErrorString and map the line to the declaration | fixtures |
| A5 | A model without ground passes checkModel and fails at build | **M ✓** checkModel passes (21 = 21); buildModel returns `{"", ""}` with `Internal error Circular Equalities Detected ... IndexReduction.pantelidesIndexReduction failed!`: our validation must catch it (it does) | test_format |
| A6 | `_init.xml` has `isValueChangeable` and the variable filter | partly: the filter is stored (`variableFilter = "..."`); the runtime refuses what isValueChangeable forbids (A8); the attribute itself was not printed by the probe: check when reading `_init.xml` in M2 | M2 test on mochi |
| A7 | Log lines `LOG_x | level | text` (continuations `|  | |  | ...`); exit 0 on success | **M ✓** | fixtures |
| A8 | Override semantics | **M ✓** primary `Rv=20` applied, and **bound parameters are recomputed** (Rdep 20→40, R2.R 24000→44000); `Rdep`, `Rev` (Evaluate), `Rfin` (final) refused with "It is not possible to override ..."; `nosuch` → "override variable name not found"; state starts with fixed=true (`C.v=1`, `L.i=0.5`) applied; exit 0 in every case (so we must read the log) | simrun fixtures |
| A9 | `-stopTime`, `-tolerance`, `-stepSize` work without rebuilding | **M ✓** (last time 0.02) | |
| A10 | A build runs from another folder with only exe, `_init.xml`, `_info.json`, `_JacA.bin`, `.libs`, `.log` via `-inputPath`/`-outputPath` | **M ✓** | |
| A11 | `-variableFilter` at run time | **M ✓** it narrows the time series (C.v only); **parameters are always written** (all 20 in `data_1`), so read-back works whatever the filter | |
| A12 | `-steadyState` stops too early for slow systems | **M ✓** default tol stopped the thermal block at t = 9980 s, T = 373.49 K, 2.9 K short of the final 376.38 K (tau 3000 s); 1e-6 never stopped. We detect steady state ourselves | |
| A13 | `--linearizationDumpLanguage=modelica` at build, `-l=t` on the same executable | **M ✓** `linearized_model.mo` in `-outputPath`; log `Linear model is created at <path>` | |
| A14 | `linearized_model.mo` format | **M ✓** 16 digits; numerical Jacobian: entries off by ~1e-8 relative (A[1,2] = 10000.00000000412, B[2] = 99.9999988); names `'x_C.v'`, `'u_u'`, `'y_y'` (u_/y_ + the top-level name). Compare eigenvalues at 1e-6 relative | ref |
| A15 | Failures and exit codes | **M ✓** assert: `LOG_ASSERT | info | [file:l:c-l:c:writable] The following assertion has been violated at time 0.010000 ...`, `LOG_ASSERT | error | No event found, but assert was triggered. Throwing now!`, exit 255. Init failure: `LOG_ASSERT | debug | ..._06inz.c:34: Invalid root: (-1)^(0.5)`, `simulation terminated by an assertion at initialization`, exit 255. **Chattering: `LOG_STDOUT | info | Chattering detected around time 1.0000000001..1.0000000199 (100 state events ...)` and the run went on for 120 s (killed by timeout, exit 124)**: always pass `-alarm` and our own timeout | simrun |
| A16 | Threads | **M ✓ OpenMP threads make a simulation slower**: Chua 0.22 s wall / 2.9 s CPU by default, 0.09-0.13 s / 0.06-0.10 s with `OMP_NUM_THREADS=1`; four at once 0.09-0.14 s each. So `OMCT_OMP_THREADS=1` always. Compile (omc + MSL load + C, Chua): `-n=1` 6.4 s, `-n=4` 3.2 s, `-n=0` (40) 2.4 s: `OMCT_COMPILE_PROCS` 4 | |
| A17 | MAT layout | **M ✓** binTrans; `data_1` (n_par × 2: start, stop), `data_2` (n_var × n_times; 2004 columns for 2000 intervals: events add columns); aliases share columns | matfile tests |
| A18 | MSL sign conventions (flow into the port positive; LossPower ≥ 0) | ? | benchmarks + energy balance (M3) |
| A19 | dassl at tolerance 1e-6 reproduces closed forms to ~1e-4 relative on the output grid | ? | benchmarks; `converge` (M3) |

Other facts from the probe (2026-10-21): mochi kernel 7.0.0, Ubuntu "resolute", gcc 15.2.0; apt source `deb [arch=amd64 signed-by=/usr/share/keyrings/openmodelica-keyring.gpg] https://build.openmodelica.org/apt resolute stable` (for BACKENDS.md). One build of the probe RLC: about 1 s inside a 6-build omc script (6.25 s wall, 27 s CPU). A run of the RLC (2000 intervals): 0.08 s wall. A build folder keeps ~60 files, 476 kB; the cache keeps 6.

## Scope, version 1 (proposal)

**Domains** on a curated catalog of MSL 4.0.0 classes:

| Domain | Package |
|---|---|
| Analog circuits | `Modelica.Electrical.Analog` (Basic, Sources, Sensors; Ideal: diode and op-amp only) |
| DC motor | `dc_motor`: a small model built from MSL R, L, RotationalEMF, Inertia, Damper (see "DC motor") |
| Translational mechanics | `Modelica.Mechanics.Translational` |
| Rotational mechanics | `Modelica.Mechanics.Rotational` |
| Heat transfer | `Modelica.Thermal.HeatTransfer` |
| Signals and control | `Modelica.Blocks` (Sources, Continuous incl. PI/PID/LimPID, Math, Nonlinear limiters, CombiTimeTable) |

**Analyses:** `transient`, `steady`, `linearize`; `sweep` applies to any of them (a grid or list of parameter sets on one compiled model).

**Out of scope for v1:** Modelica.Fluid and Media (v2 could start with `Thermal.FluidHeatFlow` for coolant loops), MultiBody, FMU export, Modelica beyond the escape hatch, switching power electronics (ideal switches with Boolean control), electro-thermal coupling beyond `heat_port` on resistors and dampers.

**Typical requests:**

1. "Step response of this DC motor with a PI speed controller: rise time, overshoot, settling time, peak current." → `dc_motor`, `inertia` load, `pi` or `lim_pid`, `speed_sensor`, `feedback`, `step`; `derived: step` on speed, `outputs.i.max`.
2. "How long does this 2 kg aluminium block take to reach 80 °C with 50 W and h = 10 W/m²K? And the steady temperature?" → `heat_capacitor {m: 2, cp: 900}`, `fixed_heat_flow`, `convection {h, A}`, `fixed_temperature`; `derived: crossing "80 degC"`; `steady`.
3. "RLC filter: resonance, damping, and the response to a 1 kHz square wave." → `linearize` (modes, Bode) and a transient with `pulse_voltage` width 50 %, `derived: window` (amplitude, mean).
4. "Two-mass drive with a compliant shaft: natural frequencies, then a torque step." → `linearize` then `transient`.
5. "Sweep the PID gains and give me the overshoot table." → `sweep` → `sweep.table`.
6. Chain: "The bracket's stiffness from feat and its mass from b3dt: is the first mode above 50 Hz? Simulate a shock." → `mass` + `spring_damper` from chain values; `linearize`; transient with `force_step`/`pulse`.
7. Chain (v2, or tables in v1): pump curve and pipe losses from cfdt with a motor and controller here.

## The study format (proposal)

```json
{
 "components": {
   "V":   {"kind": "step_voltage", "V": 12, "startTime": 0.001},
   "R":   {"kind": "resistor", "R": 10},
   "L":   {"kind": "inductor", "L": "10 mH", "i0": 0},
   "C":   {"kind": "capacitor", "C": "100 uF", "v0": 0},
   "gnd": {"kind": "ground"}
 },
 "connect": [["V.p", "R.p"], ["R.n", "L.p"], ["L.n", "C.p"], ["C.n", "gnd"], ["V.n", "gnd"]],
 "outputs": {"v_C": "C.v", "i": "L.i"},
 "analysis": {"kind": "transient", "stop_time": 0.05, "intervals": 2000, "tolerance": 1e-6},
 "derived": [{"kind": "step", "signal": "v_C"}],
 "expect": [{"quantity": "derived.v_C.final", "value": 12, "tol": 1e-3}],
 "run": {"timeout_s": 600, "queue": false},
 "images": true
}
```

| Key | Meaning |
|---|---|
| `components` | `{name: {kind, <parameters>}}`. Names are Modelica identifiers (letters, digits, `_`; not Modelica keywords). `kind` is a catalog kind; `"msl": "<full class path>"` is accepted instead when that class is in the catalog. |
| parameters | Catalog names, which are MSL's names where MSL has the parameter (`R`, `L`, `J`, `c`, `d`, `startTime`, `offset`, `f`…), plus convenience ones per kind: initial values (capacitor `v0`, inductor `i0`, mass `s0`/`v0`, inertia `phi0`/`w0`, heat capacitor `T0`, blocks `y_start`) and alternatives that the translator converts (`heat_capacitor`: `C` or `m` + `cp`; `thermal_conductor`: `G` or `k`, `A`, `L`; `convection`: `Gc` or `h`, `A`). Omitted parameters take the catalog default (MSL's, or ours where MSL has none). |
| units | A number is SI. A string `"10 mH"`, `"80 degC"`, `"3000 rpm"`, `"50 mm"` is converted with a small unit table (prefixes p n u m k M G; degC, degF, rpm, mm, g, min, h, %…). An object `{"value": 1234, "unit": "N/mm", "from": "feat", "what": "..."}` is the same plus provenance (chains). Every conversion is listed in the result's `inputs`. |
| `connect` | Pairs `"comp.port"`; a component with a single port can be named alone (`"gnd"`). Physical ports connect to the same connector type (pin–pin, translational flange–flange, rotational flange–flange, heat port–heat port); signals connect output → input(s). |
| `outputs` | `{name: "comp.variable"}` or `"comp.port.variable"`, from each kind's listed variables (resistor `v, i, LossPower`; inertia `phi, w, a`; heat_capacitor `T, der_T`; port `p.v`, `flange_a.tau`, `port.Q_flow`…). |
| `analysis` | `transient`: `stop_time`, `intervals` (500), `tolerance` (1e-6), `method` (`dassl` \| `ida`), `start_time` (0). `steady`: `max_time`, `rel_tol` (1e-6), optional `time_scale` hint. `linearize`: `at` (time, 0 = after initialisation), `inputs` `{name: "comp.input"}` (free signal inputs such as `Vs.v`, `tau.tau`; they become top-level inputs), outputs = the study's `outputs`, `bode` `{f_min, f_max, points}` or a list of Hz. |
| `derived` | `step` (signal; optional `initial`, `final`, `band` 2 %, `rise` 10–90 %): rise time, overshoot %, peak, peak time, settling time, final, steady-state error vs `target`. `crossing` (signal, `level`, `direction`): first time it crosses. `window` (signal, `t0`, `t1`): mean, rms, min, max, peak-to-peak. `steady` (signal): value and time to within `band` of it. |
| `sweep` | `{"params": {"PI.k": [1, 2, 4], "PI.T": [0.1, 0.2]}, "mode": "grid" \| "zip", "report": ["derived.w.overshoot_pct", ...]}` (default report: every `derived` and `outputs.*.final`). Only overridable parameters (validated against the catalog; read back from each run). |
| `converge` | `true`: rerun at tolerance ÷ 10 and compare the outputs. |
| `expect` | `[{quantity, value, tol}]` on dotted result paths. |
| `run` | `{timeout_s, mem, procs, queue}` within `OMCT_MAX_*` (EXECUTION.md). `procs` = parallel sweep points. |
| `images` | `true`: time plots of the outputs (one panel per unit), Bode plots for `linearize`, sweep plots. |
| `modelica` | Escape hatch: `{"text": "...", "model": "Name"}`; still checked (balance, overrides, outputs, energy if it can) but no catalog checks and no reference. |

**Validation before anything runs** (each error says the fix): known kinds and parameters; values in range (R ≥ 0, C > 0, m > 0, …) with units checked; known ports, each connected at most as its type allows; compatible connectors; every signal input driven exactly once (or declared a linearisation input); every electrical sub-network has a ground; every mechanical sub-network has a `fixed` or a prescribed motion where needed (or is free and says so: a warning, its position drifts); every thermal network has a temperature boundary or a capacitor; no unconnected required ports; outputs exist; sweep parameters exist and are overridable; `run` within the caps; analysis keys and ranges.

### The catalog (proposal)

Ports are named as in MSL. `*` = structural (changes the build).

| Kind | MSL class | Ports | Parameters (SI) |
|---|---|---|---|
| `resistor` | Electrical.Analog.Basic.Resistor | p, n (+ heat_port with `heat_port: true`*) | R, T_ref, alpha |
| `capacitor` | Basic.Capacitor | p, n | C, v0 |
| `inductor` | Basic.Inductor | p, n | L, i0 |
| `ground` | Basic.Ground | p | |
| `emf` | Basic.RotationalEMF | p, n, flange | k |
| `transformer` | Basic.Transformer | p1, n1, p2, n2 | L1, L2, M |
| `constant_voltage` / `step_voltage` / `ramp_voltage` / `sine_voltage` / `pulse_voltage` | Analog.Sources.* | p, n | V, offset, startTime; ramp: duration; sine: f, phase; pulse: width (%), period, nperiod |
| `signal_voltage` | Sources.SignalVoltage | p, n, v (in) | |
| `constant_current` / `step_current` / `sine_current` / `signal_current` | Sources.* | p, n (, i in) | I, offset, startTime, f, phase |
| `voltage_sensor` / `current_sensor` / `potential_sensor` | Analog.Sensors.* | p, n / p, n / p; out v or i or phi | |
| `ideal_diode` | Analog.Ideal.IdealDiode | p, n | Ron, Goff, Vknee |
| `opamp` | Analog.Ideal.IdealOpAmp3Pin | in_p, in_n, out | |
| `dc_motor` | our composed model (below) | p, n, flange | R, L, k, J, d, i0, w0 |
| `mass` | Mechanics.Translational.Components.Mass | flange_a, flange_b | m, s0, v0 |
| `spring` / `damper` / `spring_damper` | Translational.Components.* | flange_a, flange_b | c, s_rel0 / d / c, d, s_rel0 |
| `fixed` | Translational.Components.Fixed | flange | s0 |
| `force` / `force_step` / `constant_force` | Translational.Sources.* | flange (, f in) | stepForce, offsetForce, startTime / f_constant |
| `position_sensor` / `speed_sensor` / `acc_sensor` / `force_sensor` | Translational.Sensors.* | flange (or a, b); out | |
| `inertia` | Mechanics.Rotational.Components.Inertia | flange_a, flange_b | J, phi0, w0 |
| `rot_spring` / `rot_damper` / `rot_spring_damper` | Rotational.Components.* | flange_a, flange_b | c, phi_rel0 / d / c, d |
| `rot_fixed` | Rotational.Components.Fixed | flange | phi0 |
| `ideal_gear` | Rotational.Components.IdealGear | flange_a, flange_b | ratio |
| `torque` / `torque_step` / `constant_torque` | Rotational.Sources.* | flange (, tau in) | stepTorque, offsetTorque, startTime / tau_constant |
| `speed_load` | Rotational.Sources.QuadraticSpeedDependentTorque / LinearSpeedDependentTorque | flange | tau_nominal, w_nominal, law: linear\|quadratic* |
| `angle_sensor` / `rot_speed_sensor` / `torque_sensor` / `rot_acc_sensor` | Rotational.Sensors.* | flange (or a, b); out | |
| `heat_capacitor` | Thermal.HeatTransfer.Components.HeatCapacitor | port | C or m+cp, T0 |
| `thermal_conductor` / `thermal_resistor` | Components.ThermalConductor / ThermalResistor | port_a, port_b | G or k+A+L / R |
| `convection` | Components.Convection + Blocks.Sources.Constant (Gc) | solid, fluid | Gc or h+A |
| `radiation` | Components.BodyRadiation | port_a, port_b | Gr (= ε·A, m²) |
| `fixed_temperature` / `prescribed_temperature` | HeatTransfer.Sources.* | port (, T in) | T |
| `fixed_heat_flow` / `prescribed_heat_flow` | HeatTransfer.Sources.* | port (, Q_flow in) | Q_flow |
| `temperature_sensor` / `heat_flow_sensor` | HeatTransfer.Sensors.* | port / port_a, port_b; out | |
| `constant` / `step` / `ramp` / `sine` / `pulse` | Blocks.Sources.* | y | k / height, offset, startTime / duration / amplitude, f, phase / width, period |
| `table` | Blocks.Sources.CombiTimeTable | y[1] | table [[t, y], …]* (size structural; values overridable), smoothness* |
| `gain` / `add` / `feedback` / `product` / `division` | Blocks.Math.* | u (u1, u2), y | k / k1, k2 |
| `integrator` / `derivative` / `first_order` / `second_order` | Blocks.Continuous.* | u, y | k, y_start / k, T / k, T, y_start / k, w, D, y_start |
| `transfer_function` | Blocks.Continuous.TransferFunction | u, y | b*, a* (lengths structural) |
| `pi` / `pid` | Blocks.Continuous.PI / PID | u, y | k, T / k, Ti, Td, Nd |
| `lim_pid` | Blocks.Continuous.LimPID | u_s, u_m, y | controllerType* (P, PI, PD, PID), k, Ti, Td, yMax, yMin, wp, wd, Ni, Nd |
| `limiter` / `dead_zone` / `slew_rate_limiter` | Blocks.Nonlinear.* | u, y | uMax, uMin / Rising, Falling, Td |

Blocks with states get `initType = InitialState` (or `InitialOutput` when `y_start` is given) so the initial state is never left to the tool's guess; the `ref.py` uses the same. Sensors are ideal.

**DC motor.** Proposal: `dc_motor` is a small composed model written into the generated file: armature `Resistor(R)` + `Inductor(L)` + `RotationalEMF(k)` + rotor `Inertia(J)` + optional viscous `Damper(d)` to a fixed housing. Why not `Electrical.Machines.BasicMachines.DCMachines.DC_PermanentMagnet`: its parameters are nominal operating points (VaNominal, IaNominal, wNominal) from which the torque constant, temperature-corrected resistance and losses (friction, core, stray, brush) are derived with bindings, so they are not overridable without rebuilding and the reference implementation would have to copy every loss model. The composed motor takes datasheet values (R, L, k_t = k_e, J), is linear (so `ref.py` checks it exactly), exposes `i`, `w`, `tau`, `P_loss`, and costs nothing to sweep. `DC_PermanentMagnet` can come later as `dc_machine` for loss/thermal studies.

### Results (one study)

| Key | Content |
|---|---|
| `ok`, `reliable`, `unreliable_because`, `warnings`, `errors` | as in the other translators |
| `model` | `{equations, variables, trivial, states, hash, cache_hit, build_s, omc, msl}` |
| `inputs` | every converted value: `{"L.L": {"given": "10 mH", "si": 0.01, "unit": "H"}}`, with chain provenance |
| `outputs` | per output: `unit`, `initial`, `final`, `min`, `max`, `t_min`, `t_max`, and `series` `{t, y}` downsampled (≤ 400 points, events kept) |
| `derived` | per signal, the requested metrics (`rise_time_s`, `overshoot_pct`, `peak`, `peak_time_s`, `settling_time_s`, `final`, `crossing_time_s`, `mean`, `rms`…) |
| `steady` | per output: value, `t_steady_s`, `reached`, remaining rate |
| `linearization` | `states`, `inputs`, `outputs`, `A`, `B`, `C`, `D`, `eigenvalues` (re, im), `modes` (`f_n_Hz`, `zeta`, `tau_s`, dominant states), `dc_gain`, `bode` (per input→output: f, gain_dB, phase_deg) |
| `sweep` | `table` (one row per point: parameters, reported values, `reliable`), `columns` |
| `checks` | `balance`, `initialization` (homotopy?), `solver` (steps, state/time events, test failures), `overrides` (requested vs read back), `finite`, `energy`, `reference`, `converge` |
| `expect`, `files` (model.mo, run.mos, override file, result .mat, logs, images), `seconds`, `execution` | as in cfdt |

## Architecture

```
openmodelica_translator/
  catalog.py     [M1] the catalog: 82 public kinds (+ convection_core), elementary (one MSL class) or
                 composite (dc_motor, convection: parts that are elementary kinds); ports, parameters (SI unit,
                 default, range, structural), variables, initial values (Modelica variable, default, priority)
  units.py       [M1] "10 mH" / {"value","unit","from"} -> SI, recorded; '*' = signal values, any unit
  network.py     [M1] a study resolved: values, composites flattened (X__part), ports -> nodes (union-find),
                 signal nets, the ground / reference / driven-input / open-pin checks
  format.py      [M1] study_format() (~14 kB), validate(): everything static, then the reference system
                 (singular models, initial values that are not free); run caps (OMCT_MAX_*)
  modelica.py    [M1] study -> stable Modelica text (sorted declarations and connects, experiment annotation,
                 top-level omct_u_* / omct_y_* for linearize); structure text with '?' for every overridable value
                 (the cache key); the override file; the build .mos; the variable filter
  cli.py         [M1] omct format | validate | model (more in M4)
  omc.py         the only module running omc: find it, version, MSL path; .mos scripts; checkModel and
                 buildModel; message records; the build cache (hash, fcntl lock, prune .c/.o)
  simrun.py      runs a cached executable: override file, flags, process group, timeout/cancel flag;
                 log parsing (LOG_SUCCESS, homotopy, asserts, chattering, statistics)
  matfile.py     MAT v4 reader (binTrans/binNormal, aliases, data_1 parameters)
  post.py        outputs, derived metrics, steady detection, energy balance, override read-back
  linear.py      linearized_model.mo parser; eigenvalues, modes, Bode, DC gain
  ref.py         [M1] independent reference (below); also chooses the fixed initial values
  render.py      time plots, Bode plots, sweep plots (matplotlib/Agg)
  runner.py      run_study(name, study, folder, ...) end to end
  sweep.py       points from grid/zip; local process pool inside one study
  bench.py       benchmarks with closed forms
  executor.py    from cfdt: local pool or one SLURM job per study (kind threads)
  api.py, cli.py, mcp_server.py   as cfdt
tests/  [M1] studies.py, test_units.py, test_catalog.py, test_format.py, test_modelica.py, test_ref.py;
        later test_matfile.py, test_linear.py, test_slurm.py, fake_slurm.py,
        fixtures/ (mochi outputs: logs, init.xml, .mat, linearized_model.mo, message records)
tools/  m0_probe.sh (M0 probe), check_catalog_msl.py (catalog vs the MSL 4.0.0 sources), measure scripts
```

### Translation and running (deterministic)

1. **Validate** (static).
2. **Generate** `model.mo`: `model omct_model` with one declaration per component (sorted by name), modifiers in SI with full precision (`repr`), `connect` equations sorted, top-level `RealInput`/`RealOutput` for linearisation, `annotation(experiment(StartTime, StopTime, Interval, Tolerance))`. The structural hash uses the same text with every overridable parameter value replaced by a placeholder, plus the omc version, MSL version and build flags. A value change therefore never recompiles; every study's values are applied through the override file, even right after a build, and read back.
3. **Build** (cache miss only), in `OMCT_CACHE/<hash>/` under an `fcntl.flock`: a `.mos` with `setCommandLineOptions("--linearizationDumpLanguage=modelica")`, `loadModel(Modelica, {"4.0.0"})`, `loadFile("model.mo")`, `checkModel(omct_model)`, `buildModel(omct_model, fileNamePrefix="omct_model", outputFormat="mat", variableFilter=".*")` (the run narrows it with `-variableFilter` if A11 holds, so the filter never changes the build), with `getErrorString()` after each call (`getMessagesStringInternal()` once the probe shows its format). `omc -n=OMCT_COMPILE_PROCS`. Keep the executable, `_init.xml`, `_info.json`, `*.bin`, `.libs`; delete `.c`/`.o`/`.h`/`.makefile`. Read `isValueChangeable` from `_init.xml` and refuse a sweep or value of a parameter that cannot be overridden (rebuild with the value in the text only if the catalog marks it structural).
4. **Run** from the study folder: `<cache>/omct_model -inputPath=<cache> -outputPath=<study> -overrideFile=params.txt -r=result.mat -stopTime -tolerance -stepSize -s=<method> -lv=LOG_STATS -alarm=<timeout>`, in its own process group, `OMP_NUM_THREADS=OMCT_OMP_THREADS`. Any override warning is an error. Linearisation: the same executable with `-l=<at>`.
5. **Read** the MAT file: series, parameters read back (`data_1`) against what was asked (relative 1e-12), outputs finite.
6. **Post-process**, check, render, write `<name>.json` atomically.

## The checker

**Every run:**

- Balance: equations == variables from checkModel (an unbalanced model never builds; the message names the counts).
- Initialisation succeeded; homotopy used → warning.
- Solver finished (`LOG_SUCCESS … simulation finished`), no assertion, exit code 0.
- Overrides: no "not found"/"not possible" warning, and each value read back from `data_1`.
- Events: state events > 1000, or `Chattering detected` → warning (and the time).
- Outputs finite.
- **Energy balance** where the components expose power: ∫ boundary power dt (sources, prescribed temperatures/heat flows/motion; computed from port effort × flow) = Δ stored energy (½Li², ½Cv², ½mv², ½Jω², ½c·s_rel², C·T) + ∫ dissipation (resistor `LossPower`, damper `lossPower`, unless routed to a heat port). Integrated by trapezoid on the output grid (OpenModelica writes both sides of events); error relative to the largest term; judged at max(1e-3, error of the integration estimated by Simpson vs trapezoid). Reported, and `reliable: false` above 1 %.

**Reference implementation (`ref.py`), like lmpt's `refcalc.py`:** builds a descriptor system E·ẋ = A·x + B·u(t), y = C·x + D·u from the study, not from the Modelica text: modified nodal analysis on each domain with the effort/flow analogy (pins v/i, flanges s/f and φ/τ, heat ports T/Q), capacitive elements as states (C, L, m, J, springs, heat capacitors), linear blocks as state-space (gain, add, feedback, integrator, first/second order, transfer function, PI, PID with its Nd filter), sources as functions of time (constant, step, ramp, sine, pulse; piecewise integration across breakpoints). Covered: linear kinds only; studies with `limiter`, `lim_pid`, `ideal_diode`, `radiation`, `table`, `dead_zone` are reported "not covered". It gives trajectories (`scipy.integrate.solve_ivp`, Radau, rtol 1e-10, or the matrix exponential for piecewise-constant inputs), the steady state (solve A·x = −B·u), and generalised eigenvalues. Compared with OpenModelica on the output grid: max |y_om − y_ref| / (range(y_ref) + atol) ≤ 1e-4 (OpenModelica at 1e-6), eigenvalues to 1e-6 relative. A mismatch makes the result unreliable. It runs in the sandbox, so it also tests the translation without omc (`OMCT_SOLVER=ref` runs studies with it alone, like cfdt's 2D solver).

**`converge: true`:** rerun at tolerance ÷ 10; outputs differing by more than 10 × tolerance × range → unreliable.

**Benchmarks (`bench.py`)**, each with tolerance and why:

| Case | Closed form | Tolerance |
|---|---|---|
| `rc_step` | v(t) = V(1 − e^(−t/RC)); v(τ) = 63.21 % | 1e-4 rel (dassl 1e-6) |
| `rlc_underdamped` | ω_d, overshoot = exp(−πζ/√(1−ζ²)); `linearize` poles | overshoot 0.5 % (grid), poles 1e-6 |
| `msd_free` | ω_n, ζ, logarithmic decrement from peaks | 0.5 % |
| `inertia_torque_step` | ω = τ t / J | 1e-6 rel |
| `dc_motor` | ω_ss = kV/(k² + R d), electrical and mechanical time constants | 1e-4 |
| `thermal_rc` | T∞ = T_amb + P/(hA), τ = C/(hA); `crossing` time | 1e-4; crossing 0.1 % |
| `two_mass` | linearised eigenvalues vs the 2-DOF closed form | 1e-6 |
| `pi_first_order` | closed-loop step response of PI on K/(τs+1) (2nd order, closed form) | 1e-4 |
| `chua` | regression against a stored mochi run (chaotic: compare to t = 100 s only) | 1e-3 |
| `sweep_rc` | a 3-point R sweep: τ per point; one compile | exact count of builds = 1 |
| `steady_thermal` | steady T, detected time | 1e-5 |
| `chain_feat_modal` | feat static cantilever (k = F/u) and its mass → ω ≈ √(k / (33/140 · m)) (Rayleigh; 1.46 % above the exact first bending frequency); compare with feat's modal `frequencies.0` | ratio 1.0146 ± 1 %; skip without feat |

## Size, cost and SLURM

- **Cost.** A compile is ~1.5 s wall (15 s CPU on many cores) for Chua; simulations take seconds. Studies are light; sweeps and `converge` multiply runs, not compiles.
- **Estimates before submitting:** compile = 0 on a cache hit, else a + b · equations (a, b measured on mochi; equations ≈ catalog sum before checkModel); run = the cache entry's last measured seconds per interval × intervals (first run: a conservative guess from intervals × equations); a sweep = points × run / procs. Memory small: ~200 MB + the result file (intervals × variables × 8 bytes).
- **Executor kind `threads`:** one job per study with `--cpus-per-task` = sweep processes (≤ points, ≤ `OMCT_PROCS`), and at least `OMCT_COMPILE_PROCS` when the cache misses. A sweep's points run in a local process pool inside the job (one study = one job). `OMCT_SLURM_MIN_MINUTES` (1) keeps single studies local.
- Everything else as cfdt: states from files and the queue, `jobs`, `cancel` (flag file + scancel), process-group kills, `execution` with `limits`, per-study `run` with `OMCT_MAX_*`, `run_benchmarks` returns within ~30 s and `bench_status` collects.

## Settings (all in `translators.json` `env` when it joins the umbrella)

| Setting | Meaning (default) |
|---|---|
| `OMCT_OMC` | path to omc (`omc` on PATH) |
| `OMCT_MSL_VERSION` | 4.0.0 |
| `OMCT_OUT` | output folder (`~/omct_runs`) |
| `OMCT_CACHE` | build cache (`$OMCT_OUT/.cache`) |
| `OMCT_SOLVER` | `auto` \| `omc` \| `ref` (auto: omc when found) |
| `OMCT_WORKERS` | local pool size (2) |
| `OMCT_TIMEOUT` | seconds per study (600) |
| `OMCT_PROCS` | parallel sweep points per study (1) |
| `OMCT_COMPILE_PROCS` | parallel C compile, omc `-n` (4) |
| `OMCT_OMP_THREADS` | threads inside one simulation (1) |
| `OMCT_EXECUTOR`, `OMCT_SLURM_MIN_MINUTES` (1), `OMCT_SLURM_ARGS`, `OMCT_SLURM_MEM` (auto: 1 GB + 256 MB per CPU), `OMCT_SLURM_TIME` (timeout), `OMCT_SLURM_TIME_MIN` (20), `OMCT_SLURM_TIME_FACTOR` (3) | SLURM (EXECUTION.md) |
| `OMCT_MAX_TIMEOUT`, `OMCT_MAX_MEM`, `OMCT_MAX_PROCS` | caps for a study's `run` |

## Milestones (each: version bump, tests, README and planning.md updated)

- **M0 Research and plan** (this file; `tools/m0_probe.sh`). Done; the probe report from mochi is still to come.
- **M1 Format and translation, offline:** `catalog.py`, `units.py`, `format.py` (study_format ~10–12 kB, validate), `modelica.py`, `.mos` generation, `ref.py`, tests. Done (2026.10.21.1).
- **M2 Backend driver:** `omc.py`, cache, `simrun.py`, `matfile.py`, log and result parsing, override verification; fixtures from mochi; tests that skip without omc.
- **M3 Checks and benchmarks:** energy balance, `converge`, reliability, `bench.py`. Done in the sandbox (2026.10.21.4); benchmarks to run on mochi.
- **M4 Interfaces:** `api.py`, `cli.py`, `mcp_server.py`, images. Done in the sandbox (2026.10.21.5).
- **M5 Analyses:** sweeps, linearisation, steady. Done (2026.10.21.6; linearisation and steady came with M2/M3).
- **M6 SLURM:** `executor.py` from cfdt with sizing, caps, fake-SLURM tests; on mochi two sweeps from two conversations queue. Done in the sandbox (2026.10.21.7); the queueing test on mochi is next.
- **M7 Chains:** `chain_feat_modal`, b3dt mass, cfdt tables. Done in the sandbox (2026.10.21.10; cfdt curves as `table` blocks until fluid networks in v2).
- **M8 Joining the umbrella** (done 2026.10.21.11: llmt install/status/test/bench/tune on mochi, mcp test on the Mac): submodule, `translators.json` (key `omct`, port 8770, `suggest: remote`, `executor: {prefix: OMCT, kind: threads, min_minutes: 1}`, backends `omc` and `msl`, every setting in `env`), `BACKENDS.md` (official apt repository, MSL 4.0.0 via the package manager, checks), README row, `llmt install/status/test/bench/tune/mcp test` on mochi, `llmt mcp setup` on the Mac.

## Decisions:

1. Units: SI numbers, plus unit strings and `{value, unit, from}` objects converted and recorded. Signal values (step heights, limits, block start values) take any unit (`'2000 rpm'`), converted to SI.
2. Parameter names: MSL's names, plus convenience ones (`v0`, `w0`, `T0`, `m`+`cp`, `G` or `k`+`A`+`L`, `Gc` or `h`+`A`).
3. DC motor: composed R + L + RotationalEMF + Inertia + Damper (+ Fixed housing); `DC_PermanentMagnet` later.
4. Results: MAT files; JSON series downsampled to <= 400 points.
5. `sweep` is a key on any analysis; `steady` detected by us.
6. Scope: FluidHeatFlow, switches, MultiBody in v2.

Made while building M1 (tell me if any is wrong):
- Resistor without `T_ref`/`alpha` (no heat port in v1, and MSL's default device temperature 293.15 K differs from T_ref 300.15 K, a trap). `pulse_voltage` has no `nperiod` (PulseVoltage lacks it; the `pulse` block has it). The speed-dependent load is two kinds, `linear_speed_load` and `quadratic_speed_load` (two MSL classes).
- Initial values: the reference system decides which are free (forced block states first, then the study's values, then defaults; priority 1 storage before priority 2 spring/damper relative positions); only those get `fixed=true`. A study value that is not free is an error. Mass and inertia flanges are tied by equations, not merged into one node (each flange keeps its own force).
- Blocks: `initType = InitialState` written for every block with a state (structural); PID's `Td` defaults to 0 (MSL has only a start value).
- Composites are flattened into elements `X__part` (no nested models, so every value is a direct override and no bound parameter has to be recomputed by OpenModelica).

## State

**2026.10.21.12 (first end-to-end request, Claude app, 2026-10-21)**: the DC motor PI step response from the Claude app ran right (rise 46.4 ms, overshoot 5.9 %, settling 174 ms, peak current 19.6 A, 2000 rpm, reliable, converge same), but Nicola saw no picture: the model said "the image in the step above". Checked in Cowork: `get_image` returns the PNG and the model sees it, but it stays inside the collapsed tool result, so the user doesn't see it there. Fix: INSTRUCTIONS and the get_image docstring say so and tell the model to chart `outputs.<name>.series` (already in every result) with its own tools; the series are rounded to 6 significant digits (about half the tokens). The same holds for cfdt's get_image (not changed). To verify: the same request in the Claude app shows a chart drawn by the model.

**2026.10.21.11 on the Mac (2026-10-21)**: `llmt mcp setup` + `llmt mcp test`: omct over ssh to mochi: ok, openmodelica-translator, 12 tools, started in 1.9 s (b3dt, feat, cfdt, lmpt ok too). **M8 done: omct is part of llm-translators.** Next: an end-to-end request in the Claude app (the DC motor PI step response; a chain with feat), then the open ideas below.

**2026.10.21.11 in llm-translators (mochi, 2026-10-21)**: `llmt status`: omct 2026.10.21.11, commit c455460, pinned; OpenModelica (omc) ok, MSL 4.0.0 ok; runs: slurm. `llmt tune omct`: OMCT_PROCS=9 (18 of 20 physical cores / 2 workers), OMCT_EXECUTOR=slurm. `llmt test omct`: 228 passed (89 s). `llmt bench omct`: all 12 pass with OpenModelica (rc_step 3.3e-6, rlc_step overshoot 1.1e-5, rlc_poles_bode and msd_modes exact, msd_free 4.3e-7, inertia 1.2e-10, dc_motor 7e-12, thermal_rc 1.8e-5, thermal_steady 7.2e-9, two_mass exact, pi_first_order 2.5e-5, sweep_rc 3.3e-6 on one build). `llmt mcp test omct` on mochi: "no entry (llmt mcp setup)", as expected: the test reads the Claude app's MCP entries, which exist only on the client (the Mac) after `llmt mcp setup`. Next: `./llmt mcp setup` (omct remote on mochi) and `./llmt mcp test omct` on the Mac, restart the app, then an end-to-end request.

**2026.10.21.11 (M8)**: on mochi with 2026.10.21.10: 228 tests pass; `bench chain_feat_modal chain_b3dt_mass` both pass. chain_feat_modal: feat's cantilever gave k = 526.24 N/mm (100 N / tip uz) and its first frequency 834.4 Hz; the mass (33/140 m) on that spring here gives 848.76 Hz, against 846.69 expected (1.0146 x feat): 0.24 % (Rayleigh's ratio comes out 1.0171: feat's static and modal meshes differ slightly in shear and discretisation; well inside the 1 %). chain_b3dt_mass: 0.0785 kg exact, 254.039 Hz exact. Prepared in llm-translators (for Nicola to review and commit): the `translators.json` entry (key omct, port 8770, suggest remote, extras [mcp], uses feat and b3dt, status/bench true, executor {OMCT, threads, 1 min}, tuning OMCT_PROCS/OMCT_WORKERS, backends omc and msl -> tool_status keys, all 21 OMCT_ settings in env; `./llmt list` reads it), BACKENDS.md (table row, the OpenModelica section with the apt repository from mochi's sources.list and installPackage for MSL 4.0.0, SLURM row), README (translators table, SLURM line), docs/EXECUTION.md (status line). Still to do by Nicola: the submodule, `llmt install / status / test / bench / tune / mcp test` on mochi, `llmt mcp setup` on the Mac.

**2026.10.21.10 (M7, sandbox)**: on mochi with 2026.10.21.9 the SLURM test passed fully: 2609 ran (30 CPUs), 2610 waited (Resources) and followed; each sweep 120/120 reliable, 1 build, **5.6 s** (was 200 s: the points now run in processes and the reference uses <= 1000 points); estimate 0.36 min (an upper bound, as it should be). New: `chains.py` (feat stiffness/mass/frequency/conductance, b3dt mass, Rayleigh) and two chain benchmarks run by name: `chain_feat_modal` runs fea-translator's own `cantilever` and `cantilever_modal` cases (100x10x10 mm steel, 20x4x4 mesh), builds a mass (33/140 m) on a spring (k = 100 N / |tip uz|) here, linearises, and checks f = 1.0146 x feat's first frequency (1 %); `chain_b3dt_mass`: a b3dt box's volume x 7850 kg/m3 -> mass (1e-9), f = sqrt(k/m)/2 pi (1e-5). bench requests store their checks (chains are prepared once, when submitted). Tests: 228 pass (the feat chain with a stand-in feat giving exact Euler-Bernoulli answers: 1.01465). Open: b3dt does not report inertia (build123d has matrix_of_inertia; ask b3dt to add it, then rotational chains).
Next on mochi (feat and b3dt are in the venv; ~1 min):
```bash
cd ~/Software/openmodelica-translator && git pull
PY=~/Software/llm-translators/.venv/bin/python
$PY -m pytest -q tests/ 2>&1 | tail -2
$PY -m openmodelica_translator.cli bench chain_feat_modal chain_b3dt_mass > tools/m7_chains_mochi.json
$PY -c "import json; d=json.load(open('tools/m7_chains_mochi.json')); print('passed', d['passed'], 'failed', d['failed'], 'skipped', d['skipped']); [print(n, [(c['quantity'], c['expected'], c['got'], c.get('error')) for c in v['checks']], v.get('why','')) for n, v in d['cases'].items()]"
```

**2026.10.21.9 (mochi, SLURM queueing, 2026-10-21)**: with 2026.10.21.8, `tools/m6_slurm_test.py`: job 2607 (sweep_a, 30 CPUs) ran while 2608 (sweep_b, 30 CPUs) waited with reason Resources (machine 30 of 40 CPUs allocated); 2608 started when 2607 ended; both 120 points, 120 reliable, 1 build each (b on a's cache), execution {executor slurm, procs_requested 30, procs_used 30, limits from_study [procs, queue]}; collected by a third process. **The queueing contract holds.** But each sweep took ~200 s for 120 points of 20000 intervals on 30 threads, and the estimate said 0.06 min: the points' Python checks (reading, energy, and above all the reference implementation on all 20001 points) ran serialised on the GIL in threads. Fixes: the reference comparison uses at most 1000 points (evenly over the non-event points); a sweep outside the server's daemonic pool (a SLURM job, the CLI) runs its points in processes (threads stay inside the pool); runs.json also keeps a whole study's time on the build (`study_s`), and the estimate uses it (x 1.5), else the simulation's s/interval x 3 + 2 s + 1e-4 s per interval. Tests: 226 pass.
Next on mochi (1-2 min): `OMCT_EXECUTOR=slurm ~/Software/llm-translators/.venv/bin/python tools/m6_slurm_test.py` again: each sweep should now take a few seconds of its own, sweep_b still queued behind sweep_a.

**2026.10.21.8 (mochi, first SLURM jobs)**: with 2026.10.21.7, 225 tests pass on mochi (91 s); `tools/m6_slurm_test.py` submitted both sweeps (jobs 2603, 2604; 30 CPUs each, estimate 0.06 min; 2604 queued behind 2603 with "Priority", ahead 1) but both jobs failed at once: `No module named 'openmodelica_translator'`. The job runs the venv's python in the study folder, and the translator is not installed in the venv yet (it will be with `llmt install omct`); the fake-SLURM tests passed because their fixture put the repository on PYTHONPATH. Fix: job.sh exports the package's folder on PYTHONPATH; the fake-SLURM fixture now clears PYTHONPATH, so the tests see what a real job sees. The failure states were right: "the job ended without a result (no accounting)" with the end of job.log.

**2026.10.21.7 (M6, sandbox)**: on mochi with 2026.10.21.6: 212 tests pass; `bench sweep_rc` passes (one build, three reliable points, tau = RC to 1e-4); `pi_gain_sweep`: 12 points, 1 build, 5.3 s, every point reliable (overshoot 5.5598 % at k 0.5, T 0.1 against the reference's 5.5595 %). New: `executor.py` from cfdt (kind threads: --ntasks=1 --cpus-per-task = min(points, run.procs/OMCT_PROCS), at least OMCT_COMPILE_PROCS on a cache miss, the reference solver never compiles; --mem auto 1 GB + 256 MB/CPU; time limit timeout + 10 or the estimate; estimates from the cache's runs.json, written after each run, else 2e-5 s/interval; job.sh/job.json; states from squeue/sacct/job.log; jobs with the queue summary; cancel with scancel; run-study sets the sweep threads from SLURM_CPUS_PER_TASK; execution record with procs_requested/used and limits); api start() submits, _collect and status read SLURM states (status returns SLURM-only waits after 15 s), bench through SLURM; MCP instructions with the SLURM paragraph (27 lines). Tests: 225 pass, 13 against `tests/fake_slurm.py` (submit and collection by a new process, routing, sizing, queued then cancelled, every final state from sacct and from job.log, sbatch failure, run-study by hand, status, bench).
Next on mochi (1-3 min): the queueing test, two 120-point sweeps from two processes, 30 CPUs each on the 40-CPU node:
```bash
cd ~/Software/openmodelica-translator && git pull
PY=~/Software/llm-translators/.venv/bin/python
$PY -m pytest -q tests/ 2>&1 | tail -2
OMCT_EXECUTOR=slurm $PY tools/m6_slurm_test.py
```

**2026.10.21.6 (M5, sandbox)**: on mochi with 2026.10.21.5: 209 tests pass (74 s); `tools/mcp_smoke.py --solver omc` through the stdio MCP client: the DC motor with LimPID PI speed control ran reliable (every check ok; reference not covered: LimPID), w 0 -> 209.44 rad/s (2000 rpm), rise 46.5 ms, overshoot 5.88 %, peak at 0.102 s, settling 0.174 s, picture returned. New: `sweep.py` (grid/zip points, the first point builds, the rest in threads, run.procs or OMCT_PROCS at a time, since pool workers are daemonic and cannot fork pools; per-point folders points/pNNNN with full checks; table with params, params_si, values, ok, reliable; builds counted; sweep.png; progress "k of n points"); `post.get_path` takes keys with dots (table columns); benchmark `sweep_rc`. Tests: 212 pass (the fake-omc sweep: 6 points, 1 build).
Next on mochi (about 1-2 min):
```bash
cd ~/Software/openmodelica-translator && git pull
PY=~/Software/llm-translators/.venv/bin/python
$PY -m pytest -q tests/ 2>&1 | tail -2
$PY -m openmodelica_translator.cli bench sweep_rc | grep -E '"(passed|failed)"' -A2
$PY -m openmodelica_translator.cli run examples/studies.json pi_gain_sweep > tools/m5_sweep_mochi.json
$PY -c "import json; s=json.load(open('tools/m5_sweep_mochi.json'))['studies']['pi_gain_sweep']; print(s['ok'], s['reliable'], s['sweep']['builds'], s['sweep']['seconds'], 's'); [print(r['params'], r['values'], r['reliable']) for r in s['sweep']['table']]"
```

**2026.10.21.5 (M4, sandbox)**: on mochi with 2026.10.21.4: 204 tests pass (65 s) and **all 11 benchmarks pass with OpenModelica 1.27.1** (rc_step, rlc_step, rlc_poles_bode, msd_modes, msd_free, inertia_torque_step, dc_motor_steady, thermal_rc, thermal_steady, two_mass, pi_first_order), each reliable (energy balance and reference comparison included). New: `api.py` with the cfdt-style worker pool (spawn, lives across calls), `start`/`status`/`run(wait)` from request folders (states from files: model.mo -> building, sim.log -> simulating; a study whose server process died is reported failed), `jobs`, `cancel` (flag files; a queued study is skipped), `bench(wait_s)` + `bench_status`; `render.py` (time plots per unit, Bode; `series.npz` saved with every run; `render_images`); `mcp_server.py` (12 tools, 22-line instructions; checked over stdio with the SDK 2.3 client: handshake, run_studies, get_image); CLI `status REQUEST`, `jobs`, `cancel`, `images`, `run-study`; `tools/mcp_smoke.py`. Tests: 209 pass. Not yet: the SLURM paragraph in the instructions and `executor` (M6), sweeps (M5).
Next on mochi (about 1-2 min):
```bash
cd ~/Software/openmodelica-translator && git pull
PY=~/Software/llm-translators/.venv/bin/python
$PY -m pytest -q tests/ 2>&1 | tail -2
$PY tools/mcp_smoke.py --solver omc
```

**2026.10.21.4 (M3, sandbox)**: on mochi with 2026.10.21.3 every example ran (the sweep refused as intended); OpenModelica compiled in M__rotor.w, J1.w, m.v (aliases) and LimPID's yMax/yMin. New: `post.py` derived quantities (step: rise 10-90 %, overshoot, peak, settling 2 %; crossing; window; steady), steady analysis (`steady.<output>`: value, t_steady, reached in the last 10 %), `expect` (passed); `energy.py` (boundaries, storage, dissipation from element values and recorded series; Stieltjes integral for force/torque work; tolerance max(1 %, 3 x Richardson)); the reference comparison on the run's time points (events and source steps masked; max(1e-4, 100 tol)); eigenvalues for linearize (1e-5); `converge` (tol/10, max(1e-6, 100 tol)); `bench.py`: 11 cases with closed forms, all pass with the reference solver; `linear.analyse` adds natural_frequencies_Hz, damping_ratios, time_constants_s, rigid-body modes counted. The energy balance found two reference bugs: Pulse edges computed two ways (floor vs sums) disagreed at 0.0035 s, and a source step exactly at the stop time; both fixed. Tests: 204 pass (+2 with omc).
Next on mochi (about 1-2 min):
```bash
cd ~/Software/openmodelica-translator && git pull
PY=~/Software/llm-translators/.venv/bin/python
$PY -m pytest -q tests/ 2>&1 | tail -3
$PY -m openmodelica_translator.cli bench > tools/m3_bench_mochi.json
$PY -c "import json; d=json.load(open('tools/m3_bench_mochi.json')); print('passed', d['passed']); print('failed', d['failed']); [print(n, c.get('why',''), [(k['quantity'], k['expected'], k['got'], k.get('error')) for k in c['checks'] if not k['passed']], c.get('checks_run')) for n, c in d['cases'].items() if not c.get('passed')]"
```

**2026.10.21.3 (mochi, first real runs, 2026-10-21)**: with 2026.10.21.2, 186 tests passed on mochi in 24 s, including the RC against its closed form at 1e-4 through OpenModelica; `omct status` found omc 1.27.1~2-g6db4671 and MSL 4.0.0. `omct run examples/studies.json` (only the tail was seen): two_mass_torque_step ran (w2 = 19.61 rad/s at 0.5 s, reliable); bracket_on_spring_from_feat failed: "m.v is fixed in the compiled model". OpenModelica made the mass's `v` (fixed=true) an alias of the spring-damper's `v_rel` (stateSelect prefer), and an alias is not changeable at run time. Fix: values with isValueChangeable false are left out of the override file when the build has the same value compiled in (meta.json `compiled_values`); a different value builds a variant whose key adds those values (`omc.build(variant=...)`); read-back still checks them. The pi_gain_sweep is refused until M5, as intended.

**2026.10.21.2 (M2, sandbox)**: the probe report is in (`tools/m0_report_mochi.txt`, assumptions table updated: A1-A17 measured). New: `omc.py` (find omc, MSL status, the build script's 11 printed values parsed with a small tokenizer, checkModel counts compared, errors mapped to the declaration's line, the cache: flock on `<hash>.lock`, build in `.<hash>.<pid>.tmp`, prune to exe/_init.xml/_info.json/*.bin/.libs/.log + model.mo/build.mos/build.log/meta.json, atomic rename; failed builds kept in `.cache/.failed/`), `simrun.py` (flags, `-alarm`, `OMP_NUM_THREADS=1`, log parsing, refused/unknown overrides as errors, read-back), `matfile.py`, `linear.py`, `post.py` (output statistics, <= 400 points keeping events and extremes), `runner.py`, `api.py` (tool_status, run synchronously), `proc.py`, CLI `status` and `run`; the escape hatch builds too. Tests: 187 pass in the sandbox, from the mochi fixtures (`tests/fixtures/`: logs, the omc script output, linearized_model.mo) and `tests/fake_omc.py` (prints what omc printed on mochi; its executable simulates with the reference and writes a MAT file); 2 tests need omc (RC against the closed form at 1e-4; every example builds and runs). The reference reproduces the probe's thermal block: 376.37728 K at 20000 s against OpenModelica's 376.37711 K (5e-7).
Not yet: the pool and pending results (M4), derived quantities / energy / reference comparison (M3), sweeps (M5: a sweep study is refused for now).
Next on mochi (about 2 minutes, no install needed):
```bash
cd ~/Software && git clone git@github.com:feranick/openmodelica-translator.git   # or git pull in it
cd ~/Software/openmodelica-translator
PY=~/Software/llm-translators/.venv/bin/python
$PY -m pytest -q tests/ 2>&1 | tail -5
$PY -m openmodelica_translator.cli status
$PY -m openmodelica_translator.cli run examples/studies.json > tools/m2_run_mochi.json; tail -c 3000 tools/m2_run_mochi.json
```

**2026.10.21.1 (M1, sandbox)**: `catalog.py`, `units.py`, `network.py`, `format.py`, `modelica.py`, `ref.py`, `cli.py` (format, validate, model); `examples/studies.json` (8 studies: the typical requests); 173 tests pass in the sandbox (`python3 -m pytest -q tests/`), including:
- the catalog against the MSL 4.0.0 sources (`tools/check_catalog_msl.py`: every class, port, parameter, variable and start value exists; it caught `PulseVoltage.nperiod`);
- the reference against closed forms: RC 1e-9, RLC step 1e-8 and poles 1e-10, mass-spring-damper 1e-10, thermal RC 1e-7 and its steady state, DC motor speed and poles, PI on a first-order plant 1e-8 (scipy.signal), two-mass eigenvalues and low-frequency response, index-2 networks (capacitor across a sine source, parallel capacitors, a current sensor between capacitors, an op-amp integrator), table/transfer function/PID, an inertia chain with a gear;
- every catalog variable has a reference value; every example validates, translates and parses as Modelica (modparc grammar; not yet compiled).
Found while testing: sources must be evaluated with their left limit at the end of an integration interval (Radau evaluates at the endpoint), and rank decisions need row/column equilibration (a capacitor across a source was taken as free before).

Not verified (no omc in the sandbox): that OpenModelica accepts the generated text (modifier syntax `v(start=..., fixed=true)` on inherited variables, enum and array literals, `table=[...]`, top-level `RealInput` connected to a block input), and every assumption A1-A19. Next: the probe report from mochi (`ssh nicola@mochi.mit.edu 'bash -s' < tools/m0_probe.sh > tools/m0_report_mochi.txt 2>&1`), then M2 (omc driver) built on its output, and a first real build of `examples/studies.json` on mochi.

**2026.10.20.1 (M0)**: read the umbrella docs (create_new_translators.md, EXECUTION.md), cfdt (planning, executor, api, mcp_server, format), lmpt refcalc.py, feat's result keys. Read the OpenModelica v1.27.1 sources (overrides, steady state, linearisation, checkModel, buildModel, logs, MAT format) and the MSL 4.0.0 sources (class names, ports, parameters). Sandbox cannot install OpenModelica (proxy blocks build.openmodelica.org; no root). Wrote this plan and `tools/m0_probe.sh`.

## Open ideas

- `dc_machine` (DC_PermanentMagnet) with losses and a thermal port; induction machines with `Machines` quasi-static models.
- FluidHeatFlow coolant loops (v2), then Modelica.Fluid with cfdt pump/pipe tables.
- Electro-thermal coupling everywhere (`heat_port` on R and dampers into thermal networks).
- FMU export for co-simulation; parameter estimation from measured data (fit k, J, d).
- Event-driven stops (`crossing` that ends the run), frequency sweeps by simulation for nonlinear systems.
- `ref.py` for piecewise-linear elements (ideal diode, limiter) by event detection.
