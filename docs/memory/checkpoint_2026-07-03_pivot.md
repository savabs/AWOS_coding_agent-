---
title: "Checkpoint: Strategic Pivot — AWOS v2.0"
tags:
  - doc/checkpoint
  - phase/4
  - topic/pivot
  - topic/ci-rescue
  - topic/transparent-routing
  - status/active
---

# Session Checkpoint — 2026-07-03 (Pivot to v2.0)

> **Immutable historical record.** Do not edit after session ends.

---

## Session Summary

Full strategic pivot from "learnable OS for autonomous agents" to "Adaptive Engineering Runtime — CI Rescue for engineering agencies." We restructured the product positioning, business model, and go-to-market plan based on a comprehensive evaluation from investor/engineer/customer perspectives.

**Core decisions made:**
1. Customer-facing identity: "Adaptive Engineering Runtime" (not "learnable OS")
2. Glass-box routing: show model + reasoning + past success + let user override
3. Customer pays subscription (₹2,000/mo), AWOS manages ALL API keys internally
4. CI Rescue is the wedge product (proven: 28 reds → green, $0.0056, 49s)
5. Engineering agencies are the first ICP (5 pilot → scale)
6. Web app (`awos.io`) is the primary product surface
7. ScaffoldEvolver OFF by default (self-modifying code scares customers)
8. "90% cost reduction" claim replaced with PEI trend ("Your AI improved X% this month")
9. Phase 4 redefined: Product-Market Fit (was: model ladder optimization)

---

## Files Changed

### Written (new content):
| File | What |
|---|---|
| `VISION.md` v2.0 | Transparency philosophy, glass-box routing, revised business model, pricing revision, new competitive positioning |
| `memories/repo/project_structure.md` | New phase (4), identity rewrite, revised key decisions, obsolete ideas marked, new cold-start |
| `docs/product/PILOT_PLAN.md` | Narrowed to 5 agencies, 30-day sprint, revised pitch + DM templates |
| `docs/product/CI_RESCUE_PRODUCT.md` | NEW — CI Rescue wedge product spec (one-command, transparent routing, free/paid tiers) |
| `docs/product/TRANSPARENT_ROUTING.md` | NEW — Routing transparency UX spec (per-task model display, overrides, budget controls) |
| `tasks/active/pivot_web_app_product.md` | NEW — Active task file (25 implementation steps across Phase 4a-4d) |

### Still need minor updates:
- `docs/product/wedge_v1_coffee_money.md` — mostly aligned already
- `docs/product/AWOS_POSITIONING_COMPOUNDING.md` — add transparency angle

---

## Key Decisions

| Decision | Rationale |
|---|---|
| "Adaptive Engineering Runtime" not "Learnable OS" | Customers buy outcomes, not architecture. OS creates wrong mental model. |
| Glass-box routing (show models) | Moat = learning loop, not secrecy. Overrides train the router. |
| Customer pays subscription, we own API keys | Margin = (₹2,000) − (LLM cost). Routing gets smarter → margin grows. |
| CI Rescue is the wedge | Universal pain. Measurable. Proven. Unattended. One command. |
| Agencies as first ICP | Multiple client repos, margin-sensitive, fast purchasing decisions. |
| Web app primary surface | Zero-install. CLI is power-user tool. |
| ScaffoldEvolver OFF by default | "Software that rewrites itself" scares customers. Research feature only. |
| No "90% savings" claims | Replaced with PEI trend. Honest, defensible, provable. |
| User is architect/director | AWOS is senior engineer. User controls what matters, not model choices. |
| Phase 4 = P-M fit | Phase 5 = Compounding moat. Phase 6 = Platform. All deferred until CI Rescue proves value. |

---

## Active Task

**[[pivot_web_app_product]]** — 0/25 steps

Next atomic step: **1. Build `awos fix-ci` CLI command** (wraps orchestrator, discovers CI failures, fixes + verifies)

Full sequence:
- Phase 4a (Week 1-2): `awos fix-ci` + web dashboard + transparent routing
- Phase 4b (Week 3-4): `awos.io` hosted + GitHub OAuth + billing
- Phase 4c (Week 3-6): 5 agency pilots → paid
- Phase 4d (Week 5-8): Polish + iterate + grow

---

## What's Blocked

Nothing. Pivot is purely doc-level. Implementation begins next session.

---

## Next Session Starting Point

```
1. Read VISION.md v2.0 (identity)
2. Read memories/repo/project_structure.md (updated facts)
3. Read this checkpoint (session handoff)
4. Read tasks/active/pivot_web_app_product.md (active task)
5. Start step 1: Build `awos fix-ci` CLI command
```

---

## Related

- [[VISION]] v2.0 — product identity
- [[CI_RESCUE_PRODUCT]] — wedge product spec
- [[TRANSPARENT_ROUTING]] — routing UX spec
- [[PILOT_PLAN]] — agency pilot
- [[AWOS_POSITIONING_COMPOUNDING]] — compounding advantage
- [[memories/repo/project_structure]] — canonical facts
- [[pivot_web_app_product]] — active task
