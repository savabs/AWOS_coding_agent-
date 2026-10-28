# Checkpoint — Stage 1 Hardening

**Date:** 2026-06-18  
**Plan:** Stage 1 Hardening (1 week)

---

## Summary

Stage 1 hardening batch complete: worktree cold-start fixed, multi-step pause/resume proved, error typing expanded, regression tests green.

---

## 1. Worktree cold-start fix

**Root causes:**
- `SymbolIndex` excluded all files under `.awos/worktrees/` because absolute path parts contained `.awos`.
- Fresh git worktrees only checkout committed HEAD — untracked files on main working tree were missing.

**Fixes:**
- `should_index_py_file()` — filter using path parts **relative to codebase root** ([`symbol_index.py`](/home/becmachlean/2024/projects/AWOS_coding_agent/scaffold/agent/symbol_index.py)).
- `sync_worktree_from_repo()` — mirror missing files from main tree into worktree after create ([`virtual_execution_runtime.py`](/home/becmachlean/2024/projects/AWOS_coding_agent/scaffold/agent/virtual_execution_runtime.py)).
- Persist `codebase_root` on session after worktree enter ([`orchestrator.py`](/home/becmachlean/2024/projects/AWOS_coding_agent/scaffold/agent/orchestrator.py)).

**Tests:** `tests/test_symbol_index_worktree.py`, `tests/test_virtual_execution_runtime.py` (15 tests).

**Live check:** SymbolIndex on existing worktree → `10 files, 71 symbols` (was `0 files`).

---

## 2. Multi-step pause/resume

**Fixes:**
- Pause/cancel checks between sequential tasks in `_run_task_batch`.
- After partial batch, honor `_pause_requested` before decomposition loop.

**Proof:** `tests/test_pause_resume_orchestrator.py` — 3-task plan, pause after task 1, resume completes tasks 2–3; session `PAUSED` → `COMPLETED` with `completed_task_ids: [1,2,3]`.

---

## 3. Error pattern typing

**Fixes:**
- New `ErrorClass` values: `FILE_NOT_FOUND`, `API_ERROR`, `VERIFY_FAIL`, `WORKER_FAIL`.
- Ordered classifiers so path/API/verify failures are not lumped into `UNKNOWN`.
- `_persist_worker_failure_pattern` saves rule-based critique when LLM critique is empty.

**Tests:** extended `tests/test_self_correction.py`.

**Note:** Legacy `.awos/error_patterns.jsonl` still dominated by historical `UNKNOWN`; new failures should classify better.

---

## 4. Prove-it test batch

```
pytest tests/test_symbol_index_worktree.py \
       tests/test_virtual_execution_runtime.py \
       tests/test_pause_resume_orchestrator.py \
       tests/test_self_correction.py -q
```

**Result:** 44 passed, 1 deselected.

---

## Stage 1 exit criteria (updated)

| Criterion | Status |
|-----------|--------|
| Worktree isolation | Fixed + tested |
| Pause/resume multi-step | Proved (integration test) |
| Learning compounds | v1 active (prior session) |
| Error typing | Improved for new failures |
| Multi-hour live run | Not yet |
| Verify-fail → replan | Not yet |
| Benchmark vs raw API | Not yet |

---

## Next

1. Live worktree run (cheap-only) to confirm no first-task file-not-found.
2. Multi-hour goal with SIGINT pause in production CLI.
3. Verify-fail → replan minimal graph mutation.
