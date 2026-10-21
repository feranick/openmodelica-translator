"""check_catalog_msl.py — checks catalog.py against the MSL 4.0.0 sources (a checkout of
github.com/modelica/ModelicaStandardLibrary at tag v4.0.0): class paths, ports, parameters, variables, inits.
Version: 2026.10.21.14
Usage: python tools/check_catalog_msl.py /path/to/ModelicaStandardLibrary
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openmodelica_translator import catalog as C  # noqa: E402

ROOT = Path("/tmp/msl")


def class_text(path: str) -> str | None:
    """Text of a class given its full path, from Name.mo files or package.mo/Package.mo definitions."""
    parts = path.split(".")
    base = ROOT
    for i, p in enumerate(parts):
        f = base / (p + ".mo")
        d = base / p
        if f.is_file():
            text = f.read_text(errors="replace")
            rest = parts[i + 1:]
            for r in rest:
                m = re.search(rf"^\s*(?:partial\s+)?(?:model|block|class|connector|record|package)\s+{r}\b.*?^\s*end\s+{r};",
                              text, re.S | re.M)
                if not m:
                    return None
                text = m.group(0)
            return text
        if d.is_dir():
            base = d
            continue
        return None
    return (base / "package.mo").read_text() if (base / "package.mo").is_file() else None


def resolve(name: str, context: str) -> str | None:
    if name.startswith("Modelica."):
        return name
    ctx = context.split(".")
    for k in range(len(ctx), 0, -1):
        cand = ".".join(ctx[:k] + [name])
        if class_text(cand) is not None:
            return cand
    return None


def full_text(path: str, depth=0) -> str:
    t = class_text(path) or ""
    if depth > 8:
        return t
    for m in re.finditer(r"^\s*extends\s+([\w.]+)", t, re.M):
        base = resolve(m.group(1), path.rsplit(".", 1)[0])
        if base:
            t += "\n" + full_text(base, depth + 1)
    return t


def check(root) -> list[str]:
    """Problems found (empty: every class, port, parameter, variable and initial value exists in MSL)."""
    global ROOT
    ROOT = Path(root)
    out = []
    for k, v in sorted(C.KINDS.items()):
        if v.get("parts") is not None:
            continue
        t = full_text(v["msl"])
        if not t:
            out.append(f"MISSING CLASS {k}: {v['msl']}")
            continue
        names = {C.PORT_PATHS.get((k, p), p).split(".")[0].split("[")[0] for p in v["ports"]}
        for p, spec in v["params"].items():
            if spec.get("msl") is not False:
                names.add((spec.get("msl") or p).split(".")[0])
        names |= set(v["mods"]) | set(v["text"])
        names |= {C.var_path(k, x).split(".")[0] for x in v["vars"]}
        names |= {i["var"] for i in v["inits"].values()}
        names |= {s_.split(".")[0] for s_ in v.get("block_states", {})}
        names |= set(v.get("cond_ports", {}).values())
        if "medium" in v["params"]:
            names.add("medium")
        for n in sorted(names):
            if not re.search(r"\b" + re.escape(n) + r"\b\s*(\(|=|;|\[|\"|$|annotation|if\b)", t, re.M):
                out.append(f"{k}: '{n}' not declared in {v['msl']} or its bases")
    return out


if __name__ == "__main__":
    problems = check(sys.argv[1] if len(sys.argv) > 1 else "/tmp/msl")
    print("\n".join(problems))
    print("problems:", len(problems))
