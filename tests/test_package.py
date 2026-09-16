from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

from ida_nexus import get_state_dir

from ida_mcp import dashboard, logs, mcp


def test_console_script_uses_ida_mcp_package() -> None:
    root = Path(__file__).parents[1]
    with (root / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    assert project["project"]["scripts"] == {"ida-mcp": "ida_mcp.cli:main"}
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"] == [
        "ida_mcp"
    ]


def test_moved_modules_are_importable() -> None:
    for name in ("cli", "dashboard", "hooks", "logs", "mcp"):
        assert importlib.import_module(f"ida_mcp.{name}") is not None


def test_semantic_sessions_keep_the_nexus_state_location() -> None:
    expected = get_state_dir() / "sessions"
    assert mcp.SESSIONS_DIR == expected
    assert dashboard.DEFAULT_SESSIONS_DIR == expected
    assert logs.DEFAULT_SESSIONS_DIR == expected
