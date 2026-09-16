"""Command-line interface for IDA MCP."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

_COMMAND_HELP = {
    "stdio": "run the MCP server over standard input and output",
    "http": "run the MCP server over Streamable HTTP",
    "dashboard": "inspect MCP session logs",
    "logs": "export MCP session logs to a ZIP archive",
    "hook": "attach agent transcript metadata to a tool call",
}


class _HelpFormatter(argparse.HelpFormatter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._action_max_length = max(
            self._action_max_length,
            max(map(len, _COMMAND_HELP)) + 4,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ida-mcp",
        description="Official Hex-Rays IDA MCP Server",
        formatter_class=_HelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for name, help_text in _COMMAND_HELP.items():
        commands.add_parser(name, add_help=False, help=help_text)
    return parser


def _idle_timeout(value: str) -> float | None:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("idle timeout must be a number") from exc
    if timeout == 0:
        return None
    if not timeout > 0 or timeout == float("inf"):
        raise argparse.ArgumentTypeError(
            "idle timeout must be positive and finite, or zero to disable"
        )
    return timeout


def _add_server_arguments(parser: argparse.ArgumentParser) -> None:
    from ida_mcp.mcp import (
        MCP_IDLE_TIMEOUT_ENVIRONMENT_VARIABLE,
        _mcp_idle_timeout_from_environment,
    )

    parser.add_argument(
        "--database",
        default=None,
        help="Path to an executable or IDB to open and activate on startup.",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent name to record in the MCP session trace.",
    )
    parser.add_argument(
        "--idle-timeout",
        type=_idle_timeout,
        default=_mcp_idle_timeout_from_environment(),
        help=(
            "Release each managed idalib lease after this many seconds without "
            "a database request; zero disables idle release. Defaults to no "
            "timeout. Can also be set with "
            f"{MCP_IDLE_TIMEOUT_ENVIRONMENT_VARIABLE}."
        ),
    )


def _stdio_cli(argv: list[str] | None = None) -> int:
    from ida_mcp.mcp import serve_stdio

    parser = argparse.ArgumentParser(
        prog="ida-mcp stdio",
        description="Run the IDA MCP server over standard input and output.",
    )
    _add_server_arguments(parser)
    args = parser.parse_args(argv)
    serve_stdio(
        database=args.database,
        agent=args.agent,
        idle_timeout=args.idle_timeout,
    )
    return 0


def _http_cli(argv: list[str] | None = None) -> int:
    from ida_mcp.mcp import serve_http

    parser = argparse.ArgumentParser(
        prog="ida-mcp http",
        description="Run the IDA MCP server over Streamable HTTP.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8737)
    parser.add_argument(
        "--path-prefix",
        default="",
        help="Optional URL path prefix placed before /mcp.",
    )
    _add_server_arguments(parser)
    args = parser.parse_args(argv)

    print("Server is running; press Ctrl+C to stop.", file=sys.stderr)
    serve_http(
        args.host,
        args.port,
        database=args.database,
        agent=args.agent,
        path_prefix=args.path_prefix,
        background=False,
        idle_timeout=args.idle_timeout,
    )
    return 0


def _command(name: str) -> Callable[[list[str] | None], int]:
    # Imports are lazy so dashboard, log, and hook commands do not initialize
    # the MCP server or idalib-facing modules.
    if name == "stdio":
        return _stdio_cli
    if name == "http":
        return _http_cli
    if name == "dashboard":
        from ida_mcp.dashboard import cli

        return cli
    if name == "logs":
        from ida_mcp.logs import main

        return main
    if name == "hook":
        from ida_mcp.hooks import cli

        return cli
    raise KeyError(name)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    if not arguments:
        parser.parse_args(arguments)
        raise AssertionError("argparse should reject a missing command")
    if arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    command = arguments.pop(0)
    if command not in _COMMAND_HELP:
        parser.parse_args([command])
        raise AssertionError("argparse should reject an unknown command")
    return _command(command)(arguments)
