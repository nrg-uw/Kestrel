from __future__ import annotations
from typing import Iterable, List, Tuple, Any, Dict
from .config import load_config
from .connection import BfrtSession
from .logging import get_logger
from bfrt.vendor.bfrt_grpc import client as gc

log = get_logger(__name__)

Key = List[Tuple[str, Any]]
Action = str
Data = List[Tuple[str, Any]]
Entry = Tuple[Key, Action, Data]

class Controller:
    """Small shim so user scripts stay tiny; everything else lives in recipes/."""
    def __init__(self, config_path: str | None = None):
        cfg = load_config(config_path)
        self.session = BfrtSession(
            host=cfg.host, port=cfg.port,
            device_id=cfg.device_id, pipe_id=cfg.pipe_id,
            program_name=cfg.program_name,
        ).connect()
        self._tables: Dict[str, Any] = {}

    def setup_tables(self, names: Iterable[str]) -> None:
        for n in names:
            self._tables[n] = self.session.get_table(n)

    def program_table(self, table: str, entries: Iterable[Entry], batch: int = 1024) -> None:
        t = self._tables.get(table) or self.session.get_table(table)
        buf: List[Entry] = []
        for e in entries:
            buf.append(e)
            if len(buf) >= batch:
                self._apply(t, buf); buf.clear()
        if buf:
            self._apply(t, buf)

    def _apply(self, table_obj: Any, batch: List[Entry]) -> None:
        key_list = []
        data_list = []
        for key_fields, action_name, data_fields in batch:
            # Support exact, LPM, ternary, ranges, arrays — whatever you pass in
            k = table_obj.make_key([gc.KeyTuple(*f) for f in key_fields])
            d = table_obj.make_data([gc.DataTuple(*p) for p in data_fields], action_name=action_name)
            key_list.append(k)
            data_list.append(d)
        try:
            table_obj.entry_add(self.session.target, key_list, data_list)
        except Exception:
            table_obj.entry_mod(self.session.target, key_list, data_list)

    def add_annotation(self, table: str, field: str, kind: str) -> None:
        """
        Set a key-field annotation (e.g., 'ipv4', 'mac') so strings are accepted.
        """
        t = self._tables.get(table) or self.session.get_table(table)
        try:
            t.info.key_field_annotation_add(field, kind)
        except Exception as e:
            log.warning("Annotation failed for %s.%s=%s: %s; will require bytes value.", table, field, kind, e)


    def tear_down(self) -> None:
        try:
            if hasattr(self.session.interface, "shutdown"):
                self.session.interface.shutdown()
        except Exception:
            pass
