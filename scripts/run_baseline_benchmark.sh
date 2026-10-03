#!/usr/bin/env bash
# Re-run baseline benchmark suites and write fresh artifacts for comparison.
# Frozen reference: docs/benchmarks/baseline_v1_2026-06-25.json
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASELINE_JSON="docs/benchmarks/baseline_v1_2026-06-25.json"
OUT_LOG="/tmp/awos_baseline_run_$(date +%Y%m%d_%H%M%S).log"

echo "=== AWOS Baseline Benchmark Re-run ===" | tee "$OUT_LOG"
echo "Frozen reference: $BASELINE_JSON" | tee -a "$OUT_LOG"
echo "Git commit: $(git rev-parse --short HEAD)" | tee -a "$OUT_LOG"
echo "" | tee -a "$OUT_LOG"

# 1. Unit tests (regression gate)
echo "--- [1/3] Unit tests ---" | tee -a "$OUT_LOG"
if python3 -m pytest tests/test_benchmark_vs_raw_api.py tests/test_mission_plan_loader.py tests/test_cache_telemetry.py -q 2>&1 | tee -a "$OUT_LOG"; then
  echo "OK: core benchmark-related tests passed" | tee -a "$OUT_LOG"
else
  echo "WARN: some unit tests failed — fix before trusting comparison" | tee -a "$OUT_LOG"
fi
echo "" | tee -a "$OUT_LOG"

# 2. PEI benchmark (live — requires API keys)
echo "--- [2/3] PEI benchmark (live) ---" | tee -a "$OUT_LOG"
if [[ -z "${DEEPSEEK_API_KEY:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "SKIP: no API keys — run with .env loaded for live PEI" | tee -a "$OUT_LOG"
  python3 scripts/benchmark_vs_raw_api.py --dry-run 2>&1 | tee -a "$OUT_LOG"
else
  python3 scripts/benchmark_vs_raw_api.py 2>&1 | tee -a "$OUT_LOG"
  echo "" | tee -a "$OUT_LOG"
  echo "Latest JSON:" | tee -a "$OUT_LOG"
  ls -t .awos/benchmarks/benchmark_vs_raw_*.json 2>/dev/null | head -1 | tee -a "$OUT_LOG"
fi
echo "" | tee -a "$OUT_LOG"

# 3. Compare to frozen baseline
echo "--- [3/3] Compare to baseline v1 ---" | tee -a "$OUT_LOG"
if [[ -f "$BASELINE_JSON" ]]; then
  python3 scripts/compare_baseline.py 2>&1 | tee -a "$OUT_LOG" || true
else
  echo "Missing $BASELINE_JSON" | tee -a "$OUT_LOG"
fi

echo "" | tee -a "$OUT_LOG"
echo "Full log: $OUT_LOG" | tee -a "$OUT_LOG"
echo "" | tee -a "$OUT_LOG"
echo "Mission suites (manual — not auto-run):" | tee -a "$OUT_LOG"
echo "  python3 awos.py mission start --long" | tee -a "$OUT_LOG"
echo "  python3 awos.py mission start --clawcode" | tee -a "$OUT_LOG"
