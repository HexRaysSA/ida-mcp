from __future__ import annotations

import ast
import importlib
import json
import re
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


def test_environment_whitelists_match() -> None:
    expected = set(mcp.MCP_ENVIRONMENT_VARIABLES)
    extension = (ROOT / "ida-mcp.ts").read_text(encoding="utf-8")
    matches = re.findall(
        r"^const\s+MCP_ENVIRONMENT_VARIABLES\s*=\s*(\[[^\]]*\])\s*as\s+const\s*;",
        extension,
        re.MULTILINE,
    )
    assert len(matches) == 1, (
        "ida-mcp.ts: expected one literal MCP_ENVIRONMENT_VARIABLES list"
    )
    names = ast.literal_eval(matches[0])
    assert isinstance(names, list) and all(isinstance(name, str) for name in names), (
        "ida-mcp.ts: MCP_ENVIRONMENT_VARIABLES must be a list of strings"
    )

    declarations: dict[str, list[str] | dict[str, str]] = {"ida-mcp.ts": names}
    for path, pointer in (
        (".claude-plugin/plugin.json", ("mcpServers", "ida", "env")),
        (".github/plugin/mcp.json", ("mcpServers", "ida", "env")),
        (".codex-plugin/mcp.json", ("ida", "env_vars")),
    ):
        value = json.loads((ROOT / path).read_text(encoding="utf-8"))
        for key in pointer:
            value = value[key]
        declarations[path] = value

    errors = []
    for path, forwarded in declarations.items():
        actual = set(forwarded)
        if missing := expected - actual:
            errors.append(f"{path}: missing {', '.join(sorted(missing))}")
        if extra := actual - expected:
            errors.append(f"{path}: unexpected {', '.join(sorted(extra))}")
        if isinstance(forwarded, dict):
            for name, value in forwarded.items():
                interpolation = f"${{{name}:-}}"
                if value != interpolation:
                    errors.append(f"{path}: {name} must forward {interpolation!r}")
    assert not errors, "\n".join(errors)
