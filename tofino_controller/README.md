# Tofino Controller

Wrapper around Intel's BFRT gRPC client for controlling Tofino-based P4 programs.
Allow easy access to tables, counters, meters, and registers.
Used in [SliceScope](https://arxiv.org/pdf/2512.12123), Kestrel and [Blink](https://rboutaba.cs.uwaterloo.ca/Papers/Conferences/2023/rouili-blink-noms25.pdf) projects.

## Quick start

```bash
# On your local machine (push + run on jump host):
./build.sh examples/connection.py
```

You should see the connected program name and a list of tables.

### What `build.sh` does
- rsync this repo to your remote jump host (e.g., /tmp/tofino_controller)
- creates/updates a venv on the jump host (~/bfrt-env) with required packages
- installs requirements from `requirements.txt`
- runs the specified script (e.g., `examples/connection.py`)

**Note**: The jump host is the machine that can reach the Tofino switch via gRPC.


### Configure the BFRT target
You can set the target Tofino switch and port via environment variables:
```bash
export BFRTCTL_HOST=ufi3        # Tofino switch hostname or IP (populate /etc/hosts on jump host)
export BFRTCTL_PORT=50052       # Tofino switch gRPC port (default 50052)
export BFRTCTL_DEVICE_ID=0      # Tofino device ID (default 0)
export BFRTCTL_PIPE_ID=0xFFFF   # Tofino pipe ID (default 0xFFFF)
```

Configuration file `~/.config/bfrt_controller/config.yaml` can also be used as well.
```yaml
host: ufi3
port: 50052
device_id: 0
pipe_id: 0xFFFF
program_name: kestrel
```
Hostnames can be managed in /etc/hosts on the jump host; e.g. 10.10.8.95 ufi3

## Examples
- `examples/connection.py`: connect to the switch and list tables
- `examples/metering.py`: configure a color-aware meter
- `examples/scheduling.py`: configure queue scheduling
- `examples/telemetry.py`: configure INT postcard generation

## Extending the controller
- Add new high-level helpers under `bfrt/recipes/`
- Use `Controller` class in your own scripts
- Use `BfrtSession` class for low-level access to tables, counters, meters



