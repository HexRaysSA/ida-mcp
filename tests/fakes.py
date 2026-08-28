"""A fake Nexus transport: no IDA, no workers, and a hand-driven clock."""

from __future__ import annotations

import itertools
from typing import Any

from ida_nexus._registry import DatabaseInstance

IDLE_TIMEOUT = 60.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeHandle:
    """The parts of DatabaseHandle that DatabaseManager actually uses."""

    def __init__(self, world: FakeWorld, path: str, record_id: str) -> None:
        self._world = world
        self.requested_path = path
        self.connected = True
        self.disconnect_reason: str | None = None
        self.closed = False
        self.close_calls: list[bool] = []
        self.analysis_complete = world.analysis_complete
        self.instance = DatabaseInstance(
            record_id=record_id,
            backend=world.backend_for(path),
            pid=4242,
            port=1024,
            _token="token",
            version=7,
            idb_path=f"{path}.i64",
            idb_key=f"{abs(hash(path)):016x}"[:16],
            exe_path="",
            managed=True,
            started_at=0.0,
        )
        self.executed: list[str] = []

    def set_disconnect_callback(self, callback: Any) -> None:
        self._disconnect = callback

    def close(self, *, wait_for_database: bool = False, timeout: float = 0.0) -> None:
        self.closed = True
        self.connected = False
        self.close_calls.append(wait_for_database)
        self._world.live.pop(self.requested_path, None)

    def execute_python(self, code: str, **kwargs: Any) -> dict[str, Any]:
        self.executed.append(code)
        return {"result": code, "stdout": "", "stderr": ""}

    def save_database(self) -> dict[str, Any]:
        return {"saved": True, "idb_path": self.instance.idb_path}

    def poll_autoanalysis(self) -> dict[str, Any]:
        return {
            "status": "complete" if self.analysis_complete else "running",
            "complete": self.analysis_complete,
        }

    def wait_autoanalysis(self, timeout: Any = None, **kwargs: Any) -> dict[str, Any]:
        return self.poll_autoanalysis()

    def cancel_operation(self, operation_id: str) -> bool:
        return True

    def cancel_active(self) -> bool:
        return True


class FakeWorld:
    """A stand-in registry: one live handle per path, spawned on demand."""

    def __init__(self) -> None:
        self.live: dict[str, FakeHandle] = {}
        self.opens: list[str] = []
        self.record_ids = itertools.count(1)
        self.gui_paths: set[str] = set()
        self.analysis_complete = True
        self.open_error: Exception | None = None

    def backend_for(self, path: str) -> str:
        return "gui" if path in self.gui_paths else "idalib"

    def open(self, path: str, *, options: Any = None, **kwargs: Any) -> FakeHandle:
        self.opens.append(path)
        if self.open_error is not None:
            raise self.open_error
        handle = FakeHandle(self, path, f"{next(self.record_ids)}-abcdef")
        self.live[path] = handle
        return handle
