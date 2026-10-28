# Spec — Stagnation Breaker

**Research:** [`docs/research/stagnation_breaker.md`](../research/stagnation_breaker.md)  
**Task:** [`tasks/active/stagnation_breaker.md`](../../tasks/active/stagnation_breaker.md)

---

## Scope

Add stagnation detection to `orchestrator.execute_feature()` main loop.

Trip when same (failure_kind, error, file) signature repeats ≥ 3 times → pause session with reason `STAGNATION`.

---

## Files to touch

| File | Change |
|------|--------|
| `scaffold/agent/orchestrator.py` | Add `_failure_history` deque; check after each failed task; trip logic |
| `scaffold/agent/runtime_session.py` | Add `pause_reason: Optional[str]` field to `RuntimeSession` |
| `tests/test_stagnation_breaker.py` | Unit + integration tests |

---

## Signature function

```python
import hashlib
import re
from collections import deque

def _failure_signature(failure_kind: str, error: str, file_path: str) -> str:
    """
    Hash (failure_kind, normalized_error, file) → 16-char hex.
    
    Normalization: digits → N, collapse whitespace.
    """
    norm = re.sub(r'\d+', 'N', error[:200].lower())
    norm = re.sub(r'\s+', ' ', norm).strip()
    payload = f"{failure_kind}:{norm}:{file_path}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

---

## Orchestrator changes

### Init

```python
self._failure_history: deque[tuple[str, Any]] = deque(maxlen=10)
self._stagnation_threshold = int(os.getenv("AWOS_STAGNATION_THRESHOLD", "3"))
```

### Main loop (after task execution, before decompose)

```python
# After each failed task result
if not r["success"]:
    sig = _failure_signature(
        r.get("failure_kind", "unknown"),
        r.get("verify_error") or r.get("worker_error", "")[:200],
        r["task"].get("path") or r["task"].get("file", ""),
    )
    self._failure_history.append((sig, r["task_id"]))
    
    # Check for stagnation
    sig_count = sum(1 for s, _ in self._failure_history if s == sig)
    if sig_count >= self._stagnation_threshold:
        if self._runtime_session and self._runtime_store:
            self._runtime_session.pause_reason = "STAGNATION"
            print(f"\n[BREAKER] Stagnation detected: same error {sig_count}× (signature {sig[:8]})")
            print(f"[BREAKER] Auto-pausing session. Review task {r['task_id']} or skip.")
            self._runtime_store.finalize(self._runtime_session, SessionStatus.PAUSED)
            # Set break flag to exit main loop cleanly
            self._pause_requested = True
```

---

## Session schema update

`RuntimeSession` dataclass:

```python
pause_reason: Optional[str] = None  # "STAGNATION", "BUDGET", "USER"
```

When `awos sessions show rs_<id>` prints a paused session, include `pause_reason` if set.

---

## Config env vars

| Var | Default | Meaning |
|-----|---------|---------|
| `AWOS_STAGNATION_THRESHOLD` | `3` | How many repeats → trip |
| `AWOS_STAGNATION_WINDOW` | `10` | Deque maxlen (recent history) |

---

## Test coverage

1. **Unit:** `_failure_signature()` hashing edge cases
2. **Integration:** orchestrator with mock worker returning same error 3×
3. **Live proof:** `./scripts/demo_stagnation_breaker.sh` — see `docs/stagnation_breaker_proof.md`

---

## Live proof

**Command:**
```bash
./scripts/demo_stagnation_breaker.sh
```

**Watch for:**
- `[BREAKER] Stagnation detected`
- `[BREAKER] Task 1 stuck — auto-pausing session`
- `awos sessions show rs_<id>` → `Status: paused (STAGNATION)`

---

## Success criteria

- [ ] Orchestrator tracks failure history in deque
- [ ] Trip on 3rd repeat of same signature
- [ ] Session paused with `pause_reason="STAGNATION"`
- [ ] User-visible message explains which task/error
- [ ] Tests pass
- [ ] One live proof (gauntlet or mission)
