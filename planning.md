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
- **mochi** (Ubuntu, 40 hardware threads, 61.5 GB, SLURM on one node): the first machine with OpenModelica (carbonio too, since 2026-10-21). Until 2026-10-22 the Mac reached it with `ssh nicola@mochi.mit.edu ~/Software/llm-translators/bin/mcp omct`; now through the router (below). One shared venv `~/Software/llm-translators/.venv`.
- **Machines behind llm-translators' router (2026-10-23):** carbonio, mochi, spark1, spark2; omct reached through
  `~/Software/llm-translators/bin/router omct` on the Mac. carbonio has OpenModelica with MSL 4.0.0 and 4.1.0 (hence
  requireExactVersion); spark1's SLURM node was down (its status can exceed the router's 35 s).
- **Deploying a new omct version (from the main llm-translators chat; use the command that matches each machine,
  the router config says which kind each is):**
  - Mac: commit and push openmodelica-translator, then `cd ~/Software/llm-translators && ./llmt pin omct --commit --push`.
  - **carbonio, mochi, spark1, spark2: all shared installs** (Nicola, 2026-10-23) (`/opt/llm-translators/current`,
    read-only release; `llmt update` is refused on purpose; only the admin builds a release), on each:
    `cd /opt/llm-translators/src && sudo git pull && sudo git submodule update --init && sudo ./llmt deploy --test`,
    then as yourself `/opt/llm-translators/current/llmt status omct`. (`~/Software/llm-translators` on these
    machines is not what the router runs.)
  - Check from the Mac: `cd ~/Software/llm-translators && ./llmt router status omct` (every machine at the new
    version), then reconnect the servers in the Claude app.
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
| A20 | `loadModel(Modelica, {"4.0.0"}, requireExactVersion=true)` loads 4.0.0(+maint.om) even when newer MSL versions are installed; without it omc takes the newest compatible one | **carbonio ✓** (plain: 4.1.0; exact: 4.0.0) | tools probe 2026-10-22 |
| A21 | The compiled model reads an empty `-overrideFile` | **✗** (user report, 2026-10-23: "could not read overrideFile ...: Success"); omct omits the flag when there are no values | escape-hatch runs |

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

**2026.10.21.22 (V3.3 in progress, sandbox, 2026-10-21)**: the nonlinear reference, **off by default** (OMCT_REF_NONLINEAR=1 turns it on; without it AC machines are 'not covered' as in 2026.10.21.21 and their benchmarks stay OpenModelica-only). Done: `refnl.py` (Radau IIA order 5 for M z' = f with singular constant M, RADAU5's error estimate and tolerance transformation, transformed real + complex Newton systems, index-2 scaling, running-maximum absolute tolerances, consistent initial values by Gauss-Newton; checked on y' = -y, an index-2 inductor, Van der Pol mu 10-100 against scipy); System rows with nonlinear terms (fn, deps) and post-computed outputs (funcs); the machines rewritten as their equations (IM in the stator frame with i_L and the rotor current as states; PMSM in the rotor frame with i_dq and the damper; losses, temperature-dependent resistances and heat as nonlinear terms); V/f controller and dq transform nonlinear; validation and the choice of initial values use the linearisation at 0 (same states as before). Two integration paths: (a) the nonlinear terms as inputs of the exact linear reduction, then an ODE in its coordinates (solve_ivp Radau), used when the dynamics need no derivative of the terms; checked exact on joined inertias with a nonlinear damper (1e-9); (b) the DAE path with the linear hidden constraints differentiated first (rigidly joined inertias are index 3), checked on an RLC with a nonlinear resistor (1e-8). Open: on the induction machine at fixed speed the phasor solution of the stamped equations gives exactly the equivalent circuit (66.0064 A, 100.0000 N.m) but path (a) settles at 64.96 A / 96.84 N.m and paths (a) and (b) disagree in the transient: a coupling bug to find. Then V3.4.

**2026.10.26.1 (2026-10-26)**: three reports from use. (1) Motor heating sweeps (QS induction machine + 4 thermal nodes, 6 h, 27 points on carbonio): 6 points unreliable on the reference alone. Diagnosis: run alone, point 3 passes with Pcu2 (rotor copper loss) at 8.5e-5 of its range against the limit 1e-4 at tolerance 1e-6, and 4.5e-6 at 1e-8 (mochi and carbonio, the same cached build): OpenModelica's integration error, at the limit; the reference is converged (its tolerances x 1/100 move Tw by 1e-5 K). (An earlier comparison of the sweep table with final values was wrong: the table holds window means.) Fix: _retry_tighter: when the independent check is the only failure, the study runs once more at tolerance/100; the tighter run is the result if it agrees (warning), else the first result stays unreliable with retry.why = a model difference. Sweeps: per point (run_study). (2) Custom models: checks.cross_solver (the other integrator, IDA <-> DASSL, at tolerance/10, compared like the reference; covered false if that run fails; cross_check: false skips it; estimates count the extra run), and invariants for any study. (3) Expression defaults in custom models: bound_parameters() (the model's parameters and one level of components of classes in the same text, with modifiers) rejects them in validation; validation also reads the cached build's changeable map; after a build, escape-hatch values marked not changeable fail at once. Cause of the late failure: omc.build recorded the study's values as compiled_values for custom models, so the runner thought the build already had them and dropped them. Unverified: (a) that OpenModelica 1.27.1 marks a parameter with an expression default isValueChangeable="false" in _init.xml (the after-build check relies on it; the read-back still catches it otherwise). Probe: a custom model `model M parameter Real a = 1; parameter Real b = 2*a; Real y; equation y = b; end M;` with values {"b": 5}: expect "values the model cannot take at run time" after the build (or validation, from the static check); (b) IDA on the users' custom models (cross_solver reports covered false when it fails).

**2026.10.25.3 (2026-10-25)**: llm-translators' checklist for translators (six items). Already done: (3) stale servers (2026.10.23.3 + the serving guard of 2026.10.25.1; run-study and tests never refuse or exit), (4) estimates in validation (2026.10.22.1; procs after the caps, mem as --mem). Done in 2026.10.25.2, completed now: (1) node pinning: the node list and CPUs now from `scontrol -o show node` (the checklist's source; was sinfo), and a job's CPUs capped at this node's (with any: the largest node's), so the estimate the router tests with sbatch --test-only matches the job; (5) failed scontrol not remembered (and msl_status: an lru_cache kept a failed first MSL load for the server's lifetime: now only success is kept, and its omc call is bounded at 40 s since study_format runs it). New: (2) OMCT_SLURM_ARGS with several options: shlex everywhere already; mem_mb_for_job now reads "--mem 8G" (two words) and --mem-per-cpu (x CPUs), test with "--mem=8G --nodelist=spark-5da5"; (6) wait bounds: sbatch timeout 60 -> 15 s and, after a timeout, the request's other studies fail at once for 30 s (a dead controller made run_studies wait 60 s per study); _run (squeue/sacct/scontrol) 20 -> 10 s; status() starts no collect it cannot finish by the deadline (measured from the last one); run() counts the submit time against wait. Compiling and simulating were already in the pool or SLURM jobs; get_model and validate_studies only translate. Tests: fake two-node scontrol (sizes 20 and 12 CPUs), args with two options, hanging sbatch, slow collect.

**2026.10.25.2 (2026-10-25)**: in sync with llm-translators 2026.10.23.6 / 2026.10.25.2 (clusters of several nodes: the sparks are one SLURM cluster, flash-dgx, homes not shared). OMCT_SLURM_NODE local (default) | any | node list, as cfdt 2026.10.25.2: local_node() from `scontrol -o show node` (host name to NodeName/NodeHostName, else this machine's addresses to NodeAddr: the sparks' Tailscale addresses), node_list() (a --nodelist/-w in OMCT_SLURM_ARGS wins; one-node clusters: none), #SBATCH --nodelist in job.sh, job.json nodelist, status.executor.node {setting, nodelist, cluster_nodes} and nodes; squeue %N remembered per job, and a job gone without job.log from another node is reported as such. Unlike cfdt, a failed sinfo/scontrol (controller down) is not cached. No rank capping (omct jobs are one task with threads). Tests: fake sinfo/scontrol (host name, address, any, list, -w, one node, failure not remembered, lost job). Not yet run on the sparks: check `llmt status omct` there shows node spark-5da5 / spark-0808 and a long sweep's job.sh has the --nodelist.

**2026.10.25.1 (2026-10-25)**: deploy --test on every machine printed two F in omct after 16 tests, then "omct: pass", and switched current; `llmt test omct` afterwards passed (361). Cause (2026.10.23.3): deploy runs the new release's tests before switching current, so stale_reason() saw current -> the old release; in-process tool calls (test_tools_run_off_the_event_loop_and_are_logged, test_tool_crash_message_reaches_the_caller) were refused with STALE_SERVER, and _exit_soon's os._exit(0) one second later ended pytest with exit code 0. Reproduced here (fake releases/r1, r2 with current -> r1: the same two failures). Fix: _STATE["serving"], set only by main(); _may_exit() is false otherwise (no refusal, no exit; a server_stale note only). Test: test_stale_install_never_exits_outside_a_server; from a fake not-yet-current release test_api, test_backend and test_slurm pass. llm-translators 2026.10.25.1: llmt test passes --junit-xml and counts a run without the report as a failure (a test that exits the process with 0).

**2026.10.23.4 (2026-10-23)**: reported from use: with converge: true a study was unreliable although its physical outputs changed by at most 3.5e-5 of their range between tolerances 1e-7 and 1e-8; the flag came from two balance outputs (total water, energy error), constant by design: their range is solver noise and the change divided by it is O(1). Fix (choice: automatic + optional absolute tolerance): judge_converge() reports an output as noise, not judged, when its range at tolerance/10 is <= 0.3 x its range at the tolerance and its change <= 2 x that range (unit-free: a real output's range does not shrink with the tolerance; a badly unconverged one fails the change test); converge: {abs_tol: {output: value with units}} judges named outputs by their absolute change. Result: checks.converge.not_judged, detail. Open: the physical outputs' 3.5e-5 is itself over max(1e-6, 100 x 1e-7) = 1e-5 unless their offset raises the allowance (allowed(): 10 tol max|y| / range); if they still trip, the limit (stricter than the reference check's max(1e-4, 100 tol)) is the question.

**2026.10.23.3 (2026-10-23)**: stale servers. Reported: on carbonio 2026.10.23.2 was installed, but three probes (run_20261023_161141_bc085f, _161211_f02ab4, _161249_9ca4c8) ran 2026.10.22.1: the router keeps its servers (up to 3 per machine) for hours, and a server keeps the release it started from (bin/mcp resolves ROOT with pwd -P), so neither fix was live. Fix (omct): stale_reason() compares realpath(<prefix>/current) with the server's own release (shared) or the __version__ in __init__.py on disk (checkout); a call finding the server stale and idle (no calls, no pool studies) is refused with STALE_SERVER (as a ToolError: SDK 2.x hides other exceptions' text) and the server exits 1 s later; a watcher exits an idle stale server every 20 s; a busy one answers with server_stale and exits when idle. Fix (llm-translators router 2026.10.23.5): a STALE_SERVER answer (or "server ended" with that line on stderr) drops the child and repeats the call once on a new one. Tested here: unit tests; a real stdio server in a fake <prefix>/releases/{r1,r2} with current flipped under it: the next call came back STALE_SERVER and the server exited after 1.0 s; with no call it exited after 20 s; the router's retry with fake children. Also: SDK 2.x hid the text of every tool crash ("Error executing tool X"): _logged now raises ToolError with the message. Next: deploy on all four, probe that results say "translator": "2026.10.23.3" with no server_stale.

**2026.10.23.2 (2026-10-23)**: the disconnects with sweeps and converge (carbonio, through the router). Evidence: the app's log shows "Shutting down server ... (intentional shutdown)" (the app closed the connection), router calls of 12-35 s, no traceback. Not reproduced here with a real MCP client over stdio (plain, sweep, converge, custom models, a fake SLURM: the server stays up). Found on the way: a sweep over a custom model failed at once (point_study assumed components): custom models now take modelica.values {path: number} (override file, no rebuild) and sweeps over Modelica paths. Changes against the disconnects: (1) every tool runs in a worker thread (anyio.to_thread): run_studies and study_status wait up to 50 s for pending runs (sweeps and converge are the ones that wait), and an MCP SDK that runs blocking tools on its event loop would leave pings and other calls unanswered meanwhile; tested: a ping and jobs() answered in < 0.01 s while run_studies waited 15 s; (2) the server's own log in $OMCT_OUT/.server/ (calls, durations, failures with tracebacks, SIGTERM/SIGHUP/SIGINT, faulthandler for fatal signals, exit, maxrss): behind the router the server's stderr is not kept. Next: reproduce on carbonio and read that log.

**2026.10.23.1 (2026-10-23)**: reported from use: (1) every escape-hatch run failed at start-up with "could not read overrideFile ...: Success": the runner always passed -overrideFile, and with no values the file is empty, which OpenModelica 1.27.1 cannot read (A21). Fixed: no flag when there is nothing to override (test with a stubbed run). (2) Requests with a sweep or converge "closed the connection to the server", which did not come back for about 10 minutes: cause not found yet (no print to stdout, child processes write to log files, process groups are their own; responses are small: a sweep's status 1.3 kB, converge 8.6 kB). Hardening meanwhile: stdin=/dev/null for every helper program (sbatch and the others inherited the MCP stdio pipe). Asked for the MCP log and the request folders.

**2026.10.22.1 (2026-10-22)**: estimates in validation (llm-translators docs/EXECUTION.md, ROUTER.md): api.validate adds, per valid study, executor.estimate(study, solver) = {minutes: estimate_minutes (upper; measured study_s once a build has run), procs: cpus_for_job (size cap; OMCT_COMPILE_PROCS on a cache miss with omc), mem_mb: mem_mb_for_job (run.mem, else --mem in OMCT_SLURM_ARGS, else OMCT_SLURM_MEM, auto 1024 + 256/CPU)}; solver as a run would pick it (OMCT_SOLVER, auto: omc when found). estimate_minutes adds 0.01 s per interval for transient AC machines without a measurement (their nonlinear reference: 12-40 s at 5000 intervals). Test in test_slurm.py.

**2026.10.21.28 (results on three machines, 2026-10-22)**: carbonio: status "MSL 4.0.0: ok" (now true), `llmt test omct` 352 passed (the two OpenModelica backend tests pass). mochi: status ok; qs_im_grid_slip and im_grid_delta pass with OpenModelica (same numbers as .26). Spark1: tests/test_nonlinear.py 9 passed (the u'' check fix). V3 closed.

**2026.10.21.28 (2026-10-22)**: carbonio has Modelica 3.2.3, 4.0.0+maint.om and 4.1.0+maint.om in ~/.openmodelica/libraries. Probe there: loadModel(Modelica, {"4.0.0"}) loads 4.1.0; with requireExactVersion=true it loads 4.0.0; {"4.0.0+maint.om"} alone loads 4.1.0, with requireExactVersion 4.0.0. So OpenModelica takes the newest compatible version unless told otherwise (mochi worked because only 4.0.0 is installed there). The build script and msl_status now pass requireExactVersion=true. (A20 below.)

**2026.10.21.27 (2026-10-22)**: carbonio's `llmt status` said "Modelica Standard Library 4.0.0: ok" while every build refused "omc loaded MSL 4.1.0, not 4.0.0". Cause: msl_status only checked that loadModel(Modelica, {"4.0.0"}) succeeded; OpenModelica then loads a newer compatible MSL when 4.0.0 is missing. Now status compares the loaded version and reports the fallback as an error (with the path omc took). Test with the fake omc (OMCT_FAKE_MSL). Spark1 (with .26's reasons): "u'' in the dynamics (9.94e-19, 1.02e-06 > 9.7e-07)": the combined u'' coefficient C2 was 1e-18, but a second check on Vp Gz's u'' columns alone (without the -P u' part that cancels it) tripped. Removed; every order o >= 2 is now judged by its whole coefficient Vp (Az P_o + Gz_o - P_(o-1)).

**2026.10.21.26 (mochi results of V3.4, 2026-10-22)**: `llmt test omct` 351 passed (on .25). Bench with OpenModelica: qs_im_grid_slip (w 3e-12, I 1e-10, T 1.4e-10, P 1.4e-10), qs_pmsm_current_torque (T 7e-13, w 6.6e-7, V 6.4e-7: w_end is the tanh without the drive resistor), qs_im_vf_start 5e-11, qs_pmsm_damper_grid (w 1.6e-10, T 4.8e-9), im_grid_delta (w 2.6e-9, T 1.3e-8), qs_im_grid_delta 3e-12; every check (balance, initialisation, overrides, energy, reference) true. D1-D4 confirmed. Spark1's rerun of the fixed test and carbonio (needs MSL 4.0.0) pending.

**2026.10.21.26 (2026-10-21)**: test results of .25 on other machines. Spark1 (ARM): 350 passed, test_pmsm_current_drive_torque_and_energy[True] failed: the current-fed orientation was rejected there (passes here): a borderline numerical check in _nl_ode. Hardened: _particular_on uses rank 1e-9 (as _rank_split) and a residual relative to |H| |P|, with one refinement step; the zero floor for C2 scales with |Vp| |Az| |P|; reasons recorded in _nlode_why (the test prints them). carbonio: omc loads MSL 4.1.0 (4.0.0 not installed): every OpenModelica build refused by design ("omc loaded MSL 4.1.0, not 4.0.0"), so the backend tests and the bench failed there; not a code problem (install MSL 4.0.0 there).

**2026.10.21.25 (V3.4, sandbox, 2026-10-21)**: quasi-static machines (design and D1-D4 in "V3.4 quasi-static machines" below). Kinds: qs_ground, qs_star, qs_ac_voltage, qs_current_rms, qs_voltage_rms, qs_power (composite over PowerSensor); hidden qs_variable_voltage, qs_reference_current, qs_resistor_core, qs_vf_controller, qs_current_controller, qs_terminal_box, qs_im_core, qs_pmsm_core, qs_pmsm_damper_core; composites qs_grid, qs_vf_inverter (a gain fans f out to the controller and the source), qs_pmsm_current_drive (1 MOhm per phase across the reference current source as in MSL's SMPM_CurrentSource, which uses 1e5: without it the machine's zero/negative-sequence constraints make the current-fed circuit structurally singular; 1e9 was too small a conductance for the reduction's rank decisions), qs_induction_machine, qs_pmsm, qs_pmsm_damper (Y: a qs_ground on the star point inside, through a composite part with a condition; D: none). Port types qs_pin, qs_plug (domain quasi-static), cvin/cvout (three complex signals). Network: QS circuits need a qs_ground and exactly one QS source; mixing with transient plugs is an error that says why; QS port values are not outputs. Reference (refqs.py): every complex quantity an (re, im) pair, one reference angle and frequency per QS circuit; machines as derived; checked by tests/test_qs.py against an oracle that solves MSL's component equations literally (winding, converter with symmetrical components, eddy core, stray permeance/reluctance, salient air gap rotated by gammar, cage, damper m = 2 with N = sqrt(3/2), magnet, magnetic grounds at sp and rn): IM with and without core loss at three slips, salient PMSM with and without damper (Rrd != Rrq, Lrsigmad != Lrsigmaq) off synchronism: 1e-9. Found and fixed on the way: (1) nonlinear rows whose linear part was empty (rotor equations written wholly as terms) made path (a)'s reduction fail: the resistances are linear now; (2) the stand-in linearised at z = 0 loses couplings when everything is multiplied by omega: if its reduction fails it is linearised at a generic point (structure only); (3) the nonlinear terms are solved block by block in dependency order (Tarjan's strongly connected components; plain iteration only while it contracts, else Newton from the start value): with heat ports the old plain iteration diverged; (4) the transient terminal box in delta left its star-point node without an equation: validation refused every delta machine since V3.2 (fixed: the unconnected node's potential is 0). Energy: QS sources -P_total, machines 1/2 Jr w^2 and lossPowerTotal; not covered for a QS PMSM on a voltage supply (the phasor model is not energy-conserving while the rotor slips: P_airgap - tau w - damper loss = 3 omega_r Lmd Ie x (I_L + I_D'), a property of the QS model). Benchmarks with the reference: qs_im_grid_slip 1e-13 (and P 6e-12), qs_pmsm_current_torque T 7e-13, qs_im_vf_start 3e-13, qs_pmsm_damper_grid 2e-12, im_grid_delta 2e-12, qs_im_grid_delta 1e-13. On mochi: D1-D4 through the qs_* benchmarks.

**2026.10.21.24 (2026-10-21)**: decided with the user: the nonlinear reference is on by default (ref.nonlinear_on(); OMCT_REF_NONLINEAR=0/false/no/off switches it off, and then the nonlinear benchmarks need OpenModelica). The MCP instructions list machines among the reference's coverage. 310 tests. V3.3 done; next V3.4 (quasi-static machines).

**2026.10.21.23 (mochi results of V3.3, 2026-10-22)**: `llmt test omct` 309 passed, 4 skipped (4 min 42 s; the nonlinear reference benches 41 s and 36 s there, against 12 s in the sandbox). One earlier full run failed test_api's first pool run (rc not ok); it passed alone and in the next full run and was not reproduced; the test now prints the reason. Bench with OpenModelica: all 31 pass. With OMCT_REF_NONLINEAR=1 (SLURM jobs) the reference comparison ran on all five nonlinear cases (covered: True, ok). OpenModelica against the closed forms: im_grid_slip w 6e-9, I 6e-7, T 1.3e-7; pmsm_current_torque T 1.6e-16, w 2e-5 (not settled); im_vf_start 3e-9; pmsm_damper_grid w 3e-11, T 3e-9; quadratic_load crossing 1.8e-6, w(2 s) 3e-7. Open: whether the nonlinear reference becomes the default.

**2026.10.21.23 (V3.3, sandbox, 2026-10-21; still behind OMCT_REF_NONLINEAR=1 until mochi)**: path (a) now covers the current-fed PMSM. (1) Late terms: a term whose arguments need u' is computed at the output points after the state (u' of the early terms from the joint solve), provided no late term drives the dynamics or needs its own derivative and no u'' appears (coefficients C0, C1, C2 of u, u', u'' in w'). (2) Orientation: when path (a) fails, the PMSM cores are rewritten for a current feed (u = R v_dq, i_dq = R^T i_L, `Ref(orient=...)`), keeping the chosen initial values; `_alt_orientation`. (3) The particular solution P of the hidden constraints is placed on the algebraic variables (column-pivoted QR with them weighted): the min-norm solution spread a constraint over the damper currents and made the dynamics depend on u''. (4) Speed: terms in dependency order (one pass when there is no loop), Jacobians only for the terms whose derivative is needed, central differences with h = 6e-6 (eps^(1/3); with 1e-7 the rounding noise made Radau take 9000 steps): the damper PMSM 64 s -> 1.1 s, im_vf_start 48 s -> 12 s. Results with the reference: im_grid_slip 1e-11, im_vf_start 8e-13, pmsm_current_torque T 5e-13 (w 2e-5: the asymptote, not yet reached at 3 s, as with OpenModelica), the damper version T 1e-11, pmsm_damper_grid (new) w 1e-10, T 7e-9, quadratic_load (new) 4e-7 (grid interpolation of the crossing); energy balance 1e-9 to 1e-11 (2.6e-4 on the grid case: trapezoids at 50 Hz). Stator voltage of the current-fed PMSM against the dq phasor: P 1e-10. bench.OMC_ONLY became omc_only() (read when called; tests clear the variable). tests/test_nonlinear.py (closed forms for both speed loads, the PMSM current drive with and without damper, the line-connected damper PMSM, IM energy, the flag, refnl on an index-1 DAE). 310 tests pass with the variable off and on. To check on mochi (OpenModelica + the reference comparison with the variable on), then decide the default.

**Later on 2026-10-21 (V3.3 still in progress, still behind OMCT_REF_NONLINEAR=1, not yet on mochi)**: fixed the coupling bug in path (a) (its rhs counted V w twice); the dynamics now also take u' when a term drives inductors (V/f inverter; joint solve with w'). With the flag on, im_grid_slip and im_vf_start pass against the reference to 1e-12 (18 s and 48 s). The speed-dependent loads are nonlinear terms now, using MSL's exact equations (quadratic; TorqueDirection = abs). The PMSM stamp gets an orientation per machine: "v" (voltage-fed, the default: v_dq = R^T u, i = R i_dq) or "c" (current-fed: u = R v_dq, i_dq = R^T i_L), with explicit v_d, v_q variables. Open: pmsm_current_torque. With orientation "v" the linear reduction fails because the voltages enter only nonlinear terms, and the DAE path stops ("step size went to zero", index-2 Newton). Next steps: (1) in _nl_ode, allow "late" terms, i.e. terms whose arguments need u' and which don't drive the dynamics (here the stator voltages); compute them after the state at each output point. (2) When path (a) fails with "v", rebuild with orient {pmsm: "c"} and delegate to it. (3) Test the flag on and off, pmsm_damper, then the mochi check and the decision on the default. With the flag off: 295 tests pass, and the behaviour is the same as 2026.10.21.21 (still unchanged).

**2026.10.21.21 (mochi results of V3.1/V3.2, 2026-10-21)**: `llmt test omct` 293 passed. Bench: three_phase_rl (I 5.5e-7, P 2e-7, Q 1.4e-6), im_grid_slip, pmsm_current_torque, im_vf_start pass with OpenModelica, energy balance ok on all (so the machines' storage terms are right); three_phase_delta failed the reference check only: Q is zero up to rounding (-1.4e-14 against 0) and was compared on its own size. Fix: outputs are compared on at least 1e-9 of the largest output of the same dimension. Probe C1-C6 answered (table).

**2026.10.21.20 (V3.2, sandbox, 2026-10-21)**: AC machines. Hidden elementary kinds im_core (IM_SquirrelCage), pmsm_core and pmsm_damper_core (SM_PermanentMagnet, useDamperCage false/true: the damper's heat port exists only with it, so two kinds), terminal_box (terminalConnection "Y"/"D"; starpoint may stay open), vf_controller, signal_ac_voltage/current, dq_to_three_phase; port types vin/vout (3-element signals, one signal net per element). Public composites induction_machine, pmsm, pmsm_damper (machine + terminal box; plug, flange, starpoint, 8 heat ports + group heatPort; the stator winding's heat ports are thermalPort.heatPortStatorWinding[k]), three_phase_grid, vf_inverter, pmsm_current_drive (as MSL's SMPM_CurrentSource: current into the machine from SignalCurrent.plug_n; the machine's starpoint must be grounded, as there, or the star potential is undetermined: found when the reference called the first version singular). Inits: is0 (3 phases), ir0 (2), w0, phi0 -> is(start, fixed={true, true, false}) with a floating star point. Reference: stand-ins with the same states (structure only, covered = False); benchmarks im_grid_slip, pmsm_current_torque, im_vf_start are OpenModelica-only (bench.OMC_ONLY) until V3.3. Energy: kinetic + 3/4 Lssigma |i_ss|^2 + 1/2 Lszero i_0_s^2 + 3/4 psi . i_m (airgap) + 3/4 Lr |ir|^2 - 3/2 Ie psi_md (the magnet, a current source on the rotor side), losses lossPowerTotal: derived from the MSL equations, unverified. 295 tests.

**2026.10.21.19 (V3.1, sandbox, 2026-10-21)**: Nicola chose for v3: induction machine + PMSM, plugs + ready drives, a nonlinear reference, quasi-static versions too. Three-phase plugs: PORT_TYPES['plug'] (phases 3); network keeps plug-level connections (the model text connects plugs) and joins phases pin by pin for nodes, islands and the unconnected check (`net.pins`, ports 'E.plug_p.pin[k]'; a star's pin_n may stay open). Kinds ac_voltage (SineVoltage, V = sqrt(2) V_rms per phase, phase - (k-1) 2pi/3), star, delta, plug_to_pin, ac_resistor/inductor/capacitor (vectors {x, x, x}; overrides name[k]), ac_current_rms, ac_voltage_rms (Polyphase quasi-RMS), ac_power (Machines.Sensors.ElectricalPowerSensor, P and Q from space phasors; m is a constant there, so no m=3 modifier). Per-phase initial values (I(..., phases=3)): candidates name[k], the model gets `i(start={...}, fixed={true, true, false})` (a floating star point makes one current dependent). Reference: per-pin stamps; quasi-RMS outputs as post-computed functions (`System.funcs`), P/Q as quadratic forms; covered only when those outputs drive no block. Benchmarks three_phase_rl and three_phase_delta pass with the reference (2e-13). 282 tests. Not run with OpenModelica: array overrides (R[1], i[1] start) and the per-phase fixed arrays are new.

**2026.10.21.18 on mochi (2026-10-21)**: rerun of the three: coolant_open_step (tau 8e-7, T 3.7e-9), dc_machine_losses (1.5e-13), resistor_self_heating (3e-7) pass: **all 13 v2 benchmarks and the chain pass with OpenModelica.** The probe report (tools/v2_report_mochi.txt) answers B1-B17 (table above): every value omct overrides is changeable; the media's values too (could become overridable); an open heat port or a partly connected machine thermal port does not build (omct prevents both); the IdealPump curve is exact. **Version 2 done.** Open: the probe's power-balance residual (B9, transient only), media at run time, a constant-speed source so the reference covers ideal_pump.

**2026.10.21.18 (mochi results of v2, 2026-10-21)**: `llmt test`: omct 270 passed, b3dt 107. `omct bench` of the 13 new cases with OpenModelica: 11 pass (coolant_loop_steady 2e-13, coolant_hydraulics exact, dc_machine_nominal, resistor_self_heating, damper_heating, dc_machine_heating 7e-9, limiter_loop, rectifier_mean, slew_rate_ramp, dc_machine_brush 1e-13, chain_b3dt_inertia 2.5e-10). Failed: (1) dc_machine_losses: OpenModelica ran it without losses on the build of dc_machine_nominal (same structure): the loss records' final parameters were compiled in (B18); the reference check caught it. Fix: PRef_*, wRef_*, VRef_core, power_w_friction structural, the default references written as literals when the loss is on. (2) coolant_open_step: tau 3.4e-4 off and the reference check failed: dassl at tolerance 1e-6 controls the absolute temperature (~300 K), 3e-4 K on a 4.8 K rise. Fixes: the reference and converge checks allow 10 x tol x max|y| (relative to the range) besides max(1e-4, 100 tol); the two temperature benchmarks run at tolerance 1e-8. Also: a fluid component's m is structural (it switches der(T) on). The probe report (tools/v2_report_mochi.txt) is still to come.

**2026.10.21.17 (V2.5, sandbox, 2026-10-21)**: events in the reference. Piecewise-linear kinds stamp per mode (`c.mode(default)`; the base system, used by validation, keeps the neutral stand-ins) and register guards (f z + c crossing 0 upwards or downwards -> the next mode): ideal_diode off/on with MSL's s-parametrisation (switches at v = Vknee in off and at i = Goff Vknee in on), limiter and dead_zone low/mid/high, slew_rate_limiter free/rising/falling on (u - y)/Td, lim_pid rewritten from the MSL source (P on wp u_s - u_m, D on wd u_s - u_m, I on u_s - u_m + (y - yu)/(k Ni): the old stand-in put wp on the integrator), the DC machine's brush (low/mid/high on ia). `Ref.mode_ref(modes)` builds a system per mode set (same variables, mapped to the base order), `_consistent` switches violated guards at the start and after each event (an event iteration), `_simulate_events` integrates with solve_ivp events (or brentq on the sources when there are no states), carries the storage values, records `switches`; products (losses) come from the active mode; steady states iterate modes; linearisation is refused (OpenModelica alone). Found: the brush's constant-drop loss V|ia| needed a constant variable 'one' in every mode (the mode systems must have the same variables). Checked against independent integrations of the MSL equations: rectifier with capacitor (1e-6 over two periods), LimPID windup (1e-6), dead zone exact; benchmarks limiter_loop (2.6e-7), rectifier_mean (2.9e-7), slew_rate_ramp (4e-13), dc_machine_brush (7e-14). 272 tests pass. **All five v2 items are implemented; none has run with OpenModelica yet.** Next on mochi: tools/v2_probe.sh (B1-B16), then `omct bench` of the 17 new cases and the chain.

**2026.10.21.16 (V2.4, sandbox, 2026-10-21)**: heat ports on resistor (now with T_ref 300.15 (MSL), alpha, T; vars R_actual, T_heatPort), ideal_diode, damper, spring_damper, rot_damper, rot_spring_damper (cond_ports, islands split so the heat port is its own domain), dc_motor (port group heatPort = Ra + friction) and dc_machine (the six, from V2.3). Reference: `Cascade` sources (flow(heatPort) = -loss as a time function): simulate runs once with them at 0 (the electrical/mechanical part does not see them), keeps the dense solution, then runs again with q(t) and q'(t) from it (z' = P F' + V w'); steady: the same with frozen values; exact only one way (alpha = 0) and when the thermal part needs no second derivative of q (checked on P and Gz). Energy: losses with a connected heat port are internal; the balance's scale is now the largest single term (a flywheel heating a capacitor sums to ~0 and gave error_rel 1). Benchmarks resistor_self_heating (5e-11), damper_heating (3e-15), dc_machine_heating (2e-9) pass with the reference; 263 tests. Not yet run with OpenModelica (B10-B14).

**2026.10.21.15 (V2.3, sandbox, 2026-10-21)**: `dc_machine` = MSL DC_PermanentMagnet. Parameters: the nominal data and the loss records as flat names (PRef_friction -> frictionParameters.PRef, ...; None = the MSL binding, e.g. wRef = wNominal); the model text groups record fields (`frictionParameters(PRef=..., power_w=...)`), the override file keeps dotted paths (`M.frictionParameters.PRef`: changeable? B2). Heat ports are conditional ports with one switch (useThermalPort): none, the group `heatPort` (one connection = six), or all six; some only is an error. Variables in records/subcomponents via `var_paths` (lossPowerTotal -> powerBalance.lossPowerTotal, Ta -> ra.T_heatPort). Reference: the MSL structure (strayLoad v = 0 in series, ra, X; la + airgap (vai = k w) parallel to the core conductance Gc = PRef/VRef^2; brush; rotor with friction torque PRef/wRef^2 w for power_w = 1); not covered: stray load, friction power_w != 1, brush drop (V2.5), heat ports (V2.4). Benchmarks dc_machine_nominal and dc_machine_losses pass with the reference (1e-7 and 1e-13); 259 tests. Not yet run with OpenModelica (B6-B11).

**2026.10.21.14 (V2.2, sandbox, 2026-10-21)**: coolant loops. Catalog: port type `fluid` (p, m_flow; h, H_flow), 12 kinds (pipe, valve, volume_flow, signal_volume_flow, pressure_increase, signal_pressure_increase, ideal_pump, ambient, pressure_reference, three sensors), MEDIA from the MSL sources, `cond_ports` (a port exists when connected; sets useHeatPort). Model text: one top-level `parameter ...Media.Medium omct_medium_<i> = ...Media.Water()` per distinct medium, `medium=omct_medium_<i>` on every fluid component (as the probe's models do: structural, so a medium change rebuilds); pipe laminar point defaults 0.1 V_flowNominal / 0.01 dpNominal (friction "linear": half the nominal point each, so k = 0). Validation: pressure reference per loop, one medium per loop, open fluid ports, the SimpleFriction assert (dpNominal >= dpLaminar/V_flowLaminar V_flowNominal). Reference: `ref.hydraulics` (Newton, central-difference Jacobian, on the MSL relations; constant sources; stand-ins and covered = False for signal-driven sources, flange-driven pumps and components without flow), then temperatures in the linear system (semiLinear by the known flow sign, H balance at junctions). Found: the constraint solution lost 5e-7 relative on flows (1e-5) next to enthalpies (1e6): rows are now scaled before the reduction and the constraint solve is repeated with columns scaled by the variables' sizes (exact to 1e-15). Energy: pipes and valves lose V dp - Q_friction, pumps put in -dp V, ambients -(H_flow + p m_flow/rho), storage m cv T. Benchmarks coolant_open_step (tau 3.3e-7), coolant_loop_steady (2e-13), coolant_hydraulics (1.4e-15) pass with the reference; 251 tests pass. **Not yet run with OpenModelica: needs B1-B5 from tools/v2_probe.sh, then `omct bench coolant_open_step coolant_loop_steady coolant_hydraulics` on mochi.** Next: V2.3.

**2026.10.21.13 (V2.1, sandbox, 2026-10-21)**: Nicola chose: inertia added to b3dt; DC machine thermal modes none/one/separate; heat ports switched on by connecting them; reference = piecewise-linear events + one-way electrical->thermal + constant-flow coolant loops. build123d-translator 2026.10.21.1 (in llm-translators/build123d-translator, for Nicola to commit): `translate.mass_properties`, `describe()` adds `center_of_mass` and `inertia_mm5` (so check_goals and build_parts return them); MCP docs; test `test_mass_properties_exact` (box turned 30° and moved, cylinder); 107 b3dt tests pass in the sandbox (build123d 0.12.0 installed there). omct: `chains.inertia_from_props` / `b3dt_inertia` (density x (n^T I n + V d^2) x 1e-15), study values `{"b3dt": <part result>, "density", "axis", "point"}` for kg and kg.m2 parameters (units.to_si), benchmark `chain_b3dt_inertia` (two discs, one about a parallel axis 30 mm off, on a torsion spring: passes with the reference and the real b3dt in the sandbox, J 2.5e-10, f 1.8e-10). 230 tests pass. `tools/v2_probe.sh` written (B1-B17). Next: the probe report from mochi, then V2.2.

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

## Version 2 (done 2026-10-21: all checked on mochi)

Nicola's order: (V2.1) rotational chains from build123d-translator moments of inertia; (V2.2) coolant loops with
Thermal.FluidHeatFlow; (V2.3) the library's DC machine with losses and a thermal port; (V2.4) heat from resistors and
dampers into the thermal network; (V2.5) the diode and limiters in the reference check. Each step: version bump,
tests, benchmarks with closed forms, README, this file. Facts below are from the MSL 4.0.0 sources (tag v4.0.0,
read in the sandbox); OpenModelica behaviour is unverified until `tools/v2_probe.sh` runs on mochi (B1-B17).

### V2.1 Rotational chains (build123d-translator inertia)

build123d-translator 2026.10.20.1 reports volume, area, bbox: **no centre of mass, no inertia**. build123d 0.12 has
`Shape.matrix_of_inertia` (OCCT `GProp_GProps.MatrixOfInertia`, unit density, about the centre of mass, mm^5) and
`center()`. Proposal: b3dt's `describe()` adds `center_of_mass` [mm] and `inertia_mm5` (3x3 about the centre of
mass, unit density; OCCT's sign of the products checked by B17). omct `chains.b3dt_inertia(goal, density, axis,
point)`: J = density x n^T I n x 1e-15 + m d^2 (parallel axes; d = distance from the centre of mass to the axis);
`{value, unit "kg.m2", from "b3dt", what}`. Benchmark `chain_b3dt_inertia`: a steel disc (r, h) on a torsion spring:
J = m r^2 / 2 exact, f = sqrt(c/J)/2pi.

### V2.2 Coolant loops (Modelica.Thermal.FluidHeatFlow)

MSL facts: connector FlowPort {p, flow m_flow, h, flow H_flow} with a `medium` record parameter (rho, cp, cv,
lambda, nu; final in the ports, so every port of a component shares its medium). TwoPort: mass m (0 = no storage),
T0, T0fixed, tapT; `m cv der(T) = H_a + H_b + Q_flow`, `H = semiLinear(m_flow, port.h, h)` (upwind, mixing at
nodes). Media: Water (30 degC), Water_10degC, Water_90degC, Air_30degC, Air_70degC, Gylcol20_20degC (sic),
Gylcol50_20degC, MineralOil. Pipe: SimpleFriction (laminar V_flowLaminar/dpLaminar, quadratic to
V_flowNominal/dpNominal; k computed at initialisation), h_g, frictionLoss, useHeatPort (heat port at T_q). Sources:
Ambient (p, T; inputs optional), AbsolutePressure (p only), VolumeFlow, PressureIncrease (constant or input),
IdealPump (rotational flange: dp = dp0 (w/wN)^2 (1 - V/(V0 w/wN))). Valve (opening y input), OneWayValve, OpenTank,
Cylinder; sensors (T, p, V_flow, m_flow, H_flow, relative T and p).

Kinds (new port type `fluid`: potential p, flow m_flow; h is carried by the connector): `pipe` (heat port when
connected), `valve`, `volume_flow`, `pressure_increase` (constant or signal), `ideal_pump` (flange), `ambient`,
`pressure_reference`, fluid sensors. Medium: one per loop, by name or {rho, cp, cv, lambda, nu}; the validator
checks every connected fluid port has the same medium and that each loop has a pressure reference (ambient or
pressure_reference), as it checks grounds. Energy balance: storage m cv T per pipe; heat ports, friction fed to the
medium, and the H_flow of ambients. Reference: the hydraulics solved first (algebraic; quadratic friction by Newton)
for constant flow and pressure sources, then the temperatures as a linear system with the known flows (upwind),
piecewise between source breakpoints; signal-driven pumps or valves, IdealPump driven by a machine: not covered.

### V2.3 DC machine (Electrical.Machines DC_PermanentMagnet)

MSL facts: nominal data VaNominal, IaNominal, wNominal, TaNominal, Ra (at TaRef), alpha20a, La, Jr, Js (useSupport),
TaOperational; k = ViNominal / wNominal with ViNominal = VaNominal - Ra(TaNominal) IaNominal - brush drop(IaNominal);
loss records frictionParameters (PRef, wRef = wNominal, power_w = 2), coreParameters (PRef, VRef = ViNominal, wRef),
strayLoadParameters (PRef, IRef = IaNominal, wRef, power_w = 1), brushParameters (V, ILinear = 0.01 IaNominal);
`powerBalance` record (powerArmature, powerMechanical, powerInertiaRotor/Stator, lossPower*). useThermalPort=true
gives thermalPort with heatPortArmature, Core, StrayLoad, Friction, Brush, PermanentMagnet; false keeps the armature
at TaOperational. Kind `dc_machine` (ports p, n, flange; heat ports by choice), outputs w, i, v, tau, the losses.
Energy balance: the machine's own powerBalance as a cross-check. Reference: exact when the losses are linear (no
stray load, friction power_w = 1, no alpha with a thermal port), brush drop by events (V2.5); otherwise not covered.

### V2.4 Heat ports on resistors and dampers

MSL facts: Resistor (R at T_ref, alpha; ConditionalHeatPort: useHeatPort, T fixed device temperature otherwise),
IdealDiode (ConditionalHeatPort), Translational/Rotational Damper and SpringDamper
(PartialElementaryConditionalHeatPortWithoutT: lossPower out of the port). Kinds get a `heat` port that turns
useHeatPort on when connected (resistor also T_ref, alpha, T); dc_motor's `heat` takes Ra and friction losses. The
energy balance moves those losses from "lost" to heat into the thermal network. Reference: electrical -> thermal
one way (alpha = 0): the electrical part first, then the thermal part with R i^2 as a known source; two-way
(alpha != 0) not covered.

### V2.5 Events in the reference (ideal diode, limiters)

Piecewise-linear kinds get one linear stamp per mode: ideal_diode (off: i = Goff v ... on: v = Ron i + Vknee ...,
MSL's s-parametrisation), limiter / dead_zone (low, mid, high), lim_pid (its limiter and the anti-windup path),
slew_rate_limiter (rate clipped), the DC machine's brushes. The reference integrates in a mode, finds the switching
times (solve_ivp events on the mode guards), carries the storage values across and continues in the new mode;
modes at the start from the consistent initial values. A Ref per mode set, built lazily. Covered = True when every
nonlinear element is piecewise linear; chattering (more than OMCT_REF_MAX_EVENTS switchings) gives covered = False.

### Assumptions for version 2 (tools/v2_probe.sh on mochi)

| # | Assumption | Status | Probe |
|---|---|---|---|
| B1 | FluidHeatFlow open loop (ambient, VolumeFlow, heated Pipe, ambient) builds and gives T_out = T_in + Q/(rho cp V) with tau = m cv/(m_flow cp) | **M ✓** probe: T_out 297.9593 K (expected 297.9593); with omct: coolant_open_step at tolerance 1e-8 (tau 8e-7; at 1e-6 the absolute temperature limits it to ~3e-4) | OpenLoop |
| B2 | Which fluid and machine parameters are changeable at run time (medium.*, pipe friction, flows; machine nominal data, loss records) | **M ✓** every parameter omct sets is changeable (only the protected friction k, computed at initialisation, is not); medium.* too. But see B18: final parameters of records are folded | isValueChangeable list |
| B3 | Overriding the medium's cp and the pump flow at run time takes effect | **M ✓** medium.cp = 2000 and the pump flow 2e-4 both take effect at run time (T 298.1719, expected 298.1721 at 60 s). omct keeps the medium structural (one record per loop); it could become overridable | OpenLoop ov |
| B4 | Closed loop with AbsolutePressure: steady temperatures; zero flow (Vf = 0 by override) still simulates | **M ✓** steady 298.1499 / 300.5545 K (coolant_loop_steady 2e-13 with omct); zero flow (Vf = 0 by override, warning 'small value or zero') runs: the heater rises 143.6 K in 300 s = Q t/(m cv), the pump (m = 0) causes no trouble | ClosedLoop |
| B5 | IdealPump at constant speed against a pipe: the operating point of the curves | **M ✓** the IdealPump operating point exact (V 2.31961289e-4 m3/s, dp 8401.93556 Pa); flange torque -dp V/w | PumpCurve |
| B6 | DC_PermanentMagnet builds with the MSL example data; final w = wNominal, ia = IaNominal at nominal load | **M ✓** (dc_machine_nominal: w 8.5e-9, ia 9.5e-8) | Dcpm |
| B7 | Variable names: dcpm.wMechanical, ia, va, tauElectrical, tauShaft, powerBalance.* | **M ✓** wMechanical, ia, va, tauElectrical, tauShaft, powerBalance.*, airGapDC.vai, ra.R_actual recorded; turnsRatio (protected) is not | Dcpm |
| B8 | Overriding VaNominal and Ra recomputes turnsRatio (k) | **M ✓** VaNominal 200 and Ra 0.1 by override: w 74.6128, ia 50, as k = (200 - 0.1·100)/wNominal (the final ViNominal binding is recomputed) | Dcpm ov |
| B9 | With losses the power balance closes (Parm = Pmech + Pinertia + Ploss) | **M ✓** at the end (Parm 10399.0 = Pmech 9479.6 + losses 919.4; core 49.8, stray 21.5, friction 99.4, brush 208.0 W); the probe's max residual over the run, 35 kW, was not looked into (omct's energy balance on dc_machine runs passes) | DcpmLosses |
| B10 | useThermalPort=true with all six heat ports connected; armature R follows the temperature | **M ✓** six ports on one capacitor: T 362.16 K at 600 s, Ra_actual 0.0635 Ω with copper alpha | DcpmThermal |
| B11 | useThermalPort=true with only one heat port connected: error, or the open ports take no heat | **M ✓ (an error)** only the armature port connected: 'An independent subset of the model has imbalanced number of equations (155) and variables (151)', build fails: omct requires all six (or the heatPort group) | DcpmThermalOne |
| B12 | Resistor useHeatPort with alpha: steady temperature from G (T - Ta) = V^2/R(T) | **M ✓** alpha = 0.004: T 311.01 K at 600 s, R_actual 10.71 Ω (steady 311.76); alpha = 0 with omct: resistor_self_heating | HeatPorts |
| B13 | Damper useHeatPort: d w^2 into the capacitor | **M ✓** (damper_heating 8.9e-8) | HeatPorts |
| B14 | useHeatPort=true with the port open: error or LossPower forced to 0 | **M ✓ (an error)** useHeatPort = true with the port open: 'IndexReduction.pantelidesIndexReduction failed! Found empty set of continuous equations': omct's heat ports exist only when connected | HeatPortOpen |
| B15 | IdealDiode rectifier: events, ripple, peak 10 - 0.7 V | **M ✓** c.v peak 9.299998 V, ripple min 1.987 V over the last cycle; rectifier_mean with omct 9e-7 | Rectifier |
| B16 | Limiter in a loop: saturation and recovery at the closed-form time | **M ✓** final 8.3333332 (50/6); limiter_loop with omct 2.9e-7 | LimLoop |
| B18 | Parameters set at run time reach every equation that uses them | **M ✗ for final parameters of records**: dc_machine_losses reused the lossless build (values only) and ran without losses (P_loss 500 W, not 666): FrictionParameters.tauRef, CoreParameters.GcRef etc. (`final parameter ... = f(PRef, wRef)`) were folded at compile time although PRef read back as set; the brush (BrushParameters.V used directly, an if-equation on it) did follow. Fix 2026.10.21.18: the loss records' inputs are structural and their references literals | mochi bench |
| B17 | build123d matrix_of_inertia: about the centre of mass, unit density mm^5; the sign of the products | **sandbox ✓, M ✓** (build123d 0.13.0 on mochi: the same matrices) | inertia.py; b3dt test_mass_properties_exact |

## Version 3: AC machines (proposal, 2026-10-21)

Nicola's choices: the squirrel-cage induction machine and the permanent-magnet synchronous machine; 3-phase plugs
and polyphase parts **and** ready-made drives; a **nonlinear reference** for the machines; the **quasi-static**
(phasor) versions as well. Order: V3.1 plugs and polyphase parts (linear: the reference covers them per pin);
V3.2 the transient machines (Electrical.Machines.BasicMachines, m = 3) and the drives, with closed-form benchmarks;
V3.3 the nonlinear reference; V3.4 the quasi-static machines (Magnetic.QuasiStatic.FundamentalWave). OpenModelica
behaviour: `tools/v3_probe.sh` (C1-C6), unverified until it runs.

MSL facts (sources, tag v4.0.0):
- Plugs: `Polyphase.Interfaces.Plug` = `pin[m]`; PositivePlug/NegativePlug; Star (plug_p, pin_n), Delta; sources
  SineVoltage (V[m] amplitudes, phase[m] = -symmetricOrientation, f[m], offset, startTime), SignalVoltage/SignalCurrent
  (v[m]/i[m] inputs); sensors Polyphase.Sensors.CurrentQuasiRMSSensor (I), Machines.Sensors.ElectricalPowerSensor
  (plug_p, plug_ni, plug_nv; P, Q from space phasors), VoltageQuasiRMSSensor, RotorDisplacementAngle.
- IM_SquirrelCage (extends PartialBasicInductionMachine): plug_sp, plug_sn, flange; p (Integer), fsNominal, Rs,
  TsRef, alpha20s, Lszero = Lssigma, Lssigma, Lm, Lrsigma, Rr, TrRef, alpha20r, TsOperational, TrOperational, Jr, Js,
  frictionParameters (wRef = 2 pi fsNominal/p), statorCoreParameters (m = 3, VRef, wRef = 2 pi fsNominal),
  strayLoadParameters (IRef, wRef); stator: rs (Polyphase.Basic.Resistor with heat port) -> spacePhasorS (2/3 Clarke
  + zero) -> lssigma, lszero -> AirGapS (psi_ms = Lm (i_ss + i_rs), rotation by gamma = p phi, tau = m/2 p (i x psi))
  -> SquirrelCage (Rr, Lrsigma in the rotor frame). Outputs is[3], ir[2], powerBalance (powerStator, powerMechanical,
  lossPowerStatorWinding, ...Core, StrayLoad, Friction, RotorWinding). Data record IM_SquirrelCageData: p 2, 50 Hz,
  Rs 0.03, Rr 0.04, Lm = 3 sqrt(1 - 0.0667)/(2 pi 50), Lssigma = Lrsigma = 3 (1 - sqrt(1 - 0.0667))/(2 pi 50), Jr 0.29.
- SM_PermanentMagnet (same partial): VsOpenCircuit (RMS per phase at fsNominal), Lmd, Lmq, useDamperCage, Lrsigmad,
  Lrsigmaq, Rrd, Rrq, TrRef, alpha20r, permanentMagnetLossParameters; AirGapR (rotor frame, L = diag(Lmd, Lmq)),
  PermanentMagnet i_ = {-Ie, 0} with Ie = sqrt(2) VsOpenCircuit / (Lmd 2 pi fsNominal) (final). Data: VsOpenCircuit
  112.3 V, Lmd = Lmq = 0.3/(2 pi 50), Lssigma 0.1/(2 pi 50).
- Thermal ports (useThermalPort): IMC: heatPortStatorWinding, StatorCore, RotorCore, StrayLoad, Friction,
  RotorWinding; SMPM adds PermanentMagnet (RotorWinding only with the damper cage).
- TerminalBox (terminalConnection "Y" | "D"; plug_sp, plug_sn, plugSupply, starpoint if Y). VfController (u = f [Hz],
  VNominal RMS, fNominal; y[m] = sqrt(2) VNominal min(|f|/fNominal, 1) sin(x + orientation), der(x) = 2 pi f).
  DQToThreePhase (d, q RMS inputs, phi = mechanical angle; p). DQCurrentController (FOC voltages, PI on Rs, Ld, Lq).

Design:
- Port type `plug` (3 pins). Network: a plug node is three pin nodes (phase k of every plug at the node joins);
  Modelica `connect` on plugs; the reference stamps per phase. Ground rule per phase island (a star to a ground).
- Kinds (V3.1): `ac_voltage` (SineVoltage: V_rms phase voltage, f, phase), `signal_ac_voltage`/`signal_ac_current`
  (inside drives only: vector signals), `star`, `delta`, `ac_resistor`, `ac_inductor`, `ac_capacitor` (Polyphase.Basic,
  equal per phase), `ac_current_rms`, `ac_voltage_rms` and `ac_power` sensors (outputs I, V, P, Q).
- Machines (V3.2): `induction_machine` and `pmsm` = composites of the MSL machine and a TerminalBox (`terminal`: Y|D,
  structural): ports `plug` (supply), `flange`, heat ports (group `heatPort` or the six/seven) as for dc_machine; loss
  records structural (B18); outputs wMechanical, tauElectrical, tauShaft, is_rms, the power balance; init w0, phi0
  (stator and rotor currents start at zero: initial equations as in the MSL examples).
- Drives: `three_phase_grid` (SineVoltage + Star + Ground: port plug; V_rms, f), `vf_inverter` (VfController +
  SignalVoltage + Star + Ground: input f [Hz], VNominal, fNominal: port plug), `pmsm_current_drive` (SignalCurrent +
  Star + Ground + DQToThreePhase + AngleSensor: inputs id, iq [A RMS], port plug and `angle` flange to the motor
  shaft; ideal FOC as MSL's SMPM_CurrentSource).
- Checks: energy balance from the machines' storage (stator/rotor inductances, inertia) and powerBalance; the
  reference: V3.3 adds a nonlinear integrator (implicit, BDF on the residual) with the machines written
  independently in dq / alpha-beta (not MSL's structure); until then 'not covered'. Benchmarks: IM slip and current
  from the equivalent circuit (C3); PMSM torque 3/2 p psiM sqrt(2) Iq and the stator voltage (C4); a V/f start (C5).
- V3.4: QS machines (complex phasor plugs, reference angles: Connections.root in the QS sources): `qs_*` kinds with
  their own plug type `qs_plug`, QS grid/current sources, the same benchmarks (no ripple), the reference as complex
  algebra plus the mechanics.

### V3.4 quasi-static machines (design, 2026-10-21)

Nicola's choices: machines + drives + sensors (no QS passives yet); a separate port type `qs_plug` (and `qs_pin`):
QS and transient plugs never join; mechanics, heat and signals are shared.

MSL facts (Magnetic.QuasiStatic.FundamentalWave, Electrical.QuasiStatic; tag v4.0.0), phasors RMS, x(t) =
Re(sqrt(2) X e^(j gamma)) with gamma the reference angle of the connector:
- Sources are Connections.root: VoltageSource (V[m] RMS, phi[m] = -symmetricOrientation, f; gamma(start=0) a state,
  omega = der(gamma) = 2 pi f), VariableVoltageSource (inputs f, V[m] complex), ReferenceCurrentSource (inputs
  gamma, I[m]: plug reference gamma = the input). Ground: potentialRoot, v = 0. Star, TerminalBox (Y: starpoint pin,
  D: Delta). Sensors: CurrentQuasiRMSSensor I and VoltageQuasiRMSSensor V = mean_k |x_k| (quasiRMS is the mean of
  the magnitudes), PowerSensor apparentPower = sum v_k conj(i_k) (currentP/N, voltageP/N).
- Machine (BaseClasses.Machine): no Lszero; stator SymmetricPolyphaseWinding = resistor (R_k) -> converter;
  converter: i's non-positive symmetrical components = 0 (m = 3: zero and negative sequence), V_m = sqrt(2) (2/pi)
  (m/2) N I_pos, -sqrt(2) V_pos = conj(N) j omega Phi (I_pos = 1/3 sum I_k e^(j phi_k), phi_k = (k-1) 2 pi/3); core
  EddyCurrent G = m/2 GcRef in series with the converter's flux path, stray permeance 2 Lsigma/m parallel to it.
  RotorSaliencyAirGap: Phi_s = Phi_r, (pi/2)(V_ms + V_mr) e^(j gammar) = (R_d, R_q) . Phi e^(j gammar), R = m/(2 Lm),
  tau = -(pi p/2)(Phi.im V_ms.re - Phi.re V_ms.im), gammas = gammar + p phi. Cage (SymmetricPolyphaseCageWinding):
  converter parallel to its resistor, stray reluctance m/(2 Lrsigma). PMSM: PermanentMagnet MMF V_mPM e^(-j gammar)
  in series with the rotor (with the damper: SaliencyCageWinding, m = 2, N = sqrt(3/2), stray reluctances d/q on
  the rotor-reference phasor). Losses: friction as transient; StrayLoad and PermanentMagnetLosses with iRMS = mean
  |I_k|; core loss 3 Gc |E_pos|^2.
- Derived here (independent of MSL's structure; checked against OpenModelica on mochi): with I_L = I_pos - Gc E_pos
  (stray current), psi = flux linkage (RMS): E_pos = j omega_s (Lssigma I_L + psi); rotor frame (x e^(j gammar)):
  psi_d = Lmd (I_L + I_x)_d (+ Ie), psi_q = Lmq (I_L + I_x)_q; IM: I_x = I_r, Rr I_r + j omega_r (Lrsigma I_r + psi) = 0;
  PMSM: Ie = VsOpenCircuit / (Lmd 2 pi fsNominal) on d; damper I_D' = sqrt(2/3) I_D: (Rrd + Rrq)/2 I_D' + j omega_r
  ((Lrsigmad, Lrsigmaq) . I_D' + psi) = 0; omega_r = omega_s - p w; tau = 3 p (psi_re I_L,im - psi_im I_L,re).
- Kinds: qs_ground, qs_star, qs_ac_voltage, qs_current_rms, qs_voltage_rms, qs_power; composites qs_grid,
  qs_vf_inverter, qs_pmsm_current_drive, qs_induction_machine, qs_pmsm, qs_pmsm_damper (machine + QS terminal box;
  terminal Y grounds the machine's star point inside: with the zero-sequence current forced to 0 a floating QS
  star is singular). Inits: w0, phi0 (machine), gamma0 (the source's reference angle; fixed). Checks: each QS
  circuit has a qs_ground and exactly one QS source (it sets the frequency and the reference angle).
- Reference (refqs.py): complex quantities as (re, im) pairs; the machines' products with omega, the rotation by
  gammar and the torque are nonlinear terms (V3.3 path (a)); QS electrical variables are algebraic, the states are
  the source angle, phi, w (and temperatures).
- Benchmarks: the transient ones in QS form (qs_im_grid_slip, qs_pmsm_current_torque, qs_im_vf_start,
  qs_pmsm_damper_grid): the same closed forms (no electrical transients, no ripple).

| # | Assumption (V3.4) | Status | Probe |
|---|---|---|---|
| D1 | omct's QS models (grid + machine with the star grounded inside the composite, source `gamma(fixed=true)`, `phiMechanical(fixed=true)`) build, initialise and run in OpenModelica 1.27.1 | **M ✓** all six new benchmarks build, initialise and run (mochi, 2026.10.21.26) | the qs_* benchmarks |
| D2 | QS values are changeable at run time (VoltageSource V[k], phi[k], f; machine Rs, Lm, ...; L0 is a final binding) | **M ✓** every override read back (checks.overrides true) | overrides read back in the qs_* benchmarks |
| D3 | Complex outputs record as `x.apparentPower.re` / `.im` in the MAT file | **M ✓** P read and right (1.4e-10 against the equivalent circuit) | qs_power in qs_im_grid_slip |
| D4 | The phasor equations derived above give OpenModelica's QS machine to 1e-6 (reference comparison) | **M ✓** reference check passed on all four QS benchmarks and both delta cases | reference check in the qs_* benchmarks |

### Assumptions for version 3 (tools/v3_probe.sh on mochi)

| # | Assumption | Status | Probe |
|---|---|---|---|
| C1 | The MSL examples (IMC_DOL, IMC_Inverter, SMPM_CurrentSource, SMPM_VoltageSource, QS IMC_DOL, QS SMPM_CurrentSource) build and run in OpenModelica 1.27.1 | **M ✓** all six MSL examples build (whole probe 14 s with -n=4) and run (0.1-0.3 s each); the probe's PmsmCurrent with the machine's star point floating fails: 'pantelidesIndexReduction failed! System is structurally singular' (as the reference found: omct requires the starpoint grounded with a current drive) | build, runs |
| C2 | The machine parameters omct sets are changeable at run time (Rs, Rr, Lm, ...; Lm enters a protected matrix L) | **M ✓** every machine parameter is changeable (none printed false; loss records structural anyway, B18) | isValueChangeable |
| C3 | IM on a 100 V 50 Hz grid at 100 N.m: slip 0.0234983, 153.3885 rad/s, Is 66.006 A RMS (equivalent circuit) | **M ✓** speed 153.38853 (expected 153.38853), torque 99.9999, Is 66.0063 A (66.0064); im_grid_slip with omct: 6e-9, 6e-7 | ImGrid |
| C4 | Current-fed PMSM (Id -53.5, Iq 84.6 A RMS): torque 3/2 p psiM sqrt(2) Iq; the stator RMS voltage from dq | **M ✓** with omct (pmsm_current_torque): torque exact (1.6e-16), speed 2e-5 | PmsmCurrent |
| C5 | V/f start, then 100 N.m: the C3 speed | **M ✓** 153.38854 (3e-9 with omct: im_vf_start) | ImVf |
| C6 | Run time per simulated second at 50 Hz (steps per period) | **M ✓** about 1000-1800 steps per simulated 1.5 s at 50 Hz (0.1-0.15 s wall); quasi-static IMC_DOL 3800 steps, 0.3 s | runs |

## Open ideas

- Induction machines with `Machines` quasi-static models; Modelica.Fluid with cfdt pump/pipe tables.
- FMU export for co-simulation; parameter estimation from measured data (fit k, J, d).
- Event-driven stops (`crossing` that ends the run), frequency sweeps by simulation for nonlinear systems.
