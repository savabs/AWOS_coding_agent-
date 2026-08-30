---
title: "Checkpoint: 2026-07-30 evaluation holdout corpus"
tags:
  - doc/checkpoint
  - phase/1
  - topic/evaluation
---

# Checkpoint — Held-out evaluation corpus v1

This is an immutable continuation checkpoint after [[checkpoint_2026-07-30]].

## Completed
- Resumed from the dated checkpoint; confirmed `session_warmup.py` still reports an older checkpoint and did not treat it as authoritative.
- Researched held-out evaluation guidance from SWE-bench, OpenHands, OpenHands Benchmarks, and OpenAI evaluation best practices.
- Created and completed [[evaluation_holdout_corpus_v1]].
- Added `scaffold/agent/evaluation_corpus.py` with fail-closed `HoldoutTaskRef`, `ReportRef`, and `HoldoutCorpusManifest` contracts.
- Added `tests/test_evaluation_corpus.py` with 9 tests.
- Added metadata-only `tests/fixtures/evaluation_v1/holdout_manifest.example.json`.
- Added `docs/evaluation_holdout_v1.md` operator guidance.
- Updated `memories/repo/project_structure.md`.

## Evidence
- `python3 -m pytest -q tests/test_evaluation_corpus.py` → 9 passed.
- `python3 -m pytest -q tests/test_evaluation_v1.py` → 13 passed.
- Combined focused evaluator proof → 22 passed.
- Scoped Obsidian lint for the four new Markdown artifacts → clean.

## Contract behavior
- Only `split="holdout"` is accepted.
- Task IDs must be unique.
- Public fixture-root references and traversal references are rejected.
- Fixture, repository, and report SHA-256 digests are required.
- Environment identity is required.
- Baseline and candidate report references must be digest-bound.
- Private fixture contents are intentionally not stored in this repository.

## Important limitations
- The example manifest is a contract proof, not a real capability result.
- No private held-out tasks are available in this worktree.
- No credentialed live agent run was performed.
- The evaluator still needs a real separately controlled holdout corpus, immutable baseline artifacts, and candidate comparison runs.
- Full repository Obsidian lint remains blocked by previously known legacy errors.

## Next steps
1. Provision a separately controlled private holdout corpus and compute fixture/repository hashes.
2. Run an authorized baseline with explicit provider, budget, and environment metadata.
3. Run a candidate comparison without tuning on holdout results.
4. Add CI gates for correctness regression, safety regression, and missing evidence.

## Related
- [[evaluation_holdout_corpus_v1]]
- [[evaluation_holdout_corpus_v1_spec]]
- [[self_learning_reward_evaluation_v1]]
- [[checkpoint_2026-07-30]]
