#!/usr/bin/env bash
# Verify Code Evolution Lab fixture states (no API).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/tests/fixtures/wedge_v1/code_evolution"

echo "=== Code Evolution Lab — fixture smoke ==="

run_stage() {
  local name="$1" expect="$2"
  echo ""
  echo "[$name]"
  cd "$FIX/$name"
  out=$(python3 -m pytest tests/ -q --tb=no 2>&1 || true)
  echo "$out" | tail -1
  echo "$out" | tail -1 | grep -q "$expect" || {
    echo "FAIL: expected '$expect'" >&2
    exit 1
  }
}

run_stage "commit_0_baseline" "12 passed"
run_stage "commit_1_tiers" "12 passed"
run_stage "commit_1_tiers" "3 failed"
run_stage "commit_2_refactor" "15 passed"
run_stage "commit_2_refactor" "2 failed"
run_stage "commit_3_bulk" "16 passed"
run_stage "commit_3_bulk" "4 failed"
run_stage "commit_4_integration" "17 passed"
run_stage "commit_4_integration" "6 failed"

echo ""
echo "OK — all 5 commit stages built."
echo "Next: benchmark script + live proof."
