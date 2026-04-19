#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-kestrel}"
OUTPUT_DIR="${OUTPUT_DIR:-$(pwd)/output}"

mkdir -p "$OUTPUT_DIR"

echo "[1/2] Running detection pipeline in Docker..."
docker run --rm -v "$OUTPUT_DIR:/kestrel/output" "$IMAGE_NAME" "$@"

echo "[2/2] Running verification tests in Docker..."
docker run --rm -v "$OUTPUT_DIR:/kestrel/output" \
  --entrypoint python "$IMAGE_NAME" -m pytest test_kestrel.py -v
