---
title: "Venture Disqualifying Criteria"
tags:
  - doc/strategy
  - topic/decision
---

# Venture Disqualifying Criteria

> **Purpose:** kill a candidate in minutes, before investment, using rules
> written while you had nothing at stake.
>
> **Calibrated 2026-08-31.** Change a threshold only with a dated note saying
> why — never mid-candidate, and never after seeing a result you liked.

## Why this exists

Two projects, ~9 months, zero revenue. Neither failed on engineering.

- **tirramind** — 5 months of collectors built before the central question
  ("does this data predict returns?") was ever asked. Answer, when finally
  measured: 0 of 51 hypotheses survived correction.
- **AWOS** — entered the most contested category in software with no
  distribution. Measured 2026-08-31: the harness lost to a single API call to
  the model it wraps.

Both died at the same point — **building before knowing whether anyone wanted
it.** These criteria exist to move that discovery to the front.

## The operating constraint

Not "no human contact". The line is:

| Willing | Unwilling |
|---|---|
| understanding a problem in depth | convincing |
| supporting someone using the thing | pitching |
| digging into a customer's actual issue | persuading, closing |

**The product does the selling. You do the problem-solving.** Everything below
follows from that.

---

## The nine criteria

A candidate must survive **all nine**. One failure kills it.

### D1 — Money must already be moving
At least ~3 products people currently pay for in or adjacent to the space.
Evidence: paid competitors, marketplace revenue, or the same job posted
repeatedly. *"No competitors" means no market, not blue ocean.*
→ Would have killed tirramind.

### D2 — Not dominated
**Threshold: kill only if dominated.** Fatal = one or two well-funded players
whose *core* product this is, or a platform giving it away as a loss-leader.
A fragmented market — 20-50 small paid tools, no dominant player — **stays in**,
and is usually the ideal shape.
→ Would have killed AWOS.

### D3 — Distribution must not require persuasion
Discovery must come from a channel that delivers pre-qualified strangers. Kill if
the only route to a customer is you convincing them — enterprise sales, demos,
cold outreach, partnership deals.

**Constrained by D8.** Since single-platform dependency is an absolute no-go, the
surviving channels are ones nobody can revoke: search and content, documentation
that ranks, word of mouth, being the obvious answer in a forum thread. A
marketplace listing may be *a* channel but never the only one, and never the
thing billing runs through.

**Known cost, accepted:** this is slower than marketplace-led distribution and
puts the acquisition problem on you. It buys durability — nothing can switch you
off — at the price of a longer ramp. Budget for that rather than being surprised
by it.
→ Would have killed the AWOS pilot plan (cold outreach in Discord/Twitter).

### D4 — The core bet must be falsifiable before building
There must exist an experiment costing under a week that could prove the central
assumption wrong. If the only way to test it is to build the whole thing, kill it.
→ Would have killed tirramind in week one.

### D5 — Shippable in 8 weeks
A version a stranger could pay for, live within 8 weeks. **Subject to the week-3
gate below** — the box is not permission to build for 8 weeks unchecked.
→ Would have killed both.

### D6 — Support is fine; selling is not
No cap on support depth or difficulty. The only gate is whether *acquiring* a
customer requires persuasion. Answering "how do I do X" is good. "Let me explain
why this is worth ₹2,000" is disqualifying.

### D7 — Unit economics known and positive at small scale
What does one customer cost per month to serve? Anything data- or LLM-heavy must
have this answered before building, not after. Unknown counts as failed.
→ AWOS as a product would have resold tokens at a markup while the labs sell direct.

### D8 — Hard no-gos
Any one of these kills the candidate outright:

**Chosen deliberately as absolutes 2026-08-31, and reaffirmed after an
assistant attempt to soften them was withdrawn as mistaken.** No exceptions.

| Risk | Line |
|---|---|
| **ToS-violating scraping** | out entirely, even where enforcement is rare. Official APIs, licensed data and open data only. |
| **Regulated advice** | financial, legal, medical, tax advice where being wrong carries liability |
| **Single-platform dependency** | out entirely. No business that lives inside one marketplace or on one irreplaceable API. You own billing and the customer relationship, always. |
| **Handling personal data** | out entirely, including customer emails. The payment processor holds identity; you hold an opaque customer id and a licence key, and nothing else. |

### What these four converge on

They do not empty the field. They select an archetype:

> **Sell software or data. Do not run a hosted service that holds people's stuff.**

Shapes that survive all four:

- **Licence-key software** — desktop, CLI, or self-hosted. Processor takes the
  payment and holds the email; you ship a key against an opaque id.
- **API products with opaque keys** — no account, no profile, key maps to plan.
- **Data products** — licensed or open sources, cleaned and packaged, sold as
  files or an endpoint. No accounts required at all.
- **Self-hosted / on-prem tools** — the customer runs it, their data never
  reaches you.

Shapes that die: conventional SaaS with user accounts, marketplace-locked
businesses (Shopify-app-only, extension-only), ToS-dependent scraping, and
regulated-advice domains.

**These constraints are coherent with the operating constraint, not in tension
with it.** No PII means no DPDP/GDPR duties, no deletion requests, no breach
liability — all of which are support work requiring judgment. Self-hosted also
sidesteps D7 entirely, since the customer pays their own infrastructure.

→ tirramind edged into two of these: a sanctions match published at 0.95
confidence against a named entity, and merchant-of-record duties for a product
that could not deliver.

### D9 — Maintenance must stay survivable
Some breakage is the moat — scraper rot and API churn are what keep funded teams
out. Too much is an unpaid night job. Judge per candidate against the revenue it
would bring; kill anything that would need daily intervention to stay alive.

---

## The week-3 gate — mandatory

**By week 3 the riskiest assumption must have been tested against reality.**
Not the product finished. The central bet proven survivable.

- data product → the data is obtainable *and* someone has shown intent to pay
- tool → a stranger found it and used it unaided

**If the bet has not been tested by week 3, the candidate is killed — regardless
of how well the build is going.** "Going well" is not evidence; it was true in
month four of both previous projects.

## Ranking what survives

```
score  =  money already moving  ×  dissatisfaction  ×  annoying-to-build
```

**Annoying is a positive weight.** Regulatory complexity, undocumented APIs,
tedious maintenance, unglamorous domains — that is what keeps funded teams out.
It is the closest thing to a moat available to one person.

Bias toward small, boring, and regional. A niche worth ₹8-20 lakh/year is
invisible to anyone with investors and a living for one person. That asymmetry,
plus an Indian cost base, is the most structural advantage available here — and
the one never deliberately used.

## Before looking at any candidate

Write down what evidence would make you drop it. Then look.
That single ordering is the difference between this and the last two attempts.
