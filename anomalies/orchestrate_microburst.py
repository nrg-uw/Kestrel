#!/usr/bin/env python3
# anomalies/orchestrate_microburst.py
#
# Orchestrate UPF-style microbursts on a REMOTE host (e.g., hpc3) via SSH,
# parse injector JSON from remote stdout, and write labels LOCALLY.
#
# Usage example:
#   python -m anomalies.orchestrate_microburst \
#       --duration 120 --batch-dir data/postcards/002 --run-id 002 \
#       --iface enp2s0f0 --remote-host <host>
#
# Env overrides:
#   REMOTE_HOST, REMOTE_BIN, IFACE
#
# The remote injector is expected at /tmp/inject_microburst by default and
# discovers existing TEIDs via /tmp/qfi_ue*_qfi*.txt markers. No --config/--bin
# is needed for microburst synth mode.

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
from typing import List, Optional, Tuple, Dict

from anomalies import config
from dataset_collector.labels import ensure_labels_csv, write_label_row

# ===== Spec & defaults =====
SPEC = config.ANOMALIES["microburst"]

QFIS       = SPEC["qfis"]
WARMUP_SEC = SPEC["warmup_s"]
N_TEIDS    = SPEC["n_teids"]     
BURST_MS   = SPEC["burst_ms"]
IDLE_MS    = SPEC["idle_ms"]
CYC_MIN    = SPEC["cycles"][0]
CYC_MAX    = SPEC["cycles"][1]
GAP_MIN_S  = SPEC["gap_s"][0]
GAP_MAX_S  = SPEC["gap_s"][1]

# Simple per-QFI PPS map (per TEID)
PPS_MAP = {1: 800, 5: 120, 3: 200_000, 7: 6000, 2: 1550, 4: 775, 6: 2684, 9: 1806, 8: 3095}

# Remote execution defaults
DEFAULT_REMOTE_HOST = os.getenv("REMOTE_HOST")
DEFAULT_REMOTE_BIN  = os.getenv("REMOTE_BIN", "/tmp/inject_microburst")
DEFAULT_IFACE       = os.getenv("IFACE", "enp2s0f0")

ALIGN_MS   = 50     # align batch start to next multiple
LOCKSTEP   = True   # start all chosen TEIDs together
PHASE_MS   = 0      
JITTER_PCT = 0      
INNER_LEN  = 1200   # injector’s inner payload length (bytes)

LABEL_OFFSET_NS = config.LABEL_OFFSET_MS * 1_000_000


def now_ns() -> int:
    return time.time_ns()


def parse_inject_output(stream) -> Tuple[Optional[int], Optional[int], List[str], Optional[int]]:
    """
    Parse JSON lines from injector stdout.
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
    duration_ms: int,
    episode_id: str,
    iface: str,
    pps: float,
    inner_len: int,
    burst_ms: int,
    idle_ms: int,
    lockstep: bool,
    align_ms: int,
    phase_ms: int,
    jitter_pct: int,
) -> Tuple[int, int, List[str], Optional[int]]:
    """
    Launch remote injector via SSH and parse its JSON output.
    """
    cmd = [
        "sudo",
        remote_bin,
        "--qfi", str(qfi),
        "--n", str(n_flows),
        "--duration-ms", str(duration_ms),
        "--episode-id", episode_id,
        "--iface", iface,
        "--pps", f"{pps:.3f}",
        "--inner-len", str(inner_len),
        "--burst-ms", str(burst_ms),
        "--idle-ms", str(idle_ms),
        "--align-ms", str(align_ms),
        "--phase-ms", str(phase_ms),
        "--jitter-pct", str(jitter_pct),
    ]
    if lockstep:
        cmd.append("--lockstep")

    ssh_cmd = ["ssh", "-q", "-o", "BatchMode=yes", remote_host, " ".join(cmd)]
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
    ap = argparse.ArgumentParser(description="Orchestrate UPF-style microbursts on a remote host (SSH).")
    ap.add_argument("--batch-dir", required=True, help="Batch directory containing labels.csv")
    ap.add_argument("--duration", type=int, required=True, help="Total orchestrator runtime (seconds)")
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_TEST_001"), help="Run ID prefix")
    ap.add_argument("--iface", default=DEFAULT_IFACE, help="Remote replay interface")
    ap.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST, help="SSH host for injector")
    ap.add_argument("--remote-bin", default=DEFAULT_REMOTE_BIN, help="Absolute path to injector on remote host")
    ap.add_argument("--warmup", type=int, default=WARMUP_SEC, help="Seconds to sleep before first injection")
    # optional tuning overrides
    ap.add_argument("--n-teids", type=int, default=N_TEIDS, help="Requested TEIDs per episode (injector caps to avail.)")
    ap.add_argument("--burst-ms", type=int, default=BURST_MS, help="Burst ON (ms)")
    ap.add_argument("--idle-ms", type=int, default=IDLE_MS, help="Burst OFF (ms)")
    ap.add_argument("--align-ms", type=int, default=ALIGN_MS, help="Align to next multiple (ms) (0=disable)")
    ap.add_argument("--lockstep", action="store_true", default=LOCKSTEP, help="Start TEIDs in phase")
    ap.add_argument("--phase-ms", type=int, default=PHASE_MS, help="Extra per-TEID phase jitter (ignored if lockstep)")
    ap.add_argument("--jitter-pct", type=int, default=JITTER_PCT, help="±pct jitter for pps/burst/idle (if not lockstep)")
    args = ap.parse_args()

    if args.warmup > 0:
        print(f"[orchestrator] Warm-up: sleeping {args.warmup}s…", flush=True)
        time.sleep(args.warmup)

    labels_path = Path(args.batch_dir) / "labels.csv"
    ensure_labels_csv(labels_path)

    t_end = time.time() + args.duration
    label_lock = threading.Lock()
    print_lock = threading.Lock()
    episode_counters: Dict[int, int] = {q: 0 for q in QFIS}

    def qfi_worker(qfi: int):
        rng = random.Random()
        while True:
            now = time.time()
            if now >= t_end:
                break

            # wait random gap before next episode for this QFI
            gap = rng.randint(GAP_MIN_S, GAP_MAX_S)
            wake = now + gap
            if wake >= t_end:
                break
            with print_lock:
                print(f"[orchestrator][QFI {qfi}] Sleeping gap: {gap}s", flush=True)
            time.sleep(max(0, wake - time.time()))

            # size the episode
            cycles = rng.randint(CYC_MIN, CYC_MAX)
            period_ms = args.burst_ms + args.idle_ms
            dur_ms = cycles * period_ms

            remaining_ms = int(max(0, (t_end - time.time()) * 1000))
            if dur_ms > remaining_ms:
                dur_ms = remaining_ms
            if dur_ms <= 0:
                break

            n_flows = int(args.n_teids)  # injector will cap to available
            pps = float(PPS_MAP.get(qfi, 1000.0))

            episode_counters[qfi] += 1
            episode_id = f"{args.run_id}_UPF_mb_qfi{qfi}_{episode_counters[qfi]}"

            start_ns, end_ns, teids_list, qfi_from_meta = run_single_event_remote(
                args.remote_host,
                args.remote_bin,
                qfi,
                n_flows,
                dur_ms,
                episode_id,
                args.iface,
                pps,
                INNER_LEN,
                args.burst_ms,
                args.idle_ms,
                args.lockstep,
                args.align_ms,
                args.phase_ms,
                args.jitter_pct,
            )
            actual_qfi = int(qfi_from_meta) if qfi_from_meta is not None else int(qfi)

            params = {
                "iface": args.iface,
                "remote_host": args.remote_host,
                "remote_bin": args.remote_bin,
                "pps": round(pps, 2),
                "inner_len": INNER_LEN,
                "burst_ms": args.burst_ms,
                "idle_ms": args.idle_ms,
                "align_ms": args.align_ms if args.lockstep else 0,
                "lockstep": bool(args.lockstep),
                "phase_ms": 0 if args.lockstep else args.phase_ms,
                "jitter_pct": 0 if args.lockstep else args.jitter_pct,
                "cycles": cycles,
                "period_ms": period_ms,
                "duration_ms": dur_ms,
                "n_flows": n_flows,
                "run_id": args.run_id,
                "warmup_s": args.warmup,
            }

            # timestamp offset if configured
            s_ns = (start_ns or now_ns()) + LABEL_OFFSET_NS
            e_ns = (end_ns or now_ns()) + LABEL_OFFSET_NS

            with label_lock:
                write_label_row(
                    labels_path=labels_path,
                    anomaly_type="microburst",
                    episode_id=episode_id,
                    start_ns=s_ns,
                    end_ns=e_ns,
                    qfis=[actual_qfi],
                    teids=teids_list,
                    params=params,
                )

            with print_lock:
                print(
                    f"[orchestrator][QFI {qfi}] Labeled {episode_id}: "
                    f"qfi={actual_qfi} dur={dur_ms}ms n={n_flows} teids={' '.join(teids_list)}",
                    flush=True,
                )

        with print_lock:
            print(f"[orchestrator][QFI {qfi}] Worker exit.", flush=True)

    # Spawn a worker per QFI
    threads: List[threading.Thread] = []
    for q in QFIS:
        t = threading.Thread(target=qfi_worker, args=(q,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    print(f"[orchestrator] Done. Wrote labels to {labels_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
