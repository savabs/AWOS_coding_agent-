#!/usr/bin/env bash
# Live proof: repo map + grep literal mode (no API keys)

set -euo pipefail

cd "$(dirname "$0")/.."

echo "════════════════════════════════════════════════════════════════"
echo "REPO MAP + GREP — Live Proof"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Markers:"
echo "  [REPO_MAP] cmd_stats line anchors"
echo "  [GREP] literal mode for stats.add_argument"
echo "  passed (all tests green)"
echo ""

export AWOS_REPO_MAP=1
export AWOS_EXPLORATION_PHASE=0

echo "--- Repo map on AWOS goal (awos stats --costs) ---"
python3 - <<'PY'
from scaffold.agent.repo_map import build_repo_map
goal = "Implement awos stats --costs in awos.py near stats.add_argument savings cmd_stats"
m = build_repo_map(goal, ".", primary_file="awos.py")
text = m["summary"]
assert "cmd_stats" in text, "missing cmd_stats anchor"
assert "Grep hits" in text
print("[REPO_MAP] cmd_stats line anchors OK")
for line in text.splitlines():
    if "cmd_stats" in line or "savings" in line.lower():
        print(" ", line[:100])
PY

echo ""
echo "--- Grep literal: quoted argparse pattern ---"
python3 - <<'PY'
from scaffold.agent.tools.filesystem import GrepTool
r = GrepTool().execute({
    "pattern": 'stats.add_argument("--savings"',
    "root": ".",
    "path": "awos.py",
})
assert r.success, r.error
assert r.data.get("literal") is True
assert r.data["hits"], "expected hits in awos.py"
print(f"[GREP] literal mode: {len(r.data['hits'])} hit(s) in awos.py")
print(" ", r.data["hits"][0]["line"], r.data["hits"][0]["text"][:80])
PY

echo ""
echo "--- Unit tests ---"
python3 -m pytest tests/test_grep_repo_map.py -q --tb=short

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "PROOF COMPLETE — repo map + grep fixes verified"
echo "════════════════════════════════════════════════════════════════"
