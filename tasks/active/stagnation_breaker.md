# Stagnation Breaker

**Research:** `docs/research/stagnation_breaker.md`  
**Spec:** `docs/specs/stagnation_breaker_spec.md`

**Status:** IN PROGRESS

---

## Steps

- [x] Research doc
- [x] Spec doc
- [x] Add `pause_reason` field to `RuntimeSession`
- [x] Add `_failure_signature()` helper to `orchestrator.py`
- [x] Add `_failure_history` deque + threshold to `Orchestrator.__init__`
- [x] Wire stagnation check in main loop (after failed task)
- [x] Update `awos sessions show` to display `pause_reason`
- [x] Write `tests/test_stagnation_breaker.py` (unit tests passing)
- [x] Live proof scenario ready: `./scripts/demo_stagnation_breaker.sh`

---

## Live proof ready

Run `./scripts/demo_stagnation_breaker.sh` to see:

1. Broken file with missing colon
2. Worker fails 2× on same syntax error
3. **[BREAKER]** trips after 2nd identical failure
4. Session paused with `Status: paused (STAGNATION)`

See `docs/stagnation_breaker_proof.md` for details.

---

## Live proof plan

Create a mission with intentionally broken task (e.g., syntax error that worker can't fix) × repeat via decompose → should auto-pause after 3 identical failures.
