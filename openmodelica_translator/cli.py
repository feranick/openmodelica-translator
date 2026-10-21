"""cli.py — the command line (milestone 1: format, validate, model). Every command prints JSON; '-' reads stdin.
Version: 2026.10.21.1

    omct format                          the study format
    omct validate studies.json           static checks of {name: study}
    omct model studies.json NAME         the generated Modelica, the omc script and the override file of one study
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def _load(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else open(path).read()
    return json.loads(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="omct", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("format")
    p = sub.add_parser("validate")
    p.add_argument("studies")
    p = sub.add_parser("model")
    p.add_argument("studies")
    p.add_argument("name")
    a = ap.parse_args(argv)
    if a.cmd == "format":
        from .format import study_format
        out = study_format()
    elif a.cmd == "validate":
        from .format import validate
        out = validate(_load(a.studies))
    else:
        from .format import validate_study
        from .modelica import build_mos, override_file, translate
        studies = _load(a.studies)
        if a.name not in studies:
            out = {"error": f"no study '{a.name}' (studies: {sorted(studies)})"}
        else:
            st = studies[a.name]
            v = validate_study(st)
            if not v["ok"]:
                out = {"error": "invalid study", **v}
            else:
                t = translate(st, a.name)
                an = st.get("analysis", {})
                out = {"model.mo": t.text, "build.mos": build_mos("model.mo", "4.0.0",
                                                                  an.get("stop_time", an.get("max_time", 1.0)),
                                                                  an.get("intervals", 500), an.get("tolerance", 1e-6),
                                                                  an.get("method", "dassl")),
                       "params.txt": override_file(t.overrides), "fixed": t.fixed,
                       "outputs": {k: {"terms": v_[0], "unit": v_[1]} for k, v_ in t.outputs.items()},
                       "warnings": v["warnings"]}
    json.dump(out, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    sys.exit(main())
