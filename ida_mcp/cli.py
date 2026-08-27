"""Command-line proxy for the IDA Nexus MCP server."""

from ida_nexus.cli.mcp import cli


def main() -> int:
    """Run the IDA Nexus MCP server."""
    return cli()
