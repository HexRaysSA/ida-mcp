"""Hold a database open in an IDA that ida-nexus does not know about.

Opens the target with idalib, renames the first function so the session has
work worth losing, prints ``READY``, and then waits for a line on stdin before
closing and saving. Used by the tests to model a GUI without the ida-mcp
plugin working on the same target as a Nexus worker.

Usage: hold_database.py <executable-or-idb> <mark>
"""

from __future__ import annotations

import sys


def main() -> int:
    target, mark = sys.argv[1], sys.argv[2]

    import idapro

    if idapro.open_database(target, run_auto_analysis=True) != 0:
        print("OPEN-FAILED", flush=True)
        return 1

    import ida_funcs
    import ida_name

    ea = ida_funcs.get_func_ea_by_num(0)
    ida_name.set_name(ea, mark, ida_name.SN_FORCE)
    print(f"READY {ea:#x} {ida_name.get_name(ea)}", flush=True)

    sys.stdin.readline()
    idapro.close_database(save=True)
    print("CLOSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
