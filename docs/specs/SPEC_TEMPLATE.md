---
title: "Spec: <Feature Name>"
tags:
  - doc/spec
  - phase/N
  - topic/<slug>
  - layer/<slug>
---

# Spec: <Feature Name>

> **Purpose:** Transform research into an ordered, atomic implementation plan.
> Every step must be independently falsifiable. No vague steps.
> Research must exist before this spec is written.

---

## Goal

One sentence: what this feature must accomplish when complete.

Exit condition: how will you know this is done? (e.g., "Test X passes and interface Y returns Z")

---

## Research Reference

Research doc: [[<feature_name>]] — key insight: <one sentence from research>

---

## Files Affected

*Explicit list of files to create or modify. Nothing outside this list should change.*

**New files:**
- `agent/module/new_file.py` — does X

**Modified files:**
- `agent/module/existing_file.py` — add Y to function Z
- `tests/test_module.py` — add tests for new_file

**Files explicitly NOT touched:**
- `agent/module/leave_alone.py` — not in scope

---

## Implementation Steps

*Numbered, ordered, each step independently testable. Each step has a one-line verification.*

### Step 1: <verb> <specific thing>

What to do:
- Specific change #1
- Specific change #2

Verification: `<one-line test or check>`

Edge cases handled by this step:
- ...

---

### Step 2: <verb> <specific thing>

What to do:
- ...

Verification: `<one-line test or check>`

---

### Step 3: <verb> <specific thing>

...

---

*(Add more steps. If any step contains "and," split it.)*
*(If any step would take more than ~2h of focused work, split it.)*

---

## Interface Contract

*Describe any new public interfaces introduced by this feature.*

```python
def new_function(param: Type) -> ReturnType:
    """One-sentence description."""
    ...
```

Input constraints:
- `param` must be non-empty / non-negative / etc.

Output guarantees:
- Returns X when Y
- Raises Z when invalid input

---

## Edge Cases

*Specific scenarios this spec must handle correctly. Each should map to a test.*

| Scenario | Expected behavior |
|---|---|
| Empty input | Returns empty list, no error |
| API timeout | Returns cached value if available; raises TimeoutError if not |
| Invalid type | Raises ValueError at system boundary |
| Null field in response | Skips record, logs warning |

---

## Testing Plan

*How this feature will be validated. Be specific — test names, file locations, what each test covers.*

**Unit tests** (`tests/test_<feature>.py`):
- Happy path: ...
- Failure case 1: ...
- Failure case 2: ...
- Edge case: ...

**Integration tests** (if applicable):
- ...

**Manual verification** (if applicable):
- Run `python scripts/verify_<feature>.py` and confirm output contains X

---

## Mathematical Details

*(Fill in if this feature involves math — required per AWOS §7.2)*

**Quantity being estimated:** ...

**Objective / test statistic:** ...

**Assumptions:** ...

**Numerical stability concerns:** ...

**Implementation options considered:**

| Option | Tradeoff | Decision |
|---|---|---|
| Option A | Exact but slow | Rejected — too slow for real-time |
| Option B | Approximate but fast | Chosen — acceptable error bound |

**Source:** [Author, Title, Year] or [Library docs, version X]

---

## Rollback Plan

If this feature causes problems after deployment, how is it reversed?
- Which files need to be reverted?
- Is there a feature flag?
- Are there database migrations that need to be undone?

---

## Related

- [[<feature_name>]] — research doc
- [[<task_name>]] — task tracking
- [[<related_spec>]] — related spec
