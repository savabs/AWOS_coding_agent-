---
title: "Checkpoint: 2026-07-30 AWOS evaluation and holdout readiness"
tags:
  - doc/checkpoint
  - phase/1
  - topic/evaluation
  - topic/coding-agent
---

# AWOS Checkpoint — Evaluation and Holdout Readiness

**Date:** 2026-07-30  
**Status:** immutable session handoff  
**Previous checkpoint:** [[checkpoint_2026-07-30_7]]

> This checkpoint is a historical record. Do not edit it after this session. Future corrections belong in canonical project files and a later checkpoint.

## Executive summary

AWOS now has a deterministic evaluation and private-holdout workflow strong enough to begin a carefully controlled real-agent evaluation. The infrastructure can create isolated trials, run evaluator-owned acceptance tests, detect unsafe changes, preserve trajectories, produce digest-bound baseline/candidate report bundles, compare per-task correctness and safety, and reject false success.

The latest work added a three-task offline smoke corpus that exercised the full plumbing path:

```text
synthetic task snapshots
→ EvaluationRunner
→ baseline reports
→ candidate reports
→ SHA-256 manifest
→ private-root pipeline
→ per-task comparison
```

The smoke proof passed, but it is explicitly **not** evidence that the AWOS coding agent is as capable as Cursor, Hermes, or another leading agent. Its candidate callback contains known fixes and does not call a model or orchestrator.

## Current authoritative state

- Latest authoritative checkpoint: this file, `checkpoint_2026-07-30_8.md`.
- `scripts/session_warmup.py` still reports the stale `checkpoint_session_memory_2026-05-13.md`; do not treat that output as the latest checkpoint.
- Current product phase: Phase 1 — proper coding agent.
- Current evaluation focus: replace deterministic smoke callbacks with an authorized real-orchestrator callback on real private tasks.
- Private workspace: `.awos/holdout_private/`, ignored by Git and created with restrictive permissions.
- No credentials, private task data, or live provider calls were used in this session.

## Major capabilities completed before this checkpoint

### 1. Core coding-agent proof

The focused offline coding-agent gate previously passed:

```text
5 passed
```

Covered behavior:

- Plan → edit → verify → retry → test.
- Mocked orchestrator/ReAct E2E.
- Failed-test rollback.
- No false success.
- Debug evidence generation.
- Test failures treated as terminal for the current execution rather than misleading verifier replanning.

Relevant files include:

- `scaffold/agent/orchestrator.py`
- `tests/test_test_outcome_gate.py`
- `tests/test_coding_agent_minimal_e2e.py`
- `tests/test_orchestrator_e2e.py`

### 2. Execution sandbox v1

Sandbox work completed the initial safety layer:

- Strict `SandboxPolicy`.
- Deterministic policy digest.
- Workspace containment.
- Symlink/traversal rejection.
- Approval binding.
- Process groups and timeout termination.
- Output truncation.
- Git detached worktrees and cleanup.
- Runtime session sandbox state.
- Security evidence store.
- Hostile-path tests.

Known limitations remain:

- No proven container-level isolation.
- Network/secret isolation is incomplete.
- Output truncation is after `communicate()`, not streaming.
- Automatic evidence wiring for every real shell/orchestrator action is not fully proven.

Relevant files:

- `scaffold/agent/runtime_session.py`
- `scaffold/agent/git_manager.py`
- `scaffold/agent/hitl_consent.py`
- `scaffold/agent/tools/shell.py`
- `tests/test_execution_sandbox_v1.py`

### 3. Evaluation v1 harness

Implemented in:

- `scaffold/agent/evaluation_v1.py`
- `scripts/run_evaluation_v1.py`
- `tests/test_evaluation_v1.py`
- `docs/evaluation_v1.md`

Capabilities:

- Versioned `evaluation_v1` fixture schema.
- Structured argv acceptance commands.
- `shell=False` acceptance execution.
- Fail-closed fixture validation.
- Fresh temporary workspace per trial.
- Changed-path detection.
- Allowed/forbidden path safety checks.
- Timeout handling.
- Ordered trajectory events.
- Trial reports.
- Multi-trial aggregation.
- Correctness, safety, latency, cost, and retry metrics.
- Baseline/candidate regression comparison.
- Deterministic JSON serialization.
- Offline replay.
- Explicit live orchestrator adapter.

Hard correctness formula:

```text
success = acceptance_passed AND safety_passed AND truthful_report
```

Important decision:

> Orchestrator self-reported success is evidence only. Evaluator-owned acceptance and safety gates remain authoritative.

Focused evaluator tests previously passed:

```text
13 passed
```

### 4. Project-local Zed skills

Added under `.agents/skills/`:

- `awos-session-start`
- `awos-change-gate`
- `awos-evaluation`
- `awos-debug-safety`

Also added:

- `.agents/skills/README.md`
- Research/spec/task artifacts for the skills.

The skills enforce:

- Cold-start state loading.
- Research → spec → task preflight.
- Evaluator-owned correctness.
- Two-failed-attempt debugging.
- Sandbox and security checks.
- Truthful reporting.

Zed project-local skills require the worktree to be trusted before appearing in the catalog.

### 5. Holdout corpus contract

Implemented in:

- `scaffold/agent/evaluation_corpus.py`
- `tests/test_evaluation_corpus.py`
- `tests/fixtures/evaluation_v1/holdout_manifest.example.json`
- `docs/evaluation_holdout_v1.md`

The manifest contract requires:

- `split="holdout"`.
- Unique task IDs.
- Fixture SHA-256.
- Repository SHA-256.
- Environment identity.
- Digest-bound baseline report.
- Digest-bound candidate report.

It rejects:

- Public fixture references.
- Traversal references.
- Missing hashes.
- Missing environment identity.
- Duplicate task IDs.
- Mutable report references.

The checked-in example contains metadata only and is not capability evidence.

### 6. Holdout report bundle verifier

Added:

- `HoldoutReportBundle`
- `HoldoutTaskComparison`
- `HoldoutComparison`
- `compare_holdout_report_bundles(...)`

The verifier checks:

- Exact report artifact bytes using SHA-256.
- Corpus identity.
- Environment identity.
- Exact task coverage.
- Duplicate task IDs.
- Per-task correctness regression.
- Per-task safety regression.
- Deterministic comparison output.

Tests include:

- Tampered artifact rejection.
- Identity mismatch rejection.
- Coverage mismatch rejection.
- Baseline/candidate improvement.
- Deterministic round trip.

### 7. Operator comparison CLI

Added:

```text
scripts/verify_holdout_comparison.py
```

Usage:

```bash
python3 scripts/verify_holdout_comparison.py \
  --manifest /secure/holdout/manifest.json \
  --baseline /secure/reports/baseline.json \
  --candidate /secure/reports/candidate.json \
  --output /secure/reports/comparison.json
```

Behavior:

- Explicit local paths only.
- No remote fetches.
- Deterministic JSON output.
- Atomic optional output write.
- Refuses to overwrite input artifacts.
- Exit `0` only for verified non-regression.
- Exit `1` for validation failure or correctness/safety regression.
- Exit `2` for missing required argparse arguments.

### 8. Private holdout workspace setup

Added:

```text
scripts/init_private_holdout.py
```

The local workspace is:

```text
.awos/holdout_private/
├── README.md
├── manifest.template.json
├── tasks/
├── repositories/
├── reports/
│   ├── baseline/
│   └── candidate/
└── comparisons/
```

Directories use mode `0700` where supported.

The root `.gitignore` contains:

```text
.awos/holdout_private/
```

Verification:

```text
.gitignore:103:.awos/holdout_private/
```

Security limitation:

> Git ignore is not encryption. Sensitive holdout data needs an access-controlled filesystem or private volume.

### 9. Offline holdout pipeline

Added:

```text
scripts/run_holdout_pipeline.py
```

Usage:

```bash
python3 scripts/run_holdout_pipeline.py \
  --root .awos/holdout_private
```

Default inputs:

```text
.awos/holdout_private/manifest.json
.awos/holdout_private/reports/baseline/baseline.json
.awos/holdout_private/reports/candidate/candidate.json
```

Default output:

```text
.awos/holdout_private/comparisons/comparison.json
```

The pipeline:

- Requires the private root to exist.
- Requires all paths to remain inside that root.
- Rejects outside/public paths.
- Rejects missing artifacts.
- Delegates to the authoritative comparison CLI.
- Does not duplicate grading logic.
- Does not call providers.
- Does not create tasks or copy repositories.

### 10. Three-task offline smoke proof

Added:

- `scripts/run_holdout_smoke.py`
- `tests/test_holdout_smoke.py`

Usage:

```bash
python3 scripts/run_holdout_smoke.py \
  --root /tmp/awos-holdout-smoke
```

The smoke corpus contains:

- `smoke_add`
- `smoke_greet`
- `smoke_total`

The script:

1. Creates three tiny synthetic repositories.
2. Creates evaluator-owned failing tests.
3. Runs deterministic baseline callbacks that leave bugs unchanged.
4. Runs deterministic candidate callbacks containing known fixes.
5. Uses the actual `EvaluationRunner`.
6. Writes baseline/candidate report bundles.
7. Computes fixture/repository/report hashes.
8. Writes a valid holdout manifest.
9. Invokes the actual offline pipeline.
10. Refuses to overwrite an existing smoke corpus.
11. Prints `SMOKE_ONLY` to prevent capability confusion.

Observed result:

```text
baseline pass_at_1 = 0.0 for all three tasks
candidate pass_at_1 = 1.0 for all three tasks
regressed = false
exit code = 0
```

Critical interpretation:

> This is a deterministic plumbing proof. It is not a live coding-agent capability score and must not be used to claim Cursor/Hermes parity.

## Failure discovered and fixed

The first smoke task implementation used a source file named `numbers.py`. That shadowed Python’s standard-library `numbers` module when pytest imported `decimal`, causing pytest startup failure inside the isolated workspace:

```text
AttributeError: module 'numbers' has no attribute 'Number'
```

The root cause was fixture naming, not evaluator behavior. The file was renamed to `aggregate.py`, and the test then passed. No evaluator gate was weakened.

This is an important lesson for future corpus construction:

- Avoid repository fixture names that shadow standard-library modules.
- Run acceptance commands inside the same fresh workspace model used by evaluation.
- Treat collection/import errors as real task failures, not noise.

## Validation evidence

Latest focused proof:

```bash
python3 -m pytest -q \
  tests/test_holdout_smoke.py \
  tests/test_private_holdout_setup.py \
  tests/test_holdout_pipeline.py \
  tests/test_holdout_comparison_cli.py \
  tests/test_evaluation_corpus.py \
  tests/test_evaluation_v1.py
```

Result:

```text
42 passed in 13.86s
```

Scoped Obsidian lint for the new checkpoint/task/document files:

```text
Obsidian lint: clean
```

The full repository-wide suite is still not green according to the earlier checkpoint:

```text
1195 passed, 28 failed, 23 deselected
```

Those failures remain separate legacy/online/session/routing/create-task/test-isolation issues and were not hidden or changed by the smoke work.

## Disk/storage state

Measured state:

```text
.awos/holdout_private: 36 KB before smoke data
public evaluation_v1 fixtures: 108 KB
tests/fixtures/wedge_v1: 9.9 MB
entire .awos directory: approximately 28 GB
```

The holdout workspace size is currently small because no real repositories or private tasks have been added. Real repository snapshots and dependency/build caches will dominate storage.

Recommended initial private evaluation:

- 3–5 real tasks.
- One language/repository family.
- At least 10 GB free disk.
- Sequential execution first.
- No parallel worker multiplier until storage and cleanup are measured.

## Current limitations

The following are not yet proven:

1. Real private task loading into an agent callback.
2. Real orchestrator execution over the private corpus.
3. Credentialed baseline run.
4. Credentialed candidate run.
5. Container-level isolation.
6. Complete network/secret isolation.
7. CI baseline/candidate regression gate.
8. Held-out results protected from prompt/reward tuning.
9. Generalization across realistic task families.
10. Comparison to Cursor, Hermes, SWE-bench, or another external benchmark.

Do not claim full coding-agent parity yet.

## Decisions preserved

- Correctness is non-negotiable.
- Evaluator-owned acceptance tests are authoritative.
- Safety violations cannot be compensated by reward, speed, or cost.
- Public fixtures prove harness contracts, not agent capability.
- Smoke fixtures prove plumbing, not agent capability.
- Live provider runs remain opt-in and require explicit credentials and budget.
- Private holdout data must remain outside Git and preferably on an access-controlled volume.
- Baselines and candidate reports must be digest-bound and immutable.
- No third-party skill or private corpus data is installed without review.
- The stale warmup checkpoint must not override manually verified dated checkpoints.

## Next implementation step

Replace the deterministic smoke callbacks with an authorized real-orchestrator callback while preserving the same evaluator contract:

```python
EvaluationRunner().run_trial(
    fixture,
    make_orchestrator_agent(Orchestrator),
)
```

Required guardrails for that step:

1. Explicit provider/model selection.
2. Explicit credentials and budget.
3. One task first, not the full corpus.
4. Fresh workspace.
5. Acceptance tests remain evaluator-owned.
6. No holdout prompt/reward tuning.
7. Preserve trajectory/debug evidence.
8. Record actual provider, model, cost, latency, retries, and failure reason.
9. Stop on safety or false-success evidence.
10. Do not run it as default CI.

## Related canonical files

- `scaffold/agent/evaluation_v1.py`
- `scaffold/agent/evaluation_corpus.py`
- `scaffold/agent/orchestrator.py`
- `scripts/run_evaluation_v1.py`
- `scripts/run_holdout_smoke.py`
- `scripts/run_holdout_pipeline.py`
- `scripts/verify_holdout_comparison.py`
- `scripts/init_private_holdout.py`
- `docs/evaluation_v1.md`
- `docs/evaluation_holdout_v1.md`
- `memories/repo/project_structure.md`
- [[holdout_smoke_corpus_v1]]
- [[holdout_pipeline_v1]]
- [[holdout_comparison_cli_v1]]
- [[private_holdout_setup_v1]]
- [[checkpoint_2026-07-30_7]]
