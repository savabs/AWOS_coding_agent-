---
name: awos-kernel-change
description: Checklist for editing scaffold/agent kernel modules (orchestrator, worker, verifier, routing, learning). Use when modifying orchestrator.py, worker.py, reward_store, ml_router, or any scaffold/agent hot path.
---

# AWOS Kernel Change Checklist

## Before editing

1. Read `docs/CODEBASE_INDEX.md` for module map
2. Check `.awosignore` — hot-path files need minimal diffs
3. Confirm spec + task exist (`awos-preflight` skill)

## Invariants (never break)

- Verify → reward → learn (no learning on unverified output)
- Model routing via `escalation_engine.py` / `ml_router.py` — no hardcoded model IDs in worker
- Session state via `runtime_session.py`; isolation via `worktree.py`

## After editing

```bash
pytest tests/test_<area>.py -q --timeout=10
```

Kernel features also need live proof (`awos-live-proof` skill).

## Orchestrator-specific

See `.cursor/rules/awos-orchestrator.mdc` — loop order is sacred.
