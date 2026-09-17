import asyncio
import json
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from ida_nexus.manager import DatabaseManager

from ida_mcp import cli as mcp_cli
from ida_mcp import mcp as mcp_api


def test_programmatic_http_server_builds_manager_and_uses_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class HostManager(DatabaseManager):
        def __init__(self, host_name: str, **kwargs: object) -> None:
            created.update(kwargs)
            created["host_name"] = host_name
            super().__init__(**kwargs)

    def serve_with_prefix(
        _host: str,
        _port: int,
        *,
        path_prefix: str,
    ) -> None:
        monkeypatch.setattr(mcp_api.mcp, "path_prefix", path_prefix.rstrip("/"))

    serve = Mock(side_effect=serve_with_prefix)
    stop = Mock()
    shutdown = Mock()
    trace = Mock()

    def hub_status() -> str:
        """Return the embedded hub status."""
        return "ready"

    monkeypatch.setattr(mcp_api, "DATABASE_MANAGER", DatabaseManager())
    monkeypatch.setattr(mcp_api.mcp, "serve", serve)
    monkeypatch.setattr(mcp_api.mcp, "stop", stop)
    monkeypatch.setattr(mcp_api, "_shutdown_server_state", shutdown)
    monkeypatch.setattr(mcp_api, "_start_mcp_trace", trace)
    monkeypatch.setattr(mcp_api, "_HTTP_SERVER_STARTED", False)

    mcp_api.tool(hub_status)
    try:
        mcp_api.serve_http(
            "127.0.0.1",
            18737,
            database_manager_class=HostManager,
            database_manager_kwargs={"host_name": "test hub"},
            agent="test hub",
            path_prefix="/hex-rays/",
        )
    finally:
        mcp_api.mcp.tools.methods.pop("hub_status", None)

    assert created == {
        "host_name": "test hub",
        "on_event": mcp_api._trace_database_event,
    }
    assert isinstance(mcp_api.DATABASE_MANAGER, HostManager)
    serve.assert_called_once_with(
        "127.0.0.1",
        18737,
        path_prefix="/hex-rays/",
    )
    trace.assert_called_once_with(
        "http://127.0.0.1:18737/hex-rays/mcp",
        "test hub",
    )

    mcp_api.stop_http_server()
    stop.assert_called_once_with()
    shutdown.assert_called_once_with()


@pytest.mark.parametrize(
    ("host", "port"),
    [("", 18737), ("127.0.0.1", 0), ("127.0.0.1", 65536)],
)
def test_programmatic_http_server_rejects_invalid_address(host: str, port: int) -> None:
    with pytest.raises(ValueError):
        mcp_api.serve_http(host, port)


@pytest.mark.parametrize(
    ("environment_value", "expected"),
    [(None, None), ("0", None), ("45", 45.0)],
)
def test_mcp_cli_idle_timeout_environment_default(
    monkeypatch: pytest.MonkeyPatch,
    environment_value: str | None,
    expected: float | None,
) -> None:
    if environment_value is None:
        monkeypatch.delenv(
            mcp_api.MCP_IDLE_TIMEOUT_ENVIRONMENT_VARIABLE,
            raising=False,
        )
    else:
        monkeypatch.setenv(
            mcp_api.MCP_IDLE_TIMEOUT_ENVIRONMENT_VARIABLE,
            environment_value,
        )
    serve = Mock()
    monkeypatch.setattr(mcp_api, "serve_stdio", serve)

    assert mcp_cli.main(["stdio"]) == 0
    serve.assert_called_once_with(
        database=None,
        agent=None,
        idle_timeout=expected,
    )


def test_mcp_cli_argument_overrides_idle_timeout_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(mcp_api.MCP_IDLE_TIMEOUT_ENVIRONMENT_VARIABLE, "45")
    serve = Mock()
    monkeypatch.setattr(mcp_api, "serve_stdio", serve)

    assert mcp_cli.main(["stdio", "--idle-timeout", "120"]) == 0
    assert serve.call_args.kwargs["idle_timeout"] == 120.0


def test_open_database_recovery_output_schema_is_string() -> None:
    tool = next(
        tool
        for tool in mcp_api.mcp._mcp_tools_list()["tools"]
        if tool["name"] == "open_database"
    )

    assert tool["outputSchema"]["properties"]["recovery"]["type"] == "string"


def test_tool_rejects_builtin_name_collision() -> None:
    def open_database() -> None:
        pass

    with pytest.raises(ValueError, match="open_database"):
        mcp_api.tool(open_database)


def test_mcp_unsets_empty_forwarded_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("IDA_MCP_ID", "")
    monkeypatch.setenv("IDAUSR", "/tmp/ida-user")
    monkeypatch.setenv("IDA_NEXUS_STATE_DIR", "")

    mcp_api._unset_empty_environment_variables()

    assert "IDA_MCP_ID" not in mcp_api.os.environ
    assert mcp_api.os.environ["IDAUSR"] == "/tmp/ida-user"
    assert "IDA_NEXUS_STATE_DIR" not in mcp_api.os.environ


def test_mcp_gui_plugin_requires_current_or_newer_version(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "ida-nexus"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "ida_nexus_plugin.py").touch()
    manifest = plugin_dir / "ida-plugin.json"

    cases = {
        "1.2.2": False,
        "1.2.3-dev.1": False,
        "1.2.3-dev.2": True,
        "1.2.3": True,
        "1.3.0": True,
    }
    for plugin_version, expected in cases.items():
        manifest.write_text(
            json.dumps({"plugin": {"version": plugin_version}}), encoding="utf-8"
        )
        assert mcp_api._compatible_gui_plugin(plugin_dir, "1.2.3.dev2") is expected


def test_mcp_gui_plugin_rejects_missing_or_invalid_version(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "ida-nexus"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "ida_nexus_plugin.py").touch()
    manifest = plugin_dir / "ida-plugin.json"

    assert mcp_api._compatible_gui_plugin(plugin_dir, "1.2.3") is False
    for contents in ("not json", "{}", '{"plugin":{"version":"invalid"}}'):
        manifest.write_text(contents, encoding="utf-8")
        assert mcp_api._compatible_gui_plugin(plugin_dir, "1.2.3") is False


def test_mcp_recognizes_consumer_gui_provider(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "ida-mcp"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "ida_mcp_plugin.py").touch()
    (plugin_dir / "ida-plugin.json").write_text(
        json.dumps(
            {
                "plugin": {
                    "name": "ida-mcp",
                    "entryPoint": "ida_mcp_plugin.py",
                    "pythonDependencies": ["ida-nexus>=0.7.0"],
                }
            }
        ),
        encoding="utf-8",
    )

    assert mcp_api._declares_nexus_gui_provider(plugin_dir, "ida-mcp") is True
    assert mcp_api._declares_nexus_gui_provider(plugin_dir, "ida-chat") is False


def test_mcp_execute_owns_autoanalysis_policy(monkeypatch) -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def resolve_instance_id(self, instance_id: str | None) -> str:
            self.calls.append(("resolve_instance_id", instance_id))
            assert instance_id is not None
            return instance_id

        def ensure_autoanalysis(
            self,
            instance_id: str | None,
            *,
            operation_id: str | None = None,
        ) -> None:
            self.calls.append(("ensure_autoanalysis", instance_id, operation_id))

        def execute_python(
            self,
            code: str,
            instance_id: str | None,
            timeout: float | None = None,
            *,
            operation_id: str | None = None,
            operation_label: str | None = None,
            persist_globals: bool = False,
        ):
            assert persist_globals
            self.calls.append(
                (
                    "execute_python",
                    code,
                    instance_id,
                    timeout,
                    operation_id,
                    operation_label,
                )
            )
            return {"result": 1, "stdout": "", "stderr": ""}

    manager = FakeManager()
    trace_records: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(mcp_api, "DATABASE_MANAGER", manager)
    monkeypatch.setattr(
        mcp_api,
        "TRACE",
        SimpleNamespace(
            emit=lambda event, **fields: trace_records.append((event, fields))
        ),
    )

    result = asyncio.run(mcp_api.execute_python("lambda: 1", "test-instance"))
    assert result == {
        "result": 1,
        "stdout": "",
        "stderr": "",
    }
    assert manager.calls[0] == ("resolve_instance_id", "test-instance")
    operation_id = manager.calls[1][2]
    assert isinstance(operation_id, str) and len(operation_id) == 32
    tool_call = next(fields for event, fields in trace_records if event == "tool_call")
    assert operation_id == tool_call["call_id"]
    assert manager.calls[1:] == [
        ("ensure_autoanalysis", "test-instance", operation_id),
        (
            "execute_python",
            "lambda: 1",
            "test-instance",
            360,
            operation_id,
            "ida-mcp",
        ),
    ]


def test_mcp_execute_honors_cancellation_notification(monkeypatch) -> None:
    class BlockingManager:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.executed = threading.Event()
            self.cancel_calls: list[tuple[str, str]] = []

        @staticmethod
        def resolve_instance_id(instance_id: str | None) -> str:
            assert instance_id is not None
            return instance_id

        def ensure_autoanalysis(
            self,
            _instance_id: str | None,
            *,
            operation_id: str | None = None,
        ) -> None:
            assert operation_id is not None
            self.started.set()
            assert self.release.wait(2)
            # Successful completion races with the accepted cancellation. User
            # code must still not start after the MCP request was cancelled.

        def execute_python(self, *_args, **_kwargs):
            self.executed.set()
            raise AssertionError("execution should not follow cancelled analysis")

        def cancel_operation(self, instance_id: str, operation_id: str) -> bool:
            self.cancel_calls.append((instance_id, operation_id))
            self.release.set()
            return True

    manager = BlockingManager()
    monkeypatch.setattr(mcp_api, "DATABASE_MANAGER", manager)
    monkeypatch.setattr(
        mcp_api,
        "TRACE",
        SimpleNamespace(emit=lambda *_args, **_kwargs: None),
    )
    result: dict[str, object] = {}

    def call() -> None:
        result["response"] = mcp_api.mcp._dispatch_mcp(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "execute_python",
                    "arguments": {
                        "code": "1",
                        "instance_id": "test-instance",
                    },
                },
                "id": "cancel-me",
            }
        )

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    assert manager.started.wait(1)
    mcp_api.mcp._dispatch_mcp(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "cancel-me", "reason": "client timeout"},
        }
    )
    thread.join(2)

    assert not thread.is_alive()
    assert result["response"] is None
    assert not manager.executed.is_set()
    assert manager.cancel_calls
    assert {instance for instance, _operation in manager.cancel_calls} == {
        "test-instance"
    }
    assert len({operation for _instance, operation in manager.cancel_calls}) == 1


def test_cancelling_queued_mcp_execution_does_not_cancel_running_request(
    monkeypatch,
) -> None:
    class QueuedManager:
        def __init__(self) -> None:
            self.operation_lock = threading.Lock()
            self.state_lock = threading.Lock()
            self.active: tuple[str, str] | None = None
            self.operation_ids: dict[str, str] = {}
            self.first_started = threading.Event()
            self.first_release = threading.Event()
            self.second_waiting = threading.Event()
            self.second_started = threading.Event()
            self.second_release = threading.Event()
            self.cancel_attempted = threading.Event()
            self.cancel_calls: list[str] = []

        @staticmethod
        def resolve_instance_id(instance_id: str | None) -> str:
            assert instance_id is not None
            return instance_id

        @staticmethod
        def ensure_autoanalysis(
            _instance_id: str,
            *,
            operation_id: str | None = None,
        ) -> None:
            assert operation_id is not None

        def execute_python(
            self,
            code: str,
            _instance_id: str,
            timeout: float | None = None,
            *,
            operation_id: str | None = None,
            operation_label: str | None = None,
            persist_globals: bool = False,
        ) -> dict[str, object]:
            assert timeout == 360
            assert persist_globals
            assert operation_id is not None
            assert operation_label == "ida-mcp"
            self.operation_ids[code] = operation_id
            if code == "second":
                self.second_waiting.set()
            with self.operation_lock:
                with self.state_lock:
                    self.active = (code, operation_id)
                if code == "first":
                    self.first_started.set()
                    assert self.first_release.wait(2)
                else:
                    self.second_started.set()
                    assert self.second_release.wait(2)
                with self.state_lock:
                    self.active = None
            if code == "second":
                raise RuntimeError("second operation cancelled")
            return {"result": code, "stdout": "", "stderr": ""}

        def cancel_operation(self, _instance_id: str, operation_id: str) -> bool:
            self.cancel_calls.append(operation_id)
            self.cancel_attempted.set()
            with self.state_lock:
                active = self.active
            if active is None or active[1] != operation_id:
                return False
            if active[0] == "first":
                self.first_release.set()
            else:
                self.second_release.set()
            return True

    manager = QueuedManager()
    monkeypatch.setattr(mcp_api, "DATABASE_MANAGER", manager)
    monkeypatch.setattr(
        mcp_api,
        "TRACE",
        SimpleNamespace(emit=lambda *_args, **_kwargs: None),
    )
    results: dict[str, object] = {}

    def call(code: str, request_id: str) -> None:
        results[request_id] = mcp_api.mcp._dispatch_mcp(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "execute_python",
                    "arguments": {"code": code, "instance_id": "test-instance"},
                },
                "id": request_id,
            }
        )

    first = threading.Thread(target=call, args=("first", "first-request"))
    second = threading.Thread(target=call, args=("second", "second-request"))
    first.start()
    assert manager.first_started.wait(1)
    second.start()
    assert manager.second_waiting.wait(1)

    mcp_api.mcp._dispatch_mcp(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "second-request", "reason": "client timeout"},
        }
    )
    assert manager.cancel_attempted.wait(1)
    manager.first_release.set()
    assert manager.second_started.wait(1)
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results["first-request"] is not None
    assert results["second-request"] is None
    assert manager.operation_ids["first"] != manager.operation_ids["second"]
    assert set(manager.cancel_calls) == {manager.operation_ids["second"]}


def test_stdio_eof_starts_shutdown_once() -> None:
    shutdown_calls: list[None] = []
    stdin = mcp_api._ShutdownOnEOFInput(
        BytesIO(b'{"jsonrpc":"2.0"}\n'),
        lambda: shutdown_calls.append(None),
    )

    assert stdin.readline() == b'{"jsonrpc":"2.0"}\n'
    assert shutdown_calls == []
    assert stdin.readline() == b""
    assert stdin.readline() == b""
    assert shutdown_calls == [None]


def test_mcp_execute_schema_exposes_numeric_timeout_default() -> None:
    tools = mcp_api.mcp.registry.methods["tools/list"]()["tools"]
    execute_tool = next(tool for tool in tools if tool["name"] == "execute_python")
    timeout_schema = execute_tool["inputSchema"]["properties"]["timeout"]

    assert timeout_schema == {
        "type": "number",
        "description": (
            "Python execution timeout in seconds. This does not include the separate "
            "initial autoanalysis wait."
        ),
        "default": 360,
    }


@pytest.mark.parametrize("agent", ["codex", "future_agent", None, ""])
def test_mcp_session_fields_filter_paths_by_configured_agent(monkeypatch, agent) -> None:
    monkeypatch.setenv("IDA_MCP_ID", "process-mcp-id")
    monkeypatch.setattr(mcp_api, "TRACE", Mock())
    monkeypatch.setattr(mcp_api, "_TRACE_STARTED", False)
    monkeypatch.setattr(mcp_api, "_OPERATION_LABEL", "ida-mcp")
    monkeypatch.setattr(mcp_api, "_AGENT_SESSION_PATH_FIELD", None)
    mcp_api._start_mcp_trace("stdio", agent)

    metadata = {
        "dsh_session_id": "session-42",
        "codex_session_path": "/tmp/codex-session.jsonl",
        "future_agent_session_path": "/tmp/future-session.jsonl",
        "other_agent_session_path": "/tmp/other-session.jsonl",
        "future_agent": {"name": "example", "version": 1},
        "enabled": False,
        "mcp_id": "request-mcp-id",
    }
    original = dict(metadata)
    expected = {
        "dsh_session_id": "session-42",
        "future_agent": {"name": "example", "version": 1},
        "enabled": False,
        "mcp_id": "process-mcp-id",
    }
    if agent:
        key = f"{agent}_session_path"
        expected[key] = metadata[key]
    assert mcp_api._session_fields_from_meta(metadata) == expected
    assert metadata == original


def test_mcp_trace_is_created_on_first_tool_call(tmp_path: Path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(mcp_api, "SESSIONS_DIR", sessions_dir)
    trace = mcp_api._TraceLogger()

    trace.emit("mcp_started", agent="test-agent")
    trace.emit("mcp_initialized", clientInfo={"name": "test-client"})

    assert not sessions_dir.exists()
    assert not trace.path.exists()

    trace.emit("tool_call", call_id="call-1", tool="list_databases")
    trace.emit("tool_result", call_id="call-1", tool="list_databases")

    records = [json.loads(line) for line in trace.path.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "mcp_started",
        "mcp_initialized",
        "tool_call",
        "tool_result",
    ]


def test_mcp_trace_is_discarded_without_a_tool_call(
    tmp_path: Path, monkeypatch
) -> None:
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(mcp_api, "SESSIONS_DIR", sessions_dir)
    trace = mcp_api._TraceLogger()

    trace.emit("mcp_started", agent="test-agent")
    trace.emit("mcp_initialized", clientInfo={"name": "test-client"})
    trace.emit("mcp_stopped")

    assert not sessions_dir.exists()
    assert not trace.path.exists()


def test_mcp_session_trace_metadata(tmp_path: Path, monkeypatch) -> None:
    class FakeTrace:
        path = tmp_path / "session.jsonl"

        def __init__(self) -> None:
            self.records: list[tuple[str, dict[str, object]]] = []

        def emit(self, event: str, **fields: object) -> None:
            self.records.append((event, fields))

    trace = FakeTrace()
    manager = DatabaseManager(
        on_event=mcp_api._trace_database_event,
    )
    monkeypatch.setattr(mcp_api, "TRACE", trace)
    monkeypatch.setattr(mcp_api, "DATABASE_MANAGER", manager)
    monkeypatch.setattr(mcp_api, "_TRACE_STARTED", False)
    monkeypatch.setattr(mcp_api, "_TRACE_STOPPED", False)
    monkeypatch.setattr(mcp_api, "_AGENT_SESSION_PATH_FIELD", None)

    mcp_api._start_mcp_trace("stdio", "test-agent")
    assert mcp_api._OPERATION_LABEL == "test-agent"
    mcp_api.mcp.registry.methods["initialize"](
        "2025-06-18",
        {},
        {"name": "test-client", "version": "1.0"},
        {"model": "test-model"},
    )
    manager._emit("database_opened", instance_id="test-instance")
    mcp_api._shutdown_server_state()

    assert [event for event, _fields in trace.records] == [
        "mcp_started",
        "mcp_initialized",
        "database_opened",
        "mcp_stopped",
    ]
    assert trace.records[0][1]["agent"] == "test-agent"
    assert trace.records[1][1]["clientInfo"] == {
        "name": "test-client",
        "version": "1.0",
    }
    assert trace.records[1][1]["_meta"] == {"model": "test-model"}
    assert trace.records[2][1]["instance_id"] == "test-instance"


def test_database_event_inherits_active_trace_call_id(monkeypatch) -> None:
    records: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        mcp_api,
        "TRACE",
        SimpleNamespace(emit=lambda event, **fields: records.append((event, fields))),
    )

    token = mcp_api._TRACE_CALL_ID.set("tool-call-id")
    try:
        mcp_api._trace_database_event("database_opened", {"instance_id": "instance-1"})
    finally:
        mcp_api._TRACE_CALL_ID.reset(token)

    assert len(records) == 1
    event, fields = records[0]
    assert event == "database_opened"
    assert fields["instance_id"] == "instance-1"
    assert fields["call_id"] == "tool-call-id"


def test_database_disconnect_sends_structured_warning(monkeypatch) -> None:
    traces: list[tuple[str, dict[str, object]]] = []
    output = BytesIO()
    monkeypatch.setattr(
        mcp_api,
        "TRACE",
        SimpleNamespace(emit=lambda event, **fields: traces.append((event, fields))),
    )
    database_state = {
        "state": "crashed",
        "id0_path": "/tmp/sample.id0",
        "packed_database_exists": False,
    }
    target = {
        "record_id": "123-deadbe",
        "pid": 123,
        "idb_path": "/tmp/sample.i64",
        "worker_log_path": "/tmp/123-deadbe.log",
    }

    with mcp_api.mcp._stdio_output_scope(output):
        mcp_api._trace_database_event(
            "database_disconnected",
            {
                "instance_id": "instance-1",
                "reason": "database process crashed",
                "target": target,
                "database_state": database_state,
            },
        )

    notification = json.loads(output.getvalue())
    assert notification["method"] == "notifications/message"
    assert notification["params"] == {
        "level": "warning",
        "logger": "ida_mcp.database",
        "data": {
            "event": "database_lost",
            "message": (
                "IDA database worker crashed; "
                "the previous instance is permanently invalid"
            ),
            "instance_id": "instance-1",
            "reason": "database process crashed",
            "target": target,
            "database_state": database_state,
            "recovery_required": True,
        },
    }
    assert traces[0][0] == "database_disconnected"
    assert traces[0][1]["level"] == "warning"
    assert traces[0][1]["database_state"] == database_state


def test_database_disconnect_trace_survives_unavailable_logging_transport(
    monkeypatch,
) -> None:
    traces: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        mcp_api,
        "TRACE",
        SimpleNamespace(emit=lambda event, **fields: traces.append((event, fields))),
    )

    def unavailable(*_args, **_kwargs) -> None:
        raise RuntimeError("no active stdio transport")

    monkeypatch.setattr(mcp_api.mcp, "send_log_message", unavailable)

    mcp_api._trace_database_event(
        "database_disconnected",
        {
            "instance_id": "instance-1",
            "reason": "connection closed",
            "database_state": {"state": "unknown"},
        },
    )

    assert traces[0][0] == "database_disconnected"
    assert traces[0][1]["level"] == "warning"
