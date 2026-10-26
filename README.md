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

**Status (2026.10.21.22, milestone 8 of 8):** every milestone checked on mochi with OpenModelica 1.27.1: 11 closed-form benchmarks, the MCP server, sweeps on one build, SLURM queueing, and the chains with fea-translator and build123d-translator (`chain_feat_modal`: Rayleigh/feat = 1.0171 against 1.0146 expected, 0.24 %; `chain_b3dt_mass`: exact). It joins llm-translators as `omct` (M8). 228 tests pass without OpenModelica.

## Install

1. **OpenModelica and MSL 4.0.0** on the machine that runs the studies: llm-translators' [BACKENDS.md](https://github.com/feranick/llm-translators/blob/main/BACKENDS.md#openmodelica) (the official apt repository, then `installPackage(Modelica, "4.0.0+maint.om")` with OpenModelica's package manager), and a C compiler.
2. **The translator:** `./llmt install omct` in llm-translators (the shared venv; `./llmt tune omct` sets `OMCT_PROCS` and, with SLURM, `OMCT_EXECUTOR=slurm`). For development: `pip install -e .[mcp,test]`.
3. **Check:** `./llmt status` (omc and MSL ok), `./llmt test omct`, `./llmt bench omct`, `./llmt mcp test omct`.

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
- **Clusters of several nodes** (the sparks): `OMCT_SLURM_NODE` = `local` (default: every job gets `--nodelist=<this node>`, found from `scontrol -o show node` by host name or address); a job asks for at most this node's CPUs (with `any`, the largest node's), `any` (SLURM chooses; shared home folders only) or a node list; a `--nodelist`/`-w` in `OMCT_SLURM_ARGS` wins; one-node clusters add nothing. `status.executor.node` reports `{setting, nodelist, cluster_nodes}`. A job that went to another node and left no `job.log` says so.
- **Memory:** `OMCT_SLURM_MEM` auto = 1 GB + 256 MB per CPU (or a size, or none); `run.mem` wins, then a `--mem`/`--mem-per-cpu` in `OMCT_SLURM_ARGS` (split like a shell: `"--mem=8G --nodelist=spark-5da5"` or `"--mem 8G -w spark-5da5"`).
- **A slow or stopped controller:** every tool still ends within its `wait_s` (≤ 50 s): `sbatch` gets 15 s and a timeout fails the request's other studies at once (for 30 s); `squeue`/`sacct`/`scontrol` get 10 s, and `study_status` starts no check it could not finish in time. A failed `scontrol` or MSL check is asked again next time, never remembered.
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
- **Three-phase** (`Modelica.Electrical.Polyphase`, m = 3): a port type `plug` (three pins; plugs connect phase by phase), `ac_voltage` (symmetric sine, `V_rms` per phase, `f`, `phase`), `star` (star point `pin_n` grounded or floating), `delta`, `plug_to_pin`, `ac_resistor`, `ac_inductor`, `ac_capacitor` (per-phase initial values: one number or a list; the free ones are fixed, e.g. two of three currents into a floating star), sensors `ac_current_rms` (I), `ac_voltage_rms` (V), `ac_power` (P, Q from space phasors). Values given once are applied to each phase (`R={10, 10, 10}` in the model, `R[1]`... at run time). The reference covers these networks per phase; quasi-RMS and power outputs are computed from its solution.
- **AC machines** (`Modelica.Electrical.Machines.BasicMachines`, transient space-phasor models, m = 3), each with its terminal box (`terminal`: `Y` | `D`): `induction_machine` (IM_SquirrelCage: `p`, `fsNominal`, `Rs`, `Lssigma`, `Lm`, `Lrsigma`, `Rr` per phase as in the equivalent circuit, `Jr`), `pmsm` (SM_PermanentMagnet without damper cage: `VsOpenCircuit`, `Lmd`, `Lmq`; needs a drive), `pmsm_damper` (with damper cage: `Lrsigmad`, `Rrd`, ...: can start on a grid). Ports `plug` (supply), `flange`, `starpoint` (Y), and heat ports when connected (`heatPort` takes every loss, or the eight separate ports). Losses as for `dc_machine` (`PRef_friction`, `PRef_statorCore` + `VRef_statorCore`, `PRef_strayLoad` + `IRef_strayLoad`, `PRef_pm` + `IRef_pm`; structural). Outputs `wMechanical`, `tauElectrical`, `tauShaft`, `is[1..3]`, `vs[1..3]`, `ir[1..2]` (cage or damper), the power balance. Supplies: `three_phase_grid` (`V_rms`, `f`), `vf_inverter` (input `f` in Hz; `VNominal`, `fNominal`), `pmsm_current_drive` (inputs `id`, `iq` in A RMS, its `flange` on the motor shaft: ideal field-oriented current control; ground the motor's `starpoint`). The reference covers them (V3.3); their energy balance counts the rotor's kinetic energy, the stray, airgap and magnet terms and `lossPowerTotal`.
- **Quasi-static machines** (`Modelica.Magnetic.QuasiStatic.FundamentalWave`, `Modelica.Electrical.QuasiStatic`: complex RMS phasors, no electrical transients, typically 10x faster): `qs_induction_machine`, `qs_pmsm`, `qs_pmsm_damper` (the same parameters as the transient machines, without `Lszero`; terminal `Y` grounds the star point inside, `D`), on `qs_grid` (`V_rms`, `f`, `phase`), `qs_vf_inverter` (input `f`) or `qs_pmsm_current_drive` (inputs `id`, `iq`; 1 MOhm across its source, as MSL's example). Their ports are of type `qs_plug`: they join only quasi-static parts (`qs_ac_voltage`, `qs_star`, `qs_ground`, `qs_current_rms`, `qs_voltage_rms`, `qs_power`), never transient plugs; shafts, heat ports and signals are shared. Each quasi-static circuit needs a `qs_ground` and exactly one source (it sets the frequency and the reference angle, `gamma0` at the start). Outputs `wMechanical`, `tauElectrical`, `abs_is[k]`, `abs_vs[k]` (RMS), `Ps_total`, `Qs_total`, the power balance. The energy balance is not covered for a QS synchronous machine on a voltage supply: the phasor model does not conserve energy while its rotor slips.
- **Coolant loops** (`Modelica.Thermal.FluidHeatFlow`, incompressible media with constant properties): `pipe` (friction through the nominal point `V_flowNominal`, `dpNominal`: linear up to `V_flowLaminar`, then quadratic, or `friction: "linear"`; height `h_g`; `m` kg of medium inside for the temperature lag; a `heatPort` that exists when connected), `valve` (opening input `y`), `volume_flow` / `pressure_increase` (and `signal_` forms), `ideal_pump` (driven by its flange), `ambient` (open ends), `pressure_reference` (closed loops), sensors for temperature, pressure and volume flow. One medium per loop: `water` (MSL's, 30 °C), `water_10degC`, `water_90degC`, `air_30degC`, `air_70degC`, `glycol20_20degC`, `glycol50_20degC`, `mineral_oil`, or `{rho, cp, cv, lambda, nu}`. Validation checks the medium and the pressure reference of every loop. A component's `T` is its outlet temperature; junctions mix.
- **DC machine** (`dc_machine`, MSL `DC_PermanentMagnet`): nominal data (`VaNominal`, `IaNominal`, `wNominal`, `Ra`, `La`, `Jr`; k = (VaNominal − Ra·IaNominal − brush drop)/wNominal), losses (`PRef_friction` with `power_w_friction`, `PRef_core`, `PRef_strayLoad`, `V_brush`; 0 unless given; references default to the nominal point), outputs `va`, `ia`, `wMechanical`, `tauElectrical`, `tauShaft`, `Ta`, `Ra_actual` and the power balance (`powerArmature`, `powerMechanical`, `lossPowerTotal`, `lossPower<Armature|Core|StrayLoad|Friction|Brush>`). Heat: no heat port (the armature at `TaOperational`), `heatPort` (every loss into one thermal node), or all six (`heatPortArmature`, `…Core`, `…StrayLoad`, `…Friction`, `…Brush`, `…PermanentMagnet`); with heat ports `Ra` follows the armature temperature (`alpha20a`).
- **Heat ports** (conditional ports): a pipe's, resistor's, ideal diode's or damper's `heatPort`, `dc_motor`'s `heatPort` (Ra and friction; or `heatPort_R`, `heatPort_friction`) and `dc_machine`'s exist when connected (`useHeatPort` / `useThermalPort = true`: a different build): the losses then heat the thermal network instead of leaving the model, and a resistor's `R_actual = R (1 + alpha (T − T_ref))` follows the port's temperature (without a heat port, at `T`, default `T_ref`). The energy balance moves those losses into the thermal storage; the reference covers one-way heating (alpha = 0): the electrical or mechanical part first, then the thermal part with its losses as sources.
- **Units:** SI. A value may also be `"10 mH"`, `"80 degC"`, `"3000 rpm"`, or `{"value": 1.85, "unit": "N/mm", "from": "feat", "what": "tip stiffness"}` for chained values; every conversion is recorded. Hz and rad/s are never converted into each other. Signal values (step heights, limits) take any unit.
- **Connections:** physical ports of the same type (pin, translational flange, rotational flange, heat port, fluid port); a signal output to inputs. A component with one port of the right type may be named alone.
- **Initial values** (`v0`, `i0`, `s0`, `v0`, `phi0`, `w0`, `T0`) default to 0 (T0 to 293.15 K). Only the free ones become `fixed=true`: a capacitor across a source or an inertia rigidly joined to another follows the others, and giving it a value is an error. Blocks start from `y_start` with their states at 0 (`initType = InitialState`).
- **Analyses:** `transient`, `steady`, `linearize` (A, B, C, D, eigenvalues, modes, Bode); `sweep` on any of them; `derived` quantities: `step` (rise, overshoot, settling), `crossing`, `window`, `steady`; `converge`; `expect`; `run`; `images`.
- **Escape hatch:** `"modelica": {"text": ..., "model": ...}`, checked but without catalog checks or reference.

### Values from the other translators (`chains.py`)

A value from another tool goes into the study as `{"value", "unit", "from", "what"}`; the result's `inputs` records the conversion. Helpers: `feat_stiffness(static, face, comp, force)` (|F| / |faces.<face>.mean_u.<c>|, N/mm), `feat_mass(result, factor)` (mass_kg; 33/140 for a cantilever's tip mass by Rayleigh), `feat_frequency(modal, i)`, `feat_conductance(thermal, face, ΔT)` (W/K), `b3dt_mass(goal, density)` (volume from build123d-translator's check × density), `b3dt_inertia(goal, density, axis, point)` (moment of inertia about an axis, kg·m², from the check's `center_of_mass` and `inertia_mm5`, parallel axes), `rayleigh_cantilever_hz(k, m)`. Keys read: fea-translator `faces.<face>.mean_u.<ux|uy|uz>`, `applied_force`, `frequencies`, `mass_kg`, `heat_flow.<face>`; build123d-translator `check()[name].volume`. cfd-translator's pressure-drop and pump curves enter as `table` blocks until fluid networks (v2).

### Interface (read by other tools; kept stable)

`outputs.<name>.{final, min, max, initial, t_min, t_max}`, `derived.<name>.*`, `steady.<name>.*`, `linearization.{eigenvalues, modes, dc_gain, bode}`, `sweep.table`.

`validate_studies` → `studies.<name>.estimate: {minutes, procs, mem_mb}` for each valid study: what its run would ask for (the same sizing as a SLURM job; minutes an upper bound), read by llm-translators' router to place runs.

## The checker

- **Validation** before anything runs: kinds, parameters and ranges, units, ports and connector types, a ground in every circuit, a reference or inertia in every mechanical system, a capacitor or a temperature boundary in every thermal network, every signal input driven once, outputs, analysis, sweep (only values that can be overridden), `expect`, `run` within the machine's caps; then the reference system finds singular models and initial values that are not free.
- **Reference implementation** (`ref.py`), independent of the generated Modelica: it writes the MSL equations of each kind into a descriptor system A z + B ż + G F(t) = 0, reduces its index (shuffle algorithm), and integrates the consistent states between the sources' breakpoints (Radau, rtol 1e-10). It gives trajectories, steady states, eigenvalues and frequency responses of linear networks (R, L, C, transformers, op-amps, sources, sensors, masses, springs, dampers, inertias, gears, the DC motor and the MSL DC machine with linear losses, thermal capacitors, conductors and convection, linear blocks and PI/PID), and it chooses which initial values are fixed in the model. Since v2 it also covers: **piecewise-linear elements by events** (ideal diode, limiter, dead zone, LimPID with its anti-windup, slew-rate limiter, the DC machine's brush drop: one linear system per set of modes, built when first needed; solve_ivp stops where a guard crosses zero, the element switches, the storage values carry over; at the start and after each switch the modes are made consistent like an event iteration; steady states pick the modes whose guards hold; no linearisation), **coolant loops** (the hydraulics by Newton, then the temperatures), and **one-way heating** (losses into a thermal network as sources from a first run). **Nonlinear terms** (on by default since 2026.10.21.24; `OMCT_REF_NONLINEAR=0` switches them off): the induction machine, the PMSM with and without damper cage (losses, temperature-dependent resistances, heat), the V/f controller, the dq transform and the speed-dependent loads, written as their equations; the nonlinear terms become inputs of the exact linear reduction, so the system is an ODE in its coordinates (solve_ivp Radau, rtol 1e-8), the terms solved in dependency order at each evaluation and their derivatives, where the dynamics need them, solved together with the state derivative; terms that need a derivative but drive nothing (a current-fed machine's voltages) are computed at the output points; a PMSM is written for a voltage or a current feed, whichever gives that ODE; otherwise a Radau IIA DAE integrator (`refnl.py`). Switched off, machines and quadratic loads are not covered. Not covered: radiation, products and divisions, two-way electro-thermal coupling (alpha ≠ 0 with a heat port), signal-driven or machine-driven pumps. Tested against closed forms: RC (1e-9), RLC step and poles (1e-8, 1e-10), mass–spring–damper (1e-10), thermal RC (1e-7), DC motor speed and poles, PI on a first-order plant, two-mass eigenvalues and frequency response, index-2 networks.
- **On every run:** balance (omc's equation and variable counts; omc itself accepts an unbalanced model), initialisation (homotopy noted), the solver (assertions, exit code, statistics), every value refused or unknown to the compiled model (OpenModelica only warns and runs on), every applied value read back from the result file, events (chattering), finite outputs, and:
  - **the energy balance:** work in at the boundaries (sources −∫v·i dt, force and torque sources ∫f ds and ∫τ dφ, temperature and heat-flow boundaries −∫Q dt, op-amp supplies) = change of stored energy (½Cv², ½Li², ½mv², ½Jω², ½c·Δs², C·T) + dissipation (LossPower, lossPower), trapezoids on the output grid, judged at max(1 %, 3 × the Richardson estimate of the integration error);
  - **the reference comparison** (linear, piecewise-linear and coolant networks, AC machines, speed-dependent loads): the reference implementation on the same time points (events and source steps left out), max error / range ≤ max(1e-4, 100 × tolerance); for `linearize`, eigenvalues within 1e-5 of the largest;
  - **Independent checks:** catalog studies are compared with the reference implementation; custom models (`modelica`) with a second run by the other integrator (`checks.cross_solver`, numerical errors only). A failure of that check alone is retried once at tolerance/100: integration error closes the gap, a model difference does not. `invariants: [{signal, kind: zero | constant, abs | rel}]` checks what the model must keep (a balance error at 0, a total constant).
  - **`converge: true`:** a second run at tolerance / 10; outputs change by ≤ max(1e-6, 100 × tolerance) of their range (more for outputs with a large offset). Outputs that are constant by design (balance totals, energy errors) vary only by solver noise, which shrinks with the tolerance: they are listed in `checks.converge.not_judged` instead of failing. `converge: {abs_tol: {output: value}}` judges those outputs by their absolute change instead (values with units, e.g. `"1 mJ"`).
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
| `chain_feat_modal` (by name) | fea-translator's static cantilever (k = F/u) and mass → Rayleigh √(k/(33/140 m)) = 1.0146 × fea-translator's modal first frequency | 1 % |
| `chain_b3dt_mass` (by name) | build123d-translator box volume × density → mass; f = √(k/m)/2π | 1e-5 |
| `coolant_open_step` | water through a pipe heated with 2 kW (FluidHeatFlow): outlet T a first-order lag, τ = m c_v/(ṁ c_p) = 5.02 s; T(40 s); the friction curve | 1e-4, 1e-6, 1e-9 |
| `coolant_loop_steady` | closed loop: pump, heater 1 kW, cooler 200 W/K to 20 °C: T_cool = T_amb + Q/G, T_hot = T_cool + Q/(ṁ c_p) | 1e-7 |
| `coolant_hydraulics` | pressure source, quadratic pipe and half-open valve: the operating point solves a quadratic in V | 1e-7 |
| `dc_machine_nominal` | MSL DC_PermanentMagnet with its example data at 100 V: no-load speed Va/k, then wNominal and IaNominal at nominal load; Ra·Ia² | 1e-4, 1e-5 |
| `dc_machine_losses` | the same with linear friction (100 W) and core losses (50 W): the steady state of the linear equations (speed, current, losses, torque) | 1e-5 |
| `resistor_self_heating` | 10 Ω on 10 V heating 100 J/K through its heatPort, 0.5 W/K to 20 °C: τ = C/G = 200 s, T(1000 s) | 1e-4, 1e-5 |
| `damper_heating` | a flywheel spinning down through a rotational damper whose heatPort feeds 1 kJ/K: the kinetic energy lost as heat; w0·e^(−dt/J) | 1e-5, 1e-4 |
| `dc_machine_heating` | the DC machine at nominal load, heatPort on 2 kJ/K cooled by 10 W/K: steady T = T_amb + Ra·Ia²/G = 70 °C (and Ta) | 1e-5 |
| `limiter_loop` | P control of a first-order plant through a limiter: saturation ends at t = 0.1 − ln(1 − 6/20), then the linear loop | 1e-4, 1e-5 |
| `rectifier_mean` | half-wave rectifier (IdealDiode, Vknee 0.7 V) into 100 Ω: the mean current over a period from MSL's characteristic, the peak | 1e-3, 1e-4 |
| `slew_rate_ramp` | a unit step through a slew-rate limiter (2/s, Td 1 ms): the ramp, then the exponential | 1e-4, 1e-6 |
| `dc_machine_brush` | the DC machine with a 2 V brush drop at nominal load: steady ia = TL/k, w = (Va − V_brush − Ra ia)/k | 1e-5 |
| `three_phase_rl` | symmetric 230 V source, star R-L load with a floating star point: quasi-RMS current, P, Q over the last period from the phasors | 1e-5 |
| `three_phase_delta` | the same source on a delta of resistors: line current 3V/R, P = 9V²/R, Q = 0 | 1e-5 |
| `im_grid_slip` | the MSL induction machine on a 100 V 50 Hz grid at 100 N.m: speed (slip) and stator current from the equivalent circuit, the torque | 1e-4, 1e-3 |
| `pmsm_current_torque` | the MSL PMSM with the ideal current drive (Id −53.5, Iq 84.6 A): torque 3/2·p·ψM·√2·Iq, the speed against a quadratic load | 1e-4 |
| `im_vf_start` | the induction machine started by a V/f inverter (0 → 50 Hz in 1 s), 100 N.m: the grid's steady speed | 1e-4 |
| `pmsm_damper_grid` | the PMSM with damper cage on a 112.3 V 50 Hz grid, started synchronous, 50 N.m from 0.2 s: it stays synchronous, torque = load | 1e-6, 1e-4 |
| `quadratic_load` | an inertia driven by 10 N.m against a quadratic speed load: w = √50·tanh(t√2/J) (crossing time, w(2 s)) | 1e-4, 1e-6 |
| `im_grid_delta` | the induction machine in delta on 100/√3 V per phase: the star machine's speed and torque on 100 V | 1e-4, 1e-3 |
| `qs_im_grid_slip` | the QS induction machine on a QS 100 V 50 Hz grid, 100 N.m: speed, current (`qs_current_rms`) and power (`qs_power`) from the equivalent circuit | 1e-6, 1e-5 |
| `qs_pmsm_current_torque` | the QS PMSM on the QS current drive: torque 3·p·ψM·Iq from the start, so the speed against the quadratic load is w∞·tanh(t√(Tk)/J); the stator voltage from the dq phasors | 1e-7, 1e-5 |
| `qs_im_vf_start` | the QS induction machine started by the QS V/f inverter, 100 N.m: the grid's steady speed | 1e-6 |
| `qs_pmsm_damper_grid` | the QS PMSM with damper on a QS grid, started synchronous, 50 N.m: synchronous, torque = load | 1e-7, 1e-5 |
| `qs_im_grid_delta` | the QS induction machine in delta on 100/√3 V | 1e-6, 1e-5 |
| `chain_b3dt_inertia` (by name) | two build123d-translator steel discs on a torsion spring, about the axis and about a parallel axis 30 mm off: J = m r²/2, J + m d²; f = √(c/ΣJ)/2π | 1e-5 |

A case passes when its run is ok and reliable (energy, reference) and every check holds. All pass with `--solver ref`
(the machines and the quadratic load, the nonlinear ones, need OpenModelica when `OMCT_REF_NONLINEAR=0`); with OpenModelica: see `planning.md` (State).

### Assumptions about OpenModelica

Listed in `planning.md` (A1–A19), each with the probe that checks it (`tools/m0_probe.sh`, run on mochi).

## How it works

| Module | Role |
|---|---|
| `catalog.py` | the curated MSL 4.0.0 kinds: classes, ports, parameters (SI), variables, initial values; checked against the MSL sources by `tools/check_catalog_msl.py` |
| `units.py` | values with units to SI, recorded |
| `network.py` | a study resolved: values, composites expanded into elements, conditional ports and port groups, ports to nodes, signal nets, ground/reference/pressure-reference and medium checks |
| `format.py` | `study_format()`, `validate()` |
| `modelica.py` | the model text (stable, sorted), its structure (values as placeholders: the build cache key), the override file, the omc script |
| `ref.py`, `refqs.py`, `refnl.py` | the reference implementation (linear networks, coolant hydraulics, one-way heating, piecewise-linear modes and events; nonlinear terms of machines and loads; quasi-static circuits and machines as (re, im) pairs) and the choice of fixed initial values; the Radau IIA DAE integrator |
| `omc.py` | the only module running omc: version, MSL, the build script and its printed values, errors mapped to declarations, the build cache (flock, atomic rename, pruned to 6 files) |
| `simrun.py` | runs a cached model: override file, flags, process group, timeout and cancel flag, `OMP_NUM_THREADS=1`; reads the log (refused overrides, assertions, chattering, statistics) and reads values back |
| `matfile.py`, `linear.py` | the MAT v4 result file; `linearized_model.mo` → A, B, C, D, modes, DC gain, Bode |
| `post.py`, `energy.py`, `bench.py` | outputs, derived quantities (step, crossing, window, steady), comparisons, expect; the energy balance; the benchmarks |
| `runner.py`, `api.py`, `proc.py` | one study end to end with every check; the API (pool, request folders, status from files, cancel, jobs, benchmarks); process groups |
| `render.py`, `mcp_server.py`, `sweep.py` | time and Bode plots; the MCP server; sweeps |
| `chains.py` | values from fea-translator and build123d-translator results (masses, moments of inertia about an axis), with provenance |
| `executor.py` | the local pool or one SLURM job per study: sizing, estimates, job.sh/job.json, states, jobs, cancel, run-study |
| `cli.py` | `omct status [REQUEST] | format | validate | model | run | bench | jobs | cancel | images | run-study` |

### Sweeps

`"sweep": {"params": {"pi.k": [0.5, 1, 2], "pi.T": [0.1, 0.2]}, "mode": "grid" | "zip", "report": ["derived.y.overshoot_pct"]}` on any analysis. Each point is the study with those values (parameters or initial values, with units allowed), run in `points/pNNNN/` with every check; the first point builds the model, the others reuse it and run `run.procs` (or `OMCT_PROCS`) at a time, in processes (in threads inside the server's daemonic pool). Values compiled into the model (see the run warnings) make variant builds. Result: `sweep.table[i] = {point, params, params_si, values, ok, reliable, why}`, `columns`, `builds`, `ok_points`, `reliable_points`, `failed`; with `images`, `images/sweep.png` (each column against the first parameter). Default report: `outputs.*.final` (`steady.*.value`; `linearization.natural_frequencies_Hz`, `damping_ratios`, `time_constants_s`) and every numeric derived quantity.

## For LLM tools: the MCP server

`omct-mcp` (stdio; `--http 8770` for HTTP). Tools: `study_format`, `validate_studies`, `run_studies`, `study_status`, `get_model`, `run_benchmarks`, `bench_status`, `jobs`, `cancel`, `get_image` (a PNG as an MCP image: the model sees it, the user usually does not, so the instructions tell the model to chart `outputs.<name>.series` itself for the user), `render_images`, `get_file`. `run_studies` and `study_status` return within 50 s with the rest `pending` (stage, elapsed, the time limit left); studies run in a worker pool (`OMCT_WORKERS`, 2) that lives across calls, and every state is read from the request folder, so another server process sees the results. Polling costs tokens: the instructions tell the model to report when a long run should finish and stop. `tools/mcp_smoke.py` runs the server as the Claude app would and simulates one example.

Pictures: `"images": true` in a study writes `images/outputs.png` (one panel per unit) or `images/bode.png` (linearize with `bode`); `render_images` draws other selections (`{name, outputs, window}`) from the saved `series.npz`.

## Files

`examples/studies.json` (the typical requests: DC motor with PI speed control, heated aluminium block, RLC filter Bode and square wave, two-mass drive, PI gain sweep, a chained bracket, and a DC machine cooled by a water loop: `motor_cooling`), `tests/`, `tools/m0_probe.sh` and `tools/v2_probe.sh` (OpenModelica facts on mochi), `tools/check_catalog_msl.py`, `planning.md` (session context).

## Versions

| Version | Changes |
|---|---|
| 2026.10.26.1 | (1) A reference check that fails while every other check passes is retried once at tolerance/100: OpenModelica's integration error at the default 1e-6 reached ~1e-4 of an output's range on 6-hour motor-heating runs, at the check's limit (rotor copper loss 8.5e-5 at 1e-6, 4.5e-6 at 1e-8), so values that agreed with their neighbours were flagged. If the gap closes, the tighter run is the result (with a warning); if not, it stays a failure, now labelled a model difference. (2) Custom Modelica models get `checks.cross_solver` (the same model with the other integrator at tolerance/10; `cross_check: false` skips it), and any study can declare `invariants` (`zero` / `constant`, `abs` or `rel`) checked over the whole run. (3) A custom model's value or sweep parameter whose default is an expression is rejected by `validate_studies` (and by the cached build's record); after a build, an unchangeable value fails at once with what to do, where before the run dropped it and only the read-back noticed |
| 2026.10.25.3 | Checked against llm-translators' translator checklist: node detection from `scontrol -o show node` (was `sinfo`), job CPUs capped at this node's (`any`: the largest node's), `--mem 8G`/`--mem-per-cpu` in `OMCT_SLURM_ARGS` read for estimates; tools end within their wait with a slow controller (sbatch 15 s then fail fast, queue queries 10 s, status loop deadline-aware, run_studies counts submitting against the wait); a failed MSL check is no longer cached for the server's lifetime |
| 2026.10.25.2 | `OMCT_SLURM_NODE` (llm-translators docs/EXECUTION.md, "Clusters of several nodes"; as cfdt 2026.10.25.2): jobs stay on this machine's node by default (`--nodelist`), `any` or a node list otherwise; `executor.node` in status; a failed `sinfo`/`scontrol` is asked again rather than remembered; a job lost on another node says so |
| 2026.10.25.1 | Fix for 2026.10.23.3: only a running MCP server refuses calls and exits when stale. `llmt deploy --test` runs the new release's tests before switching `current`, so the tests saw a stale install: two failed, and the exit ended pytest with exit code 0, which counted as a pass (on every machine) |
| 2026.10.23.4 | `converge` no longer flags studies whose balance outputs are constant by design: such an output's range is solver noise, and dividing its change by that range made any change look huge. An output whose range at tolerance/10 is ≤ 0.3 × its range at the tolerance, and whose change is ≤ 2 × that range, is reported in `checks.converge.not_judged`; `converge: {abs_tol: {output: value}}` judges named outputs by their absolute change (`checks.converge.detail`) |
| 2026.10.23.3 | A server no longer runs old code after a deploy: when a newer release is installed (shared install: `<prefix>/current` moved; checkout: another `__version__` on disk) and nothing runs in it, a call is refused with `STALE_SERVER` and the server exits (llm-translators' router, 2026.10.23.5, then repeats the call on a new server running the installed release); an idle stale server exits within 20 s; a busy one answers with a `server_stale` warning and exits when idle (`OMCT_EXIT_WHEN_STALE` = 1 / 0; default: shared installs). Crashes inside a tool now reach the caller with their message on MCP SDK 2.x too (before: only "Error executing tool X") |
| 2026.10.23.2 | The MCP server runs every tool in a worker thread (a 50 s wait in run_studies/study_status no longer blocks pings and other calls on SDK versions that run blocking tools on the event loop) and keeps its own log, `$OMCT_OUT/.server/<time>_<host>_<pid>.log` (each call with its duration, failures with tracebacks, signals, fatal errors, exit; the last 20 servers); custom models (`modelica`) take `values: {path: number}` and sweeps over Modelica paths (before: "internal error: KeyError 'components'") |
| 2026.10.23.1 | A model with nothing to override (the escape hatch) runs without `-overrideFile`: OpenModelica 1.27.1 cannot read an empty override file ("could not read overrideFile ...: Success"); the helper programs omct starts (omc --version, sbatch, squeue, sacct, scancel) get stdin from /dev/null, so none can read the MCP server's stdio channel |
| 2026.10.22.1 | `validate_studies` returns, for each valid study, `estimate: {minutes, procs, mem_mb}` from the job sizing (llm-translators docs/EXECUTION.md "Estimates in validation"; its router places runs with it): procs as the job asks (size cap, compile CPUs on a cache miss), memory as for `--mem`, minutes the upper estimate (now with the transient AC machines' reference time) |
| 2026.10.21.28 | MSL is loaded with `requireExactVersion=true` (builds and status): with several MSL versions installed, omc took the newest compatible one (4.1.0) for "4.0.0" although 4.0.0+maint.om was there (carbonio, probed) |
| 2026.10.21.27 | The nonlinear reference's ODE path judges u'' and higher derivatives in the dynamics by their whole coefficient (a separate check on one piece flagged terms that cancel: Spark1); the MSL status check compares the version omc actually loads: omc falls back to a newer compatible MSL (4.1.0) when 4.0.0 is missing, and status said "ok" while every build refused it (carbonio) |
| 2026.10.21.26 | The nonlinear reference's ODE path is less sensitive to rounding (Spark1 rejected the current-fed damper PMSM): the particular solution's rank and residual are judged relative to the system's size, with one refinement step; the "is it zero" floor for the dynamics' u'' terms scales with |Vp| |Az| |P|; when the path gives up it says why (`_nlode_why`) |
| 2026.10.21.25 | V3.4: quasi-static machines `qs_induction_machine`, `qs_pmsm`, `qs_pmsm_damper` with `qs_grid`, `qs_vf_inverter`, `qs_pmsm_current_drive` and the parts `qs_ac_voltage`, `qs_star`, `qs_ground`, `qs_current_rms`, `qs_voltage_rms`, `qs_power` (port types `qs_plug`, `qs_pin`; complex signals inside the drives; conditional parts in composites); the reference writes them as phasor equations (`refqs.py`), checked against MSL's own component equations solved literally (1e-9); nonlinear terms solved block by block (strongly connected components); the stand-in linearised at a generic point when 0 loses couplings; fixed: a delta-connected transient machine was refused by validation (its star-point node); benchmarks `qs_im_grid_slip`, `qs_pmsm_current_torque`, `qs_im_vf_start`, `qs_pmsm_damper_grid`, `im_grid_delta`, `qs_im_grid_delta` |
| 2026.10.21.24 | mochi: all 31 benchmarks pass with OpenModelica, and the reference comparison covers the five nonlinear ones (AC machines, quadratic load); the nonlinear reference is now on by default (`OMCT_REF_NONLINEAR=0` switches it off) |
| 2026.10.21.23 | V3.3 nonlinear reference (still off by default, `OMCT_REF_NONLINEAR=1`): the nonlinear terms become inputs of the exact linear reduction and the rest is an ODE in its coordinates; terms that need a derivative but drive nothing (a current-fed machine's voltages) are computed afterwards; the PMSM written for a voltage or a current feed, whichever gives that ODE; the constrained part placed on algebraic variables; speed-dependent loads as nonlinear terms (MSL's equations); new benchmarks `pmsm_damper_grid`, `quadratic_load`; with the variable set every benchmark passes with the reference (AC machines to 1e-11) |
| 2026.10.21.22 | V3.3 started (off by default, `OMCT_REF_NONLINEAR=1`): a Radau IIA DAE integrator (`refnl.py`), nonlinear rows in the reference, the AC machines and drives written as nonlinear equations; behaviour unchanged unless the variable is set |
| 2026.10.21.21 | mochi: the three-phase and AC machine benchmarks pass with OpenModelica (energy balance included); the reference comparison judges an output that is zero up to rounding on 1e-9 of the largest output of its dimension (three_phase_delta's Q) |
| 2026.10.21.20 | V3.2: AC machines `induction_machine`, `pmsm`, `pmsm_damper` (MSL BasicMachines with a terminal box; heat ports, structural loss records, per-phase initial currents), supplies `three_phase_grid`, `vf_inverter`, `pmsm_current_drive` (vector signals inside); energy balance terms for the machines; benchmarks `im_grid_slip`, `pmsm_current_torque`, `im_vf_start` (OpenModelica only until the nonlinear reference) |
| 2026.10.21.19 | V3.1: three-phase plugs (port type `plug`, phases joined pin by pin in the network and the reference), kinds `ac_voltage`, `star`, `delta`, `plug_to_pin`, `ac_resistor`, `ac_inductor`, `ac_capacitor`, `ac_current_rms`, `ac_voltage_rms`, `ac_power`; per-phase values and initial values (`fixed={true, true, false}`); the reference computes quasi-RMS and power outputs afterwards; benchmarks `three_phase_rl`, `three_phase_delta`; `tools/v3_probe.sh` (AC machines in OpenModelica) |
| 2026.10.21.18 | mochi: 11 of the 13 new benchmarks pass with OpenModelica. Fixes: the DC machine's loss records are structural (OpenModelica compiles their final parameters in: a reused build ran without losses); a fluid component's `m` is structural; the reference and converge checks allow the solver's relative tolerance on the output's size (absolute temperatures); `coolant_open_step` and `resistor_self_heating` run at tolerance 1e-8 |
| 2026.10.21.17 | V2.5: the reference covers piecewise-linear elements by events (ideal diode, limiter, dead zone, LimPID with anti-windup (its integrator now on u_s − u_m plus the track, as in MSL), slew-rate limiter, the DC machine's brush drop): modes, guards, consistent modes at the start and after each switch, mode-specific losses, steady states by modes; benchmarks `limiter_loop`, `rectifier_mean`, `slew_rate_ramp`, `dc_machine_brush`; tests against independent integrations (rectifier with capacitor 1e-6, LimPID windup 1e-6) |
| 2026.10.21.16 | V2.4: heat ports on resistors (`T_ref`, `alpha`, `T`; `R_actual`, `T_heatPort`), ideal diodes, the four dampers, `dc_motor` (group `heatPort`) and `dc_machine`, switched on by connecting them; reference: one-way heating as `Cascade` sources (a first run without the heat, then the thermal part with the losses from its dense solution; steady states the same way); energy balance: connected losses are internal, and the scale counts every term (a store feeding another cancels in the sums); benchmarks `resistor_self_heating`, `damper_heating`, `dc_machine_heating` |
| 2026.10.21.15 | V2.3: `dc_machine` (MSL DC_PermanentMagnet) with loss records as flat parameters (`frictionParameters(PRef=...)` in the model), its power balance as outputs, heat ports none / `heatPort` group / six; port groups and record fields in the catalog; reference: the machine's linear equations (exact without stray load, with friction power_w 1, brush drop 0, no heat ports); energy balance from its storage and lossPowerTotal; benchmarks `dc_machine_nominal`, `dc_machine_losses` |
| 2026.10.21.14 | V2.2: coolant loops (Thermal.FluidHeatFlow): port type `fluid`, kinds `pipe`, `valve`, `volume_flow`, `signal_volume_flow`, `pressure_increase`, `signal_pressure_increase`, `ideal_pump`, `ambient`, `pressure_reference`, fluid sensors; media; conditional ports (a pipe's heatPort exists when connected); validation (pressure reference, one medium per loop, open fluid ports, friction curve); reference: hydraulics by Newton, then the temperatures (upwind, mixing); energy balance with enthalpy and pressure work; benchmarks `coolant_open_step`, `coolant_loop_steady`, `coolant_hydraulics`; the reference reduction scales rows and the constraint solution columns (flows of 1e-5 next to enthalpies of 1e6) |
| 2026.10.21.13 | V2.1: moments of inertia from build123d-translator (2026.10.21.1 adds `center_of_mass` and `inertia_mm5`): `chains.b3dt_inertia`, study values `{"b3dt": ..., "density", "axis", "point"}` for masses and inertias, benchmark `chain_b3dt_inertia`; `tools/v2_probe.sh` (OpenModelica facts for v2) |
| 2026.10.21.12 | Pictures for the user: the instructions and `get_image` say the user usually does not see images in tool results (Claude app); the model charts `outputs.<name>.series` itself and does not refer to "the image above". Series rounded to 6 significant digits (about half the tokens) |
| 2026.10.21.11 | M8: joins llm-translators (`translators.json` entry `omct`, port 8770, executor threads, backends omc and msl, every setting; BACKENDS.md section; README row); chains confirmed on mochi |
| 2026.10.21.10 | M7: `chains.py`, benchmarks `chain_feat_modal` and `chain_b3dt_mass` (by name; skipped without the other translator); bench requests keep their checks; on mochi SLURM sweeps take 5.6 s (was 200 s) |
| 2026.10.21.9 | SLURM queueing confirmed on mochi (two 30-CPU sweeps: the second waits for Resources, both 120/120 reliable); sweeps run their points in processes outside the server's pool, the reference comparison uses ≤ 1000 points, estimates from a whole study's measured time |
| 2026.10.21.8 | job.sh puts the package's folder on PYTHONPATH (mochi: the first SLURM jobs could not import the translator, not yet installed in the venv) |
| 2026.10.21.7 | M6: SLURM (`executor.py` from cfdt, kind threads: CPUs from sweep points and cache misses, memory, time estimates from measured runs, states, jobs, cancel, run-study, bench through SLURM), fake-SLURM tests (13), `tools/m6_slurm_test.py`; on mochi the 12-point PI sweep ran on one build in 5.3 s, every point reliable |
| 2026.10.21.6 | M5: sweeps (grid/zip on one build, threads, table, plot, progress), `get_path` with dotted keys, benchmark `sweep_rc`; on mochi the MCP smoke test passed with OpenModelica (DC motor + LimPID: reliable, 5.9 % overshoot) |
| 2026.10.21.5 | M4: worker pool and request folders (`run` with `wait`, `start`, `status`, `jobs`, `cancel`), the MCP server (12 tools, SDK 1.x and 2.x), pictures (`render.py`), CLI `status REQUEST`, `jobs`, `cancel`, `images`, `run-study`; on mochi all 11 benchmarks pass with OpenModelica |
| 2026.10.21.4 | M3: derived quantities, steady values, energy balance, reference comparison, converge, expect, 11 benchmarks (`omct bench`); the reference's square-wave edges and end-of-run steps fixed (found by the energy balance) |
| 2026.10.21.3 | First runs on mochi (RC matches its closed form through OpenModelica); a value OpenModelica cannot change at run time (a mass's `v` became an alias of a spring-damper's `v_rel`) is now compiled in: the same value reuses the build, another value builds a variant |
| 2026.10.21.2 | M2: the OpenModelica driver (build cache, runs, logs, MAT results, read-back, linearisation), `omct status` and `omct run`, the mochi probe's answers in `planning.md`; 187 tests (+2 with omc) |
| 2026.10.21.1 | M1: catalog (82 kinds), units, validation, Modelica generation with structure hash and override file, reference implementation, CLI `format`/`validate`/`model`; 173 tests (Modelica syntax checked with modparc) |
| 2026.10.20.1 | M0: research and plan (`planning.md`, `tools/m0_probe.sh`) |

Author: Nicola Ferralis <ferralis@generalflash.com>. License: MIT.
