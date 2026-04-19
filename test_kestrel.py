"""
Kestrel verification tests.

Run after the detection pipeline:
    ./run.sh          # or: docker run --rm -v "$(pwd)/output:/kestrel/output" kestrel
    python -m pytest test_kestrel.py -v

Each test is tied to a specific claim or design property from the paper.
"""

import json
import pytest
from collections import defaultdict
from pathlib import Path

OUTPUT = Path("output/events.jsonl")
REQUIRED_FIELDS = {"window", "qfi", "score", "thr", "pred", "debounced"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def records():
    assert OUTPUT.exists(), (
        f"{OUTPUT} not found -- run the pipeline first: ./run.sh"
    )
    rows = [json.loads(line) for line in OUTPUT.read_text().splitlines() if line.strip()]
    assert rows, f"{OUTPUT} is empty"
    return rows


@pytest.fixture(scope="session")
def episodes(records):
    """Reconstruct debounced anomaly episodes per QFI from the output records."""
    by_qfi = defaultdict(list)
    for r in records:
        by_qfi[r["qfi"]].append(r)

    eps = []
    for qfi, rows in by_qfi.items():
        rows = sorted(rows, key=lambda r: r["window"])
        dur, peak = 0, 0.0
        for r in rows:
            if r["debounced"] == 1:
                dur += 1
                peak = max(peak, r["score"])
            elif dur > 0:
                eps.append({"qfi": qfi, "duration": dur, "peak_score": peak})
                dur, peak = 0, 0.0
        if dur > 0:
            eps.append({"qfi": qfi, "duration": dur, "peak_score": peak})
    return eps


# ---------------------------------------------------------------------------
# Test 1: Output schema
# Basic sanity -- every record has the expected fields and value ranges.
# ---------------------------------------------------------------------------

def test_output_schema(records):
    for r in records:
        assert REQUIRED_FIELDS == set(r.keys()), f"Unexpected fields: {r}"
        assert isinstance(r["window"], int)
        assert isinstance(r["qfi"], int)
        assert 0.0 <= r["score"] <= 1.0, f"Score out of range: {r['score']}"
        assert 0.0 <= r["thr"]   <= 1.0, f"Threshold out of range: {r['thr']}"
        assert r["pred"]      in (0, 1)
        assert r["debounced"] in (0, 1)


# ---------------------------------------------------------------------------
# Test 2: Score discrimination
# The paper claims Kestrel achieves high detection accuracy (§VI-A, Fig. 3).
# The XGBoost model should produce clearly separated scores for normal vs.
# anomalous windows.
# ---------------------------------------------------------------------------

def test_score_discrimination(records):
    anomalous = [r["score"] for r in records if r["pred"] == 1]
    normal    = [r["score"] for r in records if r["pred"] == 0]

    assert anomalous and normal, "Need both anomalous and normal records"

    mean_anomalous = sum(anomalous) / len(anomalous)
    mean_normal    = sum(normal)    / len(normal)

    assert mean_anomalous > 0.9, (
        f"Mean anomaly score too low ({mean_anomalous:.3f}); model may not be discriminating"
    )
    assert mean_normal < 0.1, (
        f"Mean normal score too high ({mean_normal:.3f}); model may be over-firing on baseline traffic"
    )
    assert (mean_anomalous - mean_normal) > 0.8, (
        f"Insufficient score separation: anomalous={mean_anomalous:.3f}, normal={mean_normal:.3f}"
    )


# ---------------------------------------------------------------------------
# Test 3: Per-QFI thresholds differ
# The paper uses per-QID sketch partitioning (§III-C) and per-QFI anomaly
# thresholds calibrated to each traffic class. A single global threshold
# would indicate this mechanism is not working.
# ---------------------------------------------------------------------------

def test_per_qfi_thresholds(records):
    thr_by_qfi = {}
    for r in records:
        thr_by_qfi.setdefault(r["qfi"], set()).add(round(r["thr"], 6))

    # Each QFI should have a single stable threshold
    for qfi, thrs in thr_by_qfi.items():
        assert len(thrs) == 1, f"QFI {qfi} has inconsistent thresholds: {thrs}"

    # Thresholds should differ across QFIs (per-QFI calibration)
    all_thresholds = {list(v)[0] for v in thr_by_qfi.values()}
    assert len(all_thresholds) >= 2, (
        f"All QFIs share the same threshold ({all_thresholds}); per-QFI calibration not reflected"
    )


# ---------------------------------------------------------------------------
# Test 4: Both transient and sustained anomaly episodes detected
# The paper targets diverse anomaly types (Table I): microbursts are
# short-lived (sub-second), while congestion and policy abuse are sustained
# (tens of seconds). Both should be visible in the detected episodes.
# ---------------------------------------------------------------------------

def test_anomaly_episode_diversity(episodes):
    assert episodes, "No debounced anomaly episodes found"

    short     = [e for e in episodes if e["duration"] <= 3]  # microburst-like
    sustained = [e for e in episodes if e["duration"] >= 5]  # congestion / policy-abuse-like

    assert short, (
        "No short episodes (<=3 windows) found; transient anomalies (microbursts) may not be detected"
    )
    assert sustained, (
        "No sustained episodes (>=5 windows) found; prolonged anomalies (congestion, policy abuse) may not be detected"
    )

    affected_qfis = {e["qfi"] for e in episodes}
    assert len(affected_qfis) >= 3, (
        f"Too few QFIs affected ({affected_qfis}); contention-type anomalies distribute across flows"
    )


# ---------------------------------------------------------------------------
# Test 5: Debouncing filters spurious detections
# The paper's debouncing step (§VI pipeline) requires k consecutive windows
# before raising an alarm, suppressing single-window noise. The raw pred=1
# count should therefore exceed the debounced alarm count.
# ---------------------------------------------------------------------------

def test_debouncing_filters_transients(records):
    n_pred      = sum(1 for r in records if r["pred"] == 1)
    n_debounced = sum(1 for r in records if r["debounced"] == 1)

    assert n_pred > 0,      "No anomalies detected at all (pred=1 count is zero)"
    assert n_debounced > 0, "No debounced alarms raised"
    assert n_pred > n_debounced, (
        f"Debouncing had no effect: pred={n_pred}, debounced={n_debounced}. "
        "Transient detections should be suppressed."
    )
