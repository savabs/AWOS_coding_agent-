#!/bin/bash
# Run `python -m <module> [args...]` inside "AWOS Helper.app" (so it gets the helper's
# Accessibility / Input Monitoring grants), wait for it, print its log, exit with its code.
#   scripts/macos_helper/run_helper.sh scaffold.agent.desktop.capture check
set -uo pipefail
APP="${AWOS_HELPER_APP:-$HOME/Applications/AWOS Helper.app}"
JOBS="$HOME/.awos/helper"
mkdir -p "$JOBS"
rm -f "$JOBS/last.exit" "$JOBS/last.log"
printf '%s ' "$@" > "$JOBS/job.args"
open -W -n "$APP"
cat "$JOBS/last.log" 2>/dev/null
exit "$(cat "$JOBS/last.exit" 2>/dev/null || echo 1)"
