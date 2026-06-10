# AWOS Effectiveness Analysis

> **Canonical metric:** Project Efficiency Index (PEI) = (Quality × Speed) / Cost
> See [[VISION]] for full strategy. This doc tracks early Coding App benchmarks.

**See full analysis:** [EFFECTIVENESS_ANALYSIS.html](EFFECTIVENESS_ANALYSIS.html)

---

## North Star

AWOS optimizes **project delivery efficiency**, not tokens or model names:

| Axis | Definition | Tracked via |
|---|---|---|
| **Quality** | Success rate, test pass rate, rework cycles | `performance.json`, verifier results |
| **Speed** | Time from goal to done | `spans.jsonl` (latency_ms) |
| **Cost** | Total LLM spend per project | `budget.json`, `reward_store.jsonl` |

**PEI** = (Quality × Speed) / Cost — should improve week over week as `.awos/` compiles experience.

---

## Coding App Benchmarks (Phase 1 proof point)

Early sandbox results for App #1 on the kernel:

| Metric | Value | Notes |
|---|---|---|
| Avg cost/request | $0.00053 | Conservative routing (DeepSeek-first) |
| Avg latency/task | ~2.6s | Single-task sandbox |
| Quality (simple tasks) | ~100% | Sandbox scope |
| Quality (medium tasks) | ~85% | Improves with PromptEvolver + LinUCB |
| Cache hit rate | ~40% | Pure margin on repeats |

These are **kernel capability proofs**, not the product promise. The product promise is **PEI improvement over time per deployment**.

---

## What the learnable kernel adds (not in static tools)

| Learning layer | Effect on PEI |
|---|---|
| LinUCB routing | Cost ↓ — right model first try |
| PromptEvolver | Quality ↑ — fewer repeated mistakes |
| LiveToolSynthesizer | Speed ↑, Cost ↓ — skip re-derivation |
| VectorMemory | Speed ↑ — past solutions injected |
| Cache | Speed ↑↑, Cost ↓↓ — instant on repeats |

Week 1 vs Week 8 on same repo (expected trajectory):

| | Week 1 | Week 8 |
|---|---|---|
| Quality | ~70% | ~85% |
| Speed | baseline | 2–3× faster |
| Cost | baseline | 60–80% cheaper |

---

## What we do not optimize for

- IDE UX or autocomplete acceptance rate
- Seat count or daily active users
- Model brand or frontier benchmark scores
- Beating Cursor on feature parity

We optimize **project outcomes for niche customers** who embed or deploy AWOS as infrastructure.

---

## Related

- [[VISION]] — canonical identity and business model
- `awos stats` — live self-learning observability
- `scaffold/agent/reward_store.py` — objective function implementation
