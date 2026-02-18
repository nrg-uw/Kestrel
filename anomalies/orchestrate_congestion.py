#!/usr/bin/env python3
# anomalies/orchestrate_congestion.py
#
# Orchestrate "congestion" episodes on a REMOTE host (e.g., hpc3) via SSH,
# parse injector JSON from remote stdout, and write labels LOCALLY.
#
# Defaults can be overridden via CLI or env:
#   REMOTE_HOST, REMOTE_BIN, IFACE, WARMUP_S, DUR_MIN, DUR_MAX, GAP_MIN, GAP_MAX

import argparse
import json
import os
import random
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from anomalies import config
from dataset_collector.labels import ensure_labels_csv, write_label_row

# ===== Defaults / Spec =====
SPEC = config.ANOMALIES["congestion"]

QFIS = SPEC["qfis"]
WARMUP_SEC = int(os.getenv("WARMUP_S", SPEC["warmup_s"]))
DUR_MIN = int(os.getenv("DUR_MIN", SPEC["episode_s"][0]))
DUR_MAX = int(os.getenv("DUR_MAX", SPEC["episode_s"][1]))
GAP_MIN = int(os.getenv("GAP_MIN", SPEC["gap_s"][0]))
GAP_MAX = int(os.getenv("GAP_MAX", SPEC["gap_s"][1]))

# Per-QFI overrides for number of flows (optional)
CONGESTION_CONFIG = {
    2: {"n_min": 3, "n_max": 5},
    3: {"n_min": 4, "n_max": 7},
    7: {"n_min": 3, "n_max": 5},
}

# Remote execution defaults
DEFAULT_REMOTE_HOST = os.getenv("REMOTE_HOST", "")
DEFAULT_REMOTE_BIN  = os.getenv("REMOTE_BIN", "/tmp/inject_congestion")  # path on hpc3
DEFAULT_IFACE       = os.getenv("IFACE", "enp2s0f0")


def now_ns() -> int:
    return time.time_ns()


def parse_inject_output(stream) -> Tuple[Optional[int], Optional[int], List[str], Optional[int]]:
    """
    Parse JSON lines from injector stdout/stderr stream.
    Returns: (start_ns, end_ns, teids_list, qfi_from_meta)
    """
    start_ns: Optional[int] = None
    stop_ns: Optional[int] = None
    teids_list: List[str] = []
    qfi_from_meta: Optional[int] = None

    for raw in stream:
        line = raw.decode("utf-8", "ignore").strip()
        if not line:
            continue
        print(line, flush=True)  # echo for visibility
        if not line or line[0] not in "{[":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        ev = obj.get("event", "")
        if ev == "start":
            start_ns = int(obj.get("ts_ns", start_ns or now_ns()))
            actors = obj.get("actors", {}) if isinstance(obj.get("actors", {}), dict) else {}
            if isinstance(actors.get("qfi"), (int, float)):
                qfi_from_meta = int(actors["qfi"])
            teids = actors.get("teids", [])
            if isinstance(teids, list):
                teids_list = [t for t in teids if isinstance(t, str) and t]
        elif ev == "stop":
            stop_ns = int(obj.get("ts_ns", stop_ns or now_ns()))

    return start_ns, stop_ns, teids_list, qfi_from_meta


def run_single_event_remote(
    remote_host: str,
    remote_bin: str,
    qfi: int,
    n_flows: int,
    duration: int,
    episode_id: str,
    iface: str,
) -> Tuple[int, int, List[str], Optional[int]]:
    """
    Launch injector remotely via SSH and parse its JSON output.
    """
    remote_cmd = [
        "sudo",
        remote_bin,
        "--qfi", str(qfi),
        "--n", str(n_flows),
        "--duration", str(duration),
        "--episode-id", episode_id,
        "--iface", iface,
        "--config", "/tmp/config.json",
    ]
    ssh_cmd = ["ssh", "-q", "-o", "BatchMode=yes", remote_host, " ".join(remote_cmd)]
    print(f"[orchestrator] SSH launch: {' '.join(ssh_cmd)}", flush=True)

    proc = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert proc.stdout is not None
    start_ns, stop_ns, teids_list, qfi_from_meta = parse_inject_output(proc.stdout)
    rc = proc.wait()
    if rc != 0:
        print(f"[orchestrator][WARN] injector (remote) exited rc={rc}", file=sys.stderr, flush=True)
    return int(start_ns or now_ns()), int(stop_ns or now_ns()), teids_list, qfi_from_meta


def main():
    ap = argparse.ArgumentParser(description="Orchestrate congestion episodes on remote host (SSH)")
    ap.add_argument("--batch-dir", required=True, help="Batch directory containing labels.csv")
    ap.add_argument("--duration", type=int, required=True, help="Total orchestrator runtime (seconds)")
    ap.add_argument("--dur-min", type=int, default=DUR_MIN, help="Min congestion duration (seconds)")
    ap.add_argument("--dur-max", type=int, default=DUR_MAX, help="Max congestion duration (seconds)")
    ap.add_argument("--gap-min", type=int, default=GAP_MIN, help="Min gap between events (seconds)")
    ap.add_argument("--gap-max", type=int, default=GAP_MAX, help="Max gap between events (seconds)")
    ap.add_argument("--n-min", type=int, default=6, help="Min flows per congestion event")
    ap.add_argument("--n-max", type=int, default=12, help="Max flows per congestion event")
    ap.add_argument("--iface", default=DEFAULT_IFACE, help="Replay interface on remote host")
    ap.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST, help="SSH host for injector")
    ap.add_argument("--remote-bin", default=DEFAULT_REMOTE_BIN, help="Absolute path to injector on remote host")
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_TEST_001"), help="Run ID prefix for episode_id")
    ap.add_argument("--warmup", type=int, default=WARMUP_SEC, help="Seconds to sleep before first injection")
    args = ap.parse_args()


    if args.dur_min <= 0 or args.dur_max < args.dur_min:
        ap.error("--dur-min/--dur-max invalid")
    if args.gap_min < 0 or args.gap_max < args.gap_min:
        ap.error("--gap-min/--gap-max invalid")
    if args.n_min <= 0 or args.n_max < args.n_min:
        ap.error("--n-min/--n-max invalid")

    # warmup
    if args.warmup > 0:
        print(f"[orchestrator] Warm-up: sleeping {args.warmup}s…", flush=True)
        time.sleep(args.warmup)

    labels_path = Path(args.batch_dir) / "labels.csv"
    ensure_labels_csv(labels_path)

    # global stop time
    t_end = time.time() + args.duration
    label_lock = threading.Lock()
    print_lock = threading.Lock()
    episode_counters: Dict[int, int] = {q: 0 for q in QFIS}

    # NOTE: when running remotely we DO NOT use the local UNIX datagram socket.
    # We always write labels locally (CSV) here.

    def qfi_worker(qfi: int):
        rng = random.Random()
        while True:
            now = time.time()
            if now >= t_end:
                break

            gap = rng.randint(args.gap_min, args.gap_max)
            wake = now + gap
            if wake >= t_end:
                break
            with print_lock:
                print(f"[orchestrator][QFI {qfi}] Sleeping gap: {gap}s", flush=True)
            time.sleep(max(0, wake - time.time()))

            dur_req = rng.randint(args.dur_min, args.dur_max)
            n_min = CONGESTION_CONFIG.get(qfi, {}).get("n_min", args.n_min)
            n_max = CONGESTION_CONFIG.get(qfi, {}).get("n_max", args.n_max)
            n_flows = rng.randint(n_min, n_max)

            now2 = time.time()
            if now2 >= t_end:
                break
            remaining = max(0, int(t_end - now2))
            duration = min(dur_req, remaining)
            if duration == 0:
                break

            episode_counters[qfi] += 1
            episode_id = f"{args.run_id}_cong_qfi{qfi}_{episode_counters[qfi]}"

            start_ns, end_ns, teids_list, qfi_from_meta = run_single_event_remote(
                args.remote_host, args.remote_bin, qfi, n_flows, duration, episode_id, args.iface
            )
            actual_qfi = int(qfi_from_meta) if qfi_from_meta is not None else int(qfi)

            params = {
                "episode_id": episode_id,
                "iface": args.iface,
                "remote_host": args.remote_host,
                "remote_bin": args.remote_bin,
                "dur_req_s": duration,
                "n_flows": n_flows,
                "requested_qfi": qfi,
            }

            # Optional timestamp offset
            offset_ns = config.LABEL_OFFSET_MS * 1_000_000
            start_ns_o = start_ns + offset_ns
            end_ns_o = end_ns + offset_ns

            with label_lock:
                write_label_row(
                    labels_path=labels_path,
                    anomaly_type="congestion",
                    episode_id=episode_id,
                    start_ns=start_ns_o,
                    end_ns=end_ns_o,
                    qfis=[actual_qfi],
                    teids=teids_list,
                    params=params,
                )

            with print_lock:
                print(
                    f"[orchestrator][QFI {qfi}] Labeled {episode_id}: "
                    f"qfi={actual_qfi} dur={duration}s n={n_flows} teids={' '.join(teids_list)}",
                    flush=True,
                )

        with print_lock:
            print(f"[orchestrator][QFI {qfi}] Worker exit.", flush=True)

    threads: List[threading.Thread] = []
    for q in QFIS:
        t = threading.Thread(target=qfi_worker, args=(q,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    print(f"[orchestrator] Done. Wrote labels to {labels_path}", flush=True)


if __name__ == "__main__":
    main()
