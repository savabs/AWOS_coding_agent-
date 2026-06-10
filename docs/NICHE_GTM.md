# AWOS — Fast Earnable Track (Niche GTM)

> **Canonical owner for go-to-market strategy.**
> Complements [[VISION]] — identity and architecture.
> Last updated: 2026-06-08

---

## Principle

**Services first, product second.** Fastest path to revenue is not building a public API and waiting for signups. It is:

1. Pick **one niche** with acute pain + budget
2. Sell **done-for-you** deployment on their repos
3. Prove **PEI improvement** week over week
4. Productize after 3 paying clients say the same thing

Target: **first $5k/month within 60 days** from 2–3 niche clients, not 1,000 self-serve users.

---

## What is shippable today (no new product required)

| Capability | Status | Sales use |
|---|---|---|
| `awos run --goal` | ✅ Works | "We execute features autonomously" |
| Multi-session goals | ✅ Works | "Resume 3-week work across sessions" |
| Budget hard cap | ✅ Works | "Never exceed $X/month on AI" |
| Self-learning loop | ✅ Works | "Gets cheaper and better over time" |
| `awos stats` | ✅ Works | Internal proof; needs PEI wrapper for clients |
| Vector memory | ✅ Works | "Remembers your codebase patterns" |
| MCP server | ✅ Works | Embed in Cursor/Windsurf for agencies |
| LinUCB routing | ✅ Works | Hidden; margin story for you, not them |

**Not required for first revenue:** public API, landing page with Stripe, perfect test suite, model ladder cleanup.

**Required for first sale (1–2 weeks build):**
- Client-facing **PEI scorecard** (PDF or HTML report from `.awos/` data) — ✅ **`awos report --html`**
- **One-page offer** (who, pain, price, deliverable) — ✅ **`docs/AGENCY_ONE_PAGER.md`**
- **2 case study templates** (even from your own repo runs) — ✅ **`reports/pei_report.html`**

---

## Ranked niches — fastest to revenue

### 🥇 Tier 1 — Dev agencies (5–20 devs) — START HERE

**Pain:** Same patterns across client repos. Juniors repeat mistakes. No memory between projects. AI spend is chaotic (everyone on Copilot, no routing).

**Why AWOS wins:**
- `.awos/` learns **agency patterns**, not just one repo
- PEI scorecard = billable proof ("we saved 40% on client X delivery")
- MCP server = works inside their existing Cursor workflow (no IDE migration)

**Offer:** "Learning Agent Deployment"
```
Setup:     $2,000 one-time (install AWOS, index repos, configure budget)
Monthly:   $499/mo (managed .awos/ state, PEI reports, prompt evolution)
Includes:  3 repos, $50/mo AI budget cap, weekly PEI report
```

**Sales cycle:** 1–3 weeks (founder-to-founder, Clutch/Twitter DMs)

**How to find:** Search "web development agency" + city on Clutch. Indie dev shops on Twitter/X. Agencies posting job ads for React/Node (they're busy = they need leverage).

**First outreach angle:**
> "We deploy an agent on your client repos that learns your agency's patterns — gets faster and cheaper every project. First month we prove it with a PEI scorecard on one repo."

**Path to $5k/mo:** 2 agency clients at $499 + 1 setup fee = ~$3.5k first month, $1k recurring growing.

---

### 🥈 Tier 2 — AI cost audit (teams already spending on Cursor + APIs)

**Pain:** CTO doesn't know what AI is actually costing per feature. Devs use Sonnet for everything. No learning, no caps.

**Why AWOS wins:**
- BudgetLedger + bandit routing = immediate 60–80% cost reduction story
- PEI audit is a **1-week engagement**, not a product sale
- Converts to managed deployment ($499/mo) if audit shows savings

**Offer:** "AI Delivery Audit"
```
One-time:  $1,500 (1 week)
Deliverable: PEI baseline report on 1 repo
             Current cost per task vs AWOS-routed cost (projected)
             Top 5 waste patterns found
Upsell:    $499/mo managed AWOS if savings > $500/mo
```

**Sales cycle:** 1–2 weeks (CTO/engineering lead)

**How to find:** Companies posting "AI engineer" roles. DevTools startups with public GitHub (check their CI spend patterns). HN "Ask HN: how much do you spend on AI coding tools?"

**First outreach angle:**
> "We'll run your repo through our learning agent for one week and show you exactly what each feature costs in AI spend — and what it could cost with routing optimization."

**Path to $5k/mo:** 3 audits/month at $1.5k = $4.5k (services-heavy, but fast cash).

---

### 🥉 Tier 3 — OSS maintainers with stale backlogs

**Pain:** 200 open issues, 2 maintainers, no time. Contributors don't know project conventions.

**Why AWOS wins:**
- Multi-session goals (`awos run --resume`)
- Learns project conventions into `.awos/skills/`
- Good word-of-mouth in dev community

**Offer:** "Maintainer Agent"
```
Monthly:   $99/mo (lower price, higher volume potential)
Includes:  1 repo, issue backlog triage + small fix PRs, PEI report
```

**Sales cycle:** 2–4 weeks, but lower ACV. Better as **marketing** (case studies, HN posts) than primary revenue initially.

**Use when:** You have agency case study and want credibility. Not first revenue source.

---

### Tier 4 — Platform embed (DevTools / no-code) — MONTH 3+

**Pain:** Want "AI coding agent" in their product. Can't build routing/learning layer.

**Offer:** Platform license $1,500–2,000/mo + usage

**Sales cycle:** 6–12 weeks. High ACV but slow. Pursue after you have PEI proof from Tier 1.

---

## What NOT to pursue early (slow earn)

| Niche | Why slow |
|---|---|
| Individual developers at $20/mo | Wrong customer, wrong price, support hell |
| Enterprise compliance (fintech/health) | 6-month sales cycle, legal review |
| "Better Cursor" positioning | Competing on UX you don't have |
| Public API + wait for signups | No distribution, no trust yet |
| General-purpose autonomous agent | Hermes/OpenClaw territory |

---

## 30 / 60 / 90 day plan

### Days 1–14: Make it sellable

| Task | Effort | Output |
|---|---|---|
| Build PEI scorecard generator | 2–3 days | HTML/PDF report from `.awos/` spans + budget |
| Run AWOS on 2 of your own repos | 1 day | Before/after PEI numbers for pitch deck |
| Write one-page offer (agency) | 2 hours | PDF or Notion page |
| List 30 target agencies | 2 hours | Spreadsheet with contact + repo link |
| Send 10 outreach emails/DMs | Ongoing | 2 per day |

**Do not build:** FastAPI, landing page with payments, new features.

### Days 15–30: First paying client

| Task | Target |
|---|---|
| 3 discovery calls | From outreach |
| 1 free 1-week pilot | One agency, one repo — generate PEI report |
| 1 paid setup ($2k) or audit ($1.5k) | Convert pilot |
| Document case study | Even partial data counts |

### Days 31–60: Recurring base

| Task | Target |
|---|---|
| 2nd agency client | Referral or second outreach batch |
| Productize weekly PEI email | Automated from `awos stats` |
| Raise price to $499/mo after proof | |
| **Target: $3–5k MRR** | 2 clients × $499 + setups |

### Days 61–90: Repeatable motion

| Task | Target |
|---|---|
| 3rd + 4th client | Same playbook |
| CI integration offer | "AWOS fixes failing CI" GitHub Action |
| Start 1 platform conversation | Warm intro from agency client |
| **Target: $5–8k MRR** | |

---

## Revenue math (realistic)

| Month | Source | Revenue |
|---|---|---|
| 1 | 1 agency setup | $2,000 |
| 1 | 1 audit | $1,500 |
| 2 | 2 agencies × $499/mo | $998 MRR |
| 2 | 1 audit | $1,500 |
| 3 | 3 agencies × $499/mo | $1,497 MRR |
| 3 | 1 platform pilot | $1,500 |
| **Month 3 total** | | **~$4k MRR + $5k one-time** |

Not unicorn scale. Real business with 2 people.

---

## The pitch (agency version)

> **Your agency's AI forgets everything between client projects.**
>
> We deploy AWOS — a learning agent that runs on your repos, tracks your team's patterns and mistakes, and gets faster and cheaper every project.
>
> Week 1: baseline PEI scorecard.
> Week 4: 40% cost reduction, 2× speed on repeat task types.
> You keep the `.awos/` state — it's your institutional memory.
>
> $2k setup + $499/mo. One repo free for 2 weeks to prove it.

---

## The pitch (audit version)

> **You're spending $2–5k/month on AI coding tools and have no idea if it's working.**
>
> We run your repo through AWOS for one week and deliver a PEI report:
> - Cost per feature (actual, not estimated)
> - Where you're overpaying for model tier
> - Projected savings with learning routing
>
> $1,500, one week, one repo. If we can't find 30%+ savings, you don't pay.

(Risk reversal on second pitch accelerates closes.)

---

## Immediate next build (priority order)

1. ~~**PEI scorecard** — `awos report` command → HTML client deliverable~~ ✅ DONE
2. ~~**Agency one-pager** — offer, pricing, PEI sample~~ ✅ DONE
3. **Outreach list** — 30 agencies, start sending
4. **Pilot on client repo** — generate before/after PEI numbers (quality must improve)
5. **Enable self-learning flags** — `AWOS_PROMPT_EVOLUTION=true` in `.env` for pilot repos

---

## Related

- [[VISION]] — product identity
- [[memories/repo/project_structure]] — current phase and metrics
- `awos stats` — internal observability (basis for PEI report)
