"""omct on llmt-runtime: the shared pieces are the runtime's, configured for omct (2026.11.09.1, phase 1: workers,
final_state, versions; 2026.11.09.2, phase 2: the SLURM layer, with omct's sizing as hooks).
Version: 2026.11.09.2
"""
import json
import math
import os
import shlex
import time
from pathlib import Path

import pytest

import llmt_runtime
from llmt_runtime import jobs as J
from llmt_runtime import slurm as S
from llmt_runtime import workers as W
from llmt_runtime.testing import fake_slurm
from studies import RC, study

from openmodelica_translator import __version__, api, workers
from openmodelica_translator import executor as X
from openmodelica_translator.runtime import RT


def test_runtime_configured_for_omct(monkeypatch):
    assert (RT.prefix, RT.cli, RT.package, RT.kind) == ("OMCT", "omct", "openmodelica_translator", "threads")
    monkeypatch.setenv("OMCT_OUT", "/tmp/omct-x")
    assert RT.out_root() == Path("/tmp/omct-x") == api.out_root()


def test_runtime_version_reported(monkeypatch):
    monkeypatch.setenv("OMCT_EXECUTOR", "local")
    assert X.status()["runtime"] == llmt_runtime.__version__


def test_workers_are_the_runtimes():
    assert workers.track is W.track and workers.untrack is W.untrack
    assert workers._CHILDREN is W._CHILDREN and workers._STARTED is W._STARTED


def test_final_state_oom_kill_wording_with_accounting(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "job.json").write_text(json.dumps({"procs": 4, "mem": "2G"}))
    log = "slurmstepd: error: Detected 1 oom_kill event in StepId=77.batch. Some of the step tasks have been OOM Killed."
    state, why = X.final_state("FAILED", log, d)                 # accounted as FAILED: the log says oom_kill
    assert state == "out_of_memory" and why.startswith("SLURM killed the job for memory (2G with 4 sweep points")
    assert "run.mem" in why
    assert X.final_state(None, "(no job.log)")[0] == "cancelled"
    st, why = X.final_state("TIMEOUT", "", d)
    assert st == "timed_out" and "OMCT_TIMEOUT" in why and "OMCT_SLURM_TIME=timeout" in why
    assert X.final_state("CANCELLED by 1000", "")[1].startswith("cancelled by 1000")


def test_request_records_versions_and_status_says_what_ran(tmp_path, monkeypatch):
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    monkeypatch.setenv("OMCT_EXECUTOR", "local")
    r = api.run({"bad": {"components": {}}}, "ref", wait=5, request_name="run_versions")   # invalid: nothing runs
    meta = json.loads((tmp_path / "run_versions" / "request.json").read_text())
    assert meta["translator"] == __version__ and meta["runtime"] == llmt_runtime.__version__
    assert r["translator"] == __version__ and "translator_now" not in r
    meta["translator"] = "2026.11.07.1"                              # submitted by the omct before
    (tmp_path / "run_versions" / "request.json").write_text(json.dumps(meta))
    again = api.status(str(tmp_path / "run_versions"))
    assert again["translator"] == "2026.11.07.1" and again["translator_now"] == __version__


# ---- 2026.11.09.2, phase 2: the SLURM layer (settings, the machine, time limits, job.sh and job.json, submission,
# states, the queue's CPUs, cancel's flags and scancel) is llmt-runtime's; omct's sizing comes in as hooks

FAKE_KNOBS = ("FAKE_SBATCH_FAIL", "FAKE_SBATCH_PENDING", "FAKE_PENDING", "FAKE_SBATCH_DELAY", "FAKE_SBATCH_SLEEP",
              "FAKE_SBATCH_SLOW_AFTER", "FAKE_MIN_JOB_AGE", "FAKE_PRIORITY", "FAKE_RUN_NODE", "FAKE_START",
              "FAKE_SACCT_FAIL", "FAKE_SQUEUE_FAIL", "FAKE_SCONTROL_NODES", "FAKE_SCONTROL_FAIL", "FAKE_SCONTROL_DENY",
              "FAKE_SINFO_FAIL", "FAKE_SINFO_PARTITIONS", "FAKE_SINFO_CPUS", "FAKE_TIME_LEFT")
SWEEP12 = {"params": {"R.R": [float(500 * (i + 1)) for i in range(12)]}}


@pytest.fixture()
def slurm(tmp_path, monkeypatch):
    """llmt-runtime's fake SLURM (one node fake1: 40 CPUs, 64000 MB); returns its state folder."""
    monkeypatch.setenv("OMCT_EXECUTOR", "slurm")
    monkeypatch.setenv("OMCT_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("OMCT_TIMEOUT", "600")
    monkeypatch.setenv("OMCT_SOLVER", "ref")
    for k in FAKE_KNOBS + ("SLURM_JOB_ID", "SLURM_CPUS_PER_TASK"):
        monkeypatch.delenv(k, raising=False)
    return fake_slurm(tmp_path, monkeypatch)


def _fake_job(state_dir: Path, jid: str, **rec) -> None:
    """A job in the fake's queue (a record as its sbatch writes them; pid 999999: not running)."""
    d = state_dir / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    base = {"name": "omct:r:s", "cpus": 1, "t0": time.time(), "log": "x", "pid": 999999, "node": "fake1"}
    (d / f"{jid}.json").write_text(json.dumps({**base, **rec}))


def _request(state_dir: Path, jid: str, log: str | None = None, job: dict | None = None) -> Path:
    """A request with one SLURM study 's' whose job.json names the job `jid`."""
    req = state_dir.parent / "out" / f"run_{jid}"
    (req / "s").mkdir(parents=True)
    (req / "request.json").write_text(json.dumps({"names": ["s"], "solver": "ref", "started": time.time(),
                                                  "studies": {"s": study(RC)}}))
    (req / "s" / "job.json").write_text(json.dumps({"executor": "slurm", "job_id": jid, "procs": 1, "time_min": 20,
                                                    "timeout_s": 600, **(job or {})}))
    if log is not None:
        (req / "s" / "job.log").write_text(log)
    return req


def test_slurm_layer_is_the_runtimes_with_omcts_hooks(monkeypatch):
    monkeypatch.setenv("OMCT_SOLVER", "ref")
    h = RT.hooks
    assert h.time_need_min is None and h.resource_lines is None             # the runtime's, with omct's solves
    assert h.study_procs(study(RC)) is None and X.procs_of(study(RC, run={"procs": 3})) == 3
    assert S.time_need_min(RT, {"run": {"timeout_s": 600}}) == 20 == X.time_limit_min({"run": {"timeout_s": 600}})
    sw = study(RC, sweep=SWEEP12, run={"procs": 4})
    assert h.procs(sw) == X.cpus_for_job(sw, "ref") == 4 and h.mem_auto(sw) == "2048M"     # 1 GB + 256 MB x 4
    assert h.solves(study(RC)) == 1 and h.solves(sw) == 4                    # 1, then 11 points on 4 CPUs: 3 rounds
    line = f"export PYTHONPATH={shlex.quote(X.PKG_ROOT)}${{PYTHONPATH:+:$PYTHONPATH}}"
    assert h.script_lines(Path("/r"), "s", sw) == [line]
    assert h.job_extra(study(RC)) == {"estimate_min": 0.05, "time_min": 20}
    assert h.oom_advice is not None and "OMCT_SLURM_TIME" in h.timeout_hint
    assert X.FINAL == J.FINAL and X.write_atomic is J.write_atomic
    monkeypatch.setenv("OMCT_EXECUTOR", "local")
    assert set(X.status()) == {"kind", "workers", "procs", "timeout_s", "caps", "compile_procs", "runtime"}


def test_executor_status_keeps_omcts_keys(slurm, monkeypatch):
    ex = X.status()
    assert set(ex) == {"kind", "sbatch", "min_minutes", "time_limit_min", "time", "mem", "args", "nodes", "node", "procs",
                       "timeout_s", "caps", "compile_procs", "runtime"}
    assert ex["time"] == "timeout" and ex["time_limit_min"] == 20 and ex["runtime"] == llmt_runtime.__version__
    assert ex["nodes"] == [{"name": "fake1", "cpus": 40}] and set(ex["node"]) >= {"setting", "nodelist", "cluster_nodes"}
    # OMCT_SLURM_TIME gains llmt-runtime's max and minutes
    monkeypatch.setenv("OMCT_SLURM_TIME", "max")
    monkeypatch.setenv("FAKE_SINFO_PARTITIONS", "main*|2:00:00")
    assert X.time_limit_min(study(RC), 1.0) == 120 and X.status()["time"] == "max"
    monkeypatch.setenv("OMCT_SLURM_TIME", "45")
    assert X.time_limit_min(study(RC), 1.0) == 45


def test_job_sh_and_job_json(slurm, monkeypatch):
    """job.sh as omct wrote it, with --mem inside (llmt-runtime's fix: it was only on sbatch's command line, so
    `sbatch job.sh` lost it) and --nodelist after --time (none here: one node); job.json with omct's keys and the
    runtime's."""
    monkeypatch.setenv("FAKE_PENDING", "1")
    req = slurm.parent / "out" / "run_j"
    info = X.submit(req, "a", study(RC), "ref")
    text = (req / "a" / "job.sh").read_text()
    exe = text.splitlines()[-1]
    assert text.splitlines() == [
        "#!/bin/bash", "#SBATCH --job-name=omct:run_j:a", f"#SBATCH --chdir={req / 'a'}",
        f"#SBATCH --output={req / 'a' / 'job.log'}", "#SBATCH --ntasks=1", "#SBATCH --cpus-per-task=1",
        "#SBATCH --time=20", "#SBATCH --mem=1280M", "#SBATCH --export=ALL",
        f"export PYTHONPATH={shlex.quote(X.PKG_ROOT)}${{PYTHONPATH:+:$PYTHONPATH}}", exe]
    assert exe.startswith("exec ") and exe.endswith(f"-m openmodelica_translator.cli run-study {req} a")
    assert text == X.job_script(req, "a", 1, 20, study(RC))
    j = json.loads((req / "a" / "job.json").read_text())
    old = {"executor", "job_id", "submitted", "procs", "time_min", "nodelist", "time_from", "timeout_s", "mem",
           "estimate_min", "command", "run"}
    assert old <= set(j) and j == info and j["job_id"] == "1001" and j["procs"] == 1 and j["time_min"] == 20
    assert j["mem"] == "1280M" and j["estimate_min"] == 0.05 and j["timeout_s"] == 600 and j["time_from"] == "timeout"
    assert not any(str(a).startswith("--mem") for a in j["command"])
    assert j["translator"] == __version__ and j["runtime"] == llmt_runtime.__version__


def test_a_sweeps_time_limit_covers_its_rounds(slurm, monkeypatch):
    """omct until 2026.11.09.1 asked one timeout + 10 min for a whole sweep while each point had the full timeout:
    SLURM could stop a big sweep long before omct's own timeouts. 12 points on 4 CPUs: the first alone (it builds the
    model), then 11 in 3 rounds of 4: 4 x 10 + 10 = 50 min."""
    monkeypatch.setenv("FAKE_PENDING", "1")
    req = slurm.parent / "out" / "run_sw"
    sw = study(RC, sweep=SWEEP12, run={"procs": 4})
    info = X.submit(req, "sw", sw, "ref")
    sh = (req / "sw" / "job.sh").read_text()
    assert "#SBATCH --cpus-per-task=4" in sh and "#SBATCH --time=50" in sh and info["time_min"] == 50
    assert X.sweep_rounds(sw, 4) == 4 and X.sweep_rounds(sw, 12) == 2 and X.sweep_rounds(sw, 1) == 12
    assert X.time_limit_min(sw) == math.ceil(4 * 600 / 60) + 10
    # more CPUs, fewer rounds; the job's CPUs never above the points
    big = study(RC, sweep=SWEEP12, run={"procs": 16})
    assert X.cpus_for_job(big, "ref") == 12 and X.time_limit_min(big) == 30
    # OMCT_SLURM_TIME=estimate: each point's own timeout fits the shorter limit (job.json timeout_s, run-study reads it)
    monkeypatch.setenv("OMCT_SLURM_TIME", "estimate")
    e = X.submit(req, "sw2", sw, "ref")
    assert e["time_min"] == 20 and e["time_from"] == "estimate" and e["timeout_s"] == (20 - 10) * 60 / 4
    assert J.job_timeout_s(req / "sw2", 600) == 150


def test_an_ended_job_squeue_still_lists_is_final(slurm, monkeypatch):
    """squeue lists an ended job for MinJobAge (300 s) in its final state: omct 2026.11.09.1 read every state but
    PENDING/CONFIGURING/REQUEUED/SUSPENDED as running. It is now final, from job.log (no accounting here), and written
    as the study's result."""
    monkeypatch.setenv("FAKE_MIN_JOB_AGE", "300")
    monkeypatch.setenv("FAKE_SACCT_FAIL", "1")
    _fake_job(slurm, "1001", ended=time.time(), final="FAILED")
    req = _request(slurm, "1001", log="Traceback (most recent call last):\nValueError: boom")
    assert X.squeue(["1001"])["1001"]["state"] == "FAILED"
    st = X.slurm_states(req, ["s"], {"s": study(RC)})["s"]
    assert st["state"] == "failed" and "ValueError: boom" in st["why"] and st["reliable"] is False
    assert json.loads((req / "s" / "s.json").read_text())["state"] == "failed"         # final: kept


def test_queue_position_by_priority(slurm):
    """A queued study's 'ahead': the pending jobs SLURM considers first (higher priority, or equal and older)."""
    _fake_job(slurm, "1001", state="PENDING", prio=1000)
    _fake_job(slurm, "1002", state="PENDING", prio=5000)
    _fake_job(slurm, "1003", state="PENDING", prio=1000)
    assert X.pending_ahead() == {"1001": 1, "1002": 0, "1003": 2}
    req = _request(slurm, "1003")
    st = X.slurm_states(req, ["s"], {"s": study(RC)})["s"]
    assert st["state"] == "queued" and st["ahead"] == 2 and st["reason"] == "Priority" and st["eta_min_max"] == 20


def test_jobs_cancel_and_queue_summary(slurm, monkeypatch):
    """jobs() keeps omct's shape on the runtime's states and CPUs (each node once: one row per node and partition);
    cancel() is the runtime's cancel_request (flags, scancel)."""
    monkeypatch.setenv("FAKE_PENDING", "1")
    monkeypatch.setenv("FAKE_SINFO_PARTITIONS", "debug*|1:00:00;long|infinite")      # fake1 in two partitions
    r = api.run({"a": study(RC, run={"queue": True}), "b": study(RC, run={"queue": True})}, "ref", wait=0)
    j = api.jobs()
    assert j["queue"] == {"running": 0, "queued": 2, "cpus_in_use": 0}
    assert j["machine"] == {"cpus": 8, "cpus_allocated": 2, "cpus_idle": 6}
    rec = j["requests"][0]
    assert rec["active"] and rec["studies"]["a"]["state"] == "queued" and rec["studies"]["b"]["ahead"] == 1
    assert api.cancel(r["request"], ["nope"]) == {"error": "no studies ['nope'] in this request (['a', 'b'])"}
    c = api.cancel(r["request"], ["a"])
    assert c["cancelled"] == ["a"] and c["cancelled_jobs"] == ["1001"] and c["states"]["a"] == "cancelled"
    assert (Path(r["request"]) / "a" / "cancel").is_file() and not (Path(r["request"]) / "b" / "cancel").exists()
    assert api.status(r["request"])["pending"] == ["b"]


def test_running_progress_for_the_runtimes_jobs(tmp_path):
    d = tmp_path / "r" / "s"
    (d / "points" / "p0000").mkdir(parents=True)
    (d / "sweep.json").write_text(json.dumps({"points": 4, "params": ["R.R"]}))
    (d / "points" / "p0000" / "p0000.json").write_text("{}")
    p = RT.hooks.running_progress(d, study(RC))
    assert p["stage"] == "sweep: 1 of 4 points done" and p["points_done"] == 1 and p["points"] == 4
    assert "eta_min_max" in p and "state" not in p


def test_benchmarks_through_slurm_run_once(slurm, monkeypatch):
    """cfdt's bench ran a case it had sent to SLURM locally as well (2026.11.09.4). omct's does not: run_benchmarks
    (wait_s) submits each case once and bench_status only reads the queue; `omct bench` (wait_s None) runs inline and
    submits nothing."""
    monkeypatch.setenv("FAKE_PENDING", "1")
    monkeypatch.setenv("OMCT_SLURM_MIN_MINUTES", "0")
    b = api.bench(["rc_step"], "ref", wait_s=0)
    assert b["pending"] == ["rc_step"] and b["progress"]["rc_step"]["job_id"] == "1001"
    for _ in range(3):
        b = api.bench_status(b["bench"], 0)
        assert not b["done"] and b["pending"] == ["rc_step"]
    assert not (Path(b["bench"]) / "rc_step" / "rc_step.json").exists()             # never run here
    assert [f.name for f in (slurm / "jobs").glob("*.json")] == ["1001.json"]
    inline = api.bench(["rc_step"], "ref")
    assert inline["done"] and inline["passed"] == ["rc_step"]
    assert not (Path(inline["bench"]) / "rc_step" / "job.json").exists()
    assert [f.name for f in (slurm / "jobs").glob("*.json")] == ["1001.json"]
    assert os.environ["OMCT_EXECUTOR"] == "slurm"
