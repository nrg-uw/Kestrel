# Kestrel

**Kestrel** is a sketch-based telemetry system for 5G user planes that detects
QoS anomalies with fine-grained per-flow visibility at a fraction of the cost of
per-packet telemetry. Kestrel is evaluated on a 5G testbed with an Intel Tofino
switch, and achieves ~10% better detection accuracy than selective postcard schemes
while reducing export bandwidth by 10x.

This repository contains code for our IEEE/IFIP NOMS 2026 paper:
[Rethinking Telemetry Design for Fine-Grained Anomaly Detection in 5G User Plane](https://arxiv.org/abs/2510.27664)


## Quickstart

```bash
./run.sh
```

This sets up a virtual environment, installs dependencies, and runs the
detection pipeline on the included telemetry data. Results are written to
`output/events.jsonl`.

To process all 600 windows:
```bash
./run.sh --max-windows 600
```


## Repository Structure

```
kestrel/
├── kestrel.py             # anomaly detection pipeline (start here)
├── run.sh                 # setup venv and run kestrel.py
├── bins.json              # per-QID histogram bin edges (latency and IAT)
├── data/
│   ├── cms/               # sketch register dumps from the Tofino switch
│   └── keys/              # flow keys (teid, qfi, qid) per window
├── bundles/
│   ├── xgb.json           # pre-trained XGBoost model
│   └── bundle.json        # feature list, thresholds, debounce parameters
├── tofino/                # P4 program for the Intel Tofino switch
├── tofino_controller/     # Python controller via BFRT gRPC
├── telemetry_collector/   # collects INT telemetry postcards from the switch
├── traffic_generator/     # generates GTP-U traffic for experiments
├── anomaly_injector/      # injects synthetic anomalies on a remote host
└── anomalies/             # SSH orchestration for anomaly injection
```


## Data

The telemetry in `data/` is switch telemetry collected from our 5G testbed
during baseline and anomaly experiments. Each window file contains a 1-second
snapshot of sketch registers exported from the Tofino switch. The data includes
windows with injected anomalies (microburst, congestion, contention,
policy abuse).

See the paper for details.


## Testbed

Our setup used four machines connected to an Intel Tofino switch:

```
  [traffic host] ----> [Tofino switch] ----> [receiver host]
                              |
                              | INT postcards (per-packet)
                              | sketch registers via gRPC (1s windows)
                              v
                       [telemetry host]
                              ^
                              | SSH orchestration
                       [control host]
                       (runs kestrel.py)
```

We release the source code for the switch P4 program, controller, telemetry collector, traffic generator, and anomaly injector. Deployment scripts use placeholder hostnames and should be updated to match your environment.

- [`tofino/`](tofino/) — P4 program for the switch
- [`tofino_controller/`](tofino_controller/) — BFRT gRPC controller
- [`telemetry_collector/`](telemetry_collector/) — INT postcard collector
- [`traffic_generator/`](traffic_generator/) — GTP-U traffic generator
- [`anomaly_injector/`](anomaly_injector/) — synthetic anomaly injection
- [`anomalies/`](anomalies/) — SSH orchestration for anomaly injection


## Citation

If you use this code in your research, please cite:

```bibtex
@inproceedings{kestrel-noms26,
  author    = {Saha, Niloy and Limam, Noura and Xiao, Yang and Boutaba, Raouf},
  title     = {{Rethinking Telemetry Design for Fine-Grained Anomaly Detection in 5G User Plane}},
  booktitle = {Proc. IEEE/IFIP NOMS},
  year      = {2026},
  pages     = {1--9},
}
```