---
title: "Sprint 2 — Layer 2–3 Dispatcher (HITL + Routing)"
tags:
  - doc/task
  - phase/2-3
  - topic/awos-v2
  - status/active
date: 2026-05-15
---

> **Content:** Focus on workable product, skip tests.
> Build complexity scoring → routing → HITL consent → pre-flight manifest.

## Sprint 2 Steps (Atomic)

### Step 2.1: ComplexityScorer (analyze code complexity)
- [ ] Create `scaffold/agent/complexity_scorer.py`
- [ ] Implement heuristics: lines of code, cyclomatic complexity, import depth
- [ ] Input: STRUCT.xml or file path
- [ ] Output: score 1–10 (1=trivial, 10=expert-required)
- [ ] Test on one real codebase file

### Step 2.2: ModelRouter (map complexity → model tier)
- [ ] Create `scaffold/agent/model_router.py`
- [ ] Define tier mapping: 1–3=DeepSeek, 4–7=Claude-Haiku, 8–10=Claude-Opus
- [ ] Input: complexity score
- [ ] Output: {provider, model, max_tokens, temperature}
- [ ] Validate routing logic with 3 example scores

### Step 2.3: PreflightManifest (document what agent will do)
- [ ] Create `scaffold/agent/preflight_manifest.py`
- [ ] Generate XML: task description, estimated cost, required files, proposed model tier
- [ ] Input: task description, STRUCT.xml context
- [ ] Output: `.awos/preflight_<timestamp>.xml`
- [ ] Validate XML is well-formed

### Step 2.4: HITLConsent (ask user permission)
- [ ] Create `scaffold/agent/hitl_consent.py`
- [ ] Load manifest, display to user, capture y/n input
- [ ] Default: DENY (no auto-approval)
- [ ] Record decision in COST_LOG.xml
- [ ] Test: approve/deny both paths work

### Step 2.5: Dispatcher (orchestrate all 4)
- [ ] Create `scaffold/agent/dispatcher.py`
- [ ] Pipeline: STRUCT.xml → complexity → routing → manifest → consent → route to handler
- [ ] Input: task, codebase path
- [ ] Output: {approved, model_config, manifest_path, cost_estimate}
- [ ] Test end-to-end on a mock task

### Step 2.6: CLI integration (awos dispatch)
- [ ] Add `scaffold/agent/cli.py` command: `awos dispatch <task_desc> <repo_path>`
- [ ] Wire Dispatcher into CLI
- [ ] Output: manifest display, consent prompt, approval/denial message

### Step 2.7: Cost tracking (append to COST_LOG.xml)
- [ ] Implement `scaffold/agent/cost_logger.py`
- [ ] Log every decision: task, model, complexity, cost, user decision
- [ ] Output: append-only COST_LOG.xml in `.awos/`

### Step 2.8: Integration test (end-to-end)
- [ ] Run full pipeline: `awos dispatch "refactor function X" /tmp/test_repo`
- [ ] Verify: manifest displays, consent prompt works, decision logged
- [ ] Done when user can interact with the full flow

---

## Progress Tracker

| Step | Status | Notes |
|---|---|---|
| 2.1 | ✅ | Complexity scoring — heuristics-based (LOC, cyclomatic, imports, nesting) |
| 2.2 | ✅ | Model routing — 1–3→DeepSeek, 4–7→Haiku, 8–10→Opus |
| 2.3 | ✅ | Preflight manifest — XML generation with cost estimates |
| 2.4 | ✅ | HITL consent — Default deny, shows manifest, records decision |
| 2.5 | ✅ | Dispatcher orchestrator — Full pipeline working end-to-end |
| 2.6 | ✅ | CLI integration — `awos dispatch <task> <repo> [--files ...] [--no-confirm]` |
| 2.7 | ✅ | Cost tracking — COST_LOG.xml appends all decisions (approvals + denials) |
| 2.8 | ✅ | E2E test — Verified: manifest display, approval/denial, logging |

## Definition of Done
- All 8 steps complete
- User can run `awos dispatch <task> <repo>` and see:
  1. Complexity score calculated
  2. Model tier selected
  3. Manifest generated and displayed
  4. Consent prompt (default deny)
  5. Decision logged to COST_LOG.xml
- No crashes on valid input

---

## Related
- Spec: [[awos_v2_blueprint_spec]]
- Research: [[awos_v2_blueprint]]
- Previous: [[sprint1_layer1_task]]
