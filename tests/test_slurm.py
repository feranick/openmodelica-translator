"""test_slurm.py — SLURM execution (llm-translators docs/EXECUTION.md) against llmt-runtime's fake SLURM
(llmt_runtime.testing.fake_slurm; omct's own tests/fake_slurm.py until 2026.11.09.1), with the reference solver (no
OpenModelica needed).
Version: 2026.11.09.2
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from llmt_runtime import jobs as J
from llmt_runtime import slurm as S
from llmt_runtime.testing import calls, fake_slurm
from studies import MSD, RC, study

from openmodelica_translator import api, executor

REPO = Path(__file__).resolve().parents[1]
FAKE_KNOBS = ("FAKE_SBATCH_FAIL", "FAKE_SBATCH_PENDING", "FAKE_PENDING", "FAKE_SBATCH_DELAY", "FAKE_SBATCH_SLEEP",
              "FAKE_SBATCH_SLOW_AFTER", "FAKE_MIN_JOB_AGE", "FAKE_PRIORITY", "FAKE_RUN_NODE", "FAKE_START",
              "FAKE_SACCT_FAIL", "FAKE_SQUEUE_FAIL", "FAKE_SCONTROL_NODES", "FAKE_SCONTROL_FAIL", "FAKE_SCONTROL_DENY",
              "FAKE_SINFO_FAIL", "FAKE_SINFO_PARTITIONS", "FAKE_SINFO_CPUS", "FAKE_TIME_LEFT")


@pytest.fixture
def slurm(tmp_path, monkeypatch):
    """llmt-runtime's fake SLURM on PATH (one node fake1: 40 CPUs, 64000 MB); returns its state folder (job records in
    <state>/jobs/<id>.json)."""
    monkeypatch.setenv("OMCT_EXECUTOR", "slurm")
    monkeypatch.setenv("OMCT_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("OMCT_TIMEOUT", "600")
    monkeypatch.setenv("OMCT_SOLVER", "ref")
    monkeypatch.delenv("PYTHONPATH", raising=False)     # as on mochi: job.sh must find the package itself
    for k in FAKE_KNOBS + ("SLURM_JOB_ID", "SLURM_CPUS_PER_TASK", "OMCT_EXECUTION", "OMCT_PROCS_REQUESTED"):
        monkeypatch.delenv(k, raising=False)
    return fake_slurm(tmp_path, monkeypatch)


def _job(state_dir: Path, i) -> dict:
    return json.loads((state_dir / "jobs" / f"{i}.json").read_text())


def _set(state_dir: Path, i, **kw):
    """Changes a job's record in the fake: state=None takes it out of PENDING (with final=...: it left the queue in
    that state, as squeue and sacct then say). (state_dir: the fake's folder; a record's own 'state' is one of the
    keywords.)"""
    r = _job(state_dir, i)
    r.update(kw)
    (state_dir / "jobs" / f"{i}.json").write_text(json.dumps(r))


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
    sh = (req / "rc" / "job.sh").read_text()
    # llmt-runtime's fix (2026.11.09.2): --mem is in job.sh, no longer only on sbatch's command line (`sbatch job.sh`
    # by hand lost it)
    assert job["mem"] == "1280M" and "#SBATCH --mem=1280M" in sh
    assert not any(str(a).startswith("--mem") for a in job["command"])
    assert f"#SBATCH --job-name=omct:{req.name}:rc" in sh and "#SBATCH --ntasks=1" in sh
    assert "#SBATCH --cpus-per-task=1" in sh and "#SBATCH --export=ALL" in sh and f"run-study {req} rc" in sh
    assert f"export PYTHONPATH={REPO}" in sh          # found without being installed (mochi, before llmt install)
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
    # 2026.11.09.2: the 6 points run in 3 rounds (the first alone, then 5 on 3 CPUs), each with the 600 s timeout, so
    # 'timeout' is 3 x 10 + 10 = 40 min (it was 20: SLURM could stop a big sweep); 'estimate' stays within it
    assert executor.time_limit_min(sw) == 40
    assert executor.time_limit_min(sw, 0.1) == 20 and executor.time_limit_min(sw, 30) == 40   # min(100, 40)
    monkeypatch.setenv("OMCT_TIMEOUT", "36000")
    assert executor.time_limit_min(sw, 30) == 100
    r = api.run({"sw": sw}, "ref", wait=0)
    req = Path(r["request"])
    assert "#SBATCH --cpus-per-task=3" in (req / "sw" / "job.sh").read_text()
    out = _wait_done(req)
    s = out["studies"]["sw"]
    assert s["ok"] and s["sweep"]["points"] == 6 and s["sweep"]["procs"] == 3, s
    assert s["execution"]["procs_requested"] == 3 and s["execution"]["procs_used"] == 3


def test_validation_gives_the_job_estimate(slurm, monkeypatch):
    """docs/EXECUTION.md "Estimates in validation": {minutes, procs, mem_mb} from the job sizing, valid studies only."""
    sw = study(RC, sweep={"params": {"R.R": [500, 1000, 2000, 4000, 8000, 16000]}}, run={"procs": 3})
    monkeypatch.setenv("OMCT_SOLVER", "ref")
    r = api.validate({"sw": sw, "rc": study(RC), "bad": {"components": {}}})
    e = r["studies"]["sw"]["estimate"]
    assert e == {"minutes": executor.estimate(sw, "ref")["minutes"], "procs": 3, "mem_mb": 1024 + 256 * 3}
    assert e["minutes"] == round(executor.estimate_minutes(sw, "ref"), 3)
    assert r["studies"]["rc"]["estimate"]["procs"] == 1
    assert "estimate" not in r["studies"]["bad"]
    # memory as for --mem: the study's run.mem, else --mem in OMCT_SLURM_ARGS, else OMCT_SLURM_MEM
    assert executor.estimate(study(RC, run={"mem": "3G"}), "ref")["mem_mb"] == 3072
    monkeypatch.setenv("OMCT_SLURM_ARGS", "--mem=16G --partition=x")
    assert executor.estimate(study(RC), "ref")["mem_mb"] == 16384
    monkeypatch.delenv("OMCT_SLURM_ARGS")
    monkeypatch.setenv("OMCT_SLURM_MEM", "2G")
    assert executor.estimate(study(RC), "ref")["mem_mb"] == 2048
    # a build to do with omc: the compile CPUs, and its time in the minutes
    e = executor.estimate(study(RC), "omc")
    assert e["procs"] == 4 and e["minutes"] > executor.estimate(study(RC), "ref")["minutes"]


def test_queued_then_cancelled(slurm, monkeypatch):
    monkeypatch.setenv("FAKE_PENDING", "1")
    r = api.run({"m": study(MSD, run={"queue": True})}, "ref", wait=0)
    req = r["request"]
    p = api.status(req)["progress"]["m"]
    # SLURM's reason as the fake gives it: llmt-runtime's says Priority (omct's own fake said Resources)
    assert p["state"] == "queued" and p["reason"] == "Priority" and p["ahead"] == 0
    c = api.cancel(req)
    assert c["cancelled_jobs"] == ["1001"]
    s = api.status(req)["studies"]["m"]
    assert s["state"] == "cancelled"


@pytest.mark.parametrize("final, state", [("TIMEOUT", "timed_out"), ("OUT_OF_MEMORY", "out_of_memory"),
                                          ("FAILED", "failed"), ("CANCELLED by 1000", "cancelled")])
def test_final_states_from_sacct(slurm, monkeypatch, final, state):
    monkeypatch.setenv("FAKE_PENDING", "1")
    r = api.run({"rc": study(RC, run={"queue": True})}, "ref", wait=0)
    _set(slurm, 1001, state=None, final=final)           # it left the queue without a result
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
    _set(slurm, 1001, state=None, final="X")             # gone from the queue; sacct fails
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


# ---- clusters of several nodes (llm-translators docs/EXECUTION.md, "Clusters of several nodes") -------------
SCONTROL = ("NodeName=spark-5da5 Arch=aarch64 CPUTot=20 NodeAddr=100.77.175.91 NodeHostName=spark-5da5 RealMemory=124000\n"
            "NodeName=spark-0808 Arch=aarch64 CPUTot=12 NodeAddr=100.76.107.42 NodeHostName=spark-0808 RealMemory=60000\n")


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    """A fake two-node cluster (the sparks), seen from spark-5da5: llmt-runtime's fake SLURM with the nodes as
    `scontrol -o show node` lines (FAKE_SCONTROL_NODES; omct's tests patched executor._run before 2026.11.09.2).
    Returns (executor, the nodes file, the fake's folder)."""
    state = fake_slurm(tmp_path, monkeypatch)
    for k in FAKE_KNOBS:
        monkeypatch.delenv(k, raising=False)
    nodes = tmp_path / "nodes.txt"
    nodes.write_text(SCONTROL)
    monkeypatch.setenv("FAKE_SCONTROL_NODES", str(nodes))
    monkeypatch.setenv("OMCT_EXECUTOR", "slurm")
    monkeypatch.setattr("socket.gethostname", lambda: "spark-5da5")
    monkeypatch.setattr(S, "addresses", lambda: {"10.0.0.5", "100.76.107.42"})    # llmt-runtime's this_node asks it
    S.forget()
    return executor, nodes, state


def _nodes(nodes: Path, text: str) -> None:
    """Other nodes in the fake's `scontrol -o show node`; then the cluster is asked again."""
    nodes.write_text(text)
    S.forget()


def test_node_local_by_host_name_and_by_address(cluster, monkeypatch):
    X, _, _ = cluster
    assert X.local_node() == "spark-5da5" and X.node_list() == "spark-5da5"
    assert "#SBATCH --nodelist=spark-5da5" in X.job_script(Path("/r"), "s", 2, 20)
    st = X.status()
    assert st["node"] == {"setting": "local", "nodelist": "spark-5da5", "cluster_nodes": 2}
    assert st["nodes"] == [{"name": "spark-5da5", "cpus": 20}]
    monkeypatch.setattr("socket.gethostname", lambda: "dgx-peer")       # host name unknown to SLURM: by address
    assert X.local_node() == "spark-0808" and X.node_cpus() == 12


def test_node_any_list_args_and_one_node(cluster, monkeypatch):
    X, nodes, _ = cluster
    monkeypatch.setenv("OMCT_SLURM_NODE", "any")
    assert X.node_list() is None and "--nodelist" not in X.job_script(Path("/r"), "s", 2, 20)
    assert X.status()["node"]["nodelist"] is None
    monkeypatch.setenv("OMCT_SLURM_NODE", "spark-0808,spark-5da5")
    assert X.node_list() == "spark-0808,spark-5da5"
    monkeypatch.setenv("OMCT_SLURM_NODE", "local")
    monkeypatch.setenv("OMCT_SLURM_ARGS", "-w spark-0808")                # the machine's own --nodelist wins
    assert X.node_list() is None
    monkeypatch.delenv("OMCT_SLURM_ARGS")
    _nodes(nodes, "NodeName=mochi CPUTot=40 NodeAddr=mochi NodeHostName=mochi\n")
    assert X.node_list() is None                                         # one node: nothing to pin


def test_node_failure_is_not_remembered(cluster, monkeypatch):
    """A failed scontrol is asked again (llmt-runtime's: not within NODES_RETRY_S, 5 s; 0 here), an answer is kept."""
    X, _, state = cluster
    monkeypatch.setattr(S, "NODES_RETRY_S", 0.0)
    monkeypatch.setenv("FAKE_SCONTROL_FAIL", "1")
    assert X.node_list() is None and X.local_node() is None
    monkeypatch.delenv("FAKE_SCONTROL_FAIL")
    assert X.node_list() == "spark-5da5"                                 # asked again once the controller is back
    n = calls(state, "scontrol")
    X.node_list()
    assert calls(state, "scontrol") == n                                 # then remembered


def test_job_size_capped_at_this_nodes_cpus(cluster, monkeypatch):
    """A sweep asking 32 points at once gets this node's 20 CPUs (spark-5da5), not the cluster's 32; with any,
    the largest node's."""
    X, _, _ = cluster
    st = study(RC, sweep={"params": {"R.R": [float(i + 1) for i in range(40)]}}, run={"procs": 32})
    monkeypatch.setattr(X, "cache_hit", lambda s: True)
    assert X.cpus_for_job(st) == 20 and X.estimate(st)["procs"] == 20
    monkeypatch.setattr("socket.gethostname", lambda: "spark-0808")
    assert X.cpus_for_job(st) == 12
    monkeypatch.setenv("OMCT_SLURM_NODE", "any")
    assert X.cpus_for_job(st) == 20


def test_slurm_args_with_several_options(cluster, monkeypatch):
    """OMCT_SLURM_ARGS holds several options: split like a shell; its --mem wins over OMCT_SLURM_MEM, its
    --nodelist over OMCT_SLURM_NODE; the estimate asks what the job asks."""
    X, _, _ = cluster
    monkeypatch.setattr(X, "cache_hit", lambda s: True)
    st = study(RC)
    monkeypatch.setenv("OMCT_SLURM_ARGS", "--mem=8G --nodelist=spark-5da5")
    monkeypatch.setenv("OMCT_SLURM_MEM", "2G")
    assert X.mem_for_job(st, 1) is None and X.mem_mb_for_job(st, 1) == 8192 and X.estimate(st)["mem_mb"] == 8192
    assert X.node_list() is None and "--nodelist" not in X.job_script(Path("/r"), "s", 1, 20)
    assert X.sbatch_args(st, X.mem_for_job(st, 1)) == ["--mem=8G", "--nodelist=spark-5da5"]
    monkeypatch.setenv("OMCT_SLURM_ARGS", "--mem 6G   -w spark-5da5")       # separate values, extra spaces
    assert X.mem_mb_for_job(st, 1) == 6144 and X.node_list() is None
    monkeypatch.setenv("OMCT_SLURM_ARGS", "--mem-per-cpu=1G --qos='high prio'")
    assert X.mem_mb_for_job(st, 3) == 3072
    assert X.sbatch_args(st, None) == ["--mem-per-cpu=1G", "--qos=high prio"]
    # llmt-runtime's slurm_args (2026.11.09.2): unbalanced quotes are read as plain words instead of raising, and a
    # --mem-bind is not taken for a --mem (omct's startswith('--mem') dropped the job's memory)
    monkeypatch.setenv("OMCT_SLURM_ARGS", "--mem-bind=local --qos='high")
    assert X.sbatch_args(st) == ["--mem-bind=local", "--qos='high"] and X.mem_for_job(st, 1) == "2G"


def test_sbatch_that_hangs_fails_the_rest_at_once(slurm, tmp_path, monkeypatch):
    """A controller that does not answer: the first submit waits SBATCH_TIMEOUT, the others fail at once (llmt-runtime's
    breaker, 30 s), so run_studies ends within its wait."""
    monkeypatch.setattr(J, "_SBATCH_DOWN", {"until": 0.0, "why": ""})
    n = {"calls": 0}
    real = subprocess.run

    def hang(cmd, *a, **kw):
        if cmd and cmd[0] == "sbatch":
            n["calls"] += 1
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return real(cmd, *a, **kw)
    monkeypatch.setattr(J.subprocess, "run", hang)
    for i in range(3):
        r = executor.submit(tmp_path, f"s{i}", study(RC))
        assert not r["ok"] and "slurmctld" in r["why"]
    assert n["calls"] == 1


def test_job_lost_on_another_node(cluster, tmp_path, monkeypatch):
    """A job that ran on the other node and left no job.log: llmt-runtime's lost-node diagnosis names the node and
    OMCT_SLURM_NODE; on this node, the usual one."""
    X, _, state = cluster
    monkeypatch.setenv("FAKE_PENDING", "1")
    monkeypatch.setenv("OMCT_SLURM_NODE", "any")
    req = tmp_path / "req"
    (req / "s").mkdir(parents=True)
    info = X.submit(req, "s", study(RC), "ref")
    # it ran (pid: sacct names its node), on spark-0808, and left neither a result nor job.log
    _set(state, info["job_id"], state=None, final="FAILED", node="spark-0808", pid=999999)
    out = X.slurm_states(req, ["s"], write=False)
    assert out["s"]["state"] == "failed" and "node spark-0808" in out["s"]["why"] and "OMCT_SLURM_NODE" in out["s"]["why"]
    _set(state, info["job_id"], node="spark-5da5")                       # its own node: the usual diagnosis
    assert "OMCT_SLURM_NODE" not in X.slurm_states(req, ["s"], write=False)["s"]["why"]


def test_status_ends_within_its_wait_when_the_queue_is_slow(monkeypatch, tmp_path):
    """A slow squeue (a busy controller) must not push study_status past its wait: no collect starts that could
    not end in time."""
    from openmodelica_translator import api as A
    req = tmp_path / "r"
    req.mkdir()
    (req / "request.json").write_text(json.dumps({"names": ["s"], "solver": "ref", "studies": {"s": {}}}))
    calls_ = []

    def slow(folder):
        calls_.append(time.time())
        time.sleep(0.8)
        return {"done": False, "progress": {"s": {"state": "queued"}}}
    monkeypatch.setattr(A, "_collect", slow)
    t0 = time.time()
    A.status(str(req), 2.0)
    assert time.time() - t0 <= 2.3 and len(calls_) >= 1


def test_caps_report_this_nodes_memory_for_the_router(cluster, monkeypatch):
    """caps.mem / caps.mem_mb from the SLURM node's RealMemory (llm-translators' router leaves out a machine whose
    limit is below run.mem); OMCT_MAX_MEM wins when lower; validation refuses more (2026-11-07: caps {} before)."""
    from openmodelica_translator import format as F
    X, nodes, _ = cluster
    c = F.caps()
    # llmt-runtime's fix (2026.11.09.2): the node's memory exactly as SLURM gives it (it read 121G, 123904 MB, and
    # refused a run.mem of the node's own 124000M)
    assert c["mem"] == "124000M" and c["mem_mb"] == 124000 and "RealMemory" in c["mem_from"]
    assert X.status()["caps"]["mem_mb"] == 124000
    monkeypatch.setattr("socket.gethostname", lambda: "spark-0808")
    assert F.caps()["mem"] == "60000M"                                # spark2's own node, not the cluster's largest
    monkeypatch.setenv("OMCT_MAX_MEM", "16G")
    assert F.caps() == {"mem": "16G", "mem_mb": 16384}
    monkeypatch.delenv("OMCT_MAX_MEM")
    r = api.validate({"s": study(RC, run={"mem": "90G"})})["studies"]["s"]
    assert not r["ok"] and any("at most 60000M" in e and "RealMemory" in e for e in r["errors"])
    assert api.validate({"s": study(RC, run={"mem": "8G"})})["studies"]["s"]["ok"]
    assert api.validate({"s": study(RC, run={"mem": "60000M"})})["studies"]["s"]["ok"]      # the node's own size
    _nodes(nodes, "NodeName=mochi CPUTot=40 RealMemory=1\n")          # memory not configured: no cap
    assert "mem" not in F.caps()


def test_caps_without_slurm_have_no_memory(monkeypatch):
    from openmodelica_translator import format as F
    monkeypatch.delenv("OMCT_EXECUTOR", raising=False)
    assert F.caps() == {}
