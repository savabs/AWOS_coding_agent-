---
title: "Task: Three-task holdout smoke corpus v1"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/evaluation
  - layer/agent-workflow
---

# Task: Three-task holdout smoke corpus v1

**Status:** active  
**Phase:** 1  
**Research:** [[holdout_smoke_corpus_v1]]  
**Spec:** [[holdout_smoke_corpus_v1_spec]]  
**Started:** 2026-07-30  
**Completed:** —

## Context
Create a tiny offline smoke corpus to prove the real runner → bundle → manifest → comparison flow. It uses deterministic callbacks, not a model, and must never be described as agent capability evidence.

## Steps
- [x] 1.1 Define three synthetic task snapshots → each has an evaluator-owned failing test.
- [x] 1.2 Run baseline callbacks → baseline bundle contains three failed task reports.
- [x] 1.3 Run candidate callbacks → candidate bundle contains three passing task reports.
- [x] 1.4 Write digest-bound manifest → hashes and environment identity validate.
- [x] 1.5 Run the real offline pipeline → comparison exits zero with no regression.
- [x] 1.6 Add smoke tests and docs → rerun safety and smoke-only labeling pass.
- [x] 1.7 Run focused tests/lint and write checkpoint → 42 focused tests pass; scoped lint clean.

## Blockers
- This is a harness proof only; real private tasks and live agent callbacks remain future work.

## Related
- [[holdout_smoke_corpus_v1_spec]]
- [[holdout_smoke_corpus_v1]]

## Evidence
- `python3 -m pytest -q tests/test_holdout_smoke.py` → 2 passed.
- Smoke command → three tasks baseline `0.0`, candidate `1.0`, `regressed=false`.
- Existing evaluator proof → 40 passed.
- Scoped Obsidian lint → clean.

## Failure lesson
The first fixture used `numbers.py`, which shadowed Python's standard-library `numbers` module and caused pytest startup failure. Renamed it to `aggregate.py`; no evaluator code was weakened.
