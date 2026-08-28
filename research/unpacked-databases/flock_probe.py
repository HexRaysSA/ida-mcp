#!/usr/bin/env python3
"""Report whether a live IDA owns an unpacked database.

IDA holds an advisory ``flock(LOCK_EX)`` on ``.id0``/``.id1``/``.nam`` for as
long as a database is open, and the kernel releases those locks when the owner
dies. A non-blocking ``LOCK_EX`` on ``.id0`` therefore distinguishes "another
IDA is working here" from "a previous IDA died here", for any IDA process --
registered with Nexus or not.

Usage: flock_probe.py <path-to-.id0> [...]
"""

import fcntl
import os
import sys


def probe(path: str) -> str:
    if not os.path.exists(path):
        return "missing"
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "stale (no live owner)"
    except OSError as exc:
        return f"live owner holds the lock ({exc.errno} {exc.strerror})"
    finally:
        os.close(fd)


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        print(f"{argument}: {probe(argument)}")
