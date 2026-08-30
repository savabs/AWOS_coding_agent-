#!/usr/bin/env bash
# Live proof: ReAct worker stack (tools + exploration + mock ReAct loop)

set -euo pipefail

echo "════════════════════════════════════════════════════════════════"
echo "REACT WORKER — Live Proof"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "This demo proves the P0 ReAct stack without API keys:"
echo "  1. Pre-plan exploration (grep keywords from goal)"
echo "  2. Workspace-scoped coding tools (local + web + GitHub + HF + run_python)"
echo "  3. ReAct loop (mock LLM: grep → edit → finish)"
echo ""
echo "Watch for these markers:"
echo "  [EXPLORE] Pre-plan repository scan..."
echo "  [PROOF] ReAct mock loop: grep → edit_file → finish OK"
echo "  passed (all tests green)"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo ""

cd "$(dirname "$0")/.."

export AWOS_REACT_WORKER=1
export AWOS_EXPLORATION_PHASE=1
export AWOS_SAFE_TO_RUN_TESTS=0
export AWOS_REACT_MAX_TURNS=5

echo "--- Step 1: Exploration phase ---"
python3 - <<'PY'
from scaffold.agent.exploration_phase import run_exploration
r = run_exploration("greet function in pkg hello", ".")
print("[EXPLORE] Pre-plan repository scan...")
print(r["exploration_summary"][:500])
PY

echo ""
echo "--- Step 2: Unit tests (tools + mock ReAct loop) ---"
python3 -m pytest tests/test_react_worker.py -v -s --tb=short

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "PROOF COMPLETE — ReAct stack verified"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "To run with real LLM on a goal:"
echo "  export AWOS_SAFE_TO_RUN_TESTS=1"
echo "  awos worker start --auto-approve \"your goal here\""
echo ""
