---
title: "Task: Private holdout setup v1"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/evaluation
  - layer/agent-workflow
---

# Task: Private holdout setup v1

**Status:** active  
**Phase:** 1  
**Research:** [[private_holdout_setup_v1]]  
**Spec:** [[private_holdout_setup_v1_spec]]  
**Started:** 2026-07-30  
**Completed:** —

## Context
Create a local-only holdout workspace outside Git tracking. The setup creates directories and metadata templates only; it never creates private tasks, copies repositories, stores credentials, or runs a provider.

## Steps
- [x] 1.1 Add a Git ignore boundary → `git check-ignore` confirms `.awos/holdout_private/` is ignored.
- [x] 1.2 Add a safe initializer → 3 setup tests pass; directories use mode `0700`.
- [x] 1.3 Add a metadata-only manifest template → template has no private task data.
- [x] 1.4 Add setup tests → temporary-root and existing-root safety paths pass.
- [x] 1.5 Initialize this worktree’s local directory → directories exist and are ignored.
- [x] 1.6 Run focused tests/lint and write checkpoint → 35 focused tests pass; scoped lint clean.

## Blockers
- Real private tasks, repository snapshots, and provider credentials must be supplied separately by the operator.

## Related
- [[private_holdout_setup_v1_spec]]
- [[private_holdout_setup_v1]]

## Evidence
- `python3 scripts/init_private_holdout.py` → created metadata template and README only.
- `python3 -m pytest -q tests/test_private_holdout_setup.py` → 3 passed.
- Existing evaluator proof → 32 passed.
- `git check-ignore -v .awos/holdout_private/manifest.template.json` → ignored.
- Scoped Obsidian lint → clean.
