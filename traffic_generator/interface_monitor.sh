#!/bin/bash
# ==============================================================================
# Script Name: interface_monitor.sh
# Purpose:     Monitor TX/RX throughput and packet rate on a network interface
# Author:      Niloy Saha
# Date:        2025-08-20
# ==============================================================================
#
# Description:
# This script monitors a specified network interface and displays:
#   - TX/RX rate in Mbps and Gbps
#   - Packets per second (PPS) for TX and RX
#
# Usage:
#   ./interface-monitor.sh <interface> [interval]
#
# Example:
#   ./interface-monitor.sh eth2 1
#
# Notes:
#   - Default interval is 1 second if not specified
#   - Requires access to /sys/class/net/<interface>/statistics
# ==============================================================================

INTERFACE="$1"
INTERVAL="${2:-1}"

if [[ -z "$INTERFACE" ]]; then
    echo -e "\nUsage: $0 <network-interface> [interval-seconds]"
    echo -e "Example: $0 eth2 1"
    echo -e "\nShows TX/RX rate in Mbps, Gbps, and packets per second (PPS)\n"
    exit 1
fi

# Check if interface exists
if [[ ! -d "/sys/class/net/$INTERFACE" ]]; then
    echo "Error: Interface '$INTERFACE' not found."
    exit 2
fi

echo "Monitoring interface: $INTERFACE (interval: ${INTERVAL}s)"
echo "Press Ctrl+C to stop."

while true; do
    R1_BYTES=$(< /sys/class/net/$INTERFACE/statistics/rx_bytes)
    T1_BYTES=$(< /sys/class/net/$INTERFACE/statistics/tx_bytes)
    R1_PKTS=$(< /sys/class/net/$INTERFACE/statistics/rx_packets)
    T1_PKTS=$(< /sys/class/net/$INTERFACE/statistics/tx_packets)

    sleep "$INTERVAL"

    R2_BYTES=$(< /sys/class/net/$INTERFACE/statistics/rx_bytes)
    T2_BYTES=$(< /sys/class/net/$INTERFACE/statistics/tx_bytes)
    R2_PKTS=$(< /sys/class/net/$INTERFACE/statistics/rx_packets)
    T2_PKTS=$(< /sys/class/net/$INTERFACE/statistics/tx_packets)

    RX_BYTES=$((R2_BYTES - R1_BYTES))
    TX_BYTES=$((T2_BYTES - T1_BYTES))
    RX_PKTS=$((R2_PKTS - R1_PKTS))
    TX_PKTS=$((T2_PKTS - T1_PKTS))

    RX_Mbps=$(echo "scale=2; $RX_BYTES * 8 / 1000000 / $INTERVAL" | bc)
    TX_Mbps=$(echo "scale=2; $TX_BYTES * 8 / 1000000 / $INTERVAL" | bc)
    RX_Gbps=$(echo "scale=3; $RX_BYTES * 8 / 1000000000 / $INTERVAL" | bc)
    TX_Gbps=$(echo "scale=3; $TX_BYTES * 8 / 1000000000 / $INTERVAL" | bc)

    TIMESTAMP=$(date '+%F %T')
    printf "%s | TX: %5s Mbps (%3s Gbps), %3d PPS\tRX: %5s Mbps (%3s Gbps), %3d PPS\n" \
  "$TIMESTAMP" "$TX_Mbps" "$TX_Gbps" "$TX_PKTS" "$RX_Mbps" "$RX_Gbps" "$RX_PKTS"

done
