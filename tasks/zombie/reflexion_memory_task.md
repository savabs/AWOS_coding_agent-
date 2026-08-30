---
title: Task — ReflexionMemory (Phase 5B)
date: 2026-05-19
phase: 5B
status: pending
related:
  - docs/specs/reflexion_memory_spec.html
  - docs/research/reflexion_memory.html
  - scaffold/agent/error_pattern_store.py (new)
  - scaffold/agent/self_correction.py (modified)
  - scaffold/agent/worker.py (modified)
  - scaffold/agent/orchestrator.py (modified)
---

## Goal
Add persistent verbal critique memory: save why tasks fail as LLM-generated critiques,
retrieve by (file_path, error_type), inject into retry prompts to prevent repeat mistakes.

## Steps (8 atomic)
1. Create `error_pattern_store.py` — ErrorPattern dataclass + JSONL store
2. Add `generate_critique()` to SelfCorrectionEngine (cheap LLM call)
3. Wire critique generation + save() in Orchestrator after worker failure
4. Wire retrieve() + inject `task["past_critiques"]` before retry
5. Modify Worker to prepend `[PAST CRITIQUE]` blocks to prompt
6. Add `_cheap_call()` helper to Orchestrator
7. Write `tests/test_reflexion_memory.py` (~14 tests)
8. Full test suite — zero regressions

## Key Decisions
- JSONL (not SQLite) — consistent with RewardStore, crash-safe
- Exact match retrieval (not vector search) — sufficient until 500+ critiques
- Critique stored after failure, injected on next attempt (not same attempt)
