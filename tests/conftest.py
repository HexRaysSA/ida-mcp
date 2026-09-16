from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_STATE_DIR = Path(tempfile.mkdtemp(prefix="ida-mcp-tests-"))
os.environ["IDA_NEXUS_STATE_DIR"] = str(_STATE_DIR)


@pytest.fixture(autouse=True)
def clean_session_state() -> Iterator[None]:
    for name in ("instances", "spawn", "logs", "sessions"):
        shutil.rmtree(_STATE_DIR / name, ignore_errors=True)
    yield
    for name in ("instances", "spawn", "logs", "sessions"):
        shutil.rmtree(_STATE_DIR / name, ignore_errors=True)


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    del session, exitstatus
    shutil.rmtree(_STATE_DIR, ignore_errors=True)
