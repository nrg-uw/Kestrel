#!/usr/bin/env bash
set -euo pipefail

# anomalies/check_iperf3.sh
# Quick health-check that the interferer host can reach the iperf3 server.

# Defaults (override via flags or env)
HOST="${HOST}"
SERVER="${SERVER:-192.168.44.128}"
PORT="${PORT:-5201}"

# Timeouts
SSH_OPTS=(-q -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2)
IPERF_TIMEOUT="${IPERF_TIMEOUT:-5}"      # seconds for the iperf3 client test
TCP_PROBE_TIMEOUT="${TCP_PROBE_TIMEOUT:-2}"  # seconds for TCP reachability probe

usage() {
  cat <<EOF
Usage: $0 [--host <ssh_host>] [--server <iperf_server_ip>] [--port <port>]

Defaults:
  --host   ${HOST}
  --server ${SERVER}
  --port   ${PORT}

Env overrides:
  HOST, SERVER, PORT, IPERF_TIMEOUT, TCP_PROBE_TIMEOUT
EOF
}

# Parse flags (optional)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)   HOST="$2"; shift 2 ;;
    --server) SERVER="$2"; shift 2 ;;
    --port)   PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

echo "[*] Checking SSH access to ${HOST}"
if ! ssh "${SSH_OPTS[@]}" "${HOST}" "echo ok" >/dev/null 2>&1; then
  echo "[x] Cannot SSH to ${HOST} (keys/permissions?)."
  exit 1
fi
echo "[✓] SSH reachable."

echo "[*] Verifying iperf3 is installed on ${HOST}"
if ! ssh "${SSH_OPTS[@]}" "${HOST}" "command -v iperf3 >/dev/null"; then
  echo "[x] iperf3 not found on ${HOST}. Install it (e.g., sudo apt-get install iperf3)."
  exit 1
fi
echo "[✓] iperf3 present."

echo "[*] Probing TCP ${SERVER}:${PORT} from ${HOST} (timeout ${TCP_PROBE_TIMEOUT}s)"
# Prefer nc if present; otherwise use /dev/tcp with timeout to avoid hangs.
REMOTE_PROBE="if command -v nc >/dev/null 2>&1; then
  nc -z -w${TCP_PROBE_TIMEOUT} ${SERVER} ${PORT};
else
  command -v timeout >/dev/null 2>&1 && timeout ${TCP_PROBE_TIMEOUT}s bash -lc '</dev/tcp/${SERVER}/${PORT}';
fi"

if ! ssh "${SSH_OPTS[@]}" "${HOST}" "${REMOTE_PROBE}"; then
  echo "[x] TCP ${SERVER}:${PORT} NOT reachable from ${HOST}."
  echo "    Ensure routing/firewall is open and the iperf3 server is running."
  echo "    Start on server host:"
  echo "      iperf3 -s -p ${PORT}"
  exit 1
fi
echo "[✓] TCP reachable."

echo "[*] Running quick iperf3 client from ${HOST} → ${SERVER}:${PORT} (timeout ${IPERF_TIMEOUT}s)"
if ! ssh "${SSH_OPTS[@]}" "${HOST}" "timeout ${IPERF_TIMEOUT}s iperf3 -c '${SERVER}' -p ${PORT} -t 1 -P 1 --get-server-output >/dev/null 2>&1"; then
  echo "[x] iperf3 test failed."
  echo "    Make sure the server is running on ${SERVER}:${PORT}."
  echo "    Quick start on the server host:"
  echo "      iperf3 -s -p ${PORT}"
  exit 1
fi
echo "[✓] iperf3 quick test passed."

echo "[✓] All checks OK."
