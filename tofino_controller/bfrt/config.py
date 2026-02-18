from __future__ import annotations
import os, yaml
from dataclasses import dataclass
from typing import Optional

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".config", "bfrt_controller", "config.yaml")

@dataclass
class BfrtConfig:
    host: str = "localhost"
    port: int = 50052
    pipe_id: int = 0xFFFF
    device_id: int = 0
    program_name: Optional[str] = None

def load_config(path: Optional[str]) -> BfrtConfig:
    if path is None:
        path = DEFAULT_PATH
    data = {}
    if os.path.exists(path):
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}

    bfrt = data.get("bfrt", {})
    host = os.environ.get("BFRTCTL_HOST", bfrt.get("host", "localhost"))
    port = int(os.environ.get("BFRTCTL_PORT", bfrt.get("port", 50052)))
    pipe_id = int(os.environ.get("BFRTCTL_PIPE_ID", bfrt.get("pipe_id", 0xFFFF)))
    device_id = int(os.environ.get("BFRTCTL_DEVICE_ID", bfrt.get("device_id", 0)))
    program_name = data.get("program", {}).get("name")

    return BfrtConfig(host=host, port=port, pipe_id=pipe_id, device_id=device_id, program_name=program_name)
