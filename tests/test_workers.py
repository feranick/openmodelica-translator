"""test_workers.py — pool workers and the programs they run end when the process that started them dies (llm-translators
"Worker pools never outlive the server": leftover spawn workers held old releases 'in use').
Version: 2026.11.07.1
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
    assert "initializer=watch_parent" in inspect.getsource(api._Pool.submit)
    assert "initializer=watch_parent" in inspect.getsource(sweep.run_sweep)
