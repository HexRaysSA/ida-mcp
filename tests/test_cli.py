"""Tests for the ida-mcp command line and idle-release installation."""

from __future__ import annotations

import pytest
from ida_nexus.cli import mcp as nexus_mcp
from ida_nexus.manager import DatabaseManager

from ida_mcp import cli as ida_mcp_cli
from ida_mcp.idle import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    IdleDatabaseManager,
    install_idle_release,
)


@pytest.fixture(autouse=True)
def restore_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the module-global manager swap contained to one test."""
    monkeypatch.setattr(
        nexus_mcp,
        "DATABASE_MANAGER",
        nexus_mcp.DATABASE_MANAGER,
        raising=True,
    )
    monkeypatch.delenv(ida_mcp_cli.IDLE_TIMEOUT_ENVIRONMENT_VARIABLE, raising=False)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    forwarded: list[list[str]] = []
    monkeypatch.setattr(
        ida_mcp_cli,
        "cli",
        lambda argv: forwarded.append(list(argv)) or 0,
    )
    return forwarded


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("900", 900.0),
        (" 1800 ", 1800.0),
        ("0", 0.0),
        ("off", 0.0),
        ("None", 0.0),
    ],
)
def test_idle_timeout_values(text: str, expected: float) -> None:
    assert ida_mcp_cli._parse_idle_timeout(text) == expected


@pytest.mark.parametrize("text", ["-1", "later", "inf", "nan", ""])
def test_invalid_idle_timeout_values(text: str) -> None:
    with pytest.raises(ValueError):
        ida_mcp_cli._parse_idle_timeout(text)


def test_idle_timeout_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ida_mcp_cli._resolve_idle_timeout(None) == DEFAULT_IDLE_TIMEOUT_SECONDS


def test_empty_environment_variable_uses_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Agent hosts forward declared-but-unset variables as empty strings; that
    # must not silently disable idle release.
    monkeypatch.setenv(ida_mcp_cli.IDLE_TIMEOUT_ENVIRONMENT_VARIABLE, "")
    assert ida_mcp_cli._resolve_idle_timeout(None) == DEFAULT_IDLE_TIMEOUT_SECONDS


def test_environment_variable_sets_the_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ida_mcp_cli.IDLE_TIMEOUT_ENVIRONMENT_VARIABLE, "120")
    assert ida_mcp_cli._resolve_idle_timeout(None) == 120.0


def test_flag_overrides_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ida_mcp_cli.IDLE_TIMEOUT_ENVIRONMENT_VARIABLE, "120")
    assert ida_mcp_cli._resolve_idle_timeout("300") == 300.0


def test_invalid_environment_variable_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ida_mcp_cli.IDLE_TIMEOUT_ENVIRONMENT_VARIABLE, "soon")
    assert ida_mcp_cli._resolve_idle_timeout(None) == DEFAULT_IDLE_TIMEOUT_SECONDS
    assert "ignoring invalid" in capsys.readouterr().err


def test_invalid_flag_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        ida_mcp_cli._resolve_idle_timeout("soon")
    assert error.value.code == 2
    assert "invalid --idle-timeout" in capsys.readouterr().err


def test_main_installs_idle_release_and_forwards_the_rest(
    served: list[list[str]],
) -> None:
    assert ida_mcp_cli.main(["--idle-timeout=300", "--agent=claude"]) == 0

    assert served == [["--agent=claude"]]
    manager = nexus_mcp.DATABASE_MANAGER
    assert isinstance(manager, IdleDatabaseManager)
    assert manager._idle_timeout == 300.0
    assert manager._open_timeout == nexus_mcp.OPEN_TIMEOUT_SECONDS
    assert manager._execute_timeout == nexus_mcp.EXECUTE_TIMEOUT_SECONDS


def test_main_can_disable_idle_release(served: list[list[str]]) -> None:
    original = nexus_mcp.DATABASE_MANAGER

    assert ida_mcp_cli.main(["--idle-timeout", "off"]) == 0

    assert served == [[]]
    assert nexus_mcp.DATABASE_MANAGER is original


def test_report_session_hook_does_not_install_a_manager(
    served: list[list[str]],
) -> None:
    original = nexus_mcp.DATABASE_MANAGER

    assert ida_mcp_cli.main(["--report-session=claude"]) == 0

    assert served == [["--report-session=claude"]]
    assert nexus_mcp.DATABASE_MANAGER is original


def test_install_is_skipped_when_ida_nexus_changes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = nexus_mcp.DATABASE_MANAGER
    monkeypatch.setattr(
        "ida_mcp.idle._nexus_supports_idle_release",
        lambda: "DatabaseManager is missing _get_session",
    )

    assert install_idle_release(300.0) is False

    assert nexus_mcp.DATABASE_MANAGER is original
    assert isinstance(nexus_mcp.DATABASE_MANAGER, DatabaseManager)
    assert "idle database release is disabled" in capsys.readouterr().err


def test_installing_does_not_change_the_mcp_tool_surface(
    served: list[list[str]],
) -> None:
    before = nexus_mcp.mcp._mcp_tools_list()

    assert ida_mcp_cli.main(["--idle-timeout=300"]) == 0

    assert nexus_mcp.mcp._mcp_tools_list() == before
