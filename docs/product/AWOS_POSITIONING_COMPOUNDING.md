# AWOS Positioning — The Compounding Advantage

**Date:** 2026-06-27  
**Strategy:** Apple ecosystem playbook — easy to start, hard to leave

---

## The Core Insight

**Cursor:** Stateless. Every job costs the same. No switching cost.  
**AWOS:** Learning. Every job costs less. High switching cost.

**This is your moat.** Not faster execution. Not better models. **Compounding.**

---

## The Value Curve

### Cursor (Stateless)

```
Month 1: $300 API spend
Month 3: $300 API spend  
Month 6: $300 API spend
Month 12: $300 API spend

TOTAL: $3,600 over 12 months
```

Every job starts from scratch. No memory. No learning.

### AWOS (Learning)

```
Month 1: $150 API spend (50% less via DeepSeek routing)
Month 3: $90 API spend (70% less — learning kicks in)
Month 6: $45 API spend (85% less — fully optimized)
Month 12: $30 API spend (90% less — your repo expert)

TOTAL: $900 over 12 months (75% savings)
```

Every job improves the next one. `.awos/` compounds.

---

## How Compounding Works

### 1. Immediate Savings (Day 1)

**DeepSeek routing:** 50% cheaper than Claude  
- Claude Sonnet: $3/MTok input  
- DeepSeek: $0.14/MTok input (20× cheaper)  
- Start cheap, escalate only on failure

**Result:** Job 1 costs $1.50 instead of $3

### 2. Learning Savings (Week 2-4)

**PromptEvolver learns YOUR patterns:**
- First CI rescue: Generic prompts, 15 API calls  
- Fifth CI rescue: Learned patterns, 8 API calls  
- Fewer retries = lower cost

**Result:** Job 10 costs $1 (33% less than job 1)

### 3. Optimization Savings (Month 2-6)

**SkillLibrary caches YOUR solutions:**
- Repeated tasks use cached skills (0 cost)  
- Common patterns solved once, reused forever  
- Cheaper models work (better context)

**Result:** Job 50 costs $0.50 (67% less than job 1)

### 4. Expert Savings (Month 6+)

**Full repo intelligence:**
- `.awos/` knows your coding style  
- Error patterns pre-solved  
- Optimal model routing learned  
- Near-zero retry loops

**Result:** Job 100 costs $0.30 (90% less than job 1)

---

## The Switching Cost Moat (Apple Playbook)

### Easy to Start

```bash
pip install awos
awos worker start "fix CI"
```

First job works immediately. No learning curve. No config.

### Hard to Leave

After 6 months:
- **1,000+ learned patterns** in `.awos/prompts/`  
- **200+ cached skills** in `.awos/skills/`  
- **Your repo's error map** in `.awos/error_patterns.jsonl`  
- **Optimized routing** via `.awos/ml_router/`

**Switch back to Cursor?**  
→ Lose all that learning  
→ Back to $300/month  
→ Back to generic prompts  
→ Back to cold-start every job

**The pain is real.** Just like leaving iPhone for Android.

---

## The Customer Conversation

### Month 1 (Trial)

**Customer:** "Let me try AWOS on one repo."  
**You:** "Sure. First month: $150 vs Cursor's $300."

### Month 3 (Seeing Value)

**Customer:** "Wow, my bill dropped to $90. Why?"  
**You:** "AWOS learned your repo. It's getting smarter."

### Month 6 (Locked In)

**Customer:** "Can I cancel?"  
**You:** "Yes, but you'll lose 6 months of learning. Back to $300/month with Cursor."  
**Customer:** "...I'll stay."

### Month 12 (Advocate)

**Customer:** "I'm paying $30/month for what used to cost $300. AWOS learned everything."  
**You:** "Want to add another repo? Learning transfers."

---

## Pricing That Reinforces Lock-In

### Structure

```
AWOS Solo: ₹2,000/month
  • Unlimited jobs
  • One repo
  • Learning compounds forever
  • Cancel anytime (but lose .awos/)

AWOS Team: ₹8,000/month
  • Unlimited jobs
  • 5 repos
  • Shared learning pool
  • Team-level skill library
```

### The Hook

**First month discount:** ₹1,000 (50% off)  
→ Easy to try

**After 6 months:** Customer's bill is ₹2,000 but saves ₹6,000 vs Cursor  
→ 3× ROI every month

**Cancel?** Lose `.awos/` → back to ₹8,000 with Cursor  
→ ₹6,000/month pain to leave

---

## What To Prove

### Benchmark: Repeat Job Cost Decline

**Setup:** Run same CI rescue 10 times on same repo

**Measure:**
- API cost per run  
- API calls per run  
- `.awos/` state growth

**Expected Result:**

| Run | Cost | Calls | .awos/ Size | Notes |
|-----|------|-------|-------------|-------|
| 1 | $3.00 | 15 | 0 KB | Cold start |
| 2 | $2.50 | 13 | 50 KB | First patterns |
| 3 | $2.00 | 11 | 120 KB | Learning |
| 5 | $1.50 | 8 | 300 KB | Optimizing |
| 10 | $1.00 | 5 | 800 KB | Expert mode |

**Proof:** "Job 10 costs 67% less than job 1"

---

## Competitive Positioning

### vs Cursor

| | Cursor | AWOS |
|--|--------|------|
| **Month 1 cost** | $300 | $150 (50% less) |
| **Month 6 cost** | $300 | $45 (85% less) |
| **Switching cost** | $0 (stateless) | High (lose learning) |
| **Moat** | Brand, UX | Compounding data |

### vs Raw API

| | Raw API | AWOS |
|--|---------|------|
| **Month 1 cost** | $200 | $150 (25% less) |
| **Month 6 cost** | $200 | $45 (78% less) |
| **Quality** | No verify loop | Semantic pass |
| **Moat** | None | Compounding data |

---

## The Pitch (30 seconds)

> "AWOS is the agent that gets cheaper the more you use it.
>
> Cursor costs $300/month forever — no memory, starts fresh every job.  
> AWOS costs $150 first month, $90 third month, $45 by month six.
>
> Why? It learns your repo. Caches your patterns. Uses cheaper models.
>
> First month: Save $150.  
> First year: Save $2,700.  
> Switch back? Lose all that learning.
>
> Like iPhone — easy to start, hard to leave."

---

## The Pitch (Founder Version)

> "I built AWOS because I was tired of paying Anthropic $500/month for the same mistakes.
>
> Every CI rescue, Cursor starts from scratch. Generic prompts. Expensive models.
>
> AWOS learns. First rescue costs $3. Tenth rescue costs $1. Fiftieth costs $0.50.
>
> It's not smarter models. It's smarter context.
>
> Your `.awos/` folder is your moat. Six months of learned patterns.
>
> Try to switch back to Cursor? You'll feel it immediately.
>
> Like leaving iPhone for Android — technically possible, emotionally painful."

---

## Internal Metrics to Track

### Compounding Health

1. **Cost decline rate:** Average % drop from job 1 → job 10
2. **Learning velocity:** Days until 50% cost reduction
3. **Churn by usage:** Customers who ran 50+ jobs vs 5 jobs
4. **`.awos/` size vs cost:** Correlation between state size and savings

### Target Goals

- **Month 1:** 50% cheaper than Cursor (routing)
- **Month 3:** 70% cheaper (routing + learning)
- **Month 6:** 85% cheaper (fully optimized)
- **Churn:** <5% for customers past 20 jobs (locked in)

---

## What This Unlocks

### Product Strategy

- **Free tier:** 10 jobs/month (hook them with learning)
- **Pay tier:** Unlimited (let them compound)
- **Enterprise:** Shared learning pool (team moat)

### Sales Strategy

- **First call:** "Try one repo, see the savings"
- **Month 3 check-in:** "Notice your bill dropped? That's learning."
- **Renewal:** "Cancel? You'll lose X patterns, back to $Y/month"

### Roadmap Priority

1. ✅ **Routing** (immediate 50% savings)
2. ✅ **Learning infrastructure** (PromptEvolver, SkillLibrary)
3. ⚠️ **Prove compounding** (repeat-job benchmark)
4. 🔲 **Visualize moat** (show customer their `.awos/` value)
5. 🔲 **Cross-repo learning** (enterprise moat)

---

## Status (2026-06-27)

**Infrastructure:** Ready  
- ✅ DeepSeek routing  
- ✅ PromptEvolver  
- ✅ SkillLibrary  
- ✅ Cheap-only mode  

**Proof:** Missing  
- ❌ Repeat-job benchmark  
- ❌ Cost decline over 10 runs  
- ❌ Customer testimonial  

**Next Step:** Run repeat-job benchmark, prove compounding works.

---

## The One-Liner

**"AWOS: The agent that gets cheaper every run. Like iPhone — easy to start, expensive to leave."**
