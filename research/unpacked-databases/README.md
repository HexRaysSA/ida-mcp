# Unpacked databases left behind by crashed workers

Research notes for HexRaysSA/ida-nexus#46 (*audit handling of unpacked databases*).
Everything below was measured, not inferred.

## Environment

- IDA Pro 9.4 (x64 Linux), installed with `hcli ida install --download-id ida-pro:latest`.
- `idapro` 0.0.10, `ida-domain` (as resolved by `ida-nexus==0.8.1`), Python 3.11.
- ida-nexus 0.8.1 installed from a source checkout; `IDA_NEXUS_STATE_DIR` pointed at a scratch directory.
- Target: `tests/crackme03.elf` from the ida-nexus repository.

## Background: what "unpacked" means on disk

While a database is open, IDA keeps it unpacked as `<name>.id0`, `.id1`, `.id2`,
`.nam`, `.til` next to the input file. A clean close packs those into `<name>.i64`
and deletes them. A worker killed with SIGKILL (OOM, segfault, container teardown,
lost machine) leaves the unpacked set on disk; a packed `.i64` exists only if the
session had already saved at least once.

IDA holds an advisory `flock(LOCK_EX)` on `.id0`, `.id1` and `.nam` for the whole
time a database is open (verified via `/proc/locks` and `/proc/<pid>/fd`). The
kernel drops those locks when the owner dies, so `flock(LOCK_EX|LOCK_NB)` on
`.id0` is an exact, race-free test for "is a live IDA using this unpacked
database?" -- and it sees *any* IDA, including a GUI that has no Nexus plugin.
`.til` is not locked; probe `.id0`.

## Confirmed behavior

### 1. Plain idalib open (no `-c`/`-o`), stale unpacked set present

IDA auto-answers its own prompts in batch mode and the answers are visible only
with `idapro.enable_console_messages(True)`:

| On-disk state | IDA's auto-answer | Result |
|---|---|---|
| unpacked only, intact | `Database ... already exists. Do you want to overwrite it? -> Load existing`, then `... is not closed. Do you want IDA to repair it? -> Repair` | `open_database` returns 0; the crashed session's data is recovered (a rename flushed before the kill survived) |
| unpacked only, `.id0` damaged | same two answers | repair runs, reports `Database still contains inconsistencies`, then `Database is empty`; `open_database` returns **4** (failure) |
| unpacked + packed `.i64` | `IDA has found an unpacked version of database ... It appears IDA did not close properly; it is probably safer to restart your work from the packed database -> Restore packed base` | opens the packed base; everything since the last save is discarded |

So IDA already implements the issue's "desired behavior" for the packed case.
The gap is everything else.

### 2. ida-nexus worker spawn, stale unpacked set present, **no** packed `.i64`

`_resolver._build_worker_command()` passes `--output-database <expected_idb>`
whenever the expected `.i64` does not exist. `ida_domain.database.IdaCommandOptions`
turns `output_database` into `-c -o"<path>"` -- and `-c` means *delete the old
database and create a new one* (`database.py:263`, "Implies new_database").

Measured through the public API (`crash_worker.py` then a reopen):

- worker opens `crackme03.elf`, renames a function, `flush_buffers()` writes it to `.id0`, SIGKILL;
- the next `DatabaseHandle.open()` succeeds in 0.5s, the rename is gone, `.id0`/`.id1` have **new inodes**.

The crashed session is silently deleted and re-analyzed from the executable. No
error, no warning, nothing in the worker log, nothing in the `open_database`
result. This is the concrete answer to the issue's first question: Nexus does not
crash and does not recover -- it destroys.

### 3. Same path, but a **live** IDA owns the unpacked database

The `-c` open ignores the fact that another process is using those files:

- a plain second open is correctly refused (`DatabaseError: Failed to open database ...`);
- the `-c -o` open **succeeds**, taking over the live session's files;
- when the original process later closes, it saves into files that no longer exist and produces **no `.i64` at all** -- the directory is left with a stray `.til` and nothing else.

Reachable today whenever a foreign IDA (a GUI without the Nexus plugin, another
idalib program) is analyzing a target that has no `.i64` yet and an agent opens the
same path through MCP. Both databases are lost.

### 4. Save converts a crash into a recoverable crash

With `handle.save_database()` called before the SIGKILL, the `.i64` exists, so the
next open takes IDA's "Restore packed base" path: the saved rename survives, the
post-save rename does not. This is exactly the issue's third point, and it is the
cheapest mitigation available.

### 5. Adjacent bugs found while reproducing (not #46, worth their own issues)

- **Startup autoanalysis never runs under idalib.** Every worker log contains
  `RuntimeError: Function can be called from the main thread only` from
  `_server._run_startup_autoanalysis -> _runtime.advance_autoanalysis ->
  ida_auto.auto_is_ok()` (ida-nexus 0.8.1, IDA 9.4, idapro 0.0.10).
- **SIGTERM is not acted on while the worker is idle.** The handler sets a flag and
  calls `stop_serving()`, but Python only runs the handler when the interpreter
  regains control; blocked in `ida_kernwin.serve()`, a worker sat for 40s+ after
  SIGTERM and only shut down (and saved) once an HTTP request arrived. Any
  supervisor that does SIGTERM-then-SIGKILL therefore *always* produces the
  unpacked leftovers this issue is about.
- **Worker logs do not capture IDA kernel output.** The recovery/repair decisions
  above are invisible in `logs/<record-id>.log` because console messages are off.

## Reproducing

```bash
uv venv .venv && uv pip install ida-domain /path/to/ida-nexus
export IDA_NEXUS_STATE_DIR=$PWD/state

# 2: crash with no packed base, then reopen and watch the data disappear
mkdir case && cp crackme03.elf case/
.venv/bin/python crash_worker.py case/crackme03.elf
.venv/bin/python flock_probe.py case/crackme03.elf.id0     # -> stale
.venv/bin/python probe_open.py case/crackme03.elf          # plain: recovers + repairs
.venv/bin/python probe_open.py case/crackme03.elf --output-database  # -c: destroys

# 4: save first, then crash -- the packed base survives
.venv/bin/python crash_worker.py case/crackme03.elf --save
```
