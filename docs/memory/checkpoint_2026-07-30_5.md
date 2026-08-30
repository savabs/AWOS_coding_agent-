---
title: "Checkpoint: 2026-07-30 private holdout setup"
tags:
  - doc/checkpoint
  - phase/1
  - topic/evaluation
---

# Checkpoint — Private holdout setup v1

Immutable continuation checkpoint after [[checkpoint_2026-07-30_4]].

## Completed
- Implemented and completed [[private_holdout_setup_v1]].
- Added `.gitignore` boundary for `.awos/holdout_private/`.
- Added `scripts/init_private_holdout.py`.
- Added setup tests in `tests/test_private_holdout_setup.py`.
- Initialized this worktree’s local `.awos/holdout_private/` workspace.
- Updated operator guidance and project structure.

## Local workspace
Created, with mode `0700`:
- `.awos/holdout_private/tasks/`
- `.awos/holdout_private/repositories/`
- `.awos/holdout_private/reports/baseline/`
- `.awos/holdout_private/reports/candidate/`
- `.awos/holdout_private/comparisons/`

Created metadata-only files:
- `.awos/holdout_private/manifest.template.json`
- `.awos/holdout_private/README.md`

No private tasks, repositories, credentials, or provider calls were created.

## Evidence
- `python3 -m pytest -q tests/test_private_holdout_setup.py` → 3 passed.
- Existing focused evaluator proof → 32 passed.
- Combined current proof → 35 passed.
- `git check-ignore` confirms the private manifest is ignored.
- Scoped Obsidian lint → clean.

## Important security limitation
Git ignore is not encryption. Sensitive holdout data should use an access-controlled filesystem or volume, and report artifacts should remain digest-bound and immutable after creation.

## Next steps
1. Supply real private task fixtures and repository snapshots out of Git.
2. Fill a real manifest with computed fixture/repository hashes and environment identity.
3. Run an authorized baseline and candidate agent evaluation.
4. Run `scripts/verify_holdout_comparison.py` and preserve its output.

## Related
- [[private_holdout_setup_v1]]
- [[holdout_comparison_cli_v1]]
- [[holdout_report_verification_v1]]
- [[checkpoint_2026-07-30_4]]
