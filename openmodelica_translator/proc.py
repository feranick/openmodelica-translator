"""proc.py — run a backend program in its own process group, with a time limit and a cancel flag.
Version: 2026.11.19.5

run(cmd, cwd, timeout, log, cancel, env) -> {exit, seconds, timed_out, cancelled}. The whole group is killed on
timeout or cancel (omc starts make and the C compiler; nothing is left behind). The cancel flag is a file: any
process (another server, cancel()) can create it, and the run stops within about 0.2 s.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .workers import track, untrack


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):
        if p.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


EXTEND_POLL_S = 2.0


def run(cmd, cwd, timeout: float, log: Path, cancel: Path | None = None, env: dict | None = None,
        extend_dir: Path | None = None) -> dict:
    """extend_dir (default: the cancel flag's folder, i.e. the study's): the time the extend tool added to the study
    (llmt-runtime's take_extension, which consumes an 'extend' file there; the total in extended.json) moves this
    run's deadline, also while it runs: timeout + every extension so far."""
    from llmt_runtime.jobs import extended_s, take_extension
    t0 = time.perf_counter()
    ext_dir = Path(extend_dir) if extend_dir else (Path(cancel).parent if cancel is not None else None)
    extra, next_poll = (extended_s(ext_dir) if ext_dir else 0.0), 0.0
    e = dict(os.environ)
    e.update(env or {})
    with open(log, "wb") as out:
        p = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=e, start_new_session=True)
        track(p)                          # killed with this worker if its server dies (workers.py)
        timed_out = cancelled = False
        while p.poll() is None:
            el = time.perf_counter() - t0
            if ext_dir is not None and el >= next_poll:
                take_extension(ext_dir)
                extra, next_poll = extended_s(ext_dir), el + EXTEND_POLL_S
            if el > timeout + extra:
                timed_out = True
                _kill(p)
                break
            if cancel is not None and cancel.exists():
                cancelled = True
                _kill(p)
                break
            time.sleep(0.05 if time.perf_counter() - t0 < 2 else 0.2)
        p.wait()
        untrack(p)
    return {"exit": p.returncode, "seconds": round(time.perf_counter() - t0, 3), "timed_out": timed_out,
            "cancelled": cancelled, "extended_s": extra}
