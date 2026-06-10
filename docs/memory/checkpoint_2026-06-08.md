---
title: "Checkpoint: 2026-06-08 — Vision Finalized"
tags:
  - doc/checkpoint
  - phase/identity
---

# Session Checkpoint — 2026-06-08

> Immutable record. Identity changes go in VISION.md; corrections in the next checkpoint.

---

## Session Summary

Finalized AWOS product identity and strategy after strategic brainstorm. Rewrote canonical docs to align. Superseded outdated positioning that framed AWOS as a Cursor competitor or workflow template.

---

## Identity Finalized (canonical: VISION.md)

**AWOS is a learnable operating system for autonomous work** — a greedy meta-AI that optimizes:

```
Quality × Speed ÷ Cost
```

- **OS** — schedules agents, manages `.awos/` state, enforces budget
- **Compiler** — experience → prompts, tools, routing weights
- **Database** — habits, mistakes, patterns per deployment
- **Coding agent** — App #1, not the product
- **Business** — agent-making firm, ~1,000 niche users, API model (hide routing, keep margin)
- **Metric** — Project Efficiency Index (PEI), not tokens or seats

---

## Docs Updated

| File | Change |
|---|---|
| `VISION.md` | **NEW** — canonical identity document |
| `README.md` | Rewritten — points to VISION.md, kernel + app framing |
| `AWOS.md` | v2.0 — reframed as operational protocols, not identity |
| `memories/repo/project_structure.md` | Filled with actual identity, metrics, phase |
| `AGENT_INDEX.md` | Cold-start now reads VISION.md first |
| `awos.py` | Docstring updated |
| `.github/copilot-instructions.md` | Points to VISION.md |
| `docs/research/AWOS_VS_TOP_AGENTS_COMPARISON.md` | Superseded banner |
| `docs/memory/EFFECTIVENESS_ANALYSIS.md` | Reframed around PEI metric |
| `docs/AWOS_V2_COMPLETE.md` | Historical note added |
| `scaffold/agent/__init__.py` | Kernel + app docstring |
| `scaffold/agent/unified_agent.py` | Coding App entry point docstring |

---

## What Was Removed / Superseded

- README framing as "backbone for complex agent-driven projects" workflow template
- AWOS.md as sole identity document (now operational protocols only)
- Competitive positioning as "research-first coding agent vs Hermes/Cursor"
- Effectiveness analysis framed as "vs Copilot cost" without PEI north star
- "AI Coding Agent CLI" as primary product description

Historical task files, specs, and research docs retained for implementation reference. Not deleted.

---

## State at Session End

- **Active phase:** 4 — model ladder cleanup, PEI scorecard, API productization
- **Tests:** 776 passing, 1 flaky (PER sampler)
- **Next work:** PER fix, model ladder alignment, PEI scorecard implementation, FastAPI layer

---

## Related

- [[VISION]] — canonical identity
- [[memories/repo/project_structure]] — project facts
