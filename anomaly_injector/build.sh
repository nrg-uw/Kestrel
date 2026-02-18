#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[*] Building Go injectors…"
mkdir -p build

# Initialize module if missing
if [[ ! -f go.mod ]]; then
  echo "  - init go.mod"
  go mod init github.com/uw-kestrel/anomaly_injector
fi

# Fetch deps (pin gopacket; x/sys latest is fine)
echo "  - fetching deps"
go get github.com/google/gopacket@v1.1.19
go get golang.org/x/sys@latest
go mod tidy

# Build all inject_* programs present
status=0
for f in inject_*.go; do
  [[ -e "$f" ]] || continue
  out="build/${f%.go}"
  echo "  - $f → $out"
  if ! go build -o "$out" "$f"; then
    status=1
  fi
done

if [[ $status -ne 0 ]]; then
  echo "[x] Build failed"
  exit 1
fi
echo "[✓] Done."
