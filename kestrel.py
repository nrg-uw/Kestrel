#!/usr/bin/env python3
"""
Kestrel: Post-Switch Anomaly Detection Pipeline
================================================
Runs anomaly detection on sketch telemetry collected from an Intel Tofino
switch. The data in data/ is telemetry from our 5G testbed.

Pipeline steps:
  1. CMS min-query: reconstructs per-flow estimates from sketch registers
  2. TEID feature extraction: computes latency, IAT, and traffic features
  3. QFI aggregation: aggregates per-TEID features to QoS flow level
  4. Streaming feature engineering: causal rolling statistics (no lookahead)
  5. Anomaly scoring: XGBoost model inference with per-QFI thresholds
  6. Debouncing: suppresses transient false positives

Inputs:
  data/cms/window_<n>.parquet  -- sketch register dump from the Tofino switch
  data/keys/window_<n>.csv     -- flow keys (teid, qfi, qid) to query
  bundles/xgb.json             -- pre-trained XGBoost model
  bundles/bundle.json          -- feature list, thresholds, debounce parameters
  bins.json                    -- per-QID histogram bin edges

Output:
  output/events.jsonl          -- one JSON record per (window, qfi)

Usage:
  python kestrel.py
  python kestrel.py --max-windows 600
  python kestrel.py --data-dir <path>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import struct
import sys
import zlib
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from natsort import natsorted
from tqdm import tqdm
from xgboost import XGBClassifier


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DEPTH       = 4
DEFAULT_WIDTH       = 512
DEFAULT_MAX_WINDOWS = 120

LOOKBACK      = 5
IAT_HEAD_BINS = 2
LAT_TAIL_BINS = 2

ZS_WIN     = 7
ZL_WIN     = 21
RUNLEN_THR = 0.8
RUNLEN_CAP = 9

_RX_WIN = re.compile(r"window_(\d+)\.(csv|parquet)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Bins loader
# ---------------------------------------------------------------------------
def _validate_edge_list(name: str, qid: int, edges: List[float]) -> None:
    if not isinstance(edges, list):
        raise ValueError(f"{name}[qid={qid}] must be a list of numbers")
    if any(
        (not isinstance(x, (int, float))) or math.isnan(float(x)) or math.isinf(float(x))
        for x in edges
    ):
        raise ValueError(f"{name}[qid={qid}] contains non-finite values")
    for i in range(1, len(edges)):
        if not (edges[i] > edges[i - 1]):
            raise ValueError(
                f"{name}[qid={qid}] must be strictly increasing; "
                f"found {edges[i-1]} -> {edges[i]} at idx {i-1}->{i}"
            )


def load_qid_bins(
    path: Optional[Path] = None,
) -> Tuple[Optional[Dict[int, List[float]]], Optional[Dict[int, List[float]]]]:
    """
    Load per-QID latency and IAT histogram edges (microseconds) from bins.json.

    Expected schema:
        {
          "bins": {
            "0": { "latency_us": [...], "iat_us": [...] },
            "1": { "latency_us": [...], "iat_us": [...] },
            ...
          }
        }

    Returns (latency_edges, iat_edges) as dicts keyed by int QID.
    Returns (None, None) if the file is not found.
    Raises ValueError if the file is malformed.

    Search order when path is None:
      1. $KESTREL_BINS_JSON environment variable
      2. bins.json in the current working directory
      3. bins.json alongside this script
    """
    candidates: List[Path] = [
        Path.cwd() / "bins.json",
        Path(__file__).resolve().parent / "bins.json",
    ]

    env_path = os.environ.get("KESTREL_BINS_JSON")
    if path is None and env_path:
        path = Path(env_path)

    if path is None:
        path = next((p for p in candidates if p.exists()), None)

    if path is None or not path.exists():
        return None, None

    try:
        cfg = json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"bins.json is not valid JSON: {e}")

    bins_block = cfg.get("bins")
    if not isinstance(bins_block, dict):
        raise ValueError("bins.json must contain a top-level 'bins' object with QID entries.")

    lat: Dict[int, List[float]] = {}
    iat: Dict[int, List[float]] = {}

    for qid_str, entry in bins_block.items():
        if not isinstance(entry, dict):
            raise ValueError(f"bins[{qid_str!r}] must be an object with 'latency_us' and 'iat_us'.")
        try:
            qid = int(qid_str)
        except Exception:
            raise ValueError(f"QID keys must be integers-as-strings; got {qid_str!r}")

        lat_arr = entry.get("latency_us")
        iat_arr = entry.get("iat_us")
        if not isinstance(lat_arr, list) or not isinstance(iat_arr, list):
            raise ValueError(f"bins[{qid}] must have list fields 'latency_us' and 'iat_us'.")

        lat_vals = [float(x) for x in lat_arr]
        iat_vals = [float(x) for x in iat_arr]
        _validate_edge_list("latency_us", qid, lat_vals)
        _validate_edge_list("iat_us",     qid, iat_vals)

        lat[qid] = lat_vals
        iat[qid] = iat_vals

    if set(lat) != set(iat):
        raise ValueError(f"QID sets differ between latency and iat: {set(lat)} vs {set(iat)}")

    lat_bin_counts = {qid: len(edges) + 1 for qid, edges in lat.items()}
    iat_bin_counts = {qid: len(edges) + 1 for qid, edges in iat.items()}
    if len(set(lat_bin_counts.values())) != 1:
        raise ValueError(f"Latency bin count differs across QIDs: {lat_bin_counts}")
    if len(set(iat_bin_counts.values())) != 1:
        raise ValueError(f"IAT bin count differs across QIDs: {iat_bin_counts}")

    return lat, iat


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def win_num(p: Path) -> int:
    m = _RX_WIN.search(p.name)
    return int(m.group(1)) if m else -1


def list_windows(d: Path) -> List[Path]:
    files = [p for p in d.glob("window_*.parquet") if _RX_WIN.search(p.name)]
    return natsorted(files, key=win_num)


# ---------------------------------------------------------------------------
# CMS hashing
# ---------------------------------------------------------------------------
def key_bytes_of(teid: int, qfi: int) -> bytes:
    return struct.pack("!IB", int(teid), int(qfi))


def cms_hash(kb: bytes, row: int, width: int) -> int:
    return zlib.crc32(row.to_bytes(1, "little") + kb) % int(width)


# ---------------------------------------------------------------------------
# Histogram utilities
# ---------------------------------------------------------------------------
def quantile_from_histogram(counts: np.ndarray, edges: np.ndarray, q: float) -> float:
    total = float(np.nansum(counts))
    if total <= 0:
        return 0.0
    target = float(np.clip(q, 0.0, 1.0)) * total
    cumsum = np.cumsum(np.nan_to_num(counts, nan=0.0))
    b = int(np.searchsorted(cumsum, target, side="left"))

    if b <= 0:
        low  = 0.0
        high = float(edges[0]) if edges.size else 0.0
        prev = 0.0
    elif b >= len(counts):
        return float(edges[-1]) if edges.size else 0.0
    else:
        low  = float(edges[b - 1])
        high = float(edges[b]) if b < edges.size else float(edges[-1])
        prev = float(cumsum[b - 1])

    bc = float(counts[b]) if 0 <= b < len(counts) else 0.0
    if bc <= 0 or high <= low:
        return low
    frac = float(np.clip((target - prev) / bc, 0.0, 1.0))
    return low + frac * (high - low)


def tail_fraction(counts: np.ndarray, n_tail: int) -> float:
    total = float(np.nansum(counts))
    if total <= 0:
        return 0.0
    n = int(max(1, min(n_tail, len(counts))))
    return float(np.nansum(counts[-n:]) / total)


def head_fraction(counts: np.ndarray, n_head: int) -> float:
    total = float(np.nansum(counts))
    if total <= 0:
        return 0.0
    n = int(max(1, min(n_head, len(counts))))
    return float(np.nansum(counts[:n]) / total)


def exceed_fraction_scalar(counts: np.ndarray, edges: np.ndarray, threshold: float) -> float:
    total = float(np.nansum(counts))
    if total <= 0 or not np.isfinite(threshold):
        return 0.0
    exceed = 0.0
    for i, c in enumerate(counts):
        lo = float(edges[i - 1]) if i > 0 and (i - 1) < edges.size else 0.0
        hi = float(edges[i]) if i < edges.size else np.inf
        if lo >= threshold:
            exceed += float(c)
        elif lo < threshold < hi and np.isfinite(hi) and hi > lo:
            exceed += float(c) * ((hi - threshold) / (hi - lo))
    return float(exceed / total)


def below_fraction_scalar(counts: np.ndarray, edges: np.ndarray, threshold: float) -> float:
    total = float(np.nansum(counts))
    if total <= 0 or not np.isfinite(threshold):
        return 0.0
    below = 0.0
    for i, c in enumerate(counts):
        lo = float(edges[i - 1]) if i > 0 and (i - 1) < edges.size else 0.0
        hi = float(edges[i]) if i < edges.size else np.inf
        if hi <= threshold:
            below += float(c)
        elif lo < threshold < hi and np.isfinite(hi) and hi > lo:
            below += float(c) * ((threshold - lo) / (hi - lo))
    return float(below / total)


def rolling_threshold(history: Deque[float], q: float, lookback: int) -> float:
    """Causal rolling percentile over the last `lookback` values (no lookahead)."""
    if not history:
        return 0.0
    arr = np.asarray(list(history)[-int(lookback):], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0.0
    return float(np.percentile(arr, q))


def safe_entropy(shares: np.ndarray) -> float:
    p = np.asarray(shares, dtype=float)
    p = p[(p > 0) & np.isfinite(p)]
    if p.size == 0:
        return 0.0
    return float(-np.sum(p * np.log(p)))


# ---------------------------------------------------------------------------
# CMS min-query
# ---------------------------------------------------------------------------
def min_query(
    cms_df: pd.DataFrame,
    keys_df: pd.DataFrame,
    feat_cols: List[str],
    depth: int,
    width: int,
) -> pd.DataFrame:
    """
    For each (teid, qfi, qid) in keys_df, compute the CMS min-query estimate
    across all `depth` hash rows and return a flat DataFrame of estimates.
    """
    cms_df = cms_df.copy()
    for c in ["qid", "row", "bucket"]:
        cms_df[c] = pd.to_numeric(cms_df.get(c, -1), errors="coerce").fillna(-1).astype(int)

    for c in feat_cols:
        if c not in cms_df.columns:
            cms_df[c] = 0
        cms_df[c] = pd.to_numeric(cms_df[c], errors="coerce").fillna(0).astype(int)

    lookup: Dict[Tuple[int, int, int], Dict[str, int]] = {}
    for r in cms_df.itertuples(index=False):
        lookup[(int(r.qid), int(r.row), int(r.bucket))] = {
            c: int(getattr(r, c)) for c in feat_cols
        }

    keys_df = keys_df.copy()
    for c in ["teid", "qfi", "qid"]:
        keys_df[c] = pd.to_numeric(keys_df.get(c, -1), errors="coerce").fillna(-1).astype(int)

    out_rows: List[dict] = []
    for teid, qfi, qid in keys_df[["teid", "qfi", "qid"]].drop_duplicates().itertuples(index=False):
        kb  = key_bytes_of(teid, qfi)
        est: Dict[str, int] = {}
        for c in feat_cols:
            vals = [
                lookup.get((qid, row, cms_hash(kb, row, width)), {}).get(c, 0)
                for row in range(int(depth))
            ]
            est[c] = int(min(vals)) if vals else 0
        est.update({"teid": int(teid), "qfi": int(qfi), "qid": int(qid)})
        out_rows.append(est)

    return pd.DataFrame(out_rows)


# ---------------------------------------------------------------------------
# TEID-level feature extraction
# ---------------------------------------------------------------------------
class TeidRollingState:
    """Causal rolling histories of per-(qfi, teid) latency/IAT statistics."""

    def __init__(self):
        self.lat_p95_hist: Dict[Tuple[int, int], Deque[float]] = {}
        self.iat_p10_hist: Dict[Tuple[int, int], Deque[float]] = {}

    def get_hist(self, key: Tuple[int, int], which: str) -> Deque[float]:
        d = self.lat_p95_hist if which == "lat" else self.iat_p10_hist
        if key not in d:
            d[key] = deque(maxlen=256)
        return d[key]


def teid_features_from_recon(
    recon: pd.DataFrame,
    lat_edges: Dict[int, List[float]],
    iat_edges: Dict[int, List[float]],
    state: TeidRollingState,
    window_secs: float = 1.0,
) -> pd.DataFrame:
    """
    Compute per-(qfi, teid) features from CMS-reconstructed sketch data.

    Latency and IAT bin counts are combined across QIDs using packet-weighted
    averaging. Rolling exceed/below fractions use causal thresholds derived
    from per-TEID history (no lookahead).
    """
    if recon.empty:
        return pd.DataFrame()

    recon = recon.copy()
    lat_cols = sorted([c for c in recon.columns if c.startswith("lat")], key=lambda s: int(s[3:]))
    iat_cols = sorted([c for c in recon.columns if c.startswith("iat")], key=lambda s: int(s[3:]))

    for c in ["pkt_cnt", "byte_cnt", "green_cnt", "yellow_cnt", "drop_cnt"]:
        if c not in recon.columns:
            recon[c] = 0

    recon["_color_pkts"] = recon["green_cnt"] + recon["yellow_cnt"] + recon["drop_cnt"]
    qfi_total_color = recon.groupby("qfi")["_color_pkts"].sum().to_dict()
    teid_color      = recon.groupby(["qfi", "teid"])["_color_pkts"].sum()
    qfi_teid_count  = teid_color.gt(0).groupby(level=0).sum().astype(int).to_dict()

    rows: List[dict] = []

    for (qfi, teid), g in recon.groupby(["qfi", "teid"], sort=False):
        qfi_i  = int(qfi)
        teid_i = int(teid)

        byte_sum    = float(g["byte_cnt"].sum())
        pkt_sum     = float(g["pkt_cnt"].sum())
        green       = float(g["green_cnt"].sum())
        yellow      = float(g["yellow_cnt"].sum())
        red         = float(g["drop_cnt"].sum())
        color_total = green + yellow + red

        key      = (qfi_i, teid_i)
        lat_hist = state.get_hist(key, "lat")
        iat_hist = state.get_hist(key, "iat")
        thr_lat  = rolling_threshold(lat_hist, q=95, lookback=LOOKBACK)
        thr_iat  = rolling_threshold(iat_hist, q=10, lookback=LOOKBACK)

        weights                               = []
        lat_p50s, lat_p95s, lat_tails        = [], [], []
        iat_p10s, iat_p50s, iat_heads        = [], [], []
        per_exceed, per_below, per_w         = [], [], []

        for qid in sorted(g["qid"].unique()):
            sub = g[g["qid"] == qid]
            w   = float(sub["pkt_cnt"].sum())
            if w <= 0:
                w = float(sub[lat_cols].sum(axis=1).sum()) if lat_cols else 0.0
            if w <= 0:
                continue

            lat_counts = sub[lat_cols].to_numpy(dtype=float).sum(axis=0) if lat_cols else np.zeros(1)
            iat_counts = sub[iat_cols].to_numpy(dtype=float).sum(axis=0) if iat_cols else np.zeros(1)

            le_us = np.asarray(lat_edges.get(int(qid), []), dtype=float)[:max(0, len(lat_counts) - 1)]
            ie_us = np.asarray(iat_edges.get(int(qid), []), dtype=float)[:max(0, len(iat_counts) - 1)]

            lp50_ms = (quantile_from_histogram(lat_counts, le_us, 0.50) / 1000.0) if le_us.size else 0.0
            lp95_ms = (quantile_from_histogram(lat_counts, le_us, 0.95) / 1000.0) if le_us.size else 0.0
            ltail   = tail_fraction(lat_counts, LAT_TAIL_BINS) if lat_counts.size else 0.0

            ip10_us = quantile_from_histogram(iat_counts, ie_us, 0.10) if ie_us.size else 0.0
            ip50_us = quantile_from_histogram(iat_counts, ie_us, 0.50) if ie_us.size else 0.0
            ihead   = head_fraction(iat_counts, IAT_HEAD_BINS) if iat_counts.size else 0.0

            weights.append(w)
            lat_p50s.append(lp50_ms); lat_p95s.append(lp95_ms); lat_tails.append(ltail)
            iat_p10s.append(ip10_us); iat_p50s.append(ip50_us); iat_heads.append(ihead)

            thr_lat_us = float(thr_lat) * 1000.0
            ex = exceed_fraction_scalar(lat_counts, le_us, thr_lat_us) if le_us.size else 0.0
            bl = below_fraction_scalar(iat_counts, ie_us, float(thr_iat)) if ie_us.size else 0.0
            per_exceed.append(ex); per_below.append(bl); per_w.append(w)

        if weights:
            w_arr = np.asarray(weights, dtype=float)

            def wavg(vs: List[float]) -> float:
                arr = np.asarray(vs, dtype=float)
                m   = np.isfinite(arr) & np.isfinite(w_arr) & (w_arr > 0)
                return float(np.average(arr[m], weights=w_arr[m])) if np.any(m) else 0.0

            lat_p50_ms = wavg(lat_p50s)
            lat_p95_ms = wavg(lat_p95s)
            lat_tail   = wavg(lat_tails)
            iat_p10_us = wavg(iat_p10s)
            iat_p50_us = wavg(iat_p50s)
            iat_head   = wavg(iat_heads)
        else:
            lat_p50_ms = lat_p95_ms = lat_tail = 0.0
            iat_p10_us = iat_p50_us = iat_head = 0.0

        if per_w:
            ww         = np.asarray(per_w, dtype=float)
            lat_exceed = float(np.average(np.asarray(per_exceed, dtype=float), weights=ww))
            iat_below  = float(np.average(np.asarray(per_below,  dtype=float), weights=ww))
        else:
            lat_exceed = 0.0
            iat_below  = 0.0

        if np.isfinite(lat_p95_ms):
            lat_hist.append(float(lat_p95_ms))
        if np.isfinite(iat_p10_us):
            iat_hist.append(float(iat_p10_us))

        bw_mbps = (byte_sum * 8.0) / (window_secs * 1e6) if window_secs > 0 else 0.0
        pps     = pkt_sum / window_secs if window_secs > 0 else 0.0

        yellow_frac = (yellow / color_total) if color_total > 0 else 0.0
        red_frac    = (red    / color_total) if color_total > 0 else 0.0

        teid_color_pkts = float(teid_color.get((qfi_i, teid_i), 0.0))
        qfi_total_pkts  = float(qfi_total_color.get(qfi_i, 0.0))
        teid_share      = (teid_color_pkts / qfi_total_pkts) if qfi_total_pkts > 0 else 0.0

        rows.append({
            "qfi":                           qfi_i,
            "teid":                          teid_i,
            "feat_bw_mbps":                  bw_mbps,
            "feat_pps":                      pps,
            "feat_lat_p50_ms":               lat_p50_ms,
            "feat_lat_p95_ms":               lat_p95_ms,
            "feat_lat_tail_frac":            lat_tail,
            "feat_lat_roll_p95_exceed_frac": lat_exceed,
            "feat_iat_p10_us":               iat_p10_us,
            "feat_iat_p50_us":               iat_p50_us,
            "feat_iat_head_frac":            iat_head,
            "feat_iat_roll_p10_below_frac":  iat_below,
            "feat_yellow_frac":              yellow_frac,
            "feat_red_frac":                 red_frac,
            "feat_teid_share":               teid_share,
            "feat_qfi_teid_count":           int(qfi_teid_count.get(qfi_i, 0)),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# QFI aggregation
# ---------------------------------------------------------------------------
def aggregate_qfi(teid_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate per-(qfi, teid) rows to per-qfi rows.

    Bandwidth and PPS are summed; latency/fraction features take the max
    across TEIDs; everything else takes the median.
    """
    if teid_df is None or teid_df.empty:
        return pd.DataFrame()

    df = teid_df.copy()
    if "qfi" not in df.columns:
        if df.index.name == "qfi":
            df = df.reset_index()
        elif isinstance(df.index, pd.MultiIndex) and "qfi" in df.index.names:
            df = df.reset_index()
        else:
            raise ValueError("aggregate_qfi(): missing 'qfi' column.")

    df["qfi"] = pd.to_numeric(df["qfi"], errors="coerce").fillna(-1).astype(int)
    df = df[df["qfi"] >= 0]
    if df.empty:
        return pd.DataFrame()

    feat_cols = [c for c in df.columns if c.startswith("feat_")]
    g = df.groupby("qfi", sort=False)

    def sel(cols: List[str], suffixes: tuple) -> List[str]:
        return [c for c in cols if any(c.endswith(s) for s in suffixes)]

    cols_sum  = sel(feat_cols, ("_mbps", "_pps"))
    cols_max  = sel(feat_cols, ("_ms", "_tail_frac", "_head_frac", "_frac"))
    cols_keep = [c for c in feat_cols if c not in set(cols_sum + cols_max)]

    parts = []
    if cols_sum:  parts.append(g[cols_sum].sum())
    if cols_max:  parts.append(g[cols_max].max())
    if cols_keep: parts.append(g[cols_keep].median())

    agg = pd.concat(parts, axis=1) if parts else g[feat_cols].median()
    agg = agg.reset_index()
    agg["qfi_bw_total"] = g["feat_bw_mbps"].sum().values if "feat_bw_mbps" in df.columns else 0.0

    extra_rows: List[dict] = []
    for key, sub in g:
        qfi   = int(key[0] if isinstance(key, tuple) else key)
        bw    = pd.to_numeric(sub.get("feat_bw_mbps",    0.0), errors="coerce").fillna(0.0).to_numpy(float)
        yfrac = pd.to_numeric(sub.get("feat_yellow_frac", 0.0), errors="coerce").fillna(0.0).to_numpy(float)
        rfrac = pd.to_numeric(sub.get("feat_red_frac",    0.0), errors="coerce").fillna(0.0).to_numpy(float)

        total_bw = float(np.nansum(bw))
        shares   = (bw / total_bw) if total_bw > 0 else np.zeros_like(bw)
        top      = np.sort(shares)[::-1] if shares.size else np.array([0.0, 0.0])

        extra_rows.append({
            "qfi":                   qfi,
            "qfi_teid_hhi":          float(np.nansum(shares * shares)),
            "qfi_teid_top1_share":   float(top[0]) if top.size >= 1 else 0.0,
            "qfi_teid_top2_share":   float(top[1]) if top.size >= 2 else 0.0,
            "qfi_share_entropy":     safe_entropy(shares),
            "qfi_frac_teids_active": float(np.sum(bw > 1e-6) / max(1, len(bw))),
            "qfi_yellow_bw_share":   float(np.nansum(bw * yfrac) / total_bw) if total_bw > 0 else 0.0,
            "qfi_red_bw_share":      float(np.nansum(bw * rfrac) / total_bw) if total_bw > 0 else 0.0,
        })

    return agg.merge(pd.DataFrame(extra_rows), on="qfi", how="left")


# ---------------------------------------------------------------------------
# Streaming feature engineering
# ---------------------------------------------------------------------------
def robust_z_causal(values: Deque[float], current: float, win: int) -> float:
    """Robust z-score using rolling median/MAD over the last `win` values."""
    arr = np.asarray(list(values)[-win:], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 5:
        return 0.0
    med   = float(np.median(arr))
    mad   = float(np.median(np.abs(arr - med)))
    denom = 1.4826 * (mad if mad > 0 else 1e-6)
    z     = (float(current) - med) / denom
    return float(z) if np.isfinite(z) else 0.0


def ewma_step(prev: Optional[float], x: float, alpha: float = 0.3) -> float:
    if prev is None or not np.isfinite(prev):
        return float(x)
    return float(alpha * x + (1.0 - alpha) * prev)


def slope_last_k(series: Deque[float], k: int = 5) -> float:
    ys = np.asarray(list(series)[-k:], dtype=float)
    if ys.size < 2 or not np.any(np.isfinite(ys)):
        return 0.0
    xs = np.arange(ys.size, dtype=float)
    A  = np.vstack([xs, np.ones_like(xs)]).T
    try:
        m, _ = np.linalg.lstsq(A, ys, rcond=None)[0]
    except Exception:
        m = 0.0
    return float(m) if np.isfinite(m) else 0.0


def rolling_stats(series: Deque[float], k: int) -> Tuple[float, float, float]:
    """Return (median, max, std) over the last k finite values."""
    arr = np.asarray(list(series)[-k:], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0.0, 0.0, 0.0
    return (
        float(np.median(arr)),
        float(np.max(arr)),
        float(np.std(arr, ddof=0)) if arr.size >= 2 else 0.0,
    )


def runlen_high(prev_run: int, z: float, thr: float = RUNLEN_THR, cap: int = RUNLEN_CAP) -> int:
    return int(min(cap, prev_run + 1)) if float(z) >= float(thr) else 0


class QfiFeatureEngineer:
    """
    Maintains per-QFI causal histories and emits engineered features for the model.

    All features are computed without lookahead: only values from previous
    windows are used to compute rolling statistics for the current window.
    """

    _ROLL_KEYS = [
        "qfi_bw_total",
        "qfi_yellow_bw_share",
        "qfi_red_bw_share",
        "qfi_teid_hhi",
        "qfi_teid_top1_share",
        "feat_lat_p95_ms",
    ]
    _Z_KEYS = ["qfi_bw_total", "qfi_yellow_bw_share", "feat_lat_p95_ms"]

    def __init__(self):
        self.hist:   Dict[int, Dict[str, Deque[float]]] = {}
        self.ewma_:  Dict[int, Dict[str, float]]        = {}
        self.runlen: Dict[int, Dict[str, int]]          = {}

    def _series(self, qfi: int, name: str) -> Deque[float]:
        self.hist.setdefault(qfi, {})
        if name not in self.hist[qfi]:
            self.hist[qfi][name] = deque(maxlen=512)
        return self.hist[qfi][name]

    def step(self, qfi_row: Dict[str, float]) -> Dict[str, float]:
        qfi = int(qfi_row["qfi"])
        out = dict(qfi_row)

        self.ewma_.setdefault(qfi, {})
        self.runlen.setdefault(qfi, {})

        base_fields = [k for k in out if k.startswith("feat_") or k.startswith("qfi_")]

        for name in base_fields:
            self._series(qfi, name).append(float(out.get(name, 0.0)))

        for name in base_fields:
            out[f"rz_{name}"] = robust_z_causal(
                self._series(qfi, name), float(out.get(name, 0.0)), win=ZL_WIN
            )

        for name in self._ROLL_KEYS:
            s            = self._series(qfi, name)
            med3, _,   _ = rolling_stats(s, 3)
            med5, mx5, std5 = rolling_stats(s, 5)
            med9, _,   _ = rolling_stats(s, 9)
            cur  = float(out.get(name, 0.0))
            prev = float(list(s)[-2]) if len(s) >= 2 else cur

            out[f"roll3_med_{name}"] = med3
            out[f"roll5_med_{name}"] = med5
            out[f"roll9_med_{name}"] = med9
            out[f"roll5_max_{name}"] = mx5
            out[f"roll5_std_{name}"] = std5
            out[f"delta_{name}"]     = cur - prev

            ew = ewma_step(self.ewma_[qfi].get(name), cur, alpha=0.3)
            self.ewma_[qfi][name] = ew
            out[f"ewma_{name}"]   = ew
            out[f"slope5_{name}"] = slope_last_k(s, k=5)

        for name in self._Z_KEYS:
            s   = self._series(qfi, name)
            cur = float(out.get(name, 0.0))
            zS  = robust_z_causal(s, cur, win=ZS_WIN)
            zL  = robust_z_causal(s, cur, win=ZL_WIN)
            out[f"zS_{name}"]      = zS
            out[f"zL_{name}"]      = zL
            out[f"deltaSL_{name}"] = zS - zL

            rl_prev = int(self.runlen[qfi].get(name, 0))
            rl = runlen_high(rl_prev, zL)
            self.runlen[qfi][name]   = rl
            out[f"runlen_hi_{name}"] = float(rl)

        return out

# ---------------------------------------------------------------------------
# Debouncer
# ---------------------------------------------------------------------------
class Debouncer:
    """
    Stateful per-QFI debouncer.

    An anomaly is declared after `k` consecutive positive predictions.
    It is cleared when the score drops below `thr * h` (hysteresis).
    """

    def __init__(self, k: int, h: float):
        self.k     = int(k)
        self.h     = float(h)
        self.buf:   Dict[int, Deque[int]] = {}
        self.state: Dict[int, int]        = {}

    def step(self, qfi: int, pred: int, score: float, thr: float) -> int:
        qfi = int(qfi)
        b   = self.buf.setdefault(qfi, deque(maxlen=self.k))
        b.append(int(pred))
        cur = int(self.state.get(qfi, 0))
        if cur == 0:
            if sum(b) >= self.k:
                cur = 1
        else:
            if float(score) < float(thr) * self.h:
                cur = 0
        self.state[qfi] = cur
        return cur


# ---------------------------------------------------------------------------
# Episode tracking
# ---------------------------------------------------------------------------
class EpisodeTracker:
    """
    Tracks anomaly episodes per QFI based on debounced predictions.

    An episode opens on the first window where debounced == 1
    and closes when debounced returns to 0.
    """

    def __init__(self):
        self.open_episodes: Dict[int, Dict] = {}   # qfi -> episode in progress
        self.closed: List[Dict]             = []   # completed episodes

    def step(self, qfi: int, win: int, deb_pred: int, score: float) -> Optional[str]:
        """
        Update state for one (qfi, window) observation.
        Returns 'start', 'end', or None.
        """
        ep = self.open_episodes.get(qfi)

        if deb_pred == 1:
            if ep is None:
                # New episode
                self.open_episodes[qfi] = {
                    "qfi":        qfi,
                    "start_win":  win,
                    "end_win":    win,
                    "peak_score": score,
                    "duration":   1,
                }
                return "start"
            else:
                # Ongoing episode
                ep["end_win"]    = win
                ep["peak_score"] = max(ep["peak_score"], score)
                ep["duration"]  += 1
                return None
        else:
            if ep is not None:
                # Episode just ended
                ep["end_win"] = win - 1
                self.closed.append(ep)
                del self.open_episodes[qfi]
                return "end"
            return None

    def close_all(self, last_win: int):
        """Close any episodes still open at the end of processing."""
        for qfi, ep in list(self.open_episodes.items()):
            ep["end_win"] = last_win
            self.closed.append(ep)
        self.open_episodes.clear()

    def all_episodes(self) -> List[Dict]:
        return sorted(self.closed, key=lambda e: (e["start_win"], e["qfi"]))


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def print_summary(
    episodes: List[Dict],
    n_processed: int,
    out_path: Path,
    interrupted: bool = False,
) -> None:
    sep = "-" * 60
    print(f"\n{sep}")
    print("Summary")
    status = "  (interrupted)" if interrupted else ""
    print(f"  Windows processed : {n_processed}{status}")
    print(f"  Anomaly episodes  : {len(episodes)}")
    if episodes:
        print()
        for ep in episodes:
            print(
                f"    QFI {ep['qfi']:<3}  "
                f"win {ep['start_win']:04d}-{ep['end_win']:04d}  "
                f"{ep['duration']:>3} windows  "
                f"peak score={ep['peak_score']:.3f}"
            )
    print(sep)
    suffix = "  (partial)" if interrupted else ""
    print(f"wrote {out_path}{suffix}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Kestrel MWE: run the post-switch anomaly detection pipeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--data-dir",    type=Path, default=Path("data"),
                    help="Root data directory. Expected sub-dirs: cms/ and keys/.")
    ap.add_argument("--bundle-dir",  type=Path, default=Path("bundles"),
                    help="Directory containing xgb.json and bundle.json.")
    ap.add_argument("--bins-json",   type=Path, default=None,
                    help="Path to bins.json. Defaults to bins.json in the current directory.")
    ap.add_argument("--window-secs", type=float, default=1.0,
                    help="Duration of each measurement window in seconds.")
    ap.add_argument("--max-windows", type=int, default=DEFAULT_MAX_WINDOWS,
                    help="Maximum number of windows to process. Use 0 for all.")
    ap.add_argument("--depth",       type=int, default=DEFAULT_DEPTH,
                    help="CMS depth (number of hash rows).")
    ap.add_argument("--width",       type=int, default=DEFAULT_WIDTH,
                    help="CMS width (number of buckets per row).")
    args = ap.parse_args()

    # -- Bins ----------------------------------------------------------------
    lat_dict, iat_dict = load_qid_bins(args.bins_json)
    if lat_dict is None or iat_dict is None:
        raise SystemExit(
            "bins.json not found or malformed. "
            "Pass --bins-json or set KESTREL_BINS_JSON."
        )

    # -- Bundle + model ------------------------------------------------------
    bundle_path = args.bundle_dir / "bundle.json"
    model_path  = args.bundle_dir / "xgb.json"
    for p in (bundle_path, model_path):
        if not p.exists():
            raise SystemExit(f"Missing file: {p}")

    bundle     = json.loads(bundle_path.read_text())
    feat_cols  = list(bundle["feat_cols"])
    thr_global = float(bundle["thr_global"])
    thr_map    = {int(k): float(v) for k, v in bundle.get("thr_map", {}).items()}
    deb_k      = int(bundle.get("debounce", {}).get("k", 1))
    deb_h      = float(bundle.get("debounce", {}).get("h", 1.0))

    model = XGBClassifier()
    model.load_model(str(model_path))

    # -- Data dirs -----------------------------------------------------------
    cms_dir  = args.data_dir / "cms"
    keys_dir = args.data_dir / "keys"
    for d in (cms_dir, keys_dir):
        if not d.exists():
            raise SystemExit(f"Missing directory: {d}")

    all_windows = list_windows(cms_dir)
    if not all_windows:
        raise SystemExit(f"No parquet windows found under {cms_dir}")

    max_w = args.max_windows if args.max_windows > 0 else len(all_windows)
    cms_files = all_windows[:max_w]

    # -- Output --------------------------------------------------------------
    out_dir  = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "events.jsonl"

    # -- Startup banner ------------------------------------------------------
    print()
    print("Kestrel -- anomaly detection on 5G sketch telemetry")
    print(f"  Data   : {args.data_dir}/  ({len(all_windows)} windows available, processing first {len(cms_files)})")
    print(f"  Model  : {args.bundle_dir}/")
    print(f"  Output : {out_path}")
    print()
    print("  Press Ctrl+C at any time to stop and print a summary.")
    print()
    
    # -- Stateful components -------------------------------------------------
    teid_state = TeidRollingState()
    qfi_eng    = QfiFeatureEngineer()
    deb        = Debouncer(k=deb_k, h=deb_h)
    tracker    = EpisodeTracker()

    n_processed  = 0
    last_win     = 0
    interrupted  = False

    # -- Ctrl+C handler ------------------------------------------------------
    def _handle_sigint(sig, frame):
        # Raise KeyboardInterrupt so the except block below runs cleanly
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_sigint)

    # -- Main loop -----------------------------------------------------------
    try:
        with out_path.open("w") as f_out:
            pbar = tqdm(cms_files, desc="Stream", unit="win")
            for cms_path in pbar:
                n         = win_num(cms_path)
                keys_path = keys_dir / f"window_{n}.csv"
                if not keys_path.exists():
                    continue

                cms_df  = pd.read_parquet(cms_path)
                keys_df = pd.read_csv(keys_path)

                lat_cols = sorted(
                    [c for c in cms_df.columns if c.startswith("lat")],
                    key=lambda s: int(s[3:]),
                )
                iat_cols = sorted(
                    [c for c in cms_df.columns if c.startswith("iat")],
                    key=lambda s: int(s[3:]),
                )
                sketch_feat_cols = (
                    lat_cols + iat_cols
                    + ["pkt_cnt", "byte_cnt", "green_cnt", "yellow_cnt", "drop_cnt"]
                )

                recon = min_query(
                    cms_df, keys_df, sketch_feat_cols,
                    depth=args.depth, width=args.width,
                )

                teid_df = teid_features_from_recon(
                    recon, lat_dict, iat_dict, teid_state, args.window_secs
                )
                if teid_df.empty:
                    continue

                qfi_df = aggregate_qfi(teid_df)
                if qfi_df.empty:
                    continue

                eng_df = pd.DataFrame([
                    qfi_eng.step(row) for row in qfi_df.to_dict(orient="records")
                ])

                for c in feat_cols:
                    if c not in eng_df.columns:
                        eng_df[c] = 0.0
                    eng_df[c] = (
                        pd.to_numeric(eng_df[c], errors="coerce")
                        .replace([np.inf, -np.inf], np.nan)
                        .fillna(0.0)
                    )

                X      = eng_df[feat_cols].to_numpy(dtype=float)
                scores = model.predict_proba(X)[:, 1]

                for i, row in eng_df.reset_index(drop=True).iterrows():
                    qfi      = int(row["qfi"])
                    score    = float(scores[i])
                    thr      = float(thr_map.get(qfi, thr_global))
                    pred     = int(score >= thr)
                    deb_pred = int(deb.step(qfi, pred=pred, score=score, thr=thr))

                    event = tracker.step(qfi, n, deb_pred, score)
                    if event == "start":
                        tqdm.write(
                            f"[win {n:04d}]  ANOMALY START  "
                            f"QFI {qfi}  score={score:.3f}  thr={thr:.3f}"
                        )
                    elif event == "end":
                        ep = tracker.closed[-1]
                        tqdm.write(
                            f"[win {n:04d}]  ANOMALY END    "
                            f"QFI {qfi}  duration={ep['duration']} windows"
                        )

                    f_out.write(json.dumps({
                        "window":    int(n),
                        "qfi":       qfi,
                        "score":     score,
                        "thr":       thr,
                        "pred":      pred,
                        "debounced": deb_pred,
                    }) + "\n")

                n_processed += 1
                last_win     = n

    except KeyboardInterrupt:
        interrupted = True
        tqdm.write(f"\nInterrupted at window {last_win}.")

    # -- Close any episodes still open ---------------------------------------
    tracker.close_all(last_win)

    # -- Summary -------------------------------------------------------------
    print_summary(
        tracker.all_episodes(),
        n_processed,
        out_path,
        interrupted=interrupted,
    )


if __name__ == "__main__":
    main()