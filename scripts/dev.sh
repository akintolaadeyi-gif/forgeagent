#!/usr/bin/env bash
# Development mode: backend with auto-reload on :8000 and the Vite dev server (hot reload) on :5173.
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/backend/.venv/bin/python"
[ -x "$PY" ] || { echo "Backend not installed. Run ./scripts/install.sh first." >&2; exit 1; }
cd "$ROOT/backend"
"$PY" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
BACKEND=$!
trap 'kill "$BACKEND" 2>/dev/null || true' EXIT
cd "$ROOT/frontend"
npm run dev
