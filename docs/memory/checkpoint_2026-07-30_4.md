---
title: "Checkpoint: 2026-07-30 holdout comparison CLI"
tags:
  - doc/checkpoint
  - phase/1
  - topic/evaluation
---

# Checkpoint — Held-out comparison CLI v1

Immutable continuation checkpoint after [[checkpoint_2026-07-30_3]].

## Completed
- Implemented and completed [[holdout_comparison_cli_v1]].
- Added `scripts/verify_holdout_comparison.py` with required manifest/baseline/candidate arguments and optional output.
- Added subprocess tests for success, hard regression, tampering, missing arguments, and input overwrite protection.
- Updated `docs/evaluation_holdout_v1.md` with the operator command and exit semantics.
- Updated `memories/repo/project_structure.md`.

## CLI behavior
- Uses only explicit local artifact paths; never fetches external references.
- Validates manifest and both report digests through `compare_holdout_report_bundles`.
- Prints deterministic comparison JSON.
- Writes optional output atomically after successful comparison.
- Refuses output paths equal to manifest/baseline/candidate inputs.
- Returns `0` only when evidence verifies and no correctness/safety regression exists.
- Returns `1` for tampering, malformed evidence, identity/coverage mismatch, or regression.
- Missing required arguments use argparse’s non-zero exit (`2`).

## Evidence
- `python3 -m pytest -q tests/test_holdout_comparison_cli.py` → 5 passed.
- `python3 -m pytest -q tests/test_evaluation_corpus.py tests/test_evaluation_v1.py` → 27 passed.
- Combined focused proof → 32 passed.
- Scoped Obsidian lint → clean.
- Diagnostics: no errors in the CLI; warnings only.

## Limitations
- No real private baseline/candidate artifacts or provider execution occurred.
- The checked-in manifests and synthetic bundles prove contracts, not agent capability.
- Full repository lint remains blocked by known legacy errors.

## Next steps
1. Provision a private corpus outside Git.
2. Produce an authorized baseline and candidate bundle.
3. Run the CLI against those artifacts and preserve its output.
4. Add CI gates for correctness regression, safety regression, and missing evidence.

## Related
- [[holdout_comparison_cli_v1]]
- [[holdout_report_verification_v1]]
- [[evaluation_holdout_corpus_v1]]
- [[checkpoint_2026-07-30_3]]
