#!/usr/bin/env bash
# demo_multi_resolution_context.sh
# Live proof: Multi-Resolution Context Pipeline token savings
#
# Run: bash scripts/demo_multi_resolution_context.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================="
echo "  Multi-Resolution Context Pipeline — Live Proof"
echo "============================================================="
echo ""

# ── Step 1: Imports + basic smoke ──
echo "[1/4] Module imports + basic validation..."
python3 "$SCRIPT_DIR/_demo_context_imports.py"
echo ""

# ── Step 2: All levels with real code ──
echo "[2/4] Building context at Levels 0-3 (real worker.py)..."
python3 "$SCRIPT_DIR/_demo_context_levels.py"
echo ""

# ── Step 3: Edge cases ──
echo "[3/4] Graceful degradation (edge cases)..."
python3 "$SCRIPT_DIR/_demo_context_edge_cases.py"
echo ""

# ── Step 4: Tests + lint ──
echo "[4/4] Unit tests + lint..."
python3 -m pytest tests/test_semantic_retriever.py tests/test_multi_resolution_context.py -q -o "addopts=" 2>&1 | tail -1
python3 -m ruff check scaffold/agent/semantic_retriever.py scaffold/agent/multi_resolution_context.py 2>&1

echo ""
echo "============================================================="
echo "  [OK] Multi-Resolution Context Pipeline — Live Proof Complete"
echo ""
echo "  Deliverables:"
echo "    scaffold/agent/semantic_retriever.py          — semantic search wrapper"
echo "    scaffold/agent/multi_resolution_context.py     — 4-level context pipeline"
echo "    tests/test_semantic_retriever.py               — 10 tests"
echo "    tests/test_multi_resolution_context.py         — 19 tests"
echo "    scaffold/agent/worker.py                       — context_assembly integration"
echo ""
echo "  Context levels (escalation strategy):"
echo "    Level 0 — task only       (~50 tokens)   — typos, renames, simple fixes"
echo "    Level 1 — + module overview (~150 tokens) — low complexity tasks"
echo "    Level 2 — + signatures     (~300 tokens)  — medium complexity"
echo "    Level 3 — + code chunks    (~1200 tokens)  — complex changes"
echo "============================================================="
