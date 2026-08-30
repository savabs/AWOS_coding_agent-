#!/usr/bin/env bash
# Run AWOS with highlighted live output — watch planning, tasks, verify, success/fail.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GOAL="${1:-}"
if [[ -z "$GOAL" ]]; then
  echo "Usage: $0 \"<goal text>\""
  exit 1
fi

CYAN='\033[36m'
YELLOW='\033[33m'
GREEN='\033[32m'
RED='\033[31m'
MAGENTA='\033[35m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

echo ""
echo "${BOLD}╔══════════════════════════════════════════════════════════════════════╗${RESET}"
echo "${BOLD}║  AWOS VISIBLE RUN — watch highlighted stages below                   ║${RESET}"
echo "${BOLD}╚══════════════════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo "${DIM}Goal:${RESET} ${GOAL:0:72}$([[ ${#GOAL} -gt 72 ]] && echo ...)"
echo ""
echo "${DIM}Legend:${RESET} ${CYAN}PLAN${RESET} · ${YELLOW}TASK${RESET} · ${MAGENTA}WORKER${RESET} · ${GREEN}PASS${RESET} · ${RED}FAIL${RESET}"
echo "${DIM}────────────────────────────────────────────────────────────────────────${RESET}"
echo ""

export PYTHONUNBUFFERED=1

python3 awos.py worker start --auto-approve "$GOAL" 2>&1 | while IFS= read -r line; do
  case "$line" in
    *"[PLANNER]"*|*"[PLAN REVIEW]"*)
      echo "${CYAN}▶ PLAN${RESET}  $line"
      ;;
    *"[TASK "*)
      echo "${YELLOW}▶ TASK${RESET}  $line"
      ;;
    *"Task "*"/"*)
      echo "${YELLOW}▶ TASK${RESET}  $line"
      ;;
    *"[WORKER]"*|*"generating patch"*|*"Worker attempt"*)
      echo "${MAGENTA}▶ WORK${RESET}  $line"
      ;;
    *"VERIFIED:"*|*"verification passed"*|*"task complete"*)
      echo "${GREEN}✓ PASS${RESET}  $line"
      ;;
    *"VERIFICATION FAILED"*|*"verification failed"*|*"task failed"*|*"ERROR:"*)
      echo "${RED}✗ FAIL${RESET}  $line"
      ;;
    *"[BREAKER]"*|*"Stagnation"*)
      echo "${RED}⏸ PAUSE${RESET} $line"
      ;;
    *"EXECUTION SUMMARY"*|*"Tasks Completed:"*|*"Success:"*)
      echo "${BOLD}$line${RESET}"
      ;;
    *"╭─"*|*"╰─"*|*"│"*)
      echo "$line"
      ;;
    *linucb*|*tensorflow*|*FutureWarning*|*UserWarning*|*ConvergenceWarning*|*critic*|*Unable to register*)
      : # skip noisy init warnings
      ;;
    *)
      echo "$line"
      ;;
  esac
done

EXIT=${PIPESTATUS[0]}
echo ""
echo "${DIM}────────────────────────────────────────────────────────────────────────${RESET}"
if [[ $EXIT -eq 0 ]]; then
  echo "${GREEN}${BOLD}Run finished (exit 0)${RESET}"
else
  echo "${RED}${BOLD}Run finished (exit $EXIT)${RESET}"
fi
exit $EXIT
