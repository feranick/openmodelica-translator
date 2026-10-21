"""conftest.py — the repository on sys.path; settings cleared so tests do not depend on config/env.
Version: 2026.10.21.1
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
