"""omct on llmt-runtime (2026.11.09.1, phase 1): the shared pieces are the runtime's, configured for omct.
Version: 2026.11.09.1
"""
import json
from pathlib import Path

import llmt_runtime
from llmt_runtime import workers as W

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
