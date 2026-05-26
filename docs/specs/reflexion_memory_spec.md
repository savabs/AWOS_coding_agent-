---
title: Spec — ReflexionMemory (Phase 5B)
date: 2026-05-19
phase: 5B
status: ready-to-implement
related:
  - docs/research/reflexion_memory.html
  - scaffold/agent/error_pattern_store.py (new)
  - scaffold/agent/self_correction.py (modified)
  - scaffold/agent/worker.py (modified)
  - scaffold/agent/orchestrator.py (modified)
---

## Goal
Add persistent verbal critique memory: capture *why* tasks fail as natural-language
critiques, store them cross-session, and inject them into retry prompts to prevent
the same mistake repeating.

## Affected Files
- NEW: `scaffold/agent/error_pattern_store.py`
- MODIFY: `scaffold/agent/self_correction.py` — add `generate_critique()` using cheap LLM
- MODIFY: `scaffold/agent/worker.py` — prepend injected critiques to prompt
- MODIFY: `scaffold/agent/orchestrator.py` — wire store + inject on failure

## Interface Contract
```python
# ErrorPattern dataclass
@dataclass
class ErrorPattern:
    task_id: str
    file_path: str
    error_type: str      # ErrorClass name from SelfCorrectionEngine
    error_msg: str       # raw error string (truncated to 300 chars)
    critique: str        # LLM-generated verbal critique (1-3 sentences)
    timestamp: str       # ISO 8601

# ErrorPatternStore
class ErrorPatternStore:
    def save(self, pattern: ErrorPattern) -> None: ...
    def retrieve(self, file_path: str, error_type: str, top_n: int = 3) -> list[ErrorPattern]: ...
    def summary(self) -> dict: ...
```

## Key Design Decisions
- Storage: `.awos/error_patterns.jsonl` append-only, same as reward_store.
- Critique generation: one cheap LLM call (Gemini Flash) per failure only.
- Retrieval: exact match on file_path + error_type, sorted by recency, top-3.
- Injection: `[PAST CRITIQUE]` block prepended inside worker prompt system section.
- Cap: max 3 critiques injected, max 100 tokens each (truncated).
- Fallback: if LLM critique call fails, fall back to static SelfCorrectionEngine hint.

## Implementation Steps (9 atomic)
1. Create `scaffold/agent/error_pattern_store.py` with `ErrorPattern`, `ErrorPatternStore`.
2. Add `generate_critique(task, error, error_class) -> str` to `SelfCorrectionEngine`.
3. Wire `generate_critique()` call in `Orchestrator._execute_single_task` on worker failure.
4. Wire `ErrorPatternStore.save()` after critique is generated.
5. Add `ErrorPatternStore.retrieve()` to pull relevant critiques before retry.
6. Inject retrieved critiques into `task['past_critiques']` dict key.
7. Modify `Worker.execute_task()` to prepend `[PAST CRITIQUE]` blocks if present.
8. Write `tests/test_reflexion_memory.py` (store, retrieve, generate, inject, integration).
9. Run full test suite — zero regressions.

## Edge Cases
- No past critiques: worker prompt unchanged, no overhead.
- LLM critique call times out: log warning, fall back to static hint.
- Critique is stale (old file version): still useful — mentions error class + approach.
- Circular loop: critique injected but still fails → pass_rate penalises LinUCB, escalates model.
