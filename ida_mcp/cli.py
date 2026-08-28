"""Command-line proxy for the IDA Nexus MCP server."""

import argparse
import math
import os
import sys

from ida_nexus.cli.mcp import cli

from ida_mcp.idle import DEFAULT_IDLE_TIMEOUT_SECONDS, install_idle_release

IDLE_TIMEOUT_ENVIRONMENT_VARIABLE = "IDA_MCP_IDLE_TIMEOUT"
_DISABLED_VALUES = {"0", "off", "none", "never", "disabled"}

IDLE_TIMEOUT_HELP = f"""ida-mcp options:
  --idle-timeout SECONDS
                        Release the lease on a database that has not been used
                        for this long, letting its idalib worker save, close
                        the IDB, and exit. The database reopens transparently
                        on its next use. GUI databases are never released.
                        "off" disables this. Defaults to
                        {DEFAULT_IDLE_TIMEOUT_SECONDS:g} seconds, or to
                        ${IDLE_TIMEOUT_ENVIRONMENT_VARIABLE} when it is set.
"""


def _parse_idle_timeout(value: str) -> float:
    """Parse a timeout in seconds, where a disabling word means zero."""
    text = value.strip().casefold()
    if text in _DISABLED_VALUES:
        return 0.0
    seconds = float(text)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("idle timeout must be a non-negative number of seconds")
    return seconds


def _resolve_idle_timeout(argument: str | None) -> float:
    if argument is not None:
        try:
            return _parse_idle_timeout(argument)
        except ValueError:
            print(
                f"ida-mcp: invalid --idle-timeout value: {argument!r}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None

    configured = os.environ.get(IDLE_TIMEOUT_ENVIRONMENT_VARIABLE)
    # Agent hosts pass declared-but-unset variables through as empty strings.
    if not configured or not configured.strip():
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    try:
        return _parse_idle_timeout(configured)
    except ValueError:
        print(
            f"ida-mcp: ignoring invalid {IDLE_TIMEOUT_ENVIRONMENT_VARIABLE}="
            f"{configured!r}",
            file=sys.stderr,
        )
        return DEFAULT_IDLE_TIMEOUT_SECONDS


def main(argv: list[str] | None = None) -> int:
    """Run the IDA Nexus MCP server with idle database release enabled."""
    arguments = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--idle-timeout", default=None)
    known, remaining = parser.parse_known_args(arguments)

    if "-h" in remaining or "--help" in remaining:
        # ida-nexus prints its own help and exits from cli() below.
        print(IDLE_TIMEOUT_HELP)

    # --report-session is a PreToolUse hook that never serves a database.
    if not any(item.startswith("--report-session") for item in remaining):
        install_idle_release(_resolve_idle_timeout(known.idle_timeout))
    return cli(remaining)
