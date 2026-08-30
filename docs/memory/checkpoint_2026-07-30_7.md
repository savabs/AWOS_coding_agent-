---
title: "Checkpoint: 2026-07-30 three-task holdout smoke proof"
tags:
  - doc/checkpoint
  - phase/1
  - topic/evaluation
---

# Checkpoint — Three-task holdout smoke proof

Immutable continuation checkpoint after [[checkpoint_2026-07-30_6]].

## Completed
- Implemented and completed [[holdout_smoke_corpus_v1]].
- Added `scripts/run_holdout_smoke.py`.
- Added `tests/test_holdout_smoke.py`.
- Updated `docs/evaluation_holdout_v1.md` with the smoke command and warning.
- Updated `memories/repo/project_structure.md`.

## Smoke corpus
The script creates three synthetic tasks under an explicitly supplied root:
- `smoke_add`
- `smoke_greet`
- `smoke_total`

It runs:
1. Deterministic baseline callbacks that leave each bug unchanged.
2. Deterministic candidate callbacks that apply known fixes.
3. The real `EvaluationRunner` for acceptance and safety evidence.
4. Digest-bound baseline/candidate bundles and a holdout manifest.
5. The real `run_holdout_pipeline.py` comparison.

The script refuses to overwrite an existing corpus and prints `SMOKE_ONLY`.

## Evidence
- `python3 -m pytest -q tests/test_holdout_smoke.py` → 2 passed.
- Smoke command on `/tmp/awos-smoke-debug` → all three baseline reports had `pass_at_1=0.0`; all three candidate reports had `pass_at_1=1.0`; comparison `regressed=false`; exit zero.
- Existing focused proof → 40 passed.
- Combined current proof → 42 passed.
- Scoped Obsidian lint → clean.

## Failure and fix
The first version used `numbers.py` for the total task. That shadowed Python’s standard-library `numbers` module and caused pytest startup failure inside the isolated workspace. The fixture was renamed to `aggregate.py`; no evaluator code or acceptance gate was weakened.

## Critical interpretation
This is a deterministic plumbing proof, not a live coding-agent capability evaluation. The candidate callback contains the known fix and is not an LLM/orchestrator. Do not include this result in capability metrics or claim Cursor/Hermes parity from it.

## Next steps
1. Replace deterministic callbacks with an authorized live orchestrator callback.
2. Use real private tasks and repository snapshots outside Git.
3. Preserve baseline/candidate bundles and run the same pipeline.
4. Compare real holdout results without tuning on the holdout set.

## Related
- [[holdout_smoke_corpus_v1]]
- [[holdout_pipeline_v1]]
- [[holdout_comparison_cli_v1]]
- [[checkpoint_2026-07-30_6]]
