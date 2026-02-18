#!/usr/bin/env python3
# anomalies/orchestrate_contention.py
#
# Orchestrate ingress contention episodes using iperf3 TCP from an interferer host.
# One label per episode, written locally.

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from anomalies import config
from dataset_collector.labels import ensure_labels_csv, write_label_row

SPEC = config.ANOMALIES["contention"]

# ----- Defaults -----
DEFAULT_INTERFERER = os.getenv("INTERFERER_HOST")
DEFAULT_IPERF_SRV = os.getenv("IPERF_SERVER", "192.168.44.128")
DEFAULT_IPERF_PORT = int(os.getenv("IPERF_PORT", "5201"))
DEFAULT_QFIS = SPEC["qfis"]                # pass-through
DEFAULT_TEIDS: List[str] = []              # pass-through
DUR_MIN = SPEC["episode_s"][0]
DUR_MAX = SPEC["episode_s"][1]
GAP_MIN = SPEC["gap_s"][0]
GAP_MAX = SPEC["gap_s"][1]
WARMUP_SEC = SPEC["warmup_s"]

LABEL_OFFSET_NS = config.LABEL_OFFSET_MS * 1_000_000


def now_ns() -> int:
    return time.time_ns()


def ssh_run(host: str, cmd: str, verbose: bool = False) -> int:
    full_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", host, cmd]
    return subprocess.run(
        full_cmd,
        check=False,
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.DEVNULL,
    ).returncode


def run_tcp_episode(
    host: str,
    server: str,
    port: int,
    duration: int,
    parallel: int,
    tos: str,
    verbose: bool,
) -> Tuple[int, int]:
    """
    Launch a single TCP contention episode via iperf3 on the interferer host.
    Returns (start_ns, end_ns) measured locally.
    """
    cmd = f"iperf3 -c {server} -p {port} -t {duration} -P {parallel} --get-server-output"
    if tos:
        cmd += f" -S {tos}"
    start_ns = now_ns()
    # Allow iperf3 to fail without killing the loop (server hiccup etc.)
    ssh_run(host, f"{cmd} || true", verbose=verbose)
    end_ns = now_ns()
    return start_ns, end_ns


def main():
    ap = argparse.ArgumentParser(description="Orchestrate TCP ingress contention episodes (labels written locally)")
    ap.add_argument("--batch-dir", required=True, help="Batch directory containing labels.csv")
    ap.add_argument("--duration", type=int, required=True, help="Total orchestrator runtime (seconds)")

    # Episode timing controls
    ap.add_argument("--dur-min", type=int, default=DUR_MIN)
    ap.add_argument("--dur-max", type=int, default=DUR_MAX)
    ap.add_argument("--gap-min", type=int, default=GAP_MIN)
    ap.add_argument("--gap-max", type=int, default=GAP_MAX)

    # Interferer + iperf3 config (TCP)
    ap.add_argument("--host", default=DEFAULT_INTERFERER, help="Interferer SSH host (runs iperf3 client)")
    ap.add_argument("--server", default=DEFAULT_IPERF_SRV, help="iperf3 server IP/host")
    ap.add_argument("--port", type=int, default=DEFAULT_IPERF_PORT, help="iperf3 server port")
    ap.add_argument("--parallel", type=int, default=4, help="iperf3 parallel TCP streams (-P)")
    ap.add_argument("--tos", default="", help="Optional ToS/DSCP byte for iperf3 (-S)")
    ap.add_argument("--verbose", action="store_true")

    # Labeling / metadata
    ap.add_argument("--qfis", type=int, nargs="*", default=DEFAULT_QFIS, help="QFIs to tag in labels (pass-through)")
    ap.add_argument("--teids", type=str, nargs="*", default=DEFAULT_TEIDS, help="TEIDs to tag in labels (pass-through)")
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_TEST_INGRESS"))
    ap.add_argument("--warmup", type=int, default=WARMUP_SEC)
    args = ap.parse_args()

    # Validation
    if args.dur_min <= 0 or args.dur_max < args.dur_min:
        ap.error("--dur-min/--dur-max invalid")
    if args.gap_min < 0 or args.gap_max < args.gap_min:
        ap.error("--gap-min/--gap-max invalid")

    # Warm-up
    if args.warmup > 0:
        print(f"[orchestrator] Warm-up: sleeping {args.warmup}s…", flush=True)
        time.sleep(args.warmup)

    labels_path = Path(args.batch_dir) / "labels.csv"
    ensure_labels_csv(labels_path)

    t_end = time.time() + args.duration
    event_idx = 0

    while True:
        now = time.time()
        if now >= t_end:
            break

        # Episode sizing subject to remaining time
        dur = random.randint(args.dur_min, args.dur_max)
        rem = t_end - now
        if rem <= 1:
            break
        if dur > rem:
            dur = int(rem)
            if dur < 2:
                break

        gap = random.randint(args.gap_min, args.gap_max)

        event_idx += 1
        episode_id = f"{args.run_id}_ingress_cont_{event_idx}"
        print(f"[orchestrator] Episode {event_idx}: iperf3 TCP {dur}s (-P {args.parallel})", flush=True)

        start_ns, end_ns = run_tcp_episode(
            host=args.host,
            server=args.server,
            port=args.port,
            duration=dur,
            parallel=args.parallel,
            tos=args.tos,
            verbose=args.verbose,
        )

        params = {
            "episode_id": episode_id,
            "protocol": "tcp",
            "parallel": args.parallel,
            "tos": args.tos,
            "interferer_host": args.host,
            "iperf_server": args.server,
            "iperf_port": args.port,
            "dur_req_s": dur,
        }

        # Timestamp offset (consistent with other orchestrators)
        start_ns += LABEL_OFFSET_NS
        end_ns   += LABEL_OFFSET_NS

        # Write label locally
        write_label_row(
            labels_path=labels_path,
            anomaly_type="contention",
            episode_id=episode_id,
            start_ns=start_ns,
            end_ns=end_ns,
            qfis=args.qfis,
            teids=args.teids,
            params=params,
        )

        print(f"[orchestrator] Labeled {episode_id} ({(end_ns - start_ns)/1e9:.2f}s)", flush=True)

        # Sleep gap unless that would exceed total end
        if time.time() + gap >= t_end:
            break
        print(f"[orchestrator] Gap {gap}s…", flush=True)
        time.sleep(gap)

    print(f"[orchestrator] Done. Wrote labels to {labels_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
