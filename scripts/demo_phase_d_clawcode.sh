#!/usr/bin/env bash
# Phase D live proof — AWOS on clawcode (real external repo)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Phase D: clawcode real-repo mission ==="
echo "Target: /home/becmachlean/2024/projects/clawcode"
echo ""

python3 awos.py mission guide --clawcode | head -n 30

echo ""
echo "Starting mission (background-friendly — Ctrl+C pauses after current task)..."
echo ""

python3 awos.py mission start --clawcode

echo ""
echo "=== Post-run checks ==="
python3 awos.py mission status --clawcode
python3 awos.py stats 2>&1 | tail -n 12

echo ""
echo "Review sandbox:"
echo "  awos worker diff"
echo "  cd /home/becmachlean/2024/projects/clawcode/.awos/worktrees/<session_id> && python3 -m pytest tests/ -q"
