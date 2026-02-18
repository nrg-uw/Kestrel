#!/usr/bin/env python3
"""
Basic port + multicast + IPv4 setup for our Tofino testbed.

- Brings up specific front-panel ports with speed/FEC/AN settings
- Programs per-ingress multicast groups and DMAC broadcast map
- Installs static IPv4 host forwarding rules

Reads BFRT target from:
  - env: BFRTCTL_HOST / BFRTCTL_PORT / BFRTCTL_PIPE_ID / BFRTCTL_DEVICE_ID
  - or ~/.config/bfrt_controller/config.yaml
"""

from __future__ import annotations
import logging
from bfrt.controller import Controller
from bfrt.helpers import setup_ports, configure_multicast

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def program_ipv4_forwarding(c: Controller) -> None:
    """Install static forwarding rules to route IPs to dev ports."""
    logging.info("Programming Ingress.Forward.ipv4_host_table")

    # Best-effort: pretty-print IPv4 in dumps if supported by your SDE
    try:
        c.setup_tables(["Ingress.Forward.ipv4_host_table"])
        c.add_annotation("Ingress.Forward.ipv4_host_table", "hdr.ipv4.dst_addr", "ipv4")
    except Exception:
        pass

    entries = [
        ([("hdr.ipv4.dst_addr", "192.168.44.101")], "Ingress.Forward.send", [("port", 66)]),
        ([("hdr.ipv4.dst_addr", "192.168.44.102")], "Ingress.Forward.send", [("port", 67)]),
        ([("hdr.ipv4.dst_addr", "192.168.44.13")],  "Ingress.Forward.send", [("port", 64)]),
        ([("hdr.ipv4.dst_addr", "192.168.44.18")],  "Ingress.Forward.send", [("port", 16)]),
        ([("hdr.ipv4.dst_addr", "192.168.44.203")], "Ingress.Forward.send", [("port", 17)]),
        ([("hdr.ipv4.dst_addr", "192.168.44.201")], "Ingress.Forward.send", [("port", 18)]),
        ([("hdr.ipv4.dst_addr", "192.168.44.128")], "Ingress.Forward.send", [("port", 19)]),
        ([("hdr.ipv4.dst_addr", "192.168.44.12")],  "Ingress.Forward.send", [("port", 64)]),
    ]

    c.program_table("Ingress.Forward.ipv4_host_table", entries)


def main() -> None:
    c = Controller()

    # 1) Bring up ports (UW wiring list is in bfrt/helpers.setup_ports)
    setup_ports(c)

    # 2) Multicast: map each ingress dev_port to its own group, program DMAC broadcast table
    #    These are dev_port IDs (match your pipeline’s ig_intr_md.ingress_port)
    ingress_dev_ports = [16, 17, 18, 19, 64, 66, 67]
    configure_multicast(c, ingress_dev_ports)

    # 3) Static IPv4 forwarding
    program_ipv4_forwarding(c)

    c.tear_down()


if __name__ == "__main__":
    main()
