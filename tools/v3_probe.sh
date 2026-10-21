#!/bin/bash
# v3_probe.sh — openmodelica-translator, version 3: AC machines with OpenModelica 1.27.1 + MSL 4.0.0.
# Version: 2026.10.21.19
#
#   ssh nicola@mochi.mit.edu 'bash -s' < tools/v3_probe.sh > tools/v3_report_mochi.txt 2>&1
# About 5-10 minutes. Works in ~/omct_v3probe. Each probe is answered in planning.md ("Assumptions for
# version 3", C1, C2, ...).
#   C1  the MSL examples build and run: IMC_DOL, IMC_Inverter, SMPM_CurrentSource, SMPM_VoltageSource (transient),
#       the quasi-static IMC_DOL and SMPM_CurrentSource (build and run times, steps, events)
#   C2  isValueChangeable of the machine parameters omct would set (and the loss records' finals, B18)
#   C3  an induction machine on a 50 Hz grid reaches the slip of the equivalent circuit at a constant load
#   C4  a current-fed PMSM gives torque 3/2 p psiM sqrt(2) Iq (Ld = Lq) and the dq voltages
#   C5  a V/f inverter start (ramped frequency), steady speed
#   C6  run time against stop time (50 Hz ripple: steps per period)

set -u
OMC=${OMCT_OMC:-omc}
PY=${OMCT_PY:-$HOME/Software/llm-translators/.venv/bin/python}
W=$HOME/omct_v3probe
rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
hdr() { echo; echo "################ $*"; }
hdr "machine"; date -Is; "$OMC" --version

cat > v3.mo <<'EOF'
package OmctV3
  model ImGrid "C3: IM_SquirrelCage (MSL example data, Y) on a 100 V (phase RMS) 50 Hz grid, J 0.29 + 0.29, load 100 N.m from 0.5 s"
    import Modelica.Constants.pi;
    parameter Modelica.Electrical.Machines.Utilities.ParameterRecords.IM_SquirrelCageData d;
    Modelica.Electrical.Machines.BasicMachines.InductionMachines.IM_SquirrelCage im(p=d.p, fsNominal=d.fsNominal,
      Rs=d.Rs, TsRef=d.TsRef, alpha20s=d.alpha20s, Lszero=d.Lszero, Lssigma=d.Lssigma, Jr=d.Jr, Js=d.Js,
      frictionParameters=d.frictionParameters, statorCoreParameters=d.statorCoreParameters,
      strayLoadParameters=d.strayLoadParameters, Lm=d.Lm, Lrsigma=d.Lrsigma, Rr=d.Rr, TrRef=d.TrRef,
      alpha20r=d.alpha20r, TsOperational=293.15, TrOperational=293.15,
      phiMechanical(fixed=true, start=0), wMechanical(fixed=true, start=0));
    Modelica.Electrical.Machines.Utilities.TerminalBox tb(terminalConnection="Y");
    Modelica.Electrical.Polyphase.Sources.SineVoltage vs(m=3, V=fill(sqrt(2)*100, 3), f=fill(50, 3));
    Modelica.Electrical.Polyphase.Basic.Star star(m=3);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    Modelica.Mechanics.Rotational.Components.Inertia load(J=0.29);
    Modelica.Mechanics.Rotational.Sources.TorqueStep tl(stepTorque=-100, startTime=0.5);
    Modelica.Electrical.Machines.Sensors.CurrentQuasiRMSSensor irms;
    Modelica.Electrical.Machines.Sensors.ElectricalPowerSensor pw;
  initial equation
    im.is[1:2] = zeros(2);
    im.ir = zeros(2);
  equation
    connect(vs.plug_n, star.plug_p); connect(star.pin_n, gnd.p);
    connect(vs.plug_p, pw.plug_p); connect(pw.plug_ni, irms.plug_p); connect(irms.plug_n, tb.plugSupply);
    connect(pw.plug_nv, star.plug_p);
    connect(tb.plug_sp, im.plug_sp); connect(tb.plug_sn, im.plug_sn);
    connect(im.flange, load.flange_a); connect(load.flange_b, tl.flange);
  end ImGrid;
  model PmsmCurrent "C4: SM_PermanentMagnet (no damper cage) fed with Id = -53.5, Iq = 84.6 A RMS, quadratic load 181.4 N.m at 157 rad/s"
    parameter Modelica.Electrical.Machines.Utilities.ParameterRecords.SM_PermanentMagnetData d(useDamperCage=false);
    Modelica.Electrical.Machines.BasicMachines.SynchronousMachines.SM_PermanentMagnet pm(p=d.p, fsNominal=d.fsNominal,
      Rs=d.Rs, TsRef=d.TsRef, alpha20s=d.alpha20s, Lszero=d.Lszero, Lssigma=d.Lssigma, Jr=d.Jr, Js=d.Js,
      frictionParameters=d.frictionParameters, statorCoreParameters=d.statorCoreParameters,
      strayLoadParameters=d.strayLoadParameters, VsOpenCircuit=d.VsOpenCircuit, Lmd=d.Lmd, Lmq=d.Lmq,
      useDamperCage=d.useDamperCage, Lrsigmad=d.Lrsigmad, Lrsigmaq=d.Lrsigmaq, Rrd=d.Rrd, Rrq=d.Rrq, TrRef=d.TrRef,
      alpha20r=d.alpha20r, permanentMagnetLossParameters=d.permanentMagnetLossParameters, TsOperational=293.15,
      TrOperational=293.15, phiMechanical(fixed=true, start=0), wMechanical(fixed=true, start=0));
    Modelica.Electrical.Machines.Utilities.TerminalBox tb(terminalConnection="Y");
    Modelica.Electrical.Polyphase.Sources.SignalCurrent ic(m=3);
    Modelica.Electrical.Polyphase.Basic.Star star(m=3);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    Modelica.Electrical.Machines.Utilities.DQToThreePhase dq(p=d.p);
    Modelica.Blocks.Sources.Constant id(k=-53.5);
    Modelica.Blocks.Sources.Constant iq(k=84.6);
    Modelica.Mechanics.Rotational.Sensors.AngleSensor ang;
    Modelica.Mechanics.Rotational.Components.Inertia load(J=0.29);
    Modelica.Mechanics.Rotational.Sources.QuadraticSpeedDependentTorque tl(tau_nominal=-181.4,
      w_nominal=157.07963267949, TorqueDirection=false);
    Modelica.Electrical.Machines.Sensors.VoltageQuasiRMSSensor vrms;
    Modelica.Electrical.Polyphase.Basic.Star starM(m=3);
    Modelica.Electrical.Machines.Sensors.ElectricalPowerSensor pw;
  equation
    connect(star.pin_n, gnd.p); connect(ic.plug_n, star.plug_p); connect(ic.plug_p, pw.plug_p);
    connect(pw.plug_ni, tb.plugSupply); connect(pw.plug_nv, starM.plug_p); connect(tb.starpoint, starM.pin_n);
    connect(vrms.plug_p, tb.plugSupply); connect(vrms.plug_n, starM.plug_p);
    connect(tb.plug_sp, pm.plug_sp); connect(tb.plug_sn, pm.plug_sn);
    connect(id.y, dq.d); connect(iq.y, dq.q); connect(dq.y, ic.i); connect(ang.phi, dq.phi);
    connect(pm.flange, ang.flange); connect(pm.flange, load.flange_a); connect(load.flange_b, tl.flange);
  end PmsmCurrent;
  model ImVf "C5: the same IM started by a V/f inverter (0 -> 50 Hz in 1 s), load 100 N.m from 1.2 s"
    parameter Modelica.Electrical.Machines.Utilities.ParameterRecords.IM_SquirrelCageData d;
    Modelica.Electrical.Machines.BasicMachines.InductionMachines.IM_SquirrelCage im(p=d.p, fsNominal=d.fsNominal,
      Rs=d.Rs, TsRef=d.TsRef, alpha20s=d.alpha20s, Lszero=d.Lszero, Lssigma=d.Lssigma, Jr=d.Jr, Js=d.Js,
      frictionParameters=d.frictionParameters, statorCoreParameters=d.statorCoreParameters,
      strayLoadParameters=d.strayLoadParameters, Lm=d.Lm, Lrsigma=d.Lrsigma, Rr=d.Rr, TrRef=d.TrRef,
      alpha20r=d.alpha20r, TsOperational=293.15, TrOperational=293.15,
      phiMechanical(fixed=true, start=0), wMechanical(fixed=true, start=0));
    Modelica.Electrical.Machines.Utilities.TerminalBox tb(terminalConnection="Y");
    Modelica.Blocks.Sources.Ramp f(height=50, duration=1);
    Modelica.Electrical.Machines.Utilities.VfController vf(VNominal=100, fNominal=50);
    Modelica.Electrical.Polyphase.Sources.SignalVoltage sv(m=3);
    Modelica.Electrical.Polyphase.Basic.Star star(m=3);
    Modelica.Electrical.Analog.Basic.Ground gnd;
    Modelica.Mechanics.Rotational.Components.Inertia load(J=0.29);
    Modelica.Mechanics.Rotational.Sources.TorqueStep tl(stepTorque=-100, startTime=1.2);
  initial equation
    im.is[1:2] = zeros(2);
    im.ir = zeros(2);
  equation
    connect(f.y, vf.u); connect(vf.y, sv.v); connect(sv.plug_n, star.plug_p); connect(star.pin_n, gnd.p);
    connect(sv.plug_p, tb.plugSupply); connect(tb.plug_sp, im.plug_sp); connect(tb.plug_sn, im.plug_sn);
    connect(im.flange, load.flange_a); connect(load.flange_b, tl.flange);
  end ImVf;
end OmctV3;
EOF

cat > build.mos <<'EOF'
loadModel(Modelica, {"4.0.0"}); getErrorString();
loadFile("v3.mo"); getErrorString();
EOF
models="ImGrid:2 PmsmCurrent:1.5 ImVf:1.5 \
Modelica.Electrical.Machines.Examples.InductionMachines.IMC_DOL:1.5 \
Modelica.Electrical.Machines.Examples.InductionMachines.IMC_Inverter:1.5 \
Modelica.Electrical.Machines.Examples.SynchronousMachines.SMPM_CurrentSource:1.5 \
Modelica.Electrical.Machines.Examples.SynchronousMachines.SMPM_VoltageSource:1.5 \
Modelica.Magnetic.QuasiStatic.FundamentalWave.Examples.BasicMachines.InductionMachines.IMC_DOL:1.5 \
Modelica.Magnetic.QuasiStatic.FundamentalWave.Examples.BasicMachines.SynchronousMachines.SMPM_CurrentSource:1.5"
for mm in $models; do
  n=${mm%%:*}; t=${mm#*:}; dir=$(echo "$n" | sed 's/.*\.//; s/^/d_/')
  case "$n" in Modelica.*) cls=$n;; *) cls=OmctV3.$n;; esac
  [ "${n#Modelica.Magnetic.QuasiStatic}" != "$n" ] && dir="qs_$(echo "$n" | sed 's/.*\.//')"
  mkdir -p "$dir"
  cat >> build.mos <<EOF
cd("$dir"); buildModel($cls, stopTime=$t, numberOfIntervals=3000, tolerance=1e-6, outputFormat="mat", fileNamePrefix="m", variableFilter=".*"); getErrorString(); cd("..");
EOF
done

hdr "C1: build all (omc time)"
/usr/bin/time -f "  [time] %e s wall" timeout 3000 "$OMC" -n=4 build.mos 2>&1 | cut -c1-500 | grep -v "^\"\"$" | head -150

hdr "C1, C6: runs (time, steps, events)"
for d in */; do
  d=${d%/}
  echo "--- $d"
  if [ -x "$W/$d/m" ]; then
    (cd "$W/$d" && /usr/bin/time -f "  [time] %e s wall" timeout 600 ./m -lv=LOG_STATS 2>&1 | grep -E "time\]|steps taken|state events|time events|calls of functionODE|evaluations of jacobian|error|assert|LOG_ASSERT" | head -12)
  else
    echo "  (no executable)"
  fi
done

hdr "C2: isValueChangeable (attribute printed only when false)"
chg() { local d=$1; shift; for v in "$@"; do printf '  %-14s %-44s ' "$d" "$v"
  grep -A3 "name = \"$v\"" "$W/$d/m_init.xml" 2>/dev/null | tr -s ' \n' ' ' | grep -o 'isValueChangeable = "[a-z]*"\|variability = "[a-zA-Z]*"' | tr '\n' ' '
  grep -q "name = \"$v\"" "$W/$d/m_init.xml" 2>/dev/null || printf 'not in _init.xml'; echo; done; }
chg d_ImGrid im.Rs im.Rr im.Lm im.Lssigma im.Lrsigma im.Lszero im.Jr im.p im.fsNominal im.TsOperational im.alpha20s \
  im.frictionParameters.PRef im.frictionParameters.tauRef im.statorCoreParameters.PRef im.statorCoreParameters.GcRef \
  im.strayLoadParameters.PRef im.airGap.L im.airGap.Lm vs.V vs.f d.Rs
chg d_PmsmCurrent pm.VsOpenCircuit pm.Lmd pm.Lmq pm.Rs pm.p pm.useDamperCage pm.Ie id.k iq.k tl.tau_nominal

hdr "C3-C5: values"
cat > vals.py <<'EOF'
import cmath, math, os, sys
import numpy as np
from openmodelica_translator import matfile
W = sys.argv[1]
def R(d):
    try:
        return matfile.Result(os.path.join(W, d, "m_res.mat"))
    except Exception as e:
        print(f"  {d}: unreadable ({e})"); return None
def fin(r, v):
    try:
        s = r.series(v) if r.kind(v) != 1 else [r.value(v)]
        return float(s[-1])
    except Exception as e:
        return f"missing ({e})"
# C3: equivalent circuit of IM_SquirrelCageData (Rs .03, Lssigma, Lm, Lrsigma, Rr .04, p 2, 50 Hz), 100 V phase
f, p, Rs, Rr = 50.0, 2, 0.03, 0.04
w = 2 * math.pi * f
Lssigma = 3 * (1 - math.sqrt(1 - 0.0667)) / w
Lrsigma = Lssigma
Lm = 3 * math.sqrt(1 - 0.0667) / w
def torque(s):
    Zs = Rs + 1j * w * Lssigma
    Zm = 1j * w * Lm
    Zr = Rr / s + 1j * w * Lrsigma
    Is = 100 / (Zs + Zm * Zr / (Zm + Zr))
    Ir = Is * Zm / (Zm + Zr)
    return 3 * p / w * abs(Ir) ** 2 * Rr / s, abs(Is)
lo, hi = 1e-6, 0.2
for _ in range(200):
    m_ = (lo + hi) / 2
    if torque(m_)[0] < 100: lo = m_
    else: hi = m_
s = (lo + hi) / 2
print(f"C3 equivalent circuit at 100 N.m: slip {s:.9g}, speed {w / p * (1 - s):.9g} rad/s, Is {torque(s)[1]:.9g} A RMS")
r = R("d_ImGrid")
if r is not None:
    for v in ("im.wMechanical", "im.tauElectrical", "irms.I", "pw.P", "pw.Q", "im.powerBalance.lossPowerTotal"):
        print(f"  ImGrid {v:36s} final {fin(r, v)}")
# C4: PMSM current fed, Ld = Lq: T = 3/2 p psiM sqrt(2) Iq, psiM = sqrt(2) VsOpenCircuit / (2 pi fsNominal)
psiM = math.sqrt(2) * 112.3 / (2 * math.pi * 50)
T = 1.5 * 2 * psiM * math.sqrt(2) * 84.6
wN = 157.07963267949
wss = wN * math.sqrt(T / 181.4)
print(f"C4 torque {T:.9g} N.m, steady speed {wss:.9g} rad/s (quadratic load)")
r = R("d_PmsmCurrent")
if r is not None:
    for v in ("pm.tauElectrical", "pm.wMechanical", "vrms.V", "pw.P", "pw.Q", "pm.powerBalance.lossPowerTotal"):
        print(f"  PmsmCurrent {v:31s} final {fin(r, v)}")
    # dq steady voltages (RMS per phase): Vd = Rs Id - we Lq Iq, Vq = Rs Iq + we Ld Id + we psiM/sqrt2
    Ld = Lq = 0.1 / (2 * math.pi * 50) + 0.3 / (2 * math.pi * 50)   # Lssigma + Lmd (smpmData)
    we = p * wss
    Vd = 0.03 * -53.5 - we * Lq * 84.6
    Vq = 0.03 * 84.6 + we * Ld * -53.5 + we * psiM / math.sqrt(2)
    print(f"  expected stator RMS voltage per phase {math.hypot(Vd, Vq):.9g} V (Rs 0.03, Ld = Lq = {Ld:.6g} H)")
r = R("d_ImVf")
if r is not None:
    for v in ("im.wMechanical", "im.tauElectrical"):
        print(f"  ImVf {v:38s} final {fin(r, v)}")
    print(f"  ImVf expected final speed {w / p * (1 - s):.9g} (100 N.m at 50 Hz, 100 V)")
EOF
"$PY" vals.py "$W" 2>&1 | cut -c1-300

hdr "done"; du -sh "$W"
