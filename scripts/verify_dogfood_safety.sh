#!/bin/bash
set -euo pipefail

echo "╔═══════════════════════════════════════════════════════════════════════╗"
echo "║           AWOS DOGFOODING SAFETY CHECK                               ║"
echo "╚═══════════════════════════════════════════════════════════════════════╝"
echo ""

SAFE=true

# Check 1: AWOS_USE_WORKTREE enabled
echo "✓ Checking AWOS_USE_WORKTREE..."
if grep -q "^AWOS_USE_WORKTREE=true" .env 2>/dev/null; then
    echo "  ✓ AWOS_USE_WORKTREE=true in .env"
else
    echo "  ✗ MISSING: AWOS_USE_WORKTREE=true in .env"
    echo "    Add: export AWOS_USE_WORKTREE=true"
    SAFE=false
fi
echo ""

# Check 2: .awosignore exists
echo "✓ Checking .awosignore..."
if [ -f .awosignore ]; then
    echo "  ✓ .awosignore exists"
    echo "  Protected files:"
    grep -v "^#" .awosignore | grep -v "^$" | head -5 | sed 's/^/    - /'
else
    echo "  ✗ MISSING: .awosignore"
    echo "    Create with: cat docs/DOGFOOD_SAFETY.md (see template)"
    SAFE=false
fi
echo ""

# Check 3: Git clean state
echo "✓ Checking git status..."
if git diff-index --quiet HEAD -- 2>/dev/null; then
    echo "  ✓ Working directory clean"
else
    echo "  ⚠ UNCOMMITTED CHANGES:"
    git status --short | head -5 | sed 's/^/    /'
    echo "    Recommendation: commit before dogfooding"
fi
echo ""

# Check 4: Backup branch
echo "✓ Checking backup branch..."
BACKUP_BRANCH="dogfood-backup-$(date +%Y%m%d)"
if git show-ref --verify --quiet "refs/heads/$BACKUP_BRANCH" 2>/dev/null; then
    echo "  ✓ Backup branch exists: $BACKUP_BRANCH"
else
    echo "  ⚠ NO BACKUP BRANCH TODAY"
    echo "    Create with: git branch $BACKUP_BRANCH"
fi
echo ""

# Check 5: awos.py works
echo "✓ Checking awos.py..."
if python3 awos.py --help >/dev/null 2>&1; then
    echo "  ✓ awos.py executable"
else
    echo "  ✗ BROKEN: awos.py --help failed"
    SAFE=false
fi
echo ""

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$SAFE" = true ]; then
    echo "✅ SAFE TO DOGFOOD"
    echo ""
    echo "First job (recommended):"
    echo "  awos worker start \"Research AWOS search and planning infrastructure: read scaffold/agent/planner.py, memory/vector_memory.py, and tools/symbol_index.py. Document current capabilities. Output: docs/research/search_planning_audit.md\""
    echo ""
    echo "After completion:"
    echo "  awos worker diff  # Review changes"
    echo "  awos stats --savings  # Track cost"
else
    echo "❌ NOT SAFE - Fix issues above first"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
