---
title: "Task: Held-out comparison CLI v1"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/evaluation
  - layer/agent-workflow
---

# Task: Held-out comparison CLI v1

**Status:** active  
**Phase:** 1  
**Research:** [[holdout_comparison_cli_v1]]  
**Spec:** [[holdout_comparison_cli_v1_spec]]  
**Started:** 2026-07-30  
**Completed:** —

## Context
Add an operator-only CLI that verifies and compares two locally supplied holdout report bundles against a manifest. The command must print deterministic JSON, optionally persist it, and exit non-zero for malformed evidence or hard correctness/safety regression.

## Steps
- [x] 1.1 Add CLI argument parsing → required paths and optional output are accepted.
- [x] 1.2 Load and validate manifest → malformed manifest exits with a clear error.
- [x] 1.3 Compare authorized local bundles → deterministic JSON is printed/written.
- [x] 1.4 Enforce exit semantics → regression and validation errors exit non-zero; success exits zero.
- [x] 1.5 Add CLI tests → 5 subprocess tests pass.
- [x] 1.6 Update operator guide → command and evidence interpretation documented.
- [x] 1.7 Run focused tests/lint and write checkpoint → 32 focused tests pass; scoped lint clean.

## Blockers
- Real private artifacts and live credentials remain external; CLI tests use synthetic local bundles.

## Related
- [[holdout_comparison_cli_v1_spec]]
- [[holdout_comparison_cli_v1]]

## Evidence
- `python3 -m pytest -q tests/test_holdout_comparison_cli.py` → 5 passed.
- `python3 -m pytest -q tests/test_evaluation_corpus.py tests/test_evaluation_v1.py` → 27 passed.
- Scoped Obsidian lint → clean.
