# openmodelica-translator: context for a new session

Session context for Claude (the user documentation is README.md). Start a chat with: "Read planning.md and start."

Version: 2026.11.19.11 (2026-11-19). Done: M0-M8 (v1), V2 (coolant, DC machine, heat ports, events), V3 (AC
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
  controllers, Modelica.Fluid (V5: liquids, T-dependent liquids and gases, with their own reference, fluidref.py).
  Analyses transient, steady, linearize; sweeps on one build. What the Fluid reference does not cover yet: Open
  items, "Open improvements: Modelica.Fluid".
- **Checked:** 37 benchmarks with closed forms pass with OpenModelica 1.27.2 (carbonio, 2026-10-31; the same with
  1.27.1 on mochi before). SLURM, queueing, reconnecting and the sweep limits checked on carbonio and
  the sparks (2026-11-19, see Open items).
- **Runtime:** omct runs on llmt-runtime (`llmt_runtime`, a submodule of llm-translators at
  `~/Software/llmt-runtime`; pinned 2026.11.19.2; omct declares the floor `llmt-runtime>=2026.11.19.2`). Read its
  `README.md` (API, hooks, settings) and `PLANNING.md` (open items) before touching SLURM, jobs, memory, workers,
  requests or the MCP server's common code.
- **Code:** 28 modules (`runtime.py`, `workers.py` thin), about 12,300 lines (`ref.py` alone ~3,000); 497 tests pass
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

### Open improvements: Modelica.Fluid (as of 2026.11.19.26)

Everything below falls back to OpenModelica's own checks (balance, initialisation, solver, mass, fluid_energy) plus
the cross-solver run; nothing here is silently wrong. Each line says what is missing and where it is decided
(`fluidref.covered()`, `FluidRef.__init__` / `_thermal_setup`, `network.py`, `catalog.py`, `runner.py`).

**A. Not in the Fluid reference (the study gets no reference check; the cross-solver runs instead)**
- Kinds: `fl_pump_shaft` (Machines.Pump on a flange: needs the mechanics in the same reference); any non-Fluid,
  non-thermal kind in the same study (circuits, machines, mechanics, blocks, FluidHeatFlow) — `covered()`.
- Thermal parts on heat ports other than fixed_temperature, fixed_heat_flow, thermal_conductor, thermal_resistor,
  heat_capacitor, temperature_sensor (convection, radiation, prescribed temperature/heat-flow inputs, body radiation)
  — `THERMAL`.
- Inputs that change the flows: `p_in`, `m_flow_in`, `opening`, `w_in` (constant values only) — `HYDRAULIC_INPUTS`.
  `T_in`: constant-density liquids keep the hydraulic comparison with temperatures off; T-dependent liquids and gases
  are not covered at all.
- Pipe friction `turbulent` and `nominal_turbulent` (static and dynamic pipes): only detailed and nominal_laminar.
- Dynamic pipes: modelStructure a_vb / a_v_b (omct never writes them); `heat_transfer: ideal` with the heat port
  connected (volumes at the wall's T: temperatures off; T-dependent media: not covered).
- `fl_resistance` with a = 0 (MSL's VolumeFlowRate gives m = rho b dp then; omct steers to fl_valve_linear).
- System: `allowFlowReversal: false`; `dynamics` steady_state_initial / free_initial with tanks (not covered) or with
  volumes and pump volumes (temperatures off; T-dependent media and gases: not covered).
- Media: only water, custom constant liquids, glycol47, essotherm650, air, simple_air (omct offers no others).
- Gases: two gas volumes on one connection point (e.g. a dynamic pipe joined directly to fl_volume ports;
  OpenModelica runs it, run_fl15_probe2 b3); gases in pumps (fans) or tanks.
- Topology: a closed volume's port sharing its connection point with a boundary, a tank, a multi-port source or
  another volume port; a connection point with only sensors; two boundaries/tanks joined directly; two parts fixing
  one thermal node (FluidRefError, the reason in the study's warnings).
- Analyses: `linearize` (the Fluid reference does not linearise); `steady` with tanks, volumes, pump volumes or
  capacitors (no algebraic steady state: the reference says so and is skipped).
- Tanks: the inflow-only mode (a level below a port: "a tank ran empty"); port heights other than 0 (omct does not
  expose portsData_height); `port_diameter` only as one value for every port.

**B. Covered, with assumptions to revisit**
- Momentum is steady (System.momentumDynamics default); omct exposes no dynamic momentum, so the reference never
  needs it. If it is ever exposed, pipes need Ib_flows and the staggered momentum states.
- `system.use_eps_Re` false (dp_small, m_flow_small fixed): the Re-based regularisation is not ported.
- Valves: no opening filter, leakageOpening only through the filter (not exposed); xtCharacteristic = one.
- Pumps: constant speed; efficiency constant or MSL quadraticPower; no heat port; V > 0 with checkValve.
- TableBased media are fitted with numpy.polyfit (same least squares as Polynomials.fitting; ~1e-9 differences seen);
  their T_min/T_max asserts (and SimpleAir's 0..100 degC, DryAirNasa's 200 K) are not enforced: the reference
  extrapolates where OpenModelica would stop.
- T-dependent liquids and gases: each instant is a fixed point (hydraulics <-> mixing/energy) to 1e-11 K, at most 100
  passes ("did not agree" otherwise); a follow volume's mass change is solved exactly per volume (FL15), interior
  liquid volumes still by the fixed point.
- inStream with no outflow at a point: the other port's h_outflow (two ports) or the mean of the others (Modelica
  leaves this to the tool's regularisation); only the outputs at zero flow depend on it.

**C. The checks**
- The reference compares only the study's outputs (catalog vars and ports' p, m_flow); internal values
  (mediums[i].T, Wb_flows, h_outflow) are computed but not compared, and users cannot ask for them as outputs (no
  port .h, no raw paths). An output alias for them would widen both.
- fluid_energy and mass integrate on the output grid (trapezoid, Richardson allowance): coarse `intervals` widen the
  allowance (fl_pipe_loop_reference: 1 % error inside a 4.6 % limit with 30 intervals). Reading OpenModelica's own
  steps (all rows of result.mat) or asking for more intervals would tighten it.
- Mass check on closed loops: the allowance includes 10 tol of the stored mass (no throughput to scale by).
- fluid_energy counts potential energy only through StaticPipe's height_ab and DynamicPipe's Wb_flows, kinetic
  energy only at tank ports with port_diameter (as MSL does).
- Covered studies skip the cross-solver (cross_check: true forces it).

**D. Cost**
- Reference runs with T-dependent liquids or gases take 0.1–25 s per study (Python, Radau, a root solve per pass);
  constant-density ones well under a second. Large networks or long sweeps with `solver: ref` will be slow; a
  vectorised or compiled residual would help.

**E. Found in OpenModelica 1.27.2 + MSL 4.0.0 (handled, keep in mind)**
- DynamicPipe nNodes 1 with av_vb does not build (network error since .22).
- Constant-density volumes need preferredMediumStates=false (FL10); TableBased and gases run with MSL's preferred
  states.
- A gas dynamic pipe's end volume on a boundary: OpenModelica reports the initial p as over-specified and drops it
  (the reference takes the boundary's p, agreeing to 1e-13).

**F. Pumps: priming and cavitation (Nicola, 2026-11-19: "Is a self-priming centrifugal pump covered?")**
- Priming is out of scope: a self-priming pump first evacuates air from the suction line (an air-water mixture in the
  casing, a free surface rising in the pipe). Modelica.Fluid's pumps (PrescribedPump, Pump) carry one single-phase
  medium, and MSL 4.0.0 has no air-water mixture through a pump and no free surface in a pipe, so neither OpenModelica
  (through omct) nor the reference can represent it. Once primed it is an ordinary centrifugal pump running full of
  liquid: fl_pump and the reference cover that (head curve, constant speed, power curve or efficiency, volume, check
  valve, suction lift as a tank below the pump with a rising static pipe).
- NPSH / cavitation is not checked (feasible): neither OpenModelica's run nor the reference compares the pump's suction
  pressure with the liquid's vapour pressure, so a suction lift that would cavitate still runs and is reliable. A check
  or warning could take NPSH_available = (p_inlet - p_vap(T_inlet)) / (rho g) from the pump's port_a.p and inStream T
  (outputs we already have): p_vap for water (IF97 saturation or a fit), glycol47 and essotherm650 from the
  tableVaporPressure in MSL's TableBased packages (poly_pVap, not ported yet); no p_vap for gases (not applicable). An
  NPSH_required curve (a pump parameter, or from cfd-translator's cavitation results: NPSH3) would turn the warning
  into a margin. MSL's PumpMonitoringNPSH needs a two-phase medium, so this would be omct's own check, not MSL's.

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

**2026.11.19.20 (2026-11-19)**: V5 FL10, pump volumes (Nicola: item 1 of the open list). **Probe matrix on carbonio (.19, run_pv_matrix1-5; V = 0.001, catalog studies and escape-hatch variants):** with omct's energyDynamics = system (FL7): pump->Ts->bnd, pump->Ts->pipe->bnd, pipe->pump->bnd, pump->pipe->Ts->tank, pump->valve->tank ran; pump->pipe->tank(2 ports)->orifice->bnd 'structurally singular'; pump->tank initialisation failed (linear system singular). With MSL's own SteadyState energy both of those ran (K_D_steady, K_E_steady), with massDynamics = system or energyDynamics SteadyStateInitial they failed as before. Cause: PartialLumpedVolume sets medium(preferredMediumStates = not (energy and mass SteadyState)), i.e. p and T preferred states as soon as a balance is dynamic; with constant density p is fixed by whatever sets the pressure (a tank's level, a loop's tank), so OpenModelica's index reduction or initialisation fails. **medium(preferredMediumStates=false)** with dynamic energy: pump->tank ran (D_pms), the check-valve pump before pipe->tank->orifice ran (Icv_pms), a closed check valve against 25 m ran (m 0, head 25), the closed loop with a check-valve pump volume ran (flows equal to V = 0's), and so did the FL2 cases that were refused: a ClosedVolume on a closed loop (loop_vol_pms) and a DynamicPipe after a valve on a closed loop with mediums(each preferredMediumStates=false) (loop_dynpipe_pms), both m 4.905757354364529. modelica.fluid_parts: constant-density (liquid_const) fl_volume, pump volumes: medium.preferredMediumStates = false; fl_dynamic_pipe: mediums(each preferredMediumStates=false); gases and T-dependent liquids unchanged. network._closed_loops: no errors any more; only the modelStructure (av_b right after one pump on a loop, else av_vb). Benchmarks fl_pump_into_tank (against the reference), fl_heated_loop (balances, cross-solver). tests: test_closed_loops_with_volumes, test_pump_volume_on_a_closed_liquid_loop.

**Carbonio with .19 (bench_20261119_212803_199142): all 19 fl_* pass, reliable.** fl_thermal_reference: OpenModelica against the reference 1.8e-6 (temperatures, heat flows, levels; reference covered); fl_pump_heating: T 6.6e-6 K from the closed form (limit 1e-5 K: OpenModelica at tolerance 1e-8; tight, watch it), m exact; fl_heated_tank exact. Open: dynamic pipes in the reference, T-dependent liquids and gases (pump volumes: FL10, .20).

**2026.11.19.19 (2026-11-19)**: V5 FL9, temperatures in the Fluid reference (Nicola: lumped parts first; the thermal network on heat ports; constant-property liquids). MSL 4.0.0 PartialSimpleMedium: h = cp (T - T0), u = cv (T - T0), T0 273.15 (ConstantPropertyLiquidWater: cp = cv = 4184). fluidref: _thermal_setup / _thermal_net / _energy / _thermal_paths; states [levels, U tanks, U pump volumes, T capacitors] integrated together (Radau); node enthalpies by a linear solve of the mixing (each connection point: the discharging components' outflow enthalpies weighted by their flows; static parts pass the upstream node's, a pump without a volume adds W/|m|); tank Hb_flow = sum m actualStream(h) + m v^2/2 at lossy ports; pump volumes der(U) = |m| (h_in - h) + W; the thermal network solved for its free nodes each instant (conductors, resistors, heat flows with alpha), fixed by fixed_temperature, capacitors and the tanks' heat ports (two fixing parts on one node: refused). fl_temperature: regStep(m, T_a_inflow, T_b_inflow, m_flow_small) as MSL. covered(): thermal kinds accepted; T_in inputs: temperatures off (thermal_why), hydraulics still compared. **Checked on carbonio before the release (run_fl9_probe2, .18, fl9_noV):** boundary 300 K -> pump (V 0) -> sensor -> pipe -> tank (290 K, 20 kW heater, conductor 50 W/K to a capacitor 1e5 J/K at 350 K with a 5 kW heater, alpha 0.002) -> orifice -> sensor -> boundary 280 K, 600 s: tank T 300.47276033 vs OpenModelica 300.47275411 (2.1e-8), capacitor 365.741134 (4.0e-9), conductor Q 7.3e-8, port Q 1.0e-8, level 2.5e-8, the sensor after the pump 300.02296123 (9e-13: W/(m cp) exact); the series agree at every point. **Pump volumes in OpenModelica (run_fl9_probe1-3), recorded, not solved:** a pump with V > 0 (system energy dynamics, start flow) builds and runs between boundaries and before a pipe to a tank, but OpenModelica 1.27.2 failed on 'pump -> tank' directly (initialisation: linear system 59 singular) and on 'pump -> temperature sensor -> pipe -> tank -> orifice -> boundary' (structurally singular), while the same circuit with V = 0 runs. Open: which connections a pump volume tolerates (an fl_temperature next to it?); until then V > 0 (and so checkValve on pumps) is fragile. Benchmarks fl_pump_heating (closed form, also with the reference), fl_thermal_reference (OpenModelica against the reference). tests/test_fluidref.py: heat conservation tank + capacitor, mixing at a junction, the pump-heating closed form, T_in.

**Carbonio with .18 (bench_20261119_211149_da4000): all 17 fl_* pass, reliable, fluid_energy run in every OpenModelica case** (fl_heated_tank T 291.5 exact and energy 0.0; drain, port drain, network and loop now within the limit). **V5 done through FL8.** Open: T-dependent liquids and gases in the reference (temperatures), Fluid energy in sweep points.

**2026.11.19.18 (2026-11-19)**: carbonio with .17 (bench_20261119_203924_185ab6): 12 of 17 fl_* passed; fluid_energy failed in fl_drain_torricelli (1.35e-6 > 1e-6 at tolerance 1e-8: U is an integrated state, mass is not), fl_network_reference (3.0e-5 at tolerance 1e-6, run_fl8_net_e), fl_tank_port_drain and fl_loop_reference (1.4e-3: stored 9263.35 J against 9250.33 J in 10 s = 1.3 W = the kinetic energy m v^2/2 at the 50 mm tank ports: PartialLumpedVessel's Hb_flow = sum(ports_H_flow) + sum(ports_E_flow), ports_E_flow = m (v^2/2 + g height), which the isenthalpic static parts do not carry); fl_heated_tank's T rose 1.434 K instead of 1.5 K: the custom liquid set cp_const only, cv_const stayed 4184 (ConstantPropertyLiquidWater: cp_const = cv_const = 4184; u = cv (T - T0)). Fixes: vessels' ports_E_flow[j] in the balance (recorded: run_fl8_eflow, 0 without portsData, -47 W of -289 kW with); limit max(ENERGY_FLOOR 1e-5, 100 tol, 10 Richardson); fmedium: cv_const = cp for custom liquids (e_drain: U(0) = 1000 kg x 4000 x 20 K = 8.0e7 J exactly).

**2026.11.19.17 (2026-11-19)**: V5 FL8, the Fluid energy balance. MSL 4.0.0 (Interfaces.mo): PartialLumpedVolume (vessels, pumps) has U, Qb_flow, Wb_flow, Hb_flow with der(U) = Hb_flow + Qb_flow + Wb_flow (0 = ... when SteadyState); PartialDistributedVolume (DynamicPipe) Us[n], Qb_flows[n], Wb_flows[n], Hb_flows[n]; static parts are isenthalpic. **Probed on carbonio (run_fl8_probe1-3, escape hatch):** a circuit boundary -> pump (V 0.001) -> heated dynamic pipe (3 nodes, wall 350 K) -> heated open tank (20 kW) -> orifice -> boundary: U, Qb_flow, Wb_flow of tank and pump, Us/Qb_flows/Wb_flows of the pipe and every port's m_flow and h_outflow are in the result; with H at the boundaries = -m (m < 0: own h_outflow; else the connected port's h_outflow) the balance integrated by OpenModelica itself (tolerance 1e-8) closes to 2.3e-7 of the throughput over 300 s (Q 58.6-64.6 kW, pump Wb_flow = W_total 509-661 W, OpenTank Wb_flow 0, the pipe's Wb_flows ~1e-13). (probe1: a mass-flow source feeding a pump drove the pump inlet below 0 Pa: a study error, not omct's.) runner: fluid_energy_terms / fluid_energy_paths (added to the variable filter) / boundary_enthalpy_in (inStream: the other ports' h_outflow mixed by their outflows; one other port: exactly its h_outflow) / _fluid_energy -> checks.fluid_energy (OpenModelica runs; limit as the mass balance: max(1e-6, 10 tol, 10 Richardson estimate) of the throughput). Fixes found on the way: network's thermal-island rule now counts fl_tank, fl_volume, fl_dynamic_pipe heat ports as setting the temperature; ref's Fluid stand-in heat port conducts 1 W/K to 293.15 K (was flow 0: a heater alone on a tank's heat port made the system singular). Benchmark fl_heated_tank (OpenModelica only: the reference has no temperatures), fl_heated_pipe checks fluid_energy.ok. tests/test_mfluid.py: the energy balance on synthetic series, mixing.


**2026.11.19.16 (2026-11-19)**: V5 FL7 (Nicola: "I'll let you decide"; chosen: check valves and tank port losses in the reference first, the Fluid energy balance next). fluidref: regRoot2 with use_yd0, regSquare2, regStep ported (MSL 4.0.0 Utilities, re-cloned to outputs/msl_src); valve checkValve m = rc Av sqrt(rho_a) regRoot2(dp, dp_turbulent, 1, 0, true, 0); pump checkValve with NoHomotopy (MSL's default): unknown s, head = (N/N0)^2 H(s/rho N0/N) for s > 0 else (N/N0)^2 H(0) - s (unitHead 1 m), m = nParallel max(s, 0); tank ports with use_portsData: an element from the port's node to a node at vessel_ps_static, p = ps + 0.5/Ap^2 regSquare2(m, m_flow_small, (zeta_in - 1 + Ap^2/A^2)/rho pen, (zeta_out + 1 - Ap^2/A^2)/rho/pen), pen = regStep(level - 0.1 D, 1, 1e-3, 0.1 D), VesselPortsData zeta_out 0.5, zeta_in 1.04, height 0; pump volumes covered (constant density: pump.m = rho V). **Probed on carbonio (.15, escape hatch and catalog, run_fl7_probe1-7):** a pump with checkValve closed (outlet above its shutoff head) between boundaries fails after a successful initialisation: 'division by zero ... pump.m_flow * ((if pump.m_flow > 0 ...) - (if -pump.m_flow > 0 ...))' with V = 0, and also with V = 0.001 while the pump keeps MSL's energyDynamics = SteadyState; with V = 0.001 and energyDynamics = FixedInitial (the system's) it runs: m 0, head 25 m (closed), 7.0710678 kg/s and 10 m (open), cross-solver 0. A pump with V > 0 and no check valve failed initialisation with m_flow_start 0 (homotopy) and ran with m_flow_start 5. A pump with V > 0 on a closed loop (tank, two pumps, pipe, valve): 'IndexReduction.pantelidesIndexReduction failed! System is structurally singular' (as fl_volume there in FL2); the same loop with V = 0 pumps runs and equals fluidref (m 4.73963197086665 vs ...649, m2 0.1433349028 2.4e-14, port pressure equal), with tank port losses on all three ports. The valve check valve forward 2.77, backward exactly 0. The tank drain through a port with losses: level 0.8154302269656616 (closed form ...61, fluidref ...610). Rules now: checkValve needs V > 0 (error); V > 0 -> energyDynamics = system.energyDynamics and m_flow_start = rho x the curve's middle flow x nParallel unless given; a pump volume on a closed constant-density loop is refused. network.var_expr: multi ports 'X.port[k].var', 'X.port.m_flow' (sum), 'X.port.p' (one connection) (before: 'X.port.var' gave a path not in the model). runner.fluid_mass_terms counts pump.m. Benchmarks fl_check_valve, fl_tank_port_drain (pass with the reference), fl_loop_reference (OpenModelica only). Version lines fixed: network.py (line 3) was left at .10 in .14/.15, mcp_server.py (line 3) at .10 in .14.
**Carbonio with .16 (bench_20261119_202800_6477c5):** fl_check_valve (exact: 2.77 / 0 / closed at 25 m / 7.0710678), fl_tank_port_drain (2e-15, mass 2.5e-15), fl_loop_reference (OpenModelica against the reference 1.7e-13) pass. 

**2026.11.19.15 (2026-11-19)**: V5 FL6. **Carbonio with .14 (bench_20261119_194844_382c67): all eleven fl_* pass, reliable** (cross_solver now only on fl_heated_pipe and fl_air_fill; fl_cfdt_pump 7e-15, fl_cfdt_resistance 2e-13 against the fitted law and 7.9e-4 against OpenFOAM). **Sweeps and fits over Fluid values with OpenModelica (carbonio, run_fl6_sweepfit1):** a zip sweep of v.opening and v.rangeability (lifted), p.length, p.roughness, v.Kv and pump.N_nominal on one build, every point against the reference <= 4.7e-16; a fit of v.Kv from a steady flow 20.593016840739 (reference 20.593016841039, 1.5e-11), 13 runs on the cached build, 23 overrides read back. **spark1 (aarch64, run_fl6_spark1, .14):** fl_network_reference (reference 2.0e-8), fl_cfdt_pump, fl_heated_pipe, fl_air_fill, fl_two_tanks reliable with the same numbers as carbonio to the last digit printed. Benchmarks fl_sweep_opening, fl_fit_kv (pass with the reference). Seen, not changed: MSL warns 'Wrong pump curve' when the head's slope is 0 at a curve point (a flat shutoff, as [[0, 20], [0.005, 15], [0.01, 0]]: quadraticFlow asserts dH/dV <= -small at every point, level warning); omct only requires the head to fall between points.
**Carbonio with .15 (bench_20261119_195731_7bfe78):** fl_sweep_opening (one build, exact) and fl_fit_kv (1.1e-12) pass. **V5 done** (FL1-FL6). Open: Modelica.Fluid energy balance (temperatures; the reference has no thermal part), T-dependent liquids and gases in the reference (check valves and tank port losses: FL7, .16).

**2026.11.19.14 (2026-11-19)**: V5 FL5, the cfdt chain (Nicola: a component key; least-squares quadratic; a power curve; density scaling). chains: quad_points (least squares, written as 3 points at min/mid/max flow, a line as 2 for the head), cfdt_pump (results.pump.head_m/flow_m3h/power_W, rotating.<zone>.rpm, density = mdot/Q at the inflow; affinity laws V~N, H~N^2, P~N^3 to N_nominal (given or the first result's speed); refuses unreliable results, missing pump data, densities differing by > 1 %, a head that does not fall at the ends), cfdt_resistance (total_pressure_drop at the inflow; NNLS a, b >= 0; one result: b = 0; a = 0 refused). network: the cfdt key (catalog fluid['cfdt'] = pump | resistance), values only where the study gives none, net.inputs['<comp>.cfdt'] with the points and fits; _cfdt_rescale after the medium is known: a x rho/rho_cfdt, b x mu/mu_cfdt (cfdt.mu; cfd-translator's results do not carry its viscosity), media without constant properties: a warning. catalog: pump power ([[V, W]], quadraticPower through quad_points' 3 points; linearPower is avoided: MSL 4.0.0 solves it with ones(3) against 2 points) and rho_power (rho_nominal, structural); eta given + power given refused. **Probed on carbonio (escape hatch, run_fl5_power_probe1/2):** use_powerCharacteristic with quadraticPower fails to initialise with m_flow_start 0 ('division by zero ... pump.V_flow_single_init': MSL's homotopy W = N/N_nominal V/V_flow_single_init P(V_flow_single_init)); with m_flow_start = rho_power x the middle flow it runs (3 homotopy steps), the lifted power points read back (20 overrides), and V, W_total, eta, head equal fluidref's to 8e-16 with water (995.586) against rho_nominal 1000 (the rho/rho_nominal factor). omct now writes that m_flow_start when the study gives none. fluidref: W = (N/N0)^3 (rho/rho_nominal) P(V N0/N) nParallel, eta = dp V_single / W_single. **Real chain (cfdt 2026.11.18.9 on carbonio, run_fl5_pipe_chain1):** a 20 x 200 mm pipe, rho 1000, mu 0.01, laminar, uniform inflow at 0.05/0.1/0.15/0.2 m/s (13520 cells, reliable): fl_resistance from the 0.05, 0.1, 0.2 runs (a 1.225e9, b 5.15e5, rms 1.6e-4) predicts the 0.15 run's flow at its total pressure drop within 7.9e-4 (tests/fixtures/cfdt_pipe_chain1.json; benchmark fl_cfdt_resistance). Benchmarks fl_cfdt_pump (synthetic results shaped like cfdt's, closed form at another speed), fl_cfdt_resistance. tests/test_cfdt_chain.py (10). tools/check_catalog_msl.py fl_: 0 problems.

**2026.11.19.13 (2026-11-19)**: format.cross_checked: Fluid studies the reference covers (format.fluid_covered -> fluidref.covered) skip the cross-solver run unless cross_check is true (Nicola: drop it); custom models and uncovered Fluid studies keep it; executor estimates follow. **Carbonio with .12 (bench_20261119_190837_94db43): all nine fl_* pass, reliable** (drain 3e-15 and mass 3e-15, laminar 2e-16, pump exact, valve exact, heated pipe 5e-8 K, air T 0.12 K / m 7e-7, turbulent 4e-16, two tanks 2e-8 / 4e-8 and mass 2e-7, network reference covered 2e-8).

**2026.11.19.12 (2026-11-19)**: V5 FL4, the reference for liquids. fluidref.py: MSL 4.0.0 relations ported from the sources (tag v4.0.0; read in full, not from memory): Utilities.regRoot, regRoot2 (+ regRoot2_utility, evaluatePoly3_derivativeAtZero), regFun3, cubicHermite_withDerivative; Dissipation SmoothPower, dp_volumeFlowRate_MFLOW/DP; WallFriction.Detailed.massFlowRate_dp_staticHead with **Detailed.Internal**.m_flow_of_dp_fric (Hagen-Poiseuille / Colebrook-White / log-log Hermite; Pipes.mo has a second Internal.m_flow_of_dp_fric in LaminarAndQuadraticTurbulent, quadratic with zeta from roughness: not the one Detailed uses), dp_small = system.dp_small (use_eps_Re false), Re_turbulent 4000, continuousFlowReversal false (useUpstreamScheme) -> the staticHead branch; NominalLaminarFlow dps_fg = g dh rho + dp_nominal/m_flow_nominal m (total flow); SimpleGenericOrifice (from_dp, dp_turbulent = dp_small, k = 8 zeta/(pi^2 D^4)); GenericResistances.VolumeFlowRate (its dp_small from dp_volumeFlowRate_DP at m_flow_small, dp_min 1e-10); ValveIncompressible: opening_actual = opening when filteredOpening is false (the minLimiter with leakageOpening only feeds the filter: no leakage limit), regRoot2(dp, dp_small, rho, rho), OpPoint Av from rc(opening_nominal 1) sqrt(rho_nominal) regRoot(dp_nominal, dp_small); ValveLinear; PartialPump (head = (N/N_nominal)^2 f(V_single N_nominal/N), quadratic and polynomial curves extrapolate linearly from the end slopes); OpenTank without portsData: ports[i].p = p_ambient + rho g max(0, level), level_start_eps = max(level_start, eps); PartialSource: all ports at medium.p, sum(ports.m_flow) = -m_flow (a source's ports share one pressure). Solver: Newton (scipy hybr, then lm) on flows and free node pressures (bar), warm-started; tank levels by Radau rtol 1e-10. covered(net): mfluid kinds only (mixed domains not), liquid_const media, no p_in/m_flow_in/opening/w_in inputs (T_in and heat ports fine), fl_pipe detailed or nominal_laminar, fl_resistance a > 0, no check valves, no pump volume, no tank port losses, tanks with fixed_initial, allowFlowReversal true. Integration: modelica.fluid_ref (tr.ref = FluidRef when covered, else Ref with the reason in not_exact; format's warning uses it); runner._reference skips outputs the reference does not compute (not_compared; temperatures), _ref_eigen/_run_ref linearize refuse, _run_ref adds checks.mass (_Series shim); fit uses it through tr.ref (steady(): algebraic without tanks). catalog: fl_resistance a > 0 (MSL 4.0.0's dp_volumeFlowRate_MFLOW returns rho b dp for a = 0, wrong units). Benchmarks fl_pipe_turbulent (Colebrook explicit in dp), fl_two_tanks (tau = k/(g(1/A1 + 1/A2))), fl_network_reference (judged against the reference; OpenModelica only); the four liquid FL3 cases leave omc_only (pass with solver ref: drain 7e-16, laminar 4e-16, pump exact, valve exact). tests/test_fluidref.py (14). **Checked on carbonio before the release (omct .11, run_fl4_precheck1, compared here with fluidref):** network initial values equal to 4e-15 (transitional pipe Re 2790, equal-percentage valve, orifice, a,b resistance, pump at 1400/1450 rpm, relative-pressure and mass-flow sensors), finals 2e-10 to 1.2e-8 (OpenModelica's integration at tol 1e-6); two tanks h1 2e-8; drain 3e-15; turbulent pipe 0.37893547357999263 (= Colebrook, = fluidref); pump V exact.

**2026.11.19.11 (2026-11-19)**: V5 FL3. format.has_fluid / cross_checked: custom models and Fluid studies get runner._cross_solver (cross_check allowed for fl_* studies; executor counts the run). runner._fluid_mass: inflow of fl_boundary / fl_mass_flow ports (-ports[k].m_flow) by trapezoid with a Richardson estimate (_trapezoid_richardson, every other point, last kept) against the change of tank/volume m and dynamic-pipe ms[i]; allowed max(1e-6, 10 tol, 10 x estimate) of the throughput; not covered with a pump volume; paths added to the variable filter (fluid_mass_paths). OM_NOTES -> model_info.notes. Benchmarks fl_* (bench.FLUID_CASES, in omc_only). **Checked on carbonio before the release** (the generated models, escape hatch, run_fl3_bench_escape): drain level 0.62113657977462 (closed form ...46211), laminar m 2.4543692606e-4 (exact), pump V 8.13826697e-3 (exact), valve 2.77 (exact), heated pipe T 329.5976793 (closed form ...6794, 5e-8 K), air T 355.380 (constant-gamma estimate 355.496, 0.12 K: cp varies) and m 0.21612346 (7e-7). Also run_fl2_catalog1 (.10, normal studies): loop, heated + speed input, air, drain, glycol, pump shaft all reliable. Tests: 497 pass (6 skipped).
**Carbonio with .11 (bench_20261119_183437_76bab0): all six fl_* pass, reliable, with checks.cross_solver and checks.mass run** (mass error 3e-15 drain, 1.7e-6 air; drain level 3e-15, laminar 2e-16, pump exact, valve exact, heated pipe 5e-8 K, air T 0.12 K off the constant-gamma estimate). **Next: FL4** (the reference for liquids), then FL5 (cfdt chain). mochi and the sparks still run .9.

**2026.11.19.10 (2026-11-19)**: V5 FL2, Modelica.Fluid kinds. catalog: port type mfluid; kind() takes multi ({port: (array, size)}) and fluid ({build(p, e) -> (modifier texts, lifted values), collector, speed_in, pressure_ref, vessel, volume, liquid_only/gas_only, pump}); FMEDIA, fmedium(), FMED; 19 fl_* kinds (MSL names checked: tools/check_catalog_msl.py /tmp/msl_src fl_ -> 0 problems; the tool now knows multi ports, redirects, var sums, fmedium). network: fmedium values; multi ports -> 'port[k]' per connection (_multi_index), port_type/mpath for them; redirects (helpers' ports); var_paths as sums; cond_ports with flag None; _fluid_sets (system naming, flags _n_ports/_opening_connected/_w_in/_heat, valve sizing, curve falling, medium spread and mixing, pressure reference, valve phase, closed-loop rule via _closed_loops/_cycle_edges (bridges), modelStructure av_b/av_vb). modelica: fmedia packages, redeclare Medium/FlowModel/HeatTransfer/functions, lifted top-level parameters (+ their overrides), helpers (ThermalCollector, Gain rad/s -> rpm), inner system (default or fl_system). ref: Modelica.Fluid stand-in (heat/flange flows 0, outputs 0; not covered). format: study_format(topic='fluid') (6.6 k chars; the main one 31,946: two convention sentences shortened), MCP study_format(topic). Fixes: simrun min/max violations -> warnings (minmax); runner.allowed / post.compare: range < CONSTANT_REL (1e-9) x size = constant. **Checked on carbonio (escape hatch with the generated text and every override, run_fl2_generated1-2):** loop (tank, pump, pipe, valve) ✓ 21 overrides read back; dynamic pipe after the pump with heat (ThermalCollector) and the speed input (Gain) ✓ 25 overrides, 50 kW into the water, pump at 1594.7 rpm from 167 rad/s, cross-solver retried at 1e-8 (2e-6); air volume filled through a pipe and emptied through fl_valve_gas ✓; tank draining through an orifice with a pressure sensor ✓; glycol47 through a resistance with T, mass-flow and relative-pressure sensors and fl_system steady_state_initial ✓; fl_pump_shaft on an inertia ✓. A DynamicPipe's length and height_ab were fixed by the build (structural now). tests/test_mfluid.py (16). Tests: 489 pass (6 skipped).
**Next: FL3** (balances, cross-solver for Fluid studies, fit_*-style benchmarks with closed forms on carbonio), then FL4 (the reference for liquids), FL5 (cfdt chain). Deploy .10 and rerun the generated cases through the normal path (catalog studies, not the escape hatch).

**2026.11.19.9 (2026-11-19)**: F4 on carbonio (OpenModelica 1.27.2, 2026.11.19.8, bench_20261119_145315_56d73d): fit_rc_tau (R 1000.61, 1.9 se; stderr 0.31659 against 0.31666; chi2_reduced 0.77), fit_rc_unidentifiable (correlation -1.0), fit_thermal_curve (m 2.0000078, h 9.9999932, T0 303.15007), fit_msd (c 799.99998, d 4.0000196) pass, all reliable. **fit_thermal_steady failed and said reliable**: h 21.54 for 10, 'converged' by xtol, 28 of 78 runs failed 'steady point: T still changes' (the bench's 20000 s is 6.7 tau at h = 10; the reference path's exact steady state hid it). Fix: optimize keeps each failed run's x; a start whose end point has a failed run within 1 % of a parameter's (x-space) range and did not end by gtol has failed_at_end > 0 -> unreliable ('it stopped where the model's runs fail', with the last error). A counter of failures since the last Jacobian missed it: a tiny accepted step after the failures reset it. Bench fit_thermal_steady at 90000 s; settled_end's message gives the change and the limit. Test (fake omc): a divider whose steady runs settle only away from the optimum -> unreliable with that reason. **Rerun on carbonio with .9 (bench_20261119_153328_39e8e8): all five fit_* pass, reliable; fit_thermal_steady h 10.000000021.**

**2026.11.19.8 (2026-11-19)**: V4 F3. fit.Budget (timeout from t_start, + extended_s of the study folder, - reserve min(0.25 timeout, max(20 s, 10 x first run)) set by the first evaluation); OmcEvaluator's run timeout also <= the budget left. optimize: Stop (BaseException) from raw() at the budget (time_limit: ends all starts) or at a start's share of max_evaluations (counted in model runs); the start's result = its last Jacobian's x, J, f (status -10 time_limit / 0 max_evaluations); nothing yet -> run_fit ok false 'ran out before its first step'. Not converged -> unreliable_because. Final run's timeout = what is left (>= min(timeout, 60 s)). runner: no record_study for a fit. executor: _parallel (fit: n params) in cpus_for_job and run_study_job; fit_time (expected: 15 iterations a start, upper: the share; run = s_per_interval x max(intervals, 2000) + 0.3 s); estimate_minutes for fits (min(upper, timeout) + final + build); _cache_dir builds a fit's key at the initial values. api.validate warning; api._progress fit stage (fit_progress.json: start, evaluations, failed, best_cost; 'final run' once final/ exists); job_progress passes evaluations. Images: fit.plot (the line from the final run's series); render() reads final/series.npz for a fit; render_images adds fit.plot. **Bug fixed**: with sigma, stderr was sqrt(n) too large (chi_factors undo the 1/sqrt(n) and weight; the earlier test only bounded it); chi2_reduced. Benchmarks fit_rc_tau (stderr against the analytic one: 5 %; chi2 ~1), fit_rc_unidentifiable, fit_thermal_steady, fit_thermal_curve, fit_msd (data from closed forms). MCP instructions: fit, rewrapped to 118 columns (test: <= 30 lines). study_format 31,963 chars. Tests: 470 pass (6 skipped).
**Next: Modelica.Fluid** (F4 done in .9): see "V5: Modelica.Fluid" (FL2 in .10). The router's omct description (llm-translators) does not mention fit: for the llmt chat.

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

**Status (2026.11.19.9):** F1-F4 done: the five fit_* benchmarks pass with OpenModelica 1.27.2 on carbonio (2026-11-19). Open: a real measured data set when Nicola has one; mochi and the sparks still run .6 until deployed. F2 checked on carbonio (2026-11-19, see Recent changes).

**F3 (built 2026.11.19.8; Nicola, 2026-11-19: whole-fit budget):** run.timeout_s (default 600 s, within the caps) limits the whole fit like one study (job limit = it + 10 min, sweep_rounds stays 1). The optimizer stops at timeout - reserve (reserve for the final run: min(0.25 timeout, max(20 s, 10 x first run))), deadline moved by extend (llmt_runtime.jobs.extended_s of the study folder); each run's timeout also <= the time left. At the deadline: the last accepted iterate (the x of the last Jacobian, its J for the uncertainty), status time_limit; nothing yet: ok false naming the first run's time. max_evaluations counts model runs per start (Jacobian runs included; a hard stop, the same best-so-far path). Not converged (time_limit, max_evaluations, failed) -> unreliable. Sizing: cpus_for_job uses min(run.procs, n_params) for fits (today it gives 1); estimate_minutes for fits from the measured run (runs.json) x expected runs, capped at the timeout; api.validate warns when the expected time exceeds timeout_s. runner.run_study must not record_study a fit's total time (it would become the sweep points' study_s). api._progress: stage from fit_progress.json (start, evaluations, best cost) and 'final run'. Then images (data vs model + residuals, steady model vs data), benchmarks fit_*, README section.

**Open questions** (decide while building, or ask): whether the fast path may skip reading the full MAT file (a
variable filter on the outputs only); the CSV's location for router placement (a path under the machine's home: the
router cannot see it; the study gives `run.host`).

## V5: Modelica.Fluid (design, 2026-11-19; FL1 probes, FL2 2026.11.19.10, FL3 .11, FL4 .12, FL5 .14, FL6 .15: done)

Nicola's choices (2026-11-19): media = liquids (constant properties, Incompressible tables) and ideal-gas air;
components = pipes/fittings/valves, pumps, vessels and boundaries, and the cfdt chain (pump curves, fitting losses);
checks = the Python reference for incompressible single-phase liquid networks, mass and energy balances plus the
cross-solver run for everything else. FluidHeatFlow coolant loops (V2.2, port type `fluid`) stay as they are.

**Port type `mfluid`** (Modelica.Fluid.Interfaces.FluidPort: potential p, flow m_flow, stream h_outflow; Xi/C empty
for the chosen media). Components with `nPorts` arrays (boundaries, tanks, volumes) get one port per connection,
numbered by the network in connection order (`ports[k]`), as plugs are split into pins today. More than two
connections at one port are ideal mixing (stream connectors); validation warns to put a volume there when a loop
needs one (OpenModelica's handling of such sets: probe P5).

**Media** (structural: one package per connected set, checked like FluidHeatFlow's one medium per loop):
`water` (Modelica.Media.Water.ConstantPropertyLiquidWater), `glycol47`, `essotherm650` (Media.Incompressible
examples: density and cp follow T), `air` (Media.Air.DryAirNasa, ideal gas), `simple_air` (Media.Air.SimpleAir),
and a custom liquid {rho, cp, eta, lambda} as a generated package extending ConstantPropertyLiquidWater with those
constants (probe P3). The FluidHeatFlow media names (water_90degC, ...) are not reused: those are records, these are
packages.

**System:** one `inner Modelica.Fluid.System system` added whenever a study has mfluid parts; an optional
`fluid_system` component sets p_ambient, T_ambient, g, allowFlowReversal, energy/mass dynamics (default
FixedInitial for energy and mass: every storage starts at its own T_start / p_start / level_start, as v0 does today;
momentum steady, MSL's default), m_flow_small, dp_small.

**Kinds** (MSL class; parameters with units as usual; `*` structural):
- Boundaries: `fl_boundary` (Sources.Boundary_pT: p, T, signal forms p_in/T_in*), `fl_mass_flow` (MassFlowSource_T:
  m_flow or V_flow, T, signal form*), `fl_ambient` (FixedBoundary at the system's p and T).
- Pipes and fittings: `fl_pipe` (Pipes.StaticPipe: length, diameter, roughness, height_ab, nParallel, flow model*
  detailed | laminar | turbulent | nominal {dp_nominal, m_flow_nominal}), `fl_dynamic_pipe` (Pipes.DynamicPipe:
  the same + nNodes*, heat transfer* ideal | constant {alpha} | local (Dittus-Boelter / laminar), one `heat` port
  through a ThermalCollector), `fl_resistance` (Fittings.GenericResistances.VolumeFlowRate: a, b; or from cfdt),
  `fl_orifice` (Fittings.SimpleGenericOrifice: diameter, zeta), `fl_bend` (Fittings.Bends.CurvedBend: R_0, d_hyd).
- Valves: `fl_valve` (ValveIncompressible / ValveCompressible* by medium; Kv | Cv | Av | op point {dp_nominal,
  m_flow_nominal}; characteristic* linear | quadratic | equal_percentage {rangeability}; `opening` signal input or
  constant; filteredOpening, riseTime; checkValve*), `fl_valve_linear` (ValveLinear), `fl_check_valve` (a valve
  with checkValve and opening 1).
- Pumps: `fl_pump` (PrescribedPump: N_nominal, N (constant) or signal N_in*; head curve from points
  [[V_flow, head], ...] (2 points linear, 3 quadratic, more polynomial*) or from cfdt results; efficiency constant or
  points, or power points; nParallel, V, checkValve), `fl_pump_shaft` (Pump: the same curve, driven by a rotational
  flange: a motor from the catalog), `fl_controlled_pump` (ControlledPump: m_flow or p_b set point, constant or
  signal).
- Vessels: `fl_tank` (Vessels.OpenTank: height, crossArea, level_start, port diameters/heights per connection,
  heat port), `fl_volume` (Vessels.ClosedVolume: V, p_start, T_start, heat port).
- Sensors: `fl_pressure`, `fl_temperature` (two-port), `fl_mass_flow_sensor`, `fl_volume_flow_sensor`,
  `fl_relative_pressure`, `fl_relative_temperature` (signal outputs, usable in control loops and as outputs).
- Variables for outputs without sensors: pipes m_flow, V_flow, dp; pumps head, dp, m_flow, V_flow, W_total, eta,
  N; tanks level, T, m; volumes p, T, m; boundaries m_flow (positive into the network).

**study_format size:** ~25 kinds do not fit under 32,000 characters (31,963 now). Proposal: study_format() lists
the Fluid kinds by name with one line on the domain, and `study_format(topic="fluid")` returns their parameters,
ports, media and conventions (as cfdt's guide('pumps')). The MCP tool gains an optional `topic`.

**Checks:**
- Reference (`fluidref.py`, the "covered" studies): incompressible, single phase, constant-property media
  (water, custom liquids); boundaries, mass-flow sources, static pipes (MSL's WallFriction Detailed /
  Laminar / QuadraticTurbulent / nominal, ported with their regularisation at small flows), generic resistances,
  orifices, incompressible valves (all characteristics, every Cv form), pumps (the three characteristics and the
  affinity laws), open tanks (level ODE, static head at the port heights), closed volumes (incompressible:
  algebraic), static head height_ab; temperatures by upwind energy balances in tanks and volumes. Momentum is
  steady (MSL's default), so the hydraulics are algebraic at each instant (Newton on the loop flows), the levels
  and temperatures are the states. Sources from blocks (step, ramp, table) are covered; Fluid loops with feedback
  controllers, dynamic pipes, gases and T-dependent media are not ("covered: false", the balances and the
  cross-solver run instead).
- Balances (every Fluid study): mass (sum of boundary flows against the stored mass's rate: tanks, volumes, dynamic
  pipes) and energy (enthalpy flows, heat ports, pump shaft or electrical work, against the stored energy's rate),
  as the existing energy check does, judged against the flows' scale.
- cross_solver for Fluid studies the reference does not cover: the other integrator at tolerance/10 (today only
  custom models get it).

**cfdt chain** (chains.py, as b3dt's inertias): a pump's head curve `{"from": "cfdt", "results": [<cfdt study
results at several flows>]}` takes pump.flow_m3h, pump.head_m and pump.efficiency from each (at least 2; rpm from
the rotating zone; density for the conversion); a resistance `{"from": "cfdt", "results": [...]}` fits a, b of
dp = a V^2 + b V to the cfdt pressure drops (total pressure) at their flow rates (one result: b = 0). The result's
`inputs` lists every value taken, as now.

**Probes (FL1; OpenModelica 1.27.2 on carbonio, through omct's escape hatch; nothing here is assumed):**
- P1 MSL's Fluid examples build and run: Examples.PumpingSystem, Examples.Tanks.ThreeTanks,
  Examples.HeatingSystem, Examples.ControlledTankSystem.ControlledTanks, Examples.AST_BatchPlant? (time each).
- P2 Which parameters change at run time (modelica.values on each): pipe length, diameter, roughness, height_ab;
  valve Kv, dp_nominal, m_flow_nominal; the pump curve's points (function modifiers), N_nominal, N; tank crossArea,
  height, level_start; boundary p, T, m_flow; medium constants of a custom liquid. What is evaluated decides what
  a sweep or a fit may change.
- P3 A custom constant-property liquid as `package L = Modelica.Media.Water.ConstantPropertyLiquidWater(cp_const=..,
  d_const=.., eta_const=..)` (or extending PartialSimpleMedium) compiles and runs.
- P4 Initialisation of a pump - pipe - valve - open-tank loop with FixedInitial and with MSL's defaults.
- P5 Three connections at one port (a tee without a volume): builds, initialises, mixes.
- P6 DynamicPipe with nNodes 5, heat ports joined by a ThermalCollector to a prescribed temperature.
- P7 Air (DryAirNasa): a mass-flow source filling a ClosedVolume through a pipe; run time.
- P8 A pump on a rotational flange driven by a catalog dc_motor (the coupling omct will generate).

**FL1 probe results (carbonio, OpenModelica 1.27.2, omct 2026.11.19.9 escape hatch, 2026-11-19; requests
run_fl1_p1, p1b, p2, p9, p10, p11, p12):**
- P1 MSL examples: Tanks.ThreeTanks ✓ (build 2.8 s, run 0.1 s, cross-solver 8e-6), ControlledTankSystem.ControlledTanks ✓
  (0.02 s), IncompressibleFluidNetwork ✓ (0.36 s, 3 homotopy steps). PumpingSystem ran to the end ("simulation
  finished successfully") but omct called it failed: OpenModelica reports min/max violations ("Variable violating
  min/max constraint", here pipe m_flows during homotopy and the example's own opening 1+1e-6) as LOG_ASSERT
  warning/info and goes on; simrun.parse_log counts every LOG_ASSERT with 'violated' as an assertion -> **omct fix
  (FL2): min/max violations are warnings**. HeatingSystem (CompressibleLiquids.LinearWater_pT_Ambient) does not
  initialise (NLS 'm_flow is inf or nan', with dassl and ida); with ConstantPropertyLiquidWater it does not build
  (structurally singular, also with steady mass balances in pipes and pump).
- P2 ✓ every value tried changes at run time and reads back (15): pipe length, diameter, roughness, height_ab;
  valve Kv, dp_nominal, m_flow_nominal; tank crossArea, height, level_start; pump N_nominal, N_const; the pump
  curve's points as top-level parameter arrays (Hc[2], Vc[2]) passed to the function modifier; opening.k. So
  omct declares curve points as model parameters and sweeps/fits can change them.
- P3 ✓ `package Liq = Modelica.Media.Water.ConstantPropertyLiquidWater(cp_const=3800, d_const=1050,
  eta_const=0.004)` builds and runs (medium constants: structural).
- P4 ✓ closed loop open tank -> PrescribedPump -> StaticPipe -> ValveIncompressible -> tank, FixedInitial energy
  and mass: 3 homotopy steps, 4.779 kg/s, head 15.857 m, cross-solver 1e-6. MSL warns "Wrong pump curve" for a
  curve flat at zero flow (its own check: slope <= -small everywhere) and a unit warning in PartialPump
  (V_flow_single_init has unit m): MSL's, harmless; omct may filter the second.
- P5 ✓ two MassFlowSource_T (1 kg/s at 300 and 350 K) on one pipe port: 325.000 K, 2 kg/s (ideal mixing works).
- P6 ✓ DynamicPipe nNodes 5, ConstantFlowHeatTransfer, ThermalCollector(m=5) to a fixed 350 K, between two
  Boundary_pT: outlet 321.163 K, Q = 22855 W = m cp dT (0.2581 x 4184 x 21.16): energy balance exact; build 5.2 s.
- P7 ✓ DryAirNasa MassFlowSource_T 0.01 kg/s into a 0.1 m3 ClosedVolume: p 1e5 -> 2.205e5 Pa, T 300 -> 355.4 K in
  10 s (adiabatic filling), cross-solver 3e-5.
- P8 ✓ Machines.Pump driven through its shaft by Rotational.Sources.Speed (ramp to 157 rad/s): 8.14 kg/s, 4.21 N.m.
- **Closed incompressible loops with volumes (P10-P12; tank -> pump -> X -> valve -> tank, constant density):**
  DynamicPipe modelStructure av_b ✓ (also with the pump's balances steady, and two av_b pipes in series);
  av_vb ✗, a_v_b ✗, a_vb ✗, av_vb with steady mass ✗ (all "structurally singular", Pantelides fails);
  a ClosedVolume between pipe and valve ✗. In an open circuit (Boundary_pT at both ends) av_vb ✓. So with constant
  density OpenModelica accepts a volume only where it adds no second pressure state to a loop that the tank
  already fixes; the general rule is not established yet (FL2 decision below).
- **omct issue found (any study):** runner.allowed() gives a huge tolerance (7.7e9) to an output that is
  constant up to rounding (range 1e-15 of its size): 10 tol max|y| / range. compare() and allowed() should treat
  a range below ~1e-9 of the size as constant (FL2 fix; it hides nothing in the probes, all within 1e-6).

**Decision (Nicola, 2026-11-19): safe rule now, widen later.** In a closed loop of a constant-density medium
(no Boundary in the connected set, an open tank as its pressure reference) omct gives each dynamic pipe
modelStructure av_b (its volume at the inlet) and refuses closed volumes, with a validation error naming the fix
(an open tank, or an open circuit with boundaries). FL2 starts with a probe matrix on carbonio (pipe positions next
to pumps, valves, tanks and tees; several pumps; two tanks; pumps with V > 0) and widens the rule from what builds;
each case is recorded here. Open circuits and gases are not restricted.

**FL2 probe matrix (carbonio, 2026-11-19, requests run_fl2_matrix1-3; ConstantPropertyLiquidWater, FixedInitial
unless said):**
- In a closed loop (a cycle through tanks, pumps, pipes, valves): ✓ only a DynamicPipe av_b whose port_a is the
  single partner of one pump's port_b (also with the pump's V = 0.001 or its balances steady). ✗ (structurally
  singular / StateSet Jacobian non-linear): av_b on the suction side, after the valve, after a static pipe, after
  two pumps in parallel, as two branches after one pump, connected backwards; two tanks in a loop with a dynamic
  pipe; a ClosedVolume at the pump outlet; av_vb. With massDynamics SteadyState everywhere but the tanks: the same
  ✗ (and av_vb fails in the C build).
- Without a cycle: ✓ tank -> pump -> DynamicPipe av_vb -> Boundary_pT; ✓ tank -> DynamicPipe av_vb -> tank (no pump);
  ✓ tank -> pump -> ClosedVolume -> static pipe -> Boundary_pT (cross-solver retried at 1e-8: 4.6e-8).
- ✓ two tanks in a loop with a pump and static pipes only.
- **Rule built (FL2):** with a constant-density medium, a fixed-volume component (fl_dynamic_pipe, fl_volume) on
  a hydraulic cycle is refused, except a dynamic pipe whose inlet connects only to one pump's outlet (omct gives it
  av_b). Cycles: the graph of connection sets joined by two-port components and by tanks (a tank's ports share its
  volume); boundaries (fixed pressure) cut it. Open circuits and gases: no restriction. Closed liquid loops with
  heat transfer: the FluidHeatFlow kinds (coolant loops, V2.2) model them.

**Milestones:** FL1 probes (above) and their record; FL2 catalog, network (mfluid, ports[k], media sets, system),
modelica (redeclare Medium, system, ThermalCollector, the generated custom medium), validation, study_format
topic, tests with the fake omc; FL3 balances, cross_solver for Fluid, benchmarks with closed forms on carbonio
(laminar pipe Hagen-Poiseuille, turbulent pipe against the Colebrook friction factor (MSL's own approximation:
tolerance from its stated error), Torricelli tank draining through an orifice, pump on a system curve (the two
quadratics' crossing), valve Kv at its op point, two tanks equalising, a heated pipe's outlet T = T_in + Q/(m cp),
air volume filling (ideal gas)); FL4 the reference for liquids; FL5 the cfdt chain; FL6 docs, fit and sweep over
Fluid parameters (whatever P2 allows), machines.

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

**Carbonio with .20 (bench_20261119_214014_50b86e): 20 of 21 fl_* pass, reliable.** FL10 works there: fl_pump_into_tank runs (the pump volume straight into a tank failed to initialise before), fl_heated_loop passes (a ClosedVolume pump and a DynamicPipe on a closed liquid loop; mass and energy balances ok). The miss: fl_pump_into_tank's benchmark check max_error_rel 1.445e-4 > 1e-4 (tank T only; m and level 1.3e-13; omct's own allowance for T 3.54e-4, so checks.reference ok and reliable true). Rerun at tolerance 1e-8 (run_pit_tol8): T 2.36e-6 against the reference, fluid_energy 1.7e-8, mass exact. **2026.11.19.21**: the benchmark runs at tolerance 1e-8, like fl_drain_torricelli and fl_two_tanks.

**Carbonio with .21 (bench_20261119_214732_11f45f): all 21 fl_* pass, reliable.** fl_pump_into_tank against the reference 2.3e-6 (tolerance 1e-8), fl_heated_loop mass, energy and cross-solver ok, fl_thermal_reference 1.8e-6, fl_pump_heating T 1.6e-7 K. Open: dynamic pipes in the reference; T-dependent liquids and gases; Fluid energy in sweep points.

**2026.11.19.22 (2026-11-19)**: V5 FL11, dynamic pipes and closed volumes in fluidref (Nicola: "Go ahead" on item 1 of the open list). Ported from MSL 4.0.0 (outputs/msl_src): PartialTwoPortFlow (av_vb: n-1 flow segments, port_a.p = mediums[1].p, port_b.p = mediums[n].p, pathLengths {L1 + L2/2, (Li + Li+1)/2, ..., Ln-1/2 + Ln}, L for n = 2; av_b: n segments of L/n, the last to port_b), PartialStaggeredFlowModel with momentum SteadyState (system default: from_dp, Ib_flows 0, dps_fg = p_i - p_i+1), PartialGenericPipeFlow with continuousFlowReversal false (useUpstreamScheme, allowFlowReversal): massFlowRate_dp_staticHead with dp_small/nFM, NominalLaminarFlow dp_nominal/nFM; PartialDistributedVolume der(Us) = Hb_flows + Wb_flows + Qb_flows, upwind H_flows (semiLinear), DynamicPipe's Wb_flows per modelStructure (steady momentum: -m g dh per volume), ConstantFlowHeatTransfer and LocalPipeFlowHeatTransfer (spliceFunction of MoistAir.Utilities, Re = |vs| rho D / mu, Pr = mu cp / lambda, lambda_const 0.598) through omct's ThermalCollector; ClosedVolume without portsData (ports at medium.p, ports_E_flow 0, Wb_flow 0, IdealHeatTransfer on the heat port). Mixing now on the study's connection points (a closed volume's or a multi-port source's ports join one hydraulic node, not one mixing point). Not covered: modelStructure a_vb / a_v_b (omct does not write them), ideal heat transfer with heat connected (volumes at the wall T: temperatures off), dynamics other than fixed_initial with volumes (temperatures off), a closed volume port sharing a connection point with a boundary, tank, multi-port source or another volume port. format.fluid_covered also needs the temperatures (FluidRef.thermal) to skip the cross-solver run. **Carbonio (.21, run_fl11_probe1, probe2; tolerance 1e-8)**: heated closed loop (av_vb, check-valve pump volume) tank T 1.3e-9, Q 7.6e-9; inclined pipe with nParallel 2 (m exact; outlet T transient 2.2e-9: the Wb_flows term is 3e-3 K there); av_b after a pump with local heat transfer and height 1 m: m exact, T 1.9e-9, Q 2.4e-8, p_b 1e-16; reversed flow with local heat transfer: m exact, Q 6e-12; a two-port closed volume with a conductor and a laminar 2-node pipe: vol T 5e-14, outlet T 1.8e-9, Q 6e-13. **nNodes 1 with av_vb** does not build: "Type mismatch in equation pipe.pathLengths[1:pipe.n - 1] = cat(...) of type Real[0] = Real[2]" (p6_n1): network error now. Benchmarks fl_pipe_loop_reference, fl_volume_reference; fl_heated_pipe adds the reference check; fl_heated_loop judged against the reference (tolerance 1e-8) instead of the cross-solver (covered studies skip it). Open: T-dependent liquids and gases; Fluid energy in sweep points; the fluid_energy check in solver "ref" runs.


**Carbonio with .22 (bench_20261119_220627_d0e7df): all 23 fl_* pass, reliable.** Against the reference: fl_heated_loop 7.2e-8, fl_pipe_loop_reference 4.3e-8 (av_b, local heat transfer), fl_volume_reference 4.9e-8, fl_heated_pipe ok (T 3e-9 K from the closed form); the others as with .21. Open: T-dependent liquids and gases; Fluid energy in sweep points; the fluid_energy check in solver "ref" runs.

**2026.11.19.23 (2026-11-19)**: V5 FL12, the fluid_energy check in solver "ref" runs (Nicola: "go ahead" on item 1). runner._run_ref calls _fluid_energy on the reference's series when FluidRef.thermal. fluidref writes the paths fluid_energy_terms asks for, as MSL 4.0.0 defines them: ports[j].h_outflow of boundaries, sources (their h), tanks and closed volumes (medium.h), dynamic pipes (mediums[1].h, mediums[n].h), pump volumes (medium.h); PartialTwoPortTransport and flow sensors pass inStream across (port_a.h_outflow = inStream(port_b.h_outflow)); a pump without a volume medium.h = inStream(inlet) + W/|m| (steady PartialLumpedVolume); PartialAbsoluteSensor / PartialRelativeSensor m_flow 0, h_outflow = Medium.h_default (cp (293.15 - T0)). inStream: the other ports' outflows mixed by their flows; with no outflow at the point, the other port's h_outflow (two ports, Modelica's stream semantics) or the mean of the others (iterated along chains of parts without flow). OpenTank: Wb_flow = 0 (singleState media: MSL neglects -p_ambient der(V)), Qb_flow = the heat port's, ports_E_flow = m v^2/2 with port_diameter (portsData_height 0) else 0; ClosedVolume Wb_flow 0, ports_E_flow 0; PartialPump Wb_flow = W_total, Qb_flow 0, U = rho V u (0 without a volume). The h_outflow pass runs at output points only. Locally (solver ref) every fl_* benchmark the reference covers passes the check; its errors equal OpenModelica's own fluid_energy errors on carbonio (fl_pipe_loop_reference 0.0101, fl_heated_loop 3.67e-5, fl_volume_reference 2.42e-4: the trapezoid on the output grid), and a 2 kJ/kg error in one boundary's h_outflow fails it (test). fl_heated_tank leaves FLUID_OMC_ONLY. Open: Fluid energy in sweep points; T-dependent liquids and gases.

**2026.11.19.24 (2026-11-19)**: V5 FL13, Fluid energy in sweep points (Nicola: "go ahead"). Each point is a full run_study, so the mass and fluid_energy checks (and the electrical/mechanical energy check) already ran on every point and a failing one made the point unreliable; the sweep table and summary showed only the independent checks (reference, cross_solver, invariants, converge). sweep._point_checks now adds BALANCES (energy, mass, fluid_energy) where covered, error_rel as max_error_rel, so sweep.checks.<balance>.{points, failed, max_error_rel} and checks.<balance> appear for sweeps as for single studies. fl_sweep_opening checks sweep.checks.mass.failed and fluid_energy.failed = 0. Open: T-dependent liquids and gases in the reference.

**2026.11.19.25 (2026-11-19)**: V5 FL14, T-dependent liquids (Nicola: "Liquids + both gases"; liquids first). **Probes (carbonio, .24):** run_fl14_probe1 (default tolerance) and run_fl14_probe2 (tolerance 1e-8): every glycol47 / essotherm650 configuration builds and runs in OpenModelica 1.27.2 with MSL's preferred states (static chain, heated tank, heated dynamic pipe, closed loop with a dynamic pipe, a closed volume with a heat port, a pump volume into a tank, av_b loop with local heat transfer and tank port losses, reversed flow through a valve and resistance, essotherm static chain with a 1 m rise and a locally heated rising dynamic pipe). **Ported (MSL 4.0.0):** Incompressible.TableBased (npol 2 Polynomials.fitting over degC; log(eta) over 1/T [K]; h = integral of cp from T0 = 0 degC, enthalpyOfT; u = h - reference_p/d, reference_p 1.013e5, singleState so OpenTank's Wb_flow = 0; T_ph inverts h(T); rho_nominal = density at T_default 20 degC for pumps and OpPoint valves, the resistance's dp_small at reference_T 25 degC); each relation's densities and viscosities at MSL's states: PartialTwoPortTransport state_a/state_b = inStream at each port (valve regRoot2(dp, dp_small, d_a, d_b), check valve sqrt(d_a); orifice d_a/k, d_b/k; resistance rho = regStep(dp, d_a, d_b, dp_small); Detailed.massFlowRate_dp_staticHead with rho_a/rho_b, mu_a/mu_b and dp_a = max(heads) + dp_small, dp_b = min - dp_small, zero flow at their mean; NominalLaminarFlow's head with the upwind density), a pipe segment's at its volumes (av_b's last at inStream(port_b)), a pump's at its medium (rho = medium.d; V = 0: medium.h = inStream + W/m), tank ports' portInDensities (inStream) and medium.d, portVelocities with the actual stream's density; volumes: m = V d(T), der(m) = V d'(T) dT/dt in the node balances (a pump volume at port_b's node; a pipe's first volume at port_a's, av_vb's last at port_b's), port flows with it (port_a.m_flow = m_flows[2] + dm1/dt; pump port_b.m_flow = -(m_a - dm/dt)); tanks: states m and U, level = m / (d A). Each instant: hydraulics -> energy -> port states and mass changes, repeated to 1e-11 K (about 100x per pass; constant-density liquids: one pass). **Found on the way (all liquids): StaticPipe has port_b.h_outflow = inStream(port_a.h_outflow) - g height_ab** (Pipes.mo, "considering change of potential energy"): e1_static's OpenModelica result shows the 9.81 J/kg step (pipe.port_a.h_outflow 426438.73 vs b's 426428.92); the reference now carries it (water: 2.3 mK per metre), and fluid_energy adds the rise's work -m_flow g height_ab per static pipe (OpenModelica's e1 run failed its own energy check, 1.6e-5, without it). **Mass check:** a closed loop has no throughput, so its scale fell to 1e-9 of the stored mass (OpenModelica g4_loop: 3.5e-4 kg of 1050 kg at tolerance 1e-6 read as 100 %); the allowance now includes 10 tol of the stored mass. **Comparison at 1e-8 (outputs' finals):** g1 static 1e-15; g2 heated tank T 9e-8, level 1e-9; g3 dynamic pipe 6e-10..2e-9; g4 loop 2e-10..2e-8; g5 volume 1e-8; g6 pump volume 8e-10..7e-9; e1 static (after the StaticPipe fix) 8.5e-15; e2 essotherm local 2e-12; g7 reverse 3e-16; g8 av_b local tank ports Q 4.6e-7, T 8e-8. Reference runs' own balances ok on all ten. Cost: 0.1-23 s per study (the coupled instants). tests: fixtures/fl14_tablebased.json (six OpenModelica results), media, StaticPipe rise, mass allowance. Benchmarks fl_glycol_loop, fl_essotherm_pipe, fl_static_head. Next (FL15): gases.


**Carbonio with .25 (bench_20261119_230651_b7f2cf): all 26 fl_* pass, reliable.** New: fl_glycol_loop against the reference 2.0e-6 over the whole series (closed loop: mass ok with the stored-mass allowance), fl_essotherm_pipe 2.1e-6, fl_static_head 6.6e-15 and OpenModelica's own fluid_energy ok with the rise's work; the earlier 23 as with .22.

**V5 FL15, gases (in progress).** Probe on carbonio (.25, run_fl15_probe1, tolerance 1e-8), all ran and were reliable by omct's own checks (cross-solver within 1.6e-6): a1 air (DryAirNasa) from 3 bar through fl_valve_gas (ValveCompressible) into a 50 l volume, out through an orifice (p 1 -> 1.93 bar, T peaks 328.9 K then 307.5 K); a2 simple_air through a static pipe into a 20 l volume and a resistance (p 1.2 -> 1.085 bar, T -> 320 K); a3 air through a heated 3-node dynamic pipe between boundaries (OpenModelica: "initial conditions over specified" for the end volumes' p = the boundaries', handled); a4 a mass-flow source into a heated 100 l volume out through a gas valve (p 1 -> 3.19 bar, T 392 K). Design for fluidref: media SimpleAir (PartialSimpleIdealGasMedium: cp const, d = p/(R T), u = h - R T) and DryAirNasa (SingleGasNasa: NASA-7 polynomials for cp, h with its reference, the low-pressure viscosity and conductivity MSL uses); gas volumes hold mass and energy (m, U) states and set their node's pressure p = m R T / V, so they act like tanks in the hydraulic solve; a volume on a node whose pressure a boundary fixes (a dynamic pipe's end volume at a boundary) takes p from it and its mass from p and T (the T-dependent-liquid mass change); ValveCompressible (IEC 534 with Fxt, choked flow, regularised) and the density at each port's state (p and T) in every relation.

**2026.11.19.26 (2026-11-19)**: V5 FL15, gases (Nicola: "deployed, go ahead"). Ported (MSL 4.0.0, sparse checkout now with Media/IdealGases and Math): Air.SimpleAir (PartialSimpleIdealGasMedium: cp 1005.45, R = Modelica.Constants.R / 0.0289651159, T0 = reference_T 298.15, h = cp (T - T0), u = h - R T, d = p/(R T), eta 1.82e-5, lambda 0.026), Air.DryAirNasa (SingleGasNasa with SingleGasesData.Air: h_T with excludeEnthalpyOfFormation and ZeroAt0K, h = R_s (...)/T - Hf + H0, cp_T, T from h by Newton; dynamicViscosity and thermalConductivity polynomials in degC with Polynomials.evaluateWithRange -150..1000 degC), Valves.ValveCompressible (p = max(port pressures), x = dp/p, xs = x within +-Fxt, Fxt = Fxt_full xtCharacteristic one, Y = 1 - |xs|/(3 Fxt), m = rc Av Y regRoot2(p xs, dp_small, d_a, d_b); checkValve: rc Av Y sqrt(d_a) regRoot(p xs) for xs >= 0; OpPoint: Av from m_flow_nominal at p_nominal, x_nominal = dp_nominal/p_nominal, rho_nominal at p_default, T_default). Gas volumes (ClosedVolume, DynamicPipe volumes; ps_start = linspace(p_a_start, p_b_start, n), p_a_start default system.p_start = p_ambient): states m and U, p = m R T / V fixes the node ("state"); on a boundary's node the volume follows p with m = V p/(R T) ("follow", OpenModelica reports the over-specified initial p there and drops it). Densities in every relation at each port's (p, inStream T): the hydraulic solve evaluates them with the iterate's pressures for gases. **A follow volume's mass change enters its own port flow** (port_a.m_flow = m_flows[2] + dm1/dt): for a gas the fixed point's gain is -h/(cp T - h) (it diverged on a3_dynpipe); dm/dt is now solved from der(U) = E + s (dm/dt - dm0), der(m) = V dd dT/dt, der(U) = der(m) u + m u' dT/dt per volume (s the port stream's enthalpy), which also speeds the liquids. **Carbonio (run_fl15_probe1, probe2; tolerance 1e-8) against the reference:** a1 valve into a volume p 4.6e-8, T 1e-8; a2 simple_air pipe into a volume 4e-9; a3 heated dynamic pipe between boundaries 7e-13; a4 heated volume 7e-8; b1 reversed flow through a rising static pipe, orifice, resistance, temperature and volume-flow sensors 3e-16; b2 choked OpPoint equal-percentage valve, check valve, conductor 1e-10; b4 volume, valve, rising cooled 4-node pipe to the atmosphere Q 4.5e-7, others 7e-8. b3 (a dynamic pipe joined directly to two volumes) runs in OpenModelica (initial p over-specified, dropped) and is refused by the reference (two gas volumes on one point). Reference runs' mass and energy balances ok on all. Not covered: two gas volumes on one point, gases in pumps or tanks. tests: fixtures/fl15_gases.json, media, refusal. Benchmarks fl_gas_valve_volume, fl_gas_choked, fl_gas_pipe; fl_air_fill leaves FLUID_OMC_ONLY (the reference runs it).

**Carbonio with .26 (bench_20261119_233250_55c8ce): all 29 fl_* pass, reliable.** Gases against the reference over the whole series: fl_gas_valve_volume 7.5e-7, fl_gas_choked 5.2e-7, fl_gas_pipe 3.8e-7; fl_air_fill now judged with the reference (no cross-solver); the liquids as with .25. **V5 FL15 done.** All media omct offers are in the reference; what is left is listed under Open items, "Open improvements: Modelica.Fluid".

