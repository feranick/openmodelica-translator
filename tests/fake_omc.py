#!/usr/bin/env python3
"""fake_omc.py — a stand-in for omc and the executables it builds, for tests without OpenModelica.
Version: 2026.11.19.7

    fake_omc.py --version            prints like omc 1.27.1
    fake_omc.py [-n=N] script.mos    the msl status script, or a build (modelica.build_mos): prints the 11 values
                                     omc printed on mochi, writes omct_model (a shell script running --sim),
                                     omct_model_init.xml
    fake_omc.py --sim <flags>        the executable: simulates the study (OMCT_FAKE_STUDY) with the reference implementation,
                                     writes a MAT file in OpenModelica's layout and the log of a mochi run
Environment: OMCT_FAKE_STUDY (study JSON), OMCT_FAKE_BUILD_FAIL=1, OMCT_FAKE_REFUSE=<name>, OMCT_FAKE_UNBALANCED=1,
OMCT_FAKE_SHIFT=<name> (the run records a different value for it). Override values that differ from the study's
are applied where the path is comp.param or comp.<initial value's variable> (_apply).
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
FIX = HERE / "fixtures"


def build(mos: Path):
    text = mos.read_text()
    if "getSourceFile" in text:
        v = os.environ.get("OMCT_FAKE_MSL", "4.0.0")      # what omc loads (it falls back to a newer MSL)
        print(f'true\n""\n"{v}"\n"/fake/libraries/Modelica {v}/package.mo"')
        return 0
    cwd = Path.cwd()
    print('true\n""\ntrue\n""\n"4.0.0"\ntrue\n""')
    eq = 10
    var = 11 if os.environ.get("OMCT_FAKE_UNBALANCED") else 10
    print(f'"Check of omct_model completed successfully.\nClass omct_model has {eq} equation(s) and {var} variable(s).\n'
          f'3 of these are trivial equation(s)."\n""')
    if os.environ.get("OMCT_FAKE_BUILD_FAIL"):
        print('{"", ""}\n"[%s/model.mo:5:3-5:60:writable] Error: Modified element Rx not found in class Resistor.\n"' % cwd)
        return 0
    exe = cwd / "omct_model"
    exe.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT}" exec "{sys.executable}" "{Path(__file__).resolve()}" --sim "$@"\n')
    exe.chmod(0o755)
    names = []
    refuse = os.environ.get("OMCT_FAKE_REFUSE", "")
    for line in (cwd / "model.mo").read_text().splitlines():
        pass
    (cwd / "omct_model_init.xml").write_text(
        '<?xml version = "1.0" encoding="UTF-8"?>\n<fmiModelDescription>\n<ModelVariables>\n' +
        "".join(f'<ScalarVariable name = "{n}" isValueChangeable = "{"false" if n == refuse else "true"}">'
                f'<Real start="0.0"/></ScalarVariable>\n' for n in names + ([refuse] if refuse else [])) +
        "</ModelVariables>\n</fmiModelDescription>\n")
    (cwd / "omct_model_info.json").write_text("{}")
    (cwd / "omct_model.c").write_text("/* removed by the cache */")
    print('{"%s/omct_model", "omct_model_init.xml"}\n""' % cwd)
    return 0


def _apply(study, ov):
    """The override values that differ from the study's own, set back into the study where a value's Modelica path
    is comp.param (a catalog parameter) or comp.var (an initial value's variable): enough for fits and sweeps of
    simple kinds (R.R, C.C, C.v0); values of composite kinds (blk.C = m cp) stay the study's."""
    from openmodelica_translator import catalog as C
    from openmodelica_translator.modelica import translate
    own = translate(study).overrides
    comps = study.get("components") or {}
    for key, v in ov.items():
        if v is None or own.get(key) == v or key.count(".") != 1:
            continue
        comp, _, x = key.partition(".")
        k = C.KINDS.get((comps.get(comp) or {}).get("kind"))
        if k is None:
            continue
        if x in k["params"]:
            comps[comp][x] = v
        else:
            init = next((i for i, d in k["inits"].items() if d["var"] == x), None)
            if init:
                comps[comp][init] = v
    return study


def sim(args):
    import numpy as np

    from openmodelica_translator import matfile
    from openmodelica_translator.network import build as net_build
    from openmodelica_translator.ref import Ref
    flags = {}
    for a in args:
        k, _, v = a.lstrip("-").partition("=")
        flags[k] = v
    inp, outp = Path(flags["inputPath"]), Path(flags["outputPath"])
    study = json.loads(Path(os.environ["OMCT_FAKE_STUDY"]).read_text())
    ov = {}
    for line in Path(flags["overrideFile"]).read_text().splitlines():
        k, _, v = line.partition("=")
        ov[k] = {"true": 1.0, "false": 0.0}.get(v, None) if v in ("true", "false") else float(v)
    t0, t1 = float(flags["startTime"]), float(flags["stopTime"])
    n = max(1, round((t1 - t0) / float(flags["stepSize"])))
    t = np.linspace(t0, t1, n + 1)
    study = _apply(study, ov)
    r = Ref(net_build(study))
    r.init_selection()
    vals = r.simulate(t)
    params = {k: v for k, v in ov.items() if k not in vals}
    ref_ = os.environ.get("OMCT_FAKE_REFUSE")
    if ref_ and ref_ not in ov:            # compiled in: the value comes from the model text, as in OpenModelica
        import re
        e, p = ref_.split(".", 1)
        m = re.search(rf"\b{e}\(.*?\b{p}=([-+0-9.eE]+)", (inp / "model.mo").read_text())
        params[ref_] = float(m.group(1))
    shift = os.environ.get("OMCT_FAKE_SHIFT")
    if shift in params:
        params[shift] *= 1.5
    matfile.write(outp / flags.get("r", "result.mat"), t, vals, params)
    log = (FIX / "run_ok.log").read_text()
    if os.environ.get("OMCT_FAKE_REFUSE") in ov:
        log = (f"LOG_STDOUT        | warning | It is not possible to override the following quantity: "
               f"{os.environ['OMCT_FAKE_REFUSE']}\n|                 | |       | It seems to be structural.\n") + log
    if "l" in flags:
        shutil.copy(FIX / "linearized_model.mo", outp / "linearized_model.mo")
        log += f"LOG_STDOUT        | info    | Linear model is created at {outp}/linearized_model.mo\n"
    sys.stdout.write(log)
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--version":
        print("OpenModelica 1.27.1~fake")
        sys.exit(0)
    if a and a[0] == "--sim":
        sys.exit(sim(a[1:]))
    mos = [x for x in a if x.endswith(".mos")]
    sys.exit(build(Path(mos[0])))
