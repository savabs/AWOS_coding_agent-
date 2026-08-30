---
title: "OpenRouter Integration Task"
tags:
  - doc/task
  - phase/4
  - topic/openrouter
  - status/active
---

# OpenRouter Integration

> **Source of truth for progress.** Spec: `docs/specs/openrouter_integration_spec.md`  
> **Research:** `docs/research/openrouter_integration.md`

---

## Execution Checklist

- [x] **Step 1:** escalation_engine.py — Add EscalationLevel.OPENROUTER + LADDER entry
- [x] **Step 2:** react_worker.py — Add OpenRouter client + call branch
- [x] **Step 3:** worker.py — Add OpenRouter client + fallback + dispatch
- [x] **Step 4:** unified_agent.py — Add _call_openrouter() method
- [ ] **Step 5:** gui_chat.py — Add OpenRouter streaming path (lower priority — not blocking live proof)
- [x] **Step 6:** orchestrator.py — Add openrouter_client in _cheap_call()
- [x] **Step 7:** reward_store.py — Add "openrouter" to action_names
- [x] **Step 8:** ml_router.py — Bump N_ACTIONS, add to ACTION_NAMES
- [x] **Step 9:** learning_inspector.py — Add to ACTION_NAMES
- [x] **Step 10:** core/queue_model.py — Bump _N_ACTIONS, add to ACTION_NAMES
- [x] **Step 11:** task_learning.py — Add OpenRouter model name mapping
- [x] **Step 12:** Live proof — all 6 checks passed
- [ ] **Step 13:** Checkpoint + update project_structure.md

---

## Blockers

None currently.

## Notes

- Model IDs like `"openai/gpt-4o-mini"` contain `"openai"` as a substring → must check `"openrouter"` before `"openai"` in `task_learning.py`
- OpenRouter uses OpenAI-compatible API — reuse `from openai import OpenAI` with custom `base_url`
- Pricing varies by model; default `cost_per_req=0.002` in LADDER
