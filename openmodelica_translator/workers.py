"""workers.py — omct's worker processes end with the process that started them (from llmt-runtime).
Version: 2026.11.09.1

    watch_parent(ppid)     initializer of every worker pool (the server's study pool, a sweep's process pool): a
                           daemon thread ends the worker when its parent (or the server ppid) is gone; SIGTERM
                           (Pool.terminate, the stale-server exit, llmt servers --stop) does the same
    track(p) / untrack(p)  the programs a worker runs (omc builds, simulations; proc.run starts each in its own
                           process group): killed with it

The code is llmt-runtime's workers module (llmt_runtime.workers): polling os.getppid(), not PR_SET_PDEATHSIG (it fires
when the *thread* that created the child exits, and omct's pool is created inside a tool call). These names stay so
that omct's callers (proc.run) and tests keep working. Before 2026.11.09.1 a worker given SIGTERM (api._close_pool's
terminate) died at once and its omc build or simulation, in its own process group, ran on: an omc build has no time
limit of its own.
"""
from __future__ import annotations

from llmt_runtime import workers as _W
from llmt_runtime.workers import end_children, on_orphan, track, untrack  # noqa: F401 (omct's names)

PARENT_POLL_S = _W.PARENT_POLL_S
_CHILDREN = _W._CHILDREN
_STARTED = _W._STARTED


def watch_parent(ppid: int | None = None) -> None:
    """Starts (once per process) the watcher that ends this worker, and the programs it started, when its parent
    `ppid` (default: the parent now) is gone (llmt_runtime.workers.watch_parent)."""
    _W.PARENT_POLL_S = PARENT_POLL_S       # (a test may set a shorter poll here)
    _W.watch_parent(ppid, name="omct")
