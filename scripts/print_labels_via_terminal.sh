#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 1 ]]; then
  echo "Usage: $0 LABEL [LABEL ...]" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUOTED_LABELS=""

for label in "$@"; do
  QUOTED_LABELS+=" $(printf '%q' "$label")"
done

COMMAND="cd $(printf '%q' "$ROOT_DIR") && ./scripts/print_labels.sh$QUOTED_LABELS"

osascript <<APPLESCRIPT
tell application "Terminal"
  activate
  do script "$COMMAND"
end tell
APPLESCRIPT
