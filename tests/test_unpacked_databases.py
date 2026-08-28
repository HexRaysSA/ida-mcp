"""How ida-mcp behaves when a worker dies with its database still unpacked.

Every test here drives a real IDA. The scenarios follow HexRaysSA/ida-nexus#46:
a worker that is OOM-killed, segfaults, or loses its machine leaves the
database unpacked on disk, and the next open has to do something sensible with
what it finds.

Tests marked ``xfail(strict=True)`` state the behavior we want and do not have
yet; when ida-nexus ships the fix they turn into unexpected passes, which is
the signal to drop the marker.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from conftest import (
    ForeignIDA,
    WorkerTracker,
    function_name,
    hard_kill,
    packed_database,
    rename_function,
    unpacked_components,
    wait_for,
    wait_process_gone,
)

NEXUS_46 = "HexRaysSA/ida-nexus#46: unpacked databases are discarded, not recovered"

#: Generous enough for a cold IDA start on a loaded CI runner.
ANALYSIS_TIMEOUT = 300.0


def test_a_live_worker_keeps_the_database_unpacked(
    target: Path, workers: WorkerTracker
) -> None:
    """The precondition for everything else: open databases live unpacked."""
    handle = workers.open(target)
    handle.wait_autoanalysis(timeout=ANALYSIS_TIMEOUT)

    assert unpacked_components(target), "expected .id0/.id1/... beside the input"
    assert not packed_database(target).exists()


def test_clean_shutdown_packs_the_database(
    target: Path, workers: WorkerTracker
) -> None:
    handle = workers.open(target)
    handle.wait_autoanalysis(timeout=ANALYSIS_TIMEOUT)
    rename_function(handle, "CLEAN_SHUTDOWN_MARK")

    # Releasing the last lease is what an MCP client does at the end of a
    # session: the managed worker saves and closes the database itself.
    handle.close(wait_for_database=True)

    assert packed_database(target).exists()
    assert wait_for(lambda: not unpacked_components(target), timeout=60.0), (
        f"clean shutdown left {unpacked_components(target)} behind"
    )

    reopened = workers.open(target)
    assert function_name(reopened) == "CLEAN_SHUTDOWN_MARK"


def test_a_killed_worker_leaves_the_database_unpacked(
    target: Path, workers: WorkerTracker
) -> None:
    handle = workers.open(target)
    handle.wait_autoanalysis(timeout=ANALYSIS_TIMEOUT)
    pid = handle.instance.pid

    hard_kill(pid)
    wait_process_gone(pid)

    assert unpacked_components(target)


def test_reopen_after_a_saved_crash_keeps_the_saved_work(
    target: Path, workers: WorkerTracker
) -> None:
    """A save before the crash must survive it.

    IDA restores the packed base by itself here, so this holds today; it is the
    invariant a recovery policy must not regress.
    """
    handle = workers.open(target)
    handle.wait_autoanalysis(timeout=ANALYSIS_TIMEOUT)
    rename_function(handle, "SAVED_MARK")
    assert handle.save_database()["saved"] is True
    assert packed_database(target).exists()

    pid = handle.instance.pid
    hard_kill(pid)
    wait_process_gone(pid)

    reopened = workers.open(target)
    assert function_name(reopened) == "SAVED_MARK"


@pytest.mark.xfail(strict=True, reason=NEXUS_46)
def test_reopen_after_an_unsaved_crash_keeps_the_work(
    target: Path, workers: WorkerTracker
) -> None:
    """Work done before a crash must survive even without an explicit save.

    Today the worker neither flushes after execution nor recovers the unpacked
    database: the reopen is spawned with ``-c -o`` and silently re-analyzes the
    executable from scratch.
    """
    handle = workers.open(target)
    handle.wait_autoanalysis(timeout=ANALYSIS_TIMEOUT)
    rename_function(handle, "UNSAVED_MARK")

    pid = handle.instance.pid
    hard_kill(pid)
    wait_process_gone(pid)

    reopened = workers.open(target)
    assert function_name(reopened) == "UNSAVED_MARK"


@pytest.mark.xfail(strict=True, reason=NEXUS_46)
def test_open_refuses_to_clobber_a_live_foreign_ida(
    target: Path, workers: WorkerTracker, foreign_ida
) -> None:
    """A database another IDA is using must be left alone.

    Today the worker is spawned with ``-c``, which takes the files over while
    the other process is still using them; that session then closes into files
    that no longer exist and loses its database entirely.
    """
    from ida_nexus import DatabaseBusyError

    foreign: ForeignIDA = foreign_ida(target)
    assert unpacked_components(target)

    with pytest.raises(DatabaseBusyError):
        workers.open(target)

    foreign.close()
    assert packed_database(target).exists(), "the live session lost its database"

    reopened = workers.open(target)
    assert function_name(reopened) == foreign.mark


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory locks; Windows uses sharing modes")
def test_a_live_unpacked_database_is_locked(
    target: Path, workers: WorkerTracker
) -> None:
    """The liveness signal a recovery policy can rely on.

    IDA holds an exclusive advisory lock on .id0 for as long as the database is
    open, and the kernel releases it when the owner dies. That distinguishes
    "another IDA is working here" from "an IDA died here" for any IDA, whether
    or not it is registered with Nexus.
    """
    import fcntl

    handle = workers.open(target)
    handle.wait_autoanalysis(timeout=ANALYSIS_TIMEOUT)
    id0 = target.with_suffix(target.suffix + ".id0")

    def try_lock() -> bool:
        fd = os.open(id0, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    assert not try_lock(), "a live database should not be lockable by anyone else"

    pid = handle.instance.pid
    hard_kill(pid)
    wait_process_gone(pid)

    assert try_lock(), "a dead owner's lock should have been released"


@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason=NEXUS_46)
def test_work_is_saved_without_a_clean_shutdown(
    target: Path, workers: WorkerTracker
) -> None:
    """A debounced save must bound how much a crash can cost.

    The worker should pack the database about a minute after the last change,
    so a crash never costs more than that minute of work.
    """
    handle = workers.open(target)
    handle.wait_autoanalysis(timeout=ANALYSIS_TIMEOUT)
    rename_function(handle, "DEBOUNCED_MARK")

    packed = packed_database(target)
    assert wait_for(packed.exists, timeout=120.0, interval=2.0), (
        "no packed database appeared within two minutes of a change"
    )
    time.sleep(2.0)

    pid = handle.instance.pid
    hard_kill(pid)
    wait_process_gone(pid)

    reopened = workers.open(target)
    assert function_name(reopened) == "DEBOUNCED_MARK"
