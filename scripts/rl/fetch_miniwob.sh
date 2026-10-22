#!/usr/bin/env bash
# Fetch the MiniWoB++ static HTML tasks (MIT, Farama-Foundation/miniwob-plusplus) at a
# PINNED commit into a cache dir outside the repo. Only miniwob/html/** is kept; nothing
# from the download is ever executed here -- scaffold/rl/miniwob serves it as static files
# to a sandboxed headless browser.
#
#   scripts/rl/fetch_miniwob.sh            -> prints the html root on the last line
#   AWOS_RL_CACHE=/tmp/x scripts/rl/fetch_miniwob.sh
#
# Layout: $AWOS_RL_CACHE/miniwob/<sha>/html/{core,common,miniwob,flight}/...
# MiniWobSource finds it via $AWOS_MINIWOB_HTML, else this default path.
set -euo pipefail

MINIWOB_SHA="${MINIWOB_SHA:-a7658beb68e1d3572329e06734c5822bd284ddc8}"   # tag v1.1 (2026-08-13)
CACHE="${AWOS_RL_CACHE:-$HOME/.cache/awos-rl}"
DEST="$CACHE/miniwob/$MINIWOB_SHA"

if [ -f "$DEST/html/core/core.js" ] && [ -d "$DEST/html/miniwob" ]; then
  echo "$DEST/html"
  exit 0
fi

# Download into its own fresh, empty directory; never cd into it or run anything from it.
DL="$(mktemp -d "${TMPDIR:-/tmp}/miniwob-dl.XXXXXX")"
trap 'rm -rf "$DL"' EXIT
URL="https://codeload.github.com/Farama-Foundation/miniwob-plusplus/tar.gz/$MINIWOB_SHA"
curl -fsSL --retry 5 -o "$DL/src.tar.gz" "$URL"
mkdir -p "$DL/x"
tar -xzf "$DL/src.tar.gz" -C "$DL/x" --strip-components=1
test -f "$DL/x/miniwob/html/core/core.js" || { echo "unexpected archive layout" >&2; exit 1; }

mkdir -p "$CACHE/miniwob"
rm -rf "$DEST.tmp"
mkdir -p "$DEST.tmp"
mv "$DL/x/miniwob/html" "$DEST.tmp/html"
cp "$DL/x/LICENSE" "$DEST.tmp/LICENSE" 2>/dev/null || true
echo "$MINIWOB_SHA" > "$DEST.tmp/SHA"
rm -rf "$DEST"
mv "$DEST.tmp" "$DEST"
echo "$DEST/html"
