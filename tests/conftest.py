from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import FakeClock, FakeHandle, FakeWorld

# ida-nexus resolves its state directory at import time; keep tests out of the
# developer's real IDA state.
_STATE_DIR = tempfile.mkdtemp(prefix="ida-mcp-tests-")
os.environ["IDA_NEXUS_STATE_DIR"] = _STATE_DIR


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    del session, exitstatus
    shutil.rmtree(_STATE_DIR, ignore_errors=True)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    fake = FakeWorld()

    class FakeDatabaseHandle:
        @staticmethod
        def open(path: str, **kwargs: Any) -> FakeHandle:
            return fake.open(path, **kwargs)

    monkeypatch.setattr("ida_nexus.manager.DatabaseHandle", FakeDatabaseHandle)
    monkeypatch.setattr("ida_nexus.manager.scan_instances", lambda *a, **k: [])
    return fake


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def paths(tmp_path: Path) -> SimpleNamespace:
    """Canonical paths, since the manager canonicalizes what it is given."""
    base = tmp_path.resolve()
    return SimpleNamespace(
        **{
            name: str(base / name)
            for name in ("sample", "gui", "busy", "forgotten", "first", "second")
        }
    )
