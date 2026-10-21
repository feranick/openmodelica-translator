#!/bin/bash
# v2_probe.sh — openmodelica-translator, version 2: what OpenModelica 1.27.1 + MSL 4.0.0 do with the v2 components
# (Thermal.FluidHeatFlow loops, the DC_PermanentMagnet machine, heat ports on resistors and dampers, ideal
# diode / limiter events) and what build123d reports as the matrix of inertia.
# Version: 2026.10.21.13
#
# Run on mochi from the Mac (the report lands in the repository, where Claude reads it):
#   ssh nicola@mochi.mit.edu 'bash -s' < tools/v2_probe.sh > tools/v2_report_mochi.txt 2>&1
# About 2-4 minutes. Works in ~/omct_v2probe (deleted and recreated). Needs omc; the Python part uses the
# llm-translators venv (numpy, build123d, openmodelica_translator.matfile).
# Each probe is answered in planning.md, "Assumptions for version 2" (B1, B2, ...).

set -u
OMC=${OMCT_OMC:-omc}
PY=${OMCT_PY:-$HOME/Software/llm-translators/.venv/bin/python}
W=$HOME/omct_v2probe
rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
hdr() { echo; echo "################ $*"; }

hdr "machine"
date -Is; "$OMC" --version; "$PY" --version

cat > v2.mo <<'EOF'
package OmctV2
  import FHF = Modelica.Thermal.FluidHeatFlow;
  import HT = Modelica.Thermal.HeatTransfer;
  model OpenLoop "B1-B3: water from an ambient through a pump and a heated pipe (2 kW) to an ambient"
    parameter FHF.Media.Medium medium = FHF.Media.Water();
    FHF.Sources.Ambient amb1(medium=medium, constantAmbientPressure=0, constantAmbientTemperature=293.15);
    FHF.Sources.VolumeFlow pump(medium=medium, m=0, T0=293.15, constantVolumeFlow=1e-4);
    FHF.Components.Pipe pipe(medium=medium, m=0.5, T0=293.15, T0fixed=true, V_flowLaminar=1e-5, dpLaminar=100,
      V_flowNominal=1e-4, dpNominal=2000, h_g=0, useHeatPort=true);
    FHF.Sources.Ambient amb2(medium=medium, constantAmbientPressure=0, constantAmbientTemperature=293.15);
    HT.Sources.FixedHeatFlow q(Q_flow=2000);
  equation
    connect(amb1.flowPort, pump.flowPort_a); connect(pump.flowPort_b, pipe.flowPort_a);
    connect(pipe.flowPort_b, amb2.flowPort); connect(q.port, pipe.heatPort);
  end OpenLoop;
  model ClosedLoop "B4: closed loop: pump, heater (1 kW), cooler (G = 200 W/K to 20 degC), pressure reference"
    parameter FHF.Media.Medium medium = FHF.Media.Water();
    parameter Real Vf = 1e-4;
    FHF.Sources.AbsolutePressure pref(medium=medium, p=1e5);
    FHF.Sources.VolumeFlow pump(medium=medium, m=0, T0=293.15, constantVolumeFlow=Vf);
    FHF.Components.Pipe heater(medium=medium, m=0.5, T0=293.15, T0fixed=true, V_flowLaminar=1e-5, dpLaminar=100,
      V_flowNominal=1e-4, dpNominal=2000, h_g=0, useHeatPort=true);
    FHF.Components.Pipe cooler(medium=medium, m=0.5, T0=293.15, T0fixed=true, V_flowLaminar=1e-5, dpLaminar=100,
      V_flowNominal=1e-4, dpNominal=2000, h_g=0, useHeatPort=true);
    HT.Sources.FixedHeatFlow q(Q_flow=1000);
    HT.Components.ThermalConductor g(G=200);
    HT.Sources.FixedTemperature amb(T=293.15);
    FHF.Sensors.TemperatureSensor tHot(medium=medium);
  equation
    connect(pump.flowPort_b, heater.flowPort_a); connect(heater.flowPort_b, cooler.flowPort_a);
    connect(cooler.flowPort_b, pump.flowPort_a); connect(pref.flowPort, pump.flowPort_a);
    connect(q.port, heater.heatPort); connect(cooler.heatPort, g.port_a); connect(g.port_b, amb.port);
    connect(tHot.flowPort, heater.flowPort_b);
  end ClosedLoop;
  model PumpCurve "B5: IdealPump at constant speed against a pipe (operating point of a quadratic curve)"
    parameter FHF.Media.Medium medium = FHF.Media.Water();
    FHF.Sources.Ambient amb1(medium=medium, constantAmbientPressure=0, constantAmbientTemperature=293.15);
    FHF.Sources.IdealPump pump(medium=medium, m=0, T0=293.15, wNominal=314.159, dp0=2e4, V_flow0=4e-4);
    FHF.Components.Pipe pipe(medium=medium, m=0, T0=293.15, V_flowLaminar=1e-5, dpLaminar=100,
      V_flowNominal=1e-4, dpNominal=2000, h_g=0);
    FHF.Sources.Ambient amb2(medium=medium, constantAmbientPressure=0, constantAmbientTemperature=293.15);
    Modelica.Mechanics.Rotational.Sources.ConstantSpeed spd(w_fixed=314.159);
  equation
    connect(amb1.flowPort, pump.flowPort_a); connect(pump.flowPort_b, pipe.flowPort_a);
    connect(pipe.flowPort_b, amb2.flowPort); connect(spd.flange, pump.flange_a);
  end PumpCurve;
  model Dcpm "B6-B8: DC_PermanentMagnet (MSL example data) at 100 V, load torque step at 1 s, no thermal port"
    Modelica.Electrical.Machines.BasicMachines.DCMachines.DC_PermanentMagnet dcpm(VaNominal=100, IaNominal=100,
      wNominal=149.2256510455152, TaNominal=293.15, Ra=0.05, TaRef=293.15, alpha20a=0, La=0.0015, Jr=0.15, Js=0.15,
      TaOperational=293.15, wMechanical(fixed=true, start=0), ia(fixed=true, start=0));
    Modelica.Electrical.Analog.Sources.ConstantVoltage va(V=100);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    Modelica.Mechanics.Rotational.Sources.TorqueStep load(stepTorque=-63.66197723675813, startTime=1);
  equation
    connect(va.p, dcpm.pin_ap); connect(dcpm.pin_an, va.n); connect(va.n, gnd.p);
    connect(dcpm.flange, load.flange);
  end Dcpm;
  model DcpmLosses "B9: the same machine with friction, core, stray-load and brush losses: the power balance"
    extends Dcpm(dcpm(frictionParameters(PRef=100, power_w=2), coreParameters(PRef=50),
      strayLoadParameters(PRef=20), brushParameters(V=2, ILinear=1)));
  end DcpmLosses;
  model DcpmThermal "B10: thermal port on, every loss into one heat capacitor (2 kJ/K, G = 10 W/K to 20 degC), alpha20a"
    Modelica.Electrical.Machines.BasicMachines.DCMachines.DC_PermanentMagnet dcpm(VaNominal=100, IaNominal=100,
      wNominal=149.2256510455152, TaNominal=293.15, Ra=0.05, TaRef=293.15,
      alpha20a=Modelica.Electrical.Machines.Thermal.Constants.alpha20Copper, La=0.0015, Jr=0.15, Js=0.15,
      useThermalPort=true, frictionParameters(PRef=100), wMechanical(fixed=true, start=0), ia(fixed=true, start=0));
    Modelica.Electrical.Analog.Sources.ConstantVoltage va(V=100);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    Modelica.Mechanics.Rotational.Sources.TorqueStep load(stepTorque=-63.66197723675813, startTime=1);
    HT.Components.HeatCapacitor cap(C=2000, T(start=293.15, fixed=true));
    HT.Components.ThermalConductor g(G=10);
    HT.Sources.FixedTemperature amb(T=293.15);
  equation
    connect(va.p, dcpm.pin_ap); connect(dcpm.pin_an, va.n); connect(va.n, gnd.p);
    connect(dcpm.flange, load.flange);
    connect(dcpm.thermalPort.heatPortArmature, cap.port); connect(dcpm.thermalPort.heatPortCore, cap.port);
    connect(dcpm.thermalPort.heatPortStrayLoad, cap.port); connect(dcpm.thermalPort.heatPortFriction, cap.port);
    connect(dcpm.thermalPort.heatPortBrush, cap.port); connect(dcpm.thermalPort.heatPortPermanentMagnet, cap.port);
    connect(cap.port, g.port_a); connect(g.port_b, amb.port);
  end DcpmThermal;
  model DcpmThermalOne "B11: thermal port on, only the armature port connected (the others left open)"
    Modelica.Electrical.Machines.BasicMachines.DCMachines.DC_PermanentMagnet dcpm(VaNominal=100, IaNominal=100,
      wNominal=149.2256510455152, Ra=0.05, La=0.0015, Jr=0.15, useThermalPort=true, frictionParameters(PRef=100),
      wMechanical(fixed=true, start=0), ia(fixed=true, start=0));
    Modelica.Electrical.Analog.Sources.ConstantVoltage va(V=100);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    HT.Components.HeatCapacitor cap(C=2000, T(start=293.15, fixed=true));
  equation
    connect(va.p, dcpm.pin_ap); connect(dcpm.pin_an, va.n); connect(va.n, gnd.p);
    connect(dcpm.thermalPort.heatPortArmature, cap.port);
  end DcpmThermalOne;
  model HeatPorts "B12-B13: R (alpha) and a rotational damper heating capacitors; R2 with its heat port open"
    Modelica.Electrical.Analog.Sources.ConstantVoltage v(V=10);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    Modelica.Electrical.Analog.Basic.Resistor r(R=10, T_ref=293.15, alpha=0.004, useHeatPort=true);
    HT.Components.HeatCapacitor cr(C=100, T(start=293.15, fixed=true));
    HT.Components.ThermalConductor gr(G=0.5);
    HT.Sources.FixedTemperature amb(T=293.15);
    Modelica.Mechanics.Rotational.Sources.ConstantSpeed spd(w_fixed=100);
    Modelica.Mechanics.Rotational.Components.Damper d(d=0.01, useHeatPort=true);
    Modelica.Mechanics.Rotational.Components.Fixed fx;
    HT.Components.HeatCapacitor cd(C=100, T(start=293.15, fixed=true));
  equation
    connect(v.p, r.p); connect(r.n, v.n); connect(v.n, gnd.p);
    connect(r.heatPort, cr.port); connect(cr.port, gr.port_a); connect(gr.port_b, amb.port);
    connect(spd.flange, d.flange_a); connect(d.flange_b, fx.flange); connect(d.heatPort, cd.port);
  end HeatPorts;
  model HeatPortOpen "B14: a resistor with useHeatPort=true and the port left unconnected"
    Modelica.Electrical.Analog.Sources.ConstantVoltage v(V=10);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    Modelica.Electrical.Analog.Basic.Resistor r(R=10, useHeatPort=true);
  equation
    connect(v.p, r.p); connect(r.n, v.n); connect(v.n, gnd.p);
  end HeatPortOpen;
  model Rectifier "B15: half-wave rectifier: 10 V 50 Hz, IdealDiode (Vknee 0.7), R 100 ohm || C 100 uF"
    Modelica.Electrical.Analog.Sources.SineVoltage vs(V=10, f=50);
    Modelica.Electrical.Analog.Ideal.IdealDiode dio(Vknee=0.7);
    Modelica.Electrical.Analog.Basic.Resistor rl(R=100);
    Modelica.Electrical.Analog.Basic.Capacitor c(C=1e-4, v(start=0, fixed=true));
    Modelica.Electrical.Analog.Basic.Ground gnd;
  equation
    connect(vs.p, dio.p); connect(dio.n, rl.p); connect(rl.n, gnd.p); connect(c.p, dio.n); connect(c.n, gnd.p);
    connect(vs.n, gnd.p);
  end Rectifier;
  model LimLoop "B16: P control of a first-order plant through a limiter (saturates, then leaves the limit)"
    Modelica.Blocks.Sources.Step sp(height=10, startTime=0.1);
    Modelica.Blocks.Math.Feedback fb;
    Modelica.Blocks.Math.Gain kp(k=5);
    Modelica.Blocks.Nonlinear.Limiter lim(uMax=20);
    Modelica.Blocks.Continuous.FirstOrder plant(k=1, T=1, initType=Modelica.Blocks.Types.Init.InitialState, y_start=0);
  equation
    connect(sp.y, fb.u1); connect(fb.y, kp.u); connect(kp.y, lim.u); connect(lim.y, plant.u);
    connect(plant.y, fb.u2);
  end LimLoop;
end OmctV2;
EOF

cat > build.mos <<'EOF'
loadModel(Modelica, {"4.0.0"}); getErrorString();
loadFile("v2.mo"); getErrorString();
EOF
for m in OpenLoop:60 ClosedLoop:300 PumpCurve:1 Dcpm:3 DcpmLosses:3 DcpmThermal:600 DcpmThermalOne:2 HeatPorts:600 HeatPortOpen:1 Rectifier:0.1 LimLoop:5; do
  n=${m%%:*}; t=${m#*:}
  mkdir -p "$n"
  cat >> build.mos <<EOF
checkModel(OmctV2.$n); getErrorString();
cd("$n"); buildModel(OmctV2.$n, stopTime=$t, numberOfIntervals=2000, tolerance=1e-6, outputFormat="mat", fileNamePrefix="m", variableFilter=".*"); getErrorString(); cd("..");
EOF
done

hdr "build (checkModel + buildModel of each probe model)"
timeout 1200 "$OMC" build.mos 2>&1 | cut -c1-700

run() {  # run <model> [extra flags...]
  local n=$1; shift
  echo "--- run $n $*"
  if [ -x "$W/$n/m" ]; then
    (cd "$W/$n" && timeout 120 ./m -lv=LOG_STATS "$@" 2>&1 | grep -v "^LOG_STATS" | cut -c1-300 | head -30; echo "exit=${PIPESTATUS[0]}")
    (cd "$W/$n" && timeout 120 ./m -lv=LOG_STATS -r=stats.mat "$@" 2>&1 | grep -E "state events|time events|steps taken|Jacobian" | head -6)
  else
    echo "(no executable: the build failed, see above)"
  fi
}

hdr "B1, B4-B16: runs"
for n in OpenLoop ClosedLoop PumpCurve Dcpm DcpmLosses DcpmThermal DcpmThermalOne HeatPorts HeatPortOpen Rectifier LimLoop; do run $n; done

hdr "B2: isValueChangeable of the parameters omct would set"
chg() {  # chg <model> <name>...
  local n=$1; shift
  for v in "$@"; do
    printf '  %-12s %-42s ' "$n" "$v"
    grep -A3 "name = \"$v\"" "$W/$n/m_init.xml" 2>/dev/null | tr -s ' \n' ' ' | grep -o 'isValueChangeable = "[a-z]*"\|variability = "[a-zA-Z]*"' | tr '\n' ' '
    grep -q "name = \"$v\"" "$W/$n/m_init.xml" 2>/dev/null || printf 'not in _init.xml'
    echo
  done
}
chg OpenLoop medium.rho medium.cp medium.cv pipe.medium.cp pump.medium.cp amb1.medium.cp pipe.m pipe.T0 pipe.T0fixed \
  pipe.V_flowLaminar pipe.dpLaminar pipe.V_flowNominal pipe.dpNominal pipe.h_g pipe.frictionLoss pipe.tapT \
  pump.constantVolumeFlow amb1.constantAmbientPressure amb1.constantAmbientTemperature q.Q_flow pipe.k
chg PumpCurve pump.wNominal pump.dp0 pump.V_flow0 spd.w_fixed
chg Dcpm dcpm.VaNominal dcpm.IaNominal dcpm.wNominal dcpm.TaNominal dcpm.Ra dcpm.TaRef dcpm.alpha20a dcpm.La dcpm.Jr \
  dcpm.Js dcpm.TaOperational dcpm.frictionParameters.PRef dcpm.frictionParameters.wRef dcpm.frictionParameters.power_w \
  dcpm.coreParameters.PRef dcpm.coreParameters.VRef dcpm.strayLoadParameters.PRef dcpm.strayLoadParameters.IRef \
  dcpm.brushParameters.V dcpm.brushParameters.ILinear dcpm.turnsRatio dcpm.ViNominal dcpm.useThermalPort
chg HeatPorts r.R r.alpha r.T_ref d.d
chg Rectifier dio.Ron dio.Goff dio.Vknee
chg LimLoop lim.uMax lim.uMin

hdr "B3, B8: overrides (flow, medium cp, machine data) and their effect"
printf 'pump.constantVolumeFlow=2e-4\nmedium.cp=2000\npipe.dpNominal=4000\n' > "$W/OpenLoop/ov.txt"
run OpenLoop -overrideFile=ov.txt -r=ov.mat
printf 'dcpm.VaNominal=200\ndcpm.Ra=0.1\ndcpm.Jr=0.3\n' > "$W/Dcpm/ov.txt"
run Dcpm -overrideFile=ov.txt -r=ov.mat
printf 'Vf=0\n' > "$W/ClosedLoop/ov.txt"
run ClosedLoop -overrideFile=ov.txt -r=zero.mat

hdr "values (final; expected values from the closed forms)"
cat > vals.py <<'EOF'
import math, os, sys
from openmodelica_translator import matfile
W = sys.argv[1]
def R(n, f="m_res.mat"):
    p = os.path.join(W, n, f)
    try:
        return matfile.Result(p)
    except Exception as e:
        print(f"  {n}/{f}: unreadable ({e})"); return None
def show(r, names, label=""):
    if r is None:
        return
    for v in names:
        try:
            s = r.series(v) if r.kind(v) != 1 else [r.value(v)]
            print(f"  {label:10s} {v:44s} first {float(s[0]):<16.9g} final {float(s[-1]):<16.9g}")
        except Exception as e:
            print(f"  {label:10s} {v:44s} missing ({type(e).__name__}: {e})")
rho, cp = 995.6, 4177.0
r = R("OpenLoop"); print("OpenLoop: expected T_out final", 293.15 + 2000 / (rho * cp * 1e-4), "K; tau", 0.5 * cp / (rho * 1e-4 * cp), "s")
show(r, ["pipe.T", "pipe.T_a", "pipe.T_b", "pipe.dp", "pipe.V_flow", "pump.dp", "pipe.Q_flow", "pipe.heatPort.T",
         "pipe.flowPort_a.H_flow", "pipe.flowPort_b.H_flow", "amb2.flowPort.H_flow", "medium.cp", "pipe.k"], "base")
show(R("OpenLoop", "ov.mat"), ["pipe.T", "pipe.dp", "pipe.V_flow", "medium.cp", "pipe.medium.cp", "pipe.dpNominal"], "override")
print("  override expected (if cp and V_flow both apply): T_out", 293.15 + 2000 / (rho * 2000 * 2e-4))
r = R("ClosedLoop"); print("ClosedLoop: expected steady cooler.T 298.15, heater.T", 298.15 + 1000 / (rho * cp * 1e-4))
show(r, ["heater.T", "cooler.T", "tHot.y", "pump.dp", "pref.flowPort.m_flow", "cooler.heatPort.Q_flow", "g.Q_flow"], "base")
show(R("ClosedLoop", "zero.mat"), ["heater.T", "cooler.T", "pump.V_flow", "heater.V_flow"], "Vf=0")
k = (2000 - 100 / 1e-5 * 1e-4) / (1e-4 - 1e-5) ** 2
def dp_pipe(V): return 100 / 1e-5 * V + k * (V - 1e-5) ** 2
lo, hi = 1e-5, 4e-4
for _ in range(200):
    mid = (lo + hi) / 2
    if 2e4 * (1 - mid / 4e-4) - dp_pipe(mid) > 0: lo = mid
    else: hi = mid
print(f"PumpCurve: expected operating point V_flow {lo:.9g} m3/s, dp {dp_pipe(lo):.9g} Pa")
show(R("PumpCurve"), ["pump.V_flow", "pump.dp", "pipe.dp", "spd.flange.tau", "pump.flange_a.tau"], "base")
Vi = 100 - 0.05 * 100; wN = 149.2256510455152; kk = Vi / wN
print(f"Dcpm: k = {kk:.9g} V.s/rad; expected final (after the nominal load) w {wN:.9g}, ia 100; no-load w {100/kk:.9g}")
show(R("Dcpm"), ["dcpm.wMechanical", "dcpm.ia", "dcpm.va", "dcpm.tauElectrical", "dcpm.tauShaft", "dcpm.turnsRatio",
                 "dcpm.powerBalance.powerArmature", "dcpm.powerBalance.powerMechanical",
                 "dcpm.powerBalance.lossPowerTotal", "dcpm.airGapDC.vai", "dcpm.ra.R_actual"], "base")
show(R("Dcpm", "ov.mat"), ["dcpm.wMechanical", "dcpm.ia", "dcpm.turnsRatio", "dcpm.VaNominal", "dcpm.Ra", "dcpm.ra.R"], "override")
print("  override: if turnsRatio follows VaNominal=200, Ra=0.1: k =", (200 - 0.1 * 100) / wN)
r = R("DcpmLosses")
show(r, ["dcpm.wMechanical", "dcpm.ia", "dcpm.powerBalance.powerArmature", "dcpm.powerBalance.powerMechanical",
         "dcpm.powerBalance.powerInertiaRotor", "dcpm.powerBalance.powerInertiaStator", "dcpm.powerBalance.lossPowerTotal",
         "dcpm.powerBalance.lossPowerArmature", "dcpm.powerBalance.lossPowerCore", "dcpm.powerBalance.lossPowerStrayLoad",
         "dcpm.powerBalance.lossPowerFriction", "dcpm.powerBalance.lossPowerBrush"], "losses")
if r is not None:
    try:
        pb = "dcpm.powerBalance."
        res = (r.series(pb + "powerArmature") - r.series(pb + "powerMechanical") - r.series(pb + "powerInertiaRotor")
               - r.series(pb + "powerInertiaStator") - r.series(pb + "lossPowerTotal"))
        print("  power balance residual max |Parm - Pmech - Pinertia - Ploss| =", float(abs(res).max()), "W")
    except Exception as e:
        print("  power balance: ", e)
show(R("DcpmThermal"), ["cap.T", "dcpm.ra.R_actual", "dcpm.wMechanical", "dcpm.ia", "dcpm.powerBalance.lossPowerTotal",
                        "g.Q_flow", "dcpm.thermalPort.heatPortPermanentMagnet.Q_flow"], "thermal")
show(R("DcpmThermalOne"), ["cap.T", "dcpm.ia", "dcpm.wMechanical", "dcpm.thermalPort.heatPortFriction.Q_flow",
                           "dcpm.thermalPort.heatPortFriction.T"], "one port")
print("HeatPorts: r with alpha 0.004: steady T solves G (T - Ta) = V^2 / (R (1 + alpha (T - Ta)))")
Ta, G, V, R0, al = 293.15, 0.5, 10.0, 10.0, 0.004
lo, hi = 0.0, 1000.0
for _ in range(200):
    m = (lo + hi) / 2
    if G * m - V * V / (R0 * (1 + al * m)) < 0: lo = m
    else: hi = m
print(f"  expected steady cr.T {Ta + lo:.9g} K (tau ~ {100/G:g} s; the run stops at 600 s); damper: 100 W into cd: "
      f"cd.T(600) = {293.15 + 0.01 * 100 ** 2 * 600 / 100:.9g} K")
show(R("HeatPorts"), ["cr.T", "r.R_actual", "r.LossPower", "r.heatPort.Q_flow", "cd.T", "d.lossPower"], "heat")
show(R("HeatPortOpen"), ["r.i", "r.LossPower", "r.T_heatPort"], "open")
r = R("Rectifier")
show(r, ["c.v", "dio.i", "dio.v", "dio.off", "rl.i"], "rect")
if r is not None:
    try:
        t, v = r.time, r.series("c.v")
        last = t >= 0.08
        print("  c.v over the last cycle: min", float(v[last].min()), "max", float(v[last].max()), "(peak ~ 10 - 0.7 = 9.3 V)")
    except Exception as e:
        print("  rectifier:", e)
show(R("LimLoop"), ["plant.y", "lim.y", "lim.u"], "lim")
print("  LimLoop: saturated while 5 (10 - y) > 20, i.e. y < 6: y = 20 (1 - exp(-(t - 0.1))) until y = 6 at t =",
      0.1 - math.log(1 - 6 / 20), "then y -> 10*5/6 =", 50 / 6)
EOF
"$PY" vals.py "$W" 2>&1 | cut -c1-400

hdr "B17: build123d matrix of inertia (units, about the centre of mass, sign of the products)"
cat > inertia.py <<'EOF'
import math
import build123d as bd
print("build123d", bd.__version__)
a, b, c = 100.0, 10.0, 20.0
box = bd.Box(a, b, c)
V = a * b * c
print("box", a, b, c, "volume", box.volume, "center", tuple(box.center()))
print("  matrix_of_inertia", [[round(x, 3) for x in row] for row in box.matrix_of_inertia])
print("  expected (unit density, mm^5) Ixx", V * (b * b + c * c) / 12, "Iyy", V * (a * a + c * c) / 12, "Izz", V * (a * a + b * b) / 12)
moved = bd.Pos(50, 20, 0) * box
print("moved by (50, 20, 0): center", tuple(moved.center()), "matrix", [[round(x, 3) for x in row] for row in moved.matrix_of_inertia])
rot = bd.Rot(0, 0, 30) * box
th = math.radians(30)
cs, sn = math.cos(th), math.sin(th)
Ixx0, Iyy0 = V * (b * b + c * c) / 12, V * (a * a + c * c) / 12
# rotated tensor (true tensor, off-diagonal = -integral x y): I' = R I R^T
Ixx = cs * cs * Ixx0 + sn * sn * Iyy0
Iyy = sn * sn * Ixx0 + cs * cs * Iyy0
Ixy = cs * sn * (Ixx0 - Iyy0)
print("rotated 30 deg about Z: matrix", [[round(x, 3) for x in row] for row in rot.matrix_of_inertia])
print("  expected tensor Ixx", Ixx, "Iyy", Iyy, "Ixy (tensor, = -integral xy)", Ixy, "; integral xy itself", -Ixy)
cyl = bd.Cylinder(20, 50)
print("cylinder r 20 h 50: Izz", cyl.matrix_of_inertia[2][2], "expected", math.pi * 20 ** 2 * 50 * 20 ** 2 / 2)
print("principal_properties (box rotated)", [(tuple(round(x, 6) for x in ax), round(m, 3)) for ax, m in rot.principal_properties])
EOF
"$PY" inertia.py 2>&1 | cut -c1-500

hdr "done"
du -sh "$W"
