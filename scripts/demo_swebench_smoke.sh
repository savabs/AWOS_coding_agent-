#!/usr/bin/env bash
# Live proof: SWE-bench Lite subset harness (dry-run — no API, no Docker)

set -euo pipefail

cd "$(dirname "$0")/.."

echo "════════════════════════════════════════════════════════════════"
echo "SWE-BENCH LITE SUBSET — Live Proof (dry-run)"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Markers:"
echo "  [SWEBENCH] dry-run OK"
echo "  10 instance(s)"
echo "  passed (unit tests)"
echo ""

echo "--- Step 1: Load default 10-instance subset from HuggingFace ---"
python3 scripts/run_swebench_lite_subset.py --dry-run 2>&1 | tee /tmp/awos_swebench_dry.log

grep -q "dry-run OK" /tmp/awos_swebench_dry.log
grep -q "10 instance" /tmp/awos_swebench_dry.log
echo "[SWEBENCH] dry-run OK"

echo ""
echo "--- Step 2: Unit tests (patch extraction, prediction format) ---"
python3 -m pytest tests/test_swebench_lite_harness.py -q --tb=short -m "not integration"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "PROOF COMPLETE — harness ready"
echo ""
echo "Run predictions (API + git clone, slow):"
echo "  python3 scripts/run_swebench_lite_subset.py --predict --limit 1"
echo ""
echo "Official grade (Docker):"
echo "  pip install swebench"
echo "  python3 scripts/run_swebench_lite_subset.py --evaluate"
echo "════════════════════════════════════════════════════════════════"
