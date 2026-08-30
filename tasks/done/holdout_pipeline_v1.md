---
title: "Task: Offline holdout pipeline v1"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/evaluation
  - layer/agent-workflow
---

# Task: Offline holdout pipeline v1

**Status:** active  
**Phase:** 1  
**Research:** [[holdout_pipeline_v1]]  
**Spec:** [[holdout_pipeline_v1_spec]]  
**Started:** 2026-07-30  
**Completed:** —

## Context
Add a thin offline operator pipeline around the existing holdout comparison CLI. It validates the local workspace inputs, invokes the existing comparison command, stores the comparison under the private workspace, and never runs a provider or creates private data.

## Steps
- [x] 1.1 Add pipeline argument parsing → root and artifact options are validated.
- [x] 1.2 Validate private workspace boundary → outside-root paths fail closed.
- [x] 1.3 Delegate to comparison verifier → existing hard-gated semantics remain authoritative.
- [x] 1.4 Add pipeline subprocess tests → 5 tests pass for success/regression/tamper/boundary/missing artifacts.
- [x] 1.5 Update operator runbook → one-command local workflow documented.
- [x] 1.6 Run focused tests/lint and write checkpoint → 40 focused tests pass; scoped lint clean.

## Blockers
- Real private data and credentialed live runs remain external and are not created by this task.

## Related
- [[holdout_pipeline_v1_spec]]
- [[holdout_pipeline_v1]]

## Evidence
- `python3 -m pytest -q tests/test_holdout_pipeline.py` → 5 passed.
- Existing evaluator proof → 35 passed.
- Scoped Obsidian lint → clean.
