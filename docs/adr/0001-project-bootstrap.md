---
title: "ADR 0001: Agentic OS Project Bootstrap"
tags:
  - doc/adr
  - phase/0
  - topic/bootstrap
---

# ADR 0001: Agentic OS Project Bootstrap

**Status:** accepted
**Date:** YYYY-MM-DD (fill in when you adopt this for your project)
**Deciders:** project team

---

## Context

This project uses the Agentic OS workflow system. This ADR records the decision to adopt it
and the customizations made for this specific project.

---

## Decision

Adopt the Agentic OS workflow system as the backbone for this project.

Customizations for this project:
- (Fill in any project-specific tag taxonomy extensions)
- (Fill in any project-specific layer names)
- (Fill in any modifications to the standard templates)

---

## Options Considered

### Option A: Agentic OS (chosen)
Full workflow system: research → spec → task triad, single-owner facts, session checkpoints, obsidian knowledge graph, automation scripts.

Pros:
- Prevents the three primary failure modes (memory loss, unplanned implementation, fact drift)
- Scales to multi-month, multi-session projects
- Machine-readable progress tracking

Cons:
- Initial setup overhead (~15 minutes)
- Requires discipline to maintain templates and checkpoints

### Option B: Ad hoc documentation
Write docs as needed, no enforced structure.

Pros: Zero setup

Cons: Does not survive context loss between sessions. Agent re-derives decisions already made.

### Option C: External project management tool (Jira, Linear, etc.)
Use a dedicated tool for task tracking.

Pros: Rich UI, integrations

Cons: Not co-located with codebase. Agent cannot read/write directly. Breaks single-owner rule.

---

## Rationale

Option A was chosen because this project will span multiple sessions and the cost of memory loss (re-deriving decisions, contradicting prior work, re-reading the codebase) is higher than the overhead of maintaining the Agentic OS artifacts.

---

## Consequences

### Positive
- Cold-start from any session in ~5 minutes
- All decisions are traceable and durable
- Agent and human share the same source of truth

### Negative
- Every non-trivial change requires a research doc and spec before code is touched
- Checkpoints must be written at every natural breakpoint

---

## Related

- [[project_structure]] — canonical project facts
- `AWOS.md` — full doctrine
