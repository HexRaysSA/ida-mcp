# IDA MCP architecture

IDA MCP is an adapter over the protocol-agnostic
[`ida-nexus`](https://github.com/HexRaysSA/ida-nexus) library. Nexus owns IDA
process discovery, authenticated database services, leases, workers, and Python
execution. This repository owns the model-facing MCP protocol, semantic tracing,
agent transcript correlation, dashboard, and support-log archives.

```text
agent / MCP client
        │
        ├─ stdio or Streamable HTTP
        │
    ida_mcp.mcp
        │
        ├─ semantic trace ──> <mcp-state>/sessions/<server-id>.jsonl
        │
        └─ ida_nexus.DatabaseManager
                    │
                    └─ DatabaseHandle lease ──> GUI or idalib worker
```

## Commands

The `ida-mcp` entry point requires one subcommand:

| Command | Responsibility |
|---|---|
| `stdio` | Serve MCP on stdin/stdout until the peer disconnects. |
| `http` | Serve Streamable HTTP in the foreground. |
| `dashboard` | Render semantic traces and linked agent transcripts. |
| `logs` | Build a validated support ZIP from traces, transcripts, and Nexus worker logs. |
| `hook` | Rewrite one Claude, Codex, or Copilot pre-tool payload to attach transcript metadata. |

The CLI imports command implementations lazily. Dashboard, log, and hook
operations therefore do not initialize the MCP server or database manager.

## Tool and database model

The process-wide ZeroMCP server exposes six tools:

- `open_database(path, set_current=True)`
- `execute_python(code, instance_id=None, timeout=360)`
- `reference(query)`
- `list_databases()`
- `save_database(instance_id=None)`
- `close_database(instance_id=None)`

`DatabaseManager` keeps MCP-local instance IDs and one lease for each attached
Nexus database. Separate MCP server processes retain independent leases. Closing
one MCP handle never closes a GUI database or another client's lease.

`execute_python` resolves the target once, waits for initial autoanalysis, and
then performs persistent lease-scoped execution. MCP cancellation sends a
request-owned Nexus cancellation operation and waits for the IDA operation to
unwind before abandoning the MCP response. A failed mutating request is never
retried because it may already have executed.

Stdio EOF, SIGINT, SIGTERM, normal interpreter exit, and explicit HTTP shutdown
release the process's handles. Nexus performs final managed-worker save and
shutdown when the last lease disappears.

## Semantic tracing

One schema-1 JSONL trace is created lazily on the first tool call. Lifecycle-only
connections leave no file. Records emitted before that first call are buffered
until it arrives, including across shutdown, so a tool call that completes after
stdio EOF still writes the `mcp_started` record that correlation depends on.
Every record contains a timestamp, MCP server ID, process ID, and event. Tool calls and outcomes are paired with a `call_id`, and
database lifecycle events emitted during a call inherit that ID.

The trace location is owned by ida-mcp, independent of ida-nexus's own state:

```text
<IDA_MCP_STATE_DIR>/sessions/<mcp-server-id>.jsonl
```

When `IDA_MCP_STATE_DIR` is unset, this resolves to `<IDAUSR>/mcp/sessions`.
Sessions previously lived under ida-nexus's own state directory
(`<IDA_NEXUS_STATE_DIR>/sessions`, normally `<IDAUSR>/nexus/sessions`);
`ida-mcp dashboard` and `ida-mcp logs` still read that legacy location too,
joined with the current one, unless `--sessions-dir` or `--archive` narrows
the search to a single directory.

`IDA_MCP_ID` supplies an optional trusted process-level correlation ID.
Agent hooks place transcript paths in hidden request metadata using the
`<agent-kind>_session_path` convention. Embedded metadata is removed from public
tool arguments before dispatch and copied into semantic records. Only the
session-path key matching the configured `--agent` is retained; without an
agent, no session-path keys are retained. Other metadata is preserved.

Unexpected database disconnection is recorded at warning level. Stdio also
receives a best-effort MCP logging notification under `ida_mcp.database`;
Streamable HTTP tracing remains available even where transport logging is not.

## Dashboard and archives

The stdlib-only dashboard reads local schema-1 traces or a validated support
archive. It correlates tool calls with results, renders Python execution and
errors, tracks database targets, and interleaves supported Claude, Codex,
Copilot, Pi, and OMP transcript events. The dashboard and exporter follow only
`<agent-kind>_session_path` references matching the agent in the trace's first
`mcp_started` record. Without a recorded agent, neither follows transcript paths.
This check also applies to existing traces.

`ida-mcp logs` writes an `ida-mcp-logs` schema-1 ZIP with
`ida-mcp-logs.json` as its table of contents. Collecting the local sessions
directory archives the same traces the dashboard shows and skips lifecycle-only
ones; an explicitly named session file is always archived. The TOC contains checksums,
original-to-archive mappings, per-session transcript references, and missing
references. Nexus operational logs under `<nexus-state>/logs` are included as
supporting diagnostics but remain produced and owned by Nexus.

Archive extraction rejects duplicate or unsafe member names, validates sizes
and hashes, and exposes files through a private temporary directory. Missing
archive references are never resolved against the receiving machine's
filesystem.

## Embedding API

Applications can import `serve_http`, `serve_stdio`, `stop_http_server`, and
`tool` from `ida_mcp.mcp`. `serve_http` accepts a `DatabaseManager` subclass and
constructor arguments, allowing cloud or hub applications to customize database
resolution while retaining the official tools, tracing, cancellation, and
shutdown behavior.

The MCP package has direct dependencies on ZeroMCP and `packaging`. IDA Nexus
has no dependency on this package or on ZeroMCP.
