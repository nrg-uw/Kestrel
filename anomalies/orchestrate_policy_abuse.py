#!/usr/bin/env python3
# anomalies/orchestrate_policy_abuse.py
#
# Orchestrate policy-abuse episodes on a REMOTE host (e.g., hpc3) via SSH.
# - We run /tmp/inject_policy_abuse remotely under sudo
# - We parse its JSON "start"/"end" lines from remote stdout
# - We write labels LOCALLY (CSV) into the provided batch dir
#
# Usage (example):
#   python -m anomalies.orchestrate_policy_abuse \
#     --duration 300 --batch-dir data/postcards/002 --run-id 002 \
#     --victim-qfis 3,5 --iface enp2s0f0
#
# Env overrides:
#   REMOTE_HOST, REMOTE_BIN, IFACE

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

# ───────── Spec & defaults ─────────
SPEC = config.ANOMALIES["policy_abuse"]

DEFAULT_VICTIM_QFIS   = SPEC["qfis"]                # one worker per victim QFI
DEFAULT_MAP_STR       = SPEC["qfi_map"]             # "victimQFI:fakeQFI,..."
DEFAULT_WARMUP        = SPEC["warmup_s"]
EPISODE_DUR_MIN       = SPEC["episode_s"][0]
EPISODE_DUR_MAX       = SPEC["episode_s"][1]
GAP_MIN               = SPEC["gap_s"][0]
GAP_MAX               = SPEC["gap_s"][1]
COUNT_MIN             = 1                           # how many existing TEIDs per episode
COUNT_MAX             = 1
DEFAULT_JITTER_MS     = "100:800"

# Remote execution defaults
DEFAULT_REMOTE_HOST   = os.getenv("REMOTE_HOST")
DEFAULT_REMOTE_BIN    = os.getenv("REMOTE_BIN", "/tmp/inject_policy_abuse")
DEFAULT_IFACE         = os.getenv("IFACE", "enp2s0f0")

LABEL_OFFSET_NS       = config.LABEL_OFFSET_MS * 1_000_000


def now_ns() -> int:
    return time.time_ns()


def parse_inject_output(stream) -> Tuple[Optional[int], Optional[int], List[str], Dict[str, Any]]:
    """
    Parse JSON lines from injector stdout (remote).
    Returns: (start_ns, end_ns, teids_hex[], meta_dict)
    """
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    teids: List[str] = []
    meta: Dict[str, Any] = {}

    for raw in stream:
        line = raw.decode("utf-8", "ignore").strip()
        if not line:
            continue
        print(line, flush=True)  # echo remote logs
        if line[0] not in "{[":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        ev = obj.get("event", "")
        if ev == "teid_impersonation":
            start_ns = int(obj.get("ts_ns", start_ns or now_ns()))
            victims = obj.get("victims", [])
            if isinstance(victims, list):
                teids = [v.get("teid") for v in victims if isinstance(v, dict) and v.get("teid")]
            meta = obj
        elif ev == "teid_impersonation_end":
            end_ns = int(obj.get("ts_ns", end_ns or now_ns()))

    return start_ns, end_ns, teids, (meta if isinstance(meta, dict) else {})


def run_single_event_remote(
    remote_host: str,
    remote_bin: str,
    victim_qfi: int,
    count: int,
    duration_s: int,
    iface: str,
    map_str: str,
    jitter_ms: str,
) -> Tuple[int, int, List[str], Dict[str, Any], int]:
    """
    Launch injector once on the remote host via SSH and parse its JSON.
    Returns (start_ns, end_ns, teids, meta, rc).
    """
    cmd = [
        "sudo",
        remote_bin,
        "--victim-qfis", str(victim_qfi),
        "--count",        str(count),
        "--duration",     str(duration_s),
        "--iface",        iface,
        "--jitter-ms",    jitter_ms,
        "--config",       "/tmp/config.json",
    ]
    if map_str:
        cmd += ["--map", map_str]

    ssh_cmd = ["ssh", "-q", "-o", "BatchMode=yes", remote_host, " ".join(cmd)]
    print(f"[orchestrator] SSH launch: {' '.join(ssh_cmd)}", flush=True)

    proc = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert proc.stdout is not None
    start_ns, end_ns, teids, meta = parse_inject_output(proc.stdout)
    rc = proc.wait()
    return int(start_ns or 0), int(end_ns or 0), teids, meta, int(rc)


def parse_csv_ints(s: str) -> List[int]:
    out: List[int] = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description="Orchestrate policy-abuse impersonation (remote SSH).")
    ap.add_argument("--batch-dir", required=True, help="Batch directory containing labels.csv")
    ap.add_argument("--duration", type=int, required=True, help="Total orchestrator runtime (seconds)")
    ap.add_argument("--dur-min",   type=int, default=EPISODE_DUR_MIN)
    ap.add_argument("--dur-max",   type=int, default=EPISODE_DUR_MAX)
    ap.add_argument("--gap-min",   type=int, default=GAP_MIN)
    ap.add_argument("--gap-max",   type=int, default=GAP_MAX)
    ap.add_argument("--count-min", type=int, default=COUNT_MIN)
    ap.add_argument("--count-max", type=int, default=COUNT_MAX)

    ap.add_argument("--victim-qfis", default=",".join(map(str, DEFAULT_VICTIM_QFIS)))
    ap.add_argument("--map",         default=DEFAULT_MAP_STR, help="VictimQFI:FakeQFI pairs (e.g., 3:6,5:4)")
    ap.add_argument("--jitter-ms",   default=DEFAULT_JITTER_MS)

    ap.add_argument("--iface",       default=DEFAULT_IFACE, help="Remote interface for replay")
    ap.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    ap.add_argument("--remote-bin",  default=DEFAULT_REMOTE_BIN)

    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_TEST_001"))
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    args = ap.parse_args()

    # sanity
    if args.dur_min <= 0 or args.dur_max < args.dur_min:
        ap.error("--dur-min/--dur-max invalid")
    if args.gap_min < 0 or args.gap_max < args.gap_min:
        ap.error("--gap-min/--gap-max invalid")
    if args.count_min <= 0 or args.count_max < args.count_min:
        ap.error("--count-min/--count-max invalid")

    victim_qfis = parse_csv_ints(args.victim_qfis) or list(DEFAULT_VICTIM_QFIS)

    if args.warmup > 0:
        print(f"[orchestrator] Warm-up: sleeping {args.warmup}s…", flush=True)
        time.sleep(args.warmup)

    labels_path = Path(args.batch_dir) / "labels.csv"
    ensure_labels_csv(labels_path)

    t_end = time.time() + args.duration
    label_lock = threading.Lock()
    print_lock = threading.Lock()
    episode_counters: Dict[int, int] = {q: 0 for q in victim_qfis}

    def worker(victim_qfi: int):
        rng = random.Random()
        while True:
            now = time.time()
            if now >= t_end:
                break

            # independent gap per victim QFI
            gap = rng.randint(args.gap_min, args.gap_max)
            wake = now + gap
            if wake >= t_end:
                break
            with print_lock:
                print(f"[orchestrator][QFI {victim_qfi}] Sleeping gap: {gap}s", flush=True)
            time.sleep(max(0, wake - time.time()))

            # sample duration & #TEIDs (existing impersonations)
            dur_req = rng.randint(args.dur_min, args.dur_max)
            cnt     = rng.randint(args.count_min, args.count_max)

            now2 = time.time()
            if now2 >= t_end:
                break
            remaining = max(0, int(t_end - now2))
            duration  = min(dur_req, remaining)
            if duration == 0:
                break

            episode_counters[victim_qfi] += 1
            episode_id = f"{args.run_id}_polabuse_qfi{victim_qfi}_{episode_counters[victim_qfi]}"

            start_ns, end_ns, teids, meta, rc = run_single_event_remote(
                args.remote_host,
                args.remote_bin,
                victim_qfi,
                cnt,
                duration,
                args.iface,
                args.map,
                args.jitter_ms,
            )

            if rc != 0 or not teids:
                with print_lock:
                    print(f"[orchestrator][QFI {victim_qfi}][SKIP] {episode_id}: rc={rc}, teids={len(teids)}", flush=True)
                continue

            # derive actual victim QFIs from meta if provided
            actual_qfis: List[int] = [victim_qfi]
            if isinstance(meta, dict) and isinstance(meta.get("victims"), list):
                got = []
                for v in meta["victims"]:
                    if isinstance(v, dict) and isinstance(v.get("victim_qfi"), int):
                        got.append(v["victim_qfi"])
                if got:
                    actual_qfis = sorted(set(got))

            # timestamp fallback + single offset
            if start_ns <= 0:
                start_ns = now_ns()
            if end_ns <= start_ns:
                end_ns = start_ns + int(duration * 1_000_000_000)
            start_ns += LABEL_OFFSET_NS
            end_ns   += LABEL_OFFSET_NS

            params = {
                "episode_id": episode_id,
                "iface": args.iface,
                "remote_host": args.remote_host,
                "remote_bin": args.remote_bin,
                "map": args.map,
                "jitter_ms": args.jitter_ms,
                "dur_req_s": duration,
                "count": cnt,
                "victims": meta.get("victims", []) if isinstance(meta, dict) else [],
            }

            with label_lock:
                write_label_row(
                    labels_path=labels_path,
                    anomaly_type="policy_abuse",
                    episode_id=episode_id,
                    start_ns=start_ns,
                    end_ns=end_ns,
                    qfis=actual_qfis,
                    teids=teids,
                    params=params,
                )

            with print_lock:
                print(
                    f"[orchestrator][QFI {victim_qfi}] Labeled {episode_id}: "
                    f"qfi={actual_qfis} dur={duration}s count={cnt} teids={' '.join(teids)}",
                    flush=True,
                )

        with print_lock:
            print(f"[orchestrator][QFI {victim_qfi}] Worker exit.", flush=True)

    # spawn one worker per victim QFI
    threads: List[threading.Thread] = []
    for qfi in victim_qfis:
        t = threading.Thread(target=worker, args=(qfi,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    print(f"[orchestrator] Done. Wrote labels to {labels_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
