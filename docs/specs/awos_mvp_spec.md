---
title: "Spec: AWOS terminal MVP (walkable product)"
tags:
  - doc/spec
  - phase/1
  - topic/awos
  - topic/diramide
  - layer/context-optimization
---

# Spec: AWOS terminal MVP (walkable product)

> **Purpose:** Define the smallest **terminal-native autonomous engineering loop** for ongoing work on **Diramide** (and similar repos), without Copilot Pro+ dependency. Advanced cognition (RL, GNNs, symbolic stacks, world models) is **explicitly out of scope** until this core is stable.

## Goal

Deliver a **walkable product**: a Typer-based CLI that gives daily leverage through **repository awareness**, **context compression**, **terminal execution**, **bounded edit–test–repair loops**, **persistent memory** under `.awos/`, and **cost-aware model routing** — integrated naturally with a **Vim-first** terminal workflow.

**Exit condition (Milestone 1):** From the Diramide repo root, a developer can run `awos map`, `awos ask`, `awos run`, and `awos memory` such that (a) project soul + task state persist across sessions, (b) at least one **automated** test failure → structured summary → single retry path exists (even if skeletonization is initially shallow), (c) model tier is selectable and defaults to a cheap API, (d) autonomous edits land on a **non-main git branch**.

## Research reference

[[awos_mvp]] — context entropy, skeleton/hydrate hypothesis, MVP constraints.

## Architectural philosophy (non-negotiables)

1. **Repository as structured cognitive environment** — not flat text dumps.
2. **Context Optimization Layer** — Tree-sitter–based **skeletons** (signatures, classes, imports/exports, deps, short behavioral summaries); **hydrate** full implementations only when editing or debugging requires it.
3. **Terminal as closure** — compile, test, capture failures; prefer **structured** failure semantics over raw log paste.
4. **Git mandatory** — major autonomous changes on **isolated branches**; user keeps rollback.
5. **Memory is lightweight files** — `.awos/project_soul.md` (why / philosophy / recurring bugs / constraints), `memory.json`, `task_history.json` — not a bespoke database in MVP.
6. **Model routing** — Sonnet-class for hard architecture/debug; DeepSeek (etc.) for repetitive edits; **local Ollama** for summarization/indexing/search when wired; never default to frontier for everything.

## CLI surface (MVP)

| Command | Responsibility |
|---------|----------------|
| `awos map` | Build or refresh repo map / skeleton index (incremental ok in v0). |
| `awos ask` | Read-only Q&A using compressed context + optional hydration. |
| `awos edit` | Apply a bounded edit with hydration of touched files only. |
| `awos run` | Execute shell command(s); return structured result (exit code, parsed errors when possible). |
| `awos debug` | Orchestrate a **small** edit–test–repair loop (cap iterations). |
| `awos memory` | Show/update `.awos/` memory files (soul, tasks, history). |

## Internal components (MVP)

| Component | Role |
|-----------|------|
| **FileAgent** | Read/write; skeletonize (Tree-Sitter); hydrate on demand — **foundation**. |
| **TerminalAgent** | Run commands; structured failure extraction (categories, files, symbols where feasible). |
| **Context Builder** | Assemble prompts from skeleton + soul + active task + diffs. |
| **Task Planner** | Lightweight planner only (no hierarchical MCTS in MVP). |
| **Model Router** | Complexity-aware tier selection; integrates with existing pricing truth ([[models_pricing_catalog]]). |
| **Persistent Memory** | `.awos/` directory lifecycle; soul append/update rules. |

## Phased implementation (disciplined)

### Phase 0 — Repo shell (no Tree-Sitter yet)

- Typer package `awos/` with stub subcommands wiring to `--help`.
- `.awos/` bootstrap: create `project_soul.md` template, `memory.json`, `task_history.json` if missing.
- Git: `awos` ensures branch naming for autonomous sessions (e.g. `awos/session-YYYYMMDD-n`).

**Verification:** `awos --help` shows all commands; cold start creates `.awos/` skeleton.

### Phase 1 — Terminal + memory + router (still shallow context)

- `awos run` wraps subprocess; optional JSON output schema.
- `awos memory` read/append soul with safe prompts.
- Model router: env-driven tiers (reuse patterns from `scripts/mvp_aider.py` / `CONVENTIONS.md`); no local Ollama until optional Phase 1b.

**Verification:** `awos run -- pytest -q` returns structured exit; router picks model id from tier flag.

### Phase 2 — Context Optimization Layer (Tree-Sitter)

- FileAgent: language plugins or single-language start (match Diramide stack); skeleton cache on disk under `.awos/cache/`.
- `awos map` regenerates skeleton index; `awos ask` uses skeletons by default.

**Verification:** map output size order-of-magnitude smaller than concatenated sources for sample module tree.

### Phase 3 — Autonomous debug loop

- `awos debug`: fixed max iterations; test command from config file `.awos/config.toml` or repo `pyproject`/`Makefile` discovery (smallest: user-configured command).
- Structured error → single patch proposal → rerun.

**Verification:** injected failing test is fixed or loop stops with capped report.

## Explicitly postponed (post-MVP charter)

- Multi-agent cognitive meshes, RL for routing/prompts, self-modifying tools.
- GNNs on dependency graphs, full symbolic theorem-style enforcement, Bayesian regression risk engines, MCTS planners.
- World models and self-evolution.

## Files affected (when implementation starts)

*To be filled in the first implementation PR; expect new top-level package `awos/`, `pyproject.toml` entry point, and `.awos/` gitignore rules.*

## Related

- [[awos_mvp]] — research
- [[AWOS]] — `AWOS.md` doctrine
- [[model_tiering]] — multi-provider cost routing
- `memories/repo/models_pricing_catalog.md` — canonical API pricing
- `scripts/mvp_aider.py` — interim tier launcher until `awos` subsumes routing
