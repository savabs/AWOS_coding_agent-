#!/usr/bin/env bash
# Company-style smoke: fix failing tests (Claude Code workflow).
# Direct ReAct backend — no planner/decomposer noise.

set -euo pipefail
cd "$(dirname "$0")/.."

export AWOS_REACT_WORKER=1
export AWOS_SAFE_TO_RUN_TESTS=1
export AWOS_ENABLE_WEB_TOOLS=1
export AWOS_REACT_MAX_TURNS=18
export ALLOW_CODE_EXEC=1

echo "════════════════════════════════════════════════════════════════"
echo "AWOS COMPANY SMOKE — ReAct backend on logic bugs"
echo "════════════════════════════════════════════════════════════════"
echo ""

python3 scripts/run_company_smoke.py
