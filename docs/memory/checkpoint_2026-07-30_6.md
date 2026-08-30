---
title: "Checkpoint: 2026-07-30 offline holdout pipeline"
tags:
  - doc/checkpoint
  - phase/1
  - topic/evaluation
---

# Checkpoint — Offline holdout pipeline v1

Immutable continuation checkpoint after [[checkpoint_2026-07-30_5]].

## Completed
- Implemented and completed [[holdout_pipeline_v1]].
- Added `scripts/run_holdout_pipeline.py`.
- Added `tests/test_holdout_pipeline.py` with subprocess tests.
- Updated `docs/evaluation_holdout_v1.md` with the one-command workflow.
- Updated `memories/repo/project_structure.md`.

## Pipeline behavior
- Defaults to `.awos/holdout_private/`.
- Defaults to `manifest.json`, `reports/baseline/baseline.json`, `reports/candidate/candidate.json`, and `comparisons/comparison.json` below that root.
- Allows explicit path overrides only when all paths remain inside the private root.
- Rejects missing artifacts and public/outside-root paths.
- Delegates comparison to `scripts/verify_holdout_comparison.py`; no duplicate grading logic.
- Does not create tasks, copy repositories, call providers, fetch remote references, or mutate the manifest.
- Preserves verifier exit semantics: zero only for verified non-regression; one for regression or validation failure.

## Evidence
- `python3 -m pytest -q tests/test_holdout_pipeline.py` → 5 passed.
- Existing setup/comparison/evaluator proof → 35 passed.
- Combined focused proof → 40 passed.
- Scoped Obsidian lint → clean.

## Important limitation
The local workspace still contains only the metadata template and README. Real private task fixtures, repository snapshots, manifest, baseline bundle, and candidate bundle must be supplied separately by the operator. No live evaluation occurred.

## Next steps
1. Add real private task/repository artifacts outside Git.
2. Fill `.awos/holdout_private/manifest.json` with computed hashes and environment identity.
3. Produce baseline and candidate report bundles through an authorized workflow.
4. Run `python3 scripts/run_holdout_pipeline.py --root .awos/holdout_private`.
5. Preserve comparison output and inspect any non-zero regression result.

## Related
- [[holdout_pipeline_v1]]
- [[private_holdout_setup_v1]]
- [[holdout_comparison_cli_v1]]
- [[checkpoint_2026-07-30_5]]
