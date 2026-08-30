---
title: "Spec: Recover July 27 Worktree"
tags:
  - doc/spec
  - phase/1
  - topic/worktree-recovery
  - layer/operations
---

# Spec: Recover July 27 Worktree

## Goal

Produce a validated, documented handoff for the existing July 27 uncommitted work without deleting, resetting, or committing user work.

**Exit condition:** focused tests pass, `git diff --check` is clean for the recovery-owned documentation changes, and the task records the remaining source/runtime classification.

## Research Reference

Research: [[recover_july27_worktree]] — the latest implementation is present in the worktree but was not consolidated after validation.

## Files Affected

**New files:**
- `docs/research/recover_july27_worktree.html`
- `docs/research/recover_july27_worktree.md`
- `docs/specs/recover_july27_worktree_spec.html`
- `docs/specs/recover_july27_worktree_spec.md`
- `tasks/active/recover_july27_worktree.html`
- `tasks/active/recover_july27_worktree.md`

**Modified files:**
- `docs/specs/wedge_v1_test_rescue_spec.md` — remove reported trailing whitespace only.
- `tasks/active/wedge_v1_test_rescue.md` — remove reported trailing whitespace only.

**Files explicitly NOT touched:**
- All implementation files, tests, `.awos/` runtime state, benchmark fixtures, and unrelated documentation until a separate classification decision is made.
- Git history and branches.

## Implementation Steps

### Step 1: Record the recovery state

Verification: `git status --short` and the latest checkpoint are referenced in the task file.

### Step 2: Remove only the reported documentation whitespace

Verification: `git --no-pager diff --check` reports no whitespace errors for the recovery-owned changes.

### Step 3: Re-run focused debugger and quality-gate tests

Verification: `python3 -m pytest tests/test_debug_*.py tests/test_quality_gate_reliability.py -q` passes.

### Step 4: Classify uncommitted changes without deleting them

Verification: the task notes identify source, test, documentation, and generated-runtime groups, with no reset or clean command run.

### Step 5: Write the recovery handoff

Verification: the task file records the validated baseline and the next independent development task.

## Interface Contract

This recovery task introduces no runtime interface and no dependency changes.

## Edge Cases

| Scenario | Expected behavior |
|---|---|
| Focused tests fail | Stop recovery and record the exact failure; do not broaden edits. |
| Generated `.awos/` files are mixed with source changes | Classify them; do not delete or stage them automatically. |
| Whitespace cleanup changes semantic content | Revert that cleanup attempt and inspect the exact lines. |
| User asks to commit | Review the classified diff before creating a commit. |

## Testing Plan

- `git --no-pager diff --check`
- `python3 -m pytest tests/test_debug_*.py tests/test_quality_gate_reliability.py -q`
- `python3 scripts/obsidian_lint.py` scoped to the six recovery artifacts if supported by the script.

## Live Proof

Run the focused test command and observe `62 passed` or a later passing count. Then run `git diff --check` and observe no output with exit code 0.

## Related

- [[recover_july27_worktree]]
- [[recover_july27_worktree]]
- [[checkpoint_2026-07-27_2]]
