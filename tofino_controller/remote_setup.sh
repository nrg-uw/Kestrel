#!/usr/bin/env bash
set -euo pipefail

REMOTE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${VENV_DIR:-$HOME/bfrt-env}"
REQ_FILE="$REMOTE_DIR/requirements.txt"
RUN_PATH="${1:-}"   # optional, e.g. "examples/metering.py"
BFRTCTL_HOST="${BFRTCTL_HOST:-ufi3}"  # ufi3 Tofino switch
BFRTCTL_PORT="${BFRTCTL_PORT:-50052}"

# Ensure python3-venv on Debian/Ubuntu
if command -v apt-get >/dev/null 2>&1; then
  if ! dpkg -s python3-venv >/dev/null 2>&1; then
    echo "[remote] Installing python3-venv..."
    sudo -v
    sudo apt-get update -y
    sudo apt-get install -y python3-venv
  fi
fi

# Create venv if missing
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[remote] Creating venv at $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

# Activate
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

# Install deps
pip install -U pip wheel
pip install -r "$REQ_FILE"

# Make repo importable WITHOUT installing it: use PYTHONPATH
export PYTHONPATH="$REMOTE_DIR"

# Quick sanity checks
python - <<'PY'
import importlib, bfrt, bfrt.controller
print("[remote] bfrt:", bfrt.__file__)
print("[remote] controller:", bfrt.controller.__file__)
m = importlib.import_module("bfrt.vendor.bfrt_grpc.client")
print("[remote] vendor client:", m.__file__)
PY

# Optional: run a script inside this repo
if [[ -n "$RUN_PATH" ]]; then
  echo "[remote] Running: python $REMOTE_DIR/$RUN_PATH"
  export BFRTCTL_HOST BFRTCTL_PORT
  python "$REMOTE_DIR/$RUN_PATH"
fi

echo "[remote] All good."
