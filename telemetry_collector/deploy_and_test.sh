#!/bin/bash
./build.sh
./deploy.sh

# Tests that the telemetry collector binary was deployed and runs correctly
ssh user@collector_ip "
  sudo timeout 2s /tmp/postcard_collector --interface eth2 --binary | \
  head -c 1000 | \
  hexdump -C | head -20
"