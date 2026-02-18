#!/usr/bin/env bash
set -euo pipefail

# Config
SDE_SETUP="${SDE_SETUP:-$HOME/set_sde.bash}"
SDE_DIR="${SDE_DIR:-$HOME/bf-sde-9.9.0}"
PIPELINE="${PIPELINE:-kestrel}"
LOGFILE="${LOGFILE:-$HOME/bf_switchd.log}"

cmd="${1:-}"

start_cmd() {
  echo "Starting bf_switchd..."
  
  # Check if already running
  if pgrep -x bf_switchd >/dev/null 2>&1; then
    echo "Already running. Use: $0 stop"
    exit 1
  fi
  
  # Validate paths
  [[ -f "$SDE_SETUP" ]] || { echo "Missing: $SDE_SETUP"; exit 1; }
  [[ -d "$SDE_DIR" ]] || { echo "Missing: $SDE_DIR"; exit 1; }

  # Prompt for sudo
  sudo -v
  
  # Clean old log
  rm -f "$LOGFILE"
  
  # Start daemon
  bash -c "
    source '$SDE_SETUP'
    cd '$SDE_DIR'
    nohup sudo -E ./run_switchd.sh -p '$PIPELINE' >> '$LOGFILE' 2>&1 &
  "
  
  sleep 2
  
  if pgrep -x bf_switchd >/dev/null 2>&1; then
    echo "Started. Logs: $LOGFILE"
  else
    echo "Failed to start. Last 30 lines:"
    tail -n 30 "$LOGFILE"
    exit 1
  fi
}

stop_cmd() {
  echo "Stopping bf_switchd..."
  
  if ! pgrep -x bf_switchd >/dev/null 2>&1; then
    echo "Not running"
    return 0
  fi
  
  sudo pkill -x bf_switchd
  sleep 1
  
  if pgrep -x bf_switchd >/dev/null 2>&1; then
    echo "Still running, force kill..."
    sudo pkill -9 -x bf_switchd
  fi
  
  echo "Stopped"
}

status_cmd() {
  if pgrep -x bf_switchd >/dev/null 2>&1; then
    echo "Running - PIDs: $(pgrep -x bf_switchd | xargs)"
  else
    echo "Not running"
  fi
}

logs_cmd() {
  [[ -f "$LOGFILE" ]] || { echo "No log file"; exit 1; }
  tail -n 50 -f "$LOGFILE"
}

case "$cmd" in
  start)  start_cmd ;;
  stop)   stop_cmd ;;
  status) status_cmd ;;
  logs)   logs_cmd ;;
  reload) stop_cmd; start_cmd ;;
  *)
    echo "Usage: $0 {start|stop|status|logs|reload}"
    exit 1
  ;;
esac