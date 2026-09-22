"""Snapshot of the MCP metadata advertised to clients.

Regenerate the golden file after an intentional metadata change with:

    IDA_MCP_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_tool_metadata.py
"""

from __future__ import annotations

import inspect
import json
import os
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ida_mcp import mcp as mcp_api

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "mcp_metadata.json"
UPDATE_SNAPSHOTS_ENVIRONMENT_VARIABLE = "IDA_MCP_UPDATE_SNAPSHOTS"
# Newest revision ZeroMCP negotiates; older revisions fold tool titles into
# annotations and are not what current clients see.
PROTOCOL_VERSION = "2025-11-25"
VERSION_PLACEHOLDER = "<package-version>"


def _served_metadata(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(
        mcp_api,
        "TRACE",
        SimpleNamespace(emit=lambda *_args, **_kwargs: None),
    )
    # The stdio transport records negotiated state on the shared server object.
    monkeypatch.setattr(mcp_api.mcp, "_stdio_protocol_version", None)
    monkeypatch.setattr(mcp_api.mcp, "_stdio_log_level", mcp_api.mcp._stdio_log_level)

    requests = [
        {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "snapshot", "version": "1.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "tools/list", "method": "tools/list"},
    ]
    stdin = BytesIO(
        b"".join(json.dumps(request).encode() + b"\n" for request in requests)
    )
    stdout = BytesIO()
    # The synchronous loop shares the production stdio request path but handles
    # requests in order, so tools/list sees the protocol negotiated above.
    mcp_api.mcp.stdio(stdin=stdin, stdout=stdout)

    responses = {
        message["id"]: message
        for message in map(json.loads, stdout.getvalue().splitlines())
        if "id" in message
    }
    assert "error" not in responses["initialize"], responses["initialize"]
    assert "error" not in responses["tools/list"], responses["tools/list"]

    initialize = responses["initialize"]["result"]
    assert initialize["serverInfo"]["version"] == mcp_api.PACKAGE_VERSION
    initialize["serverInfo"]["version"] = VERSION_PLACEHOLDER
    return {"initialize": initialize, "tools/list": responses["tools/list"]["result"]}


def test_tool_descriptions_are_dedented(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _served_metadata(monkeypatch)["tools/list"]["tools"]
    for tool in tools:
        description = tool["description"]
        assert description == inspect.cleandoc(description), tool["name"]


def test_mcp_metadata_matches_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keys keep the order they are served in, so tool and parameter order is
    # part of the snapshot.
    actual = json.dumps(
        _served_metadata(monkeypatch),
        indent=2,
        ensure_ascii=False,
    )
    actual += "\n"

    if os.environ.get(UPDATE_SNAPSHOTS_ENVIRONMENT_VARIABLE) == "1":
        SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text(actual, encoding="utf-8")

    expected = (
        SNAPSHOT_PATH.read_text(encoding="utf-8") if SNAPSHOT_PATH.exists() else ""
    )
    assert actual == expected, (
        f"MCP metadata differs from {SNAPSHOT_PATH.name}; if the change is "
        f"intended, regenerate it with {UPDATE_SNAPSHOTS_ENVIRONMENT_VARIABLE}=1 "
        "uv run pytest tests/test_tool_metadata.py"
    )
