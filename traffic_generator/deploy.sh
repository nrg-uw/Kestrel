#!/usr/bin/env bash
# Commands: sync | start | stop | status | logs | restart
set -euo pipefail

# ---- remote host + paths ----
REMOTE="${REMOTE}"
REMOTE_BIN="${REMOTE_BIN:-/tmp/traffic_generator}"
REMOTE_CFG="${REMOTE_CFG:-/tmp/config.json}"
REMOTE_DAEMON="${REMOTE_DAEMON:-/tmp/trafficd}"   # remote daemon (file)

# ---- local artifacts ----
LOCAL_BIN="${LOCAL_BIN:-build/traffic_generator}"
LOCAL_CFG="${LOCAL_CFG:-config.json}"
LOCAL_DAEMON="${LOCAL_DAEMON:-trafficd.sh}"       # your local script name

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing '$1'"; exit 1; }; }
need ssh; need scp

ensure_daemon() {
  if ! ssh -q "$REMOTE" "test -x '$REMOTE_DAEMON'"; then
    [[ -f "$LOCAL_DAEMON" ]] || { echo "Error: $LOCAL_DAEMON not found."; exit 1; }
    echo "[*] installing daemon on remote..."
    scp -q "$LOCAL_DAEMON" "$REMOTE:$REMOTE_DAEMON"
    ssh -q "$REMOTE" "chmod +x '$REMOTE_DAEMON'"
    echo "[✓] daemon installed at $REMOTE_DAEMON"
  fi
}

sync() {
  [[ -f "$LOCAL_BIN" ]] || { echo "Error: $LOCAL_BIN not found. Run build.sh first."; exit 1; }
  [[ -f "$LOCAL_CFG" ]] || { echo "Error: $LOCAL_CFG not found."; exit 1; }
  echo "[*] syncing binary + config..."
  scp -q "$LOCAL_BIN" "$REMOTE:$REMOTE_BIN"
  scp -q "$LOCAL_CFG" "$REMOTE:$REMOTE_CFG"
  echo "[✓] sync complete"
}

remote() { ssh -t "$REMOTE" "$REMOTE_DAEMON $*"; }

case "${1:-}" in
  sync)     sync ;;
  start)    ensure_daemon; sync; remote start ;;
  restart)  ensure_daemon; sync; remote restart ;;
  stop)     ensure_daemon; remote stop ;;
  status)   ensure_daemon; remote status ;;
  logs)     ensure_daemon; remote logs ;;
  *)
    echo "Usage: $0 {sync|start|stop|status|logs|restart}"
    echo "Env: REMOTE, REMOTE_BIN, REMOTE_CFG, REMOTE_DAEMON, LOCAL_BIN, LOCAL_CFG, LOCAL_DAEMON"
    exit 1
    ;;
esac
