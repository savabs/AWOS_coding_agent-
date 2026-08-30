---
title: "Task: Held-out evaluation corpus v1"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/evaluation
  - layer/agent-workflow
---

# Task: Held-out evaluation corpus v1

**Status:** active  
**Phase:** 1  
**Research:** [[evaluation_holdout_corpus_v1]]  
**Spec:** [[evaluation_holdout_corpus_v1_spec]]  
**Started:** 2026-07-30  
**Completed:** —

## Context
Create a fail-closed registry for private/held-out evaluation metadata. The repository will store schemas and validation tests, not private task contents. The registry must prevent accidental overlap with public fixtures, require stable fixture hashes and environment identity, and support immutable baseline/candidate references.

## Steps
- [x] 1.1 Define a versioned corpus manifest schema → `9 passed` in `tests/test_evaluation_corpus.py`.
- [x] 1.2 Reject public fixture paths in holdout manifests → public path and traversal tests pass.
- [x] 1.3 Require fixture hashes and environment identity → missing metadata tests fail closed.
- [x] 1.4 Require immutable baseline/candidate report references → digest validation passes.
- [x] 1.5 Add a checked-in example manifest with no private task data → example loads successfully.
- [x] 1.6 Run focused evaluator tests → `9 + 13 passed`.
- [x] 1.7 Run scoped documentation lint and write checkpoint → scoped lint clean; checkpoint follows.

## Blockers
- Actual private fixtures and credentialed agent runs are intentionally external to this repository and require explicit access/budget.

## Related
- [[evaluation_holdout_corpus_v1_spec]]
- [[evaluation_holdout_corpus_v1]]

## Evidence
- `python3 -m pytest -q tests/test_evaluation_corpus.py` → 9 passed.
- `python3 -m pytest -q tests/test_evaluation_v1.py` → 13 passed.
- `python3 scripts/obsidian_lint.py --files ...` → clean for four new Markdown artifacts.
