# IDA MCP

⚠️ Experimental prerelease ⚠️

This repository is actively WIP, do not use.

## Installation

### Requirements

- Installed in your PATH
  - [Git](https://git-scm.com/)
  - [uv](https://github.com/astral-sh/uv)
- IDA 9.4 or higher with idalib and Python 3.11+
- Other IDA MCP servers must be disabled to reduce agent confusion

### IDA GUI Plugin

To support IDA GUI instances when using IDA MCP, install the plugin:

```bash
uvx ida-hcli plugin install https://github.com/HexRaysSA/ida-mcp
# or if you have hcli installed:
hcli plugin install https://github.com/HexRaysSA/ida-mcp
```

_Note_: Without the GUI plugin, IDA MCP will only work headlessly.

### [Claude Code](https://claude.com/product/claude-code)

```bash
# Add Hex-Rays marketplace
claude plugin marketplace add HexRaysSA/claude-marketplace
# Install plugin
claude plugin install ida-mcp@HexRaysSA
# Update to latest version
claude plugin update ida-mcp@HexRaysSA
```

### [Codex CLI](https://learn.chatgpt.com/docs/codex/cli)

```bash
# Add Hex-Rays marketplace
codex plugin marketplace add HexRaysSA/codex-marketplace
# Install plugin
codex plugin add ida-mcp@HexRaysSA
```

### [Pi](https://pi.dev/)

```bash
# Install extension
pi install git:github.com/HexRaysSA/ida-mcp@latest
# Update to latest version
pi update --extensions
```

### [oh-my-pi](https://github.com/can1357/oh-my-pi)

```bash
# Install extension
omp plugin install github:HexRaysSA/ida-mcp#latest
# Update to latest version
omp plugin upgrade
```

### Idle database release

An agent that opens a database and never closes it would otherwise keep an IDA
worker, and its unpacked IDB, alive for the whole agent session. IDA MCP
therefore releases its lease on any database it has not used for 30 minutes:
the worker saves the database, closes it, and exits, so an idle machine can be
suspended and no IDB stays open longer than the work needs it.

This is invisible to agents. The next tool call that uses the database reopens
it under the same `instance_id`, so nothing an agent is holding goes stale. GUI
databases are never released, because a GUI instance is not kept alive by MCP
leases.

Pass `--idle-timeout` to choose a different period, or to turn the behavior
off:

```bash
ida-mcp --agent=my-agent --idle-timeout=300   # five minutes
ida-mcp --agent=my-agent --idle-timeout=off   # never release
```

`IDA_MCP_IDLE_TIMEOUT` sets the same value for hosts that configure MCP servers
through the environment rather than through command-line arguments. The plugin
installations above forward it.

### Other agents

Configure a regular stdio MCP server in your MCP JSON configuration:

```json
{
  "mcpServers": {
    "ida": {
      "command": "uvx",
      "args": [
        "ida-nexus",
        "mcp",
        "--agent=my-agent"
      ]
    }
  }
}
```

`uvx` resolves the latest stable `ida-nexus` release from PyPI, so this
configuration does not need to be updated for each release. It runs the IDA
Nexus MCP server directly, so it keeps its database leases until the agent
closes them or the server exits; idle release is part of `ida-mcp`.

`--agent=my-agent` is a human-chosen label (like `claude-code`, `cursor`,
`my-custom-agent`, etc.) used to differentiate sessions in a metrics dashboard.

### Example Usage

Start your agent harness and ask it something like:

> Reverse /path/to/sample.elf for me

To test the GUI integration, open something in IDA and ask your harness:

> What do I have open in the IDA GUI?

## Developers: IDA Nexus

The IDA MCP project is built on [IDA Nexus](https://github.com/HexRaysSA/ida-nexus),
which allows multiple clients to seamlessly share and operate on IDA databases.

You can build your own tools on top of the `ida-nexus` library, see
[the documentation](https://github.com/HexRaysSA/ida-nexus/blob/main/README.md#python-package-developers)
for more information.