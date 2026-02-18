#!/usr/bin/env bash
set -euo pipefail

# Config
JUMP_HOST="user@jump_host"
TOFINO_HOST="user@tofino_ip"
LOCAL_SCRIPT="./run_switchd.sh"
REMOTE_SCRIPT="\$HOME/.local/bin/run_switchd.sh"

cmd="${1:-}"

deploy() {
  echo "Deploying script..."
  ssh -q -J "$JUMP_HOST" "$TOFINO_HOST" 'mkdir -p ~/.local/bin'
  scp -q -J "$JUMP_HOST" "$LOCAL_SCRIPT" "$TOFINO_HOST:~/.local/bin/run_switchd.sh"
  ssh -q -J "$JUMP_HOST" "$TOFINO_HOST" 'chmod +x ~/.local/bin/run_switchd.sh'
}

run_remote() {
  ssh -q -t -J "$JUMP_HOST" "$TOFINO_HOST" "$REMOTE_SCRIPT $*"
}

case "$cmd" in
  start|stop|status|logs|reload)
    deploy
    run_remote "$cmd"
    ;;
  "")
    echo "Usage: $0 {start|stop|status|logs|reload}"
    exit 1
    ;;
  *)
    echo "Unknown command: $cmd"
    echo "Usage: $0 {start|stop|status|logs|reload}"
    exit 1
    ;;
esac