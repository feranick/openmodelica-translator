"""omc.py — the only module that runs omc: finding it, its version and MSL, and the build cache.
Version: 2026.10.25.3

find_omc()          {exe, version} or {error}            (OMCT_OMC, else omc on PATH)
msl_status()        {version, path} or {error}           (loadModel(Modelica, {"4.0.0"}, requireExactVersion=true))
build(tr, ...)      the compiled model of a translation (modelica.Translation), from the cache or built now:
                    {ok, hash, dir, cache_hit, seconds, check: {equations, variables, trivial}, changeable,
                    errors, warnings}

The cache: OMCT_CACHE (default OMCT_OUT/.cache)/<hash>/ with the executable, _init.xml, _info.json, *.bin,
.libs, .log, model.mo, build.mos, build.log and meta.json. The hash covers the model's structure (values
replaced by '?'), the omc and MSL versions and the build flags. A build happens in a temporary folder under
an flock on <hash>.lock and is renamed into place, so concurrent studies (and SLURM jobs) building the same
model wait for one build. Measured on mochi (OpenModelica 1.27.1): checkModel reports an unbalanced model as
"completed successfully", so the equation and variable counts are compared here; omc's errors look like
"[<file>:<line>:<col>-<line>:<col>:writable] Error: <message>", and the line names the declaration.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from . import modelica as M
from .proc import run as run_proc

EXE = M.MODEL
KEEP = {EXE, f"{EXE}_init.xml", f"{EXE}_info.json", f"{EXE}.libs", f"{EXE}.log", "model.mo", "build.mos",
        "build.log", "meta.json", "runs.json"}


def out_root() -> Path:
    return Path(os.environ.get("OMCT_OUT", "~/omct_runs")).expanduser()


def cache_root() -> Path:
    c = os.environ.get("OMCT_CACHE")
    return Path(c).expanduser() if c else out_root() / ".cache"


def msl_version() -> str:
    return os.environ.get("OMCT_MSL_VERSION", "4.0.0")


def compile_procs() -> int:
    try:
        return max(0, int(os.environ.get("OMCT_COMPILE_PROCS", "4")))
    except ValueError:
        return 4


@lru_cache(maxsize=1)
def find_omc() -> dict:
    exe = os.environ.get("OMCT_OMC") or shutil.which("omc")
    if not exe:
        return {"error": "omc not found (install OpenModelica, or set OMCT_OMC to the omc executable)"}
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{exe} --version failed: {exc}"}
    m = re.search(r"OpenModelica\s+v?([0-9][^\s]*)", r.stdout + r.stderr)
    if not m:
        return {"error": f"{exe} --version: unexpected output {r.stdout.strip()[:200]!r}"}
    return {"exe": exe, "version": m.group(1)}


# ---- omc's printed values ----------------------------------------------------------------------------
def parse_values(text: str) -> list:
    """The values omc prints for each statement of a script, in order: strings, true/false, numbers, {lists}."""
    out, i, n = [], 0, len(text)

    def value(i):
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return None, i
        c = text[i]
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append({"n": "\n", "t": "\t"}.get(text[j + 1], text[j + 1]))
                    j += 2
                    continue
                if text[j] == '"':
                    return "".join(buf), j + 1
                buf.append(text[j])
                j += 1
            return "".join(buf), n
        if c == "{":
            items, j = [], i + 1
            while True:
                while j < n and (text[j].isspace() or text[j] == ","):
                    j += 1
                if j >= n:
                    return items, n
                if text[j] == "}":
                    return items, j + 1
                v, j = value(j)
                items.append(v)
        j = i
        while j < n and not text[j].isspace() and text[j] not in ",}":
            j += 1
        tok = text[i:j]
        if tok in ("true", "false"):
            return tok == "true", j
        try:
            return float(tok), j
        except ValueError:
            return tok, j

    while True:
        v, i = value(i)
        if v is None and i >= n:
            break
        out.append(v)
    return out


ERR = re.compile(r"^\[(?P<file>[^\]]*?):(?P<l0>\d+):(?P<c0>\d+)-(?P<l1>\d+):(?P<c1>\d+):\w+\]\s*"
                 r"(?P<level>Error|Warning|Notification):\s*(?P<msg>.*)$")
CHECK = re.compile(r"has (\d+) equation\(s\) and (\d+) variable\(s\)\.\s*(\d+) of these are trivial", re.S)


def messages(err: str, text: str = "") -> list[dict]:
    """getErrorString() -> [{level, message, line, element}]: the line of model.mo names the declaration."""
    lines = text.splitlines()
    out = []
    cur = None
    for raw in (err or "").splitlines():
        m = ERR.match(raw.strip())
        if m:
            ln = int(m.group("l0"))
            elem = None
            if 0 < ln <= len(lines):
                dm = re.match(r"^\s+[\w.]+\s+(\w+)[\s(;\"]", lines[ln - 1])
                elem = dm.group(1) if dm else None
            cur = {"level": m.group("level").lower(), "message": m.group("msg").strip(), "line": ln, "element": elem}
            out.append(cur)
        elif raw.strip().startswith(("Error:", "Warning:", "Notification:")):
            lvl, _, msg = raw.strip().partition(":")
            cur = {"level": lvl.lower(), "message": msg.strip(), "line": None, "element": None}
            out.append(cur)
        elif raw.strip() and cur is not None:
            cur["message"] += " " + raw.strip()
    return out


_MSL: dict = {}


def msl_status() -> dict:
    """MSL as omc loads it; remembered only when it loads (a failure, e.g. a slow first load, is checked again)."""
    if "ok" not in _MSL:
        r = _msl_status()
        if "error" in r:
            return r
        _MSL["ok"] = r
    return _MSL["ok"]


msl_status.cache_clear = _MSL.clear      # as with lru_cache (tests)


def _msl_status() -> dict:
    o = find_omc()
    if "error" in o:
        return {"error": o["error"]}
    import tempfile
    with tempfile.TemporaryDirectory(prefix="omct_msl_") as d:
        mos = Path(d) / "msl.mos"
        mos.write_text(f'loadModel(Modelica, {{"{msl_version()}"}}, requireExactVersion=true); getErrorString();\n'
                       'getVersion(Modelica);\ngetSourceFile(Modelica);\n')
        r = run_proc([o["exe"], str(mos)], d, 40, Path(d) / "msl.log")     # inside a tool call (study_format)
        vals = parse_values((Path(d) / "msl.log").read_text(errors="replace"))
    if r["exit"] != 0 or len(vals) < 4 or vals[0] is not True:
        return {"error": f"MSL {msl_version()} not loadable by omc: {vals[1] if len(vals) > 1 else vals!r}; install it with "
                         f"OpenModelica's package manager (BACKENDS.md)"}
    path = str(Path(vals[3]).parent) if vals[3] else None
    if str(vals[2]) != msl_version():
        # omc falls back to a newer compatible MSL when the requested one is missing: builds refuse it
        return {"error": f"omc loads MSL {vals[2]} ({path}) when asked for {msl_version()}: MSL {msl_version()} is not "
                         f"installed; install it with OpenModelica's package manager (BACKENDS.md)",
                "version": vals[2], "path": path}
    return {"version": vals[2], "path": path}


# ---- _init.xml ------------------------------------------------------------------------------------------
def read_init(path: Path) -> dict:
    """{name: {changeable: bool|None, start: str|None, variability, causality}} from <model>_init.xml."""
    out = {}
    root = ET.parse(path).getroot()
    for sv in root.iter("ScalarVariable"):
        a = sv.attrib
        start = None
        for child in sv:
            if "start" in child.attrib:
                start = child.attrib["start"]
        ch = a.get("isValueChangeable")
        out[a.get("name")] = {"changeable": None if ch is None else ch == "true", "start": start,
                              "variability": a.get("variability"), "causality": a.get("causality")}
    return out


# ---- the build ------------------------------------------------------------------------------------------
def structure_key(tr) -> str:
    o = find_omc()
    return M.structure_hash(tr.structure, o.get("version", "?"), msl_version())


def build(tr, *, timeout: float = 600, cancel: Path | None = None, procs: int | None = None,
          variant: dict | None = None) -> dict:
    """variant: values the compiled model cannot take at run time (isValueChangeable false: OpenModelica made the
    variable an alias, or evaluated it); they become part of the key, so each set of them is its own build."""
    o = find_omc()
    if "error" in o:
        return {"ok": False, "errors": [o["error"]], "warnings": []}
    key = structure_key(tr)
    if variant:
        import hashlib
        key = key[:12] + hashlib.sha256((key + json.dumps(sorted(variant.items()))).encode()).hexdigest()[:8]
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    final = root / key
    t0 = time.perf_counter()
    if (final / "meta.json").is_file() and (final / EXE).is_file():
        meta = json.loads((final / "meta.json").read_text())
        return {**meta, "ok": True, "dir": str(final), "cache_hit": True, "seconds": round(time.perf_counter() - t0, 3),
                "errors": [], "warnings": meta.get("warnings", [])}
    with open(root / f"{key}.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if (final / "meta.json").is_file() and (final / EXE).is_file():       # built while we waited
            meta = json.loads((final / "meta.json").read_text())
            return {**meta, "ok": True, "dir": str(final), "cache_hit": True, "waited_s": round(time.perf_counter() - t0, 3),
                    "seconds": round(time.perf_counter() - t0, 3), "errors": [], "warnings": meta.get("warnings", [])}
        tmp = root / f".{key}.{os.getpid()}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        (tmp / "model.mo").write_text(tr.text)
        (tmp / "build.mos").write_text(M.build_mos("model.mo", msl_version(), model=getattr(tr, "model", M.MODEL)))
        n = compile_procs() if procs is None else procs
        r = run_proc([o["exe"], f"-n={n}", "build.mos"], tmp, timeout, tmp / "build.log", cancel)
        res = interpret_build((tmp / "build.log").read_text(errors="replace"), tr.text, r)
        res["seconds"] = round(time.perf_counter() - t0, 3)
        res["omc"], res["msl"], res["hash"] = o["version"], msl_version(), key
        if not res["ok"] or not (tmp / EXE).is_file():
            if res["ok"]:
                res["ok"] = False
                res["errors"].append("omc reported success but wrote no executable (see build.log)")
            keep = root / ".failed"
            keep.mkdir(exist_ok=True)
            dest = keep / f"{key}.{time.strftime('%Y%m%d_%H%M%S')}"
            shutil.rmtree(dest, ignore_errors=True)
            for f in ("model.mo", "build.mos", "build.log"):
                if (tmp / f).is_file():
                    dest.mkdir(exist_ok=True)
                    shutil.copy2(tmp / f, dest / f)
            shutil.rmtree(tmp, ignore_errors=True)
            res["files"] = {"failed_build": str(dest)}
            return res
        try:
            init = read_init(tmp / f"{EXE}_init.xml")
            res["changeable"] = {k: v["changeable"] for k, v in init.items() if v["changeable"] is not None}
            res["init_names"] = len(init)
        except (ET.ParseError, OSError) as exc:
            res["warnings"].append(f"could not read {EXE}_init.xml: {exc}")
        for f in tmp.iterdir():
            if f.name not in KEEP and not (f.name.startswith(EXE) and f.suffix == ".bin"):
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink()
        meta = {k: res[k] for k in ("hash", "omc", "msl", "check", "seconds", "warnings") if k in res}
        meta["built"] = time.time()
        meta["changeable"] = res.get("changeable", {})
        meta["compiled_values"] = dict(getattr(tr, "overrides", {}) or {})
        meta["variant"] = variant or {}
        res["compiled_values"] = meta["compiled_values"]
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1))
        os.replace(tmp, final) if not final.exists() else shutil.rmtree(tmp, ignore_errors=True)
        res.update({"dir": str(final), "cache_hit": False})
        return res


def interpret_build(log: str, text: str, proc: dict) -> dict:
    """The build script's printed values (modelica.build_mos: 11 of them) -> {ok, check, errors, warnings}."""
    errs, warns = [], []
    if proc.get("timed_out"):
        return {"ok": False, "errors": ["the build timed out (OMCT_TIMEOUT, or run.timeout_s)"], "warnings": []}
    if proc.get("cancelled"):
        return {"ok": False, "errors": ["cancelled"], "warnings": []}
    vals = parse_values(log)
    if len(vals) < 11:
        return {"ok": False, "errors": [f"omc printed {len(vals)} values instead of 11 (exit {proc.get('exit')}); "
                                        f"see build.log: {log.strip()[-600:]}"], "warnings": []}
    (_opt, e0, loaded, e1, version, loaded_file, e2, check, e3, built, e4) = vals[:11]
    for e in (e0, e1, e2, e3, e4):
        for m in messages(e if isinstance(e, str) else "", text):
            (errs if m["level"] == "error" else warns).append(
                (f"{m['element']}: " if m["element"] else "") + m["message"])
    if loaded is not True:
        errs.insert(0, f"omc could not load MSL {msl_version()} (install it with OpenModelica's package manager)")
    elif str(version) != msl_version():
        errs.insert(0, f"omc loaded MSL {version}, not {msl_version()}")
    if loaded_file is not True:
        errs.insert(0, "omc could not read the generated model (a translator bug: report it with model.mo)")
    chk = {}
    m = CHECK.search(check or "")
    if m:
        chk = {"equations": int(m.group(1)), "variables": int(m.group(2)), "trivial": int(m.group(3))}
        if chk["equations"] != chk["variables"]:
            errs.append(f"the model is not balanced: {chk['equations']} equations for {chk['variables']} variables")
    elif not errs:
        errs.append(f"checkModel failed: {str(check)[:300]}")
    if not (isinstance(built, list) and built and built[0]):
        if not errs:
            errs.append("buildModel failed (see build.log)")
    dedup = list(dict.fromkeys(errs))
    return {"ok": not dedup, "check": chk, "errors": dedup, "warnings": list(dict.fromkeys(warns))}


def cache_status() -> dict:
    root = cache_root()
    if not root.is_dir():
        return {"path": str(root), "models": 0, "mb": 0.0}
    n, size = 0, 0
    for d in root.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            n += 1
            size += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    return {"path": str(root), "models": n, "mb": round(size / 1e6, 1)}
