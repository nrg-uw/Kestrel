#!/bin/bash
# Deploys built binary to remote collector node (cn203)
scp build/postcard_collector user@collector_ip:/tmp/
ssh user@collector_ip "chmod +x /tmp/postcard_collector"