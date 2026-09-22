#!/usr/bin/env bash
# demo_ci_rescue_sprint_live.sh — Live API proof for CI Rescue Sprint mission.
# Spec: docs/specs/ci_rescue_sprint_corpus_spec.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== CI Rescue Sprint LIVE (awos mission start --ci-rescue) ==="
echo "Watch for: [MISSION] Pre-planned task list: 18 tasks"
echo "           Session: rs_*"
echo "           WEDGE_ASSERT: SEMANTIC_PASS"
echo ""

python3 awos.py mission start --ci-rescue 2>&1 | tee /tmp/ci_rescue_sprint_live.log

echo ""
echo "=== Live log saved: /tmp/ci_rescue_sprint_live.log ==="
if grep -q "WEDGE_ASSERT: SEMANTIC_PASS" /tmp/ci_rescue_sprint_live.log; then
  echo "CI_RESCUE_LIVE: SEMANTIC_PASS"
else
  echo "CI_RESCUE_LIVE: assert markers not in log — check mission output"
  exit 1
fi
