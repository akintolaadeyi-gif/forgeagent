#!/usr/bin/env bash
# ForgeAgent installer for macOS and Linux.
#   ./scripts/install.sh                   # backend venv + frontend build + Playwright Chromium
#   ./scripts/install.sh --skip-browser    # skip the Playwright browser download
#   ./scripts/install.sh --skip-frontend   # backend only (the API works, the UI is not built)
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_BROWSER=0
SKIP_FRONTEND=0
for a in "$@"; do
  case "$a" in
    --skip-browser) SKIP_BROWSER=1 ;;
    --skip-frontend) SKIP_FRONTEND=1 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
step() { printf '\n==> %s\n' "$1"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "$1 not found. $2" >&2; exit 1; }; }

step "Checking prerequisites"
PY=""
for c in python3.13 python3.12 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.12+ not found. Install it from https://www.python.org/downloads/" >&2; exit 1; }
need git "Install git (macOS: xcode-select --install)."
if [ "$SKIP_FRONTEND" = 0 ]; then
  need node "Install Node.js 20+ from https://nodejs.org/ (or re-run with --skip-frontend)."
fi
echo "using $("$PY" --version 2>&1), $(git --version)"

step "Backend: virtualenv + dependencies"
cd "$ROOT/backend"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt
if [ "$SKIP_BROWSER" = 0 ]; then
  step "Playwright Chromium (browser automation)"
  .venv/bin/python -m playwright install chromium \
    || echo "WARNING: Chromium download failed. The agent's browser tools will not work (retry later or use --skip-browser)." >&2
fi

if [ "$SKIP_FRONTEND" = 0 ]; then
  step "Frontend: install + production build"
  cd "$ROOT/frontend"
  npm install
  npm run build
fi

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Created .env from .env.example"
fi

step "Done"
echo "llama.cpp and a model are not installed by this script. You need a llama-server binary on your PATH"
echo "(or FORGE_LLAMA_SERVER_PATH in .env) and a .gguf model under ~/models (or FORGE_MODELS_DIR)."
echo "Start everything with:  ./scripts/start.sh --with-llama"
