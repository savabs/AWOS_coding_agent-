#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Code Evolution Lab — Live Benchmark ==="
echo ""
echo "This will:"
echo "  1. Run raw API (one-shot per commit)"
echo "  2. Run AWOS harness (verify loop)"
echo "  3. Compare zero-regression rate"
echo ""
echo "Expected: ~\$0.10 API cost, 5-10 minutes"
echo ""
read -p "Press Enter to continue or Ctrl+C to cancel..."

python3 scripts/benchmark_code_evolution_lab.py

echo ""
echo "Report: docs/product/code_evolution_head_to_head.md"
echo "JSON:   .awos/benchmarks/code_evolution_head_to_head_*.json"
