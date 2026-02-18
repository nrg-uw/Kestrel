#!/usr/bin/env python3
"""
Apply queue scheduling (priority + min/max rate) on Tofino egress queues.

- Reads per-QID scheduler config from CONFIG_FILE (JSON) if set, otherwise uses defaults.
- Programs: tf1.tm.queue.sched_cfg and tf1.tm.queue.sched_shaping
- Uses tf1.tm.port.cfg to map (dev_port -> pg_id + PG_QUEUE indices)

Runtime env:
  DEV_PORTS      Comma-separated dev ports (default "16")
  CONFIG_FILE    JSON file for QID config (optional)
  BFRTCTL_*      (host/port/device/pipe/program) handled by Controller

Run:
  ./build.sh examples/scheduling.py
  DEV_PORTS="16,44" ./build.sh examples/scheduling.py
  CONFIG_FILE=/tmp/qid_cfg.json ./build.sh examples/scheduling.py
"""

from __future__ import annotations
import os, json, logging
from typing import Dict
from bfrt.controller import Controller
from bfrt.vendor.bfrt_grpc import client as gc

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Device ports to configure
DEV_PORTS = [int(x) for x in os.getenv("DEV_PORTS", "16").split(",")]

# QID config (QIDs 0..7). If CONFIG_FILE is provided, it must be a JSON mapping of string QID -> {max_priority,min_rate,max_rate}
cfg_path = os.getenv("CONFIG_FILE")
if cfg_path and os.path.isfile(cfg_path):
    with open(cfg_path) as f:
        QID_CFG: Dict[int, Dict[str, int]] = {int(k): {**v} for k, v in json.load(f).items()}
    logging.info("Loaded QID_CFG from %s", cfg_path)
else:
    logging.info("Using hardcoded QID_CFG")
    QID_CFG = {
        0: {"max_priority": 7, "min_rate":  15_000,  "max_rate":  30_000},   # VoIP / URLLC-like
        1: {"max_priority": 6, "min_rate":  40_000,  "max_rate":  70_000},   # IoT
        2: {"max_priority": 5, "min_rate": 500_000,  "max_rate":1_500_000},  # Gaming
        3: {"max_priority": 4, "min_rate": 100_000,  "max_rate": 150_000},   # Video call
        4: {"max_priority": 3, "min_rate": 150_000,  "max_rate": 380_000},   # YouTube
        5: {"max_priority": 3, "min_rate": 300_000,  "max_rate": 400_000},   # Twitch
        6: {"max_priority": 2, "min_rate": 150_000,  "max_rate": 500_000},   # Downloads
        7: {"max_priority": 1, "min_rate": 300_000,  "max_rate": 900_000},   # Browsing/Sync (BE)
    }


def _entry_upsert(table, target, keys, data):
    """Try entry_mod; if rows don't exist yet, fall back to entry_add."""
    try:
        table.entry_mod(target, keys, data)
    except gc.BfruntimeReadWriteRpcException:
        table.entry_add(target, keys, data)


def apply_sched_policy(c: Controller, dev_port: int, qid_cfg: Dict[int, Dict[str, int]]) -> None:
    # Determine the pipe from dev_port and use a per-pipe Target for TM tables
    pipe = dev_port >> 7
    local_target = gc.Target(c.session.device_id, pipe)

    # Read port config to get pg_id and logical->physical queue map
    port_cfg = c.session.bfrt_info.table_get("tf1.tm.port.cfg")
    port_key = port_cfg.make_key([gc.KeyTuple("dev_port", dev_port)])
    data, _ = next(port_cfg.entry_get(local_target, [port_key], {"from_hw": True}))
    d = data.to_dict()
    pg_id: int = d["pg_id"]
    qid_map: Dict[int, int] = d["egress_qid_queues"]  # logical QID -> PG_QUEUE index

    logging.info("Configuring dev_port %s (pipe %s) -> PG_ID %s", dev_port, pipe, pg_id)

    sched_cfg     = c.session.bfrt_info.table_get("tf1.tm.queue.sched_cfg")
    sched_shaping = c.session.bfrt_info.table_get("tf1.tm.queue.sched_shaping")

    cfg_keys, cfg_data = [], []
    shp_keys, shp_data = [], []

    for logical_qid, cfg in qid_cfg.items():
        if logical_qid not in qid_map:
            logging.warning("QID %s not present for dev_port %s; skipping", logical_qid, dev_port)
            continue

        pg_queue = qid_map[logical_qid]
        max_pri  = int(cfg.get("max_priority", 1))
        min_rate = int(cfg.get("min_rate", 0) or 0)
        max_rate = int(cfg.get("max_rate", 0) or 0)

        logging.info("  QID %s -> PG_QUEUE %s | max_prio=%s min=%s max=%s",
                     logical_qid, pg_queue, max_pri, min_rate, max_rate)

        # --- sched_cfg (priority + enables) ---
        cfg_keys.append(sched_cfg.make_key([
            gc.KeyTuple("pg_id", pg_id),
            gc.KeyTuple("pg_queue", pg_queue),
        ]))
        cfg_data.append(sched_cfg.make_data([
            # IMPORTANT: string/enum on many SDEs
            gc.DataTuple("max_priority",      str_val=str(max_pri)),
            gc.DataTuple("max_rate_enable",   bool_val=(max_rate > 0)),
            gc.DataTuple("min_rate_enable",   bool_val=(min_rate > 0)),
            gc.DataTuple("scheduling_enable", bool_val=True),
        ]))

        # --- sched_shaping (rates/bursts) ---
        shp_keys.append(sched_shaping.make_key([
            gc.KeyTuple("pg_id", pg_id),
            gc.KeyTuple("pg_queue", pg_queue),
        ]))
        shp_data.append(sched_shaping.make_data([
            gc.DataTuple("unit",           str_val="BPS"),
            gc.DataTuple("provisioning",   str_val="UPPER"),
            gc.DataTuple("max_rate",       max_rate),
            gc.DataTuple("min_rate",       min_rate),
            gc.DataTuple("max_burst_size", 0),
            gc.DataTuple("min_burst_size", 0),
        ]))

    # Upsert to the per-pipe target
    _entry_upsert(sched_cfg,     local_target, cfg_keys, cfg_data)
    _entry_upsert(sched_shaping, local_target, shp_keys, shp_data)


def main() -> None:
    c = Controller()  # honors BFRTCTL_* env
    try:
        c.setup_tables(["tf1.tm.port.cfg", "tf1.tm.queue.sched_cfg", "tf1.tm.queue.sched_shaping"])
        logging.info("Applying queue scheduling policy")
        for dp in DEV_PORTS:
            apply_sched_policy(c, dp, QID_CFG)
        logging.info("Done.")
    finally:
        c.tear_down()


if __name__ == "__main__":
    main()
