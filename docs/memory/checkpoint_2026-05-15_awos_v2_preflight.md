---
title: "Checkpoint 2026-05-15 — AWOS v2 Blueprint Preflight Complete"
tags:
  - doc/checkpoint
  - phase/2-3
  - topic/awos-v2
  - status/done
date: 2026-05-15
---

## Session Summary

Completed mandatory preflight for AWOS v2 Blueprint v2.1 — the Master Engineering Blueprint for Agent-Assisted Code Development. User submitted comprehensive 5-layer architecture specification; agent validated against sources, then generated full Research → Spec → Task triad per AWOS protocol.

## What Was Done

### Preflight Artifacts Created
- **`docs/research/awos_v2_blueprint.html`** (+ .md stub) — 6-tab research document validating all technology choices:
  - Layer 1 (Tree-Sitter): 30+ language support, incremental parsing ✓
  - Layer 4 (Prompt Caching): 90% cost reduction on reads ✓
  - DeepSeek-Chat: $0.14/MTok, 70.2% polyglot ✓
  - xxhash3: Production-standard hashing ✓

- **`docs/specs/awos_v2_blueprint_spec.html`** (+ .md stub) — Atomic specification with 23 steps across 3 sprints:
  - Sprint 1 (8 steps): Layer 1 Cartographer — Tree-Sitter, STRUCT.xml, file watcher, `awos map`
  - Sprint 2 (8 steps): Layer 2–3 Dispatcher — complexity scoring, routing, HITL, pre-flight manifest
  - Sprint 3 (7 steps): Layer 4–5 Hydration + Soul — symbol hydration, caching, SOUL.xml, dreaming

- **`tasks/active/awos_v2_blueprint_task.html`** (+ .md stub) — Interactive checklist with localStorage persistence

### User Decisions Recorded
1. **Integration:** Extend MVP (Phases 0–3), not replace
2. **Languages:** All major (Python, C/C++, Rust, Go, JavaScript, Java)
3. **HITL Consent:** Deny by default (user must approve each task)
4. **Provider:** DeepSeek-Chat for Tier 3/4, Claude Opus for Tier 1
5. **SOUL Scope:** Rules + decisions + learned patterns + cost history

### Key Architecture Points (Verified from Official Docs)

| Decision | Validation Source | Status |
|---|---|---|
| Tree-Sitter incremental parsing | tree-sitter.github.io | ✓ Confirmed |
| Prompt caching 90% reduction | platform.claude.com/docs (Anthropic) | ✓ Confirmed: 10% read rate |
| DeepSeek-Chat polyglot 70.2% | Aider polyglot YAML (2025-10-03) | ✓ Verified vs Sonnet 56.4% |
| xxhash3 available | PyPI package tested | ✓ Working: xxhash==0.x |
| Default-deny HITL | User requirement | ✓ Approved |

## Blueprint Overview

**Five-Layer Architecture:**
1. **Layer 1: Universal Cartographer** — Parse code → STRUCT.xml (tree-sitter) + incremental sync (xxh3)
2. **Layer 2: Adaptive Dispatcher** — Complexity score → model tier → multi-provider routing (LiteLLM)
3. **Layer 3: Transparency & HITL** — Pre-flight manifest → consent gate (deny by default) → COST_LOG.xml
4. **Layer 4: Hydration Protocol** — Observe STRUCT.xml → Select symbols → Hydrate code → Prompt cache (90% reduction)
5. **Layer 5: Project Soul** — SOUL.xml persistence: rules, patterns, decisions, cost history. Post-session "dreaming" learns from each session.

**Key Metrics (Promised by v2.1):**
- Token efficiency: 89% reduction on repeated contexts (caching + hydration)
- Cost efficiency: 21× cheaper on commodity tasks (DeepSeek vs Sonnet)
- First-pass success: Complexity scoring prevents wrong model tier selection
- Continuity: SOUL.xml eliminates repeated mistakes; agent "wakes up" with full context

## Current State

| Artifact | Location | Status |
|---|---|---|
| Research | `docs/research/awos_v2_blueprint.html` | ✅ Complete, 6 tabs |
| Specification | `docs/specs/awos_v2_blueprint_spec.html` | ✅ Complete, 23 atomic steps |
| Task Checklist | `tasks/active/awos_v2_blueprint_task.html` | ✅ Complete, interactive |
| Decision Record | This checkpoint | ✅ Written |

**Project Structure Updated:**
- Phase: 2 (Model Tiering) → **Preflight for v2 Blueprint**
- Active tasks: 3 (model_tiering + phase1_aider + **awos_v2_blueprint**)
- AWOS Version: MVP (0–3) → **v2.1 Blueprint (5-layer, 23-step)**

## Next Steps

1. **Immediate (for next session):**
   - Cold-start by reading this checkpoint + `tasks/active/awos_v2_blueprint_task.html`
   - Begin Sprint 1, Step 1.1: Create `.awos/` project memory directory

2. **Sprint 1 Focus (~4 weeks):**
   - Establish Tree-Sitter cartographer
   - Generate STRUCT.xml for any language
   - Implement `awos map` CLI command

3. **Decision Points:**
   - After Sprint 1: Validate STRUCT.xml quality before proceeding to Sprint 2
   - After Sprint 2: Validate dispatcher + consent gate before building expensive prompt caching (Sprint 3)

## Related Artifacts

- **Research:** [[awos_v2_blueprint]]
- **Spec:** [[awos_v2_blueprint_spec]]
- **Task:** [[awos_v2_blueprint_task]]
- **Previous checkpoint:** [[checkpoint_2026-05-13b]] (Model tiering complete)
- **Foundation:** [[awos_mvp_spec]] (MVP Phases 0–3 baseline)

---

**Checkpoint signed off:** 2026-05-15  
**Prepared by:** GitHub Copilot (agent mode)  
**Authority:** User-approved Master Blueprint v2.1  
**Status:** Preflight complete; ready for Sprint 1 implementation
