---
description: "Structured debug protocol — trigger after 2 failed fix attempts. Do not attempt a 3rd fix without completing this."
mode: agent
tools:
  - read_file
  - grep_search
  - run_in_terminal
  - get_terminal_output
  - replace_string_in_file
  - create_file
---

# Debug Protocol

**Trigger:** 2 failed fix attempts on the same problem.
**Hard rule:** Do NOT attempt a 3rd fix until steps 1–4 are complete.

## Step 1: Reproduce Cleanly

Write a minimal test case that reliably triggers the bug. It must:
- Run in isolation (no side effects from other tests)
- Produce the exact failure deterministically
- Be as small as possible

If you cannot reproduce it deterministically, **stop**. You are not debugging the right thing.

```bash
python -m pytest tests/test_<module>.py::test_<specific> -xvs
```

## Step 2: Instrument — Surgical Logging

Add targeted print/log statements at the exact failure point. Do NOT add broad logging everywhere.

Focus on:
- The exact input values at the call site
- The intermediate state just before failure
- The exact error type, message, and stack trace

Run the reproduction case and capture the complete output.

## Step 3: Form a Falsifiable Hypothesis

State exactly:

```
HYPOTHESIS: The bug is at [file:line / function name] because [specific reasoning].

PREDICTION: If I am right, then [observable check] will show [expected value].
DISCONFIRM: If I am wrong, then [observable check] will show [different value].
```

Do not write any code until the hypothesis is formed.

## Step 4: Verify the Hypothesis

Run the check from Step 3 — inspect the actual value. Do NOT skip this.

If the hypothesis is confirmed → proceed to Step 5.
If disconfirmed → form a new hypothesis. Repeat Steps 3–4. Max 2 hypothesis cycles before escalating.

## Step 5: Fix — Minimal Change Only

Write the minimum change that addresses the confirmed root cause.
- One fix, one thing
- Do NOT "improve" surrounding code in the same change
- Do NOT refactor while fixing

## Step 6: Regress

```bash
python -m pytest --tb=short
```

Confirm:
- The specific bug is gone (reproduction case passes)
- No other tests broke

## Step 7: Record (if non-obvious)

If the bug was subtle or took >2 cycles, write a brief note:

```markdown
## Bug: <slug>
- Root cause: <one sentence>
- Fix: <one sentence>
- Test added: <yes/no — test name>
```

Add to the active task file or the session checkpoint.

## Escalation

If after 2 full debug cycles (Steps 1–6 twice) the bug persists:
1. **Stop patching**
2. Write a debug post-mortem to `docs/memory/debug_<date>_<slug>.md`
3. Ask the user for a strategy change — different approach, different tool, or revert and redesign
