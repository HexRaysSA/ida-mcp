import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ida_mcp import dashboard

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def ts(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def record(kind: str, event: str, seconds: float) -> dict:
    result = {"timestamp": ts(seconds).isoformat()}
    if kind in {"pi", "claude"}:
        role = {"prompt": "user", "work": "assistant", "result": "toolResult"}[event]
        content = [{"type": "text", "text": event}]
        if kind == "claude" and event == "result":
            role = "user"
            content = [{"type": "tool_result", "tool_use_id": "call", "content": "ok"}]
        result.update(
            type="message" if kind == "pi" else role,
            message={"role": role, "content": content},
        )
    elif kind == "codex":
        result.update(
            type="response_item" if event == "result" else "event_msg",
            payload={
                "type": {
                    "prompt": "user_message",
                    "work": "agent_message",
                    "result": "function_call_output",
                }[event]
            },
        )
    else:
        result.update(
            type={
                "prompt": "user.message",
                "work": "assistant.message",
                "result": "tool.execution_complete",
            }[event]
        )
    return result


@pytest.mark.parametrize("kind", ["pi", "claude", "codex", "copilot"])
def test_excludes_idle_but_counts_long_tools_and_last_answer(kind: str) -> None:
    records = [
        record(kind, "work", -10),  # No prompt: cannot infer a start.
        record(kind, "prompt", 0),
        record(kind, "work", 5),
        record(kind, "result", 125),  # Long tool execution must not be capped.
        record(kind, "work", 130),
        record(kind, "prompt", 3600),  # User took a break.
        record(kind, "work", 3610),
        record(kind, "result", 3620),  # Tool completion counts without final text.
        {"type": "session.shutdown", "timestamp": ts(7200).isoformat()},
        record(kind, "prompt", 8000),  # Pending turn: don't count time to now.
    ]
    assert dashboard._task_intervals(records, kind) == [
        (ts(0), ts(130)),
        (ts(3600), ts(3620)),
    ]


def test_missing_timestamps_and_out_of_order_records() -> None:
    records = [
        record("pi", "work", 20),
        record("pi", "prompt", 0),
        {"type": "message", "message": {"role": "user"}},
        {"type": "message", "timestamp": "invalid", "message": {"role": "user"}},
        record("pi", "result", 10),
    ]
    assert dashboard._task_intervals(records, "pi") == [(ts(0), ts(20))]


def test_pi_uses_active_branch() -> None:
    records = [
        {**record("pi", "prompt", 0), "id": "u", "parentId": None},
        {**record("pi", "work", 10), "id": "old", "parentId": "u"},
        {**record("pi", "prompt", 3600), "id": "new", "parentId": None},
        {**record("pi", "work", 3615), "id": "a", "parentId": "new"},
    ]
    assert dashboard._task_intervals(records, "pi") == [(ts(3600), ts(3615))]


def test_claude_sidechain_does_not_reset_main_prompt() -> None:
    records = [
        record("claude", "prompt", 0),
        record("claude", "work", 10),
        {**record("claude", "prompt", 20), "isSidechain": True},
        record("claude", "work", 30),
        {**record("claude", "work", 3000), "isSidechain": True},
    ]
    assert dashboard._task_intervals(records, "claude") == [(ts(0), ts(30))]


def test_session_unions_intervals_and_clips_to_attribution_window(monkeypatch) -> None:
    summary = dashboard.SessionSummary(Path("trace.jsonl"), "trace", 0)
    summary.agent_session_refs = {("pi", "a"), ("omp", "a"), ("claude", "b")}
    monkeypatch.setattr(dashboard, "_transcript_window", lambda *args: (ts(5), ts(50)))
    intervals = {
        "a": [(ts(0), ts(20)), (ts(40), ts(60))],
        "b": [(ts(10), ts(30)), (ts(100), ts(110))],
    }
    monkeypatch.setattr(
        dashboard,
        "_load_agent_items",
        lambda path: ([], {}, "pi", {"task_intervals": intervals[path]}),
    )
    assert dashboard._session_task_time(summary) == 35.0


def test_no_transcript_does_not_fall_back_to_wall_time() -> None:
    summary = dashboard.SessionSummary(
        Path("trace.jsonl"), "trace", 0, started=ts(0), last_activity=ts(3600)
    )
    assert dashboard._session_task_time(summary) is None
    assert dashboard._summary_index_row(summary).endswith(
        '<td class="mono" data-sort=""><span class="muted">—</span></td></tr>'
    )


def test_transcript_cache_refresh_and_session_detail(tmp_path, monkeypatch) -> None:
    agent = tmp_path / "agent.trace"
    trace = tmp_path / "trace.jsonl"
    records = [{"type": "session", "version": 3, "id": "pi-session"}]
    records += [
        record("pi", "prompt", 0),
        record("pi", "work", 10),
        record("pi", "prompt", 3600),
        record("pi", "work", 3620),
    ]
    agent.write_text("".join(json.dumps(r) + "\n" for r in records))
    trace.write_text(
        json.dumps(
            {
                "schema": 1,
                "ts": ts(5).isoformat(),
                "event": "mcp_started",
                "agent": "pi",
                "session": {"pi_session_path": str(agent)},
            }
        )
        + "\n"
        + json.dumps(
            {
                "schema": 1,
                "ts": ts(3605).isoformat(),
                "event": "mcp_stopped",
            }
        )
        + "\n"
    )
    monkeypatch.setattr(dashboard, "SESSIONS_DIRS", [tmp_path])
    monkeypatch.setattr(dashboard, "_AGENT_ITEMS_CACHE", {})
    summary = dashboard._scan_sessions()[0]
    assert dashboard._session_task_time(summary) == 30.0
    assert 'data-sort="30.000000">30.0 s</td>' in dashboard.render_index()
    detail = dashboard.render_session(trace.name)
    assert detail is not None
    assert 'Task time (estimated)</span><span class="v">30.0 s</span>' in detail

    # A later tool result updates the cached estimate, including tool time even
    # though the rendered transcript hides standalone tool-result records.
    with agent.open("a") as f:
        f.write(json.dumps(record("pi", "result", 3640)) + "\n")
    assert dashboard._session_task_time(summary) == 50.0
    agent.unlink()
    assert dashboard._session_task_time(summary) is None
