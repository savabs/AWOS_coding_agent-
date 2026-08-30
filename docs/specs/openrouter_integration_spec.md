---
title: "OpenRouter Integration Spec"
tags:
  - doc/spec
  - phase/4
  - topic/openrouter
  - topic/provider-routing
---

# OpenRouter Integration Spec

**Goal:** Add OpenRouter as a first-class LLM provider in the AWOS model routing ladder, using the OpenAI SDK with custom `base_url`.

---

## Files Affected

| # | File | Change |
|---|------|--------|
| 1 | `scaffold/agent/escalation_engine.py` | Add `OPENROUTER` to `EscalationLevel` enum + LADDER entry |
| 2 | `scaffold/agent/react_worker.py` | Add `openrouter_client`, model spec, call branch |
| 3 | `scaffold/agent/worker.py` | Add `openrouter_client`, fallback, dispatch |
| 4 | `scaffold/agent/unified_agent.py` | Add `_call_openrouter()` method |
| 5 | `scaffold/agent/gui_chat.py` | Add OpenRouter streaming path |
| 6 | `scaffold/agent/orchestrator.py` | Add `openrouter_client` in `_cheap_call()` |
| 7 | `scaffold/agent/reward_store.py` | Add `"openrouter"` to `action_names` |
| 8 | `scaffold/agent/ml_router.py` | Bump `N_ACTIONS`, add to `ACTION_NAMES` |
| 9 | `scaffold/agent/learning_inspector.py` | Add to `ACTION_NAMES` |
| 10 | `scaffold/agent/core/queue_model.py` | Bump `_N_ACTIONS`, add to `ACTION_NAMES` |
| 11 | `scaffold/agent/task_learning.py` | Add OpenRouter model name mapping |
| 12 | `.env.example` | Already has `OPENROUTER_API_KEY=` — verify completeness |

---

## Implementation Steps (ordered, atomic)

### Step 1: escalation_engine.py — Add OpenRouter level

Add `EscalationLevel.OPENROUTER = 3` (insert between DEEPSEEK=1 and OPENAI=2, shifting OpenAI to 3, Haiku to 4, Sonnet to 5).

Well, actually that would mess up the numbering for existing trained LinUCB weights. Let me think about this more carefully.

**Decision: Append at end with value 5 (keep existing numbers).**

```python
class EscalationLevel(Enum):
    GEMINI_FLASH  = 0
    DEEPSEEK      = 1
    OPENAI        = 2
    HAIKU         = 3
    SONNET        = 4
    OPENROUTER    = 5   # NEW
```

This preserves existing action IDs (0-4). OpenRouter becomes a special tier that can be used between DEEPSEEK and OPENAI via routing, but doesn't break existing LinUCB weights.

Then:
- Add `ModelSpec` entry for OPENROUTER in LADDER (insert at position between DEEPSEEK and OPENAI for logical ordering, but with level value 5)
- Add `"openrouter"` to allowed provider strings
- Update defaults/rotation if needed

### Step 2: react_worker.py — Add OpenRouter client

In `__init__`:
- Read `OPENROUTER_API_KEY`
- Create `self.openrouter_client = OpenAI(api_key=..., base_url="https://openrouter.ai/api/v1")`
- Add to `dead_providers` set check

In `_default_model_spec()`:
- Add `_openrouter()` factory function
- Add `"openrouter"` to `order` dict

In `_call_model()`:
- Add `provider == "openrouter"` branch (identical to `provider == "openai"` pattern)

### Step 3: worker.py — Add OpenRouter client

Same pattern as react_worker.py:
- Client init in `__init__`
- Fallback in `_try_cheap_fallback()`
- Provider dispatch chain
- `_architect_step()` OpenRouter branch

### Step 4: unified_agent.py — Add OpenRouter handler

Add `_call_openrouter()` method following `_call_deepseek()` pattern using:
```python
client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
client.chat.completions.create(...)
```

### Step 5: gui_chat.py — Add streaming

Add OpenRouter path in provider selection:
```python
elif os.getenv("OPENROUTER_API_KEY"):
```
Add `_stream_openrouter()` method.

### Step 6: orchestrator.py — Add cheap_call fallback

Add `self.worker.openrouter_client` check in `_cheap_call()`.

### Step 7-10: Plumbing sync

Update `reward_store.py`, `ml_router.py`, `learning_inspector.py`, `core/queue_model.py`, `task_learning.py` to include the new OpenRouter action.

### Step 11: Verify .env.example

Already has `OPENROUTER_API_KEY=`. No change needed.

---

## Edge Cases

| Edge case | Handling |
|-----------|----------|
| OPENROUTER_API_KEY not set | Client is None; provider skipped by `_dead_providers` tracking |
| OpenRouter rate limit | Mark `"openrouter"` in `_dead_providers`, fall back to next provider |
| OpenRouter returns error | Same as any provider error — circuit breaker marks provider dead |
| Model name contains "openai" (e.g. "openai/gpt-4o") | In `task_learning.py`, check `"openrouter"` BEFORE `"openai"` in matching chain |
| OpenRouter model priced differently | Use generic `cost_per_req=0.002` in LADDER; actual cost tracked via usage |

---

## Testing Plan

| Test | Type | What it verifies |
|------|------|-----------------|
| OpenRouter client init | Unit | Client created when key set, None when not |
| EscalationEngine routes to OpenRouter | Unit | Correct level selected for appropriate tasks |
| ReActWorker calls OpenRouter | Unit | `_call_model()` branch works |
| Worker uses OpenRouter fallback | Unit | `_try_cheap_fallback()` includes OpenRouter |
| OpenRouter circuit breaker | Unit | Dead provider tracked correctly |
| Model name mapping | Unit | `model_name_to_action_id()` returns correct ID for OpenRouter models |

---

## Live Proof

**Scenario:** Run `awos worker start "add a docstring to a function"` with `OPENROUTER_API_KEY` set and `AWOS_CHEAP_ONLY=false` (or adjust cheap-only to include OpenRouter).

**Command:**
```bash
OPENROUTER_API_KEY=sk-or-v1-... AWOS_CHEAP_ONLY=false python3 awos.py worker start "add a docstring to add function in test.py"
```

Or more simply:
```bash
python3 -c "
import os, sys
sys.path.insert(0, 'scaffold/agent')
from dotenv import load_dotenv
load_dotenv()
from react_worker import ReActWorker
w = ReActWorker()
spec = w._default_model_spec()
print(f'Default provider: {spec.provider}')
print(f'Model: {spec.name}')
"
```

**Success markers:**
- `Default provider: openrouter`
- `Model: OpenRouter router` (or similar)
- No import errors

---

## Related
- `docs/research/openrouter_integration.md` — research doc
- `tasks/active/openrouter_integration.md` — task tracker
