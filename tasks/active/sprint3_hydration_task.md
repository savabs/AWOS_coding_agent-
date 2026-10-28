---
title: "Sprint 3 — Layer 4–5 Hydration + Soul"
tags:
  - doc/task
  - phase/2-3
  - topic/awos-v2
  - status/active
date: 2026-05-15
---

> **Content:** Final layer for smart, efficient agent.
> Symbol hydration + prompt caching + persistent project soul.

## Sprint 3 Steps (Atomic)

### Step 3.1: SymbolExtractor (pull code from STRUCT.xml)
- [ ] Create `scaffold/agent/symbol_extractor.py`
- [ ] Parse STRUCT.xml → identify classes, functions, modules
- [ ] Input: complexity score + task description
- [ ] Output: list of relevant {file, symbol, code_snippet, size}
- [ ] Use simple heuristic: match task keywords to symbol names
- [ ] Test: extract 3–5 symbols from real codebase

### Step 3.2: PromptHydrator (build context-aware prompts)
- [ ] Create `scaffold/agent/prompt_hydrator.py`
- [ ] Take task + extracted symbols
- [ ] Build prompt: preamble + task + code context + instructions
- [ ] Keep under model max_tokens
- [ ] Input: task, symbols, model config
- [ ] Output: hydrated prompt string + token estimate
- [ ] Test: generate 1 prompt, count tokens

### Step 3.3: PromptCache (reuse context across calls)
- [ ] Create `scaffold/agent/prompt_cache.py`
- [ ] Hash prompt context (non-task-specific parts)
- [ ] Store in `.awos/cache/<hash>.pkl` (pickled tokens)
- [ ] On repeat call: reuse cached tokens (90% cost reduction)
- [ ] Input: hydrated prompt, model
- [ ] Output: {cached: bool, new_tokens: N, cost_savings: $}
- [ ] Test: call twice with same context, verify savings

### Step 3.4: SoulXML (persistent project memory)
- [ ] Create `scaffold/agent/soul_xml.py`
- [ ] Template: `.awos/SOUL.xml` (rules, patterns, decisions, cost history)
- [ ] Track: what worked, what failed, learned patterns
- [ ] On session end: append new learnings
- [ ] Input: dispatcher decisions, task results, patterns
- [ ] Output: SOUL.xml (append-only)
- [ ] Test: create SOUL.xml, append 2 entries, validate

### Step 3.5: SoulManager (load + update soul at session start)
- [ ] Create `scaffold/agent/soul_manager.py`
- [ ] At startup: load SOUL.xml → extract rules + patterns
- [ ] Inject into system prompt (e.g., "Avoid these patterns", "These approaches work")
- [ ] At shutdown: analyze session → append learnings to SOUL.xml
- [ ] Input: repo path, dispatcher decisions
- [ ] Output: {learned_patterns, cost_history, recommendations}
- [ ] Test: load SOUL, append learning, verify persistence

### Step 3.6: Full Hydration Pipeline (E2E)
- [ ] Combine Steps 3.1–3.5 into `scaffold/agent/hydration_engine.py`
- [ ] Pipeline: task → dispatcher (Sprint 2) → hydrator → caching → soul update
- [ ] Input: task, repo, dispatcher decision
- [ ] Output: {hydrated_prompt, cost_estimate_with_cache, soul_update}
- [ ] Test: run full pipeline on 1 real task

### Step 3.7: CLI Integration (awos think)
- [ ] Add new CLI command: `awos think <task> <repo>`
- [ ] Wire dispatcher → hydrator → cache → output prompt
- [ ] Display: hydrated prompt, token estimate, cache hit stats
- [ ] Test: `awos think "refactor" . --show-prompt`

### Step 3.8: E2E Integration Test
- [ ] Run full stack: dispatch → hydrate → cache → soul
- [ ] 2 tasks: first call (no cache), second call (with cache)
- [ ] Verify: second call costs 90% less than first
- [ ] Validate: SOUL.xml updated with patterns
- [ ] Done when pipeline outputs full hydrated prompt with cost savings

---

## Progress Tracker

| Step | Status | Notes |
|---|---|---|
| 3.1 | ✅ | Symbol extraction from STRUCT.xml — working |
| 3.2 | ✅ | Prompt hydration with code context — working |
| 3.3 | ✅ | Prompt caching with 90% savings — working |
| 3.4 | ✅ | SOUL.xml persistence — working |
| 3.5 | ✅ | Soul manager (load + update) — integrated |
| 3.6 | ✅ | Full hydration engine — orchestration working |
| 3.7 | ✅ | CLI `awos think` integration — working |
| 3.8 | ✅ | E2E integration test — 5/5 tests passing |

## Definition of Done
- All 8 steps complete
- User can run `awos think <task> <repo>` and see:
  1. Complexity score (from Sprint 2)
  2. Selected symbols (from STRUCT.xml)
  3. Hydrated prompt (with code context)
  4. Token estimate (before + after cache)
  5. Cost savings (if cache hit)
  6. SOUL.xml updated with new patterns
- Second call on same repo costs ~90% less
- No crashes on valid input

## Budget Constraint
- Remaining: 49.8% API budget
- Sprint 3 goal: Run full E2E test with 2–3 API calls to validate caching
- Estimate: ~$0.02–0.05 (small hydrated prompts)

---

## Related
- Spec: [[awos_v2_blueprint_spec]]
- Previous: [[sprint2_dispatcher_task]]
- Checkpoint: [[checkpoint_2026-05-15_sprint2_tests_complete]]
