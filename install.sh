#!/usr/bin/env bash
# Installs the usage-cap scripts into ~/.claude/scripts.
# Does NOT touch ~/.claude/settings.json or ~/.claude/CLAUDE.md -- see README.md
# for the hook/config snippets to add yourself.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts"
DEST_DIR="$HOME/.claude/scripts"

mkdir -p "$DEST_DIR"
cp "$SRC_DIR"/usage_cli.py "$SRC_DIR"/usage-check.sh "$SRC_DIR"/usage-cap.sh "$SRC_DIR"/usage-guard.sh "$DEST_DIR"/
chmod +x "$DEST_DIR"/usage_cli.py "$DEST_DIR"/usage-check.sh "$DEST_DIR"/usage-cap.sh "$DEST_DIR"/usage-guard.sh

echo "Installed to $DEST_DIR"
echo
echo "Next steps (see README.md for details):"
echo "  1. Add the PreToolUse hook to ~/.claude/settings.json"
echo "  2. (Optional) Add the natural-language convention to ~/.claude/CLAUDE.md"
echo "  3. Try it: $DEST_DIR/usage-cap.sh status"
