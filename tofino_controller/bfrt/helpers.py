from __future__ import annotations
import logging
from typing import List, Dict
from bfrt.controller import Controller
from bfrt.recipes import ports, multicast

def setup_ports(c: Controller):
    logging.info("Setting up ports")
    ports.add_many(c.session, [
        (14, 0, 10, "none", "disable"),
        (14, 1, 10, "none", "disable"),
        (14, 2, 10, "none", "disable"),
        (14, 3, 10, "none", "disable"),
        (33, 0, 10, "none", "disable"),
        (33, 2, 10, "none", "enable"),
        (33, 3, 10, "none", "enable"),
        (15, 0, 100, "none", "enable"),
        (16, 0, 100, "none", "enable"),
        (17, 0, 40, "none", "enable"),
        (18, 0, 40, "none", "enable"),
        (19, 0, 100, "none", "disable"),
        (20, 0, 100, "none", "disable"),
        (21, 0, 100, "none", "disable"),
        (22, 0, 100, "none", "disable"),
    ])

def configure_multicast(c: Controller, ingress_dev_ports: List[int]):
    logging.info("Configuring multicast for dev ports: %s", ingress_dev_ports)
    ingress_to_mgid: Dict[int, int] = {}
    next_gid = 1
    for ingress in ingress_dev_ports:
        ingress_to_mgid[ingress] = next_gid
        # upsert node for this group with "others" as egress set
        others = [p for p in ingress_dev_ports if p != ingress]
        multicast.upsert_node(c.session, node_id=next_gid, dev_ports=others)
        multicast.upsert_group(c.session, mgid=next_gid, node_ids=[next_gid])
        next_gid += 1

    multicast.program_ingress_broadcast_map(
        c.session, table="Ingress.Dmac.broadcast_table", ingress_to_mgid=ingress_to_mgid
    )
