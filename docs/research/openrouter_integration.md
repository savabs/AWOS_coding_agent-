---
title: "OpenRouter Integration Research"
tags:
  - doc/research
  - phase/4
  - topic/openrouter
  - topic/provider-routing
---

# OpenRouter Integration Research

> OpenRouter provides a unified API for 200+ LLMs through a single endpoint.

---

## 1. What OpenRouter Offers

| Feature | Detail |
|---------|--------|
| **Base URL** | `https://openrouter.ai/api/v1` |
| **API format** | OpenAI-compatible (`/v1/chat/completions`) |
| **Auth** | Bearer token via `OPENROUTER_API_KEY` env var |
| **SDK option 1** | `pip install openrouter` → `from openrouter import OpenRouter` (dedicated Python SDK) |
| **SDK option 2** | OpenAI SDK with custom `base_url` (no new dependency) |
| **Model IDs** | Format: `"provider/model-name"` e.g. `"openai/gpt-4o"`, `"anthropic/claude-sonnet-4"`, `"google/gemini-2.5-flash"` |
| **Extra headers** | `HTTP-Referer` and `X-OpenRouter-Title` (optional, for leaderboard rankings) |

**Recommended approach:** Use OpenAI SDK with custom base URL. No new dependency. Same `client.chat.completions.create()` pattern already used for DeepSeek and OpenAI.

---

## 2. Why Add OpenRouter

### 2.1 One key, 200+ models
Currently each LLM provider requires its own API key. OpenRouter unlocks the full model catalog with one env var.

### 2.2 Cost optimization
OpenRouter routes to the cheapest provider for a given model. Can be cheaper than direct API access for some models.

### 2.3 Fallback on rate limits
If a model hits rate limits, OpenRouter can auto-fallback to equivalent models.

### 2.4 Access to models we don't have keys for
Through OpenRouter we get: Llama-3, Qwen-3, Cohere, Mistral, GPT-4o, Gemini Pro 2.5, and more — all without individual API keys.

### 2.5 Router diversity
Adding OpenRouter as a provider tier (between L1 DeepSeek and L2 OpenAI) gives the EscalationEngine more routing options.

---

## 3. Integration Points

### 3.1 Core files (must change)

| File | What to change |
|------|---------------|
| `escalation_engine.py` | Add `EscalationLevel.OPENROUTER` to LADDER, update `allowed_ladder()`, `CHEAP_ONLY_MAX_LEVEL`, `_CHEAP_ROTATION` |
| `react_worker.py` | Add `openrouter_client`, `_openrouter()` model spec, `provider == "openrouter"` branch in `_call_model()` |
| `worker.py` | Add `openrouter_client`, fallback chain, dispatch in `_try_cheap_fallback()`, `_architect_step()` |
| `unified_agent.py` | Add `_call_openrouter()` method |
| `gui_chat.py` | Add OpenRouter streaming path |
| `orchestrator.py` | Add `openrouter_client` in `_cheap_call()` |
| `planner.py` | Optional: allow OpenRouter as alternative when ANTHROPIC_API_KEY is missing |

### 3.2 Routed plumbing (must stay in sync)

| File | What to change |
|------|---------------|
| `reward_store.py` | Add `"openrouter"` to `action_names` |
| `ml_router.py` | Bump `N_ACTIONS`, add `"openrouter"` to `ACTION_NAMES` |
| `learning_inspector.py` | Add `"openrouter"` to `ACTION_NAMES` |
| `core/queue_model.py` | Bump `_N_ACTIONS`, add to `ACTION_NAMES` dict |
| `task_learning.py` | Add `"openrouter"` mapping in `model_name_to_action_id()` |

### 3.3 Config (already done)

`.env.example` already has `OPENROUTER_API_KEY=`. No change needed.

---

## 4. Design Decision: New Dedicated Level vs Reuse OpenAI

**Decision: New dedicated level (`EscalationLevel.OPENROUTER`).**

Rationale:
- OpenRouter models have different pricing (often cheaper) than native OpenAI
- Model IDs use a different format (`"provider/model"` vs `"gpt-4o-mini"`)
- Makes routing, logging, and dead-provider tracking explicit
- Can position it in the ladder where it makes sense (e.g., between DeepSeek and OpenAI as a mid-tier option)

**Ladder positioning:**
```
L0  Gemini Flash            $0.001/req  — routing, simple Q&A
L1  DeepSeek V4 Flash       $0.001/req  — default worker  
L2  OpenRouter (new)        $0.002/req  — diverse model access
L3  GPT-4o-mini (OpenAI)    $0.003/req  — fallback
L4  Claude Haiku 4.5        $0.017/req  — premium (blocked cheap-only)
L5  Claude Sonnet 4.6       $0.050/req  — premium (blocked cheap-only)
```

---

## 5. Pricing

OpenRouter pricing varies by model. Representative costs per MTok (as of 2026-07):

| Model | Input $/MTok | Output $/MTok |
|-------|-------------|--------------|
| `openai/gpt-4o-mini` | $0.15 | $0.60 |
| `deepseek/deepseek-chat` | $0.14 | $0.28 |
| `meta-llama/llama-3.1-70b` | $0.59 | $0.79 |
| `mistral/mistral-large` | $2.00 | $6.00 |
| `google/gemini-2.5-flash` | $0.15 | $0.60 |
| `anthropic/claude-haiku` | $1.00 | $5.00 |

For AWOS routing purposes, we'll estimate **$0.002/req** as default (similar to DeepSeek for cheap models, higher for premium routes).

---

## 6. Risks

| Risk | Mitigation |
|------|-----------|
| OpenRouter adds latency vs direct API | Acceptable — routing diversity wins. Use direct APIs for speed-critical paths |
| OpenRouter rate limits on free models | Monitor and mark `dead_providers` like other providers |
| Model name collisions (`"openai/gpt-4o-mini"` contains `"openai"` in name) | In `task_learning.py`, check `"openrouter"` BEFORE `"openai"` in the matching chain |
| Extra dependency on `openrouter` PyPI package | **Don't use it.** Use OpenAI SDK with custom `base_url` — no new dependency |

---

## Related
- `docs/specs/openrouter_integration_spec.md` — implementation spec
- [[escalation_engine]] — model ladder
- [[react_worker]] — ReAct worker provider chain
- [[worker]] — Worker provider chain
