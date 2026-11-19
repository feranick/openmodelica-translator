"""test_workers.py — pool workers and the programs they run end when the process that started them dies (llm-translators
"Worker pools never outlive the server": leftover spawn workers held old releases 'in use').
Version: 2026.11.18.3
"""
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SERVER = textwrap.dedent("""
    import multiprocessing as mp, os, sys, time
    sys.path.insert(0, {repo!r})
    from openmodelica_translator.workers import watch_parent

    def task(folder):
        from pathlib import Path
        from openmodelica_translator import proc
        Path(folder, "worker.pid").write_text(str(os.getpid()))
        proc.run(["sh", "-c", "echo $$ > " + folder + "/prog.pid; exec sleep 60"], folder, 120, Path(folder, "prog.log"))

    if __name__ == "__main__":
        pool = mp.get_context("spawn").Pool(1, initializer=watch_parent, initargs=(os.getpid(),))
        pool.apply_async(task, ({folder!r},))
        for _ in range(200):
            if os.path.exists(os.path.join({folder!r}, "prog.pid")):
                break
            time.sleep(0.05)
        os._exit(0)          # a crash: no pool.terminate(), no atexit
""")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                      # a zombie of an exited parent is reaped by init; treat it as gone
        return "Z" not in open(f"/proc/{pid}/stat").read().split()[2]
    except OSError:
        return True


def test_worker_and_its_program_end_with_a_dead_server(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(SERVER.format(repo=str(REPO), folder=str(tmp_path)))
    subprocess.run([sys.executable, str(script)], timeout=60, check=True)
    w = int((tmp_path / "worker.pid").read_text())
    prog = int((tmp_path / "prog.pid").read_text())
    deadline = time.time() + 10
    while time.time() < deadline and (_alive(w) or _alive(prog)):
        time.sleep(0.2)
    assert not _alive(w), "the pool worker outlived its server"
    assert not _alive(prog), "the program the worker ran outlived it"


def test_pools_are_started_with_the_parent_watch(monkeypatch):
    """The server's study pool and a sweep's process pool both use watch_parent (the backstop for kill -9)."""
    import inspect
    from openmodelica_translator import api, sweep
    assert "initializer=watch_parent" in inspect.getsource(api._Pool.ensure)
    assert "initializer=watch_parent" in inspect.getsource(sweep.run_sweep)


def test_workers_not_permitted_reaches_the_caller_and_the_server_stays_up(tmp_path, monkeypatch):
    """A sandbox that refuses worker processes (Gemini's Local MCP: SemLock EPERM): run_studies fails with the
    runtime's text, leaves no request behind that would look pending, and the server answers the next call."""
    import asyncio

    import pytest
    from llmt_runtime import workers as W
    from studies import RC, study

    from openmodelica_translator import api
    from openmodelica_translator import mcp_server as S

    def refuse(*a, **k):
        raise W.WorkersNotPermitted(PermissionError(1, "Operation not permitted"))
    monkeypatch.setenv("OMCT_OUT", str(tmp_path))
    monkeypatch.setenv("OMCT_EXECUTOR", "local")
    monkeypatch.setattr(W, "make_pool", refuse)
    monkeypatch.setattr(api._POOL, "pool", None)
    monkeypatch.setattr(S.GUARD, "stale", lambda *a, **k: None, raising=False)
    with pytest.raises(Exception, match="worker processes are not permitted here"):
        asyncio.run(S.run_studies({"rc": study(RC)}, solver="ref", wait_s=1))
    assert not any(p.name.startswith("run_") for p in tmp_path.iterdir())
    assert "requests" in asyncio.run(S.jobs(limit=1))
    assert "error" not in asyncio.run(S.validate_studies({"rc": study(RC)}))


def test_sweep_executor_not_permitted_is_the_studys_error(tmp_path, monkeypatch):
    """Inside a worker (or the CLI) a sweep's process pool refused: the study fails with the runtime's text."""
    from llmt_runtime import workers as W
    from studies import RC, study

    from openmodelica_translator import runner

    def refuse(*a, **k):
        raise W.WorkersNotPermitted(PermissionError(1, "Operation not permitted"))
    monkeypatch.setattr(W, "make_executor", refuse)
    st = study(RC, sweep={"params": {"R.R": [500.0, 1000.0, 2000.0]}}, run={"procs": 2})
    r = runner.run_study("sw", st, tmp_path / "sw", "ref", 60)
    assert not r["ok"] and "worker processes are not permitted here" in r["why"]
