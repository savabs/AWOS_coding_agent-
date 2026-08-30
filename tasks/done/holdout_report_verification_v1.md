---
title: "Task: Held-out report verification v1"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/evaluation
  - layer/agent-workflow
---

# Task: Held-out report verification v1

**Status:** active  
**Phase:** 1  
**Research:** [[holdout_report_verification_v1]]  
**Spec:** [[holdout_report_verification_v1_spec]]  
**Started:** 2026-07-30  
**Completed:** —

## Context
Verify baseline and candidate report bundles for a private held-out corpus. The verifier checks artifact digests, corpus/environment identity, exact task coverage, and per-task hard-gate regressions. It must produce a deterministic comparison and fail closed on tampering or incomplete evidence.

## Steps
- [x] 1.1 Define a versioned report-bundle schema → deterministic round-trip test passes.
- [x] 1.2 Verify report artifact digest → tampered artifact test fails closed.
- [x] 1.3 Verify corpus/environment identity → mismatch tests fail closed.
- [x] 1.4 Verify exact task coverage → missing/extra task tests fail closed; duplicate IDs are rejected by schema.
- [x] 1.5 Compare per-task correctness and safety → regression result is hard-gated.
- [x] 1.6 Add deterministic comparison serialization → JSON output is stable.
- [x] 1.7 Run focused tests and scoped lint → 27 tests passed; scoped lint clean; checkpoint written.

## Blockers
- No private baseline or candidate artifacts are available; tests use synthetic `EvaluationReport` bundles only.

## Related
- [[holdout_report_verification_v1_spec]]
- [[holdout_report_verification_v1]]

## Evidence
- `python3 -m pytest -q tests/test_evaluation_corpus.py tests/test_evaluation_v1.py` → 27 passed.
- `python3 scripts/obsidian_lint.py --files ...` → clean for four new Markdown artifacts.
