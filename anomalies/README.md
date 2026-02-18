# Anomalies

Scripts to orchestrate anomaly injection (e.g., microbursts, congestion, policy abuse) on remote hosts via SSH.

## Quickstart

Check `config.py` for anomaly configuration parameters.
These assume that you have built and deployed the relevant anomaly injector binaries on the remote host.
See the [anomaly injector README](../anomaly_injector/README.md) for details.

**Note**. The orchestrate_contention scritp assumes that iperf3 is working properly. Use `check_iperf3.sh`

**Note**. Not meant to be used standalone. Use `dataset_collector.collect_anomalies` instead.


