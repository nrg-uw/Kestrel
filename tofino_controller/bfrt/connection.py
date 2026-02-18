from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Any, List
from .logging import get_logger
log = get_logger(__name__)

try:
    from bfrt.vendor.bfrt_grpc import client as gc
    from bfrt.vendor.bfrt_grpc import bfruntime_pb2_grpc as _bfruntime_pb2_grpc  # noqa: F401
except Exception as e:
    gc = None
    log.debug("BFRT client unavailable: %s", e)

@dataclass
class BfrtSession:
    host: str
    port: int
    device_id: int = 0
    pipe_id: int = 0xFFFF
    program_name: Optional[str] = None
    interface: Any = None
    bfrt_info: Any = None
    target: Any = None

    def connect(self) -> "BfrtSession":
        if gc is None:
            raise RuntimeError("BFRT client not found under vendor/bfrt_grpc.")
        addr = f"{self.host}:{self.port}"
        log.info("Connecting BFRT @ %s (device_id=%d, pipe_id=0x%X)", addr, self.device_id, self.pipe_id)
        self.interface = gc.ClientInterface(addr, client_id=0, device_id=self.device_id)
        self.bfrt_info = self.interface.bfrt_info_get()
        pname = self.program_name or self.bfrt_info.p4_name_get()
        self.interface.bind_pipeline_config(pname)
        self.target = gc.Target(self.device_id, pipe_id=self.pipe_id)
        log.info("Connected program: %s", pname)
        return self

    def list_tables(self) -> List[str]:
        return sorted(list(self.bfrt_info.table_dict.keys()))

    def get_table(self, name: str):
        return self.bfrt_info.table_get(name)
