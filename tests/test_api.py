"""test_api.py — the API (worker pool, request folders, status from files, cancel, jobs, benchmarks, pictures)
and the MCP server's tool list. Runs with the reference solver: no OpenModelica needed.
Version: 2026.10.26.1
"""
import asyncio
import json
import time

import pytest
from studies import MSD, RC, RLC, TWO_MASS, study

from openmodelica_translator import api


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    monkeypatch.setenv("OMCT_SOLVER", "ref")
    return tmp_path


def test_run_in_the_pool_then_status(out):
    r = api.run({"rc": study(RC), "bad": {"components": {}}}, None, wait=40)
    assert r["studies"]["bad"]["why"] == "invalid study"
    if r["pending"]:
        r = api.status(r["request"], 40)
    rc = r["studies"]["rc"]
    assert r["done"] and rc["ok"] and rc["reliable"], {k: rc.get(k) for k in ("ok", "reliable", "why", "unreliable_because",
                                                                          "warnings", "stage", "state")}
    # another reader sees the same results from the files
    again = api._collect(api.Path(r["request"]))
    assert again["studies"]["rc"]["outputs"]["v"]["final"] == r["studies"]["rc"]["outputs"]["v"]["final"]
    js = api.jobs(5)
    assert js["requests"][0]["request"] == r["request"] and js["requests"][0]["studies"]["rc"]["state"] == "done"


def test_inline_run_and_images(out):
    st = study(RLC, images=True)
    r = api.run({"rlc": st}, "ref")
    s = r["studies"]["rlc"]
    assert s["ok"] and s["images"]["outputs"]["path"].endswith("outputs.png")
    assert api.image_path(s["images"]["outputs"]["path"]).is_file()
    folder = api.Path(r["request"]) / "rlc"
    re = api.render_images(str(folder), [{"name": "early", "outputs": ["v_C"], "window": [0, 0.01]}])
    assert re["images"]["early"]["path"].endswith("early.png")
    assert "error" in api.render_images(str(folder), [{"name": "x", "outputs": ["nope"]}])
    lin = api.run({"tm": study(TWO_MASS, images=True, analysis={"kind": "linearize", "inputs": {"tau": "T.tau"},
                                                                "bode": {"f_min": 1, "f_max": 1000, "points": 20}})}, "ref")
    assert lin["studies"]["tm"]["images"]["bode"]["path"].endswith("bode.png")
    with pytest.raises(ValueError):
        api.image_path("/etc/passwd")
    assert "error" in api.read_file("/etc/passwd")


def test_cancel_before_start(out):
    folder = api.start({"m": study(MSD)}, "ref", inline=False)
    api.cancel(str(folder))
    r = api.status(str(folder), 40)
    assert r["done"]
    s = r["studies"]["m"]
    assert s.get("state") == "cancelled" or s.get("ok")        # it may have finished before the flag


def test_bench_in_pool_and_status(out):
    b = api.bench(["rc_step", "inertia_torque_step"], "ref", wait_s=0)
    t_end = time.time() + 60
    while not b["done"] and time.time() < t_end:
        b = api.bench_status(b["bench"], 5)
    assert b["done"] and sorted(b["passed"]) == ["inertia_torque_step", "rc_step"]
    assert json.loads((api.Path(b["bench"]) / "bench.json").read_text())["passed"]
    b = api.bench(["nope"], "ref")
    assert list(b["skipped"]) == ["nope"]


def test_mcp_tools():
    pytest.importorskip("mcp")
    from openmodelica_translator import mcp_server as m
    names = [t.name for t in asyncio.run(m.server.list_tools())]
    assert names == ["study_format", "validate_studies", "run_studies", "study_status", "get_model", "run_benchmarks",
                     "bench_status", "jobs", "cancel", "get_image", "render_images", "get_file"]
    assert len(m.INSTRUCTIONS.splitlines()) <= 30


def test_sweep_ref_and_expect_on_table(out):
    st = study(RC, sweep={"params": {"R.R": [500, 2000]}, "report": ["outputs.v.final"]}, images=True,
               expect=[{"quantity": "sweep.table.1.values.outputs.v.final", "value": 1 - 2.718281828 ** -2.5, "tol": 1e-4}])
    r = api.run({"s": st}, "ref")["studies"]["s"]
    assert r["ok"] and r["reliable"] and r["passed"], r.get("expect")
    assert r["images"]["sweep"]["path"].endswith("sweep.png")
    assert r["sweep"]["columns"] == ["outputs.v.final"]


def test_sweep_processes_outside_the_pool(out):
    st = study(RC, sweep={"params": {"R.R": [500, 1000, 2000]}}, run={"procs": 2})
    r = api.run({"s": st}, "ref")["studies"]["s"]          # inline (the CLI, a SLURM job): not daemonic
    assert r["ok"] and r["sweep"]["parallel"] == "processes" and r["sweep"]["reliable_points"] == 3
    r = api.run({"s": st}, "ref", wait=40)                  # in the server's pool: threads
    if r["pending"]:
        r = api.status(r["request"], 40)
    assert r["studies"]["s"]["sweep"]["parallel"] == "threads"


def test_escape_hatch_sweep_applies_values_by_path():
    """A sweep over a custom model: the points' values are Modelica paths, applied with the override file."""
    from openmodelica_translator import modelica as M
    from openmodelica_translator.sweep import point_study
    esc = {"modelica": {"model": "M", "text": "model M\n  parameter Real R = 1;\nend M;\n"}, "outputs": {"R": "R"},
           "analysis": {"kind": "transient", "stop_time": 1}, "sweep": {"params": {"R": [1, 2]}}}
    assert api.validate({"e": esc})["studies"]["e"]["ok"]
    st = point_study(esc, {"R": 2})
    assert M.escape_translation(st).overrides == {"R": 2.0}
    assert M.escape_translation({k: v for k, v in esc.items() if k != "sweep"}).overrides == {}
    bad = dict(esc, sweep={"params": {"R": ["x"]}})
    assert not api.validate({"e": bad})["studies"]["e"]["ok"]


def test_tools_run_off_the_event_loop_and_are_logged(tmp_path, monkeypatch):
    """Each tool runs in a worker thread (a long wait must not block pings and other calls) and is logged."""
    import inspect
    from openmodelica_translator import mcp_server as S
    assert inspect.iscoroutinefunction(S.run_studies) and inspect.iscoroutinefunction(S.jobs)
    assert list(inspect.signature(S.run_studies).parameters) == ["studies", "solver", "wait_s"]
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    p = S._log_path()                                    # (not _start_log: no signal handlers in the test process)
    with open(p, "a") as f:
        monkeypatch.setitem(S._LOG, "file", f)
        out = asyncio.run(S.jobs(limit=1))
    assert "requests" in out
    log = p.read_text()
    assert "call jobs" in log and "done jobs" in log


def _release(tmp_path, name, version):
    """A shared install: <prefix>/releases/<name>/{RELEASE.json, omct/openmodelica_translator/__init__.py}."""
    pkg = tmp_path / "releases" / name / "omct" / "openmodelica_translator"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (tmp_path / "releases" / name / "RELEASE.json").write_text("{}")
    return pkg


def test_stale_reason_shared_and_personal(tmp_path):
    from openmodelica_translator.mcp_server import stale_reason
    old = _release(tmp_path, "r1", "1.0")
    _release(tmp_path, "r2", "2.0")
    (tmp_path / "current").symlink_to(tmp_path / "releases" / "r1")
    assert stale_reason(old, "1.0") == (None, True)
    (tmp_path / "current").unlink()
    (tmp_path / "current").symlink_to(tmp_path / "releases" / "r2")
    why, shared = stale_reason(old, "1.0")
    assert shared and "r2 is installed" in why
    pers = tmp_path / "checkout" / "openmodelica_translator"
    pers.mkdir(parents=True)
    (pers / "__init__.py").write_text('__version__ = "3.0"\n')
    assert stale_reason(pers, "3.0") == (None, False)
    why, shared = stale_reason(pers, "2.9")
    assert not shared and "3.0 is installed" in why


def test_stale_server_refuses_when_idle_and_warns_when_not(tmp_path, monkeypatch):
    """A stale server that may exit refuses a call with STALE_MARK (the router repeats it on a new server) and
    exits; one that may not (personal install) answers with a server_stale warning."""
    from openmodelica_translator import mcp_server as S
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    exits = []
    monkeypatch.setattr(S, "_stale", lambda: S._STATE["why"])
    monkeypatch.setattr(S, "_exit_soon", lambda why, delay: exits.append(why) or S._STATE.update(exiting=True))
    monkeypatch.setitem(S._STATE, "why", "release r2 is installed; this server runs r1 (1.0)")
    monkeypatch.setitem(S._STATE, "exiting", False)
    monkeypatch.setitem(S._STATE, "shared", False)
    monkeypatch.setitem(S._STATE, "serving", True)
    monkeypatch.delenv("OMCT_EXIT_WHEN_STALE", raising=False)
    out = asyncio.run(S.jobs(limit=1))
    assert "requests" in out and "restart the MCP server" in out["server_stale"] and not exits
    monkeypatch.setitem(S._STATE, "shared", True)
    S._STATE["inflight"] += 1                        # another call running: answer (old code), warn, no exit
    try:
        out = asyncio.run(S.jobs(limit=1))
    finally:
        S._STATE["inflight"] -= 1
    assert "restarts when idle" in out["server_stale"] and not exits
    with pytest.raises(S.ToolError, match=S.STALE_MARK):
        asyncio.run(S.jobs(limit=1))
    assert exits and S._STATE["inflight"] == 0
    monkeypatch.setenv("OMCT_EXIT_WHEN_STALE", "0")
    monkeypatch.setitem(S._STATE, "exiting", False)
    assert "requests" in asyncio.run(S.jobs(limit=1))


def test_tool_crash_message_reaches_the_caller(monkeypatch):
    """A crash inside a tool comes back as a ToolError with its text (SDK 2.x hides other exceptions' text)."""
    from openmodelica_translator import mcp_server as S

    def boom(*a, **kw):
        raise KeyError("components")
    monkeypatch.setattr(S.api, "jobs", boom)
    with pytest.raises(S.ToolError, match="KeyError: 'components'"):
        asyncio.run(S.jobs(limit=1))


def test_stale_install_never_exits_outside_a_server(monkeypatch):
    """Tools called in-process (tests run by llmt deploy --test before current is switched) are never refused and
    never exit the process, however stale the install looks."""
    from openmodelica_translator import mcp_server as S
    exits = []
    monkeypatch.setattr(S, "_stale", lambda: S._STATE["why"])
    monkeypatch.setattr(S, "_exit_soon", lambda why, delay: exits.append(why))
    monkeypatch.setitem(S._STATE, "why", "release r2 is installed; this server runs r1 (1.0)")
    monkeypatch.setitem(S._STATE, "shared", True)
    monkeypatch.setitem(S._STATE, "serving", False)
    monkeypatch.setitem(S._STATE, "exiting", False)
    monkeypatch.setenv("OMCT_EXIT_WHEN_STALE", "1")
    out = asyncio.run(S.jobs(limit=1))
    assert "requests" in out and not exits


ESC_TEXT = """
model Box
  parameter Real C = 1000 "heat capacity";
  parameter Real G = 2.0;
  parameter Real tau = C / G "time constant: an expression";
  Wall w(k = 0.5);
  Wall w2(k = 2 * G);
  Real T(start = 300, fixed = true);
equation
  C * der(T) = G * (290 - T);
end Box;
model Wall
  parameter Real k = 1.0;
  parameter Real r = 1 / k;
end Wall;
"""


def test_escape_expression_defaults_rejected_in_validation():
    """A parameter whose default is an expression cannot be set at run time: validation says so (before: only the
    run, with 'values were not applied')."""
    from openmodelica_translator.modelica import bound_parameters
    b = bound_parameters(ESC_TEXT, "Box")
    assert set(b) == {"tau", "w.r", "w2.k", "w2.r"} and b["tau"] == "C / G"
    base = {"modelica": {"text": ESC_TEXT, "model": "Box"}, "outputs": {"T": "T"},
            "analysis": {"kind": "transient", "stop_time": 10}}
    ok = dict(base, sweep={"params": {"C": [500, 1000], "w.k": [1, 2]}})
    assert api.validate({"s": ok})["studies"]["s"]["ok"], api.validate({"s": ok})
    bad = dict(base, sweep={"params": {"tau": [1, 2]}})
    r = api.validate({"s": bad})["studies"]["s"]
    assert not r["ok"] and any("tau" in e and "expression" in e for e in r["errors"])
    bad = dict(base, modelica=dict(base["modelica"], values={"w2.k": 3.0}))
    r = api.validate({"s": bad})["studies"]["s"]
    assert not r["ok"] and any("w2.k" in e for e in r["errors"])


def test_escape_unchangeable_value_fails_right_after_the_build(tmp_path, monkeypatch):
    """A custom model's value that the build marks not changeable fails at once, with what to do (before: compiled
    values were taken as the study's values, the run dropped it, and only the read-back noticed)."""
    from openmodelica_translator import omc, runner
    monkeypatch.setattr(omc, "build", lambda tr, **kw: {"ok": True, "hash": "h", "dir": str(tmp_path),
                                                       "changeable": {"x": False}, "compiled_values": {}})
    st = {"modelica": {"text": "model M parameter Real x = 1; Real y; equation y = x; end M;", "model": "M",
                       "values": {"x": 2.0}}, "outputs": {"y": "y"}, "analysis": {"kind": "transient", "stop_time": 1}}
    r = runner.run_study("m", st, tmp_path / "m", "omc", 30)
    assert not r["ok"] and r["why"] == "values the model cannot take at run time" and "plain number" in r["errors"][0]
