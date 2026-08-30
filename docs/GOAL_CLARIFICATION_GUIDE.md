# Goal Clarification System — User Guide

> **Implemented:** 2026-06-27  
> **Purpose:** Prevent misinterpretation by clarifying ambiguous goals BEFORE execution

---

## What It Does

AWOS now has a 3-phase goal clarification system that mimics how Devin, Cursor Composer, and OpenAI o3 handle unclear requests:

**Phase 1: Clarification Questions**
- Detects if goal is missing key information
- Asks targeted questions (output file, acceptance criteria, scope)
- User answers before planning starts

**Phase 2: Plan Review Checkpoint**
- Shows generated plan to user
- User approves/rejects/refines BEFORE execution
- Catches misinterpretation in 1 minute vs 1 hour of wrong work

**Phase 3: Cheap Ambiguity Detection**
- Uses DeepSeek ($0.14/M) to detect ambiguity
- 20x cheaper than Claude ($3/M) for clarification loops
- Fallback to heuristics if model unavailable

---

## How to Use

### Enable Features

Already enabled in `.env`:
```bash
AWOS_USE_WORKTREE=true              # Safety (worktree isolation)
AWOS_ENABLE_CLARIFICATION=true      # Phase 1 & 3 (questions)
AWOS_ENABLE_PLAN_REVIEW=true        # Phase 2 (plan approval)
```

### Example: Vague Goal

**Before (old behavior):**
```bash
awos worker start "research search+planning"
# → AWOS guesses, creates wrong files, wastes time
```

**After (new behavior):**
```bash
awos worker start "research search+planning"

# AWOS detects ambiguity and asks:
```
```
╔══════════════════════════════════════════════════════════════╗
║              GOAL CLARIFICATION NEEDED                       ║
╚══════════════════════════════════════════════════════════════╝

Your goal: research search+planning

Before I start, I need to clarify 3 thing(s):

1. What file should I create or modify? (e.g., docs/research/audit.md)
   Answer: docs/research/search_planning_audit.md

2. How will we know the task is complete? (e.g., 'Tests pass', 'Code runs')
   Answer: File exists and documents current capabilities

3. What files can I touch? What files should I NOT touch?
   Answer: Only create new doc, don't edit any code files

═══════════════════════════════════════════════════════════════════

Thank you! Starting work with clarified goal...
```

### Example: Plan Review

After clarification, AWOS generates a plan and shows it:

```
╔══════════════════════════════════════════════════════════════╗
║              PLAN REVIEW CHECKPOINT                          ║
╚══════════════════════════════════════════════════════════════╝

Goal: research search+planning. Output: docs/research/search_planning_audit.md

Proposed Plan (3 tasks):
  1. Read scaffold/agent/planner.py
     File: scaffold/agent/planner.py
  2. Read memory/vector_memory.py
     File: memory/vector_memory.py
  3. Create docs/research/search_planning_audit.md with analysis
     File: docs/research/search_planning_audit.md

Files to modify: docs/research/search_planning_audit.md

Acceptance criteria: File exists and documents current capabilities

╚══════════════════════════════════════════════════════════════╝

Approve plan? [y]es / [n]o / [r]efine / [q]uestion: 
```

**Options:**
- `y` — Approve, start execution
- `n` — Reject, refine goal and retry
- `r` — Provide feedback for replanning
- `q` — Ask questions about the plan

---

## Clear vs. Ambiguous Goals

### Ambiguous (triggers clarification):
```
"research search+planning"                     # What output? What's done?
"improve performance"                          # Which file? How to verify?
"add authentication"                           # What scope? What tests?
```

### Clear (skips clarification):
```
"Create docs/research/search_planning_audit.md analyzing planner.py and vector_memory.py. Done when file exists and documents current capabilities. Only create new doc."

"Modify scaffold/agent/planner.py to add search_codebase() method. Tests pass. Don't edit worker.py."

"Fix tests in tests/test_planner.py. All tests pass. Only touch test file."
```

**Pattern for clear goals:**
```
<Action> <file/output> <details>. <Acceptance>. <Scope>.
```

---

## Cost Savings

**Old approach (guess and retry):**
- Iteration 1: $0.003 (wrong interpretation)
- Iteration 2: $0.003 (still wrong)
- Iteration 3: $0.003 (finally right)
- **Total: $0.009, 30 minutes wasted**

**New approach (clarify first):**
- Clarification: $0.0001 (DeepSeek)
- Execution: $0.003 (right the first time)
- **Total: $0.0031, 5 minutes**

**Savings: 70% cost, 83% time**

---

## Non-Interactive Mode

For programmatic use (scripts, APIs), clarification can be non-interactive:

```python
from scaffold.agent.goal_clarifier import clarify_goal

# Non-interactive: returns questions instead of asking
clarified = clarify_goal("vague goal", interactive=False)

if clarified.is_ambiguous:
    # Send questions to user via your UI
    questions = clarified.clarifying_questions
    # Get answers asynchronously
    # ...
    # Then rerun with clarified goal
```

---

## Disable Features (if needed)

If you prefer old behavior (no clarification):

```bash
# In .env
AWOS_ENABLE_CLARIFICATION=false
AWOS_ENABLE_PLAN_REVIEW=false
```

Or per-command:
```bash
AWOS_ENABLE_CLARIFICATION=false awos worker start "some goal"
```

---

## Success Metrics

| Metric | Before | After (Target) |
|--------|---------|----------------|
| Misinterpretation rate | 100% (first dogfood job) | <20% |
| Wasted work (wrong path) | 10+ min | <2 min |
| User intervention time | 0 min (fails silently) | 2 min (review plan) |
| Cost per clarification | $0.003 (Claude) | $0.0001 (DeepSeek) |

---

## References

- **Research:** `docs/research/goal_clarification_best_practices.md`
- **Implementation:**
  - `scaffold/agent/goal_clarifier.py` (Phases 1 & 3)
  - `scaffold/agent/plan_reviewer.py` (Phase 2)
  - `scaffold/agent/orchestrator.py` (integration)
- **Tests:** `tests/test_goal_clarification.py` (TODO)

---

## Troubleshooting

**Clarification not triggering:**
- Check `.env`: `AWOS_ENABLE_CLARIFICATION=true`
- Goal might already be clear (has output file + acceptance)

**Plan review not showing:**
- Check `.env`: `AWOS_ENABLE_PLAN_REVIEW=true`
- Might be disabled in non-interactive mode

**DeepSeek API failing:**
- Falls back to heuristic detection
- Check `DEEPSEEK_API_KEY` in `.env`
- Logs show: `[AmbiguityDetector] Model call failed, using heuristic`

---

## Next Steps

After validating in dogfooding:
1. Add unit tests for clarifier + reviewer
2. Measure misinterpretation rate drop
3. Consider async clarification for chat UIs
4. Train model to learn YOUR clarification patterns (Phase 4)
