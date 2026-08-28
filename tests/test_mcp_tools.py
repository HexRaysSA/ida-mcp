"""End-to-end checks through the ida-nexus MCP tools themselves.

These call the registered tool functions the way an MCP client does, with the
Nexus transport faked out, to confirm that idle release stays invisible at the
tool boundary and not merely inside the manager.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import IDLE_TIMEOUT, FakeClock, FakeWorld
from ida_nexus.cli import mcp as nexus_mcp

from ida_mcp.idle import IdleDatabaseManager


@pytest.fixture
def tools(
    monkeypatch: pytest.MonkeyPatch,
    world: FakeWorld,
    clock: FakeClock,
) -> SimpleNamespace:
    manager = IdleDatabaseManager(idle_timeout=IDLE_TIMEOUT, time_source=clock)
    monkeypatch.setattr(nexus_mcp, "DATABASE_MANAGER", manager)
    return SimpleNamespace(manager=manager, module=nexus_mcp)


def test_tools_never_observe_an_idle_release(
    tools: SimpleNamespace,
    world: FakeWorld,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    sample = str(tmp_path.resolve() / "sample")

    opened = nexus_mcp.open_database(sample)
    instance_id = opened["instance_id"]
    listed = nexus_mcp.list_databases()["instances"]

    clock.advance(IDLE_TIMEOUT + 1)
    tools.manager._tick()

    # The worker is gone, but nothing an agent can see has changed.
    assert not world.live
    assert nexus_mcp.list_databases()["instances"] == listed

    result: dict[str, Any] = asyncio.run(
        nexus_mcp.execute_python("db.entry", instance_id)
    )

    assert result["result"] == "db.entry"
    assert world.opens == [sample, sample]
    assert nexus_mcp.list_databases()["instances"] == listed
    assert nexus_mcp.open_database(sample)["instance_id"] == instance_id
    assert nexus_mcp.save_database(instance_id)["path"] == f"{sample}.i64"
    assert nexus_mcp.close_database(instance_id) == {"closed": True}
