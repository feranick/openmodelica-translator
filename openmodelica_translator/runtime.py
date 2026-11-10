"""runtime.py — omct's llmt-runtime: the executor code the translators share, with omct's settings and hooks.
Version: 2026.11.09.1

    RT                 llmt_runtime.Runtime for omct (OMCT_ settings, the `omct` CLI, threads: sweep points at once)
    RUNTIME_VERSION    llmt-runtime's version (tool_status().executor.runtime)

Phase 1 (2026.11.09.1): workers, how a job that left the queue ended (final_state), version recording and ran_with,
request_name come from llmt-runtime. The SLURM layer (settings, caps, job.sh, states, jobs, cancel) and the MCP
server's stale handling and log stay in executor.py and mcp_server.py until phases 2 and 3.
"""
from __future__ import annotations

import json
from pathlib import Path

from llmt_runtime import Hooks, Runtime
from llmt_runtime import __version__ as RUNTIME_VERSION  # noqa: F401

from . import __version__


def oom_advice(folder: Path) -> str:
    """Why and what to ask for when SLURM killed a study's job for memory: the job's --mem and CPUs (job.json)."""
    try:
        job = json.loads((Path(folder) / "job.json").read_text())
    except (OSError, ValueError):
        job = {}
    n, mem = int(job.get("procs") or 1), job.get("mem")
    had = f" ({mem} with {n} sweep point{'s' if n != 1 else ''} at once)" if mem else ""
    return (f"SLURM killed the job for memory{had}: give the study run.mem (e.g. '4G', within study_format's caps), "
            "set OMCT_SLURM_MEM, or run fewer sweep points at once (run.procs)")


RT = Runtime(prefix="OMCT", cli="omct", package="openmodelica_translator", version=__version__, kind="threads",
             out_default="~/omct_runs", min_minutes=1.0, timeout_default=600.0, slurm_time_default="timeout",
             hooks=Hooks(oom_advice=oom_advice,
                         timeout_hint=("with OMCT_SLURM_TIME=estimate rerun with OMCT_SLURM_TIME=timeout; or shorten "
                                       "the stop time / the sweep")))
