#!/usr/bin/env python3
"""Open a target with idalib and report what IDA did with the on-disk state.

With ``--output-database`` the command options carry ``-c -o<path>``, which is
exactly what an ida-nexus worker is spawned with when the expected ``.i64``
does not exist yet. Without it, IDA takes its normal recovery path.

Usage: probe_open.py <executable-or-idb> [--output-database]
"""

import sys

import idapro  # noqa: F401  (must precede the IDAPython imports)

from ida_domain import Database
from ida_domain.database import IdaCommandOptions


def main() -> int:
    target = sys.argv[1]
    with_output = "--output-database" in sys.argv[2:]
    options = IdaCommandOptions(
        auto_analysis=False,
        output_database=f"{target}.i64" if with_output else None,
    )
    print(f"ida args: {options.build_args()}", flush=True)
    idapro.enable_console_messages(True)
    database = Database.open(target, args=options, save_on_close=False)

    import ida_funcs
    import ida_name

    ea = ida_funcs.get_func_ea_by_num(0)
    print(f"first function: {ea:#x} {ida_name.get_name(ea)}", flush=True)
    print(f"function count: {ida_funcs.get_func_qty()}", flush=True)
    database.close(save=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
