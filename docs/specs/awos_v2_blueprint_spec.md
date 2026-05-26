---
title: "AWOS v2 Blueprint — Implementation Specification"
tags:
  - doc/spec
  - phase/2-3
  - topic/awos-v2
  - status/active
date: 2026-05-15
---

> **Content:** [awos_v2_blueprint_spec.html](awos_v2_blueprint_spec.html) — open in browser.

## Summary

Atomic specification for AWOS v2 Blueprint implementation. Breaks the architecture into 23 testable steps across 3 sprints:

- **Sprint 1 (8 steps):** Layer 1 Universal Cartographer — Tree-Sitter indexing, STRUCT.xml, file watcher, `awos map`
- **Sprint 2 (8 steps):** Layer 2–3 Dispatcher + HITL — complexity scoring, model routing, pre-flight manifest, consent gate
- **Sprint 3 (7 steps):** Layer 4–5 Hydration + Soul — symbol hydration, prompt caching, SOUL.xml persistence, dreaming cycle

Each step is atomic: changes one subsystem, includes testable exit condition, no "and" in descriptions.

## Related
- [[awos_v2_blueprint]] — Research & validation
- [[awos_mvp_spec]] — Current MVP spec (Phases 0–3 baseline)
- [[AWOS]] — Full doctrine
