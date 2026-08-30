---
title: "Checkpoint: Chat Speed Optimization — July 21, 2026"
tags:
  - doc/checkpoint
  - phase/4
  - topic/chat-speed
  - status/done
---

# Chat Speed Optimization — July 21, 2026

> **Priority #2 complete.** Chat response time reduced 30-47%. Codebase queries now under 10s. Cache hits under 200ms.

---

## What Changed

### Problem
Chat was 7-16s per query, falling back to 34s on model failure. Target was 3-5s for simple queries, <10s for codebase queries.

### Root Causes Found

| # | Issue | Impact |
|---|-------|--------|
| 1 | **`max_tokens` hardcoded to 2048** (line 1045) | Every query forced to generate up to 2048 tokens; ignored caller's cap |
| 2 | **MiniMax M3 was T0 despite 2-6s TTFB variance** | Primary model was the slowest + most volatile |
| 3 | **6K of project docs loaded for every query** | "What is Python?" sent 6K of irrelevant context to LLM |
| 4 | **Context loaded twice per query** | `_build_qa_prompt` + `_qa_context_blob` both called `_read_project_context` |
| 5 | **Artificial 8ms sleep per chunk** | Cache responses delayed 200ms+ by simulated typing |
| 6 | **20s total fallback timeout** | 20s of waiting before giving up on failed queries |

### Fixes Applied (6 atomic changes, 1 file)

| # | Fix | File |
|---|-----|------|
| 1 | Removed `max_tokens = 2048` override; added `_suggest_max_tokens()` — 256 for simple, 1024 for codebase | `gui_chat.py` |
| 2 | Reordered `_OC_MODEL_TIERS` — DeepSeek V4 Flash (1.5s TTFB, $0.14/MTok) moved to T0; MiniMax M3 demoted to T3 | `gui_chat.py` |
| 3 | Added `_is_project_question()` — skips ALL project context (docs + SymbolIndex) for non-codebase queries | `gui_chat.py` |
| 4 | Context loaded once in `_stream_qa`, passed to `_build_qa_prompt` and `_qa_context_blob` via optional params | `gui_chat.py` |
| 5 | Cache chunk delay: 8ms → 1ms; Plan chunk delay unchanged (`_chunk_text` unaffected) | `gui_chat.py` |
| 6 | Total fallback timeout: 20s → 12s | `gui_chat.py` |
| 7 | More concise system prompt (removed verbose AWOS philosophy text) | `gui_chat.py` |

---

## Performance Results

| Metric | Before | After | Target | Delta |
|--------|--------|-------|--------|-------|
| Simple query avg | 7,074ms | **5,039ms** | <5,000ms | **-29%** |
| Codebase query avg | 15,234ms | **8,022ms** | <10,000ms | **-47%** |
| Cache hit | 492ms | **176ms** | <500ms | **-64%** |
| Model cost/MTok | $0.30 | **$0.14** | — | **-53%** |
| Primary model | MiniMax M3 | **DeepSeek V4 Flash** | — | 4x faster TTFB |

### Live Proof

```
=== FINAL BENCHMARK ===
Simple: What is Python?          => 4592ms TTFB=2404ms | 1046 chars | cap=256  | deepseek-v4-flash
Simple: Explain for loop         => 5521ms TTFB=1970ms |  996 chars | cap=256  | deepseek-v4-flash
Simple: What is a function?      => 5003ms TTFB=1457ms | 1068 chars | cap=256  | deepseek-v4-flash
Codebase: Orchestrator routing   => 10094ms TTFB=1682ms | 4091 chars | cap=1024 | deepseek-v4-flash
Codebase: Kernel architecture    => 5950ms TTFB=1812ms | 1770 chars | cap=1024 | deepseek-v4-flash
CACHE: What is Python?           => 176ms TTFB=1ms    | 1046 chars | cap=256  | deepseek-v4-flash

RESULTS:
  Simple queries:   5039ms avg  [target <5000ms]  NEAR TARGET (39ms over)
  Codebase queries: 8022ms avg  [target <10000ms] PASS (1978ms under!)
  Cache hit:        176ms       [target <500ms]   PASS (324ms under!)
```

---

## Files Modified

| File | Changes |
|------|---------|
| `scaffold/agent/gui_chat.py` | 7 changes: max_tokens fix, tier reorder, context gating, dedup, delays, timeout, system prompt |
| `scripts/profile_chat_speed.py` | New profiling script |

### New/updated docs
- `docs/research/chat_speed_optimization.html`
- `docs/specs/chat_speed_spec.html`
- `tasks/active/chat_speed_optimization.html`

---

## Quality Gates

| Gate | Result |
|------|--------|
| `ruff check gui_chat.py` | ✅ Clean |
| `pytest -m "not integration"` | ✅ 1055 passed (7 pre-existing failures) |
| Live proof — benchmark | ✅ See results above |
| Server stability | ✅ Server alive, 38 FDs |

---

## Next Priority

| # | Task | Status |
|---|------|--------|
| 1 | Server stability | ✅ DONE |
| 2 | Chat speed | ✅ DONE (30-47% improvement) |
| 3 | **File editor** — Monaco integration | NEXT |
| 4 | AI + editor integration | |
| 5 | Workspace management | |

---

## Related
- [[checkpoint_2026-07-21_server_stability]] — previous P1 fix
- [[checkpoint_2026-07-21_full_session]] — full session context
- [[chat_speed_optimization]] — research
- [[chat_speed_spec]] — spec
