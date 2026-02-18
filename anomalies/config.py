
LABEL_OFFSET_MS = 0  


ANOMALIES = {
    "microburst": {
        "warmup_s": 10,
        "gap_s": (10, 30),
        "burst_ms": 50,
        "idle_ms": 100,
        "cycles": (3, 10),
        "qfis": [3, 5],
        "n_teids": 1,
    },
    "congestion": {
        "warmup_s": 30,
        "episode_s": (25, 40),
        "gap_s": (60, 120),
        "qfis": [2, 3, 7],
    },
    "contention": {
        "warmup_s": 30,
        "episode_s": (12, 30),
        "gap_s": (90, 150),
        "qfis": [1, 2, 3, 4, 5, 6, 7, 8, 9],
    },
    "policy_abuse": {
        "warmup_s": 30,
        "episode_s": (30, 60),
        "gap_s": (150, 300),
        "qfis": [3, 2],
        "qfi_map": "3:6,2:4",
    },
}
