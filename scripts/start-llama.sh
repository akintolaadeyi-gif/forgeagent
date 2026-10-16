#!/usr/bin/env bash
# Starts llama.cpp's OpenAI-compatible server bound to localhost.
#   ./scripts/start-llama.sh                               # Q4_K_M if present, 16k context, auto GPU/CPU split
#   ./scripts/start-llama.sh --context 32768 --gpu-layers 40
#   ./scripts/start-llama.sh --model /path/to/model.gguf
# Defaults: llama-server from FORGE_LLAMA_SERVER_PATH (environment or the repo .env) or your PATH;
# models from FORGE_MODELS_DIR (environment or .env) or ~/models.
set -eu
MODEL=""
CONTEXT=16384
GPU_LAYERS=-1        # -1 = let llama.cpp fit as many layers as memory allows
PORT=8080
KV=q8_0              # f16 | q8_0 | q4_0
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
# value of KEY in the repo's .env (the backend reads the same file); empty when absent
env_get() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -n 1 | tr -d '\r'; }
SERVER="${FORGE_LLAMA_SERVER_PATH:-$(env_get FORGE_LLAMA_SERVER_PATH)}"
[ -n "$SERVER" ] || SERVER="$(command -v llama-server || true)"
MODELS_DIR="${FORGE_MODELS_DIR:-$(env_get FORGE_MODELS_DIR)}"
[ -n "$MODELS_DIR" ] || MODELS_DIR="$HOME/models"
EXTRA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift ;;
    --context) CONTEXT="$2"; shift ;;
    --gpu-layers) GPU_LAYERS="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    --kv-cache) KV="$2"; shift ;;
    --server) SERVER="$2"; shift ;;
    --models-dir) MODELS_DIR="$2"; shift ;;
    --extra-args) EXTRA="$2"; shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$SERVER" ] || [ ! -f "$SERVER" ]; then
  echo "llama-server not found ('${SERVER:-not set}'). Install llama.cpp, or set FORGE_LLAMA_SERVER_PATH (see docs/INSTALL.md)." >&2
  exit 1
fi

if [ -z "$MODEL" ]; then
  # prefer a Q4_K_M file, then the smallest one; skip multimodal projector files
  best_key=""
  while IFS= read -r f; do
    size="$(wc -c <"$f" | tr -d ' ')"
    case "$f" in *Q4_K_M*|*q4_k_m*) pref=0 ;; *) pref=1 ;; esac
    key="$(printf '%d%020d' "$pref" "$size")"
    if [ -z "$best_key" ] || [ "$key" \< "$best_key" ]; then MODEL="$f"; best_key="$key"; fi
  done < <(find "$MODELS_DIR" -type f -name '*.gguf' ! -iname '*mmproj*' 2>/dev/null)
  [ -n "$MODEL" ] || { echo "No .gguf model found under $MODELS_DIR" >&2; exit 1; }
fi
echo "Model:   $MODEL"
echo "Context: $CONTEXT   KV cache: $KV   Port: $PORT (127.0.0.1 only)"

set -- -m "$MODEL" -c "$CONTEXT" --host 127.0.0.1 --port "$PORT" --jinja -fa on
if [ "$GPU_LAYERS" -ge 0 ]; then set -- "$@" -ngl "$GPU_LAYERS"; fi
if [ "$KV" != "f16" ]; then set -- "$@" -ctk "$KV" -ctv "$KV"; fi
# shellcheck disable=SC2086  # EXTRA is intentionally split into separate arguments
if [ -n "$EXTRA" ]; then set -- "$@" $EXTRA; fi
echo "$SERVER $*"
exec "$SERVER" "$@"
