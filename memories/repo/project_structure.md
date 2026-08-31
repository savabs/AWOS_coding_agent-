# AWOS — Project Structure

> **Canonical owner for current project facts, phase, and active priorities.**
> Product identity lives in [[VISION]].

---

## Identity

**AWOS** is a learnable operating system for autonomous work.

**Current pathway:**
1. build a **proper coding agent**
2. make it **self-learning from its own failures**
3. expand that base into an **AI-operated OS**

**Current repo focus:** the coding agent is App #1 and the main focus of this repository.

**Build strategy:** reuse and improve the existing AWOS kernel and agent components rather than building a coding agent from scratch or copying Cursor, Zed, Hermes, or another codebase. Those products are capability benchmarks, not implementation templates.

**Capability bar:** deliver repository understanding, planning, safe editing, tool use, verification, recovery, and a clear interface at the level expected from leading coding agents, while retaining AWOS's distinct evidence-first, resource-aware, failure-learning approach.

**Objective:** maximize **Quality × Speed ÷ Cost**.

**Allowed techniques:** LLMs, ML, DL, RL, tools, verification systems, memory systems, databases, low-level optimization, GPU/CUDA work, and general software-engineering techniques — anything that improves the objective.

**Important correction (2026-07-26):** CI repair remains a benchmark/workflow only. It is not the defining identity of AWOS.

---

## Status — 2026-08-31

**AWOS is no longer being pursued as a revenue product.** Measured against its
own June bar on 2026-08-31 and lost to a single API call to the model it wraps.
It remains a tool and a portfolio piece; the write-up is publishable.

Active work has moved to selecting a next venture under explicit disqualifying
criteria. See [[checkpoint_2026-08-31_bar_test_and_pivot]],
[[venture_disqualifying_criteria]], [[venture_scan_protocol]].

The phase table below describes AWOS's own development and is retained as
historical context.

---

## Current Phase

| Phase | Description | Status |
|---|---|---|
| 0 | Workflow backbone / research-spec-task discipline | DONE |
| 1 | Proper coding agent: planning, execution, verification, recovery | **ACTIVE** |
| 2 | Self-learning coding agent: improve by solving prior failures | NEXT |
| 3 | AI-operated OS expansion | LATER |

**Current phase:** 1 — proper coding agent

---

## Architecture

```text
Kernel / Harness
────────────────────────────────────────────
orchestrator.py
reward_store.py
ml_router.py
escalation_engine.py
budget_ledger.py
prompt_evolver.py
live_tool_synth.py
scaffold_evolver.py
vector_memory.py
skill_library.py
agent_state_manager.py
dag_executor.py
execution_graph.py

App #1 — Coding Agent
────────────────────────────────────────────
planner.py
worker.py
verifier.py
git_manager.py
cartographer.py
test_runner.py
ci_discovery.py
ci_planner.py
ci_display.py

gui/ and awos-app/
────────────────────────────────────────────
Interfaces for talking to the coding agent
```

**Runtime state:** `.awos/` stores sessions, rewards, traces, memory, tools, skills, and other evolving state.

---

## Key Modules

| Module | Path | Purpose |
|---|---|---|
| CLI entry | `awos.py` | Commands for running and inspecting the agent |
| Kernel loop | `scaffold/agent/orchestrator.py` | Plan → execute → verify → learn; emits live debug bundles |
| Objective function | `scaffold/agent/reward_store.py` | `compute_reward()` — quality × speed ÷ cost |
| Router | `scaffold/agent/ml_router.py` | Learnable model/policy selection from task context and outcomes; not the final quality authority |
| Escalation | `scaffold/agent/escalation_engine.py` | Cheap-first fallback ladder |
| Prompt learning | `scaffold/agent/prompt_evolver.py` | Failure/success → prompt refinement |
| Tool learning | `scaffold/agent/live_tool_synth.py` | Repeated manual steps → tools |
| Self-patching | `scaffold/agent/scaffold_evolver.py` | Controlled scaffold evolution |
| Coding execution | `scaffold/agent/worker.py` | Edit application |
| Coding verification | `scaffold/agent/verifier.py` | Syntax + apply + validation gates |
| Execution graph | `scaffold/agent/execution_graph.py` | Typed provider-free execution DAG for edits, tests, barriers, reviews, recovery, and final verification; computes deterministic safe waves |
| Evaluation v1 | `scaffold/agent/evaluation_v1.py` | Versioned fixtures, hard acceptance/safety gates, trajectories, reports, and regression comparison |
| Holdout corpus contract | `scaffold/agent/evaluation_corpus.py` | Fail-closed private holdout manifest, digest-bound report bundles, and per-task baseline/candidate regression comparison |
| Holdout comparison CLI | `scripts/verify_holdout_comparison.py` | Operator-only local artifact verifier with deterministic output and hard-gated exit status |
| Private holdout setup | `scripts/init_private_holdout.py` | Creates ignored local holdout directories and metadata-only template without private data |
| Private holdout pipeline | `scripts/run_holdout_pipeline.py` | Offline path-bound wrapper delegating to the hard-gated comparison verifier |
| Holdout smoke proof | `scripts/run_holdout_smoke.py` | Three-task deterministic runner → bundle → manifest → comparison plumbing proof; not capability evidence |
| Zed project skills | `.agents/skills/` | Session start, change gate, evaluation discipline, and debugging/safety instructions for AWOS development sessions |
| Evidence debugger | `scaffold/agent/core/debug_*.py` | Evidence bundles, bounded proof, inspection, controlled reruns |
| Web server | `gui/server.py` | Interface surface |
| Web UI | `gui/static/agent.html`, `gui/static/dashboard.html` | Human interface to the agent |
| Electron app | `awos-app/` | Native shell for the interface |

---

## Current Priorities

| Priority | Focus | Status |
|---|---|---|
| 1 | Native graph execution | COMPLETE — native Planner graph runtime and 10-module provider-free evaluation pass (2026-08-03) |
| **Proper coding agent reliability** | ACTIVE |
| 2 | Assistant live observation bridge | **ACTIVE / LIVE PROOF BLOCKED** — implementation complete through replay/SSE/snapshot/CLI; Electron must be restarted — see [[assistant_live_observer]] |
| 3 | Strong app/interface for talking to the agent | ACTIVE |
| 4 | Verification and recovery quality | ACTIVE |
| 5 | Typed execution graph | ACTIVE — provider-free contract, native Planner runtime, and multi-module evaluation complete; Planner uses OpenCode Go qwen3.7-plus and workers use deepseek-v4-pro (2026-08-03) |
| 6 | Self-learning from failures | PLANNED / PARTIAL — evaluation v1 harness complete; live-agent adapter and held-out corpus remain |
| 7 | Broader OS expansion | DEFERRED |
| 8 | Quality gate reliability | DONE — incremental lint scope and explicit artifact mode |
| 9 | Controlled rerun validation | DONE — shell-free local rerun executor |

---

## Current Metrics

| Metric | Value | Last updated |
|---|---|---|
| Model tiers | 9 (8 working, 1 down) via OpenCode Go | 2026-07-21 |
| Working models | 8/15 validated | 2026-07-21 |
| Chat latency | 5–8s via DeepSeek V4 Flash (primary) | 2026-07-21 |
| Development provider | OpenCode Go ($10/mo) is the currently validated provider; architecture remains provider-agnostic | 2026-07-21 |
| Budget spent | $2.09 of $20/mo | 2026-07-21 |
| SWE-bench Lite (dev) | 1/1 passed (sqlfluff-2419) | 2026-08-01 |
| Total requests | 2,154 | 2026-08-01 |
| Budget spent | $2.12 of $20/mo | 2026-08-01 |
| Diagnostics | Timing sensors (HTTP + LLM call) | 2026-07-21 |
| Kernel LOC | ~40K across 140 modules | 2026-07-21 |
| Active task files | 75 in `tasks/active/` (includes legacy artifacts, templates, and completed handoff artifacts) | 2026-07-29 |
| Current phase | Phase 1 — proper coding agent | 2026-07-27 |
| E2E tests | 3 (`tests/test_orchestrator_e2e.py`, `tests/test_coding_agent_minimal_e2e.py`, `tests/test_test_outcome_gate.py`) | 2026-07-29 |
| Evaluation v1 tests | 13 passed; orchestrator adapter, public fixture contracts, deterministic correct/wrong/replay CLI proofs pass as designed | 2026-07-29 |
| Dead models filtered | 5 | 2026-07-24 |
| Observer contract tests | 49 focused tests passing | 2026-07-28 |
| Observer API surface | `/api/observer/ui-event`, `/events`, `/stream`, `/snapshot` plus `awos observe` CLI | 2026-07-28 |

---

## Active Work

| Task | Focus | Status |
|---|---|---|
| **Recenter self-improving coding agent** | Align repo to coding-agent-first pathway | **ACTIVE** — see [[recenter_self_improving_coding_agent]] |
| **Agent interface pathway** | Make the app the right interface to the coding agent | **ACTIVE** — see [[pivot_web_app_product]] |
| **Assistant live observer** | Correlated, replayable event bridge between AWOS and the external assistant | **ACTIVE / LIVE PROOF BLOCKED** — implementation complete; stale Electron process must be quit/restarted — see [[assistant_live_observer]] |
| **Session resume lease** | Exclusive admission for concurrent paused-session resumes | **ACTIVE / IMPLEMENTED** — lease contention and dead-owner recovery proven; broader session save locking remains out of scope |
| **Live agent renderer** | Developer renderer over existing execution telemetry | **ACTIVE / LIVE PROOF PENDING** — see [[live_agent_renderer]] |
| **Chat prompt fidelity** | Exact user-input and provider-request traceability | **ACTIVE / LIVE PROOF PENDING** — see [[chat_prompt_fidelity]] |
| **Routing learning repair** | Fix bugs preventing learned routing from working properly | **DONE** — see [[routing_learning_repair]] |
| **Wedge v1 test rescue** | Internal CI repair benchmark for the coding agent | **ACTIVE** — see [[wedge_v1_test_rescue]] |
| **CI repair corpus** | Benchmark corpus for medium-scale coding-agent validation | **ACTIVE** — see [[ci_rescue_sprint_corpus]] |
| **OpenRouter integration** | Additional provider support | **NEAR DONE** |
| **Orchestrator E2E** | Strengthen end-to-end coding-agent proof | **ACTIVE / IMPORTANT** — test outcome gate complete; broader agent capabilities remain |
| **Self-learning evaluation v1** | Measure correctness, safety, trajectories, reward, cost, latency, and regressions | **DONE for deterministic harness, live adapter, holdout manifest, report verifier, operator CLI, local workspace setup, offline pipeline, and smoke proof; private corpus and live credentialed runs next** |
| **Small holdout evaluation v1** | Run the first real-orchestrator evaluation with one task, one trial, and bounded storage | **ACTIVE — corrected live trial reached Qwen and failed on native graph schema incompatibility; cost telemetry for early Planner failure is also pending fix; new authorization required for any rerun** |
| **SWE-bench Lite dev v1** | Evaluate one real repository issue from the official 23-task development split | **DONE** — Task `sqlfluff__sqlfluff-2419` passed with pass_at_1=1.0 and cost ~$0.03 |
| **Fixed OpenCode Go agent path** | Keep the initial product on one provider/model while core reliability is built | **DONE offline — OpenCode Go + deepseek-v4-pro path and tests pass; live SWE-bench retry pending planner-grounding repair** |
| **Quality gate reliability** | Incremental lint scope and deterministic task gate | **DONE** — see [[quality_gate_reliability]] |
| **Controlled rerun validation** | Reproduce bounded observations with local evidence | **DONE** — see [[controlled_rerun_executor]] |
| **File editor** | Workspace editing UX | **PRIORITY** |
| **AI + editor** | Agent sees and edits open files cleanly | **PRIORITY** |
| **Workspace management** | Multiple repos, per-repo `.awos/` | **PRIORITY** |

---

## Key Decisions

| Decision | Rationale | Date |
|---|---|---|
| Proper coding agent comes first | Self-learning is meaningless without a strong base agent | 2026-07-26 |
| Self-learning means solving prior failures better | Repo/user-memory alone is not enough | 2026-07-26 |
| AWOS can use any technique that improves Q × S ÷ Cost | This is a systems project, not an LLM-only project | 2026-07-26 |
| App is the interface to the coding agent | UI exists to talk to and inspect the agent | 2026-07-26 |
| CI repair is a benchmark, not the identity | Useful proving workload, wrong top-level framing | 2026-07-26 |
| One provider and one fixed model define the initial coding agent | Use OpenCode Go exclusively with `deepseek-v4-pro` on the active product path; do not use direct DeepSeek or dynamic provider/model fallback until the core product is reliable | 2026-07-30 |
| Dynamic routing is learnable allocation | Learn task difficulty, uncertainty, context, historical outcomes, latency, and cost to avoid overusing strong models or underpowering difficult tasks; verification remains the quality authority | 2026-07-27 |
| Exploration is a first-class agent capability | Deep, bounded repository exploration is required for intelligent planning and safe execution | 2026-07-27 |
| Simple programmer-oriented UI with parallel tasks | The interface should expose engineering state without becoming an overloaded dashboard | 2026-07-27 |
| Compatible reuse is encouraged | Adopt compatible ideas and open-source components after capability, license, security, and architecture review | 2026-07-27 |
| App #1 acceptance criteria | Strengthen the coding agent, preserve reusable kernel boundaries, improve Q × S ÷ Cost, help future autonomous workloads, and differentiate through behavior | 2026-07-27 |
| Quality is non-negotiable; speed and cost are tunable | A slower/cheaper path and a faster/more expensive path are both valid if they meet the same quality floor; speed is never purchased by lowering quality | 2026-07-27 |
| Differentiation must be behavioral | Demonstrate exploration, evidence, parallel work, verification, recovery, and failure-driven improvement rather than relying on product claims | 2026-07-27 |
| Every plan must serve the full AWOS vision | AWOS is a learnable operating system for autonomous work; the coding agent, debugger, router, and benchmarks are subsystems or App #1, not the final identity. Planning must avoid narrowing AWOS into any single feature, provider, workload, or interface. | 2026-07-27 |
| Glass-box routing remains important | Transparency helps debugging and trust | 2026-07-03 |
| ScaffoldEvolver stays off by default | Self-modification requires hard safety gates | 2026-07-03 |

---

## Planning Guardrail

Every new plan, specification, and implementation must be evaluated against the canonical vision in `VISION.md`:

> AWOS is a learnable operating system for autonomous work. The coding agent is App #1, not the final product.

Before starting work, explicitly identify how the proposed change contributes to the broader autonomous-work system. Do not let a subsystem become the project's identity by accident. Model routing, evidence-first debugging, CI repair, UI work, and provider integrations are enabling capabilities; they must remain interoperable, extensible, and useful beyond their immediate implementation.

The coding agent must retain AWOS's distinctive additions rather than becoming a generic coding-agent rebuild. Before designing or writing new code, inspect the existing modules and workflows, identify what can be reused or composed, and add new implementation only for a verified capability gap. Existing AWOS capabilities—including evidence-first debugging, controlled reruns, verification, recovery, learning, resource awareness, and glass-box observability—must be treated as product value and integrated into the coding-agent workflow. Repository exploration, symbol discovery, search, dependency tracing, and evidence collection are core intelligence capabilities, not optional UI features.

CI repair, test rescue, and repository maintenance are workloads and benchmarks, not kernel identity. Do not hard-code CI assumptions into reusable orchestration or learning contracts. Provider integrations are replaceable adapters; OpenCode Go is the current development provider, not a permanent dependency. Prefer simple programmer-oriented interfaces that support isolated, observable parallel tasks over dashboard-heavy surfaces.

---

## Obsolete / Superseded

| Idea | Replaced by | Why |
|---|---|---|
| CI Rescue as project identity | CI repair as one benchmark workload | Too narrow and misleading |
| Agency / pilot / pricing-first framing | Coding-agent-first engineering pathway | Core capability is not ready to be framed that way |
| “Learns your codebase” as the main self-learning story | Failure-driven self-learning | Closer to the real technical ambition |
| App as GTM shell | App as interface to the coding agent | More accurate to current mission |

---

## Cold-Start Protocol

```bash
# 1. Identity
cat VISION.md

# 2. Project facts
cat memories/repo/project_structure.md

# 3. App/UI state
cat memories/repo/ui_state.md

# 4. Last session
ls -t docs/memory/ | head -1 | xargs cat

# 5. Active tasks
cat tasks/active/*.md

# 6. Operational protocols
cat AWOS.md
```

Do not re-read the entire codebase. Follow the links.
