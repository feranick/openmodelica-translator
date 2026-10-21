"""proc.py — run a backend program in its own process group, with a time limit and a cancel flag.
Version: 2026.10.21.2

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


def run(cmd, cwd, timeout: float, log: Path, cancel: Path | None = None, env: dict | None = None) -> dict:
    t0 = time.perf_counter()
    e = dict(os.environ)
    e.update(env or {})
    with open(log, "wb") as out:
        p = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=e, start_new_session=True)
        timed_out = cancelled = False
        while p.poll() is None:
            if time.perf_counter() - t0 > timeout:
                timed_out = True
                _kill(p)
                break
            if cancel is not None and cancel.exists():
                cancelled = True
                _kill(p)
                break
            time.sleep(0.05 if time.perf_counter() - t0 < 2 else 0.2)
        p.wait()
    return {"exit": p.returncode, "seconds": round(time.perf_counter() - t0, 3), "timed_out": timed_out,
            "cancelled": cancelled}
