#!/usr/bin/env bash
# demo_ci_rescue.sh — CI Rescue live proof demo
# Shows: awos fix-ci command + web dashboard
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/wedge_v1/ci_rescue_sprint"

echo "═══════════════════════════════════════════════════════════════════"
echo "  AWOS CI Rescue — Live Proof Demo"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# Step 1: Show fixture
echo "Step 1: CI Rescue fixture (28 failing tests)"
echo "─────────────────────────────────────────────"
cd "$FIXTURE"
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -5
echo ""

# Step 2: Run awos fix-ci
echo "Step 2: Run 'awos fix-ci'"
echo "─────────────────────────────────────────────"
cd "$ROOT"
python3 awos.py fix-ci --root "$FIXTURE" 2>&1 | head -30
echo ""
echo "[Demo continues in background...]"
echo ""

# Step 3: Start web dashboard
echo "Step 3: Start web dashboard"
echo "─────────────────────────────────────────────"
echo "Run: awos serve"
echo "Open: http://127.0.0.1:8765"
echo ""
echo "Dashboard shows:"
echo "  • Total rescues"
echo "  • Tests fixed"
echo "  • Total cost"
echo "  • Savings vs Cursor"
echo "  • Recent rescue history"
echo ""

# Step 4: Show API
echo "Step 4: API endpoints"
echo "─────────────────────────────────────────────"
echo "curl http://127.0.0.1:8765/api/ci-rescue/stats"
echo "curl http://127.0.0.1:8765/api/ci-rescue/history"
echo ""

echo "═══════════════════════════════════════════════════════════════════"
echo "  Demo complete"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Review changes: awos worker diff"
echo "  2. Apply changes: git merge"
echo "  3. Start dashboard: awos serve"
echo ""
