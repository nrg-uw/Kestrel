#!/usr/bin/env python3
"""
Configure QFI → Queue (QID) mapping.

- Programs Ingress.QueueMapper.qfi_to_queue_table with QFI→QID pairs.
- Uses default mapping unless CONFIG_FILE is provided.

Usage:
  python examples/queueing.py
  CONFIG_FILE=/tmp/queueing_config.json python examples/queueing.py
"""

from __future__ import annotations
import os
import json
import logging
from bfrt.controller import Controller

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Default QFI → QID mapping for an 8-queue scheduler (0..7).
# QID 7 reserved for best-effort.
DEFAULT_MAPPING = {
    1: 0,  # VoIP / URLLC-like
    5: 1,  # IoT / telemetry
    3: 2,  # Cloud gaming
    7: 3,  # Video call
    4: 4,  # YouTube / buffered video
    2: 5,  # Twitch / live stream
    6: 6,  # File download
    9: 7,  # Browsing (best-effort)
    8: 7,  # File sync (best-effort)
}


def load_mapping() -> dict[int, int]:
    path = os.getenv("CONFIG_FILE")
    if path and os.path.exists(path):
        logging.info("Loading QFI→QID mapping from %s", path)
        with open(path) as f:
            raw = json.load(f)
        # keys may come as strings; normalize to int
        mapping = {int(k): int(v) for k, v in raw.items()}
    else:
        logging.info("Using default QFI→QID mapping")
        mapping = DEFAULT_MAPPING.copy()

    # basic validation: QIDs in 0..7
    bad = {qfi: qid for qfi, qid in mapping.items() if not (0 <= int(qid) <= 7)}
    if bad:
        raise ValueError(f"Invalid QID(s) (must be 0..7): {bad}")
    return mapping


def program_qfi_to_queue(c: Controller, qfi_to_qid: dict[int, int]) -> None:
    """Install QFI→QID mappings."""
    table = "Ingress.QueueMapper.qfi_to_queue_table"
    action = "Ingress.QueueMapper.set_queue"

    logging.info("Programming %s with %d entries", table, len(qfi_to_qid))
    c.setup_tables([table])

    # QFI is an integer match; no special annotations needed.
    entries = [
        ([("hdr.gtpu_ext_psc.qfi", int(qfi))], action, [("qid", int(qid))])
        for qfi, qid in qfi_to_qid.items()
    ]

    c.program_table(table, entries)


def main() -> None:
    c = Controller()
    try:
        mapping = load_mapping()
        program_qfi_to_queue(c, mapping)
        logging.info("Done.")
    finally:
        c.tear_down()


if __name__ == "__main__":
    main()
