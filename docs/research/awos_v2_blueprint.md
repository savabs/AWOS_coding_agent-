---
title: "AWOS v2 Blueprint — Research & Validation"
tags:
  - doc/research
  - phase/2
  - topic/awos-v2
  - status/active
date: 2026-05-15
---

> **Content:** [awos_v2_blueprint.html](awos_v2_blueprint.html) — open in browser.

## Summary

Research document validating the AWOS v2 Blueprint against verified sources. Covers five layers:
1. **Layer 1:** Universal Cartographer (Tree-Sitter + STRUCT.xml)
2. **Layer 2:** Adaptive Dispatcher (model routing by complexity)
3. **Layer 3:** Transparency & HITL (pre-flight manifest, default-deny consent)
4. **Layer 4:** Hydration Protocol (prompt caching strategy)
5. **Layer 5:** Project Soul (SOUL.xml persistence)

All technology choices verified against official docs:
- Tree-Sitter: 30+ language support, incremental parsing ✓
- Prompt Caching: 90% cost reduction, 10% read rate ✓
- DeepSeek-Chat: $0.14/MTok, 70.2% polyglot ✓
- xxhash3: Production-standard for content hashing ✓

## Related
- [[model_tiering]] — Previous tiering work (foundation for Dispatcher)
- [[awos_mvp_spec]] — Current MVP spec (Phases 0–3)
- [[AWOS]] — Full doctrine
