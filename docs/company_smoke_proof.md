# Company Smoke — Live Proof Guide

## What this simulates

A developer runs Claude Code / terminal agent on a **failing test suite**:

1. Read tests to learn expected behavior  
2. Fix implementation file only  
3. Run pytest until green  

No chat UI — just goal → agent → verified outcome.

## Scenario

`tests/fixtures/benchmark/02_wrong_logic/`:

| Bug | Symptom |
|-----|---------|
| `Cache.set` evicts wrong key | `test_cache_evicts_oldest_not_newest` fails |
| `is_palindrome` wrong comparison | palindrome tests fail |

Goal text **does not** spell out the fix (unlike `task.json`).

## Run

```bash
bash scripts/demo_company_smoke.sh
# or
python3 scripts/run_company_smoke.py
```

Requires at least one LLM API key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, or `DEEPSEEK_API_KEY`). Loads `.env` from repo root if present.

**Note:** This smoke calls `ReActWorker` directly (no planner/decomposer). That isolates backend ability — the same loop `awos worker` uses per task, without multi-task rollback noise.

## Observed result (2026-06-29)

```
[REACT] read_file → edit_file → run_tests → edit_file → run_tests → finish
[SMOKE] ✓ COMPANY SMOKE PASSED — pytest green
Agent test_result: passed=3 failed=0
~22s, GPT-4o-mini
```

Fixes applied:
- `is_palindrome`: `cleaned == cleaned[::-1]`
- `Cache.set`: evict oldest via `next(iter(self._store))`

## Success markers

1. Baseline: `pytest` **fails** before AWOS  
2. During run: `[REACT]` turns with `read_file` / `edit_file` / `run_tests`  
3. After run: independent `pytest test_case.py` **passes**  
4. Console: `✓ COMPANY SMOKE PASSED`

## Failure modes

| Output | Meaning |
|--------|---------|
| `REACT FAILED` | Model/tool loop did not finish |
| `pytest still red` | Edits wrong or incomplete |
| No `run_tests` in trace | Test gate not used |

## Task checklist

- [x] Live proof: `bash scripts/demo_company_smoke.sh` — watch for `✓ COMPANY SMOKE PASSED`
