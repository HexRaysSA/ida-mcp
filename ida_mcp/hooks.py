"""Agent hook adapters for attaching transcript paths to MCP tool calls."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO


def _report_claude_session(payload: dict[str, object]) -> dict[str, object]:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = payload.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    existing_meta = tool_input.get("_meta")
    if not isinstance(existing_meta, dict):
        existing_meta = {}

    transcript_path = payload.get("transcript_path")
    updated_input = dict(tool_input)
    updated_meta = dict(existing_meta)
    if isinstance(transcript_path, str) and transcript_path:
        updated_meta["claude_session_path"] = transcript_path
    if updated_meta:
        updated_input["_meta"] = updated_meta

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated_input,
        }
    }


def _report_codex_session(payload: dict[str, object]) -> dict[str, object]:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = payload.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    existing_meta = tool_input.get("_meta")
    if not isinstance(existing_meta, dict):
        existing_meta = {}

    transcript_path = payload.get("transcript_path")
    updated_input = dict(tool_input)
    updated_meta = dict(existing_meta)
    if isinstance(transcript_path, str) and transcript_path:
        updated_meta["codex_session_path"] = transcript_path
    if updated_meta:
        updated_input["_meta"] = updated_meta

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }


def _report_copilot_session(payload: dict[str, object]) -> dict[str, object]:
    tool_args = payload.get("toolArgs")
    if not isinstance(tool_args, dict):
        tool_args = {}

    existing_meta = tool_args.get("_meta")
    if not isinstance(existing_meta, dict):
        existing_meta = {}

    updated_args = dict(tool_args)
    updated_meta = dict(existing_meta)
    session_id = payload.get("sessionId")
    if isinstance(session_id, str) and session_id:
        configured_home = os.environ.get("COPILOT_HOME")
        copilot_home = (
            Path(configured_home).expanduser()
            if configured_home
            else Path.home() / ".copilot"
        )
        transcript_path = copilot_home / "session-state" / session_id / "events.jsonl"
        updated_meta["copilot_session_path"] = str(transcript_path)
    if updated_meta:
        updated_args["_meta"] = updated_meta

    return {
        "permissionDecision": "allow",
        "modifiedArgs": updated_args,
    }


def run_hook(
    platform: str,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Read one hook payload and emit the platform-specific response."""

    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        payload = json.load(stdin)
    except json.JSONDecodeError as exc:
        print(f"ida-mcp hook: invalid JSON on stdin: {exc}", file=stderr)
        return 1
    if not isinstance(payload, dict):
        print("ida-mcp hook: input must be a JSON object", file=stderr)
        return 1

    if platform == "claude":
        response = _report_claude_session(payload)
    elif platform == "codex":
        response = _report_codex_session(payload)
    elif platform == "copilot":
        response = _report_copilot_session(payload)
    else:
        print(f"ida-mcp hook: unsupported platform: {platform}", file=stderr)
        return 2

    print(json.dumps(response), file=stdout)
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Run the hook command after its platform has been parsed by the root CLI."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="ida-mcp hook",
        description="Attach an agent transcript path to an IDA MCP tool call.",
    )
    parser.add_argument("platform", choices=["claude", "codex", "copilot"])
    args = parser.parse_args(argv)
    return run_hook(args.platform)
