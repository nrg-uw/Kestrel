from __future__ import annotations
from bfrt.logging import get_logger
from bfrt.vendor.bfrt_grpc import client as gc

log = get_logger(__name__)

def upsert(session, sid: int, egress_port: int, *, max_pkt_len: int = 16384, direction: str = "INGRESS") -> None:
    """
    Create or update a mirror session in $mirror.cfg.

    Args:
      sid: mirror session id
      egress_port: device port to mirror to
      max_pkt_len: truncation length for mirrored packets
      direction: "INGRESS" or "EGRESS" (string enum expected by SDE)
    """
    t = session.bfrt_info.table_get("$mirror.cfg")
    key  = t.make_key([gc.KeyTuple("$sid", sid)])
    data = t.make_data(
        [
            gc.DataTuple("$direction",              str_val=direction),
            gc.DataTuple("$ucast_egress_port",      egress_port),
            gc.DataTuple("$ucast_egress_port_valid", bool_val=True),
            gc.DataTuple("$session_enable",          bool_val=True),
            gc.DataTuple("$max_pkt_len",             max_pkt_len),
        ],
        "$normal",
    )
    try:
        t.entry_add(session.target, [key], [data])
        log.info("Mirror add: sid=%s port=%s dir=%s max_len=%s", sid, egress_port, direction, max_pkt_len)
    except gc.BfruntimeReadWriteRpcException:
        t.entry_mod(session.target, [key], [data])
        log.info("Mirror update: sid=%s", sid)
