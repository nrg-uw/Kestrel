from __future__ import annotations
from typing import Iterable, Optional, Dict, Any, List, Tuple
from bfrt.logging import get_logger
from bfrt.vendor.bfrt_grpc import client as gc

log = get_logger(__name__)

# ---------- enum helpers ----------
_SPEED = {10: "BF_SPEED_10G", 25: "BF_SPEED_25G", 40: "BF_SPEED_40G", 50: "BF_SPEED_50G", 100: "BF_SPEED_100G"}
_FEC   = {"none": "BF_FEC_TYP_NONE", "fc": "BF_FEC_TYP_FC", "rs": "BF_FEC_TYP_RS"}
_AN    = {"default": "PM_AN_DEFAULT", "enable": "PM_AN_FORCE_ENABLE", "disable": "PM_AN_FORCE_DISABLE"}

# ---------- lookups ----------
def fp_lane_to_dev(session, fp_port: int, lane: int) -> int:
    """Front-panel+lane -> dev_port via $PORT_HDL_INFO (raises if not found)."""
    t = session.bfrt_info.table_get("$PORT_HDL_INFO")
    key = t.make_key([gc.KeyTuple("$CONN_ID", fp_port), gc.KeyTuple("$CHNL_ID", lane)])
    it  = t.entry_get(session.target, [key], {"from_hw": False})
    try:
        return int(next(it)[0].to_dict()["$DEV_PORT"])
    except Exception:
        raise RuntimeError(f"Port {fp_port}/{lane} not found in $PORT_HDL_INFO")

def list_active(session) -> List[int]:
    """Return active dev_ports (best-effort)."""
    t = session.bfrt_info.table_get("$PORT")
    out: List[int] = []
    for data, key in t.entry_get(session.target, [], {"from_hw": False}):
        kd = key.to_dict()
        dev = kd.get("$DEV_PORT") or kd.get("dev_port")
        if isinstance(dev, dict): dev = dev.get("value")
        if dev is not None: out.append(int(dev))
    out.sort()
    return out

# ---------- mutations ----------
def add(session, fp_port: int, lane: int, speed_gbps: int, fec: str = "none", an: str = "disable") -> int:
    dev = fp_lane_to_dev(session, fp_port, lane)
    t = session.bfrt_info.table_get("$PORT")
    key  = t.make_key([gc.KeyTuple("$DEV_PORT", dev)])
    data = t.make_data([
        gc.DataTuple("$SPEED",            str_val=_SPEED[speed_gbps]),
        gc.DataTuple("$FEC",              str_val=_FEC[fec]),
        gc.DataTuple("$AUTO_NEGOTIATION", str_val=_AN[an]),
        gc.DataTuple("$PORT_ENABLE",      bool_val=True),
    ])
    t.entry_add(session.target, [key], [data])
    log.info("Port add: %s/%s -> dev %s (%sG, fec=%s, an=%s)", fp_port, lane, dev, speed_gbps, fec, an)
    return dev

def remove(session, fp_port: int, lane: int) -> int:
    dev = fp_lane_to_dev(session, fp_port, lane)
    t = session.bfrt_info.table_get("$PORT")
    key = t.make_key([gc.KeyTuple("$DEV_PORT", dev)])
    t.entry_del(session.target, [key])
    log.info("Port del: %s/%s (dev %s)", fp_port, lane, dev)
    return dev

def add_many(session, ports: Iterable[Tuple[int,int,int,str,str]]) -> None:
    """ports: (fp_port, lane, speed_gbps, fec, an) tuples"""
    for fp, lane, speed, fec, an in ports:
        add(session, fp, lane, speed, fec, an)

# ---------- stats ----------
def stats(session, dev_ports: Optional[Iterable[int]] = None) -> Dict[int, Dict[str, Any]]:
    t = session.bfrt_info.table_get("$PORT_STAT")
    res: Dict[int, Dict[str, Any]] = {}

    if dev_ports is None:
        it = t.entry_get(session.target, [], {"from_hw": True})
    else:
        keys = [t.make_key([gc.KeyTuple("$DEV_PORT", int(d))]) for d in dev_ports]
        it = t.entry_get(session.target, keys, {"from_hw": True})

    for data, key in it:
        kd = key.to_dict()
        dev = kd.get("$DEV_PORT") or kd.get("dev_port")
        if isinstance(dev, dict): dev = dev.get("value")
        res[int(dev)] = data.to_dict()
    return res
