#!/bin/bash
# deploy_monitor.sh — copy and run interface_monitor.sh on remote host

set -euo pipefail

REMOTE_HOST="user@generator_host"
REMOTE_INTERFACE="enp2s0f0"  # Interface traffic_generator uses to send traffic
REMOTE_DIR="/tmp"

echo "[*] Copying interface monitor script to remote host..."
scp interface_monitor.sh "$REMOTE_HOST:$REMOTE_DIR/"

echo "[*] Launching interface monitor on remote host..."
ssh -t "$REMOTE_HOST" "cd $REMOTE_DIR && bash interface_monitor.sh $REMOTE_INTERFACE"
