# AWOS Codebase Index

> **Purpose:** Master navigation map for Cursor semantic search and agents.
> **Cold-start:** Read `AGENT_INDEX.md` + `memories/repo/project_structure.md` first.
> **Identity:** `VISION.md` only — never duplicate here.

Last indexed: 2026-06-29

---

## Repository Layout

```
AWOS_coding_agent/
├── awos.py                 # CLI entry (worker, mission, sessions, stats, report)
├── scaffold/agent/         # ★ Active Python kernel (135 modules)
├── tests/                  # Primary pytest suite (67 test_*.py)
├── scripts/                # Demos, benchmarks, quality gates
├── docs/                   # Research → spec → proof artifacts
├── tasks/active/           # Execution checklists
├── protocols/              # Operating protocols
├── memories/repo/          # Canonical project facts (single owner)
├── wiki/                   # Glossary, agentic concepts
├── gui/                    # Web GUI (server.py + static/)
├── compute/                # Stage 2 placeholder (bare-metal)
├── src/                    # Legacy TS fork — NOT active kernel
└── .awos/                  # Runtime state (sessions, rewards, skills)
```

**Active code path:** `awos.py` → `scaffold/agent/orchestrator.py` → planner/worker/verifier → `.awos/`

---

## Kernel Execution Loop

| Module | Path | Role |
|--------|------|------|
| Orchestrator | `scaffold/agent/orchestrator.py` | Plan → execute → verify → learn loop |
| Planner | `scaffold/agent/planner.py` | Goal → atomic JSON task blueprint |
| Worker | `scaffold/agent/worker.py` | SEARCH/REPLACE edits via LLM |
| ReAct Worker | `scaffold/agent/react_worker.py` | Observe → tool → repeat loop |
| Verifier | `scaffold/agent/verifier.py` | Apply patch, syntax/lint gate |
| Unified Agent | `scaffold/agent/unified_agent.py` | Coding App on kernel |
| DAG Executor | `scaffold/agent/dag_executor.py` | Parallel task waves by dependency |

### Planning & Goals

| Module | Path | Role |
|--------|------|------|
| Goal Clarifier | `scaffold/agent/goal_clarifier.py` | Ambiguity detection + questions |
| Plan Reviewer | `scaffold/agent/plan_reviewer.py` | HITL plan checkpoint |
| Exploration Phase | `scaffold/agent/exploration_phase.py` | Pre-plan repo grep/find |
| Project Planner | `scaffold/agent/project_planner.py` | Cross-session Goal DAG |
| Task Decomposer | `scaffold/agent/task_decomposer.py` | Failed task → sub-tasks |
| Cheap Planner | `scaffold/agent/cheap_planner.py` | Low-cost planning tier |

### Editing & Code Intelligence

| Module | Path | Role |
|--------|------|------|
| Search/Replace Parser | `scaffold/agent/search_replace_parser.py` | SEARCH/REPLACE block parser |
| Edit Models | `scaffold/agent/edit_models.py` | Structured JSON edit contract |
| File Resolver | `scaffold/agent/file_resolver.py` | Short name → full path |
| Symbol Index | `scaffold/agent/symbol_index.py` | Cross-file symbol + import graph |
| Cartographer | `scaffold/agent/cartographer.py` | Tree-sitter AST extraction |
| Fault Localizer | `scaffold/agent/fault_localizer.py` | Hierarchical fault funnel |
| Coding Tools | `scaffold/agent/coding_tools.py` | ReAct worker tool registry |
| Lint Pipeline | `scaffold/agent/lint_pipeline.py` | Post-write Ruff/pyright loop |

### Verification

| Module | Path | Role |
|--------|------|------|
| Test Runner | `scaffold/agent/test_runner.py` | Language-agnostic test executor |
| Self Verification | `scaffold/agent/self_verification.py` | In-memory pre-flight check |
| Integration Reviewer | `scaffold/agent/integration_reviewer.py` | Post-exec coherence review |
| Stability Gate | `scaffold/agent/stability_gate.py` | Suite stability before evolver |

### Model Routing & Cost

| Module | Path | Role |
|--------|------|------|
| Escalation Engine | `scaffold/agent/escalation_engine.py` | 5-tier model ladder |
| ML Router | `scaffold/agent/ml_router.py` | LinUCB contextual bandit |
| Budget Ledger | `scaffold/agent/budget_ledger.py` | Month-to-date spend tracking |
| Token Tracker | `scaffold/agent/token_tracker.py` | Per-request token visibility |
| Cache Telemetry | `scaffold/agent/cache_telemetry.py` | Prompt cache hit rates |
| Complexity Scorer | `scaffold/agent/complexity_scorer.py` | 1–10 complexity from STRUCT |

### Self-Learning & Reward

| Module | Path | Role |
|--------|------|------|
| Reward Store | `scaffold/agent/reward_store.py` | `compute_reward()` — quality × speed ÷ cost |
| Prompt Evolver | `scaffold/agent/prompt_evolver.py` | Experience → evolved prompts |
| Live Tool Synth | `scaffold/agent/live_tool_synth.py` | Failures → synthesized helpers |
| Scaffold Evolver | `scaffold/agent/scaffold_evolver.py` | Test-gated scaffold mutations |
| Skill Library | `scaffold/agent/skill_library.py` | Auto skill knowledge base |
| Error Pattern Store | `scaffold/agent/error_pattern_store.py` | Reflexion verbal memory |
| Post Mortem | `scaffold/agent/post_mortem.py` | Failure → critique → recall |
| Process Reward Model | `scaffold/agent/process_reward_model.py` | MCTS pass probability |
| Self Correction | `scaffold/agent/self_correction.py` | Failure analysis + retry hints |
| Learning State | `scaffold/agent/learning_state.py` | Durable learning KPI windows |

### Sessions & Isolation

| Module | Path | Role |
|--------|------|------|
| Runtime Session | `scaffold/agent/runtime_session.py` | Pause/resume (`.awos/sessions/rs_*.json`) |
| Virtual Execution | `scaffold/agent/virtual_execution_runtime.py` | Git worktree isolation |
| Worktree | `scaffold/agent/worktree.py` | Safe parallel execution |
| Agent State Manager | `scaffold/agent/agent_state_manager.py` | Per-goal state persistence |
| Session Memory | `scaffold/agent/session_memory.py` | Chat sliding window + summary |

### Memory Subsystem

| Module | Path | Role |
|--------|------|------|
| Vector Memory | `scaffold/agent/vector_memory.py` | ChromaDB semantic memory facade |
| Codebase Index | `scaffold/agent/memory/codebase_index.py` | Meaning-based code search |
| Memory Store | `scaffold/agent/memory/store.py` | Episodic / semantic / working tiers |

### Tools Subsystem (`scaffold/agent/tools/`)

| Module | Role |
|--------|------|
| `base.py` | Tool ABC + registry |
| `filesystem.py` | Read/write/list |
| `shell.py` | Shell execution (ALLOW_SHELL gate) |
| `code_runner.py` | Python subprocess |
| `fetch_url.py` | HTTP fetch |
| `web_search.py` | Tavily / DuckDuckGo |
| `github_tool.py` | GitHub API |
| `huggingface_tool.py` | HuggingFace Hub |

### Core Infrastructure (`scaffold/agent/core/`)

| Module | Role |
|--------|------|
| `orchestrator.py` | Research → plan → execute pipeline |
| `observability.py` | TaskSpan + MetricsSummary |
| `async_engine.py` | Circuit breaker, backpressure |
| `queue_model.py` | M/M/1 queue + regret |
| `performance_tracker.py` | Tool success per model × task |
| `reasoning.py` | ReAct trace persistence |

### GUI, Git, Diagnostics

| Module | Path | Role |
|--------|------|------|
| GUI Chat | `scaffold/agent/gui_chat.py` | HTTP bridge for web UI |
| Live Renderer | `scaffold/agent/live_renderer.py` | Terminal activity display |
| Git Manager | `scaffold/agent/git_manager.py` | Branch + rollback |
| Run Diagnostics | `scaffold/agent/run_diagnostics.py` | Stage tracing |
| PEI Report | `scaffold/agent/pei_report.py` | Client scorecard |
| Self Learning Metrics | `scaffold/agent/self_learning_metrics.py` | `.awos/` observability |

### Config

| Module | Path | Role |
|--------|------|------|
| Settings | `scaffold/agent/config/settings.py` | Env-var driven config |

---

## CLI (`awos.py`)

```
awos worker start|status|resume|diff|cancel
awos mission guide|start|status
awos run [goal] [--resume] [--session RS_ID]
awos sessions list|show|resume
awos gauntlet list|run <ID>
awos memory search|stats
awos traces list|show|clean
awos budget | performance | index | goals
awos stats [--json] [--savings]
awos report [--html] [--json]
awos guide | chat
```

Handler pattern: `build_parser()` → `cmd_<command>` or `cmd_<parent>_<subcommand>`.

---

## Scripts (`scripts/`)

### Live Proof Demos

| Script | Feature |
|--------|---------|
| `demo_react_worker.sh` | ReAct worker stack |
| `demo_stagnation_breaker.sh` | Stagnation breaker |
| `demo_clarification.sh` | Goal clarification |
| `demo_ci_rescue_sprint.sh` | CI repair benchmark corpus (no API) |
| `demo_ci_rescue_sprint_live.sh` | CI repair benchmark live API |
| `demo_ci_rescue_head_to_head.sh` | CI repair benchmark: AWOS vs raw API |
| `demo_clawcode_ci_rescue_head_to_head.sh` | Clawcode CI repair benchmark head-to-head |
| `demo_code_evolution_lab.sh` | Evolution lab fixture |
| `demo_code_evolution_benchmark.sh` | Multi-commit benchmark |
| `demo_wedge_v1_test_rescue.sh` | Test Rescue wedge |
| `demo_phase_d_clawcode.sh` | Phase D clawcode mission |

### Benchmarks

| Script | Purpose |
|--------|---------|
| `benchmark_vs_raw_api.py` | Harness vs single-shot API |
| `benchmark_ci_rescue_sprint.py` | 18-fix CI repair benchmark |
| `benchmark_clawcode_ci_rescue.py` | Clawcode 12-fix CI repair benchmark |
| `benchmark_code_evolution_lab.py` | Zero-regression multi-commit |
| `compare_baseline.py` | Diff vs frozen baseline v1 |
| `stage1_gauntlet_runner.py` | Risk gauntlet G1–G6 |

### Tooling

| Script | Purpose |
|--------|---------|
| `quality_gate.py` | Pre-commit: tests + lint |
| `session_checkpoint.py` | Auto checkpoint generation |
| `obsidian_lint.py` | Wiki link health |
| `fact_lint.py` | Duplicate fact detection |

---

## Tests (`tests/`)

Grouped by feature. Run: `pytest` (see `pytest.ini` — 10s timeout, skips integration).

| Area | Key files |
|------|-----------|
| Orchestrator | `test_adversarial_orchestrator.py`, `test_pause_resume_orchestrator.py`, `test_verify_fail_replan.py` |
| Worker/ReAct | `test_react_worker.py`, `test_structured_edit.py`, `test_self_correction.py` |
| Planner/Goals | `test_project_planner.py`, `test_mission_plan_loader.py`, `test_deliverable_router.py` |
| Verification | `test_verifier_fidelity.py`, `test_test_runner.py`, `test_stability_gate.py` |
| Sessions | `test_runtime_session.py`, `test_awos_sessions_cli.py`, `test_stagnation_breaker.py` |
| Self-learning P1–P9 | `test_p1_improvements.py` … `test_p9_confidence.py`, `test_self_learning_e2e.py` |
| MCTS | `test_mcts_search.py`, `test_process_reward_model.py` |
| Memory | `test_vector_memory.py` (ignored by default), `test_cartographer.py` |
| Routing | `test_ml_meta_controller.py`, `test_circuit_breaker.py`, `test_queue_model.py` |
| Wedge/Benchmark | `test_ci_rescue_sprint_corpus.py`, `test_code_evolution_corpus.py`, `test_wedge_v1_assert.py` |
| GUI | `test_gui_chat_store.py`, `test_gui_browser.py` |

Fixtures: `tests/fixtures/wedge_v1/` (payment_utils, ci_rescue_sprint, code_evolution).

---

## Documentation Map

| Category | Location | Notes |
|----------|----------|-------|
| Identity | `VISION.md` | Sole philosophy |
| Process | `AWOS.md`, `protocols/` | Research → Spec → Task → Live proof |
| Facts | `memories/repo/project_structure.md` | Single owner for metrics |
| Research | `docs/research/*.md` | Before implementation |
| Specs | `docs/specs/*.md` | Atomic steps |
| Live proof | `docs/*_proof.md` | Runnable demo markers |
| Checkpoints | `docs/memory/checkpoint_*.md` | Session handoffs |
| Missions | `docs/missions/*.json` | Mission configs |
| Product | `docs/product/` | Pilot, positioning, head-to-head |
| Benchmarks | `docs/benchmarks/BASELINE.md` | Frozen baseline v1 |
| Gauntlet | `docs/stage1_risk_gauntlet_booklet.md` | G1–G6 stress catalog |

---

## Runtime State (`.awos/`)

| Path | Contents |
|------|----------|
| `.awos/sessions/rs_*.json` | Durable runtime sessions |
| `.awos/reward_store.jsonl` | Episode log |
| `.awos/spans.jsonl` | Observability spans |
| `.awos/skills/` | Compiled skill library |
| `.awos/goals/` | Goal DAG persistence |
| `.awos/worktrees/` | **Excluded from Cursor index** (full repo copies) |

---

## Key Entry Points for Common Tasks

| Task | Start here |
|------|------------|
| Fix orchestrator loop | `scaffold/agent/orchestrator.py` |
| Change edit format | `scaffold/agent/edit_models.py`, `search_replace_parser.py` |
| Add CLI command | `awos.py` → `build_parser()` + `cmd_*` |
| Add self-learning | `reward_store.py`, `prompt_evolver.py`, `learning_policy.py` |
| Add live proof | `scripts/demo_<feature>.sh` + `docs/<feature>_proof.md` |
| Add test | `tests/test_<feature>.py` |
| New feature workflow | `docs/research/` → `docs/specs/` → `tasks/active/` |

---

## Related

- `AGENT_INDEX.md` — agent cold-start map
- `AGENTS.md` — session instructions
- `docs/CURSOR_INDEXING_SETUP.md` — ignore files + reindex steps
- `memories/repo/project_structure.md` — current phase and metrics
