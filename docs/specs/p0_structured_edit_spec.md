# P0 Spec — Structured Edit Format & Edit Reliability

**Full spec**: [docs/specs/p0_structured_edit_spec.html](p0_structured_edit_spec.html)  
**Priority**: P0 Critical | **Effort**: 7h | **Task**: [sota_improvements_task.html](../tasks/active/sota_improvements_task.html)

## What
Switch from SEARCH/REPLACE text block parsing to JSON structured edits. Enforce read-before-write. Detect multiple matches before applying.

## Steps (7)
1. Add EditInstruction/EditRequest/EditResult dataclasses (0.5h)
2. Add apply_edits() + multi-match detection to Verifier (2h)
3. Add _parse_json_edits() + fallback _parse_response() to Worker (1.5h)
4. Update _build_prompt() with JSON output format instruction (0.5h)
5. Update _get_file_context() for full-file-when-small logic (1h)
6. Update orchestrator to read full file before execute_task() (0.5h)
7. Write tests/test_structured_edit.py (14 tests) (1h)

## Files
- scaffold/agent/worker.py — main changes
- scaffold/agent/verifier.py — new apply_edits()
- scaffold/agent/orchestrator.py — read file before call
- scaffold/agent/models.py — new dataclasses
- tests/test_structured_edit.py — new test file
