from __future__ import annotations
from typing import Optional, Any, Dict, List, Tuple
from bfrt.logging import get_logger
from bfrt.vendor.bfrt_grpc import client as gc

log = get_logger(__name__)

def read(session, reg_name: str, index: Optional[int] = None, pipe: int = 0):
    t = session.bfrt_info.table_get(reg_name)
    def _one(idx: int):
        it = t.entry_get(session.target, [t.make_key([gc.KeyTuple("$REGISTER_INDEX", idx)])], {"from_hw": True})
        d  = next(it)[0].to_dict()
        if f"{reg_name}.f1" in d:
            return d[f"{reg_name}.f1"][pipe]
        return (d.get(f"{reg_name}.first", [0])[pipe], d.get(f"{reg_name}.second", [0])[pipe])
    if index is not None:
        return _one(int(index))
    size = getattr(t.info, "size", None); size = size if isinstance(size, int) else getattr(t.info, "size_get", lambda: 0)()
    return [(i, _one(i)) for i in range(int(size))]

def clear(session, reg_name: str) -> None:
    t = session.bfrt_info.table_get(reg_name)
    try:
        t.entry_del(session.target)
        log.info("Cleared register entries: %s", reg_name)
    except Exception as e:
        log.error("Failed to clear %s: %s", reg_name, e)
