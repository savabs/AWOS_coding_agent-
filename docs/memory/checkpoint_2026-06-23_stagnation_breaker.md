# Checkpoint — Stagnation Breaker

**Date:** 2026-06-23

---

## What was built

**Stagnation breaker** — auto-pause when same failure repeats ≥ 3 times, prevents loopmaxxing and budget burn on unattended runs.

### Files changed

| File | Change |
|------|--------|
| `scaffold/agent/runtime_session.py` | Added `pause_reason: Optional[str]` field to `RuntimeSession` |
| `scaffold/agent/orchestrator.py` | Added `_failure_signature()`, `_check_stagnation()`, deque tracking in `__init__`, trip logic in main loop |
| `awos.py` | Display `pause_reason` in `awos sessions show` |
| `tests/test_stagnation_breaker.py` | Unit tests for signature hashing + stagnation detection logic (3/6 passing — integration tests timeout on VectorMemory) |

### How it works

```
1. Task fails → hash (failure_kind, normalized_error, file_path)
2. Append to _failure_history deque (maxlen=10)
3. Count repeats of same hash
4. If count >= threshold (default 3) → pause session with reason="STAGNATION"
```

**Normalization:** digits → `N`, whitespace collapsed — so "line 10 syntax error" and "line 23 syntax error" hash to same signature.

**User-visible:**

```bash
awos sessions show rs_<id>
# Status: paused (STAGNATION)
```

---

## Config

| Env var | Default | Meaning |
|---------|---------|---------|
| `AWOS_STAGNATION_THRESHOLD` | `3` | How many repeats before trip |
| `AWOS_STAGNATION_WINDOW` | `10` | Deque size (recent failures) |

---

## Status

**Core implementation:** ✅ Done  
**Unit tests:** ✅ Passing (signature logic)  
**Integration tests:** ⚠️ Timeout (VectorMemory init slow) — skipped for now  
**Live proof:** ❌ Not run yet — need a mission with intentional stagnation

---

## Next

Live proof options:
1. Gauntlet scenario with broken task that worker can't fix
2. Long mission with task that hits same verifier error 3×

Or move to **Phase C:** `awos worker start` wrapper (higher product value than one more test).

---

## Related

- Long mission `rs_b2667c2e8eb7`: task 4 decomposed after 3 fails — would have tripped breaker if this was enabled then
- Loop engineering alignment: stagnation breaker is the "circuit breaker" pillar
