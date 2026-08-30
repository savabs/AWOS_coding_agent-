# AWOS Production Hardening — Execution Plan

> **For Hermes:** Execute this plan task-by-task with bounded context, vertical TDD, and two-stage review.

**Goal:** Implement the smallest production-hardening slice that makes one AWOS coding run reliable, bounded, auditable, and safe before expanding to sandboxing, release automation, and self-learning.

**Architecture:** Preserve the existing AWOS orchestrator, planner, execution graph, verifier, evidence, and evaluation systems. Add provider-free contracts around them rather than replacing the kernel. Work in vertical slices: stabilize the single-run path first, then isolate execution, then improve repository intelligence, then add release gates. Production scope is draft-PR coding tasks only; no auto-merge or deployment.

**Tech Stack:** Python 3, pytest, existing SQLite/WAL runtime state, existing evidence/debug modules, Tree-sitter/watchdog/xxhash already declared, Docker as the first production workspace backend, structured JSON contracts.

---

## Current context and constraints

- AWOS is an advanced Phase 1 prototype; production reliability is not yet proven.
- The latest project facts identify planner path grounding and live graph-schema compatibility as blockers.
- The repository has extensive unrelated modified/untracked artifacts. Do not clean or rewrite them during this work; use a clean release worktree before release validation.
- Existing Hermes/AWOS context tooling is useful but still needs strict-bound and performance hardening.
- Follow `Research → Spec → Task → Implement → Validate → Checkpoint` for each non-trivial slice.
- Use `python3`, not `python`.
- Do not modify global Hermes configuration.

## Definition of done for this implementation tranche

This tranche is complete only when:

1. Valid repository-relative planner paths are accepted and unsafe/out-of-snapshot paths are rejected.
2. Planner output is normalized into one canonical execution-graph schema and unknown/dropped fields fail closed.
3. A run has one schema-versioned manifest, explicit state transitions, append-only events, and deterministic replay tests.
4. Context packets satisfy `len(packet) <= max_chars` exactly at boundary cases.
5. Symbol/index tools avoid generated trees, report ambiguity, and complete within the configured test timeout.
6. A Docker workspace provider contract exists with tests for lifecycle, path boundaries, cleanup, and resource policy; no production path uses host credentials.
7. The focused and relevant regression suites pass, with live proof recorded for the run-contract/orchestrator path.
8. A checkpoint records exact commands, results, remaining blockers, and metrics.

Do not call the whole project production-ready after this tranche. Call it “single-run production hardening complete” only if all gates pass.

---

# Stage 0 — Preflight and baseline

**Exit condition:** research/spec/task artifacts and a reproducible baseline exist before implementation.

### Task 0.1: Create the production-hardening triad

**Files:**
- Create: `docs/research/production_hardening_single_run.html`
- Create: `docs/research/production_hardening_single_run.md`
- Create: `docs/specs/production_hardening_single_run_spec.html`
- Create: `docs/specs/production_hardening_single_run_spec.md`
- Create: `tasks/active/production_hardening_single_run.html`
- Create: `tasks/active/production_hardening_single_run.md`

Document current symbols, known blockers, contracts, trust boundaries, exact tests, live proof, and stage exit conditions. Keep HTML canonical and Markdown as the repository’s thin stub convention requires.

**Verification:** all six artifacts exist; task status is `active`; spec lists every test and live-proof command below.

### Task 0.2: Capture baseline without changing product code

**Files:**
- Create: `scripts/production_baseline.py`
- Create: `tests/test_production_baseline.py`

The script must emit stable JSON for:

- selected regression pass/fail counts
- planner-grounding fixture status
- graph-contract fixture status
- context packet size and strict-bound status
- symbol retrieval duration and ambiguity count
- existing holdout/evaluation status

Do not turn the current one-task SWE-bench result into a production rate.

**TDD:** write schema/fixture tests first, then implement the collector.

**Verification:** `python3 scripts/production_baseline.py --json` returns schema-valid output without secrets.

---

# Stage 1 — Planner and graph reliability

**Exit condition:** the real planner → normalizer → graph executor path accepts valid evidence and rejects unsafe/invalid plans deterministically.

### Task 1.1: Add planner path-grounding regression tests

**Files:**
- Test/create: `tests/test_planner_path_grounding.py`
- Inspect: `scaffold/agent/exploration_phase.py`
- Inspect: `scaffold/agent/planner.py`
- Inspect: `scaffold/agent/orchestrator.py`

Tests must cover:

- repository-relative evidence path accepted
- equivalent normalized path accepted
- absolute path inside project root handled consistently
- path outside root rejected
- path traversal rejected
- similarly named but unevidenced path rejected
- create-file tasks handled explicitly

**TDD:** run the new tests before implementation and preserve the failure output in the checkpoint.

### Task 1.2: Implement one canonical path normalizer

**Files:**
- Create: `scaffold/agent/core/path_contract.py`
- Modify: `scaffold/agent/orchestrator.py`
- Modify: planner/exploration callers only where required
- Test: `tests/test_path_contract.py`

Return a canonical root-relative path plus evidence status. Never compare raw strings when a path can be normalized. Reject symlink escapes and paths outside the workspace. Keep the API provider-free.

**Verification:** focused path tests, `py_compile`, and existing planner/orchestrator contract tests.

### Task 1.3: Unify graph normalization

**Files:**
- Inspect/modify: `scaffold/agent/planner_graph_contract.py`
- Inspect/modify: `scaffold/agent/execution_graph.py`
- Inspect/modify: `scaffold/agent/dag_executor.py`
- Test: `tests/test_planner_graph_contract_compatibility.py`
- Test: `tests/test_execution_graph_integration.py`

Define one versioned graph schema. Unknown node types, invalid dependencies, missing required fields, and silently dropped provider fields must fail closed. Add a round-trip test: planner-shaped input → normalized graph → deterministic execution waves → result contract.

**Verification:** focused graph tests plus provider-free evaluation tests.

---

# Stage 2 — Durable single-run contract

**Exit condition:** restart, retry, cancel, and replay operate from persisted state rather than in-memory assumptions.

### Task 2.1: Define run contracts

**Files:**
- Create: `scaffold/agent/core/run_contract.py`
- Test: `tests/test_run_contract.py`

Implement validated contracts for:

- `RunManifest`
- `RunState`
- `RunEvent`
- `VerifierResult`
- `ResultBundle`

Every contract needs a schema version, run ID, and deterministic serialization. Redact secrets before persistence.

### Task 2.2: Add explicit transition validation

**Files:**
- Modify: `scaffold/agent/agent_state_manager.py`
- Modify: `scaffold/agent/runtime_session.py`
- Modify: `scaffold/agent/core/debug_journal.py`
- Test: `tests/test_run_state_machine.py`

Allowed transitions must be explicit. Persist the transition/event before performing the associated external side effect. Duplicate idempotency keys must not execute twice. Invalid transitions must fail closed.

### Task 2.3: Add deterministic replay

**Files:**
- Create: `scaffold/agent/core/run_replay.py`
- Test: `tests/test_run_replay.py`

Replay an event stream into final state, detect missing sequence numbers and digest mismatches, and produce a bounded diagnostic. Do not replay arbitrary shell side effects; replay state and evidence only.

### Task 2.4: Wire one orchestrator path

**Files:**
- Modify: `scaffold/agent/orchestrator.py`
- Modify only if active: `scaffold/agent/core/orchestrator.py`
- Test: `tests/test_orchestrator_state_machine.py`
- Test: `tests/test_runtime_session.py`

Choose the canonical active orchestrator path after tracing imports. Do not maintain two competing implementations. Add restart/resume and cancel tests.

**Live proof:** run a deterministic E2E fixture that prints run ID, state transitions, event count, final verification status, and result-bundle digest.

---

# Stage 3 — Context and repository intelligence hardening

**Exit condition:** context is strictly bounded and localization is fast, deterministic, and explainable.

### Task 3.1: Fix strict context bounds

**Files:**
- Modify: `scripts/task_context.py`
- Inspect/modify relevant multi-resolution context code
- Test: `tests/test_context_packet_contract.py`

Guarantee exact `len(packet) <= max_chars`, including separators, markers, Unicode, and final newline. Preserve task text and selected-file identity. Test budgets around zero, marker length, required-section length, and normal operation.

### Task 3.2: Harden symbol retrieval boundaries

**Files:**
- Modify: `scripts/symbol_retrieval.py`
- Create: `scripts/build_code_index.py`
- Test: `tests/test_symbol_retrieval.py`

Exclude `awos-app/dist`, caches, generated files, worktrees, and fixtures by default. Require fully qualified names when ambiguous. Add a timeout/complexity test and emit index scope/digest metadata.

### Task 3.3: Build an incremental index

**Files:**
- Create: `scaffold/agent/code_index/`
- Inspect/reuse: `scaffold/agent/cartographer.py`, `repo_map.py`, `semantic_retriever.py`
- Test: `tests/test_incremental_code_index.py`

Use existing Tree-sitter/watchdog/xxhash dependencies. Index symbols, imports, references, tests, hashes, and schema version. Update only changed files. Do not add a vector database dependency for basic symbol lookup.

### Task 3.4: Add localization evidence

**Files:**
- Create: `scaffold/agent/localization.py`
- Test: `tests/test_localization_contract.py`

Return target symbols, candidate files, callers, callees, tests, reasons, snapshot ID, and index digest. Planner edit nodes must reference this artifact. Insufficient evidence produces `needs-research`, not a guessed edit.

---

# Stage 4 — Workspace safety slice

**Exit condition:** production mode can execute inside an isolated workspace with explicit policy and cleanup.

### Task 4.1: Define workspace provider contract

**Files:**
- Create: `scaffold/agent/workspace_provider.py`
- Test: `tests/test_workspace_provider.py`

Contract methods: `create`, `exec`, `health`, `snapshot`, `diff`, `destroy`, and `resource_usage`. All methods carry run ID and policy context. Local provider is development-only.

### Task 4.2: Implement Docker provider minimally

**Files:**
- Create: `scaffold/agent/docker_workspace.py`
- Create: `deploy/agent-images/Dockerfile`
- Create: `deploy/agent-images/README.md`
- Test: `tests/test_docker_workspace.py`

Use a pinned base image, explicit workspace mount, non-root user where practical, bounded CPU/memory/disk/time, no credential mounts, and network disabled by default. Tests must skip cleanly with a clear reason if Docker is unavailable; production CI must require Docker.

### Task 4.3: Add command and secret policy

**Files:**
- Create: `scaffold/agent/security_policy.py`
- Create: `scaffold/agent/command_policy.py`
- Test: `tests/test_command_policy.py`
- Test: `tests/test_secret_redaction.py`

Classify read-only, workspace-mutating, network, credential-sensitive, destructive, and forbidden commands. Fail closed when uncertain. Redact credentials from events, logs, tool output, and result bundles.

### Task 4.4: Add resource circuit breakers

**Files:**
- Modify: `scaffold/agent/test_runner.py`
- Modify: orchestrator/workspace execution boundary
- Test: `tests/test_resource_limits.py`

Enforce wall time, process group cleanup, output size, CPU/memory/disk, tool-call count, retries, concurrency, and cost. Persist the exact breaker reason.

---

# Stage 5 — Verification, delivery, and operations

**Exit condition:** every successful run has complete evidence, is human-gated, and can be operated after restart.

### Task 5.1: Create layered verification stack

**Files:**
- Create: `scaffold/agent/verification_stack.py`
- Modify: `scaffold/agent/verifier.py`
- Test: `tests/test_verification_stack.py`

Implement gates for patch scope, syntax, lint/type diagnostics, direct tests, impacted tests, full tests, security checks, and final acceptance. Each gate is PASS/FAIL/BLOCKED/NOT_RUN with evidence.

### Task 5.2: Create result bundle

**Files:**
- Create: `scaffold/agent/result_bundle.py`
- Modify: `scaffold/agent/core/debug_report.py`
- Test: `tests/test_result_bundle.py`

Include manifest, base revision, patch, changed files, plan/localization evidence, commands, diagnostics, test outcomes, policy decisions, cost, latency, warnings, and approval state. Validate completeness before success is emitted.

### Task 5.3: Draft-PR-only delivery

**Files:**
- Create: `scaffold/agent/delivery/github_pr.py`
- Test: `tests/test_github_delivery_policy.py`

Create branch/draft PR only when explicitly authorized. No auto-merge or deployment. Keep provider credentials outside the sandbox and use scoped delivery permissions.

### Task 5.4: Operator controls and telemetry

**Files:**
- Modify: `scaffold/agent/core/observability.py`
- Modify: `scaffold/agent/runtime_session.py`
- Modify: `awos.py`
- Test: `tests/test_runtime_recovery.py`
- Test: `tests/test_telemetry_schema.py`

Support pause, resume, cancel, retry, inspect, and cleanup. Emit duration by state, model latency, tokens/cache, tool errors, context size, resource usage, retries, cost, acceptance, and final disposition without source contents in labels.

---

# Stage 6 — Release evidence and controlled rollout

**Exit condition:** release promotion is evidence-based for a narrowly defined draft-PR workload.

### Task 6.1: Expand protected holdout corpus

**Files:**
- Modify existing holdout manifest/pipeline
- Create: `docs/evaluation/production_corpus_policy.md`
- Test: existing holdout integrity tests

Start with 20 tasks across small fixes, medium changes, cross-file/refactor cases, and adversarial safety cases. Keep private acceptance tests hidden and repository revisions pinned.

### Task 6.2: Add multi-trial release gate

**Files:**
- Create: `scripts/release_gate.py`
- Modify: `scripts/run_holdout_pipeline.py`
- Modify: `scaffold/agent/evaluation_v1.py`
- Test: `tests/test_release_gate.py`

Report pass@1, pass@N, safety failures, timeouts, retries, cost per accepted task, latency, evidence completeness, and failure taxonomy. Compare candidate to a frozen baseline.

### Task 6.3: Canary policy

**Files:**
- Create: `docs/operations/canary_policy.md`
- Create: `config/production_profiles/`
- Create: `.github/workflows/agent-release.yml`

Roll out in order: offline → shadow/no-write → human-reviewed draft PRs → small allowlist → wider cohort. Roll back on safety, credential, evidence, cost, state, or cleanup regressions.

---

# Stage 7 — Self-learning after reliability

**Exit condition:** learning improves held-out accepted-task outcomes without safety or reproducibility regression.

### Task 7.1: Failure taxonomy

**Files:**
- Create: `scaffold/agent/learning/failure_taxonomy.py`
- Create: `scaffold/agent/learning/trace_dataset.py`
- Test: `tests/test_failure_taxonomy.py`

Classify intent, localization, planning/schema, tool, edit, verification, environment, resource, security, and delivery failures.

### Task 7.2: Gate learned policy updates

**Files:**
- Create: `scaffold/agent/learning/policy_registry.py`
- Modify: `prompt_evolver.py`, `ml_router.py`, `live_tool_synth.py`
- Test: `tests/test_learning_policy_gate.py`

A learned policy must pass offline replay, public regression, private holdout comparison, safety tests, and cost/latency checks before production promotion.

Do not begin this stage until the release gate has passed.

---

# Verification matrix

## Per atomic change

```bash
python3 -m py_compile <changed_file.py>
python3 scripts/lsp_check.py --check <changed_file.py> --json
python3 scripts/focused_test.py <changed_file.py> --json
python3 -m pytest <focused_tests> -q
```

## Stage 1–3 core gate

```bash
python3 -m pytest \
  tests/test_path_contract.py \
  tests/test_planner_path_grounding.py \
  tests/test_planner_graph_contract.py \
  tests/test_planner_graph_contract_compatibility.py \
  tests/test_execution_graph.py \
  tests/test_execution_graph_integration.py \
  tests/test_context_packet_contract.py \
  tests/test_symbol_retrieval.py \
  -q
```

## Stage 2 runtime gate

```bash
python3 -m pytest \
  tests/test_run_contract.py \
  tests/test_run_state_machine.py \
  tests/test_run_replay.py \
  tests/test_orchestrator_state_machine.py \
  tests/test_runtime_session.py \
  -q
```

## Stage 4 safety gate

```bash
python3 -m pytest \
  tests/test_workspace_provider.py \
  tests/test_docker_workspace.py \
  tests/test_command_policy.py \
  tests/test_secret_redaction.py \
  tests/test_resource_limits.py \
  -q
```

## Release gate

```bash
python3 scripts/production_baseline.py --json
python3 scripts/release_gate.py --profile production-candidate
python3 scripts/run_holdout_pipeline.py --profile production-candidate
python3 scripts/verify_holdout_comparison.py --strict
make test
```

## Required live proof

For the first integrated run, record a bounded, deterministic proof containing:

- run ID
- workspace ID/provider
- state transition sequence
- localization snapshot/index digest
- planner graph digest
- commands and exit codes
- verification gate results
- result-bundle digest
- cleanup status

No credentials or raw secret-bearing output may appear in the proof.

---

# Risks and decisions

- Do not implement Docker, persistent indexing, self-learning, UI, and release automation in one change.
- Do not use a critic to override deterministic safety/test failures.
- Do not claim LSP is integrated until a supported server is installed and the runtime consumes diagnostics.
- Do not claim self-learning from stored traces; prove improvement against a frozen baseline.
- Do not add a third orchestration path. First identify the canonical active path and retire or isolate duplicates.
- Treat generated app distributions and unrelated worktree artifacts as out of scope.
- If the same file fails after two fix attempts, stop and follow the debug protocol before another edit.

# Recommended first execution slice

Implement only Stage 0 plus Tasks 1.1–1.3 first. That slice directly addresses the known live blockers and produces the evidence needed to decide whether the current orchestrator path is safe to harden further. Do not start Docker or self-learning until this slice passes.

The first implementation session should therefore be:

1. Create the research/spec/task triad.
2. Run the existing planner/graph tests and save the baseline.
3. Write failing path-grounding tests.
4. Implement the canonical path contract.
5. Write failing graph compatibility tests.
6. Implement canonical normalization.
7. Run focused regressions and one live proof.
8. Write a checkpoint with exact evidence and next blockers.

That is the safest route from the current prototype to a production-capable pilot.

## Sources

The implementation direction is grounded in the prior research plan and these repositories/papers:

- Open SWE: https://github.com/langchain-ai/open-swe
- SWE-ReX: https://github.com/SWE-agent/SWE-ReX
- OpenHands Software Agent SDK: https://arxiv.org/html/2511.03690v1
- OpenHands verification research: https://www.openhands.dev/blog/20260305-learning-to-verify-ai-generated-code
- OpenHands benchmark infrastructure: https://github.com/OpenHands/benchmarks
- mini-SWE-agent: https://github.com/SWE-agent/mini-swe-agent

No implementation changes are made by this plan. The next turn should execute only the recommended first slice after confirming the triad is current.
