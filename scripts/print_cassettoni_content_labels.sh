#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

FONT_SIZE="${FONT_SIZE:-46}" \
HEIGHT_ROWS="${HEIGHT_ROWS:-140}" \
OUT_DIR="${OUT_DIR:-labels/cassettoni-content-runs/$(date +%Y%m%d-%H%M%S)}" \
  ./scripts/print_labels.sh \
    "C1 Vestiti" \
    "C2 Vestiti" \
    "C3 Device & Gadget" \
    "C4 Cavi & Alimentazione" \
    "C5 Storage & Dati" \
    "C6 Audio MIDI Luci" \
    "C7 VR & Gadget" \
    "C8 Bogota" \
    "C9 Libero" \
    "C10 Ferramenta Piccola"
