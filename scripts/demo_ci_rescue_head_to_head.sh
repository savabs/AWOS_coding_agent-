#!/usr/bin/env bash
# demo_ci_rescue_head_to_head.sh — Compare AWOS vs raw API on CI Rescue Sprint.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "=== CI Rescue Sprint: AWOS vs Raw API ==="
python3 scripts/benchmark_ci_rescue_sprint.py "$@"
echo ""
echo "Read the summary: docs/product/ci_rescue_head_to_head.md"
