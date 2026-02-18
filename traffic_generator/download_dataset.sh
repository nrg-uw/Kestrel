#!/usr/bin/env bash
# Minimal deploy + remote control for traffic generator
# Commands: sync | start | stop | status | logs | restart

set -euo pipefail

REMOTE="user@generator_host"

# Local artifacts
LOCAL_BIN="build/traffic_generator"
LOCAL_CFG="config.json"

# Remote locations
REMOTE_BIN="/tmp/traffic_generator"
REMOTE_CFG="/tmp/config.json"
REMOTE_DAEMON="/tmp/trafficd"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing '$1'"; exit 1; }; }
need ssh; need scp

push_daemon() {
  ssh "$REMOTE" "cat > '$REMOTE_DAEMON' <<'EOF_DAEMON'
#!/usr/bin/env bash
set -euo pipefail

# --- simple knobs (edit on the remote if you need) ---
BIN="/tmp/traffic_generator"
CFG="/tmp/config.json"
UE_COUNT=100
DURATION=0           # 0 = run forever (if binary supports it)
SUDO="sudo"          # set to "" to drop sudo

# --- derived paths ---
NAME="trafficd"
RUNDIR="/tmp/$NAME"
LOGDIR="$RUNDIR/logs"
PIDFILE="$RUNDIR/$NAME.pid"
mkdir -p "$RUNDIR" "$LOGDIR"

is_running() {
  [[ -f "$PIDFILE" ]] || return 1
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start() {
  if is_running; then echo "[*] $NAME already running (pid $(cat "$PIDFILE"))"; exit 0; fi
  [[ -x "$BIN" ]] || { echo "[!] $BIN missing/not executable"; exit 1; }
  [[ -f "$CFG" ]] || { echo "[!] $CFG missing"; exit 1; }

  ts="$(date +'%Y%m%d_%H%M%S')"
  logfile="$LOGDIR/${NAME}_$ts.log"
  ln -sfn "$logfile" "$LOGDIR/latest.log"

  cmd="$SUDO $BIN --config $CFG --ue-count $UE_COUNT --duration $DURATION"
  echo "[*] Starting → $logfile"
  nohup bash -lc "exec $cmd" >> "$logfile" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1

  if is_running; then echo "[✓] Started (pid $(cat "$PIDFILE"))"; else echo "[x] Failed. See $logfile"; rm -f "$PIDFILE"; exit 1; fi
}

stop() {
  if ! is_running; then echo "[*] $NAME not running"; rm -f "$PIDFILE"; exit 0; fi
  pid="$(cat "$PIDFILE")"; echo "[*] Stopping (pid $pid)..."
  kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do kill -0 "$pid" 2>/dev/null || { echo "[✓] Stopped"; rm -f "$PIDFILE"; return 0; }; sleep 0.5; done
  echo "[!] Force kill"; kill -9 "$pid" 2>/dev/null || true; rm -f "$PIDFILE"; echo "[✓] Stopped (SIGKILL)"
}

status() {
  if is_running; then pid="$(cat "$PIDFILE")"; echo "[✓] $NAME running (pid $pid)"; ps -o pid,ppid,cmd -p "$pid"; else echo "[x] $NAME not running"; return 1; fi
}

logs() {
  f="$LOGDIR/latest.log"; [[ -f "$f" ]] || { echo "[!] No logs yet in $LOGDIR"; ls -1t "$LOGDIR" 2>/dev/null || true; exit 1; }
  echo "[*] tail -f $f (Ctrl-C to stop)"; tail -n 200 -f "$f"
}

restart() { stop || true; start; }

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  logs) logs ;;
  restart) restart ;;
  *) echo "Usage: $0 {start|stop|status|logs|restart}"; exit 1 ;;
esac
EOF_DAEMON
chmod +x '$REMOTE_DAEMON'
"
}

sync() {
  [[ -f "$LOCAL_BIN" ]] || { echo "Error: $LOCAL_BIN not found. Run build.sh first."; exit 1; }
  [[ -f "$LOCAL_CFG" ]] || { echo "Error: $LOCAL_CFG not found."; exit 1; }

  echo "[*] Copying binary + config to $REMOTE..."
  scp -q "$LOCAL_BIN" "$REMOTE:$REMOTE_BIN"
  scp -q "$LOCAL_CFG" "$REMOTE:$REMOTE_CFG"
  push_daemon
  echo "[✓] Synced and daemon installed"
}

remote() { ssh -t "$REMOTE" "$REMOTE_DAEMON $1"; }

case "${1:-}" in
  sync)    sync ;;
  start)   sync; remote start ;;
  stop)    remote stop ;;
  status)  remote status ;;
  logs)    remote logs ;;
  restart) sync; remote restart ;;
  *) echo "Usage: $0 {sync|start|stop|status|logs|restart}"; exit 1 ;;
esac
