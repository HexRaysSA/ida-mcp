from __future__ import annotations

import io
import json
from pathlib import Path

from ida_mcp.hooks import run_hook


def _run(platform: str, payload: object) -> tuple[int, dict[str, object], str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    result = run_hook(
        platform,
        stdin=io.StringIO(json.dumps(payload)),
        stdout=stdout,
        stderr=stderr,
    )
    response = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    return result, response, stderr.getvalue()


def test_claude_hook_adds_transcript_metadata() -> None:
    result, response, error = _run(
        "claude",
        {
            "tool_input": {"path": "sample.i64"},
            "transcript_path": "/tmp/claude.jsonl",
        },
    )

    assert result == 0
    assert error == ""
    assert response["hookSpecificOutput"]["updatedInput"] == {
        "path": "sample.i64",
        "_meta": {"claude_session_path": "/tmp/claude.jsonl"},
    }


def test_codex_hook_allows_call_and_preserves_metadata() -> None:
    result, response, error = _run(
        "codex",
        {
            "input": {"_meta": {"existing": True}},
            "transcript_path": "/tmp/codex.jsonl",
        },
    )

    assert result == 0
    assert error == ""
    output = response["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"]["_meta"] == {
        "existing": True,
        "codex_session_path": "/tmp/codex.jsonl",
    }


def test_copilot_hook_derives_session_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    result, response, error = _run(
        "copilot",
        {"sessionId": "session-42", "toolArgs": {"code": "1"}},
    )

    assert result == 0
    assert error == ""
    assert response == {
        "permissionDecision": "allow",
        "modifiedArgs": {
            "code": "1",
            "_meta": {
                "copilot_session_path": str(
                    tmp_path / "session-state" / "session-42" / "events.jsonl"
                )
            },
        },
    }


def test_hook_rejects_non_object_input() -> None:
    result, response, error = _run("claude", [])
    assert result == 1
    assert response == {}
    assert "input must be a JSON object" in error
