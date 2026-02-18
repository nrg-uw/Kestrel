#!/usr/bin/env bash
set -euo pipefail

### --- Config (override via env) ---
BIN="${BIN:-/tmp/traffic_generator}"
CFG="${CFG:-/tmp/config.json}"
UE_COUNT="${UE_COUNT:-100}"
DURATION="${DURATION:-600}"        # seconds; 0 = run forever (if supported)
EXTRA_ARGS="${EXTRA_ARGS:-}"       # e.g. "--rate 1g"
SUDO="${SUDO:-sudo}"               # set to "" if sudo not needed
MAX_LOGS="${MAX_LOGS:-10}"         # keep last N logs

# Files / directories
NAME="trafficd"
RUNDIR="/tmp/${NAME}.d"            # state directory (store logs + pid)
LOGDIR="${RUNDIR}/logs"
PIDFILE="${RUNDIR}/${NAME}.pid"

mkdir -p "${RUNDIR}" "${LOGDIR}"

is_running() {
  [[ -f "${PIDFILE}" ]] || return 1
  local pid; pid="$(cat "${PIDFILE}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

prune_logs() {
  shopt -s nullglob
  local files=( "${LOGDIR}/${NAME}_".*.log )
  local count="${#files[@]}"
  if (( count > MAX_LOGS )); then
    local to_delete=$(( count - MAX_LOGS ))
    printf '%s\0' "${files[@]}" \
      | xargs -0 ls -1tr \
      | head -n "${to_delete}" \
      | xargs -r rm -f --
  fi
}

start() {
  if is_running; then
    echo "[*] ${NAME} already running (pid $(cat "${PIDFILE}"))"
    exit 0
  fi
  [[ -x "${BIN}" ]] || { echo "[!] Missing or non-executable BIN: ${BIN}" >&2; exit 1; }
  [[ -f "${CFG}" ]] || { echo "[!] Missing CFG: ${CFG}" >&2; exit 1; }

  local ts; ts="$(date +'%Y%m%d_%H%M%S')"
  local logfile="${LOGDIR}/${NAME}_${ts}.log"
  ln -sfn "${logfile}" "${LOGDIR}/latest.log"

  local cmd="${SUDO} ${BIN} --config ${CFG} --ue-count ${UE_COUNT} --duration ${DURATION} ${EXTRA_ARGS}"
  echo "[*] Starting ${NAME} → ${logfile}"
  nohup bash -lc "exec ${cmd}" >> "${logfile}" 2>&1 &
  echo $! > "${PIDFILE}"
  sleep 1

  if is_running; then
    echo "[✓] Started (pid $(cat "${PIDFILE}"))"
    prune_logs
  else
    echo "[x] Failed to start. See ${logfile}" >&2
    rm -f "${PIDFILE}"
    exit 1
  fi
}

stop() {
  if ! is_running; then
    echo "[*] ${NAME} not running"
    rm -f "${PIDFILE}"
    exit 0
  fi
  local pid; pid="$(cat "${PIDFILE}")"
  echo "[*] Stopping (pid ${pid}) ..."
  kill "${pid}" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "[✓] Stopped"
      rm -f "${PIDFILE}"
      return 0
    fi
    sleep 0.5
  done
  echo "[!] Force killing ${pid}"
  kill -9 "${pid}" 2>/dev/null || true
  rm -f "${PIDFILE}"
  echo "[✓] Stopped (SIGKILL)"
}

status() {
  if is_running; then
    local pid; pid="$(cat "${PIDFILE}")"
    echo "[✓] ${NAME} running (pid ${pid})"
    ps -o pid,ppid,cmd -p "${pid}"
  else
    echo "[x] ${NAME} not running"
    [[ -f "${PIDFILE}" ]] && echo "    (stale PID file: ${PIDFILE})"
    return 1
  fi
}

logs() {
  local logfile="${LOGDIR}/latest.log"
  if [[ ! -f "${logfile}" ]]; then
    echo "[!] No logs yet in ${LOGDIR}"
    ls -1t "${LOGDIR}" 2>/dev/null || true
    exit 1
  fi
  echo "[*] tail -f ${logfile} (Ctrl-C to stop)"
  tail -n 200 -f "${logfile}"
}

restart() { stop || true; start; }

usage() {
  cat <<EOF
Usage: $0 {start|stop|status|restart|logs}
Env:
  BIN=${BIN}
  CFG=${CFG}
  UE_COUNT=${UE_COUNT}
  DURATION=${DURATION}
  EXTRA_ARGS=${EXTRA_ARGS}
  SUDO=${SUDO}
  MAX_LOGS=${MAX_LOGS}
State:
  RUNDIR=${RUNDIR}
  PIDFILE=${PIDFILE}
  LOGDIR=${LOGDIR}
EOF
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  status)  status ;;
  restart) restart ;;
  logs)    logs ;;
  *)       usage; exit 1 ;;
esac
