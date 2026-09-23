from __future__ import annotations

from pathlib import Path

from ida_mcp import paths


def test_mcp_state_dir_defaults_under_idausr(monkeypatch) -> None:
    monkeypatch.delenv(paths.STATE_DIR_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setenv("IDAUSR", "/tmp/ida-user")

    assert paths.get_mcp_state_dir() == Path("/tmp/ida-user/mcp")


def test_mcp_state_dir_environment_variable_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("IDAUSR", "/tmp/ida-user")
    monkeypatch.setenv(paths.STATE_DIR_ENVIRONMENT_VARIABLE, "/tmp/custom-mcp-state")

    assert paths.get_mcp_state_dir() == Path("/tmp/custom-mcp-state")


def test_legacy_sessions_dir_is_independent_of_mcp_state_dir(monkeypatch) -> None:
    monkeypatch.setenv(paths.STATE_DIR_ENVIRONMENT_VARIABLE, "/tmp/custom-mcp-state")
    monkeypatch.setenv("IDA_NEXUS_STATE_DIR", "/tmp/custom-nexus-state")

    assert paths.get_legacy_sessions_dir() == Path("/tmp/custom-nexus-state/sessions")


def test_default_sessions_dirs_joins_current_and_legacy_locations(monkeypatch) -> None:
    monkeypatch.setenv(paths.STATE_DIR_ENVIRONMENT_VARIABLE, "/tmp/custom-mcp-state")
    monkeypatch.setenv("IDA_NEXUS_STATE_DIR", "/tmp/custom-nexus-state")

    assert paths.default_sessions_dirs() == (
        Path("/tmp/custom-mcp-state/sessions"),
        Path("/tmp/custom-nexus-state/sessions"),
    )


def test_default_sessions_dirs_deduplicates_when_they_coincide(monkeypatch) -> None:
    monkeypatch.setenv(paths.STATE_DIR_ENVIRONMENT_VARIABLE, "/tmp/shared-state/mcp")
    monkeypatch.setenv("IDA_NEXUS_STATE_DIR", "/tmp/shared-state/mcp")

    assert paths.default_sessions_dirs() == (Path("/tmp/shared-state/mcp/sessions"),)
