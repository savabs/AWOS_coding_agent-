# <Project Name> — Project Structure

> **Canonical owner for all project metrics.**
> All other files must [[link]] here; they must not copy these values.
> Update this file whenever a metric changes.

---

## Identity

**<Project Name>** is a (fill in: one-sentence description of what it does and what the edge is).

**Mission:** (fill in)

**Strategic edge:** (fill in: what makes this defensible)

---

## Build Sequence

- Phase 0: Bootstrap — DONE (YYYY-MM-DD)
- Phase 1: (fill in) — (status)
- Phase 2: (fill in) — (status)
- Phase 3: (fill in) — (status)

**Current phase:** 0 (bootstrap)

---

## Workflow

- Phased pipeline: Research → Spec → Implement — per [[AWOS]]
- Instructions: `.github/copilot-instructions.md`
- Research: `docs/research/`
- Specs: `docs/specs/`
- Task tracking: `tasks/active/`
- Memory: `docs/memory/`, `memories/repo/`

---

## Key Modules

| Module | Path | Purpose |
|---|---|---|
| Entry point | (fill in) | (fill in) |
| Config | (fill in) | (fill in) |
| Core | (fill in) | (fill in) |
| Memory | (fill in) | (fill in) |
| Tools | (fill in) | (fill in) |

---

## Execution Flow

```
(fill in the main execution pipeline, e.g.:)
CLI → Orchestrator → Research → Plan → Execute(tools) → Synthesize
```

---

## Current Metrics

> Update these every time they change. Do not copy them elsewhere.

| Metric | Value | Last updated |
|---|---|---|
| Test count | 0 | YYYY-MM-DD |
| Tools registered | 0 | YYYY-MM-DD |
| Active tasks | 0 | YYYY-MM-DD |
| Current phase | 0 | YYYY-MM-DD |

---

## Active Tasks

| Task | File | Status |
|---|---|---|
| (none yet) | — | — |

---

## Key Decisions

| Decision | Rationale | ADR |
|---|---|---|
| Adopted Agentic OS workflow | Structured memory + phased pipeline prevents context drift | [[0001-project-bootstrap]] |

---

## Cold-Start Protocol

To resume work after a break (< 5 min):

```bash
# 1. Read this file
cat memories/repo/project_structure.md

# 2. Read the latest checkpoint
ls -t docs/memory/checkpoint_*.md | head -1 | xargs cat

# 3. Read the active task(s)
cat tasks/active/*.md

# 4. Follow wiki links to the relevant research/spec if needed
```

Do not re-read the entire codebase. Follow the links.
