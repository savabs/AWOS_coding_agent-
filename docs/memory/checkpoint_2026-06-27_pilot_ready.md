# Checkpoint — 2026-06-27 — Pilot Ready

> **Previous:** [`checkpoint_2026-06-25_session_handoff.md`](checkpoint_2026-06-25_session_handoff.md)  
> **Status:** Positioning complete, pilot plan ready, search+planning next (AFTER pilot)

---

## What we shipped today

### 1. Compounding advantage positioning ✅

**File:** [`docs/product/AWOS_POSITIONING_COMPOUNDING.md`](../product/AWOS_POSITIONING_COMPOUNDING.md)

**Key insight from user:**
> "when you leave it at friday night and see it finish at tomorrow morning. that is wrong framing, cause running the model continuously for 6hr will burn the fuck out of the token, what we want is you leave it on agent, it will for sure use less token over a long horizon cause it understand the codebase or the way of coding, basically evolving."

**Corrected value prop:**
- **NOT:** One long overnight run (burns tokens)
- **YES:** Many short jobs over weeks/months, each job gets cheaper due to learning

**Core narrative:**

| | Cursor | AWOS |
|---|---|---|
| **Job 1 cost** | $3 | $1.50 (DeepSeek routing) |
| **Job 10 cost** | $3 (same) | $1 (learning kicks in) |
| **Job 100 cost** | $3 (same) | $0.30 (repo expert) |
| **State** | Stateless | Learns every run |

**Moat strategy:** "Apple ecosystem playbook"
- Switch back to Cursor? Lose all that learning.
- Medium moat (current): Local `.awos/` files + DeepSeek routing
- Strong moat (future): Server-side LinUCB routing + fine-tuned models (enterprise)

### 2. Updated wedge doc ✅

**File:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)

**Changes:**
- Added "Immediate advantage (month 1)" vs "Compounding advantage (month 3+)"
- Updated pitch to emphasize learning curve
- Linked to full positioning doc

**30-second pitch:**
> "Cursor: Same cost every job. $3 to fix a bug today, $3 next month, $3 forever. Stateless.
> 
> AWOS: Learns your codebase. Job 1: $3. Job 10: $1. Job 100: $0.30. Gets cheaper every run.
> 
> Why? DeepSeek routing (instant 50% savings), then PromptEvolver learns YOUR patterns, SkillLibrary caches YOUR solutions, fewer retries = lower cost.
> 
> First month: Save $100 vs Cursor. Third month: Save $300 (learning kicks in).
> 
> The more you use it, the cheaper it gets. Switch back to Cursor? Lose all that learning."

### 3. Cost savings tracker CLI ✅

**Command:** `awos stats --savings`

**What it shows:**
1. Month-to-date spend + cache savings
2. Cost per request trend (batches of 10)
3. Learning trend % (first batch → last batch)
4. Value prop reminder (Cursor stateless vs AWOS learning)

**Example output:**
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
```

**Purpose:** Show pilot customers the compounding advantage in action.

### 4. Pilot plan ✅

**File:** [`docs/product/PILOT_PLAN.md`](../product/PILOT_PLAN.md)

**Goal:** Get ONE customer, prove compounding value.

**Offer:** First month free, in exchange for:
- Run AWOS on 3-5 real jobs
- Track cost vs Cursor
- Written testimonial if >50% savings
- Feedback on what's missing

**Success criteria:**
- Cost <50% vs raw Claude
- Jobs pass semantic_pass
- Customer walks away (unattended work)
- Jobs 1-3 expensive, jobs 8-10 cheaper

**Execution:** 1hr outreach → 2hr first call + onboarding

**DM template included** for outreach.

---

## Next priorities (user directive)

### IMMEDIATE (this week):
1. ✅ Document compounding positioning
2. ✅ Update wedge doc
3. ✅ Create savings tracker CLI
4. **PENDING:** Execute pilot plan (1hr outreach)

### AFTER PILOT (6 weeks):
**User request:** "we need to have the planning + search . cause that is what would make it like the a developer that is really good."

**Plan:**
- **Weeks 2-3:** Enhanced search (leverage `vector_memory.py` + `symbol_index.py`)
- **Weeks 4-6:** Deeper planning (enhance `planner.py`)
- **Week 7+:** Learning from plans (compounding moat)

**Key insight:** Good dev behavior = search codebase first → reason about dependencies → then execute.

**Current flow:**
```
Goal → Planner → Worker → Verify → Done
```

**Needed flow:**
```
Goal 
  ↓
SEARCH (understand codebase)
  "Where is this defined?"
  "What files are related?"
  "What's the existing pattern?"
  ↓
PLANNING (multi-step reasoning)
  "Need to change 3 files"
  "This order prevents breakage"
  "Tests must update here"
  ↓
Worker → Verify → Done
```

**This is the moat:**
- Cursor: Fast chat, shallow search (10-20 open files)
- AWOS: Deep search, multi-step planning (entire repo + history + `.awos/` learning)

**Timeline:** ~6 weeks to "senior dev" level search + planning.

---

## Key user insights from today

1. **"Speed" means human time, not agent time:**
   - User: "remember the time you are mentioning is what you think is time taken by humans to code it, but you are agent code it really fast that."
   - Corrected: AWOS saves human attention time (unattended operation), not machine wall-clock time.

2. **Compounding over time, not continuous long runs:**
   - User: "running the model continuously for 6hr will burn the fuck out of the token, what we want is you leave it on agent, it will for sure use less token over a long horizon cause it understand the codebase."
   - Corrected: Many short jobs (5-10 min each) over weeks/months, each gets cheaper.

3. **Apple ecosystem moat:**
   - User: "want we want is that they stick to our use case cause over long horizon we are very very much cost efficient and its tough to exit out of the ecosystem, the way apple does."
   - Key: Switching cost moat via learning, not just features.

4. **Planning + search is what makes a "really good developer":**
   - User: "we need to have the planning + search . cause that is what would make it like the a developer that is really good."
   - Priority: Build this AFTER pilot validates compounding value.

---

## Files created/updated today

**Created:**
- `docs/product/AWOS_POSITIONING_COMPOUNDING.md` — full value prop + moat analysis
- `docs/product/PILOT_PLAN.md` — execution plan for first customer
- `docs/memory/checkpoint_2026-06-27_pilot_ready.md` — this file

**Updated:**
- `docs/product/wedge_v1_coffee_money.md` — compounding advantage positioning
- `awos.py` — added `awos stats --savings` command
- `memories/repo/project_structure.md` — reflected completion status

---

## Current state

**Stage:** Positioning complete, pilot-ready  
**Next action:** 1hr outreach (find ONE customer)  
**Blocker:** None — ready to execute  

**After pilot starts:** Build search+planning agent (6 weeks).

---

## For next session

**If continuing pilot:**
- Read [`docs/product/PILOT_PLAN.md`](../product/PILOT_PLAN.md)
- Execute outreach (DM template provided)
- Track first customer through onboarding

**If pilot already running:**
- Gather feedback from Week 1 jobs
- Start research phase for search+planning agent
- Read `scaffold/agent/planner.py`, `memory/vector_memory.py`, `tools/symbol_index.py`

**Quick cold-start:**
```bash
cat VISION.md  # Core identity
cat memories/repo/project_structure.md  # Project facts
cat docs/product/AWOS_POSITIONING_COMPOUNDING.md  # Current value prop
cat docs/product/PILOT_PLAN.md  # Next action
```

---

**Status:** Ready. Ship the pilot.
