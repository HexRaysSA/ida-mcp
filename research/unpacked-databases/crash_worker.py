#!/usr/bin/env python3
"""Reproduce a crashed ida-nexus worker and inspect what it leaves behind.

Opens the target through the public Nexus API, renames the first function,
optionally saves, then SIGKILLs the worker -- the same on-disk state a worker
that is OOM-killed, segfaults, or loses its machine leaves behind.

Usage: crash_worker.py <executable> [--save]
Set IDA_NEXUS_STATE_DIR to keep the registry out of your real state directory.
"""

import json
import os
import pathlib
import signal
import sys
import time

from ida_nexus import DatabaseHandle

RENAME = """
import ida_funcs, ida_name
ea = ida_funcs.get_func_ea_by_num(0)
ida_name.set_name(ea, {mark!r}, ida_name.SN_FORCE)
"""


def listing(directory: pathlib.Path, tag: str) -> None:
    print(f"--- {tag}", flush=True)
    for path in sorted(directory.iterdir()):
        stat = path.stat()
        print(f"    {path.name:36s} size={stat.st_size:9d} ino={stat.st_ino}", flush=True)


def main() -> int:
    target = pathlib.Path(sys.argv[1])
    save = "--save" in sys.argv[2:]
    directory = target.parent

    listing(directory, "before open")
    handle = DatabaseHandle.open(str(target))
    print(f"worker pid {handle.instance.pid}", flush=True)
    print(handle.execute_python(RENAME.format(mark="SAVED_MARK" if save else "LOST_MARK")), flush=True)
    if save:
        print(f"save: {json.dumps(handle.save_database())}", flush=True)
    listing(directory, "while the worker is alive")

    os.kill(handle.instance.pid, signal.SIGKILL)
    time.sleep(2)
    listing(directory, "after SIGKILL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
