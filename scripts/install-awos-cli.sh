#!/usr/bin/env bash
# Install `awos` onto PATH via ~/.local/bin (no sudo).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"
ln -sf "$ROOT/scripts/awos" "$BIN/awos"
chmod +x "$ROOT/scripts/awos"
echo "Installed: $BIN/awos -> $ROOT/scripts/awos"
echo ""
echo "Try:"
echo "  awos guide"
echo "  awos gauntlet list"
if ! echo ":$PATH:" | grep -q ":${BIN}:"; then
  echo ""
  echo "Add to ~/.bashrc (once):"
  echo "  export PATH=\"\${HOME}/.local/bin:\$PATH\""
fi
