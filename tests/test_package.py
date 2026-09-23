from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path

from ida_nexus import get_state_dir

from ida_mcp import dashboard, logs, mcp
from ida_mcp.paths import get_mcp_state_dir

ROOT = Path(__file__).parents[1]


def test_console_script_uses_ida_mcp_package() -> None:
    root = Path(__file__).parents[1]
    with (root / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    assert project["project"]["scripts"] == {"ida-mcp": "ida_mcp.cli:main"}
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"] == [
        "ida_mcp"
    ]


def test_plugin_ida_nexus_dependency_matches_pyproject() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)
    pyproject_deps = project["project"]["dependencies"]
    pyproject_ida_nexus = [dep for dep in pyproject_deps if dep.startswith("ida-nexus")]
    assert len(pyproject_ida_nexus) == 1, (
        f"expected exactly one ida-nexus entry in pyproject.toml dependencies, "
        f"found {pyproject_ida_nexus!r}"
    )

    plugin = json.loads((ROOT / "ida-plugin.json").read_text(encoding="utf-8"))
    plugin_deps = plugin["plugin"]["pythonDependencies"]
    plugin_ida_nexus = [dep for dep in plugin_deps if dep.startswith("ida-nexus")]
    assert len(plugin_ida_nexus) == 1, (
        f"expected exactly one ida-nexus entry in ida-plugin.json pythonDependencies, "
        f"found {plugin_ida_nexus!r}"
    )

    assert plugin_ida_nexus[0] == pyproject_ida_nexus[0], (
        "ida-plugin.json pythonDependencies is out of sync with pyproject.toml "
        f"dependencies: {plugin_ida_nexus[0]!r} != {pyproject_ida_nexus[0]!r}"
    )


def test_moved_modules_are_importable() -> None:
    for name in ("cli", "dashboard", "hooks", "logs", "mcp"):
        assert importlib.import_module(f"ida_mcp.{name}") is not None


def test_semantic_sessions_use_the_mcp_state_location() -> None:
    expected = get_mcp_state_dir() / "sessions"
    assert mcp.SESSIONS_DIR == expected
    assert dashboard.DEFAULT_SESSIONS_DIR == expected
    assert logs.DEFAULT_SESSIONS_DIR == expected


def test_dashboard_and_logs_also_know_the_legacy_nexus_location() -> None:
    expected = get_state_dir() / "sessions"
    assert dashboard.LEGACY_SESSIONS_DIR == expected
    assert logs.LEGACY_SESSIONS_DIR == expected
    assert dashboard.LEGACY_SESSIONS_DIR in dashboard.SESSIONS_DIRS


def test_agent_plugin_manifests_forward_every_mcp_environment_variable() -> None:
    expected = set(mcp.MCP_ENVIRONMENT_VARIABLES)

    claude_plugin = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    claude_env = claude_plugin["mcpServers"]["ida"]["env"]
    assert set(claude_env) == expected
    for name in expected:
        assert claude_env[name] == f"${{{name}:-}}"

    copilot_plugin = json.loads(
        (ROOT / ".github" / "plugin" / "mcp.json").read_text(encoding="utf-8")
    )
    copilot_env = copilot_plugin["mcpServers"]["ida"]["env"]
    assert set(copilot_env) == expected
    for name in expected:
        assert copilot_env[name] == f"${{{name}:-}}"

    codex_plugin = json.loads(
        (ROOT / ".codex-plugin" / "mcp.json").read_text(encoding="utf-8")
    )
    assert set(codex_plugin["ida"]["env_vars"]) == expected
