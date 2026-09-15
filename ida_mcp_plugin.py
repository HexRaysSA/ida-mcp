"""IDA MCP GUI plugin entry point."""

from typing import Any

import idaapi

import ida_nexus.plugin


class IDAMCPPlugin(idaapi.plugin_t):
    flags = idaapi.PLUGIN_KEEP
    comment = "Official Hex-Rays IDA MCP Server."
    help = ""
    wanted_name = "Hex-Rays MCP"
    wanted_hotkey = ""

    def init(self) -> int:
        if not ida_nexus.plugin.init(owner="ida-mcp"):
            return idaapi.PLUGIN_SKIP
        return idaapi.PLUGIN_KEEP

    def run(self, arg: int) -> None:
        ida_nexus.plugin.run(caller="ida-mcp")

    def term(self) -> None:
        ida_nexus.plugin.term()


def PLUGIN_ENTRY() -> IDAMCPPlugin:
    # IDA's SWIG stubs model plugin_t.__new__ with spurious arguments.
    plugin_type: Any = IDAMCPPlugin
    return plugin_type()
