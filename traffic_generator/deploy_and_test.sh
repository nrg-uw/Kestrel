#!/bin/bash
./build.sh
./deploy.sh

# Tests that the traffic generator binary was deployed and runs correctly
ssh user@generator_host "
  sudo timeout 30s /tmp/traffic_generator --config /tmp/config.json --duration 30 --ue-count 100 
"