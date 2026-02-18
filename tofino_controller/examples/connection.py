#!/usr/bin/env python3
"""
Check BFRT connectivity and print available tables.

Usage:
  python examples/check_connection.py [--limit N]

Environment (optional):
  BFRTCTL_HOST, BFRTCTL_PORT, BFRTCTL_PIPE_ID, BFRTCTL_DEVICE_ID, BFRTCTL_PROGRAM
  or ~/.config/bfrt_controller/config.yaml
"""

from __future__ import annotations
import argparse
from bfrt.controller import Controller


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Show only the first N tables")
    args = ap.parse_args()

    c = Controller()  # reads env/config; connects in BfrtSession.connect()

    # Basic identity
    p4_name = c.session.bfrt_info.p4_name_get()
    print(f"[OK] Connected to BFRT")
    print(f"     Program   : {p4_name}")
    print(f"     Device    : {c.session.device_id}")
    print(f"     Pipe ID   : 0x{c.session.pipe_id:04X}")

    # List tables
    table_names = sorted(c.session.bfrt_info.table_dict.keys())
    total = len(table_names)
    if args.limit and args.limit > 0:
        shown = table_names[:args.limit]
        print(f"\nTables (showing {len(shown)}/{total}):")
        for name in shown:
            print(f"  - {name}")
    else:
        print(f"\nTables ({total}):")
        for name in table_names:
            print(f"  - {name}")

    c.tear_down()


if __name__ == "__main__":
    main()
