#!/usr/bin/env bash
# demo_wedge_v1_test_rescue.sh — Live proof for Test Rescue wedge (no API required).
# Spec: docs/specs/wedge_v1_test_rescue_spec.md
# Proof: docs/wedge_v1_test_rescue_proof.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/wedge_v1/payment_utils"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== Wedge v1 Test Rescue demo ==="
cp -a "$FIXTURE/." "$TMP/"
cd "$TMP"
git init -q
git add -A
git commit -q -m "buggy baseline"

echo ""
echo "--- Step 1: reds before fix ---"
python3 -m pytest tests/ -q --tb=line || true

echo ""
echo "--- Step 2: apply golden fix (simulates successful worker) ---"
python3 <<'PY'
from pathlib import Path
p = Path("payment.py")
text = p.read_text()
text = text.replace(
    "return amount - (amount * percent / 100 / 100)",
    "return amount - (amount * percent / 100)",
)
text = text.replace(
    """    # Bug: truncates instead of half-up quantize
    return int(taxed * 100) / 100""",
    """    return float(Decimal(str(taxed)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))""",
)
text = text.replace(
    """    if opened:
        return True
    return days_since_purchase <= 30""",
    """    if opened:
        return False
    return days_since_purchase <= 30""",
)
p.write_text(text)
PY

git add payment.py
git commit -q -m "fix payment helpers"

echo ""
echo "--- Step 3: wedge assertions ---"
python3 "$ROOT/scripts/wedge_v1_assert.py" \
  --root "$TMP" \
  --assertions "$FIXTURE/wedge_goal.json"

echo ""
echo "=== Demo complete — look for WEDGE_ASSERT: SEMANTIC_PASS above ==="
