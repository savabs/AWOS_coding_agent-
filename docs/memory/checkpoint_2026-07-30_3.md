---
title: "Checkpoint: 2026-07-30 held-out report verification"
tags:
  - doc/checkpoint
  - phase/1
  - topic/evaluation
---

# Checkpoint — Held-out report verification v1

Immutable continuation checkpoint after [[checkpoint_2026-07-30_2]].

## Completed
- Researched the next evaluation gap using SWE-bench, OpenHands, OpenHands Benchmarks, and OpenAI evaluation guidance.
- Created and completed [[holdout_report_verification_v1]].
- Extended `scaffold/agent/evaluation_corpus.py` with `HoldoutReportBundle`, `HoldoutTaskComparison`, `HoldoutComparison`, artifact digest verification, identity verification, exact coverage verification, and per-task comparison.
- Added tamper, identity mismatch, coverage mismatch, round-trip, and improvement tests to `tests/test_evaluation_corpus.py`.
- Updated the operator guide and project structure.

## Evidence
- `python3 -m pytest -q tests/test_evaluation_corpus.py tests/test_evaluation_v1.py` → 27 passed.
- Scoped Obsidian lint for four new Markdown artifacts → clean.
- Editor diagnostics: unresolved intra-package import fixed; remaining warnings are legacy typing/style warnings, with no runtime test failures.

## Verified behavior
- Exact artifact bytes are SHA-256 checked before parsing.
- Baseline and candidate bundles must match manifest corpus and environment identity.
- Bundle task IDs must exactly equal manifest task IDs.
- Duplicate task IDs are rejected.
- Per-task correctness and safety regressions cannot be hidden by other tasks’ improvements.
- Comparison output is deterministic and contains per-task results plus aggregate hard-regression flags.
- The verifier receives local artifact paths and never fetches external references or executes an agent.

## Limitations
- No real private baseline/candidate artifacts are available.
- No provider or live agent execution occurred.
- No private task contents are stored in the repository.
- A separate authorized runner still needs to produce real bundles and invoke this verifier.
- Full repository lint remains blocked by known legacy errors.

## Next steps
1. Add an operator CLI that accepts a manifest plus authorized local baseline/candidate artifact paths.
2. Provision a private holdout corpus outside Git.
3. Run a credentialed baseline and candidate comparison.
4. Add CI gates for correctness regression, safety regression, and missing evidence.

## Related
- [[holdout_report_verification_v1]]
- [[evaluation_holdout_corpus_v1]]
- [[self_learning_reward_evaluation_v1]]
- [[checkpoint_2026-07-30_2]]
