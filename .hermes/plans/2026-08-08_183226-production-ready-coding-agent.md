# AWOS Production-Ready Coding Agent Plan

> **For Hermes:** Execute this plan task-by-task with TDD, bounded context, and two-stage review.

**Goal:** Move AWOS from an advanced Phase 1 prototype to a production-ready, measurable, secure, and operable autonomous coding-agent product without weakening its quality floor.

**Architecture:** Preserve AWOS as the kernel/harness and make the coding agent App #1. Separate the agent core from evaluation, UI, provider adapters, and deployment concerns. Every run gets a durable run state, an isolated workspace, a bounded context packet, deterministic policy gates, layered verification, and an auditable result. Add self-learning only after the live reliability gates are met.

**Tech Stack:** Existing Python AWOS kernel, pytest, tree-sitter, watchdog, xxhash, Chroma/vector memory, OpenRouter/OpenCode adapters, Docker or remote sandbox backend, SQLite/WAL runtime state, structured JSON evidence, GitHub/PR integration, OpenTelemetry-compatible traces where practical.

---

## 1. Production definition

AWOS is production-ready for a defined workload only when all of the following are true:

1. **Correctness:** evaluator-owned acceptance passes at a predetermined rate on held-out real repository tasks.
2. **Safety:** no unauthorized host, credential, network, repository, or destructive-operation escape in adversarial tests.
3. **Reproducibility:** a run can be replayed from an immutable run manifest, pinned code revision, model/provider metadata, workspace image, and tool/event log.
4. **Operability:** operators can see progress, pause/cancel, recover, inspect failures, and distinguish agent failure from infrastructure failure.
5. **Isolation:** every autonomous run is isolated from production credentials and unrelated workspaces.
6. **Cost control:** budgets, timeouts, tool-call limits, concurrency limits, and circuit breakers are enforced before spend occurs.
7. **Human control:** no autonomous merge or production side effect without an explicit policy and approval gate.
8. **Regression resistance:** every release is evaluated against public fixtures, private holdout tasks, security cases, and prior production traces.

Initial product scope should be narrow: repository issue/bug-fix tasks that produce a branch or draft PR, not direct production deployment.

---

## 2. Research conclusions and design implications

### 2.1 Sandbox first, then permissions inside the boundary

Open SWE runs every task in its own isolated cloud sandbox and supports pluggable providers such as Modal, Daytona, Runloop, E2B, and LangSmith. SWE-ReX separates agent logic from execution infrastructure and supports local, Docker, remote, interactive, and parallel shell sessions. AWOS should make workspace isolation a first-class interface rather than letting the orchestrator directly assume a local host.

Sources: [1][2]

### 2.2 One durable state source and event-sourced replay

The OpenHands SDK paper identifies durable state, safe execution, local/remote portability, strict separation of concerns, immutable configuration, a single mutable state object, event-sourced replay, and typed tools as production foundations. AWOS already has evidence/debug/runtime components; these must be unified under one run manifest and event log instead of multiple partially overlapping records.

Source: [3]

### 2.3 Curated tools are safer than maximum tools

Open SWE explicitly uses a small curated toolset and server-side authorization for sensitive observability tools. AWOS should expose task-specific tool profiles and deny unused capabilities by default. MCP additions must be audited, pinned, scoped, and tested as supply-chain dependencies.

Source: [1]

### 2.4 Production verification must be layered

OpenHands describes verification as a stack rather than one final test command. Its recent critic work also warns that benchmark-trained critics transfer poorly to production and recommends production signals such as code survival and PR merge. AWOS should use deterministic gates first, then trajectory/patch critics only as advisory or reranking signals until validated on AWOS production traces.

Source: [4]

### 2.5 Evaluation must include public, private, replay, and real-production tracks

OpenHands benchmarks maintain reproducible SDK compatibility, pinned commits/images, multiple benchmark families, rich logs, local Docker workspaces, and remote parallel workspaces. AWOS should preserve its existing holdout/evaluation contracts and add release gates around them.

Source: [5]

### 2.6 Keep the core composable and applications separate

OpenHands reports that combining agent logic, evaluation, CLI, web UI, and integrations in one mutable architecture created configuration and maintenance problems. AWOS should treat `scaffold/agent` as the reusable core and keep GUI, benchmark, provider, and deployment adapters behind typed interfaces.

Source: [3]

---

## 3. Target production architecture

```text
Invocation API / CLI / GitHub / Slack
                 |
                 v
        Run Admission Controller
        - authn/authz
        - policy profile
        - budget/concurrency
        - repository allowlist
                 |
                 v
          Durable Run Service
        - run manifest
        - state machine
        - event log
        - pause/resume/cancel
                 |
                 +--------------------+
                 |                    |
                 v                    v
       Workspace Provider       Context Service
       - local/Docker/remote     - bounded packet
       - image + repo pin        - symbol/index retrieval
       - network policy           - task/test impact
                 |                    |
                 +----------+---------+
                            v
                    Agent Core Runtime
                 Planner -> Worker -> Verifiers
                   |          |          |
                   |          |          +-- syntax/type/lint/tests
                   |          +------------- edit/tool policy
                   +------------------------ plan/evidence gates
                            |
                            v
                 Result Review / PR Adapter
                 - artifact bundle
                 - human approval
                 - draft PR only by default

         Telemetry / Evaluation / Learning plane
         - traces, cost, latency, outcomes
         - holdout/replay evaluations
         - failure taxonomy
         - gated policy updates
```

### Required interfaces

Create or stabilize these provider-free contracts:

- `RunManifest`: run ID, user, repository, commit, task, policy, model metadata, budget, workspace image, tool profile, seed, timestamps.
- `RunState`: admitted, provisioning, exploring, planning, executing, verifying, awaiting_review, completed, failed, cancelled, expired.
- `WorkspaceProvider`: create, exec, snapshot, diff, destroy, health, resource usage.
- `ContextProvider`: locate, retrieve, summarize, invalidate, explain evidence.
- `AgentPolicy`: allowed paths, commands, network, secrets, tools, maximum duration, approval requirements.
- `VerifierResult`: gate name, status, evidence references, severity, reproducibility metadata.
- `RunEvent`: append-only event with schema version, sequence number, timestamp, actor, payload digest.
- `ResultBundle`: patch/diff, tests, diagnostics, evidence, cost, warnings, approvals, final disposition.

---

# Phase 0 — Freeze the production contract and baseline

**Exit gate:** a versioned production contract exists and the current implementation has a baseline report.

### Task 0.1: Create a production readiness specification

**Files:**
- Create: `docs/specs/production_readiness_spec.md`
- Create: `tasks/active/production_readiness.md`
- Modify: `memories/repo/project_structure.md`

Define supported workload, non-goals, trust boundaries, lifecycle states, acceptance gates, error classes, and release criteria. Explicitly state that initial production output is a branch/draft PR, not a direct merge or deployment.

### Task 0.2: Baseline current behavior

**Files:**
- Create: `scripts/production_baseline.py`
- Create: `tests/test_production_baseline.py`
- Reuse: `scripts/run_evaluation_v1.py`, `scripts/run_holdout_pipeline.py`, `scripts/verify_holdout_comparison.py`

Capture current values for:

- Public fixture pass rate
- Private holdout pass rate
- Test-outcome gate rate
- Planner grounding failures
- Graph-schema failures
- Timeout/retry rate
- Cost per run/task
- Run duration
- Context packet size
- Evidence completeness
- Safety-case pass rate

Do not report a production percentage from the current one-task SWE-bench result; mark it as an early smoke datapoint.

### Task 0.3: Define release gates

Create a machine-readable release policy at `config/production_release_policy.yaml` or equivalent. Suggested initial gates:

```yaml
public_regression: 1.0
private_holdout_min_pass_at_1: 0.60
safety_adversarial: 1.0
replay_determinism: 1.0
artifact_completeness: 1.0
unauthorized_merge: 0.0
credential_exposure: 0.0
unbounded_run: 0.0
```

Treat these as initial targets, not universal claims. Raise the private-holdout threshold after collecting enough tasks.

---

# Phase 1 — Stabilize the single-run execution path

**Exit gate:** one run has one state machine, one manifest, one event log, one workspace, and one authoritative result bundle.

### Task 1.1: Unify run state and manifest

**Files:**
- Inspect/modify: `scaffold/agent/agent_state_manager.py`
- Inspect/modify: `scaffold/agent/core/debug_schema.py`
- Inspect/modify: `scaffold/agent/core/debug_journal.py`
- Inspect/modify: `scaffold/agent/runtime_session.py`
- Create: `scaffold/agent/core/run_contract.py`
- Test: `tests/test_run_contract.py`

Add schema-versioned dataclasses or validated dictionaries for `RunManifest`, `RunState`, `RunEvent`, and `ResultBundle`. Reject malformed transitions and duplicate sequence numbers. Make all evidence references point to run ID plus event sequence/digest.

### Task 1.2: Make the orchestrator a state-machine owner

**Files:**
- Modify: `scaffold/agent/orchestrator.py`
- Modify: `scaffold/agent/core/orchestrator.py` if still active
- Test: `tests/test_orchestrator_state_machine.py`

Remove ambiguous parallel state ownership. Every transition must be explicit and persisted before external side effects. Add idempotency keys for retries and recovery after process restart.

### Task 1.3: Fix planner path grounding

**Files:**
- Inspect: `scaffold/agent/exploration_phase.py`
- Inspect: `scaffold/agent/planner.py`
- Inspect: `scaffold/agent/planner_graph_contract.py`
- Modify: `scaffold/agent/orchestrator.py` validation path
- Create/modify: `tests/test_planner_path_grounding.py`

Required regression:

- Exploration evidence contains `src/sqlfluff/rules/L060.py`.
- Planner emits the same repository-relative path.
- Plan validation accepts it.
- Absolute/normalized equivalents are handled safely.
- Paths outside the workspace are rejected.
- Similar filenames cannot satisfy grounding accidentally.

### Task 1.4: Fix graph-schema compatibility

**Files:**
- Inspect/modify: `scaffold/agent/planner_graph_contract.py`
- Inspect/modify: `scaffold/agent/execution_graph.py`
- Inspect/modify: `scaffold/agent/dag_executor.py`
- Test: `tests/test_planner_graph_contract_compatibility.py`
- Test: `tests/test_execution_graph_integration.py`

Create one canonical normalized graph schema. Test planner output, normalization, deterministic wave generation, execution, recovery, and final verification together. Reject unknown node types and silently dropped fields.

### Task 1.5: Enforce strict context bounds

**Files:**
- Modify: `scripts/task_context.py`
- Modify: relevant multi-resolution context modules
- Test: `tests/test_context_packet_contract.py`

Guarantee `len(packet) <= max_chars` exactly, including truncation markers and final newlines. Preserve required task text and selected file identity; emit metadata when content was truncated. Add property-based or boundary tests at budgets 0, 1, marker length, and normal budgets.

### Task 1.6: Remove duplicate/ambiguous source discovery

**Files:**
- Modify: `scripts/symbol_retrieval.py`
- Create: `scripts/build_code_index.py`
- Test: `tests/test_symbol_retrieval.py`

Exclude generated distributions, fixtures, caches, and packaged application copies by default. Require fully qualified symbol names when ambiguous. Add deterministic source roots and report ambiguity instead of choosing arbitrarily.

---

# Phase 2 — Secure, reproducible workspace execution

**Exit gate:** no production run executes arbitrary agent commands directly in the developer host environment.

### Task 2.1: Implement a workspace-provider interface

**Files:**
- Create: `scaffold/agent/workspace_provider.py`
- Create: `scaffold/agent/local_workspace.py`
- Create: `scaffold/agent/docker_workspace.py`
- Test: `tests/test_workspace_provider.py`

Support local mode only for explicitly trusted development. Production mode must use Docker or a remote provider. Define lifecycle, health checks, resource limits, cleanup, snapshot, and diff operations.

### Task 2.2: Add network and credential policy

**Files:**
- Create: `scaffold/agent/security_policy.py`
- Modify: workspace providers
- Test: `tests/test_agent_security_policy.py`
- Test: `tests/test_secret_redaction.py`

Default production policy:

- No host credential mounts
- No access to `~/.ssh`, cloud credentials, `.env`, or Hermes secrets
- Network disabled by default during edit/test
- Explicit allowlist for package registries and source control
- Secrets injected only through short-lived scoped handles, never prompt text
- Redact tokens, API keys, passwords, and bearer values in logs/evidence

### Task 2.3: Add command risk classification and approval gates

**Files:**
- Inspect/modify: existing shell safety classifier
- Create: `scaffold/agent/command_policy.py`
- Test: `tests/test_command_policy.py`

Classify commands as read-only, workspace-mutating, network, credential-sensitive, destructive, or forbidden. Require approval for destructive/network/credential operations. Fail closed when classification is uncertain.

### Task 2.4: Add resource limits and circuit breakers

**Files:**
- Modify: `scaffold/agent/test_runner.py`
- Modify: workspace provider
- Modify: orchestrator loop
- Test: `tests/test_resource_limits.py`

Enforce wall-clock timeout, CPU/memory/disk limits, process count, output size, tool-call count, retry count, and budget. Kill the full process group on timeout and persist the reason.

### Task 2.5: Reproducible workspace images

**Files:**
- Create: `deploy/agent-images/README.md`
- Create: `deploy/agent-images/Dockerfile` or image templates
- Create: `scripts/build_agent_image.py`
- Test: `tests/test_workspace_reproducibility.py`

Pin base image digest, Python/Node/tool versions, repository revision, and dependency lockfile. Add image health checks and a manifest digest. Follow the OpenHands benchmark pattern of pinning the SDK/image relationship for evaluation reproducibility.[5]

---

# Phase 3 — Repository intelligence and efficient context

**Exit gate:** the agent can localize a task with explainable evidence without rescanning the entire repository per query.

### Task 3.1: Build an incremental Tree-sitter index

**Files:**
- Create: `scaffold/agent/code_index/`
- Create: `scripts/build_code_index.py`
- Modify: existing `cartographer.py`, `repo_map.py`, `semantic_retriever.py`
- Test: `tests/test_incremental_code_index.py`

Index symbols, imports, definitions, references, inheritance, tests, file hashes, and summaries. Use `tree-sitter` and existing `watchdog`/`xxhash` dependencies. Update only changed files. Store index under `.awos/index/` and include schema/version/digest metadata.

### Task 3.2: Add explainable localization artifacts

**Files:**
- Create: `scaffold/agent/localization.py`
- Test: `tests/test_localization_contract.py`

Return:

```json
{
  "snapshot_id": "...",
  "target_symbols": [],
  "candidate_files": [],
  "callers": [],
  "callees": [],
  "test_files": [],
  "reasons": [],
  "index_digest": "..."
}
```

Every plan edit node must reference its localization evidence. If evidence is insufficient, the run moves to `needs-research` instead of guessing.

### Task 3.3: Implement test-impact selection

**Files:**
- Modify: `scripts/focused_test.py`
- Create: `scaffold/agent/test_impact.py`
- Test: `tests/test_test_impact.py`

Use changed symbols/imports/dependency edges to select direct, impacted, and full-suite tests. Always run direct tests; run impacted tests when confidence is below a configurable threshold; run the full suite at release gates.

### Task 3.4: Integrate context packets into runtime planning

**Files:**
- Modify: `scaffold/agent/exploration_phase.py`
- Modify: `scaffold/agent/planner.py`
- Modify: `scaffold/agent/orchestrator.py`
- Test: `tests/test_runtime_context_packet.py`

Move from skill-only guidance to runtime-enforced bounded packets containing task, repository facts, localization evidence, selected symbols, tests, constraints, and exact prior errors. Log packet size and evidence IDs.

---

# Phase 4 — Layered verification and human-safe delivery

**Exit gate:** no run can report success without complete, evaluator-owned, inspectable evidence.

### Task 4.1: Define deterministic verification stack

**Files:**
- Create: `scaffold/agent/verification_stack.py`
- Modify: `scaffold/agent/verifier.py`
- Test: `tests/test_verification_stack.py`

Recommended order:

1. Patch scope and path policy
2. Formatting/lint
3. Syntax/compile
4. Type/LSP diagnostics
5. Direct tests
6. Impacted tests
7. Full suite or repository CI command
8. Security/license checks where applicable
9. Diff review and contract review
10. Final evaluator/acceptance check

Each gate must have PASS, FAIL, BLOCKED, or NOT_RUN and an evidence reference.

### Task 4.2: Add patch and trajectory review

**Files:**
- Create: `scaffold/agent/critic.py`
- Create: `scaffold/agent/review_policy.py`
- Test: `tests/test_critic_policy.py`

Use a critic only for early stopping, reranking, and targeted feedback. Never let a critic override deterministic failing tests or safety failures. Train/calibrate it only on AWOS production traces such as merge, code survival, reviewer rejection, and revert outcomes, not benchmark traces alone, following the OpenHands finding.[4]

### Task 4.3: Produce a reviewable result bundle

**Files:**
- Modify: `scaffold/agent/core/debug_report.py`
- Create: `scaffold/agent/result_bundle.py`
- Test: `tests/test_result_bundle.py`

Bundle:

- Run manifest
- Git base and patch
- Changed files and line ranges
- Plan and localization evidence
- Commands and exit codes
- Test summaries
- Diagnostics
- Security policy decisions
- Cost/latency/token data
- Warnings and unresolved issues
- Human approval status

### Task 4.4: Draft PR delivery only

**Files:**
- Create: `scaffold/agent/delivery/github_pr.py`
- Test: `tests/test_github_delivery_policy.py`

Initial production behavior:

- Create branch
- Push branch only with explicit policy
- Open draft PR
- Attach result bundle summary
- Never auto-merge
- Never deploy
- Require human approval

---

# Phase 5 — Durable operations and observability

**Exit gate:** operators can manage thousands of runs without reading raw logs.

### Task 5.1: Event-sourced run history and replay

**Files:**
- Modify: runtime state/database modules
- Create: `scaffold/agent/replay.py`
- Test: `tests/test_run_replay.py`

Persist append-only events with schema versions, sequence numbers, hashes, and timestamps. Replay must reconstruct state and detect missing or modified events.

### Task 5.2: Structured telemetry

**Files:**
- Modify: `scaffold/agent/core/observability.py`
- Create: `scaffold/agent/telemetry_schema.py`
- Test: `tests/test_telemetry_schema.py`

Emit metrics/traces for:

- Time in each state
- Model/provider latency
- Input/output/cache tokens
- Tool calls and failures
- Context size
- Workspace CPU/memory/disk
- Test outcomes
- Retry/escalation counts
- Cost
- Final acceptance
- Human review outcome

Keep sensitive source content out of metrics labels.

### Task 5.3: Pause, resume, cancel, retry, and recovery

**Files:**
- Modify: `scaffold/agent/runtime_session.py`
- Modify: `awos.py`
- Test: `tests/test_runtime_recovery.py`

All operations must be idempotent. Resume from a persisted checkpoint, not from an in-memory assumption. Cancel must stop child processes and release workspace resources.

### Task 5.4: Operator dashboard and alerts

**Files:**
- Modify: observer API and UI
- Create: `docs/operations/runbook.md`
- Test: observer/replay integration tests

Expose active runs, queue depth, stuck runs, spend, failure classes, workspace health, and recent release-gate status. Add alerts for budget spikes, repeated planner failures, sandbox leaks, and evidence-incomplete completions.

---

# Phase 6 — Evaluation, release engineering, and controlled rollout

**Exit gate:** releases are promoted using evidence, not confidence.

### Task 6.1: Expand the held-out corpus

**Files:**
- Modify: private holdout manifest tooling
- Create: `docs/evaluation/production_corpus_policy.md`
- Test: holdout integrity tests

Build a contamination-aware corpus of at least 20 tasks initially:

- 8 small bug fixes
- 6 medium feature/behavior tasks
- 4 refactors or cross-file tasks
- 2 adversarial/security tasks

Keep task descriptions, expected acceptance, and private tests separated from agent-visible data. Pin repository revisions.

### Task 6.2: Add multi-trial evaluation

**Files:**
- Modify: `scripts/run_holdout_pipeline.py`
- Modify: `scaffold/agent/evaluation_v1.py`
- Test: `tests/test_holdout_pipeline.py`

Run at least three trials per task for candidate comparison. Report pass@1, pass@N, safety failures, timeout rate, cost per accepted task, latency, retries, and failure taxonomy. Compare every candidate to a fixed baseline.

### Task 6.3: Add release candidate CI

**Files:**
- Create: `.github/workflows/agent-release.yml`
- Create: `scripts/release_gate.py`
- Modify: `Makefile`

Required jobs:

- Unit/contract tests
- Static checks
- Security tests
- Deterministic replay tests
- Public evaluation
- Private holdout on protected infrastructure
- Image build and digest verification
- Dependency/security audit
- Artifact validation

### Task 6.4: Canary rollout

**Files:**
- Create: `docs/operations/canary_policy.md`
- Create: `config/production_profiles/`

Rollout sequence:

1. Offline deterministic tests
2. Shadow mode on real tasks, no writes
3. Human-in-the-loop draft PRs
4. Small allowlisted repository cohort
5. Expanded cohort after stable metrics

Automatic rollback triggers:

- Safety violation
- Credential exposure
- Evidence-incomplete success
- Regression below release threshold
- Cost anomaly
- Workspace cleanup failure
- Repeated state corruption

---

# Phase 7 — Self-learning only after reliability

**Exit gate:** learning improves held-out outcomes without reducing safety or reproducibility.

### Task 7.1: Create a failure taxonomy and dataset

**Files:**
- Create: `scaffold/agent/learning/failure_taxonomy.py`
- Create: `scaffold/agent/learning/trace_dataset.py`
- Test: `tests/test_failure_taxonomy.py`

Classify:

- Intent/requirements failure
- Localization failure
- Planning/schema failure
- Tool-use failure
- Edit failure
- Test/verification failure
- Environment/dependency failure
- Timeout/resource failure
- Security/policy failure
- Delivery/review failure

### Task 7.2: Gate policy updates

**Files:**
- Modify: `prompt_evolver.py`, `ml_router.py`, `live_tool_synth.py`
- Create: `scaffold/agent/learning/policy_registry.py`
- Test: `tests/test_learning_policy_gate.py`

No learned prompt, tool, route, or verifier policy may affect production until it passes:

- Offline replay
- Public regression
- Private holdout comparison
- Safety suite
- Cost/latency check
- Human review or explicit policy approval

### Task 7.3: Measure actual learning

A learning change is successful only if a later held-out cohort shows statistically meaningful improvement in accepted-task rate, cost per accepted task, or time to acceptance with no safety regression. Memory growth alone is not evidence of learning.

---

# 4. Operational policies

## Default production policy

```yaml
execution:
  workspace: docker_or_remote
  network: disabled
  host_credentials: forbidden
  production_paths: forbidden
  max_runtime_seconds: 1800
  max_tool_calls: 100
  max_retries: 2
  max_cost_usd: 1.00
  auto_merge: false
  auto_deploy: false
  draft_pr_only: true
  human_approval_required: true
```

## Tool profiles

- `research`: read-only repository, search, bounded web access
- `coding`: workspace read/write, tests, no deployment/network by default
- `review`: read-only workspace plus diagnostics and diff inspection
- `delivery`: branch/PR operations only, explicit authorization
- `admin`: never available to ordinary agent runs

## Incident response

Document procedures for:

- Credential exposure
- Sandbox escape
- Unauthorized repository write
- Run state corruption
- Runaway spend
- Poisoned context/MCP prompt injection
- Malicious dependency or tool
- Incorrect accepted patch

---

# 5. Recommended execution order

Do not implement all phases simultaneously. Use these milestones:

### Milestone A — Reliable single run

Complete Phase 0, Phase 1, and the minimum of Phase 2. This is the immediate priority because current evidence identifies planner grounding and graph-schema failures.

### Milestone B — Safe production pilot

Complete Phase 4 and Phase 5, with Docker/remote workspace execution, draft-PR-only delivery, event replay, and operator controls.

### Milestone C — Evidence-based release

Complete Phase 6 with at least 20 private tasks, three trials, public regression, safety tests, and canary rollout.

### Milestone D — Self-learning production loop

Complete Phase 7 only after Milestone C. Learning must be evaluated as a candidate policy against a frozen baseline.

### Milestone E — Broader autonomous OS

Only after the coding-agent product has stable reliability, safety, cost, and operations metrics should AWOS expand into broader AI-operated-OS workloads.

---

# 6. Validation commands

Focused development:

```bash
python3 -m py_compile <changed_file.py>
python3 scripts/lsp_check.py --check <changed_file.py> --json
python3 scripts/focused_test.py <changed_file.py> --json
python3 -m pytest <direct_and_impacted_tests> -q
```

Core contracts:

```bash
python3 -m pytest \
  tests/test_execution_graph.py \
  tests/test_execution_graph_integration.py \
  tests/test_planner_graph_contract.py \
  tests/test_planner_graph_contract_compatibility.py \
  tests/test_planner_path_grounding.py \
  tests/test_context_packet_contract.py \
  -q
```

Security and runtime:

```bash
python3 -m pytest \
  tests/test_command_policy.py \
  tests/test_agent_security_policy.py \
  tests/test_secret_redaction.py \
  tests/test_workspace_provider.py \
  tests/test_resource_limits.py \
  tests/test_run_replay.py \
  -q
```

Release gate:

```bash
python3 scripts/release_gate.py --profile production-candidate
python3 scripts/run_holdout_pipeline.py --profile production-candidate
python3 scripts/verify_holdout_comparison.py --strict
make test
```

---

# 7. Risks and tradeoffs

- **Complexity risk:** Do not build a distributed service prematurely. First make the single-run state machine correct and replayable.
- **Sandbox cost:** Remote sandboxes cost money and add latency, but host execution is not acceptable as the production default.
- **Model dependence:** Keep provider adapters replaceable; evaluate the harness, not one model’s marketing score.
- **Critic overconfidence:** Critics are advisory until calibrated on production outcomes.
- **Index staleness:** Every retrieval result needs an index digest and changed-file invalidation.
- **Benchmark overfitting:** Keep private tasks and production traces separate from public benchmark tuning.
- **False success:** A green unit test is not enough; require patch scope, acceptance, review, and evidence completeness.
- **Operational overload:** Prefer simple status/replay/runbook interfaces over a dashboard that hides the authoritative event log.
- **Security supply chain:** Pin MCP servers, images, dependencies, and provider adapters; audit before enabling.
- **Repository churn:** The current worktree contains extensive modified/untracked artifacts. Before production release, create a clean release branch/worktree and define exactly which files are product source, generated artifacts, fixtures, and docs.

---

# 8. Definition of done

AWOS may be called production-ready for the initial draft-PR workload only when:

- The run contract and state machine pass restart/idempotency tests.
- Planner grounding and graph-schema compatibility pass live-path regression tests.
- Exact context bounds are enforced.
- Every production run uses an isolated workspace and policy profile.
- Credential and network boundaries pass adversarial tests.
- Every completed run has a complete result bundle and replayable event history.
- Operators can pause, cancel, resume, and inspect runs.
- Draft PR delivery is human-gated and never auto-merges by default.
- A protected private corpus demonstrates the agreed pass rate across multiple trials.
- Public regressions, safety tests, replay tests, and image/dependency checks pass.
- Cost, latency, retry, timeout, and acceptance metrics are available per run.
- A canary cohort runs without safety, cleanup, state, or evidence regressions.
- Any self-learning change is separately evaluated against a frozen baseline.

Until these conditions are met, use the wording “production-hardening” or “pilot-ready,” not “production-ready.”

---

# Sources

[1] LangChain Open SWE repository — sandbox isolation, curated tools, middleware, invocation, and draft PR architecture: https://github.com/langchain-ai/open-swe

[2] SWE-ReX repository — execution/runtime separation, shell-session lifecycle, interactive commands, and parallel sandbox execution: https://github.com/SWE-agent/SWE-ReX

[3] OpenHands Software Agent SDK paper — durable state, sandbox portability, immutable configuration, event-sourced replay, typed tools, lifecycle control, observability, and QA: https://arxiv.org/html/2511.03690v1

[4] OpenHands verification research — layered verification, trajectory critics, production supervision, code-survival signals, and inference-time selection: https://www.openhands.dev/blog/20260305-learning-to-verify-ai-generated-code

[5] OpenHands benchmarks repository — pinned SDK/image compatibility, public benchmark tracks, rich logging, local/remote workspaces, and parallel evaluation: https://github.com/OpenHands/benchmarks

[6] mini-SWE-agent repository — simple control flow, local CLI, stable sandboxing, and benchmark evaluation: https://github.com/SWE-agent/mini-swe-agent

[7] OpenHands Software Agent SDK repository: https://github.com/OpenHands/software-agent-sdk

[8] NIST AI Risk Management Framework / agentic-profile search reference: https://www.nist.gov/itl/ai-risk-management-framework

---

# Final recommendation

The next implementation sprint should be Milestone A only:

1. Planner path grounding repair.
2. Graph-schema compatibility repair.
3. Unified run contract/state machine.
4. Strict context-bound enforcement.
5. Docker workspace provider with credential/network isolation.
6. One complete result bundle and replay proof.
7. A 10-task internal holdout run before adding more learning or UI features.

This sequence converts AWOS from a broad, well-instrumented prototype into a trustworthy pilot system. More features before these gates would increase surface area without increasing production confidence.
