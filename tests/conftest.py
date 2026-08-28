"""Shared fixtures for the IDA-backed test suite.

These tests drive a real IDA through ida-nexus: they spawn idalib workers, kill
them, and inspect what is left on disk. Nothing here is faked or mocked, so the
suite needs an installed, licensed IDA 9.4+ (see .github/workflows/tests.yml).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"
SUPPORT_DIR = Path(__file__).parent / "support"

#: Components IDA writes next to the input while a database is open. A clean
#: close packs them into ``<name>.i64`` and removes them.
UNPACKED_SUFFIXES = (".id0", ".id1", ".id2", ".nam", ".til")

# ida-nexus resolves its state directory when its modules are imported, so the
# redirection has to happen before any test imports it.
_STATE_DIR = Path(tempfile.mkdtemp(prefix="ida-mcp-tests-"))
os.environ["IDA_NEXUS_STATE_DIR"] = str(_STATE_DIR)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    shutil.rmtree(_STATE_DIR, ignore_errors=True)


def unpacked_components(target: Path) -> list[Path]:
    """Return the unpacked database components present next to ``target``."""
    return [
        path
        for suffix in UNPACKED_SUFFIXES
        if (path := target.with_suffix(target.suffix + suffix)).exists()
    ]


def packed_database(target: Path) -> Path:
    """The packed database ida-nexus expects for ``target``."""
    return target.with_suffix(target.suffix + ".i64")


def hard_kill(pid: int) -> None:
    """Terminate a process without giving it a chance to close its database."""
    # Python maps os.kill() to TerminateProcess on Windows, so SIGTERM there is
    # as abrupt as SIGKILL is on POSIX.
    os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)


def process_is_running(pid: int) -> bool:
    """Whether ``pid`` is still executing.

    A worker spawned by the resolver is a child of the test process, so after a
    kill it stays a zombie until someone reaps it -- and its database locks are
    still held at that point. Reap it, then treat the process as gone only once
    the OS no longer lists it at all.
    """
    if os.name != "nt":
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
        return bool(
            subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)],
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_process_gone(pid: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"process {pid} is still running after {timeout}s")


def wait_for(predicate, timeout: float = 60.0, interval: float = 0.5) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


RENAME = """
import ida_funcs, ida_name
ea = ida_funcs.get_func_ea_by_num({index})
ida_name.set_name(ea, {name!r}, ida_name.SN_FORCE)
"""

READ_NAME = """
import ida_funcs, ida_name
ida_name.get_name(ida_funcs.get_func_ea_by_num({index}))
"""


def rename_function(handle, name: str, index: int = 0) -> None:
    result = handle.execute_python(RENAME.format(index=index, name=name))
    assert result["result"] is True, result


def function_name(handle, index: int = 0) -> str:
    return handle.execute_python(READ_NAME.format(index=index))["result"]


class WorkerTracker:
    """Opens Nexus handles and guarantees the workers die with the test."""

    def __init__(self) -> None:
        self._pids: set[int] = set()

    def open(self, target: Path, **kwargs):
        from ida_nexus import DatabaseHandle

        handle = DatabaseHandle.open(str(target), **kwargs)
        self._pids.add(handle.instance.pid)
        return handle

    def track(self, pid: int) -> None:
        self._pids.add(pid)

    def cleanup(self) -> None:
        for pid in self._pids:
            try:
                hard_kill(pid)
            except OSError:
                continue


@pytest.fixture
def workers() -> Iterator[WorkerTracker]:
    tracker = WorkerTracker()
    yield tracker
    tracker.cleanup()


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A private copy of the sample executable, with no database beside it."""
    destination = tmp_path / "crackme03.elf"
    shutil.copy(DATA_DIR / "crackme03.elf", destination)
    return destination


class ForeignIDA:
    """A licensed IDA that ida-nexus knows nothing about, holding a database.

    This stands in for the common real-world case: a GUI without the ida-mcp
    plugin, or any other idalib program, working on the same target.
    """

    def __init__(self, process: subprocess.Popen[str], mark: str) -> None:
        self.process = process
        self.mark = mark

    @property
    def pid(self) -> int:
        return self.process.pid

    def close(self, timeout: float = 120.0) -> None:
        """Ask for a clean, saving close and wait for the process to exit."""
        assert self.process.stdin is not None
        self.process.stdin.write("close\n")
        self.process.stdin.flush()
        self.process.wait(timeout=timeout)


@pytest.fixture
def foreign_ida(workers: WorkerTracker):
    processes: list[subprocess.Popen[str]] = []

    def start(target: Path, mark: str = "FOREIGN_MARK") -> ForeignIDA:
        process = subprocess.Popen(
            [sys.executable, str(SUPPORT_DIR / "hold_database.py"), str(target), mark],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append(process)
        workers.track(process.pid)
        assert process.stdout is not None
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                raise AssertionError("foreign IDA exited before opening the database")
            if line.startswith("READY"):
                return ForeignIDA(process, mark)
        raise AssertionError("foreign IDA did not report READY in time")

    yield start

    for process in processes:
        if process.poll() is None:
            process.kill()
