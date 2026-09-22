# AWOS Dogfooding Safety Guide

> **"The crane lifting itself" problem — how to safely use AWOS on AWOS**

---

## The Risk

When you use AWOS to modify AWOS itself:
- Bug in worker.py could break the worker
- Bad edit to orchestrator.py could halt execution
- Syntax error in critical files = broken system

**Solution:** Multiple layers of safety.

---

## Safety Layers

### 1. Git Worktree Isolation (MANDATORY)

**What it does:**
- Creates isolated copy at `.awos/worktrees/<id>/`
- AWOS edits the worktree, NOT your main code
- You review the diff before merging
- Bad changes never touch main branch

**How to enable:**

```bash
export AWOS_USE_WORKTREE=true
```

Or add to `.env`:

```
AWOS_USE_WORKTREE=true
```

**Verify it's working:**

```bash
awos worker start "add a comment to README.md"
# Wait for it to finish
ls .awos/worktrees/  # Should see a new directory
awos worker diff     # Shows changes in worktree
```

**If worktree is created → main code is safe.**

---

### 2. Protected Files (RECOMMENDED)

Add `.awosignore` to block critical files:

```bash
cat > .awosignore << 'EOF'
# Core execution files - don't let AWOS edit these
scaffold/agent/orchestrator.py
scaffold/agent/worker.py
scaffold/agent/unified_agent.py
awos.py

# Safety system itself
scaffold/agent/worktree.py
scaffold/agent/runtime_session.py

# Your API keys
.env
EOF
```

This prevents AWOS from even attempting to edit critical files.

---

### 3. Manual Review (ALWAYS)

After each job:

```bash
# 1. Check what changed
awos worker diff

# 2. If it looks good, manually apply
cd .awos/worktrees/<id>/
# Read the changed files
# Copy good changes to main manually

# 3. Or merge via git (if confident)
git merge awos/<branch>
```

**Never auto-merge on dogfooding.** Always inspect first.

---

### 4. Backup Before Starting

```bash
git status  # Ensure clean state
git branch dogfood-backup-$(date +%Y%m%d)  # Create backup branch
```

If AWOS breaks something catastrophically:

```bash
git reset --hard dogfood-backup-20260627
```

---

## Safe Dogfooding Workflow

### Safest (Day 1-2):

```bash
# 1. Enable worktree
export AWOS_USE_WORKTREE=true

# 2. Create protected files list
cat > .awosignore << 'EOF'
scaffold/agent/orchestrator.py
scaffold/agent/worker.py
awos.py
.env
EOF

# 3. Run AWOS on safe tasks (docs, specs, research)
awos worker start "research search+planning infrastructure: read planner.py, vector_memory.py, symbol_index.py. Write docs/research/search_planning_audit.md"

# 4. Review worktree changes
awos worker diff

# 5. Manually copy good changes to main
cd .awos/worktrees/<id>/docs/research/
cat search_planning_audit.md  # Inspect
cp search_planning_audit.md ~/2024/projects/AWOS_coding_agent/docs/research/
```

**Risk level: LOW** — only touching docs, nothing executable.

---

### Medium Safe (Day 3-4):

```bash
# Same as above, but allow AWOS to edit non-critical code
# (new files, test files, helper scripts)

awos worker start "create new file: scaffold/agent/search_helper.py with semantic search utilities"

# Still review manually before merging
```

**Risk level: MEDIUM** — editing code, but in worktree, and reviewed first.

---

### Advanced (Day 5+):

```bash
# Remove some .awosignore restrictions
# Let AWOS edit planner.py, but still in worktree + manual review

awos worker start "enhance planner.py to call search_helper.py before generating tasks"

# Test the worktree version first
cd .awos/worktrees/<id>/
pytest tests/  # Ensure nothing broke

# Then merge if tests pass
```

**Risk level: MEDIUM-HIGH** — editing critical code, but tested first.

---

## Red Flags (STOP immediately)

If you see any of these, **stop and revert**:

1. **AWOS edits files outside worktree**
   - Check: `ls -la scaffold/agent/orchestrator.py` (timestamp shouldn't change)
   - If it did: `git checkout scaffold/agent/orchestrator.py`

2. **Syntax errors in core files**
   - Test: `python3 awos.py --help`
   - If broken: `git reset --hard HEAD`

3. **AWOS suggests deleting `.awos/` or critical infrastructure**
   - Reject immediately
   - Add to `.awosignore`

4. **Infinite loops or recursive self-modification**
   - Kill: `Ctrl+C`
   - Check worktree for what it was trying to do
   - Fix issue before retrying

---

## Recovery Procedures

### If AWOS broke main code (worktree failed):

```bash
# 1. Check git status
git status

# 2. Revert specific file
git checkout -- <broken-file>

# 3. Or nuclear option (lose all uncommitted work)
git reset --hard HEAD
```

### If AWOS broke itself in worktree:

```bash
# 1. Just delete the worktree
rm -rf .awos/worktrees/<id>/

# 2. Main code is unaffected
python3 awos.py --help  # Should still work
```

### If `.awos/` state is corrupted:

```bash
# 1. Backup current state
mv .awos .awos.backup

# 2. Fresh start (lose learning, but recover)
mkdir .awos

# 3. Or selectively restore
mv .awos.backup/goals .awos/
mv .awos.backup/skills .awos/
# (don't restore sessions if those are broken)
```

---

## First Dogfood Job (Safe Starter)

**Goal:** Test worktree + diff flow without risk.

```bash
# 1. Enable safety
export AWOS_USE_WORKTREE=true
cat > .awosignore << 'EOF'
scaffold/agent/orchestrator.py
scaffold/agent/worker.py
awos.py
.env
EOF

# 2. Safe task (read-only analysis → write doc)
awos worker start "Research AWOS search and planning infrastructure: read scaffold/agent/planner.py, memory/vector_memory.py, and tools/symbol_index.py. Document current capabilities, what's used, what's missing. Output: docs/research/search_planning_audit.md"

# 3. Wait (~5-10 minutes)

# 4. Check results
awos worker status  # See if completed
awos worker diff    # See what changed

# 5. Inspect worktree
ls .awos/worktrees/  # Find ID
cd .awos/worktrees/<id>/docs/research/
cat search_planning_audit.md  # Read result

# 6. If good, manually copy
cp search_planning_audit.md ~/2024/projects/AWOS_coding_agent/docs/research/

# 7. Track cost
awos stats --savings
```

**Expected outcome:**
- Worktree created ✓
- New doc written in worktree ✓
- Main code untouched ✓
- You reviewed before merging ✓

**If this works → proceed to Day 2 jobs.**

---

## Metrics to Track

After each job:

| Metric | How to check | Expected |
|--------|-------------|----------|
| Worktree used? | `ls .awos/worktrees/` | New dir each job |
| Main code safe? | `git status` | Clean (no uncommitted edits to core files) |
| Quality? | Manual review of diff | Useful, correct output |
| Cost? | `awos stats --savings` | Dropping over jobs 1-5 |
| Time saved? | Did you babysit? | <5 min of attention per job |

---

## When Safe to Remove Guardrails

After 10+ jobs where:
- Worktree always used ✓
- No main code corruption ✓
- Cost dropping ✓
- Quality high ✓

Then you can:
1. Reduce `.awosignore` restrictions
2. Auto-merge low-risk changes (docs, tests)
3. Let AWOS edit more critical files (still reviewed)

**But always keep:**
- `AWOS_USE_WORKTREE=true` (non-negotiable for dogfooding)
- Manual review of changes to core execution files
- Backup branch before big changes

---

## Summary: Minimum Safe Setup

```bash
# In .env
AWOS_USE_WORKTREE=true

# Create .awosignore
scaffold/agent/orchestrator.py
scaffold/agent/worker.py
awos.py
.env

# Create backup branch
git branch dogfood-backup-$(date +%Y%m%d)

# Run first safe job (docs only)
awos worker start "research search+planning, write audit doc"

# Review before merging
awos worker diff
```

**If you follow this → crane can safely lift itself.**
