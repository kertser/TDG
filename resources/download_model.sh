#!/usr/bin/env bash
# Download a GGUF model for the local LLM sidecar.
#
# Usage:
#   ./scripts/download_model.sh                              # default model
#   ./scripts/download_model.sh <huggingface_url> <filename>  # custom model
#
# Recommended models (pick ONE — larger = better quality, slower):
#
#  Model                          Size    Speed   Quality  Context
#  ─────────────────────────────  ──────  ──────  ───────  ───────
#  Llama-3.2-1B-Instruct-Q4_K_S  ~700MB  fast    basic    8K
#  Llama-3.2-3B-Instruct-Q4_K_M  ~1.8GB  medium  good     8K
#  Phi-3.5-mini-Q4_K_M           ~2.2GB  medium  good     128K
#  Mistral-7B-Instruct-Q4_K_M    ~4.4GB  slow    great    32K
#
# For structured JSON output (scoring, profiles), 3B+ is recommended.

set -euo pipefail

MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$MODELS_DIR"

DEFAULT_URL="https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
DEFAULT_NAME="model.gguf"

URL="${1:-$DEFAULT_URL}"
FILENAME="${2:-$DEFAULT_NAME}"
DEST="$MODELS_DIR/$FILENAME"

if [ -f "$DEST" ]; then
    echo "✓ Model already exists: $DEST ($(du -h "$DEST" | cut -f1))"
    echo "  Delete it first if you want to re-download."
    exit 0
fi

echo "Downloading model to $DEST ..."
echo "  URL: $URL"
echo ""

if command -v wget &>/dev/null; then
    wget -O "$DEST" "$URL"
elif command -v curl &>/dev/null; then
    curl -L -o "$DEST" "$URL"
else
    echo "Error: neither wget nor curl found. Install one and retry."
    exit 1
fi

echo ""
echo "✓ Model downloaded: $DEST ($(du -h "$DEST" | cut -f1))"
echo "  Start the LLM sidecar with: docker compose up llm"

