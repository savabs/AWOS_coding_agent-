# Research — Stagnation Breaker

**Date:** 2026-06-23  
**Context:** Loop engineering safety — prevent loopmaxxing and budget burn

---

## Problem

Unattended loop runs can spin on the same failure without making progress:

- Same verifier error repeating (e.g., syntax error in the same place)
- Same worker failure pattern (e.g., parse failure on the same task)
- Decomposition cycling (task 4 → 4_1, 4_2 → same error)

**Example from long mission `rs_b2667c2e8eb7`:**  
Task 4 (`verifier.py` comment edit) failed worker 3×, decomposed into `4_1`, `4_2` — still same placement issue. Loop finished but burned tokens on a stylistic fight.

Without a breaker: unattended runs burn API budget spinning until monthly cap or max_decomposition reached.

---

## Design

Detect **stagnation** = same (error signature, file) repeating ≥ 3 times in a sliding window.

**Signature = hash of:**
- Failure kind (`worker_fail`, `verify_fail`, `test_fail`)
- Error text (first 200 chars, normalized)
- File path

When tripped:
- Session → `PAUSED`
- Reason: `STAGNATION`
- Log which task/error pattern caused trip

Human can then:
- Skip the stuck task
- Edit plan
- Resume with manual hint
- Abort

---

## Implementation approach

**Where:** `orchestrator.execute_feature()` main loop, after each task cycle.

**State:**
- `_failure_history: deque[tuple[signature_hash, task_id]]` max 10 recent
- Check after each failed task: count occurrences of same signature
- If count ≥ 3 → trip breaker

**Signature hash:**

```python
def _failure_signature(failure_kind: str, error: str, file_path: str) -> str:
    normalized = re.sub(r'\d+', 'N', error[:200].lower())  # numbers → N
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return hashlib.sha256(
        f"{failure_kind}:{normalized}:{file_path}".encode()
    ).hexdigest()[:16]
```

**Config:**
- `AWOS_STAGNATION_THRESHOLD` (default 3)
- `AWOS_STAGNATION_WINDOW` (default 10 recent failures)

---

## Edge cases

| Case | Behavior |
|------|----------|
| Different files, same error | Not stagnation — different context |
| Same file, different error lines | Hash distinguishes (line numbers normalized but structure differs) |
| Decomposed sub-tasks | Still counts if error signature matches parent |
| Replan creates new task | Signature tied to file+error, not original task_id |

---

## Test plan

1. Unit test: hash collision / window mechanics
2. Integration test: orchestrator with mock Worker that returns same error 3×
3. Live proof: mission with intentionally broken task → should auto-pause

---

## Alternative considered

**Per-task attempt counter** (rejected): doesn't catch cross-task stagnation (e.g., decompose → same error on sub-task).

**Diff-hash unchanged** (future): if file hash same after 3 worker passes → also stagnation. Separate feature, not this PR.

---

## Next

Spec: `docs/specs/stagnation_breaker_spec.md`  
Task: `tasks/active/stagnation_breaker.md`
