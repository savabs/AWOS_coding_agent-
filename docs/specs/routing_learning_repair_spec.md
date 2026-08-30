---
title: "Spec: Routing Learning System Repair"
tags:
  - doc/spec
  - phase/4
  - topic/routing
  - status/active
---

> **Content:** [routing_learning_repair_spec.html](routing_learning_repair_spec.html) — open in browser.

## Goal
Fix three bugs preventing LinUCB from learning: weight persistence (N_ACTIONS mismatch), ReplayGate over-filtering (79.5% rejected), missing warm-start. 5 atomic steps.

## Related
- [[routing_learning_repair]] — research
- [[ml_router]] — LinUCB implementation
- [[reward_store]] — ReplayGate
- [[escalation_engine]] — routing status
