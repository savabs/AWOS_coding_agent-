#!/usr/bin/env bash
# demo_ci_rescue_sprint.sh — CI Rescue Sprint corpus demo (no API).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/wedge_v1/ci_rescue_sprint"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== CI Rescue Sprint corpus demo ==="
cp -a "$FIXTURE/." "$TMP/"
cd "$TMP"
git init -q
git add -A
git commit -q -m "buggy ci baseline"

echo ""
echo "--- Step 1: red CI count ---"
set +e
OUT=$(python3 -m pytest tests/ -q --tb=no 2>&1)
set -e
REDS=$(echo "$OUT" | grep -oP '\d+(?= failed)' | head -1 || true)
REDS=${REDS:-0}
echo "CI_RESCUE: red_count=$REDS"
python3 -m pytest tests/ -q --tb=line || true

if [[ "$REDS" -lt 15 ]]; then
  echo "CI_RESCUE: FAIL — need >= 15 reds for Cursor-class corpus"
  exit 1
fi

echo ""
echo "--- Step 2: apply golden reference (simulates successful sprint) ---"
for mod in auth billing inventory shipping promo; do
  cp "golden/${mod}.py" "${mod}.py"
done
git add auth.py billing.py inventory.py shipping.py promo.py
git commit -q -m "ci rescue sprint fixes"

echo ""
echo "--- Step 3: full suite ---"
python3 -m pytest tests/ -q --tb=no
echo "CI_RESCUE: golden 35/35 pass"

echo ""
echo "--- Step 4: wedge assertions ---"
python3 "$ROOT/scripts/wedge_v1_assert.py" \
  --root "$TMP" \
  --assertions "$FIXTURE/wedge_goal.json"

echo ""
echo "=== CI Rescue Sprint demo complete ==="
