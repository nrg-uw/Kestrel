#!/usr/bin/env python3
"""
Configure RFC 2697 trTCM meters per (TEID, QFI).

Defaults:
  BASE_TEID = 0x0001
  UE_COUNT  = 110
  QFIs      = keys of DEFAULT_QFI_METER_PARAMS (below)

Overrides:
  - CLI flags: --base-teid, --ues, --qfi (repeatable), --config JSON
  - JSON file can redefine the per-QFI meter params.

Run:
  python examples/metering.py
  python examples/metering.py --ues 50 --base-teid 0x0100
  python examples/metering.py --config /tmp/qfi_meters.json
"""

from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
from typing import Dict

from bfrt.controller import Controller

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Token bucket parameters per QFI (CIR, PIR in kbps; CBS, PBS in kbits)
DEFAULT_QFI_METER_PARAMS: Dict[int, Dict[str, int]] = {
    1: {"CIR_KBPS": 20_000,  "PIR_KBPS": 100_000, "CBS_KBITS": 120,  "PBS_KBITS": 360  },  # URLLC (QID0)
    5: {"CIR_KBPS": 20_000,  "PIR_KBPS": 100_000, "CBS_KBITS": 480,  "PBS_KBITS": 1440 },  # IoT (QID1)
    3: {"CIR_KBPS": 80_000,  "PIR_KBPS": 800_000, "CBS_KBITS": 600,  "PBS_KBITS": 1500 },  # Gaming (QID2)
    7: {"CIR_KBPS": 50_000,  "PIR_KBPS": 100_000, "CBS_KBITS": 180,  "PBS_KBITS": 400  },  # Video call (QID3)
    4: {"CIR_KBPS": 250_000, "PIR_KBPS": 450_000, "CBS_KBITS": 700,  "PBS_KBITS": 1440 },  # YouTube (QID4)
    2: {"CIR_KBPS": 150_000, "PIR_KBPS": 350_000, "CBS_KBITS": 850,  "PBS_KBITS": 2160 },  # Twitch (QID5)
    6: {"CIR_KBPS": 100_000, "PIR_KBPS": 500_000, "CBS_KBITS": 960,  "PBS_KBITS": 2880 },  # Downloads (QID6)
    8: {"CIR_KBPS": 450_000, "PIR_KBPS": 800_000, "CBS_KBITS": 1500, "PBS_KBITS": 2500 },  # File sync (QID7)
    9: {"CIR_KBPS": 80_000,  "PIR_KBPS": 220_000, "CBS_KBITS": 600,  "PBS_KBITS": 1800 },  # Browsing (QID7)
}

TABLE = "Ingress.QoSMeter.meter_table"
ACTION = "Ingress.QoSMeter.set_color"


def load_params(config_path: str | None) -> Dict[int, Dict[str, int]]:
    if not config_path:
        return DEFAULT_QFI_METER_PARAMS
    p = Path(config_path)
    with p.open() as f:
        raw = json.load(f)
    # normalize keys/values to int
    out: Dict[int, Dict[str, int]] = {}
    for k, v in raw.items():
        out[int(k)] = {
            "CIR_KBPS": int(v["CIR_KBPS"]),
            "PIR_KBPS": int(v["PIR_KBPS"]),
            "CBS_KBITS": int(v["CBS_KBITS"]),
            "PBS_KBITS": int(v["PBS_KBITS"]),
        }
    return out


def parse_args():
    ap = argparse.ArgumentParser(description="Program trTCM meters per (TEID,QFI)")
    ap.add_argument("--base-teid", default="0x1",
                    help="Starting TEID (int or hex string, default 0x1)")
    ap.add_argument("--ues", type=int, default=110,
                    help="Number of UEs (TEIDs) starting at base (default 110)")
    ap.add_argument("--qfi", action="append", type=int,
                    help="Limit to specific QFI(s); repeat flag to add more")
    ap.add_argument("--config", help="JSON file overriding per-QFI meter params")
    return ap.parse_args()


def to_int(x: str | int) -> int:
    if isinstance(x, int):
        return x
    s = x.strip().lower()
    return int(s, 16) if s.startswith("0x") else int(s)


def main() -> None:
    args = parse_args()
    base_teid = to_int(args["base_teid"] if isinstance(args, dict) else args.base_teid)
    ue_count  = args["ues"] if isinstance(args, dict) else args.ues
    qfi_params = load_params(args["config"] if isinstance(args, dict) else args.config)

    qfis = sorted(qfi_params.keys())
    if args["qfi"] if isinstance(args, dict) else args.qfi:
        filt = set(args["qfi"] if isinstance(args, dict) else args.qfi)
        qfis = [q for q in qfis if q in filt]

    c = Controller()
    try:
        c.setup_tables([TABLE])
        # purely for nicer dumps if you ever print the table
        try:
            c.add_annotation(TABLE, "hdr.gtpu.teid", "hex")
        except Exception:
            pass

        entries = []
        for ue in range(ue_count):
            teid = base_teid + ue
            for qfi in qfis:
                p = qfi_params[qfi]
                entries.append((
                    [("hdr.gtpu.teid", teid), ("hdr.gtpu_ext_psc.qfi", qfi)],
                    ACTION,
                    [
                        ("$METER_SPEC_CIR_KBPS",  p["CIR_KBPS"]),
                        ("$METER_SPEC_PIR_KBPS",  p["PIR_KBPS"]),
                        ("$METER_SPEC_CBS_KBITS", p["CBS_KBITS"]),
                        ("$METER_SPEC_PBS_KBITS", p["PBS_KBITS"]),
                    ],
                ))

        logging.info("Installing %d meter entries for %d UEs × %d QFIs",
                     len(entries), ue_count, len(qfis))
        c.program_table(TABLE, entries)
        logging.info("Done.")
    finally:
        c.tear_down()


if __name__ == "__main__":
    main()
