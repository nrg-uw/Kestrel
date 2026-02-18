from __future__ import annotations
from typing import List, Dict
from bfrt.logging import get_logger
from bfrt.vendor.bfrt_grpc import client as gc

log = get_logger(__name__)

def upsert_node(session, node_id: int, dev_ports: List[int]) -> None:
    """Create or update a PRE node with the given port list."""
    t = session.bfrt_info.table_get("$pre.node")
    key  = t.make_key([gc.KeyTuple("$MULTICAST_NODE_ID", node_id)])
    data = t.make_data([
        gc.DataTuple("$MULTICAST_RID", 0),
        gc.DataTuple("$DEV_PORT", int_arr_val=dev_ports),
    ])
    try:
        t.entry_add(session.target, [key], [data])
        log.info("PRE node add: node_id=%s ports=%s", node_id, dev_ports)
    except gc.BfruntimeReadWriteRpcException as e:
        # If it exists, update it in-place
        log.info("PRE node exists; updating: node_id=%s", node_id)
        t.entry_mod(session.target, [key], [data])

def delete_node(session, node_id: int) -> None:
    t = session.bfrt_info.table_get("$pre.node")
    key = t.make_key([gc.KeyTuple("$MULTICAST_NODE_ID", node_id)])
    t.entry_del(session.target, [key])
    log.info("PRE node del: node_id=%s", node_id)

def upsert_group(session, mgid: int, node_ids: List[int]) -> None:
    """Create or update a PRE group pointing at the given nodes (L1 fields disabled)."""
    t = session.bfrt_info.table_get("$pre.mgid")
    key  = t.make_key([gc.KeyTuple("$MGID", mgid)])
    data = t.make_data([
        gc.DataTuple("$MULTICAST_NODE_ID",           int_arr_val=node_ids),
        gc.DataTuple("$MULTICAST_NODE_L1_XID_VALID", bool_arr_val=[False]*len(node_ids)),
        gc.DataTuple("$MULTICAST_NODE_L1_XID",       int_arr_val=[0]*len(node_ids)),
    ])
    try:
        t.entry_add(session.target, [key], [data])
        log.info("PRE group add: mgid=%s nodes=%s", mgid, node_ids)
    except gc.BfruntimeReadWriteRpcException:
        log.info("PRE group exists; updating: mgid=%s", mgid)
        t.entry_mod(session.target, [key], [data])

def delete_group(session, mgid: int) -> None:
    t = session.bfrt_info.table_get("$pre.mgid")
    key = t.make_key([gc.KeyTuple("$MGID", mgid)])
    t.entry_del(session.target, [key])
    log.info("PRE group del: mgid=%s", mgid)

def program_ingress_broadcast_map(session, table: str, ingress_to_mgid: Dict[int, int]) -> None:
    """Ingress dev_port -> MGID map in P4 broadcast table (idempotent)."""
    t = session.bfrt_info.table_get(table)
    keys, datas = [], []
    for ingress, mgid in ingress_to_mgid.items():
        k = t.make_key([gc.KeyTuple("ig_intr_md.ingress_port", ingress)])
        d = t.make_data([gc.DataTuple("mcast_grp", mgid)], "Ingress.Dmac.set_mcast_grp")
        keys.append(k); datas.append(d)
    try:
        t.entry_add(session.target, keys, datas)
    except gc.BfruntimeReadWriteRpcException:
        t.entry_mod(session.target, keys, datas)
