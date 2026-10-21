"""test_api.py — the API (worker pool, request folders, status from files, cancel, jobs, benchmarks, pictures)
and the MCP server's tool list. Runs with the reference solver: no OpenModelica needed.
Version: 2026.10.21.5
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
    assert r["done"] and r["studies"]["rc"]["ok"] and r["studies"]["rc"]["reliable"]
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
    assert b["skipped"] == ["nope"]


def test_mcp_tools():
    pytest.importorskip("mcp")
    from openmodelica_translator import mcp_server as m
    names = [t.name for t in asyncio.run(m.server.list_tools())]
    assert names == ["study_format", "validate_studies", "run_studies", "study_status", "get_model", "run_benchmarks",
                     "bench_status", "jobs", "cancel", "get_image", "render_images", "get_file"]
    assert len(m.INSTRUCTIONS.splitlines()) <= 30
