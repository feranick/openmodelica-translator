#!/bin/bash
# m0_probe.sh — openmodelica-translator, milestone 0: what OpenModelica 1.27.1 actually does on this machine.
# Version: 2026.10.20.1
#
# Run on mochi from the Mac (the report lands in the repository, where Claude reads it):
#   ssh nicola@mochi.mit.edu 'bash -s' < tools/m0_probe.sh > tools/m0_report_mochi.txt 2>&1
# Or on mochi itself:  bash m0_probe.sh > m0_report_mochi.txt 2>&1
# Takes about 1-3 minutes. Works in ~/omct_probe (deleted and recreated). Needs omc and python3 (no packages).
# Each probe is answered in planning.md, "Assumptions about OpenModelica" (A1, A2, ...).

set -u
OMC=${OMCT_OMC:-omc}
W=$HOME/omct_probe
rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
PY=python3
hdr() { echo; echo "################ $*"; }
if [ -x /usr/bin/time ]; then
  tm() { /usr/bin/time -f "  [time] %e s wall, %U s user, %S s sys, %M kB max" "$@"; }
else
  tm() { local s=$(date +%s.%N); "$@"; local rc=$?; echo "  [time] $(echo "$(date +%s.%N) - $s" | bc) s wall (no /usr/bin/time)" >&2; return $rc; }
fi

hdr "machine"
date -Is; uname -a; nproc; free -g | head -2
command -v "$OMC"; "$OMC" --version; command -v gcc clang; gcc --version | head -1
ls /etc/apt/sources.list.d/ 2>/dev/null; cat /etc/apt/sources.list.d/*openmodelica* 2>/dev/null
ls ~/.openmodelica/libraries 2>/dev/null; head -c 600 ~/.openmodelica/libraries/index.json 2>/dev/null; echo

# ------------------------------------------------------------------------------------------------ models
cat > probe.mo <<'EOF'
package OmctProbe
  model RLC "series R-L-C driven by a step plus input u; y = C.v; R2 (large) across C"
    parameter Real Rv = 10 "primary parameter";
    parameter Real Rdep = 2*Rv "bound to another parameter";
    parameter Real Rev = 1 annotation(Evaluate=true);
    final parameter Real Rfin = 3;
    Modelica.Blocks.Interfaces.RealInput u;
    Modelica.Blocks.Interfaces.RealOutput y;
    Modelica.Electrical.Analog.Basic.Resistor R(R=Rv);
    Modelica.Electrical.Analog.Basic.Resistor R2(R=1000*(Rdep + Rev + Rfin));
    Modelica.Electrical.Analog.Basic.Inductor L(L=0.01, i(start=0, fixed=true));
    Modelica.Electrical.Analog.Basic.Capacitor C(C=1e-4, v(start=0, fixed=true));
    Modelica.Electrical.Analog.Sources.SignalVoltage V;
    Modelica.Blocks.Sources.Step st(height=12, startTime=0.001);
    Modelica.Blocks.Math.Add add;
    Modelica.Electrical.Analog.Basic.Ground gnd;
  equation
    connect(st.y, add.u1); connect(u, add.u2); connect(add.y, V.v);
    connect(V.p, R.p); connect(R.n, L.p); connect(L.n, C.p); connect(C.n, gnd.p); connect(V.n, gnd.p);
    connect(R2.p, C.p); connect(R2.n, gnd.p);
    y = C.v;
  end RLC;
  model Under "one equation short"
    Real x(start=1, fixed=true); Real z;
  equation
    der(x) = -x;
  end Under;
  model BadMod
    Modelica.Electrical.Analog.Basic.Resistor R(Rx=1);
  end BadMod;
  model BadClass
    Modelica.Electrical.Analog.Basic.Resistorr R;
  end BadClass;
  model NoGround "an RC loop without ground"
    Modelica.Electrical.Analog.Basic.Resistor R(R=1);
    Modelica.Electrical.Analog.Basic.Capacitor C(C=1);
    Modelica.Electrical.Analog.Sources.ConstantVoltage V(V=1);
  equation
    connect(V.p, R.p); connect(R.n, C.p); connect(C.n, V.n);
  end NoGround;
  model AssertFail
    extends RLC;
  equation
    assert(time < 0.01, "omct probe assert");
  end AssertFail;
  model InitFail
    Real x(start=1);
    Real y(start=0, fixed=true);
  equation
    0 = x*x + 1 + 0*y;
    der(y) = x;
  end InitFail;
  model Chatter
    Real x(start=1, fixed=true);
  equation
    der(x) = if x > 0 then -1 else 1;
  end Chatter;
  model Thermal "2 kg aluminium block, 50 W, h A = 10*0.06 W/K to 20 C ambient"
    Modelica.Thermal.HeatTransfer.Components.HeatCapacitor m(C=2*900, T(start=293.15, fixed=true));
    Modelica.Thermal.HeatTransfer.Sources.FixedHeatFlow q(Q_flow=50);
    Modelica.Thermal.HeatTransfer.Components.ThermalConductor hA(G=10*0.06);
    Modelica.Thermal.HeatTransfer.Sources.FixedTemperature amb(T=293.15);
  equation
    connect(q.port, m.port); connect(m.port, hA.port_a); connect(hA.port_b, amb.port);
  end Thermal;
end OmctProbe;
EOF

cat > build.mos <<'EOF'
setCommandLineOptions("--linearizationDumpLanguage=modelica"); getErrorString();
loadModel(Modelica, {"4.0.0"}); getErrorString();
getVersion(Modelica);
getLoadedLibraries();
loadFile("probe.mo"); getErrorString();
checkModel(OmctProbe.RLC); getErrorString();
checkModel(OmctProbe.Under); getMessagesStringInternal(); getErrorString();
checkModel(OmctProbe.BadMod); getMessagesStringInternal(); getErrorString();
checkModel(OmctProbe.BadClass); getErrorString();
checkModel(OmctProbe.NoGround); getErrorString();
cd("rlc"); buildModel(OmctProbe.RLC, stopTime=0.05, numberOfIntervals=2000, tolerance=1e-6, outputFormat="mat", fileNamePrefix="omct_model", variableFilter="time|R\\.R|R\\.i|R2\\.R|C\\.C|C\\.v|L\\.i|L\\.v|y|u|Rv|Rdep|Rev|Rfin|V\\.v|V\\.i|R\\.LossPower"); getErrorString();
cd(".."); cd("nognd"); buildModel(OmctProbe.NoGround, stopTime=1); getErrorString();
cd(".."); cd("assert"); buildModel(OmctProbe.AssertFail, stopTime=0.05); getErrorString();
cd(".."); cd("init"); buildModel(OmctProbe.InitFail, stopTime=1); getErrorString();
cd(".."); cd("chatter"); buildModel(OmctProbe.Chatter, stopTime=3); getErrorString();
cd(".."); cd("thermal"); buildModel(OmctProbe.Thermal, stopTime=20000, numberOfIntervals=1000); getErrorString();
EOF
mkdir -p rlc nognd assert init chatter thermal

hdr "A1-A5, A13: omc script (load MSL pinned, check, build; error formats)"
tm timeout 600 "$OMC" build.mos 2>&1 | cut -c1-600

hdr "A6: files a build leaves (rlc/)"
ls -la rlc | head -80
du -sh rlc
grep -o 'variableFilter = "[^"]*"' rlc/omct_model_init.xml | head -2
grep -c '<ScalarVariable' rlc/omct_model_init.xml
for v in Rv Rdep Rev Rfin R.R R2.R C.C C.v L.i; do
  grep -A2 "name = \"$v\"" rlc/omct_model_init.xml | tr -s ' \n' ' ' | cut -c1-260; echo
done

B=$W/rlc; X=$B/omct_model
hdr "A7: plain run (-lv=LOG_STATS), exit code"
(cd "$B" && tm "$X" -lv=LOG_STATS -r=base.mat; echo "exit=$?") 2>&1 | cut -c1-300

hdr "A8: -overrideFile: primary, bound, Evaluate, final, unknown, state starts"
printf 'Rv=20\nC.C=2e-4\nRdep=5\nRev=7\nRfin=9\nnosuch=1\nC.v=1\nL.i=0.5\n' > "$B/ov.txt"
(cd "$B" && "$X" -overrideFile=ov.txt -r=ov.mat; echo "exit=$?") 2>&1 | cut -c1-300

hdr "A9: -stopTime -tolerance -stepSize from the command line"
(cd "$B" && "$X" -stopTime=0.02 -tolerance=1e-8 -stepSize=1e-5 -r=st.mat; echo "exit=$?") 2>&1 | cut -c1-300

hdr "A10: run from another folder with only the cache files (no .c/.o/.makefile)"
mkdir -p "$W/cache" "$W/run1"
for f in "$B"/omct_model "$B"/omct_model_init.xml "$B"/omct_model_info.json "$B"/omct_model*.bin "$B"/omct_model.libs "$B"/omct_model.log; do [ -e "$f" ] && cp -a "$f" "$W/cache/"; done
ls "$W/cache"
(cd "$W/run1" && "$W/cache/omct_model" -inputPath="$W/cache" -outputPath="$W/run1" -r=res.mat; echo "exit=$?"; ls) 2>&1 | cut -c1-300

hdr "A11: runtime -variableFilter"
(cd "$B" && "$X" -variableFilter='C\.v|Rv' -r=vf.mat; echo "exit=$?") 2>&1 | cut -c1-300

hdr "A12: -steadyState (default tol and 1e-6) on the thermal block (tau = 3000 s, dT/dt ~ 0.028 K/s at t=0)"
(cd "$W/thermal" && ./OmctProbe.Thermal -steadyState -r=ss_default.mat; echo "exit=$?") 2>&1 | cut -c1-300
(cd "$W/thermal" && ./OmctProbe.Thermal -steadyState -steadyStateTol=1e-6 -r=ss_1e-6.mat; echo "exit=$?") 2>&1 | cut -c1-300

hdr "A14: linearize from the same build: -l=0.05 and -l=0"
mkdir -p "$B/lin" "$B/lin0"
(cd "$B" && "$X" -l=0.05 -outputPath="$B/lin"; echo "exit=$?") 2>&1 | cut -c1-300
cat "$B/lin/linearized_model.mo" 2>/dev/null | head -40
(cd "$B" && "$X" -l=0 -outputPath="$B/lin0" >/dev/null 2>&1; echo "exit=$?"; ls "$B/lin0")

hdr "A15: failures: no ground, assert, initialisation, chattering (log + exit code)"
for d in nognd:OmctProbe.NoGround assert:OmctProbe.AssertFail init:OmctProbe.InitFail chatter:OmctProbe.Chatter; do
  dir=${d%%:*}; exe=${d#*:}
  echo "--- $dir"
  if [ -x "$W/$dir/$exe" ]; then (cd "$W/$dir" && timeout 120 "./$exe" -lv=LOG_STATS 2>&1 | cut -c1-300 | head -40; echo "exit=${PIPESTATUS[0]}"); else echo "(no executable: build failed, see above)"; fi
done

hdr "A16: threads and compile parallelism (ChuaCircuit)"
cat > chua.mos <<'EOF'
loadModel(Modelica, {"4.0.0"}); getErrorString();
buildModel(Modelica.Electrical.Analog.Examples.ChuaCircuit, stopTime=10000, numberOfIntervals=50000); getErrorString();
EOF
for n in 1 4 0; do
  mkdir -p "$W/chua_n$n"; echo "--- omc -n=$n"
  (cd "$W/chua_n$n" && tm "$OMC" -n=$n ../chua.mos 2>&1 | tail -2 | cut -c1-200)
done
C=$W/chua_n0/Modelica.Electrical.Analog.Examples.ChuaCircuit
ldd "$C" | grep -i -E "omp|blas|lapack|sundials|pthread" | head
for env in "" "OMP_NUM_THREADS=1" "OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1"; do
  echo "--- run with: ${env:-defaults}"
  (cd "$W/chua_n0" && for i in 1 2; do tm env $env "$C" -r=t$i.mat >/dev/null; done) 2>&1 | grep time
done
echo "--- 4 runs at once, OMP_NUM_THREADS=1"
(cd "$W/chua_n0" && for i in 1 2 3 4; do (tm env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "$C" -r=c$i.mat >/dev/null) 2>&1 | grep time & done; wait)

hdr "A17: the MAT v4 result files (layout, parameters, read-back of overrides)"
cat > readmat.py <<'EOF'
import struct, sys
def read_mat4(path):
    out, order = {}, []
    with open(path, "rb") as f:
        while True:
            h = f.read(20)
            if len(h) < 20:
                break
            mopt, m, n, imagf, namlen = struct.unpack("<5i", h)
            name = f.read(namlen).rstrip(b"\0").decode()
            P = (mopt // 10) % 10
            T = mopt % 10
            size = {0: 8, 1: 4, 2: 4, 3: 2, 4: 2, 5: 1}[P]
            fmt = {0: "d", 1: "f", 2: "i", 3: "h", 4: "H", 5: "B"}[P]
            raw = f.read(m * n * size)
            vals = struct.unpack("<%d%s" % (m * n, fmt), raw)
            cols = [vals[j * m:(j + 1) * m] for j in range(n)]          # column-major
            out[name] = (mopt, m, n, T, cols)
            order.append(name)
    return out, order
def strings(mat, trans):
    mopt, m, n, T, cols = mat
    if trans:   # one name per column
        return ["".join(chr(c) for c in col).rstrip("\0 ") for col in cols]
    return ["".join(chr(cols[j][i]) for j in range(n)).rstrip("\0 ") for i in range(m)]
for path in sys.argv[1:]:
    try:
        d, order = read_mat4(path)
    except OSError as e:
        print(path, "unreadable:", e); continue
    ac = strings(d["Aclass"], False)
    trans = ac[3].startswith("binTrans")
    names = strings(d["name"], trans)
    di = d["dataInfo"]
    info = [list(c) for c in di[4]] if trans else [[di[4][j][i] for j in range(di[2])] for i in range(di[1])]
    print("==", path, "| matrices", [(k, d[k][1], d[k][2]) for k in order], "| Aclass", ac)
    print("   variables", len(names), ":", names[:40])
    def series(k, idx):
        mopt, m, n, T, cols = d["data_%d" % k]
        s = -1 if idx < 0 else 1
        i = abs(idx) - 1
        return [s * c[i] for c in cols] if trans else [s * v for v in cols[i]]
    t = series(2, 1)
    print("   time points", len(t), "first", t[0], "last", t[-1])
    for nm in ["Rv", "Rdep", "Rev", "Rfin", "R.R", "R2.R", "C.C", "C.v", "L.i", "y", "R.LossPower", "m.T"]:
        if nm in names:
            k, idx = info[names.index(nm)][0], info[names.index(nm)][1]
            v = series(k, idx)
            print("   %-12s data_%d idx %4d  first %-14.8g last %-14.8g  (%d values)" % (nm, k, idx, v[0], v[-1], len(v)))
EOF
$PY readmat.py "$B/base.mat" "$B/ov.mat" "$B/st.mat" "$B/vf.mat" "$W/run1/res.mat" "$W/thermal/ss_default.mat" "$W/thermal/ss_1e-6.mat" 2>&1 | cut -c1-900

hdr "done"
du -sh "$W"
