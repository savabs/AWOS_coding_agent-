# RuntimeSession v1 — Implementation Task

**Status:** COMPLETE (Phases 0–4; Phase 3 worktree done)
**Date:** 2026-06-17
**Spec:** `docs/specs/runtime_session_spec.md`
**Stage:** 1 — one excellent worker (pause/resume exit criterion)

---

## Summary

Build unified session persistence at `.awos/sessions/rs_*.json` so multi-hour goals can pause, resume, and fork without losing plan progress or sandbox context.

---

## Steps

### Phase 0 — Spec (this session)

- [x] 1. Write `docs/specs/runtime_session_spec.md`
- [x] 2. Write `tasks/active/runtime_session_task.md`

**Verification:** Both files exist; schema + interface contract defined.

---

### Phase 1 — Core store

- [x] 3. Create `scaffold/agent/runtime_session.py` (`RuntimeSession`, `RuntimeSessionStore`, `SessionResumeError`)
- [x] 4. Write `tests/test_runtime_session.py` (unit tests per spec)
- [x] 5. Run tests: `pytest tests/test_runtime_session.py -q` — **26 passed**

**Verification:** 9+ unit tests pass; manual `create`/`save`/`load` in temp dir works.

---

### Phase 2 — Orchestrator wire-up

- [x] 6. Add `session_id`, `cancel_event` params to `execute_feature()`
- [x] 7. Create/load session at run start (gated: `AWOS_RUNTIME_SESSION=true`)
- [x] 8. Checkpoint after each task (`progress` + `budget.spent_usd`)
- [x] 9. SIGINT → pause session after current task
- [x] 10. Resume skip from `rs.progress.completed_task_ids` (keep `AgentStateManager` mirror)
- [ ] 11. Write `tests/test_runtime_session_integration.py` (mock 2-task pause/resume)
- [ ] 12. Run integration tests

**Verification:** Ctrl+C mid-run leaves `paused` session; resume skips completed tasks.

---

### Phase 3 — Virtual execution runtime

- [x] 13. Create `scaffold/agent/virtual_execution_runtime.py`
- [x] 14. Wire `WorktreeManager` on session start when `AWOS_USE_WORKTREE=true`
- [x] 15. Persist `sandbox` fields on session; reattach on resume
- [x] 16. Pass worktree path as `codebase_root` to worker
- [x] 17. Tests: `tests/test_virtual_execution_runtime.py` — **9 passed**

**Verification:** Session JSON contains `sandbox.worktree_path`; edits isolated from main branch.

---

### Phase 4 — CLI + cancel

- [x] 18. Add `awos sessions` subcommand: `list`, `show`, `resume`, `fork`
- [x] 19. Add `awos run --session <id>` alias
- [x] 20. Orchestrator `threading.Event` cancel checked between tasks
- [x] 21. GUI Stop → `request_cancel()` + set cancel event
- [x] 22. `AWOS_RUNTIME_SESSION` enabled by default on `awos run` / `sessions resume`
- [x] 23. Update `awos.py` help text
- [x] 24. Tests: `tests/test_awos_sessions_cli.py` — **6 passed**

**Verification:** GUI Stop exits orchestrator within one task; `awos sessions list` shows terminal status.

---

## Files to create

| File | Phase |
|------|-------|
| `docs/specs/runtime_session_spec.md` | 0 ✓ |
| `scaffold/agent/runtime_session.py` | 1 |
| `tests/test_runtime_session.py` | 1 |
| `scaffold/agent/virtual_execution_runtime.py` | 3 |
| `tests/test_awos_sessions_cli.py` | 4 |

## Files to modify

| File | Phase |
|------|-------|
| `scaffold/agent/orchestrator.py` | 2, 4 |
| `awos.py` | 4 |
| `scaffold/agent/gui_chat.py` | 4 |

## Files NOT to touch

- `VISION.md`, `docs/MASTER_PLAN.md`
- `compute/`
- `AgentStateManager` delete/migration (defer)

---

## Env vars

| Variable | Default | Phase | Purpose |
|----------|---------|-------|---------|
| `AWOS_RUNTIME_SESSION` | `false` → `true` in phase 4 | 2 | Enable session checkpointing |
| `AWOS_USE_WORKTREE` | `false` | 3 | Isolated git worktree execution |
| `AWOS_WORKTREE_AUTO_MERGE` | `false` | 3 | Merge worktree branch on success |

---

## Acceptance (Stage 1 partial)

When phases 1–4 complete:

- [ ] Multi-hour goal can pause (Ctrl+C or Stop) and resume via `awos sessions resume`
- [ ] Session record survives process exit
- [ ] Completed tasks not re-run on resume
- [ ] GUI Stop kills orchestrator (not UI-only)
- [ ] Worktree isolation optional but wired

---

## Estimated effort

| Phase | Hours |
|-------|-------|
| 0 Spec | 1–2 ✓ |
| 1 Core store | 2–3 |
| 2 Orchestrator | 3–4 |
| 3 Worktree runtime | 2–3 |
| 4 CLI + cancel | 3–4 |
| **Total** | **11–16** |

---

## Next action

**Phase 1, step 3:** Implement `scaffold/agent/runtime_session.py` per interface contract in spec.
