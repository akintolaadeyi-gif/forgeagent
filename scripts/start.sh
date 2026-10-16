#!/usr/bin/env bash
# Starts ForgeAgent (backend API + built UI) and optionally the llama.cpp server.
#   ./scripts/start.sh                     # backend only (attach to an already running llama-server)
#   ./scripts/start.sh --with-llama        # also launch llama-server (16k context)
#   ./scripts/start.sh --with-llama --context 32768 --gpu-layers 40
#   other options: --port N   --no-browser
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_LLAMA=0
CONTEXT=16384
GPU_LAYERS=-1
PORT=8000
NO_BROWSER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-llama) WITH_LLAMA=1 ;;
    --context) CONTEXT="$2"; shift ;;
    --gpu-layers) GPU_LAYERS="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    --no-browser) NO_BROWSER=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PY="$ROOT/backend/.venv/bin/python"
[ -x "$PY" ] || { echo "Backend not installed. Run ./scripts/install.sh first." >&2; exit 1; }
[ -f "$ROOT/frontend/dist/index.html" ] || echo "WARNING: frontend is not built (run install.sh). The API will still work." >&2
mkdir -p "$ROOT/data/logs"

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
if port_open "$PORT"; then
  echo "Port $PORT is already in use. Run ./scripts/stop.sh or choose --port." >&2
  exit 1
fi

if [ "$WITH_LLAMA" = 1 ]; then
  if port_open 8080; then
    echo "llama-server already listening on 8080 - attaching."
  else
    echo "Starting llama-server (the first model load can take a minute)..."
    nohup "$ROOT/scripts/start-llama.sh" --context "$CONTEXT" --gpu-layers "$GPU_LAYERS" \
      >"$ROOT/data/logs/llama.out.log" 2>"$ROOT/data/logs/llama.err.log" </dev/null &
    echo $! >"$ROOT/data/llama.pid"
  fi
fi

echo "Starting ForgeAgent backend on http://127.0.0.1:$PORT"
cd "$ROOT/backend"
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
  >"$ROOT/data/logs/backend.out.log" 2>"$ROOT/data/logs/backend.err.log" </dev/null &
echo $! >"$ROOT/data/backend.pid"
i=0
while [ "$i" -lt 40 ]; do
  port_open "$PORT" && break
  sleep 0.5
  i=$((i + 1))
done
port_open "$PORT" || { echo "Backend did not start. See data/logs/backend.err.log" >&2; exit 1; }
echo "ForgeAgent is running:  http://127.0.0.1:$PORT"

if [ -f "$ROOT/data/llama.pid" ] && ! kill -0 "$(cat "$ROOT/data/llama.pid")" 2>/dev/null; then
  echo "WARNING: llama-server exited right after starting. See data/logs/llama.err.log" >&2
fi

if [ "$NO_BROWSER" = 0 ]; then
  if command -v open >/dev/null 2>&1; then
    open "http://127.0.0.1:$PORT" || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true
  fi
fi
