#!/usr/bin/env bash
set -euo pipefail

# Deploy built injectors + config to HPC3
# Usage:
#   ./deploy.sh            # deploy all build/inject_*
#   ./deploy.sh inject_microburst inject_policy_abuse

REMOTE="${REMOTE}"
REMOTE_DIR="${REMOTE_DIR:-/tmp}"
REMOTE_CONFIG="${REMOTE_CONFIG:-/tmp/traffic_injector_config.json}"

cd "$(dirname "$0")"

# Ensure built
if [[ ! -d build ]] || ! ls build/inject_* >/dev/null 2>&1; then
  echo "[*] Building…"
  ./build.sh
fi

# Pick which binaries to send
if (( $# )); then
  bins=()
  for name in "$@"; do
    path="build/${name#build/}"
    [[ -x "$path" ]] && bins+=("$path") || echo "[!] Skipping: $path not found" >&2
  done
else
  mapfile -t bins < <(ls -1 build/inject_* 2>/dev/null || true)
fi

(( ${#bins[@]} )) || { echo "[x] No binaries to deploy"; exit 1; }

echo "[*] Copying injectors to ${REMOTE}:${REMOTE_DIR}/ …"
for b in "${bins[@]}"; do
  base="$(basename "$b")"
  scp -q "$b" "${REMOTE}:${REMOTE_DIR}/${base}"
  ssh -q "$REMOTE" "chmod +x '${REMOTE_DIR}/${base}'"
  echo "    ${REMOTE_DIR}/${base}"
done

# Ship config.json if present
if [[ -f config.json ]]; then
  echo "[*] Copying config.json → ${REMOTE_CONFIG}"
  scp config.json "${REMOTE}:${REMOTE_CONFIG}"
fi

echo "[✓] Deploy complete. Examples:"
echo "  ssh -q ${REMOTE} 'sudo ${REMOTE_DIR}/inject_microburst --qfi 7 --n 12 --duration-ms 750 --iface enp2s0f0'"
echo "  ssh -q ${REMOTE} 'sudo ${REMOTE_DIR}/inject_policy_abuse --victim-qfis 3,5 --count 2 --duration 15 --iface enp2s0f0'"
