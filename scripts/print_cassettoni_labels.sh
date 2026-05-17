#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${OUT_DIR:-labels/cassettoni-runs/$(date +%Y%m%d-%H%M%S)}" \
  ./scripts/print_labels.sh C1 C2 C3 C4 C5 C6 C7 C8 C9 C10
