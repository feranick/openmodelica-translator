"""runtime.py — omct's llmt-runtime: the executor code the translators share, with omct's settings and hooks.
Version: 2026.11.09.2

    RT                 llmt_runtime.Runtime for omct (OMCT_ settings, the `omct` CLI, threads: sweep points at once)
    RUNTIME_VERSION    llmt-runtime's version (tool_status().executor.runtime)

Phase 1 (2026.11.09.1): workers, how a job that left the queue ended (final_state), version recording and ran_with,
request_name come from llmt-runtime. Phase 2 (2026.11.09.2): the SLURM layer (settings, the machine, the node's memory,
OMCT_SLURM_TIME, job.sh and job.json, submission, states, the queue's CPUs, cancel's flags and scancel) is
llmt-runtime's too, with omct's sizing as hooks: the CPUs (executor.job_cpus: sweep points at once, the compile CPUs on a
cache miss, capped at the node's CPUs), the memory (executor.mem_auto: 1 GB + 256 MB per CPU), the runs per job
(executor.sweep_rounds: a sweep's rounds of points, so its time limit covers them all), job.sh's PYTHONPATH line
(executor.script_lines), job.json's estimate_min and time_min (executor.job_extra), the progress of running jobs
(api.job_progress), the out-of-memory advice and the timeout hint. The time limit itself and the #SBATCH resource lines
are the runtime's defaults (ceil(runs x timeout / 60) + 10 minutes; --ntasks=1 --cpus-per-task=N). The MCP server's
stale handling and log stay in mcp_server.py until phase 3.

The hooks are small wrappers that import executor and api when called: executor imports RT from here.
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


def _study_procs(study: dict):
    """omct studies have no CPU count of their own: run.procs, then OMCT_PROCS."""
    return None


def _procs(study: dict) -> int:
    from .executor import job_cpus
    return job_cpus(study)


def _mem_auto(study: dict) -> str:
    from .executor import mem_auto
    return mem_auto(study)


def _solves(study: dict) -> int:
    from .executor import sweep_rounds
    return sweep_rounds(study)


def _script_lines(request: Path, name: str, study: dict) -> list:
    from .executor import script_lines
    return script_lines(request, name, study)


def _job_extra(study: dict) -> dict:
    from .executor import job_extra
    return job_extra(study)


def _running_progress(folder: Path, study: dict) -> dict:
    from .api import job_progress
    return job_progress(folder, study)


RT = Runtime(prefix="OMCT", cli="omct", package="openmodelica_translator", version=__version__, kind="threads",
             out_default="~/omct_runs", min_minutes=1.0, timeout_default=600.0, slurm_time_default="timeout",
             hooks=Hooks(study_procs=_study_procs, procs=_procs, mem_auto=_mem_auto, solves=_solves,
                         script_lines=_script_lines, job_extra=_job_extra, oom_advice=oom_advice,
                         timeout_hint=("with OMCT_SLURM_TIME=estimate rerun with OMCT_SLURM_TIME=timeout; or shorten "
                                       "the stop time / the sweep"),
                         running_progress=_running_progress))
