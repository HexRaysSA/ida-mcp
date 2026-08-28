"""Tests for idle database release.

These exercise ``IdleDatabaseManager`` against a fake Nexus transport: no IDA,
no worker processes, and a clock the test advances by hand. The reaper thread
is never started; ``_tick()`` is called directly so the tests are deterministic.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import IDLE_TIMEOUT, FakeClock, FakeWorld
from ida_nexus import DatabaseSelectionError
from ida_nexus.manager import DatabaseManager

from ida_mcp.idle import IdleDatabaseManager


@pytest.fixture
def manager(world: FakeWorld, clock: FakeClock) -> IdleDatabaseManager:
    return IdleDatabaseManager(idle_timeout=IDLE_TIMEOUT, time_source=clock)


def open_and_idle(
    manager: IdleDatabaseManager,
    clock: FakeClock,
    path: str,
) -> str:
    instance_id = manager.open_database(path, set_current=True)["instance_id"]
    clock.advance(IDLE_TIMEOUT + 1)
    manager._tick()
    return instance_id


def test_idle_database_is_released(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = manager.open_database(paths.sample, set_current=True)["instance_id"]
    handle = world.live[paths.sample]

    clock.advance(IDLE_TIMEOUT + 1)
    manager._tick()

    assert handle.closed
    # The lease release waits for the worker to finish closing its IDB.
    assert handle.close_calls == [True]
    assert instance_id in manager._released


def test_release_keeps_the_instance_id_and_reattaches_transparently(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = open_and_idle(manager, clock, paths.sample)

    result = manager.execute_python("1 + 1", instance_id)

    assert result["result"] == "1 + 1"
    assert world.opens == [paths.sample, paths.sample]
    assert manager.resolve_instance_id(instance_id) == instance_id
    assert instance_id not in manager._released


def test_current_target_survives_a_release(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = open_and_idle(manager, clock, paths.sample)

    manager.execute_python("db", None)

    assert manager.resolve_instance_id(None) == instance_id
    assert manager._current_instance_id == instance_id


def test_a_second_database_does_not_steal_the_current_target(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    first = manager.open_database(paths.first, set_current=True)["instance_id"]
    manager.open_database(paths.second, set_current=False)
    clock.advance(IDLE_TIMEOUT + 1)
    manager._tick()

    # Both are idle, so both are released; the current target must come back
    # as the current target.
    assert manager.resolve_instance_id(None) == first


def test_forgotten_database_is_released_while_another_stays_in_use(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    busy = manager.open_database(paths.busy, set_current=True)["instance_id"]
    forgotten = manager.open_database(paths.forgotten, set_current=False)["instance_id"]

    for _ in range(4):
        clock.advance(IDLE_TIMEOUT / 2)
        manager.execute_python("db", busy)
        manager._tick()

    assert forgotten in manager._released
    assert busy not in manager._released
    assert world.live[paths.busy].connected


def test_gui_databases_are_never_released(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    world.gui_paths.add(paths.gui)
    instance_id = manager.open_database(paths.gui, set_current=True)["instance_id"]

    clock.advance(IDLE_TIMEOUT * 10)
    manager._tick()

    assert manager._released == {}
    assert world.live[paths.gui].connected
    assert manager.resolve_instance_id(instance_id) == instance_id


def test_active_database_is_not_released(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = manager.open_database(paths.sample, set_current=True)["instance_id"]

    clock.advance(IDLE_TIMEOUT - 1)
    manager.execute_python("db", instance_id)
    clock.advance(IDLE_TIMEOUT - 1)
    manager._tick()

    assert manager._released == {}


def test_operation_in_flight_blocks_release(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = manager.open_database(paths.sample, set_current=True)["instance_id"]
    handle = world.live[paths.sample]

    ticked = threading.Event()

    def slow_execute(code: str, **kwargs: Any) -> dict[str, Any]:
        # A long execution stamps the clock only at its edges, so the reaper
        # must respect the in-flight count instead.
        clock.advance(IDLE_TIMEOUT * 5)
        manager._tick()
        ticked.set()
        return {"result": code, "stdout": "", "stderr": ""}

    handle.execute_python = slow_execute  # type: ignore[method-assign]
    manager.execute_python("slow", instance_id)

    assert ticked.is_set()
    assert manager._released == {}
    assert not handle.closed


def test_running_autoanalysis_blocks_release(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    world.analysis_complete = False
    manager.open_database(paths.sample, set_current=True)

    clock.advance(IDLE_TIMEOUT + 1)
    manager._tick()
    assert manager._released == {}

    world.live[paths.sample].analysis_complete = True
    clock.advance(IDLE_TIMEOUT + 1)
    manager._tick()
    assert manager._released != {}


def test_released_database_is_still_listed_as_the_current_target(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    manager.open_database(paths.sample, set_current=True)
    before = manager.list_databases()

    clock.advance(IDLE_TIMEOUT + 1)
    manager._tick()

    assert manager.list_databases() == before


def test_reopening_a_released_path_returns_the_original_id(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = open_and_idle(manager, clock, paths.sample)

    result = manager.open_database(paths.sample, set_current=True)

    assert result["instance_id"] == instance_id
    assert len(manager.list_databases()["instances"]) == 1


def test_closing_a_released_database_does_not_reopen_it(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = open_and_idle(manager, clock, paths.sample)

    assert manager.close_database(instance_id) == {"closed": True}

    assert world.opens == [paths.sample]
    assert manager.list_databases()["instances"] == []
    with pytest.raises(DatabaseSelectionError):
        manager.resolve_instance_id(instance_id)


def test_cancelling_a_released_database_is_a_noop(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = open_and_idle(manager, clock, paths.sample)

    assert manager.cancel_operation(instance_id, "operation") is False
    assert manager.cancel_active(None) is False
    assert world.opens == [paths.sample]


def test_failed_reattach_leaves_the_database_recoverable(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = open_and_idle(manager, clock, paths.sample)
    world.open_error = RuntimeError("worker failed to start")

    with pytest.raises(RuntimeError, match="worker failed to start"):
        manager.execute_python("db", instance_id)

    world.open_error = None
    assert manager.execute_python("db", instance_id)["result"] == "db"


def test_reaper_parks_until_a_database_is_attached(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    assert manager._next_wait() is None

    manager.open_database(paths.sample, set_current=True)
    assert manager._next_wait() == pytest.approx(6.0)

    clock.advance(IDLE_TIMEOUT + 1)
    manager._tick()
    assert manager._next_wait() is None


def test_reaper_thread_starts_on_open_and_stops_on_shutdown(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    paths: SimpleNamespace,
) -> None:
    assert manager._reaper is None

    manager.open_database(paths.sample, set_current=True)
    reaper = manager._reaper
    assert reaper is not None and reaper.is_alive()

    manager.shutdown()
    reaper.join(timeout=5.0)
    assert not reaper.is_alive()
    assert not world.live


def test_shutdown_releases_released_bookkeeping(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    open_and_idle(manager, clock, paths.sample)

    manager.shutdown()

    assert manager._released == {}
    assert manager.list_databases()["instances"] == []


def test_idle_timeout_must_be_positive(world: FakeWorld) -> None:
    with pytest.raises(ValueError, match="idle_timeout"):
        IdleDatabaseManager(idle_timeout=0)


def test_manager_only_relies_on_documented_internals() -> None:
    # The subclass reaches into DatabaseManager; fail loudly here rather than
    # mysteriously at runtime if a future ida-nexus drops one of these.
    probe = DatabaseManager()
    for name in (
        "_get_session",
        "_instances",
        "_lock",
        "_current_instance_id",
        "_open_timeout",
        "_listing_path",
        "_emit",
    ):
        assert hasattr(probe, name), name


def test_call_during_a_release_waits_and_then_reattaches(
    manager: IdleDatabaseManager,
    world: FakeWorld,
    clock: FakeClock,
    paths: SimpleNamespace,
) -> None:
    instance_id = manager.open_database(paths.sample, set_current=True)["instance_id"]
    handle = world.live[paths.sample]
    finish_close = threading.Event()
    closing = threading.Event()
    original_close = handle.close

    def blocking_close(**kwargs: Any) -> None:
        closing.set()
        assert finish_close.wait(10)
        original_close(**kwargs)

    handle.close = blocking_close  # type: ignore[method-assign]
    clock.advance(IDLE_TIMEOUT + 1)

    releasing = threading.Thread(target=manager._tick)
    releasing.start()
    assert closing.wait(10)

    results: list[Any] = []
    caller = threading.Thread(
        target=lambda: results.append(manager.execute_python("db", instance_id))
    )
    caller.start()
    # The database is mid-release: the call must wait rather than see a
    # half-closed session or a missing instance.
    time.sleep(0.05)
    assert caller.is_alive()

    finish_close.set()
    releasing.join(10)
    caller.join(10)

    assert results == [{"result": "db", "stdout": "", "stderr": ""}]
    assert world.opens == [paths.sample, paths.sample]
    assert instance_id not in manager._released
