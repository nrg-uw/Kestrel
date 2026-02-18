#!/bin/bash
set -euo pipefail

# Remote targets
JUMP_HOST="user@jump_host"
TOFINO_HOST="user@tofino_ip"
REMOTE_TMP="/tmp/p4src"
LOCAL_P4SRC_DIR="./p4src"
P4_BUILD_SCRIPT="./p4_build.sh"

echo "Copying P4 sources to Tofino via $JUMP_HOST..."
scp -q -o ProxyJump=$JUMP_HOST -r "$LOCAL_P4SRC_DIR" "${TOFINO_HOST}:${REMOTE_TMP}"

echo "Running P4 build on remote Tofino..."
ssh -q -t -A -J "$JUMP_HOST" "$TOFINO_HOST" \
  'bash -lc "source /home/user/set_sde.bash && sudo -E '"$(printf %q "$P4_BUILD_SCRIPT")"' '"$(printf %q "${REMOTE_TMP}/kestrel.p4")"'"'

echo "Done."
