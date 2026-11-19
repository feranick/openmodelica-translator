# openmodelica-translator: context for a new session

Session context for Claude (the user documentation is README.md). Start a chat with: "Read planning.md and start."

Version: 2026.11.19.9 (2026-11-19). Done: M0-M8 (v1), V2 (coolant, DC machine, heat ports, events), V3 (AC
machines, transient and quasi-static, nonlinear reference), the fixes from use since (docs/history.md), and the
move onto **llmt-runtime** (2026.11.09.1-.3: workers, SLURM layer and MCP server common code are the runtime's).

## What this is

A deterministic **translator + checker** for system-level dynamics with OpenModelica and the Modelica Standard
Library (MSL 4.0.0):

1. **The LLM writes a study:** components from a curated MSL catalog with parameters, the connections between their
   ports, the outputs wanted, the analysis and expected values; or its own Modelica text (`modelica`, the escape
   hatch).
2. **The translator does the rest:** writes the Modelica model, checks it, compiles it once (cached by structure),
   runs it with values set by override files (no recompiling), reads the results, computes engineering quantities.
3. **A checker confirms each run:** balance, initialisation, solver, events, overrides read back, energy balance, an
   independent reference implementation (catalog studies) or a second integrator (custom models), declared
   invariants, optional convergence, `expect`.
4. **Errors come back as fixes.**

It complements the other translators: they compute fields in one part; this one simulates a whole system over time
(circuits, drives and machines, mechanics, heat paths and coolant loops, controllers) and takes their results as
lumped parameters.

| Key | Repository | Backend |
|---|---|---|
| `b3dt` | build123d-translator | build123d (OpenCascade) |
| `feat` | fea-translator | Gmsh + CalculiX |
| `cfdt` | cfd-translator | Gmsh + OpenFOAM |
| `lmpt` | lammps-translator | LAMMPS |
| **`omct`** | **openmodelica-translator** | **OpenModelica 1.27.2 + MSL 4.0.0** |

### Names

| Thing | Value |
|---|---|
| Repository | `openmodelica-translator` (github.com/feranick/openmodelica-translator) |
| Package | `openmodelica_translator` |
| Key = CLI | `omct` |
| MCP server | `omct-mcp` |
| Settings prefix | `OMCT_` |
| HTTP port | 8770 |
| Output folder | `~/omct_runs` (`OMCT_OUT`); server logs in `$OMCT_OUT/.server/` |
| Build cache | `~/omct_runs/.cache/<hash>/` (`OMCT_CACHE`) |

## Current state (2026-11-19)

- **Machines:** carbonio, mochi, spark1, spark2, all shared installs of llm-translators
  (`/opt/llm-translators/current`), all with OpenModelica 1.27.2 and MSL 4.0.0 (exact), all SLURM. The sparks are
  one two-node SLURM cluster (`flash-dgx`: spark-5da5 = spark1, controller; spark-0808 = spark2; Tailscale
  addresses; home folders not shared). The Mac reaches omct through llm-translators' router.
- **Coverage:** 123 public catalog kinds (142 with hidden parts) in 14 port types: analog circuits, polyphase
  plugs, transient and quasi-static induction machines and PMSMs with drives, DC motor and MSL DC machine with
  losses, translational and rotational mechanics, heat transfer, FluidHeatFlow coolant loops, blocks and
  controllers. Analyses transient, steady, linearize; sweeps on one build.
- **Checked:** 37 benchmarks with closed forms pass with OpenModelica 1.27.2 (carbonio, 2026-10-31; the same with
  1.27.1 on mochi before). SLURM, queueing, reconnecting and the sweep limits checked on carbonio and
  the sparks (2026-11-19, see Open items).
- **Runtime:** omct runs on llmt-runtime (`llmt_runtime`, a submodule of llm-translators at
  `~/Software/llmt-runtime`; pinned 2026.11.19.2; omct declares the floor `llmt-runtime>=2026.11.19.2`). Read its
  `README.md` (API, hooks, settings) and `PLANNING.md` (open items) before touching SLURM, jobs, memory, workers,
  requests or the MCP server's common code.
- **Code:** 28 modules (`runtime.py`, `workers.py` thin), about 12,300 lines (`ref.py` alone ~3,000); 471 tests pass
  in the sandbox with the runtime (6 skipped: the real-omc tests). MCP tools: 13 (`extend` since 2026.11.19.5).

## Open items

- **Machine tests** (llmt-runtime PLANNING.md, section 1; 2026-11-19, omct 2026.11.18.3 on llmt-runtime 2026.11.18.5
  everywhere):
  - rc_step as a SLURM job: carbonio ✓ (job 5924: crossing 0.9999967 ms for 1 ms, reference 2.8e-6), spark1 ✓ (709),
    spark2 ✓ (715, after the fix below), mochi not run (40 of 40 CPUs busy, start expected in ~12 h).
  - Two at once: two 120-point sweeps on spark1, each `run.mem: 70G` of the node's 121 GB, sent together: the first
    ran (job 716, 15 s, 120/120 reliable), the second queued with reason Resources (718) and started after it ✓.
  - Collected after the MCP connection was closed and reopened ✓: after an app restart, jobs() from the new servers
    reports the second sweep (run_20261119_092619_dccce5, job 718) done, ok, reliable.
  - After deploying 2026.11.19.4 (runtime 2026.11.19.2, all four machines, 2026-11-19 10:15): a 120-point sweep on
    spark1 (job 724) got time_min 250 (was 1210), each point timeout_s 120 (`time_from: sweep points (measured)`),
    ran_on spark-5da5; while running it showed its stage and eta_min_max (0.3 min) instead of "at most ? min"; jobs()
    through list_jobs listed local-pool requests (job_id null), SLURM ones and a bench_* request (kind, collect).
  - The router's start estimate on spark2 (too high: "95 min", "183 min") is right after llm-translators' fixes:
    "4 CPUs free on node spark-0808 in 50 min, when its running jobs end" (2026-11-19 10:15).
  - Left: mochi (rc_step as a SLURM job, when its queue has room).
  - Fixed on the way: both sparks had `OMCT_SLURM_ARGS="--mem=16G --nodelist=spark-5da5"` in /etc/llm-translators/env
    (a stopgap from before OMCT_SLURM_NODE), so spark2's jobs ran on spark1, where spark2's run folder does not exist
    (job 711: "left the queue without starting (no job.log)"). Removed on both (2026-11-19); OMCT_SLURM_NODE=local now
    pins spark1 to spark-5da5 and spark2 to spark-0808. The router's start estimate on spark1 ("95 min" while idle)
    became "starts now" with it. The other translators' `--nodelist` on the sparks name each machine's own node.
## Interconnections

- **From feat:** static stiffness k = applied force / displacement (feat reports `applied_force` N and
  `probes.<p>.mean_u.<c>` mm, so k [N/m] = F / (u · 1e-3)); modal `frequencies` (Hz) and `mass_kg`; thermal
  conductance G = `heat_flow.<face>` / ΔT and capacitance m · c. Benchmark `chain_feat_modal`.
- **From b3dt:** mass = volume (mm³ · 1e-9) × density; moments of inertia from `inertia_mm5` (about the centre of
  mass, unit density): J = density · nᵀ I n · 1e-15 + m d² (`chains.py`; B17). Benchmarks `chain_b3dt_mass`,
  `chain_b3dt_inertia`.
- **From cfdt:** Δp against Q and pump curves, as `table` blocks or the coolant kinds' parameters.
- **From lmpt:** material properties (`feat_material`: E, nu, density, alpha).
- **Chain inputs are values, never paths:** `{"value": 1234, "unit": "N/mm", "from": "feat", "what": "..."}`; the
  conversion and provenance go into the result (`inputs`).
- **Its own interface keys** (stable, listed in the README): `outputs.<name>.{final,min,max,...,series}`,
  `derived.<name>.*`, `steady.<name>`, `linearization.{eigenvalues, modes, dc_gain, bode}`, `sweep.table`.
- Imports of other translators are optional: chain benchmarks skip without them. Servers may run on different
  machines: tools pass specs, `get_file` returns base64.

## Conventions

- Versions `YYYY.MM.DD.x` in `__init__.py`, `pyproject.toml`, every changed module's docstring (`Version: ...`)
  and the README's version table, which has one line per version.
- Author: Nicola Ferralis <ferralis@generalflash.com>. Nicola commits and pushes from the Mac.
- **Units: SI, as Modelica** (K, rad/s, m, kg, N·m). This differs from feat/cfdt (mm, MPa, °C), so every non-SI value
  is converted explicitly and recorded.
- Dotted result paths; JSON in/out; result files written atomically.
- Python ≥ 3.10; dependencies numpy, scipy, matplotlib; extras `[mcp, test]`; entry points `omct`, `omct-mcp`;
  build with setuptools ≥ 77 (SPDX `license = "MIT"`).
- Answers concise, commands ready to paste with how long they take. Ask before major decisions. Don't guess
  OpenModelica's behaviour: mark it unverified, give a probe, record the outcome here.
- Git: no `git` commands that write in the connected folders from the sandbox (it leaves `.git/index.lock`);
  read-only ones with `--no-optional-locks` are fine. llm-translators is changed only when Nicola asks: he updates
  it himself after the translators.
- **llmt-runtime rules** (llmt-runtime's TRANSLATOR_CHATS.md, common part):
  - Shared behaviour (SLURM, job states, memory and out of memory, workers, requests, the MCP server's common code)
    is fixed once, in llmt-runtime, never in omct's wrappers; runtime changes are additions only, with its own
    version bump, test and tag.
  - Floors only: `llmt-runtime>=X` in `pyproject.toml`, `requirements-standalone.txt`'s git tag and the README
    install line; raise it only when omct needs something newer; never an upper bound.
  - Pin order: push the runtime, then omct, then in llm-translators `./llmt pin runtime omct`.
  - What stays omct's: sizing (CPUs, sweep points and rounds), estimates, `run_errors` / `caps`, the sweep pools,
    `run_study_job`, failures written as `<name>.json`, `jobs()`'s shape, the advice texts given through hooks.
  - Tests use the runtime's fake SLURM (`llmt_runtime.testing.fake_slurm`, `FAKE_*`); patch the runtime's names
    where its code runs (`llmt_runtime.slurm.cluster_nodes`, `.addresses`, `.forget()`); a test of the MCP server's
    answers pins `GUARD.stale` to None.
  - A file that becomes a stub: `git rm -f`.

## Environment

- **Mac** (Apple Silicon): development, the Claude desktop app, commits. No OpenModelica.
- **carbonio, mochi, spark1, spark2:** shared installs; the router (`~/Software/llm-translators/bin/router omct` on
  the Mac) places each call on one of them (`run.host` chooses). mochi: 40 hardware threads, 61.5 GB; carbonio: 12
  CPUs, also has MSL 3.2.3 and 4.1.0 installed (hence `requireExactVersion`, A20); sparks: 20 CPUs each, aarch64.
- **Deploying a new omct version:**
  1. Mac: commit and push openmodelica-translator (and llmt-runtime first, when it changed), then in
     llm-translators `./llmt pin runtime omct && git add -A && git commit -m "Pin ..." && git push` (Nicola).
     `llmt deploy` refuses a build whose omct floor is above the pinned runtime.
  1a. Before users get it (on a server, in tmux): `sudo ./llmt deploy --no-switch`, then
     `R=/opt/llm-translators/releases/$(ls /opt/llm-translators/releases | sort -V | tail -1); $R/llmt test runtime omct`,
     the MCP tool list (`( printf '<initialize>\n<initialized>\n<tools/list>\n'; sleep 30 ) | $R/bin/mcp omct`),
     then `sudo ./llmt rollback $(basename $R)` to switch.
  2. On each machine:
     `cd /opt/llm-translators/src && sudo git pull && sudo git submodule update --init && sudo ./llmt deploy --test`
     (the tests run from the new release before `current` switches), then `/opt/llm-translators/current/llmt
     status omct`.
  3. Running servers notice the new release (every `OMCT_STALE_CHECK_S`, 10 s) and exit when idle; the router
     starts new ones. `machines()` shows `runtime` and `runtime_commit` too.
  4. Check from the Mac: `./llmt router status omct`, or `machines()`: `version` and `serving` are omct's version;
     `llmt_release` is the llm-translators bundle.
- **Sandbox** (aarch64 Ubuntu 22.04, no sudo): `/sessions` is a shared disk that fills up (then tests fail with
  odd errors): run them with `TMPDIR=/tmp`. llmt-runtime is visible through a `.pth` file in the user site-packages
  (`llmt_runtime_dev.pth` -> `mnt/llm-translators/llmt-runtime`), which the SLURM tests' subprocesses need (they
  clear `PYTHONPATH`, as on the machines); the MSL sources are in `/tmp/keep/msl` (symlink `~/msl`). No OpenModelica (the proxy blocks build.openmodelica.org).
  PyPI and github.com work. Background processes die when the shell call ends. The backend driver is developed
  against stored mochi output (fixtures) and fakes (`tests/fake_omc.py`, `tests/fake_slurm.py`); `ref.py` runs here;
  anything with OpenModelica runs on the machines through the omct MCP tools.

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

`tools/m0_probe.sh` answered them on mochi on 2026-10-21 (`tools/m0_report_mochi.txt`) with OpenModelica 1.27.1; the 37 benchmarks and A22 recheck them on 1.27.2. Status: S = read in the source, M = measured on the machines, ? = unverified.

| # | Assumption | Status | Caught by |
|---|---|---|---|
| A1 | `loadModel(Modelica, {"4.0.0"})` loads `4.0.0+maint.om` | **M ✓** | returns true; `getVersion(Modelica)` = "4.0.0" |
| A2 | `getLoadedLibraries()` gives the MSL path | **M ✓** | `{{"Modelica", "/home/nicola/.openmodelica/libraries/Modelica 4.0.0+maint.om"}, {"Complex", ...}, {"ModelicaServices", ...}}` |
| A3 | checkModel's message format | **M ✓**, but: **checkModel does not fail on an unbalanced model** (`Under`: "completed successfully ... 1 equation(s) and 2 variable(s)"): we compare the counts ourselves | probe A3; fixtures |
| A4 | Error formats | **M ✓** `getErrorString()`: `[<file>:<l0>:<c0>-<l1>:<c1>:writable] Error: Class ... not found in scope BadClass.`; an unknown modifier makes checkModel return "" with `Modified element Rx not found in class Resistor.` (id 214). `getMessagesStringInternal()` consumes the messages (getErrorString is then ""). We use getErrorString and map the line to the declaration | fixtures |
| A5 | A model without ground passes checkModel and fails at build | **M ✓** checkModel passes (21 = 21); buildModel returns `{"", ""}` with `Internal error Circular Equalities Detected ... IndexReduction.pantelidesIndexReduction failed!`: our validation must catch it (it does) | test_format |
| A6 | `_init.xml` has `isValueChangeable` and the variable filter | **M ✓** omct reads `isValueChangeable` from every build (variants for compiled-in values, B18); a parameter whose default is an expression is marked false, also two levels deep (1.27.2, carbonio, 2026-10-31) | omc.read_init; the probe study `Probe.i.d.b = 2*a` |
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
| A18 | MSL sign conventions (flow into the port positive; LossPower ≥ 0) | **M ✓** the energy balance closes on every benchmark (all domains) | benchmarks + energy balance |
| A19 | dassl at tolerance 1e-6 reproduces closed forms to ~1e-4 relative on the output grid | **M ✓ with a limit**: the benchmarks agree to 1e-4 ... 1e-16; on 6-hour motor-heating runs DASSL's global error reached ~1e-4 of an output's range (8.5e-5 at 1e-6, 4.5e-6 at 1e-8; 2026-10-26): hence the reference retry at tolerance/100 | benchmarks; heating sweeps |
| A20 | `loadModel(Modelica, {"4.0.0"}, requireExactVersion=true)` loads 4.0.0(+maint.om) even when newer MSL versions are installed; without it omc takes the newest compatible one | **carbonio ✓** (plain: 4.1.0; exact: 4.0.0) | tools probe 2026-10-22 |
| A21 | The compiled model reads an empty `-overrideFile` | **✗** on 1.27.1 (user report, 2026-10-23) and on 1.27.2 (spark1, 2026-11-19: `LOG_ASSERT | debug | simulation_input_xml.c: could not read overrideFile empty.txt: Success`, "Execution failed!", exit 1); omct omits the flag when there are no values | escape-hatch runs; probe on spark1 |
| A22 | OpenModelica 1.27.2 behaves as 1.27.1 for everything omct uses | **M ✓** all 37 benchmarks pass on carbonio with the same numbers (bench_20261031_144830_0febf2); custom models with IDA and invariants run on all four machines | benchmarks, 2026-10-31 |

Other facts from the probe (2026-10-21): mochi kernel 7.0.0, Ubuntu "resolute", gcc 15.2.0; apt source `deb [arch=amd64 signed-by=/usr/share/keyrings/openmodelica-keyring.gpg] https://build.openmodelica.org/apt resolute stable` (for BACKENDS.md). One build of the probe RLC: about 1 s inside a 6-build omc script (6.25 s wall, 27 s CPU). A run of the RLC (2000 intervals): 0.08 s wall. A build folder keeps ~60 files, 476 kB; the cache keeps 6.

## The study format (as built; `study_format()` is the authority)

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
 "analysis": {"kind": "transient", "stop_time": 0.05, "intervals": 2000},
 "derived": [{"kind": "step", "signal": "v_C"}],
 "expect": [{"quantity": "derived.v_C.final", "value": 12, "tol": 1e-3}]
}
```

| Key | Meaning |
|---|---|
| `components` | `{name: {kind, <parameters>, <initial values>}}`; names are identifiers, no `__`, not starting with `omct`. Parameters are MSL's names plus convenience ones (`v0`, `i0`, `w0`, `T0`; `C` or `m` + `cp`; `G` or `k` + `A` + `L`; `Gc` or `h` + `A`; flat loss-record names such as `PRef_friction`). Omitted ones take MSL's default (or ours where MSL has none). |
| units | A number is SI; `"10 mH"`, `"80 degC"`, `"3000 rpm"` are converted; `{value, unit, from, what}` adds provenance (chains); `{"b3dt": ...}` takes a build123d-translator result. Every conversion is listed in `inputs`. |
| `connect` | Pairs `"comp.port"`; a component with one port of the needed type may be named alone. Physical ports join the same port type; signals connect output → inputs. |
| `outputs` | `{name: "comp.variable"}` or `"comp.port.variable"` from each kind's variables. |
| `analysis` | `transient` {stop_time, start_time 0, intervals 500, tolerance 1e-6, method dassl \| ida}; `steady` {max_time, band 1e-4, ...} (steady state detected by omct, A12); `linearize` {at, inputs, bode}. |
| `derived` | `step` (rise, overshoot, peak, settling, final, steady-state error), `crossing` (level, direction), `window` (mean, rms, ac_rms, min, max, p2p over [t0, t1]), `steady` (value, time). |
| `sweep` | `{params, mode: grid \| zip, report}`: one build, one run per point → `sweep.table`, `sweep.checks`. Only values changeable at run time (catalog-structural ones rebuild a variant). |
| `converge` | `true` or `{abs_tol: {output: value}}`: a rerun at tolerance/10 (below). |
| `invariants` | `[{signal, kind: zero (abs) \| constant (abs \| rel), name?}]`: balances the model must keep. |
| `cross_check` | custom models only (default true): the second run with the other integrator; `false` skips it. |
| `expect` | `[{quantity, value, tol (relative) \| abs}]` on dotted result paths: sets `passed`, not `reliable`. |
| `run` | `{timeout_s, mem, procs, queue}` within the machine's caps (`OMCT_MAX_*`); the router also reads `host`. |
| `images` | `true`: time plots, Bode plots, sweep plots (PNG; `get_image`, `render_images`). |
| `modelica` | Escape hatch `{text, model, values}`: the study's own Modelica; outputs, values and sweep parameters are Modelica paths; no catalog checks and no reference (the cross-solver check and invariants instead). |

**Validation before anything runs** (each error says the fix): kinds, parameters, ranges and units; ports, port
types, connections; every signal input driven once; a ground per electrical island, a reference per QS circuit
(one QS source each), a pressure reference and one medium per coolant loop, temperature boundaries; heat ports of
a machine all or none; outputs exist; sweep values exist and can change at run time; for custom models, values and
sweep parameters whose default is an expression (also from the cached build's record); the reference system
(singular models, initial values that are not free); `run` within the caps. `validate_studies` also returns
`estimate: {minutes, procs, mem_mb}` per valid study (llm-translators' router places runs with it).

### The catalog

123 public kinds (`catalog.py`; `tools/check_catalog_msl.py` checks every class, port, parameter and variable
against the MSL 4.0.0 sources). Port types: `pin`, `plug` (3 pins), `qs_pin`, `qs_plug`, `flange_t`, `flange_r`,
`heat`, `fluid`, signals `in`/`out`, vector signals `vin`/`vout`/`cvin`/`cvout`.

| Domain | Kinds |
|---|---|
| Analog | resistor (heat port when connected: T_ref, alpha), capacitor, inductor, ground, emf, transformer, ideal_diode, opamp; constant/step/ramp/sine/pulse/signal voltage, constant/step/sine/signal current; voltage/current/potential sensors |
| Polyphase | ac_voltage, ac_resistor/inductor/capacitor, star, delta, plug_to_pin; ac_current_rms, ac_voltage_rms, ac_power |
| AC machines and drives | induction_machine, pmsm, pmsm_damper (MSL machine + terminal box Y \| D; loss records structural); three_phase_grid, vf_inverter, pmsm_current_drive |
| Quasi-static | qs_ground, qs_star, qs_ac_voltage, qs_current_rms, qs_voltage_rms, qs_power; qs_induction_machine, qs_pmsm, qs_pmsm_damper; qs_grid, qs_vf_inverter, qs_pmsm_current_drive |
| DC | dc_motor (composed R + L + EMF + inertia: linear, sweepable), dc_machine (MSL DC_PermanentMagnet with losses and heat ports) |
| Translational | mass, spring, damper, spring_damper, fixed, force, force_step, constant_force; position/speed/acc/force sensors |
| Rotational | inertia, rot_spring, rot_damper, rot_spring_damper, rot_fixed, ideal_gear, torque, torque_step, constant_torque, linear_speed_load, quadratic_speed_load; angle/speed/acc/torque sensors (dampers: heat port when connected) |
| Heat | heat_capacitor, thermal_conductor, thermal_resistor, convection, radiation, fixed/prescribed temperature, fixed/prescribed heat flow; temperature and heat-flow sensors |
| Coolant (FluidHeatFlow) | pipe (heat port when connected), valve, volume_flow, signal_volume_flow, pressure_increase, signal_pressure_increase, ideal_pump, ambient, pressure_reference; pressure, temperature, volume-flow sensors |
| Blocks | constant, step, ramp, sine, pulse, table; gain, add, feedback, product, division; integrator, derivative, first_order, second_order, transfer_function, pi, pid, lim_pid; limiter, dead_zone, slew_rate_limiter |

Composites (dc_motor, convection, the machines with their terminal boxes, the drives) are flattened into elements
`X__part`: no nested models, so every value is a direct override. Blocks with a state get `initType =
InitialState`. Initial values: the reference system decides which are free; only those get `fixed=true`.

### Results (one study)

| Key | Content |
|---|---|
| `ok`, `reliable`, `unreliable_because`, `warnings`, `errors`, `why` | as in the other translators |
| `model_info` | `{hash, cache_hit, omc, msl, equations, variables, trivial}` (+ `variant` when values are compiled in) |
| `inputs`, `fixed_initial_values` | every converted value with provenance; the initial values fixed |
| `outputs` | per output: `unit`, `initial`, `final`, `min`, `max`, `t_min`, `t_max`, `series` {t, y} (≤ 400 points, events kept) |
| `derived`, `steady`, `linearization` | as requested (above) |
| `sweep` | `points`, `table` (params, params_si, values, ok, reliable, checks), `columns`, `checks` (per check over the points), `builds`, `parallel` |
| `checks` | `balance`, `initialization`, `solver`, `events`, `overrides`, `finite`, `energy`, `reference` (catalog) or `cross_solver` (custom), `invariants`, `converge`; for a sweep, the summary over its points |
| `expect`, `passed`, `files`, `seconds`, `execution`, `translator` | as in cfdt; `server_stale` when an old server answered |

## Architecture

```
openmodelica_translator/
  catalog.py    the kinds: elementary (one MSL class) or composite; ports, parameters (SI unit, default, range,
                structural), variables, initial values
  units.py      unit strings and {value, unit, from} -> SI, recorded
  network.py    a study resolved: values, composites flattened (X__part), ports -> nodes, the static checks
  format.py     study_format() (< 32 kB), validate() (also custom models' expression defaults), run caps
  modelica.py   study -> stable Modelica text; structure text with '?' for overridable values (the cache key);
                override file; build .mos; variable filter; escape_translation; bound_parameters
  omc.py        the only module running omc: version, MSL status (exact version), builds and the cache
                (hash incl. omc and MSL versions, fcntl lock, variants for compiled-in values), _init.xml
  simrun.py     runs a cached executable (override file only when there are values, -alarm, process group,
                timeout/cancel); log parsing; read-back
  matfile.py    MAT v4 reader;  linear.py  linearized_model.mo -> eigenvalues, modes, Bode, DC gain
  post.py       outputs, derived metrics, steady detection, comparisons;  energy.py  the energy balance
  ref.py        independent reference: descriptor system per domain, events (piecewise-linear modes), coolant
                hydraulics + temperatures, nonlinear terms (machines, speed loads); chooses the fixed initial values
  refnl.py      Radau IIA for M z' = f (the DAE path);  refqs.py  quasi-static stamps (complex as re/im)
  runner.py     run_study end to end: checks, reference / cross_solver, invariants, converge, the tolerance/100
                retry of a lone failed independent check
  sweep.py      points, one build, a process pool inside one study; rows with their checks
  render.py     time, Bode and sweep plots;  chains.py  b3dt mass and inertia, feat stiffness
  bench.py      37 benchmarks with closed forms (+ chain cases by name)
  runtime.py    RT = llmt_runtime.Runtime(prefix OMCT, cli omct, kind threads, hooks: procs=job_cpus,
                mem_auto 1 GB + 256 MB per CPU, solves=sweep_rounds, script_lines (PYTHONPATH), job_extra,
                running_progress, oom_advice, timeout_hint; slurm_time_default timeout)
  executor.py   omct's sizing and estimates; its old public names as thin wrappers over RT.slurm / RT.jobs
  workers.py    wrappers over llmt_runtime.workers (watch_parent, track/untrack)
  mcp_server.py GUARD = llmt_runtime.server.Server(RT, ...): every tool under @GUARD.tool (worker thread, 48 s
                deadline, ToolError, server_stale), stale exit, log in $OMCT_OUT/.server; slog = GUARD.log
  api.py, cli.py, proc.py   requests (request_name, ran_with through the runtime), the local pool, jobs/cancel;
                proc.run starts omc and simulations in their own process groups, tracked
tests/  16 test files, studies.py, fake_omc.py, fake_slurm.py, fixtures/ (mochi outputs)
tools/  m0/v2/v3 probes and their mochi reports, check_catalog_msl.py, m6_slurm_test.py, mcp_smoke.py
docs/   history.md (the State log 2026-10-20 to 2026-10-31)
```

### Translation and running

1. **Validate** (static, above).
2. **Generate** `model.mo` (declarations and connects sorted, values in SI with full precision, experiment
   annotation, top-level `omct_u_*`/`omct_y_*` for linearize). The structure hash replaces every overridable value
   by a placeholder and adds the omc version, MSL version and build flags: a value change never recompiles; an
   OpenModelica update rebuilds everything once.
3. **Build** on a cache miss, under a lock: `loadModel(Modelica, {"4.0.0"}, requireExactVersion=true)`, checkModel
   (counts compared by omct: A3), `buildModel` with `--linearizationDumpLanguage=modelica`; keep the executable and
   its data files. `_init.xml`'s `isValueChangeable` decides: a value that cannot change at run time is compiled
   into a variant build (catalog studies) or refused with the fix (custom models).
4. **Run** the cached executable from the study folder (`-overrideFile` when there are values, `-s`, `-tolerance`,
   `-stepSize`, `-lv=LOG_STATS`, `-alarm`, `OMP_NUM_THREADS=1`); linearisation with `-l=<at>`.
5. **Read** the MAT file; every value read back from `data_1`.
6. **Check, post-process, render,** write `<name>.json` atomically.

## The checker

**Every run:** balance (equations = variables); initialisation (homotopy → warning); solver finished, no assertion;
events (> 1000 state events or chattering → unreliable); overrides (no override warning, each value read back);
outputs finite; **energy balance** where the components expose power (boundary power = Δ stored + dissipated,
trapezoid on the output grid, judged against max(1e-3, the integration error estimate); 1 % limit; custom models:
not covered).

**Reference implementation (catalog studies):** `ref.py` builds the system from the study, not from the Modelica
text (modified nodal analysis per domain with the effort/flow analogy; blocks as state space; sources as functions
of time). Covered: linear networks; piecewise-linear elements by events (diode, limiters, dead zone, lim_pid, slew
rate, brushes); coolant loops with constant sources (hydraulics, then temperatures); one-way electrical → thermal
heat; nonlinear terms (AC machines and drives, quasi-static machines, speed-dependent loads; `OMCT_REF_NONLINEAR`,
on by default). Not covered (reported as such): radiation, product/division, a varying convection Gc, two-way
electro-thermal coupling (alpha ≠ 0 with a heat port), friction or stray loss with power_w ≠ 1, piecewise-linear
elements together with machines. Compared on the output grid: max |y_om − y_ref| / range ≤ max(1e-4, 100 ×
tolerance), more for outputs with a large offset (10 × tolerance × max|y| / range); eigenvalues to 1e-5.

**Custom models:** `cross_solver`: the same model with the other integrator (IDA ↔ DASSL) at tolerance/10,
compared the same way (numerical errors only). `invariants` catch modelling errors.

**Retry:** when the reference (or cross_solver) is the only failed check, the study runs once more at
tolerance/100: if the gap closes it was integration error (DASSL's global error reached ~1e-4 of range on 6-hour
runs) and the tighter run is the result, with a warning; if not, it stays unreliable as a model difference.

**`converge`:** a rerun at tolerance/10; a change over max(1e-4, 100 × tolerance) of an output's range (more on
large offsets) → unreliable; outputs constant by design (their variation shrinks with the tolerance) are
`not_judged`; `abs_tol` judges named outputs by their absolute change.

**Benchmarks** (`bench.py`, 37, each with its closed form and tolerance; all pass with OpenModelica 1.27.2): RC,
RLC (step, poles and Bode), mass-spring-damper (modes, free), inertia, DC motor, thermal RC and steady state,
two-mass drive, PI loop, an RC sweep (one build), coolant (open step, closed loop, hydraulics), DC machine
(nominal, losses, heating, brushes), resistor and damper heating, limiter loop, rectifier, slew rate, three-phase
RL and delta, induction machine (grid slip, V/f start, delta), PMSM (current drive, damper on the grid), quadratic
load, and the quasi-static versions of the machine cases. Chains by name: `chain_feat_modal`, `chain_b3dt_mass`,
`chain_b3dt_inertia`.

## Size, cost and SLURM

- **Cost.** A build takes ~1-3 s (C compile with `OMCT_COMPILE_PROCS` 4); a simulation 0.05-0.3 s for most studies.
  Sweeps, `converge`, `cross_check` and retries multiply runs, not builds.
- **Estimates** (`executor.estimate`): the build (0 on a cache hit) plus every run, from the cache's measured
  seconds per study or per interval (`runs.json`), else a cautious default; procs after the caps and the node's
  CPUs; memory as for `--mem`.
- **Executor kind `threads`:** one SLURM job per study with `--cpus-per-task` = sweep points at once (≤ points, ≤
  `OMCT_PROCS`), at least `OMCT_COMPILE_PROCS` on a cache miss; studies under `OMCT_SLURM_MIN_MINUTES` stay in the
  local pool. Clusters of several nodes: jobs pinned to this node (`OMCT_SLURM_NODE`).
- **Every tool returns within its `wait_s`:** the runtime's 48 s deadline per call; builds and runs are in the
  pool or SLURM; submitting counts against the wait. A sweep's SLURM time limit covers all its rounds (the first
  point alone, then groups of the job's CPUs: 12 points on 4 CPUs get 50 min, not 20).

## Settings (`translators.json` `env` in llm-translators)

| Setting | Meaning (default) |
|---|---|
| `OMCT_OMC`, `OMCT_MSL_VERSION` | path to omc (`omc` on PATH); MSL version (4.0.0, loaded exactly) |
| `OMCT_OUT`, `OMCT_CACHE` | output folder (`~/omct_runs`); build cache (`$OMCT_OUT/.cache`) |
| `OMCT_SOLVER` | `auto` \| `omc` \| `ref` (auto: omc when found) |
| `OMCT_WORKERS`, `OMCT_TIMEOUT` | local pool size (2); seconds per study (600) |
| `OMCT_PROCS`, `OMCT_COMPILE_PROCS`, `OMCT_OMP_THREADS` | sweep points at once (1); omc `-n` (4); threads in one simulation (1) |
| `OMCT_EXECUTOR`, `OMCT_SLURM_MIN_MINUTES` (1), `OMCT_SLURM_ARGS` (split like a shell; its `--mem` and `--nodelist` win), `OMCT_SLURM_MEM` (auto: 1 GB + 256 MB per CPU), `OMCT_SLURM_TIME` (`timeout` \| `estimate` \| `max` \| minutes), `OMCT_SLURM_TIME_MIN` (20), `OMCT_SLURM_TIME_FACTOR` (3) | SLURM (llmt-runtime's meanings) |
| `OMCT_SLURM_NODE` | `local` (this machine's node) \| `any` \| a node list, in a cluster of several nodes |
| `OMCT_MAX_TIMEOUT`, `OMCT_MAX_MEM`, `OMCT_MAX_PROCS` | caps for a study's `run`; with SLURM, `caps.mem` is at most the node's memory (`mem_mb` for the router) |
| `OMCT_REF_NONLINEAR` | the reference's nonlinear terms (on; `0` switches them off) |
| `OMCT_REF_MAX_EVENTS` | switchings before the reference gives up on a piecewise-linear study (5000) |
| `OMCT_EXIT_WHEN_STALE`, `OMCT_STALE_CHECK_S` | a server exits when a newer release (of omct or the runtime) is installed and it is idle (shared installs; `1` always, `0` never); checked every 10 s |

## Decisions

1. Units: SI numbers, plus unit strings and `{value, unit, from}` objects, converted and recorded.
2. Parameter names: MSL's, plus convenience ones.
3. Two DC motors: `dc_motor` (composed, linear, sweepable) and `dc_machine` (MSL, with losses and heat ports).
4. Results: MAT files; JSON series downsampled to ≤ 400 points.
5. `sweep` is a key on any analysis; steady state detected by omct.
6. Composites flattened; initial values chosen by the reference system; blocks with `initType = InitialState`.
7. Values compiled in by OpenModelica (aliases, evaluated or final parameters): catalog studies get a variant build
   per value set; loss records are structural (B18); custom models are told to use a plain default.
8. Independent checks: the reference for catalog studies, a second integrator plus invariants for custom models;
   a lone failure is retried once at tolerance/100.

## Milestones and versions

- **v1, M0-M8 (2026-10-20/21):** research, format and translation, backend driver, checks and benchmarks,
  interfaces, sweeps/linearisation/steady, SLURM, chains, joining llm-translators.
- **V2 (2026-10-21):** b3dt inertia chains, coolant loops, MSL DC machine, heat ports, events in the reference.
- **V3 (2026-10-21/22):** polyphase plugs, transient AC machines and drives, the nonlinear reference, quasi-static
  machines.
- **From use (2026-10-22 to 11-02):** estimates, custom-model values and sweeps, server log and tools off the event
  loop, stale servers, node pinning, converge noise, the reference retry, cross_solver and invariants, expression
  defaults, OpenModelica 1.27.2.

Details: docs/history.md (everything to 2026-10-31) and the README's version table.

## Recent changes

**2026.11.19.9 (2026-11-19)**: F4 on carbonio (OpenModelica 1.27.2, 2026.11.19.8, bench_20261119_145315_56d73d): fit_rc_tau (R 1000.61, 1.9 se; stderr 0.31659 against 0.31666; chi2_reduced 0.77), fit_rc_unidentifiable (correlation -1.0), fit_thermal_curve (m 2.0000078, h 9.9999932, T0 303.15007), fit_msd (c 799.99998, d 4.0000196) pass, all reliable. **fit_thermal_steady failed and said reliable**: h 21.54 for 10, 'converged' by xtol, 28 of 78 runs failed 'steady point: T still changes' (the bench's 20000 s is 6.7 tau at h = 10; the reference path's exact steady state hid it). Fix: optimize keeps each failed run's x; a start whose end point has a failed run within 1 % of a parameter's (x-space) range and did not end by gtol has failed_at_end > 0 -> unreliable ('it stopped where the model's runs fail', with the last error). A counter of failures since the last Jacobian missed it: a tiny accepted step after the failures reset it. Bench fit_thermal_steady at 90000 s; settled_end's message gives the change and the limit. Test (fake omc): a divider whose steady runs settle only away from the optimum -> unreliable with that reason. **To do: rerun fit_thermal_steady on carbonio after deploying .9.**

**2026.11.19.8 (2026-11-19)**: V4 F3. fit.Budget (timeout from t_start, + extended_s of the study folder, - reserve min(0.25 timeout, max(20 s, 10 x first run)) set by the first evaluation); OmcEvaluator's run timeout also <= the budget left. optimize: Stop (BaseException) from raw() at the budget (time_limit: ends all starts) or at a start's share of max_evaluations (counted in model runs); the start's result = its last Jacobian's x, J, f (status -10 time_limit / 0 max_evaluations); nothing yet -> run_fit ok false 'ran out before its first step'. Not converged -> unreliable_because. Final run's timeout = what is left (>= min(timeout, 60 s)). runner: no record_study for a fit. executor: _parallel (fit: n params) in cpus_for_job and run_study_job; fit_time (expected: 15 iterations a start, upper: the share; run = s_per_interval x max(intervals, 2000) + 0.3 s); estimate_minutes for fits (min(upper, timeout) + final + build); _cache_dir builds a fit's key at the initial values. api.validate warning; api._progress fit stage (fit_progress.json: start, evaluations, failed, best_cost; 'final run' once final/ exists); job_progress passes evaluations. Images: fit.plot (the line from the final run's series); render() reads final/series.npz for a fit; render_images adds fit.plot. **Bug fixed**: with sigma, stderr was sqrt(n) too large (chi_factors undo the 1/sqrt(n) and weight; the earlier test only bounded it); chi2_reduced. Benchmarks fit_rc_tau (stderr against the analytic one: 5 %; chi2 ~1), fit_rc_unidentifiable, fit_thermal_steady, fit_thermal_curve, fit_msd (data from closed forms). MCP instructions: fit, rewrapped to 118 columns (test: <= 30 lines). study_format 31,963 chars. Tests: 470 pass (6 skipped).
**Next: F4** (on the machines: the fit_* benchmarks with OpenModelica; a real data set if Nicola has one), then Modelica.Fluid. The router's omct description (llm-translators) does not mention fit: for the llmt chat.

**2026.11.19.7 (2026-11-19)**: V4 F2, fitting with OpenModelica. fit.OmcEvaluator: build once at the initial values; `_moved` translates each parameter at a second value and finds the override paths it moves (none, or one compiled in (changeable false): FitError up front); a variant build when other compiled-in values differ (as runner); each evaluation translates the point (1-2 ms), checks the structure is the build's, runs simrun.simulate with the outputs-only filter at tol/10 in evaluations/eNNNNN (removed after), combines the outputs; per-run timeout min(timeout, max(OMCT_SWEEP_POINT_MIN_S, FACTOR x first run)). series_grid: stop = last data time; the data's grid when uniform from t0 (x an integer up to the study's intervals), else max(intervals, 2000) and np.interp after _dedupe (events keep the later value). Steady points: settled_end (last fifth < 1e-5 of the range, else 'lengthen'; the ref fallback uses it too). optimize: own forward-difference Jacobian (steps diff_step x max(1, |x|), backward at the upper bound, the other side when a run fails, zero column when both fail) on a ThreadPoolExecutor of min(run.procs, n_params) (1 for ref); fit_progress.json (evaluations, failed, best_cost, start). run_fit: ref refuses custom models; FitError from the evaluator -> ok false with why. tests/fake_omc.py `_apply`: override values that differ from the study's are set back (comp.param, comp.<init var>). tests/test_fit.py: omc (fake): R recovered, one build = the final run's, run folders removed; R and v0 with 2 workers; steady points (divider R2 from two V); compiled-in R.R refused; Jacobian fallback; series_grid; settled_end/_dedupe; 3 real-omc tests (RC R, heating curve m/h/T0 through composite overrides with 3 workers, a custom model's k) skip in the sandbox and where OpenModelica is only the llmt server's (the machines: pytest there has no omc on its PATH). **Checked on carbonio (2026-11-19, OpenModelica 1.27.2, through the omct server, request run_fit_f2_check)**: the same three as studies, all reliable: R 999.99920 (stderr 7e-5; 14 evaluations, 15 runs, 1.1 s); m 2.0000010, h 9.9999927, T0 293.14990 K (3 workers, 32 evaluations, 2.9 s); k 2.4999998 (14 evaluations, 2.6 s). Composite overrides (blk.C = m cp, conv__Gc.k = h A) and a custom model's parameter move at run time as assumed.
**Next: F3** (images: data vs model and residuals; benchmarks fit_*; SLURM sizing and an estimate (today a fit job gets one study's limit: a long fit can hit it); api progress from fit_progress.json; README section).

**2026.11.19.6 (2026-11-19)**: V4 F1, parameter estimation on the reference solver. New `fit.py`: `load` (params by the sweep's rules with SI bounds, a parameter's own range kept (gt strict); data inline or CSV with unit/t_unit/sigma/weight; steady points), `fit_errors` (format.validate_study and the escape hatch call it; refused with sweep, converge, linearize), `RefEvaluator` (translate + Ref.simulate at the data times; steady points by Ref.steady, exact, else a settled run's end), `optimize` (least_squares trf, x in log for log params, diff_step 1e-5, a failed evaluation = residuals 1e3; Sobol starts after the initial guess), `uncertainty` (s^2 pinv(J^T J), delta method, ci95, correlation, identifiable: no pair over 0.98 and cond < 1e12), `run_fit` (the final checked run in `<folder>/final`; warnings for not separable, at a bound, max_evaluations). runner._run dispatches a study with `fit`; _retry_tighter skips it (the final run had its own). solver omc: 'comes with F2'. study_format: STUDY_KEYS fit; media as rows and some conventions shortened (31,933 chars, the limit is 32,000: F2/F3 docs need room). tests/test_fit.py (28): R recovered; R and C not separable (R x C right); noise with sigma in mV and v0 (within 3 stderr); h from two steady points; m, h, T0 from a heating curve in degC and minutes; CSV + 3 starts; unit conversion; 16 validation errors; omc refused; cancel.
**Next: F2** (the OpenModelica evaluator: one build, runs by override without checks, the Jacobian's runs in parallel on the sweep pool, the run tolerance /10), then F3 (images, benchmarks fit_*, SLURM sizing and progress, README section).

**2026.11.19.5 (2026-11-19)**: `extend` (the last short-term item). proc.run polls the study folder's `extend` file
every 2 s (llmt-runtime take_extension; the folder is the cancel flag's, shared by a sweep's points) and runs to
timeout + extended_s; api._progress counts it; api.solver_left_s (hook) from the progress's eta; api.extend over
RT.jobs.extend_request; MCP tool `extend`. `-alarm` dropped from the simulation's command line (it fired at the
original timeout whatever the extension). Tests: a running program outlives its timeout after an extend file and
records extended.json; the tool's files, cap (OMCT_MAX_TIMEOUT), finished studies, errors.
**Next (Nicola, 2026-11-19): parameter estimation from measured data, then Modelica.Fluid.**

**2026.11.19.4 (2026-11-19)**: jobs() on llmt-runtime 2026.11.19.2's `list_jobs(all_requests=True)` (every request,
all of meta's names): hooks `jobs_states` (api.jobs_states over `_collect`, invalid studies included), `jobs_row`
(omct's row), `jobs_answer` (executor_status, bench rows' kind/collect, unreadable requests left out); `_requests`
removed; floor >=2026.11.19.2. Test: a local-pool request (with an invalid study) shows; active=True leaves it out.

**2026.11.19.3 (2026-11-19)**: llmt-runtime 2026.11.19.1's items for omct (floor raised). Done: `log_args` hook
(runtime.log_args: study names and cost keys, as omct's old _summary); `queue_user_only=True`, omct's own squeue count
dropped; job_extra no longer writes time_min/estimate_min (the runtime's: time_min is the limit now) and keeps only a
measured sweep's timeout_s; status() uses `requests.until`. Not done then: jobs() on `list_jobs` (done in 2026.11.19.4). The runtime also
fixed the wrong-node diagnosis (ran_on; "failed: ran on node X"). Tests: hooks (log_args, queue_user_only, job_extra
{}), job.json time fields and a queued study's eta (test_slurm).

**2026.11.19.2 (2026-11-19)**: two open items. (1) `runner.converge_limit(tol)` = max(1e-4, 100 tol), the reference
check's limit (was max(1e-6, 100 tol), stricter than the integration accuracy: 2026.10.23.4's 3.5e-5). (2) `jobs()`
lists bench_* requests too (`_requests` takes both prefixes), marked `kind: benchmarks` with the bench_status call.

**2026.11.19.1 (2026-11-19)**: from the machine tests. (1) A sweep's SLURM limit counted every point with the full
600 s timeout (120 points of 0.1 s on 1 CPU: 1210 min; a sweep queued behind it was told 1305 min, spark1 jobs
716/718). `executor.point_timeout_s`: once the build has a measured point (runs.json study_s), each point's timeout
is min(timeout, max(OMCT_SWEEP_POINT_MIN_S 120, OMCT_SWEEP_POINT_FACTOR 20 x the measured point scaled by
sqrt(intervals ratio), x runs per point)); the runtime's `hooks.time_need_min` (`executor.time_need_min`) counts it,
and `job_extra` writes it to job.json `timeout_s`, which run-study uses for every point. The example: 250 min.
(2) `slurm_states`: the pass-through for job states omct does not know (2026.11.18.1) came before `running`, so a
running SLURM study had no stage or eta_min_max and `next` said "at most ? min"; now after it. Tests in
test_slurm.py (limits with and without a measurement, scaling, cap, run.timeout_s, job.json/job.sh; a running job's
stage and eta). Machine check after deploying: a 120-point sweep on a spark (its job.json timeout_s and --time).

**2026.11.18.3 (2026-11-18)**: llm-translators' prompt "Worker pools through llmt-runtime's workers.make_pool":
the run pool (`api._Pool.ensure`, `make_pool`) and the sweep's `make_executor`; floor `llmt-runtime>=2026.11.18.5`.
`WorkersNotPermitted` is not caught: the tool wrapper turns it into a ToolError with the runtime's text. omct's own
change: `start()` creates the pool before writing request.json or submitting anything (and removes the folder if
refused), since a request whose local studies never reached a pool showed them pending for ever. Tests: a refused
pool through the MCP tool (message, no request left, jobs() and validate_studies answer after), a refused sweep
executor (the study's why).

**2026.11.18.2 (2026-11-18)**: llmt-runtime 2026.11.18.2 (as tagged) makes every study with `run.after` a SLURM job
and refuses `run.after` with a local executor or `queue: false`: floor raised to it; the strict xfail is a plain
test now; `test_run_after_needs_slurm` (queue: false, OMCT_EXECUTOR=local, refusal at start); the `"queue": true`
advice removed.

**2026.11.18.1 (2026-11-18)**: `run.after` (llm-translators' prompt for feat, lmpt, omct; cfdt 2026.11.18.3 the
reference): RUN_KEYS, the run messages and study_format's `run` line; `api._after_problems` (as cfdt's) in
`validate` and in `start` with the request folder before submission; floor `llmt-runtime>=2026.11.18.1`. Found while
testing: `executor.slurm_states` mapped the runtime's states queued/running only, so `waiting` became "failed: the
job left the queue without a result" and was written as the study's result; now waiting is shown like queued and
any other non-final state passes through. `cancel(at=...)`. Tests with the runtime's fake SLURM (FAKE_DEPENDENCIES,
FAKE_PENDING): validation (accept, unknown ref, itself, bad mode, mode without after, unknown key message), the
dependency (`--dependency=afterany:<a>`, `run_after` in job.json), `waiting` in status and jobs(), refusal at start
for a missing other-request run; the local-pool gap as a strict xfail (fixed by the runtime in 2026.11.18.2). Tell the llm-translators chat: omct
2026.11.18.1 (`after_since`).

**2026.11.09.1-.3 (2026-11-09, the llmt-runtime migration, done in the llm-translators chat)**: phase 1: workers
(SIGTERM now also ends a worker's omc build or simulation), out-of-memory wording, version recording (`ran_with`,
`translator_now`), request names; phase 2: the SLURM layer (settings, nodes and pinning, node memory, `job.sh` with
`--mem` inside, `job.json`, submit with late-sbatch adoption, job states, queue, cancel), omct's sizing as hooks, a
sweep's limit covering all its rounds, `OMCT_SLURM_TIME` `max` and minutes, `--mem-bind` no longer taken for
`--mem`; phase 3: the MCP server's common code (`GUARD`; every tool under the 48 s deadline; stale check every
10 s). `tests/fake_slurm.py` removed (the runtime's fake); `tests/test_runtime.py` added. Checked in this chat on
2026-11-09: 405 tests pass in the sandbox against runtime 2026.11.09.6; planning.md updated to the runtime (this
entry, conventions, environment, architecture, settings, open items).

**2026.11.07.1 (2026-11-07)**: from the llm-translators chat (router 2026.11.07.1 compares run.mem with each machine's `executor.caps.mem_mb`; omct reported `caps: {}` everywhere). (1) format.caps(): with SLURM, mem = the largest RealMemory (scontrol, values >= 1024 MB) among the nodes this machine's jobs go to (this node with OMCT_SLURM_NODE=local), or OMCT_MAX_MEM when lower; mem_mb and mem_from; run.mem above it fails validation (as cfdt 2026.11.07.1, which reads sinfo %m; omct reads RealMemory from the scontrol records it already keeps). (2) Worker pools (create_new_translators.md, 'Worker pools never outlive the server'): workers.py as feat's (os.getppid() polled every 1 s, not PR_SET_PDEATHSIG: omct's pool is created inside a tool call's worker thread); initializer on api._POOL and on the sweep's ProcessPoolExecutor; proc.run tracks the programs it starts (own process groups) so an orphaned worker kills them; the signal handler (SIGTERM/HUP/INT) now stops the pool before re-raising (re-raising skips atexit, so `llmt servers --stop` used to leave the workers). The stale-server exit already stopped the pool. Test: a real server process that dies with os._exit: its worker and the worker's program are gone within 10 s. (3) request_name on run_studies (optional item; ROUTER.md 'Requests named by the router'). Sandbox note: /sessions is full; run the tests with TMPDIR=/tmp.

**2026.11.03.1 (2026-11-03)**: planning.md reviewed and rewritten against the code: the top sections (state, open items, format, catalog, checker, settings, environment) as built, the assumptions updated (A6, A18, A19, A21, new A22), V2/V3 marked done; the State log moved unchanged (reordered) to docs/history.md. `study_format()`'s escape-hatch line names the cross-solver check.

**2026.11.02.1 (2026-11-02)**: setuptools' deprecation of `project.license` as a TOML table (refused from
2027-02-18): `license = "MIT"` (SPDX), `requires = ["setuptools>=77"]`. Not built in the sandbox (disk full, its
setuptools 59 too old); pip installs a current setuptools into the isolated build environment. Check on the Mac:
`python3 -m pip wheel --no-deps -w /tmp/omct_whl .` without the warning.

**2026.10.31.1 (2026-10-31)**: OpenModelica 1.27.1 -> 1.27.2 on carbonio, mochi, spark1, spark2 (model_info.omc "1.27.2", MSL 4.0.0 exact everywhere). No code depends on the patch level; the build cache key includes the omc version, so 1.27.1 builds are not reused. Verified on 1.27.2: all 37 benchmarks pass on carbonio (bench_20261031_144830_0febf2), numbers as with 1.27.1 (linearization, coolant, DC machine loss records folded at compile time, transient and QS machines); custom models: cross_solver with IDA, invariants, and 2026.10.26.1's open item (a): a parameter with an expression default two levels deep (Probe.i.d.b = 2*a) is marked not changeable in _init.xml and refused right after the build ("values the model cannot take at run time"). Not re-probed: whether 1.27.2 still cannot read an empty override file (A21); omct passes no -overrideFile then anyway. Found: the first run_benchmarks call after the update did not answer within the router's limit; bench() now counts start() against wait_s (as run() does). Release notes for 1.27.2 were not reachable (github timed out).

**2026.10.26.2 (2026-10-26)**: reported "the cross-check for custom models did not show up" (P_M18/22/30 on carbonio, 2026.10.26.1, run_20261026_182317_125a29). It ran: P_M22/points/p0000/p0000.json has cross_solver {ok, covered, solver ida, tolerance 1e-7, max_error_rel 7.5e-5}; this also verifies 2026.10.26.1's open item (b): IDA runs these custom models. What misled: the validation warning still said "no reference implementation", and a sweep's summary had no per-point checks. Fix: the warning names the cross-check and invariants; sweep rows carry `checks`, the sweep `sweep.checks` (points, failed, not_covered, retried, max_error_rel). Also: machines() 'release' is the llm-translators bundle, 'version'/'serving' the translator.

**2026.10.26.1 (2026-10-26)**: three reports from use. (1) Motor heating sweeps (QS induction machine + 4 thermal nodes, 6 h, 27 points on carbonio): 6 points unreliable on the reference alone. Diagnosis: run alone, point 3 passes with Pcu2 (rotor copper loss) at 8.5e-5 of its range against the limit 1e-4 at tolerance 1e-6, and 4.5e-6 at 1e-8 (mochi and carbonio, the same cached build): OpenModelica's integration error, at the limit; the reference is converged (its tolerances x 1/100 move Tw by 1e-5 K). (An earlier comparison of the sweep table with final values was wrong: the table holds window means.) Fix: _retry_tighter: when the independent check is the only failure, the study runs once more at tolerance/100; the tighter run is the result if it agrees (warning), else the first result stays unreliable with retry.why = a model difference. Sweeps: per point (run_study). (2) Custom models: checks.cross_solver (the other integrator, IDA <-> DASSL, at tolerance/10, compared like the reference; covered false if that run fails; cross_check: false skips it; estimates count the extra run), and invariants for any study. (3) Expression defaults in custom models: bound_parameters() (the model's parameters and one level of components of classes in the same text, with modifiers) rejects them in validation; validation also reads the cached build's changeable map; after a build, escape-hatch values marked not changeable fail at once. Cause of the late failure: omc.build recorded the study's values as compiled_values for custom models, so the runner thought the build already had them and dropped them. Unverified: (a) that OpenModelica 1.27.1 marks a parameter with an expression default isValueChangeable="false" in _init.xml (the after-build check relies on it; the read-back still catches it otherwise). Probe: a custom model `model M parameter Real a = 1; parameter Real b = 2*a; Real y; equation y = b; end M;` with values {"b": 5}: expect "values the model cannot take at run time" after the build (or validation, from the static check); (b) IDA on the users' custom models (cross_solver reports covered false when it fails).

**2026.10.25.3 (2026-10-25)**: llm-translators' checklist for translators (six items). Already done: (3) stale servers (2026.10.23.3 + the serving guard of 2026.10.25.1; run-study and tests never refuse or exit), (4) estimates in validation (2026.10.22.1; procs after the caps, mem as --mem). Done in 2026.10.25.2, completed now: (1) node pinning: the node list and CPUs now from `scontrol -o show node` (the checklist's source; was sinfo), and a job's CPUs capped at this node's (with any: the largest node's), so the estimate the router tests with sbatch --test-only matches the job; (5) failed scontrol not remembered (and msl_status: an lru_cache kept a failed first MSL load for the server's lifetime: now only success is kept, and its omc call is bounded at 40 s since study_format runs it). New: (2) OMCT_SLURM_ARGS with several options: shlex everywhere already; mem_mb_for_job now reads "--mem 8G" (two words) and --mem-per-cpu (x CPUs), test with "--mem=8G --nodelist=spark-5da5"; (6) wait bounds: sbatch timeout 60 -> 15 s and, after a timeout, the request's other studies fail at once for 30 s (a dead controller made run_studies wait 60 s per study); _run (squeue/sacct/scontrol) 20 -> 10 s; status() starts no collect it cannot finish by the deadline (measured from the last one); run() counts the submit time against wait. Compiling and simulating were already in the pool or SLURM jobs; get_model and validate_studies only translate. Tests: fake two-node scontrol (sizes 20 and 12 CPUs), args with two options, hanging sbatch, slow collect.

## V4: parameter estimation (design, 2026-11-19; F1 2026.11.19.6, F2 .7, F3 .8)

Nicola's choices (2026-11-19): data inline and from a CSV file on the machine; a local bounded least-squares fit with
optional multistart; transient time series, steady values and initial values; standard errors and correlations from
the Jacobian (no profile likelihood). Order: V4 (this), then Modelica.Fluid.

**The study key `fit`** (any catalog study, transient or steady analysis; custom models too):

```json
"fit": {
  "params": {"R.R": {"initial": 800, "min": 100, "max": 1e4},
             "C.v0": {"initial": 0, "min": -1, "max": 1},
             "th.G": {"initial": "2 W/K", "min": "0.1 W/K", "max": "50 W/K", "scale": "log"}},
  "data": {"v": {"t": [0, 0.001, ...], "y": [0, 0.63, ...], "sigma": 0.01},
           "T": {"csv": "~/meas/heat.csv", "t": "time_s", "y": "T_C", "unit": "degC"}},
  "steady": [{"set": {"P.Q_flow": 20}, "values": {"T": "45 degC"}},
             {"set": {"P.Q_flow": 40}, "values": {"T": "70 degC"}}],
  "starts": 1, "max_evaluations": 400
}
```

- `params`: run-time values only, by the sweep's rules (catalog parameters that are not structural, and initial
  values `v0`, `w0`, `T0`, ...); `initial`, `min`, `max` with units; `scale` lin | log (log by default when min > 0:
  R, C, J, G span decades).
- `data`: per study output, a time series inline (`t`, `y`; units; at most ~5000 points per output inline) or from a
  CSV on the machine (`csv` path, `t`/`y` column names, `unit`, optional `t_unit`); `sigma` (absolute, with units)
  or none (then each output is scaled by its data range, so outputs weigh equally); `weight` optional. Times within
  the analysis' [start_time, stop_time].
- `steady`: operating points; each sets values (`set`, as a sweep point) and gives measured steady outputs; each is
  a run to steady state. A fit may have `data`, `steady` or both.
- `starts` (1): more spread the starts over the bounds (log scale where log), deterministic (a Sobol-like sequence),
  the best kept and every start's end reported. `max_evaluations` caps the runs.
- Not with `sweep` or `converge` in the same study (validation says so); `expect` applies to the fitted run.

**How it runs** (`fit.py`):
1. Validate, translate, build once (as a sweep). Parameters change by override; values read back on the final run.
2. Residual function: for parameter vector p, run the model (and every steady point) without the checks (no
   reference, no energy: the fast path a sweep point uses minus its checks), interpolate each output at the data's
   times (event points excluded as everywhere), r = w (y_model - y_data) / (sigma or range) / sqrt(n_output).
3. `scipy.optimize.least_squares` (trf, bounds, x_scale by parameter, diff_step tied to the solver tolerance:
   finite differences need the runs' noise below the step, so the fit runs at tolerance/10 of the study's unless set).
   Each Jacobian = n_params runs in parallel (the sweep's process pool, `run.procs`); the line search runs single.
4. A failed run (solver error, assertion) returns a large residual and is counted, so the optimizer steps back.
5. At the best p: the full study run with every check (reference, energy, overrides read back) = the result's
   outputs and checks; `reliable` requires that run reliable and the optimizer converged.

**The result** (`fit` key, plus the usual result of the final run):
- `params.<name>`: value (SI and as given), initial, stderr, ci95 [lo, hi], at_bound; `correlation` matrix;
  `identifiable` false with the pair named when |corr| > 0.98 or J^T J is near singular (e.g. R and C from an RC
  step: only RC is determined).
- `cost`, per output `rms`, `r2`, `max_abs` and the residual series (t, data, model) for plotting; per steady point
  the model vs data values.
- `status` (converged / max_evaluations / failed), `evaluations`, `iterations`, `starts` (each start's end and cost).
- Standard errors: cov = s^2 (J^T J)^-1 in the fit's scaled coordinates, mapped back (delta method for log
  parameters); s^2 = 1 with sigma given, else the residual variance (reduced chi^2); stated in the result.
- `images: true`: data vs model per output, residuals.

**Checks and benchmarks** (closed forms, synthetic data from the reference):
- fit_rc_tau: RC step generated with R = 1000; fit R (C known) from 1000 points with noise 1e-3: R within 3
  stderr, stderr against the analytic one.
- fit_rc_unidentifiable: fit R and C together: `identifiable` false, |corr(R, C)| ~ 1.
- fit_thermal: heated block: C and G from a heating curve; G also from two steady points alone.
- fit_msd: mass-spring-damper free vibration: c and d recovered.
- fit_initial: T0 fitted with C and G.
- A fit with `solver: ref` runs in the sandbox (the reference as the model), so fit.py is tested without OpenModelica.

**Cost and SLURM:** estimate = starts x (iterations ~ 10-30) x (n_params + 1) runs x the measured run time (+ steady
points); a fit is one job, CPUs = min(run.procs, n_params) for the Jacobian; its time limit from the measured run
time as sweeps (each run's timeout = the sweep point's rule). Progress: evaluations done, best cost so far.

**Milestones:** F1 format, validation, data (inline, CSV), fit.py on the reference solver, tests; F2 the OpenModelica
path (one build, fast runs, parallel Jacobian), final checked run, uncertainty, results; F3 steady points, initial
values, multistart, images, benchmarks, SLURM sizing and estimates, docs; F4 on the machines (benchmarks, a real
data set if Nicola has one).

**Status (2026.11.19.9):** F1-F3 done; F4: four fit_* benchmarks pass with OpenModelica on carbonio, fit_thermal_steady to rerun after .9 (see Recent changes); a real data set if Nicola has one. F2 checked on carbonio (2026-11-19, see Recent changes).

**F3 (built 2026.11.19.8; Nicola, 2026-11-19: whole-fit budget):** run.timeout_s (default 600 s, within the caps) limits the whole fit like one study (job limit = it + 10 min, sweep_rounds stays 1). The optimizer stops at timeout - reserve (reserve for the final run: min(0.25 timeout, max(20 s, 10 x first run))), deadline moved by extend (llmt_runtime.jobs.extended_s of the study folder); each run's timeout also <= the time left. At the deadline: the last accepted iterate (the x of the last Jacobian, its J for the uncertainty), status time_limit; nothing yet: ok false naming the first run's time. max_evaluations counts model runs per start (Jacobian runs included; a hard stop, the same best-so-far path). Not converged (time_limit, max_evaluations, failed) -> unreliable. Sizing: cpus_for_job uses min(run.procs, n_params) for fits (today it gives 1); estimate_minutes for fits from the measured run (runs.json) x expected runs, capped at the timeout; api.validate warns when the expected time exceeds timeout_s. runner.run_study must not record_study a fit's total time (it would become the sweep points' study_s). api._progress: stage from fit_progress.json (start, evaluations, best cost) and 'final run'. Then images (data vs model + residuals, steady model vs data), benchmarks fit_*, README section.

**Open questions** (decide while building, or ask): whether the fast path may skip reading the full MAT file (a
variable filter on the outputs only); the CSV's location for router placement (a path under the machine's home: the
router cannot see it; the study gives `run.host`).

## Version 2 (done 2026-10-21: all checked on mochi)

Nicola's order: (V2.1) rotational chains from build123d-translator moments of inertia; (V2.2) coolant loops with
Thermal.FluidHeatFlow; (V2.3) the library's DC machine with losses and a thermal port; (V2.4) heat from resistors and
dampers into the thermal network; (V2.5) the diode and limiters in the reference check. Each step: version bump,
tests, benchmarks with closed forms, README, this file. Facts below are from the MSL 4.0.0 sources (tag v4.0.0,
read in the sandbox); OpenModelica behaviour was checked by `tools/v2_probe.sh` on mochi (B1-B18, below).

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

## Version 3: AC machines (done 2026-10-22: all checked on mochi)

Nicola's choices: the squirrel-cage induction machine and the permanent-magnet synchronous machine; 3-phase plugs
and polyphase parts **and** ready-made drives; a **nonlinear reference** for the machines; the **quasi-static**
(phasor) versions as well. Order: V3.1 plugs and polyphase parts (linear: the reference covers them per pin);
V3.2 the transient machines (Electrical.Machines.BasicMachines, m = 3) and the drives, with closed-form benchmarks;
V3.3 the nonlinear reference; V3.4 the quasi-static machines (Magnetic.QuasiStatic.FundamentalWave). OpenModelica
behaviour: `tools/v3_probe.sh` (C1-C6, below; all measured).

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
  reference: the machines written independently (IM in the stator frame, PMSM in the rotor frame), their products
  and rotations as nonlinear terms: path (a), the terms as inputs of the exact linear reduction and an ODE in its
  coordinates (late terms for the current-fed PMSM), or path (b), Radau IIA on the DAE (`refnl.py`); as built in
  V3.3 (docs/history.md, 2026.10.21.23-.27). Benchmarks: IM slip and current
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

- Modelica.Fluid with cfdt pump/pipe tables (beyond FluidHeatFlow); QS passives (resistor, inductor, capacitor).
- FMU export for co-simulation; parameter estimation from measured data (fit k, J, d).
- Event-driven stops (`crossing` that ends the run), frequency sweeps by simulation for nonlinear systems.
- The reference for the cases still not covered (radiation, two-way electro-thermal coupling, power_w ≠ 1 losses,
  piecewise-linear elements with machines).
