"""conftest.py — the repository on sys.path; settings cleared so tests do not depend on config/env; llmt-runtime's
caches of what SLURM said (the cluster's nodes, the partition's limit, the pending jobs) dropped before each test.
Version: 2026.11.09.2
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("OMCT_") and k not in ("OMCT_MSL_SRC",):
            monkeypatch.delenv(k, raising=False)
    from llmt_runtime import jobs as J
    from llmt_runtime import slurm as S
    S.forget()                                   # a node list learnt in one test (kept 60 s) is not another's
    J._PENDING.update(t=0.0, v=None)
    yield
    S.forget()
    J._PENDING.update(t=0.0, v=None)
