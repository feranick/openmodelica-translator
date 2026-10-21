"""cli.py — the command line. Every command prints JSON; '-' reads the studies from stdin.
Version: 2026.10.21.2

    omct status                              versions; omc and MSL found or not; the cache; the executor
    omct format                              the study format
    omct validate studies.json               static checks of {name: study}
    omct model studies.json NAME             the generated Modelica, the omc script and the override file
    omct run studies.json [NAMES...] [--solver auto|omc|ref]
                                             runs the studies now (milestone 2: one after the other)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def _load(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else open(path).read()
    return json.loads(text)


def _brief(res: dict) -> dict:
    """A run's result without the long series (the full one is in the request folder)."""
    out = json.loads(json.dumps(res, default=str))
    for st in out.get("studies", {}).values():
        for o in (st.get("outputs") or {}).values():
            if "series" in o:
                o["series"] = f"{len(o['series']['t'])} points (in the result file)"
        lin = st.get("linearization") or {}
        for b in (lin.get("bode") or {}).values():
            for k in ("f_Hz", "gain_dB", "phase_deg"):
                if isinstance(b.get(k), list) and len(b[k]) > 6:
                    b[k] = b[k][:3] + ["..."] + b[k][-2:]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="omct", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("format")
    p = sub.add_parser("validate")
    p.add_argument("studies")
    p = sub.add_parser("model")
    p.add_argument("studies")
    p.add_argument("name")
    p = sub.add_parser("run")
    p.add_argument("studies")
    p.add_argument("names", nargs="*")
    p.add_argument("--solver", default=None)
    p.add_argument("--full", action="store_true", help="print the series too")
    a = ap.parse_args(argv)
    from . import api
    if a.cmd == "status":
        out = api.tool_status()
    elif a.cmd == "format":
        out = api.study_format()
    elif a.cmd == "validate":
        out = api.validate(_load(a.studies))
    elif a.cmd == "model":
        studies = _load(a.studies)
        out = api.model(studies[a.name], a.name) if a.name in studies else \
            {"error": f"no study '{a.name}' (studies: {sorted(studies)})"}
    else:
        studies = _load(a.studies)
        if a.names:
            missing = [n for n in a.names if n not in studies]
            if missing:
                out = {"error": f"no studies {missing} (studies: {sorted(studies)})"}
                json.dump(out, sys.stdout, indent=1)
                sys.stdout.write("\n")
                return 1
            studies = {n: studies[n] for n in a.names}
        res = api.run(studies, a.solver)
        out = res if a.full else _brief(res)
    json.dump(out, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    sys.exit(main())
