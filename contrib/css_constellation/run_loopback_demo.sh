#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
CONFIG="${1:-configs/demo_three_sat.json}"
PIDS=()

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

start() {
  "$@" &
  PIDS+=("$!")
}

echo "Starting Front End and three ISL/RM instances..."
start python3 front_end.py --config "$CONFIG" --verbose
for scid in 101 102 103; do
  start python3 isl_rm.py --config "$CONFIG" --scid "$scid" --verbose
done

sleep 0.5

echo
echo "The routing fabric is running. In separate terminals, try:"
echo "  python3 demo_ci_sink.py --config $CONFIG --scid 103"
echo "  python3 constellation_cli.py --config $CONFIG send-tc --scid 103 --text ONLY_103 --spi 0x0004"
echo
echo "For TM, start:"
echo "  python3 constellation_cli.py --config $CONFIG listen-tm --scid 103"
echo "Then inject spacecraft-103 telemetry:"
echo "  python3 constellation_cli.py --config $CONFIG send-local-tm --scid 103 --text TM_FROM_103"
echo
echo "Press Ctrl-C to stop the routing fabric."
wait
