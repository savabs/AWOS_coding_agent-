#!/bin/bash
# awos_try_browser.sh — open a separate test browser with the AWOS Replay extension already loaded.
#
#     bash scripts/awos_try_browser.sh [url]      # default: the httpbin.org warm-up form
#
# Uses "Chrome for Testing" (Playwright's build: it accepts --load-extension, branded Chrome no longer
# does) with its own profile in ~/.awos/chrome-test, so your normal Chrome / Safari are never touched.
# The script only launches the window; everything inside it is done by you.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"
PROFILE="$HOME/.awos/chrome-test"
URL="${1:-https://httpbin.org/forms/post}"
APP=$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium-*/chrome-mac*/"Google Chrome for Testing.app" 2>/dev/null | sort -V | tail -1 || true)
if [ -z "$APP" ]; then
  echo "Chrome for Testing not found: run  python3 -m pip install playwright && python3 -m playwright install chromium"; exit 1
fi
mkdir -p "$PROFILE"
curl -s -m 2 http://127.0.0.1:8080/health | grep -q ok || echo "note: the local model is not running (bash scripts/awos_model.sh); replay still works, repair/verify need it"
open -na "$APP" --args --user-data-dir="$PROFILE" --load-extension="$EXT" --disable-extensions-except="$EXT" \
  --no-first-run --no-default-browser-check "$URL"
echo "opened a test browser (profile $PROFILE) with AWOS Replay loaded; pin it from the puzzle-piece icon"
