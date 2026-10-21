"""test_slurm.py — SLURM execution (llm-translators docs/EXECUTION.md) against a fake SLURM (tests/fake_slurm.py),
with the reference solver (no OpenModelica needed).
Version: 2026.10.21.7
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from studies import MSD, RC, study

from openmodelica_translator import api, executor

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def slurm(tmp_path, monkeypatch):
    bindir, state = tmp_path / "bin", tmp_path / "slurm"
    bindir.mkdir()
    state.mkdir()
    for cmd in ("sbatch", "squeue", "scancel", "sacct", "sinfo"):
        w = bindir / cmd
        w.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{REPO / "tests" / "fake_slurm.py"}" {cmd} "$@"\n')
        w.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SLURM_DIR", str(state))
    monkeypatch.setenv("OMCT_EXECUTOR", "slurm")
    monkeypatch.setenv("OMCT_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("OMCT_TIMEOUT", "600")
    monkeypatch.setenv("OMCT_SOLVER", "ref")
    monkeypatch.setenv("PYTHONPATH", f"{REPO}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")
    for k in ("FAKE_PENDING", "FAKE_SACCT_FAIL", "FAKE_SBATCH_FAIL", "FAKE_SBATCH_DELAY", "SLURM_JOB_ID",
              "SLURM_CPUS_PER_TASK", "OMCT_EXECUTION", "OMCT_PROCS_REQUESTED"):
        monkeypatch.delenv(k, raising=False)
    return state


def _job(state: Path, i) -> dict:
    return json.loads((state / f"{i}.json").read_text())


def _set(state: Path, i, **kw):
    r = _job(state, i)
    r.update(kw)
    (state / f"{i}.json").write_text(json.dumps(r))


def _cli(*args, timeout=60):
    return subprocess.run([sys.executable, "-m", "openmodelica_translator.cli", *args], capture_output=True, text=True,
                          timeout=timeout, env=os.environ.copy(), cwd=str(REPO))


def _wait_done(req, n=60):
    for _ in range(n):
        out = json.loads(_cli("status", str(req)).stdout)
        if out["done"]:
            return out
        time.sleep(1)
    return out


def test_submit_then_collected_by_a_new_process(slurm):
    st = study(RC, run={"queue": True})
    r = api.run({"rc": st}, "ref", wait=0)
    req = Path(r["request"])
    assert r["pending"] == ["rc"] and r["progress"]["rc"]["state"] in ("queued", "running")
    assert "SLURM jobs" in r["next"] and "1001" in r["next"]
    job = json.loads((req / "rc" / "job.json").read_text())
    assert job["executor"] == "slurm" and job["job_id"] == "1001" and job["time_min"] == 20 and job["procs"] == 1
    assert job["mem"] == "1280M" and "--mem=1280M" in job["command"]
    sh = (req / "rc" / "job.sh").read_text()
    assert f"#SBATCH --job-name=omct:{req.name}:rc" in sh and "#SBATCH --ntasks=1" in sh
    assert "#SBATCH --cpus-per-task=1" in sh and "#SBATCH --export=ALL" in sh and f"run-study {req} rc" in sh
    out = _wait_done(req)
    s = out["studies"]["rc"]
    assert out["done"] and s["ok"] and s["reliable"], s
    assert s["execution"]["executor"] == "slurm" and s["execution"]["slurm_job"] == "1001"
    js = api.jobs(5)
    assert js["requests"][0]["studies"]["rc"]["job_id"] == "1001" and "queue" in js and js["machine"]["cpus"] == 8


def test_routing(slurm, monkeypatch):
    assert not executor.use_slurm(study(RC), "ref")                       # 0.05 min < 1 min
    assert executor.use_slurm(study(RC, run={"queue": True}), "ref")
    monkeypatch.setenv("OMCT_SLURM_MIN_MINUTES", "0")
    assert executor.use_slurm(study(RC), "ref")
    assert not executor.use_slurm(study(RC, run={"queue": False}), "ref")
    monkeypatch.setenv("OMCT_EXECUTOR", "local")
    assert not executor.use_slurm(study(RC, run={"queue": True}), "ref")


def test_sizing_sweep_cpus_mem_time(slurm, monkeypatch):
    sw = study(RC, sweep={"params": {"R.R": [500, 1000, 2000, 4000, 8000, 16000]}}, run={"procs": 3, "queue": True})
    assert executor.cpus_for_job(sw, "ref") == 3
    assert executor.cpus_for_job(study(RC, run={"procs": 8}), "ref") == 1           # one point uses one CPU
    assert executor.cpus_for_job(study(RC), "omc") == 4                             # a build to do: compile CPUs
    assert executor.mem_for_job(sw, 3) == "1792M"
    assert executor.mem_for_job(study(RC, run={"mem": "3G"}), 1) == "3G"
    monkeypatch.setenv("OMCT_SLURM_TIME", "estimate")
    assert executor.time_limit_min(sw, 0.1) == 20 and executor.time_limit_min(sw, 30) == 20  # min(100, 600/60 + 10)
    monkeypatch.setenv("OMCT_TIMEOUT", "36000")
    assert executor.time_limit_min(sw, 30) == 100
    r = api.run({"sw": sw}, "ref", wait=0)
    req = Path(r["request"])
    assert "#SBATCH --cpus-per-task=3" in (req / "sw" / "job.sh").read_text()
    out = _wait_done(req)
    s = out["studies"]["sw"]
    assert s["ok"] and s["sweep"]["points"] == 6 and s["sweep"]["procs"] == 3, s
    assert s["execution"]["procs_requested"] == 3 and s["execution"]["procs_used"] == 3


def test_queued_then_cancelled(slurm, monkeypatch):
    monkeypatch.setenv("FAKE_PENDING", "1")
    r = api.run({"m": study(MSD, run={"queue": True})}, "ref", wait=0)
    req = r["request"]
    p = api.status(req)["progress"]["m"]
    assert p["state"] == "queued" and p["reason"] == "Resources" and p["ahead"] == 0
    c = api.cancel(req)
    assert c["cancelled_jobs"] == ["1001"]
    s = api.status(req)["studies"]["m"]
    assert s["state"] == "cancelled"


@pytest.mark.parametrize("final, state", [("TIMEOUT", "timed_out"), ("OUT_OF_MEMORY", "out_of_memory"),
                                          ("FAILED", "failed"), ("CANCELLED by 1000", "cancelled")])
def test_final_states_from_sacct(slurm, monkeypatch, final, state):
    monkeypatch.setenv("FAKE_PENDING", "1")
    r = api.run({"rc": study(RC, run={"queue": True})}, "ref", wait=0)
    _set(slurm, 1001, pending=False, final=final)
    s = api.status(r["request"])["studies"]["rc"]
    assert s["state"] == state and s["ok"] is False and s["reliable"] is False
    # it stays final: written as the study's result
    assert json.loads((Path(r["request"]) / "rc" / "rc.json").read_text())["state"] == state


def test_final_state_from_job_log_without_accounting(slurm, monkeypatch):
    monkeypatch.setenv("FAKE_PENDING", "1")
    monkeypatch.setenv("FAKE_SACCT_FAIL", "1")
    r = api.run({"rc": study(RC, run={"queue": True})}, "ref", wait=0)
    req = Path(r["request"])
    (req / "rc" / "job.log").write_text("slurmstepd: error: *** JOB 1001 CANCELLED AT 2026 DUE TO TIME LIMIT ***\n")
    _set(slurm, 1001, pending=False, final="X")          # gone from the queue; sacct fails
    s = api.status(str(req))["studies"]["rc"]
    assert s["state"] == "timed_out"


def test_sbatch_failure_is_a_result(slurm, monkeypatch):
    monkeypatch.setenv("FAKE_SBATCH_FAIL", "Slurm controller not responding")
    r = api.run({"rc": study(RC, run={"queue": True})}, "ref", wait=0)
    s = r["studies"]["rc"]
    assert s["ok"] is False and s["why"].startswith("could not submit") and "controller" in s["why"]


def test_run_study_by_hand(slurm, monkeypatch):
    monkeypatch.setenv("OMCT_EXECUTOR", "local")
    folder = api.start({"rc": study(RC)}, "ref", inline=True)
    (folder / "rc" / "rc.json").unlink()
    p = _cli("run-study", str(folder), "rc")
    assert p.returncode == 0, p.stderr
    r = json.loads((folder / "rc" / "rc.json").read_text())
    assert r["ok"] and r["execution"]["executor"] == "by hand"


def test_status_reports_executor(slurm):
    st = executor.status()
    assert st["kind"] == "slurm" and st["min_minutes"] == 1.0 and st["time_limit_min"] == 20 and "caps" in st


def test_bench_through_slurm(slurm, monkeypatch):
    monkeypatch.setenv("OMCT_SLURM_MIN_MINUTES", "0")
    b = api.bench(["rc_step", "inertia_torque_step"], "ref", wait_s=0)
    assert b["pending"] and all("job_id" in v for v in b["progress"].values())
    t_end = time.time() + 90
    while not b["done"] and time.time() < t_end:
        time.sleep(1)
        b = api.bench_status(b["bench"], 0)
    assert b["done"] and sorted(b["passed"]) == ["inertia_torque_step", "rc_step"]
