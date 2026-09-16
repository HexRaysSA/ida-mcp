from __future__ import annotations

from unittest.mock import Mock

import pytest

from ida_mcp import cli
from ida_mcp import mcp as mcp_api


def test_cli_requires_subcommand() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main([])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["--agent=test-agent"],
        ["--transport", "stdio"],
        ["--report-session=claude"],
        ["mcp"],
    ],
)
def test_removed_cli_forms_are_rejected(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("command", ["stdio", "http", "dashboard", "logs", "hook"])
def test_root_help_lists_every_command(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--help"]) == 0
    assert command in capsys.readouterr().out


def test_stdio_subcommand_starts_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = Mock()
    monkeypatch.setattr(mcp_api, "serve_stdio", serve)

    assert cli.main(["stdio", "--agent", "test-agent", "--database", "sample.i64"]) == 0
    serve.assert_called_once_with(
        database="sample.i64",
        agent="test-agent",
        idle_timeout=None,
    )


def test_http_subcommand_starts_foreground_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve = Mock()
    monkeypatch.setattr(mcp_api, "serve_http", serve)

    assert (
        cli.main(
            [
                "http",
                "--host",
                "127.0.0.1",
                "--port",
                "18737",
                "--path-prefix",
                "/hex-rays",
            ]
        )
        == 0
    )
    serve.assert_called_once_with(
        "127.0.0.1",
        18737,
        database=None,
        agent=None,
        path_prefix="/hex-rays",
        background=False,
        idle_timeout=None,
    )
