---
title: "Cost Analysis — Corrected (Based on Real Token Data)"
tags:
  - doc/analysis
  - topic/cost-optimization
---

# Cost Analysis — Corrected 

**Correction Date:** 2026-05-15  
**Issue:** Initial calculations assumed arbitrary token budgets. Fixed with actual data.

---

## Your Actual Usage Pattern

**Input Data:**
- 1000 requests/month
- 1,000,000 total tokens/month
- 60% input tokens (600k), 40% output tokens (400k)
- Workflow: Initially lots of code context (understanding), then developing new code
- High-cost operations: Code reviews, architecture, bug analysis, tests, refactoring

**Average per request:**
- 600 input tokens
- 400 output tokens
- 1000 tokens total

---

## Monthly Cost by Model (ACTUAL)

| Model | Cost/Month | Budget % | Growth Margin | Verdict |
|---|---|---|---|---|
| DeepSeek ($0.14/$0.14) | $0.14 | 1% | 107x | ✅ SAFEST |
| DeepSeek-R1 ($0.55/$0.55) | $0.55 | 4% | 27x | ✅ GOOD |
| Claude-Haiku ($0.80/$0.40) | $0.64 | 4% | 23x | ✅ GOOD |
| Claude-Sonnet ($5.0/$15.0) | $9.00 | 60% | 1.6x | ⚠️ RISKY |
| Claude-Opus ($15.0/$75.0) | $39.00 | 260% | 0.4x | ❌ IMPOSSIBLE |

---

## The Problem with Sonnet

**Why expensive models fail at scale:**

```
Sonnet output costs $15/1M tokens
Your output is 400k tokens/month = 400k × $15/1M = $6.00

That's 60% of your entire $15 budget for just the OUTPUT tokens!

If tokens grow 2x:
  Input:  1.2M × $5.00/1M = $6.00
  Output: 0.8M × $15.00/1M = $12.00
  Total: $18.00 ❌ EXCEEDS $15 BUDGET
```

**Growth margin:** Only 1.6x possible before hitting budget limit.

---

## The Solution: Cost-Safe Routing

**New routing strategy (output cost capped at $5/MTok):**

```
Complexity 1-3: DeepSeek ($0.14/$0.14)
  Cost at 1M tokens: $0.14/month
  Growth margin: 107x ✅
  
Complexity 4-6: DeepSeek-Reasoner ($0.55/$0.55)
  Cost at 1M tokens: $0.55/month
  Growth margin: 27x ✅
  
Complexity 7-10: Claude-Haiku ($0.80/$0.40)
  Cost at 1M tokens: $0.64/month
  Growth margin: 23x ✅
  
NEVER: Claude-Sonnet (output $15 kills margin) ❌
NEVER: Claude-Opus (output $75 impossible) ❌
```

---

## Scaling Analysis: What Happens if Tokens Grow?

### At 2M tokens/month (2x growth)

| Model | Cost |  Margin? |
|---|---|---|
| DeepSeek | $0.28 | ✅ OK (Still 54x growth possible) |
| Haiku | $1.28 | ✅ OK (Still 12x growth possible) |
| Sonnet | $18.00 | ❌ EXCEEDS BUDGET |

### At 5M tokens/month (5x growth)

| Model | Cost | Margin? |
|---|---|---|
| DeepSeek | $0.70 | ✅ OK (Still 21x growth possible) |
| Haiku | $3.20 | ✅ OK (Still 5x growth possible) |
| Sonnet | $45.00 | ❌ WAY OVER |

### At 10M tokens/month (10x growth)

| Model | Cost | Margin? |
|---|---|---|
| DeepSeek | $1.40 | ✅ OK (Still 11x growth possible) |
| Haiku | $6.40 | ✅ OK (2.3x growth possible) |
| Sonnet | $90.00 | ❌ IMPOSSIBLE |

---

## Key Insight: Output Token Cost is the Killer

**Why Sonnet doesn't work at scale:**

```
Input costs:  $5.00/1M (reasonable)
Output costs: $15.00/1M (3x more expensive!)

As your output grows, cost explodes.

At your current 600k input + 400k output:
  Input cost:  $3.00 ✅
  Output cost: $6.00 ⚠️ (60% of budget!)
  
If output grows to 600k (only 50% more):
  Input cost:  $3.00
  Output cost: $9.00 ❌ (75% of budget)
  
If output grows to 800k:
  Input cost:  $3.00
  Output cost: $12.00 ❌ (90% of budget)
```

**Solution:** Use models with low output costs.

---

## Haiku vs Sonnet

**Why Haiku is safer:**

```
Output cost: $0.40/1M (vs Sonnet $15/1M)

At your current 400k output tokens:
  Haiku output cost:   $0.16 ✅ (1% of budget)
  Sonnet output cost:  $6.00 ⚠️ (40% of budget)

If output grows to 1M tokens:
  Haiku output cost:   $0.40 ✅ (2.7% of budget)
  Sonnet output cost:  $15.00 ❌ (100% of budget!)
  
Haiku growth margin: 23x
Sonnet growth margin: 1.6x
```

---

## Recommendation: Use DeepSeek + Haiku

**Tier Distribution:**
- **Complexity 1-3 (60% of requests):** DeepSeek ($0.14/MTok)
- **Complexity 4-6 (30% of requests):** DeepSeek-Reasoner ($0.55/MTok, for reasoning)
- **Complexity 7-10 (10% of requests):** Claude-Haiku ($0.80 input, $0.40 output)

**Cost at 1M tokens/month:**
```
60% × (600 input + 400 output) × $0.14 = (600k × $0.14 + 400k × $0.14) × 0.6 = $0.084
30% × (600 input + 400 output) × $0.55 = (600k × $0.55 + 400k × $0.55) × 0.3 = $0.165
10% × (600 input + 400 output) × $0.80/$0.40 = (600k × $0.80 + 400k × $0.40) × 0.1 = $0.064
TOTAL: $0.313/month ← WAY under $15 budget
```

**Result:** 98.9% budget remaining for growth!

---

## The Math You Should Never Forget

### Input vs Output Token Cost

**Sonnet (the trap):**
- Input: $5.00/1M ✅
- Output: $15.00/1M ❌ (3x more expensive)

Your 40% output tokens = disproportionate cost.

**Haiku (the safe choice):**
- Input: $0.80/1M ✅
- Output: $0.40/1M ✅ (CHEAPER than input!)

Haiku rewards you for having output tokens.

### Growth Margin Calculation

```
Growth margin = $15 (budget) / Current monthly cost

DeepSeek: $15 / $0.14 = 107x growth possible
Haiku: $15 / $0.64 = 23x growth possible
Sonnet: $15 / $9.00 = 1.6x growth possible (ONE AND A HALF TIMES!)
```

---

## Never Make This Mistake Again

❌ **Don't do this:** Assume all tokens cost the same rate  
❌ **Don't do this:** Ignore output token cost (often 2-3x input)  
❌ **Don't do this:** Use expensive models without growth margin  

✅ **Do this:** Check OUTPUT token pricing separately  
✅ **Do this:** Calculate growth margin (budget / current cost)  
✅ **Do this:** Use models with low output costs  

---

## Summary

| Metric | Value |
|---|---|
| Your current tokens | 1M/month |
| Current cost w/ safe model | $0.14-$0.64/month |
| Budget remaining | 95%+ |
| Safe growth limit (Haiku) | 23x tokens |
| Safe growth limit (DeepSeek) | 107x tokens |
| Unsafe model | Claude-Sonnet (1.6x growth) |
| Impossible model | Claude-Opus (can't afford) |

**Recommendation:** Use DeepSeek for budgets, Haiku for complex, never Sonnet/Opus.

---

**Status:** Analysis corrected, routing updated, safety verified ✅
