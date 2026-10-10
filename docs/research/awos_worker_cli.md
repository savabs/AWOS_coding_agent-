# Research — `awos worker` CLI

**Date:** 2026-06-24  
**Context:** Phase C of Stage 1 Subscription Worker — simple product surface

---

## Problem

Current UX exposes kernel jargon:

```bash
awos mission start --long      # what's a mission?
awos sessions list             # what's a session?
awos gauntlet run G1           # what's a gauntlet?
```

**Customer confusion:**
- "Mission" vs "session" vs "gauntlet" — internal concepts leaking
- No obvious "start work and come back later" command
- Resume requires knowing session IDs

**Target UX (competitor reference):**
```bash
cursor "fix the auth bug"      # simple
devin "add tests to login.py"  # simple
```

---

## Solution

**`awos worker` — thin wrapper over mission + sessions**

### Desired flow

```bash
# Start work
awos worker start "fix auth module, add tests"
→ Session: rs_abc123
→ Running in .awos/worktrees/abc123
→ Ctrl+C to pause anytime

# Check status
awos worker status
→ rs_abc123: running, 4/8 tasks, $0.02

# Resume
awos worker resume
→ Resumes latest paused session

# Review sandbox
awos worker diff
→ Shows worktree changes
```

### What it hides

- Mission vs session terminology
- Pre-planned `plan_file` workaround
- Gauntlet (lab tool, not customer feature)
- Internal session IDs (unless needed for resume)

---

## Design choices

### 1. Session ID visibility

**Option A:** Always show
```
awos worker start "goal" → rs_abc123
```

**Option B:** Hide unless multiple active
```
awos worker start "goal" → Working...
awos worker status → 1 active session (4/8 tasks)
```

**Recommendation:** Option A (explicit) — users will Ctrl+C and need to resume by ID.

### 2. Worktree visibility

**Always explicit:**
```
Working in: .awos/worktrees/abc123
Main repo unchanged
```

Builds trust — "I can review before merge."

### 3. Goal → mission mapping

```python
def worker_start(goal: str):
    # Internally: create RuntimeSession + run orchestrator
    # User sees: "Session rs_xyz"
    # No "mission" word exposed
```

### 4. Resume behavior

```bash
awos worker resume          # latest paused
awos worker resume rs_xyz   # specific session
```

### 5. Pause behavior

Ctrl+C during run → checkpoint → pause (already works in orchestrator).

---

## Implementation approach

**Thin wrapper** — no new execution logic, just UX sugar over existing:

| Command | Maps to |
|---------|---------|
| `awos worker start "goal"` | `orchestrator.execute_feature(goal, session=new)` |
| `awos worker status` | `sessions list` + format filter |
| `awos worker resume [id]` | `orchestrator.execute_feature(session=load(id))` |
| `awos worker diff` | `git diff main worktrees/<id>` |
| `awos worker cancel` | `sessions finalize(cancelled)` |

**No new kernel logic needed.**

---

## Files to touch

| File | Change |
|------|--------|
| `awos.py` | Add `cmd_worker_*` functions, register subparser |
| (no kernel changes) | Orchestrator, sessions already support this |

---

## Edge cases

| Case | Behavior |
|------|----------|
| Multiple active sessions | `worker status` lists all; `resume` requires ID |
| Session already completed | `resume` errors with "session completed, see diff" |
| Worktree deleted | Error: "sandbox missing, cannot resume" |
| Empty goal string | Error: "goal required" |
| No API key | Same error as `awos run` (existing) |

---

## Success criteria

After implementation:
1. User runs `awos worker start "goal"` → session starts
2. Ctrl+C → pauses cleanly
3. `awos worker resume` → continues from checkpoint
4. `awos worker diff` → shows sandbox changes
5. Documentation mentions `worker`, not `mission`
6. No behavior change to kernel (pure UX wrapper)

---

## Related

- `awos.py` — CLI entry point
- `scaffold/agent/orchestrator.py` — execution (no changes needed)
- `scaffold/agent/runtime_session.py` — session lifecycle (no changes needed)
- `docs/product/stage1_subscription_worker_guideline.md` — product vision
