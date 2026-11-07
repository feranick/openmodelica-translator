"""workers.py — omct's worker processes end with the process that started them.
Version: 2026.11.07.1

    watch_parent(ppid)     initializer of every worker pool (the server's study pool, a sweep's process pool): a
                           daemon thread ends the worker when its parent is gone
    track(p) / untrack(p)  the programs a worker runs (omc builds, simulations; proc.run starts each in its own
                           process group): killed with it

llm-translators docs/create_new_translators.md, "Worker pools never outlive the server": a spawned worker holds both
ends of its task queue, so it never sees EOF when its parent dies, waits forever with its resource_tracker and keeps
its release 'in use' for llmt gc (feat, carbonio, 2026-11). A server that exits stops its pools first
(api._close_pool: atexit, the stale-server exit, SIGTERM/SIGHUP/SIGINT); this is the backstop for a crash or kill -9.

Not PR_SET_PDEATHSIG: on Linux it fires when the *thread* that created the child exits, and omct's pool is created
inside a tool call, in a worker thread that comes and goes; it would kill healthy workers in mid-study (as feat
found). Every platform polls os.getppid() instead: when the parent dies the worker is re-parented and its parent
pid changes.
"""
from __future__ import annotations

import os
import signal
import threading
import time

PARENT_POLL_S = 1.0
_CHILDREN: set = set()          # subprocess.Popen objects started in their own process group
_STARTED = {"pid": None}        # the process the watcher runs in (a fork would inherit the flag)


def track(p) -> None:
    _CHILDREN.add(p)


def untrack(p) -> None:
    _CHILDREN.discard(p)


def _orphaned() -> None:
    for p in list(_CHILDREN):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    os._exit(0)


def watch_parent(ppid: int | None = None) -> None:
    """Starts (once per process) a daemon thread that ends this process, and the programs it started, when its
    parent `ppid` (default: the parent now) is gone."""
    if _STARTED["pid"] == os.getpid():
        return
    _STARTED["pid"] = os.getpid()
    ppid = int(ppid or os.getppid())
    if os.getppid() != ppid:      # the parent died before the worker started
        _orphaned()

    def loop():
        while True:
            time.sleep(PARENT_POLL_S)
            if os.getppid() != ppid:
                _orphaned()
    threading.Thread(target=loop, daemon=True, name="omct-parent-watch").start()
