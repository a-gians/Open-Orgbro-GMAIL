#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-.venv/bin/python}"
OUT_DIR="${OUT_DIR:-labels/runs/$(date +%Y%m%d-%H%M%S)}"
HEIGHT_ROWS="${HEIGHT_ROWS:-180}"
FONT_SIZE="${FONT_SIZE:-96}"
FEED_STEPS="${FEED_STEPS:-50}"
PREVIEW_ONLY="${PREVIEW_ONLY:-0}"

if [[ "$#" -lt 1 ]]; then
  echo "Usage: $0 LABEL [LABEL ...]" >&2
  exit 2
fi

mkdir -p "$OUT_DIR/previews"

for label in "$@"; do
  safe_label="$(printf '%s' "$label" | tr -cs '[:alnum:]_.-' '_')"
  echo "Printing $label..."
  args=(
    scripts/q2_print_text.py "$label"
    --height-rows "$HEIGHT_ROWS" \
    --font-size "$FONT_SIZE" \
    --feed-steps "$FEED_STEPS" \
    --preview "$OUT_DIR/previews/$safe_label.png" \
    --out "$OUT_DIR/$safe_label.json"
  )
  if [[ "$PREVIEW_ONLY" == "1" ]]; then
    args+=(--preview-only)
  fi
  "$PYTHON" "${args[@]}"
  sleep 1
done

echo "Done. Logs and previews: $OUT_DIR"
