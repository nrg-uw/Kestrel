#!/usr/bin/env python3
"""
Configure INT watchlist (egress), postcard generation, and mirror session.

- Mirror session ($mirror.cfg) to export postcards
- Egress.IntWatchList.int_watchlist_table: mark packets to report
- Egress.IntPostcard.int_postcard_table: build INT-XD postcards
"""

from __future__ import annotations
import logging
from bfrt.controller import Controller
from bfrt.recipes import mirror

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def mac_bytes(mac: str) -> bytearray:
    return bytearray(int(b, 16) for b in mac.split(":"))

def ipv4_bytes(ip: str) -> bytearray:
    return bytearray(int(b) for b in ip.split("."))

def configure_mirror_session(c: Controller) -> None:
    # postcard export: EGRESS mirror, truncated
    mirror.upsert(c.session, sid=1, egress_port=17, max_pkt_len=73, direction="EGRESS")

def program_watchlist_table(c: Controller) -> None:
    """
    Mark packets for postcard export.
    NOTE: we pass IPv4 keys as bytes so this works regardless of runtime annotations.
    """
    logging.info("Programming Egress.IntWatchList.int_watchlist_table")
    c.setup_tables(["Egress.IntWatchList.int_watchlist_table"])

    entries = [
        (
            [
                ("hdr.ipv4.src_addr", ipv4_bytes("192.168.44.13")),
                ("hdr.ipv4.dst_addr", ipv4_bytes("192.168.44.18")),
                ("hdr.ipv4.protocol", 17),           # UDP
                ("meta.src_port", 0, 0),             # ternary wildcard (mask=0)
                ("meta.dst_port", 0, 0),             # ternary wildcard (mask=0)
            ],
            "Egress.IntWatchList.mark_to_report",
            [],
        ),
        (
            [
                ("hdr.ipv4.src_addr", ipv4_bytes("192.168.44.201")),
                ("hdr.ipv4.dst_addr", ipv4_bytes("192.168.44.18")),
                ("hdr.ipv4.protocol", 17),
                ("meta.src_port", 0, 0),
                ("meta.dst_port", 0, 0),
            ],
            "Egress.IntWatchList.mark_to_report",
            [],
        ),
    ]
    c.program_table("Egress.IntWatchList.int_watchlist_table", entries)

def program_postcard_table(c: Controller) -> None:
    """
    Provide postcard header constants: source MAC/IP and collector info.
    meta.pkt_type==2 should match our pipeline's condition for INT postcard generation.
    """
    logging.info("Programming Egress.IntPostcard.int_postcard_table")
    c.setup_tables(["Egress.IntPostcard.int_postcard_table"])

    entries = [
        (
            [("meta.pkt_type", 2)],
            "Egress.IntPostcard.generate_postcard",
            [
                ("src_mac",       mac_bytes("00:1A:2B:3C:4D:5E")),
                ("src_ip",        ipv4_bytes("192.168.44.44")),
                ("collector_mac", mac_bytes("e4:1d:2d:09:c7:50")),
                ("collector_ip",  ipv4_bytes("192.168.44.203")),
                ("collector_port", 4567),
            ],
        )
    ]
    c.program_table("Egress.IntPostcard.int_postcard_table", entries)

def main() -> None:
    c = Controller()

    # Ensure tables are loaded before programming
    c.setup_tables([
        "Egress.IntWatchList.int_watchlist_table",
        "Egress.IntPostcard.int_postcard_table",
    ])

    configure_mirror_session(c)
    program_watchlist_table(c)
    program_postcard_table(c)

    c.tear_down()

if __name__ == "__main__":
    main()
