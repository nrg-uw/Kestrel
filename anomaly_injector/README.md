# Anomaly Injector

Scripts to inject anomalies by sending traffic through the Tofino switch.
These injectors run on remote hosts (e.g., hpc3), which is connected to the Tofino switch.
They are orchestrated via SSH using orchestrator scripts in the [anomalies](../anomalies) directory.

## Quickstart

```
cd anomaly_injector
./build.sh
./deploy.sh
```

This builds and deploys the anomaly injector binaries to the remote host specified in `config.py` in /tmp/