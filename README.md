# Hex-Rays IDA MCP

⚠️ Experimental prerelease ⚠️

Official Hex-Rays IDA MCP Server.

## Installation

### Requirements

- Installed in your PATH
  - [Git](https://git-scm.com/)
  - [uv](https://github.com/astral-sh/uv)
- IDA 9.4 or higher with idalib and Python 3.11+
- We recommend disabling other IDA MCP servers to reduce agent confusion

### Automatic installation

Install the Hex-Rays IDA MCP using [hcli](https://hcli.docs.hex-rays.com/):

```bash
uvx ida-hcli mcp install
```

This will install the GUI plugin and interactively offer you to install the supported agent plugins.

### Manual installation

<details>

<summary>Expand manual installation details...</summary>

#### IDA GUI Plugin

To support IDA GUI instances when using Hex-Rays IDA MCP, install the plugin:

```bash
uvx ida-hcli plugin install https://github.com/HexRaysSA/ida-mcp
# or if you have hcli installed:
hcli plugin install https://github.com/HexRaysSA/ida-mcp
```

_Note_: Without the GUI plugin, IDA MCP will only work headlessly.

#### [Claude Code](https://claude.com/product/claude-code)

```bash
# Add Hex-Rays marketplace
claude plugin marketplace add HexRaysSA/claude-marketplace
# Install plugin
claude plugin install ida-mcp@HexRaysSA
# Update to latest version
claude plugin update ida-mcp@HexRaysSA
```

#### [Codex CLI](https://learn.chatgpt.com/docs/codex/cli)

```bash
# Add Hex-Rays marketplace
codex plugin marketplace add HexRaysSA/codex-marketplace
# Install plugin
codex plugin add ida-mcp@HexRaysSA
```

#### [GitHub Copilot CLI](https://github.com/features/copilot/cli)

```bash
# Add Hex-Rays marketplace
copilot plugin marketplace add HexRaysSA/copilot-marketplace
# Install plugin
copilot plugin install ida-mcp@HexRaysSA
# Update to latest version
copilot plugin update ida-mcp
```

#### [Pi](https://pi.dev/)

```bash
# Install extension
pi install git:github.com/HexRaysSA/ida-mcp@latest
# Update to latest version
pi update --extensions
```

#### [oh-my-pi](https://github.com/can1357/oh-my-pi)

```bash
# Install extension
omp plugin install github:HexRaysSA/ida-mcp#latest
# Update to latest version
omp plugin upgrade
```

#### Other agents

Configure a regular stdio MCP server in your MCP JSON configuration:

```json
{
  "mcpServers": {
    "ida": {
      "command": "uvx",
      "args": [
        "--exclude-newer=1s",
        "ida-mcp",
        "stdio",
        "--agent=my-agent"
      ]
    }
  }
}
```

`uvx` resolves the latest stable `ida-mcp` release from PyPI, so this
configuration does not need to be updated for each release.

`--agent=my-agent` is a human-chosen label (like `claude-code`, `cursor`,
`my-custom-agent`, etc.) used to differentiate sessions in the dashboard.

</details>

## Commands

Every invocation requires a subcommand:

```bash
# MCP server over standard input/output
uvx ida-mcp stdio --agent=my-agent

# MCP server over Streamable HTTP
uvx ida-mcp http --host 127.0.0.1 --port 8737

# Inspect session log
uvx ida-mcp dashboard --open

# Export session logs for troubleshooting
uvx ida-mcp logs
```

### Example Usage

Start your agent harness and ask it something like:

> Reverse /path/to/sample.elf in IDA for me

To test the GUI integration, open something in IDA and ask your harness:

> What do I have open in the IDA GUI?

## Developers: IDA Nexus

The IDA MCP project is built on [IDA Nexus](https://github.com/HexRaysSA/ida-nexus),
which allows multiple clients to seamlessly share and operate on IDA databases.

You can build your own tools on top of the `ida-nexus` library, see
[the documentation](https://github.com/HexRaysSA/ida-nexus/blob/main/README.md#python-package-developers)
for more information.
