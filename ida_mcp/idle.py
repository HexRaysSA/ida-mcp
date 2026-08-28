"""Release idle IDA Nexus database leases from the MCP server.

An MCP server holds one IDA Nexus lease per database it has opened, and a
managed idalib worker stays alive for as long as any lease exists. An agent
that opens a database and never closes it therefore pins an IDA process, and
its IDB, for the lifetime of the agent session.

Two things follow from that. The machine running the MCP server can never be
suspended while an agent is merely idle, and an unpacked IDB stays open on
disk far longer than the work needs it, so a crash or a host shutdown has a
much wider window in which to leave that database behind.

Lease lifetime is coordinated here rather than inside the worker: a worker is
shared by any number of clients and cannot tell which of them are still doing
useful work. The MCP server can, because every tool call passes through it.

``IdleDatabaseManager`` releases the lease on any idalib database that has not
been used for ``idle_timeout`` seconds. The worker then saves and closes its
IDB and exits on its own, exactly as it does for an explicit
``close_database()``. The next tool call that names that database reattaches
it under the *same* MCP-local ``instance_id``, so agents never observe the
release: released databases keep their identity, keep appearing in
``list_databases()``, and keep accepting operations.

GUI databases are never released. A GUI instance is not kept alive by leases,
so releasing one frees nothing and only costs a reattach later.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ida_nexus import (
    CloseDatabaseResult,
    DatabaseListing,
    DatabaseManager,
    ListDatabasesResult,
    OpenDatabaseResult,
    PythonExecutionResult,
    SaveDatabaseResult,
    WaitAutoanalysisResult,
)

DEFAULT_IDLE_TIMEOUT_SECONDS = 1800.0
MIN_TICK_SECONDS = 5.0
MAX_TICK_SECONDS = 60.0
TICK_DIVISOR = 10.0

# Ordering used by DatabaseManager.list_databases(); reapplied after released
# databases are merged back into the listing.
_STATUS_ORDER = {"current": 0, "attached": 1, "available": 2, "unavailable": 3}


@dataclass
class _ReleasedDatabase:
    """A database whose lease was released, presented to agents as attached."""

    instance_id: str
    requested_path: str
    listing_path: str
    backend: str
    was_current: bool
    released_at: float


class IdleDatabaseManager(DatabaseManager):
    """A database manager that releases leases agents have stopped using.

    The release is invisible to MCP clients. ``instance_id`` values survive it,
    so an agent that comes back to a database after an hour simply waits a
    little longer for its next tool call while the worker is reopened.
    """

    def __init__(
        self,
        *,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        time_source: Callable[[], float] = time.monotonic,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if (
            isinstance(idle_timeout, bool)
            or not isinstance(idle_timeout, (int, float))
            or not math.isfinite(idle_timeout)
            or idle_timeout <= 0
        ):
            raise ValueError("idle_timeout must be a positive finite number")
        self._idle_timeout = float(idle_timeout)
        self._now = time_source
        self._idle_lock = threading.RLock()
        self._last_touch: dict[str, float] = {}
        self._inflight: dict[str, int] = {}
        self._released: dict[str, _ReleasedDatabase] = {}
        self._releasing: dict[str, threading.Event] = {}
        # The thread performing a release passes straight through
        # _ensure_attached(): its own close_database() call resolves the
        # session it is about to close.
        self._releasing_threads: dict[str, int] = {}
        self._reattach_locks: dict[str, threading.Lock] = {}
        # Instance ids agents have been given. Anything else is an internal id
        # that exists only between a reattaching open and its rename.
        self._known_ids: set[str] = set()
        self._client_current: str | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None

    # -- activity bookkeeping ------------------------------------------------

    def _touch(self, instance_id: str) -> None:
        with self._idle_lock:
            self._last_touch[instance_id] = self._now()

    def _forget(self, instance_id: str) -> None:
        with self._idle_lock:
            self._last_touch.pop(instance_id, None)
            self._inflight.pop(instance_id, None)
            self._released.pop(instance_id, None)
            self._reattach_locks.pop(instance_id, None)
            self._known_ids.discard(instance_id)
            if self._client_current == instance_id:
                self._client_current = None

    def _note_current(self, instance_id: str, *, set_current: bool) -> None:
        with self._idle_lock:
            self._known_ids.add(instance_id)
            if set_current or self._client_current is None:
                self._client_current = instance_id

    def _peek_current(self) -> str | None:
        with self._idle_lock:
            return self._client_current

    @contextmanager
    def _busy(self, instance_id: str | None) -> Iterator[str]:
        """Resolve a target and mark it in use for the length of an operation.

        A long execution is activity even though it stamps the clock only at
        its start and end, so the reaper must never release a database with an
        operation in flight.
        """
        target_id, _ = self._get_session(instance_id)
        with self._idle_lock:
            self._inflight[target_id] = self._inflight.get(target_id, 0) + 1
        try:
            yield target_id
        finally:
            with self._idle_lock:
                remaining = self._inflight.get(target_id, 1) - 1
                if remaining > 0:
                    self._inflight[target_id] = remaining
                else:
                    self._inflight.pop(target_id, None)
                self._last_touch[target_id] = self._now()

    # -- release and reattach ------------------------------------------------

    def _reattach_lock(self, instance_id: str) -> threading.Lock:
        with self._idle_lock:
            lock = self._reattach_locks.get(instance_id)
            if lock is None:
                lock = threading.Lock()
                self._reattach_locks[instance_id] = lock
            return lock

    def _adopt(self, new_id: str, instance_id: str) -> None:
        """Rebind a freshly opened session under its original instance id."""
        if new_id == instance_id:
            return
        with self._lock:
            session = self._instances.pop(new_id, None)
            if session is None:
                return
            session.instance_id = instance_id
            self._instances[instance_id] = session
            if self._current_instance_id == new_id:
                self._current_instance_id = instance_id
        with self._idle_lock:
            self._last_touch.pop(new_id, None)
            self._known_ids.discard(new_id)
            self._last_touch[instance_id] = self._now()
            self._known_ids.add(instance_id)

    def _reattach(self, released: _ReleasedDatabase) -> None:
        instance_id = released.instance_id
        try:
            result = super().open_database(
                released.requested_path,
                set_current=released.was_current,
            )
        except BaseException:
            # Leave the database released so a later call can try again. The
            # agent sees the underlying open failure, which is the same error
            # it would have seen from open_database().
            with self._idle_lock:
                self._released.setdefault(instance_id, released)
            raise
        self._adopt(result["instance_id"], instance_id)
        # open_database() may have chosen its own current target while this
        # database had none; the agent's current target is authoritative.
        if self._peek_current() == instance_id:
            with self._lock:
                if instance_id in self._instances:
                    self._current_instance_id = instance_id
        self._emit(
            "database_reattached",
            instance_id=instance_id,
            path=released.requested_path,
            released_seconds=round(self._now() - released.released_at, 3),
        )

    def _ensure_attached(self, instance_id: str) -> None:
        """Wait out an in-progress release, then reattach if one happened."""
        while True:
            with self._idle_lock:
                releasing = self._releasing.get(instance_id)
                owner = self._releasing_threads.get(instance_id)
                released = self._released.get(instance_id)
            if releasing is not None:
                if owner == threading.get_ident():
                    return
                releasing.wait(self._open_timeout)
                continue
            if released is None:
                return
            with self._reattach_lock(instance_id):
                with self._idle_lock:
                    pending = self._released.pop(instance_id, None)
                if pending is None:
                    # Another thread reattached it while we waited.
                    continue
                self._reattach(pending)
            return

    def _release(self, instance_id: str, idle_for: float) -> None:
        """Release one idle lease and remember how to bring it back."""
        with self._lock:
            session = self._instances.get(instance_id)
            if session is None:
                return
            entry = session.handle.instance
            requested_path = session.requested_path
            was_current = self._current_instance_id == instance_id
        released = _ReleasedDatabase(
            instance_id=instance_id,
            requested_path=requested_path,
            listing_path=self._listing_path(entry),
            backend=entry.backend,
            was_current=was_current,
            released_at=self._now(),
        )
        done = threading.Event()
        with self._idle_lock:
            if instance_id in self._releasing or instance_id in self._released:
                return
            self._releasing[instance_id] = done
            self._releasing_threads[instance_id] = threading.get_ident()
        try:
            # close_database() drops the lease and waits for the worker to save
            # and close its IDB, so a reattach cannot race a draining worker.
            super().close_database(instance_id)
        except Exception as error:  # noqa: BLE001 - the lease is gone either way
            self._emit(
                "database_idle_release_error",
                instance_id=instance_id,
                error=error,
            )
        finally:
            with self._idle_lock:
                # An agent that closed this database while the release was in
                # flight has already given up its id; do not resurrect it.
                if instance_id in self._known_ids:
                    self._released[instance_id] = released
                self._releasing.pop(instance_id, None)
                self._releasing_threads.pop(instance_id, None)
                self._inflight.pop(instance_id, None)
                self._last_touch.pop(instance_id, None)
            done.set()
        self._emit(
            "database_idle_released",
            instance_id=instance_id,
            path=requested_path,
            idle_seconds=round(idle_for, 3),
            idle_timeout=self._idle_timeout,
        )

    # -- reaper --------------------------------------------------------------

    def _tick(self) -> None:
        """Release every lease that has been idle past the timeout."""
        now = self._now()
        with self._lock:
            sessions = list(self._instances.values())
        live_ids = {session.instance_id for session in sessions}
        with self._idle_lock:
            for stale in set(self._last_touch) - live_ids:
                if stale not in self._released and stale not in self._releasing:
                    self._last_touch.pop(stale, None)
            busy = {
                instance_id
                for instance_id, count in self._inflight.items()
                if count > 0
            }
            touches = dict(self._last_touch)
            pending = set(self._releasing) | set(self._released)

        for session in sessions:
            instance_id = session.instance_id
            if instance_id in busy or instance_id in pending:
                continue
            handle = session.handle
            # A GUI database is not kept alive by our lease; releasing it frees
            # nothing and only makes the next call slower.
            if handle.instance.backend != "idalib" or not handle.connected:
                continue
            idle_for = now - touches.get(instance_id, now)
            if idle_for < self._idle_timeout:
                continue
            if not self._analysis_settled(session):
                self._touch(instance_id)
                continue
            self._release(instance_id, idle_for)

    def _analysis_settled(self, session: Any) -> bool:
        """Report whether initial autoanalysis has finished.

        Releasing during the first analysis would throw away work the agent is
        about to ask for, so an analysing database is treated as active.
        """
        if session.autoanalysis_complete:
            return True
        try:
            complete = bool(session.handle.poll_autoanalysis()["complete"])
        except Exception:  # noqa: BLE001 - an unreachable worker is not ours to reap
            return False
        if complete:
            session.autoanalysis_complete = True
        return complete

    def _next_wait(self) -> float | None:
        """Seconds until the next tick, or None to sleep until woken.

        With nothing attached there is nothing to reap, so the reaper parks
        instead of waking periodically and the host can suspend cleanly.
        """
        with self._lock:
            if not self._instances:
                return None
        return min(
            max(self._idle_timeout / TICK_DIVISOR, MIN_TICK_SECONDS), MAX_TICK_SECONDS
        )

    def _reap_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._next_wait())
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self._tick()
            except Exception as error:  # noqa: BLE001 - never kill the reaper
                print(f"idle database release failed: {error}", file=sys.stderr)

    def _start_reaper(self) -> None:
        with self._idle_lock:
            if self._stop.is_set():
                return
            if self._reaper is None:
                self._reaper = threading.Thread(
                    target=self._reap_loop,
                    name="ida-mcp-idle-release",
                    daemon=True,
                )
                self._reaper.start()
        self._wake.set()

    # -- DatabaseManager surface ---------------------------------------------

    def _get_session(self, instance_id: str | None) -> tuple[str, Any]:
        wanted = instance_id if instance_id is not None else self._peek_current()
        if wanted is not None:
            self._ensure_attached(wanted)
        target = instance_id
        if target is None and wanted is not None:
            # Name the agent's current target explicitly: reattaching it may
            # have left the base manager pointing at a different database.
            with self._lock:
                if wanted in self._instances:
                    target = wanted
        target_id, session = super()._get_session(target)
        self._touch(target_id)
        return target_id, session

    def open_database(self, path: str, *, set_current: bool) -> OpenDatabaseResult:
        result = super().open_database(path, set_current=set_current)
        instance_id = result["instance_id"]
        # Reopening a released database must return the id the agent already
        # has, so match the canonical path the base manager recorded for it.
        with self._lock:
            session = self._instances.get(instance_id)
            requested_path = session.requested_path if session is not None else None
        if requested_path is not None:
            with self._idle_lock:
                previous = next(
                    (
                        released
                        for released in self._released.values()
                        if released.requested_path == requested_path
                    ),
                    None,
                )
                if previous is not None:
                    self._released.pop(previous.instance_id, None)
            if previous is not None:
                self._adopt(instance_id, previous.instance_id)
                instance_id = previous.instance_id
                result = OpenDatabaseResult(
                    instance_id=instance_id,
                    backend=result["backend"],
                    status=result["status"],
                )
        self._note_current(instance_id, set_current=set_current)
        self._touch(instance_id)
        self._start_reaper()
        return result

    def execute_python(
        self,
        code: str,
        instance_id: str | None,
        timeout: float | None = None,
        *,
        operation_id: str | None = None,
        operation_label: str | None = None,
        persist_globals: bool = False,
        filename: str | None = None,
    ) -> PythonExecutionResult:
        with self._busy(instance_id) as target_id:
            return super().execute_python(
                code,
                target_id,
                timeout,
                operation_id=operation_id,
                operation_label=operation_label,
                persist_globals=persist_globals,
                filename=filename,
            )

    def wait_autoanalysis(
        self,
        instance_id: str | None,
        timeout: float | None = None,
        *,
        operation_id: str | None = None,
    ) -> WaitAutoanalysisResult:
        with self._busy(instance_id) as target_id:
            return super().wait_autoanalysis(
                target_id,
                timeout,
                operation_id=operation_id,
            )

    def save_database(self, instance_id: str | None) -> SaveDatabaseResult:
        with self._busy(instance_id) as target_id:
            return super().save_database(target_id)

    def cancel_operation(self, instance_id: str, operation_id: str) -> bool:
        if self._is_released(instance_id):
            return False
        return super().cancel_operation(instance_id, operation_id)

    def cancel_active(self, instance_id: str | None) -> bool:
        if self._is_released(instance_id if instance_id else self._peek_current()):
            return False
        return super().cancel_active(instance_id)

    def _is_released(self, instance_id: str | None) -> bool:
        """Report whether a target has no lease, without reattaching it.

        Cancelling an operation on a database whose worker is gone is a no-op;
        reopening it to say so would be absurd.
        """
        if instance_id is None:
            return False
        with self._idle_lock:
            return instance_id in self._released or instance_id in self._releasing

    def close_database(self, instance_id: str | None) -> CloseDatabaseResult:
        wanted = instance_id if instance_id is not None else self._peek_current()
        if wanted is not None:
            with self._idle_lock:
                releasing = self._releasing.get(wanted)
            if releasing is not None:
                releasing.wait(self._open_timeout)
            with self._idle_lock:
                released = self._released.get(wanted)
            if released is not None:
                # The lease is already gone and the IDB is already closed.
                # Reopening a worker just to close it would be worse than
                # pointless.
                self._forget(wanted)
                return CloseDatabaseResult(closed=True)
        target_id, _ = self._get_session(instance_id)
        try:
            return super().close_database(target_id)
        finally:
            self._forget(target_id)

    def list_databases(self) -> ListDatabasesResult:
        result = super().list_databases()
        with self._idle_lock:
            released = list(self._released.values())
            current = self._client_current
            known = set(self._known_ids)
        entries = list(result["instances"])
        for entry in entries:
            # An id minted by a reattaching open is internal until it is
            # renamed. Never show one to an agent.
            if entry["instance_id"] is not None and entry["instance_id"] not in known:
                entry["instance_id"] = None
                entry["status"] = "available"
        for item in released:
            status = "current" if item.instance_id == current else "attached"
            match = next(
                (
                    entry
                    for entry in entries
                    if entry["path"] == item.listing_path
                    and entry["instance_id"] is None
                ),
                None,
            )
            if match is not None:
                match["instance_id"] = item.instance_id
                match["status"] = status
                match["error"] = None
                continue
            entries.append(
                DatabaseListing(
                    path=item.listing_path,
                    backend=item.backend,
                    status=status,
                    instance_id=item.instance_id,
                    error=None,
                )
            )
        entries.sort(
            key=lambda item: (
                _STATUS_ORDER[item["status"]],
                item["backend"] != "gui",
                item["path"],
            )
        )
        return {"instances": entries}

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        reaper = self._reaper
        if reaper is not None and reaper is not threading.current_thread():
            reaper.join(timeout=5.0)
        with self._idle_lock:
            self._released.clear()
            self._releasing.clear()
            self._releasing_threads.clear()
            self._last_touch.clear()
            self._inflight.clear()
            self._known_ids.clear()
            self._client_current = None
        super().shutdown()


_REQUIRED_INSTANCE_ATTRIBUTES = (
    "_get_session",
    "_instances",
    "_lock",
    "_current_instance_id",
    "_open_timeout",
    "_listing_path",
    "_emit",
)


def _nexus_supports_idle_release() -> str | None:
    """Return None when this ida-nexus exposes what the subclass needs."""
    try:
        probe = DatabaseManager()
    except Exception as error:  # noqa: BLE001 - report, never fail startup
        return f"DatabaseManager() could not be constructed: {error}"
    missing = [
        name for name in _REQUIRED_INSTANCE_ATTRIBUTES if not hasattr(probe, name)
    ]
    if missing:
        return f"ida-nexus DatabaseManager is missing {', '.join(missing)}"
    return None


def install_idle_release(idle_timeout: float) -> bool:
    """Install idle lease release into the ida-nexus MCP server.

    The MCP tools resolve ``DATABASE_MANAGER`` from their module at call time,
    so replacing it before the server starts is enough. Returns whether idle
    release is active; an ida-nexus that no longer offers the seam this
    subclass needs disables the feature instead of breaking the server.
    """
    if idle_timeout <= 0:
        return False

    from ida_nexus.cli import mcp as nexus_mcp

    problem = _nexus_supports_idle_release()
    if problem is not None:
        print(
            f"WARNING: idle database release is disabled: {problem}",
            file=sys.stderr,
        )
        return False

    try:
        manager = IdleDatabaseManager(
            idle_timeout=idle_timeout,
            on_event=nexus_mcp._trace_database_event,
            open_timeout=nexus_mcp.OPEN_TIMEOUT_SECONDS,
            execute_timeout=nexus_mcp.EXECUTE_TIMEOUT_SECONDS,
        )
    except Exception as error:  # noqa: BLE001 - report, never fail startup
        print(
            f"WARNING: idle database release is disabled: {error}",
            file=sys.stderr,
        )
        return False

    nexus_mcp.DATABASE_MANAGER = manager
    return True
