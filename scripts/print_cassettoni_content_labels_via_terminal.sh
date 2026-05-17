#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMAND="cd $(printf '%q' "$ROOT_DIR") && ./scripts/print_cassettoni_content_labels.sh"

osascript <<APPLESCRIPT
tell application "Terminal"
  activate
  do script "$COMMAND"
end tell
APPLESCRIPT
