#!/usr/bin/env bash
# Stops the ForgeAgent backend and the llama-server that start.sh launched. When the backend shuts down
# cleanly it also stops the dev servers and llama-server it started itself.
#   ./scripts/stop.sh
#   ./scripts/stop.sh --force-llama     # additionally kill every llama-server process you own
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# stop_pid <pidfile> <expected command substring>: only signals the process if it still looks like ours
stop_pid() {
  [ -f "$1" ] || return 0
  pid="$(cat "$1")"
  rm -f "$1"
  kill -0 "$pid" 2>/dev/null || return 0
  ps -p "$pid" -o command= 2>/dev/null | grep -q "$2" || return 0
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.5
  done
  kill -9 "$pid" 2>/dev/null || true
}

stop_pid "$ROOT/data/backend.pid" "uvicorn"
pkill -f "uvicorn app.main:app" 2>/dev/null || true
stop_pid "$ROOT/data/llama.pid" "llama"
if [ "${1:-}" = "--force-llama" ]; then pkill -x llama-server 2>/dev/null || true; fi
echo "ForgeAgent stopped."
