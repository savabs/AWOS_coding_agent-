---
title: "Research: <Feature Name>"
tags:
  - doc/research
  - phase/N
  - topic/<slug>
  - layer/<slug>
---

# Feature: <Feature Name>

> **Purpose:** Understand what exists, what's needed, and what risks exist — before writing any code.
> No implementation decisions go here. Only observations, analysis, and external references.

---

## Current Architecture

What exists in the codebase that is relevant to this feature?
- Which modules are involved?
- What patterns are already established that this should follow?
- What data flows through the relevant path?
- What interfaces does this feature need to integrate with?

```
# Relevant files:
# agent/module/relevant_file.py  — does X
# agent/module/another_file.py   — does Y
```

---

## Observations

What did you discover by reading the relevant code?
- What is working well that shouldn't be broken?
- What is missing that this feature provides?
- What would break if the current behavior changed?
- What surprising dependencies or coupling exists?

---

## Risks

What could go wrong?
- Edge cases that are not obvious
- Breaking changes to existing interfaces
- Performance concerns (memory, latency, throughput)
- Security concerns (injection, privilege escalation, data leakage)
- Race conditions or concurrency issues
- External API failures or rate limits
- Data quality issues (nulls, wrong types, stale data)

---

## Data Requirements

*(Fill in if this feature involves data ingestion, storage, or transformation)*

What data series or sources are needed?
- What is already available in the codebase?
- What is missing and must be fetched or created?
- What are the freshness requirements (TTL)?
- What are the volume expectations?
- What schema does the output need to conform to?

---

## Math / Algorithm Survey

*(Fill in if this feature involves scoring, estimation, inference, filtering, or optimization)*

What algorithms or mathematical methods apply?
- What libraries exist vs. what must be built from scratch?
- What are the key tradeoffs (exact vs approximate, batch vs online, parametric vs empirical)?
- What are the complexity and numerical stability concerns?
- What assumptions does the preferred method rely on?

---

## External Sources

*(Fill in after research — list all sources consulted)*

| Source | URL | Notes |
|---|---|---|
| Official docs | https://... | version X.Y |
| Reference paper | https://... | key result: ... |
| OSS repo | https://github.com/... | license: MIT / conceptual-only |

---

## Depth Roadmap

*(Fill in for data tools — per Signal Depth Doctrine)*

- **L1 (Aggregate):** What does a basic implementation produce? What does everyone see?
- **L2 (Entity-Level):** What entity-resolved data becomes possible? What per-entity time-series?
- **L3 (Cross-Domain):** What patterns only emerge by linking this source with another?

---

## Summary

1–3 sentences: what needs to be built, what risks to watch, what the key design decision is.

---

## Related

- [[<feature_name>_spec]] — specification for this feature
- [[<task_name>]] — task tracking implementation
- [[<related_topic>]] — related concept or prior work
