# Pilot Plan — Get ONE Customer

> **Status:** Ready to execute  
> **Goal:** Prove AWOS compounding value with one paying pilot  
> **Timeline:** This week (3 hours)

---

## What we've built (positioning foundation)

1. ✅ **Compounding advantage narrative** — [`AWOS_POSITIONING_COMPOUNDING.md`](AWOS_POSITIONING_COMPOUNDING.md)
2. ✅ **Updated wedge doc** — [`wedge_v1_coffee_money.md`](wedge_v1_coffee_money.md)
3. ✅ **Savings tracker CLI** — `awos stats --savings`

**Value prop in 30 seconds:**

> "Cursor: Same cost every job ($3 → $3 → $3). Stateless.  
> AWOS: Learns your codebase. Job 1: $3. Job 10: $1. Job 100: $0.30.  
> Gets cheaper every run. Switch back to Cursor? Lose all that learning."

---

## Pilot criteria (find ONE customer)

### Who to target:

**Profile:**
- Small team (1-5 devs) or solo founder with active repo
- Currently using Cursor Pro (₹2,000/month) or similar
- Has recurring maintenance work (CI failures, tech debt, refactors)
- Budget-conscious but willing to pay for value

**Where to find:**
- Discord communities (Cursor Discord, AI coding channels)
- Twitter/X (follow Cursor users, reply to complaints about cost/tokens)
- Reddit (r/cursor, r/artificial, r/SideProject)
- Direct outreach to founders in your network

### What to offer:

**First month free** — in exchange for:
1. Run AWOS on 3-5 real jobs (CI rescue, refactor, tech debt)
2. Track cost vs what they would have spent on Cursor
3. Written testimonial if they see >50% savings
4. Feedback on what's missing

**After month 1:**
- If they saw compounding value → ₹2,000/month (same as Cursor)
- If not → thank you, refine based on feedback

---

## Success criteria (pilot must prove)

| Must prove | How to measure |
|---|---|
| **Cost savings** | Month 1: <50% API cost vs raw Claude Sonnet |
| **Quality** | Jobs pass semantic_pass (not false greens) |
| **Unattended** | Customer walks away, comes back to done work |
| **Compounding** | Jobs 1-3 more expensive, jobs 8-10 cheaper (track with `awos stats --savings`) |

**Hard fail conditions:**
- Customer has to babysit (defeats the value prop)
- Cost is higher than Cursor (no switching incentive)
- Quality is poor (false greens, breaks tests)

---

## Execution plan (3 hours)

### Hour 1: Outreach (1 hour)
- Write DM template (see below)
- Send to 10 potential customers across channels
- Goal: 3 replies, 1 scheduled call

### Hour 2-3: First call + onboarding (2 hours)
- Demo: `awos worker start "fix CI"` → walk away → `awos worker diff` → merge
- Show: `awos stats --savings` compounding report
- Onboard: Install AWOS, set API keys, run first job together
- Commit: 3-5 jobs in first week, feedback session at end

---

## DM template (customizable)

```
Hey [Name],

Saw you're using Cursor. Quick question: do you ever start a fix/refactor in chat, 
then walk away halfway through because it takes too many prompts?

I built AWOS — it's like Cursor but for unattended multi-hour work (CI rescue, 
tech debt batches, refactors). The key difference: it gets cheaper the more you 
use it (learns your codebase over time).

Cursor: $3 per job, forever.
AWOS: $3 → $1 → $0.30 (compounding learning).

Would you be interested in a free pilot month? 3-5 real jobs on your repo, 
track actual cost savings vs Cursor.

If you see >50% savings, it's ₹2k/month (same as Cursor). If not, no charge, 
just feedback.

Let me know if you want a 15-min demo.

— [Your Name]
```

---

## Live proof (when pilot starts)

**Command:**
```bash
awos stats --savings
```

**Expected output:**
```
╔═════════════════════════════════════════════════════════════════════╗
║        AWOS COMPOUNDING ADVANTAGE — Cost Savings Report            ║
╚═════════════════════════════════════════════════════════════════════╝

Month:          2026-06
Total spent:    $4.27
Cache savings:  $0.82
Requests:       34

Cost per request trend (batches of 10):

  Batch  1: $0.0145 ██████████████
  Batch  2: $0.0098 █████████
  Batch  3: $0.0067 ██████

Learning trend: -53.8% cost change from first to last batch

═══════════════════════════════════════════════════════════════════════
AWOS VALUE PROPOSITION:

  Cursor: Same cost every job ($3 → $3 → $3)
  AWOS:   Gets cheaper with learning ($3 → $1 → $0.30)

  How?
    • DeepSeek routing (instant 50% savings)
    • PromptEvolver learns YOUR patterns
    • SkillLibrary caches YOUR solutions
    • Fewer retries = lower cost

  The more you use it, the cheaper it gets.
═══════════════════════════════════════════════════════════════════════
```

**Success marker:** Customer sees negative cost trend (learning kicks in).

---

## Next steps (after pilot starts)

1. **Week 1:** Customer runs 3-5 jobs, we track cost/quality
2. **Week 2:** Feedback session — what's missing? (likely: search+planning)
3. **Week 3:** Iterate based on feedback, measure compounding
4. **Week 4:** Decide: convert to paying ($2k/month) or refine more

**THEN:** Build search+planning agent (6 weeks, see priority plan).

---

## Files to send pilot customer

1. `AWOS_POSITIONING_COMPOUNDING.md` — full value prop
2. `docs/ci_rescue_sprint_proof.md` — proof AWOS can finish CI jobs
3. `awos --help` — CLI reference

---

## Open questions (for first call)

- What type of work do you wish Cursor could finish unattended?
- How much time do you spend babysitting chat agents?
- What's your monthly API budget for coding agents?
- Would 50% cost savings justify switching?

---

**Ready to execute.** Next step: 1 hour of outreach.
