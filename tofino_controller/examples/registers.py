#!/usr/bin/env python3
"""
Read or clear BFRT registers (batched), with optional CSV dump and Top-K display.

Usage:
  # Default (read one register, top-10)
  ./build.sh examples/registers.py

  # Clear a register
  ./build.sh 'python /tmp/tofino_controller/examples/registers.py --mode clear --reg Ingress.QoSMeter.drop_count_register'

  # Read multiple registers, write CSVs, show top-20 on pipe 0
  ./build.sh 'python /tmp/tofino_controller/examples/registers.py --mode read \
      --reg Ingress.QoSMeter.drop_count_register \
      --reg Egress.IntStats.packet_count_register \
      --top-k 20 --pipe 0 --output /tmp'
"""

from __future__ import annotations
import argparse
import csv
import logging
import os
from pathlib import Path
from typing import Any, Iterable, List, Tuple

from bfrt.controller import Controller
from bfrt.vendor.bfrt_grpc import client as gc  # stable tuple builders

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def table_exists(c: Controller, name: str) -> bool:
    try:
        c.session.bfrt_info.table_get(name)
        return True
    except Exception:
        return False


def read_register_batched(c: Controller, reg_name: str, pipe: int = 0) -> List[Tuple[int, Any]]:
    """Return list of (index, value) across the whole register, reading from HW."""
    t = c.session.bfrt_info.table_get(reg_name)
    target = gc.Target(c.session.device_id, c.session.pipe_id)  # default target
    # For robust per-pipe extraction, we still select value for the requested pipe from the data
    entries = t.entry_get(target, flags={"from_hw": True})
    out: List[Tuple[int, Any]] = []

    for data, key in entries:
        dd = data.to_dict()
        kd = key.to_dict()
        idx = kd.get("$REGISTER_INDEX", {}).get("value", None)

        # Try common layouts:
        val: Any
        if f"{reg_name}.f1" in dd:
            val = dd[f"{reg_name}.f1"][pipe]
        elif f"{reg_name}.first" in dd and f"{reg_name}.second" in dd:
            val = (dd[f"{reg_name}.first"][pipe], dd[f"{reg_name}.second"][pipe])
        else:
            # Fallback: take the first field and try to pick the per-pipe entry if it’s a list
            if dd:
                first_key = next(iter(dd))
                field_val = dd[first_key]
                try:
                    # Many SDEs return per-pipe lists
                    val = field_val[pipe]
                except Exception:
                    val = field_val
            else:
                val = 0

        if idx is None:
            # Fallback: if index missing (shouldn't happen), skip
            continue

        out.append((idx, val))

    return out


def clear_register(c: Controller, reg_name: str) -> None:
    """Delete all entries in a register table (clears values)."""
    t = c.session.bfrt_info.table_get(reg_name)
    target = gc.Target(c.session.device_id, c.session.pipe_id)
    try:
        t.entry_del(target)
        logging.info("Cleared register: %s", reg_name)
    except Exception as e:
        logging.error("Failed to clear register %s: %s", reg_name, e)


def _value_as_number(v: Any) -> int:
    """Sort key: handle tuples (sum), ints, and anything else."""
    if isinstance(v, tuple):
        return sum(int(x) for x in v)
    try:
        return int(v)
    except Exception:
        return 0


def dump_register(c: Controller, reg_name: str, csv_path: Path | None, top_k: int, pipe: int) -> None:
    if not table_exists(c, reg_name):
        print(f"[WARN] Register '{reg_name}' does not exist.")
        return

    print(f"[INFO] Reading register: {reg_name} (pipe {pipe})")
    values = read_register_batched(c, reg_name, pipe=pipe)
    if not values:
        print(f"[INFO] No entries in {reg_name}.")
        return

    # Filter non-zero entries
    nonzero = [(i, v) for (i, v) in values if v != 0 and v != (0, 0)]
    if not nonzero:
        print(f"[INFO] No non-zero entries found in {reg_name}.")
        return

    # Sort by numeric value desc
    nonzero.sort(key=lambda x: _value_as_number(x[1]), reverse=True)

    # CSV output
    if csv_path:
        # If output is a directory, write a default file name inside
        if csv_path.is_dir():
            csv_file = csv_path / f"{reg_name.replace('.', '_')}.csv"
        else:
            csv_file = csv_path
        csv_file.parent.mkdir(parents=True, exist_ok=True)
        with csv_file.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index", "value"])
            for i, v in nonzero:
                w.writerow([i, v if not isinstance(v, tuple) else ";".join(map(str, v))])
        print(f"[INFO] Wrote {len(nonzero)} entries to {csv_file}")

    # Print Top-K
    print(f"\n[INFO] Top {top_k} entries in {reg_name}:")
    for i, (idx, val) in enumerate(nonzero[:top_k], 1):
        print(f"{i:>3}. idx={idx:<6} value={val}")


def parse_args():
    ap = argparse.ArgumentParser(description="Clear or read telemetry registers.")
    ap.add_argument("--mode", choices=["clear", "read"], default="clear", help="Operation mode")
    ap.add_argument("--reg", action="append", dest="registers",
                    help="Register name (repeat for multiple). Default: Ingress.QoSMeter.drop_count_register")
    ap.add_argument("--output", type=str,
                    help="CSV file or directory to save values (read mode)")
    ap.add_argument("--top-k", type=int, default=10, help="Show top-K (read mode)")
    ap.add_argument("--pipe", type=int, default=0, help="Pipe index to read (default 0)")
    return ap.parse_args()


def main():
    args = parse_args()
    regs = args.registers or ["Ingress.QoSMeter.drop_count_register"]

    c = Controller()
    try:
        # Preload tables if you want errors earlier
        c.setup_tables(regs)

        if args.mode == "clear":
            for reg in regs:
                clear_register(c, reg)

        else:  # read
            csv_path = Path(args.output) if args.output else None
            for reg in regs:
                dump_register(c, reg_name=reg, csv_path=csv_path, top_k=args.top_k, pipe=args.pipe)

    finally:
        c.tear_down()


if __name__ == "__main__":
    main()
