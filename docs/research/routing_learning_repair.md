---
title: "Research: Routing Learning System Repair"
tags:
  - doc/research
  - phase/4
  - topic/routing
  - topic/linucb
  - status/active
---

> **Content:** [routing_learning_repair.html](routing_learning_repair.html) — open in browser.

## Summary

Three bugs prevent AWOS's LinUCB router from learning:
1. **N_ACTIONS mismatch** (4→6) silently discards all weights on every startup
2. **ReplayGate rejects 79.5%** of episodes due to overly strict novelty scoring
3. **No warm-start** — LinUCB starts at 0 every session, never activates

Fixes: weight versioning with padding, adaptive gate threshold, batch replay from RewardStore.

## Related
- [[ml_router]] — LinUCB implementation
- [[escalation_engine]] — EscalationEngine + routing
- [[reward_store]] — RewardStore + ReplayGate
- [[opencode_go_tiering]] — deferred until routing works
