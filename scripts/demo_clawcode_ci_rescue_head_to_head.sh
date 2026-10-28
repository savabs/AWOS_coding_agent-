#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "=== Clawcode: AWOS vs Raw API (12 fixes) ==="
python3 scripts/benchmark_clawcode_ci_rescue.py "$@"
echo ""
echo "Read: docs/product/clawcode_ci_rescue_head_to_head.md"
