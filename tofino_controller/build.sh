#!/usr/bin/env bash
set -euo pipefail

JUMP_HOST="${JUMP_HOST}"
REMOTE_DIR="${REMOTE_DIR:-/tmp/tofino_controller}"
REMOTE_CMD="${1:-}"   # optional arg, e.g. "examples/metering.py"

echo "[*] Syncing to $JUMP_HOST:$REMOTE_DIR ..."
rsync -az --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  ./ "$JUMP_HOST:$REMOTE_DIR/"

echo "[*] Running remote setup ..."
ssh -t "$JUMP_HOST" "bash '$REMOTE_DIR/remote_setup.sh' '$REMOTE_CMD'"

echo "[*] Done."
