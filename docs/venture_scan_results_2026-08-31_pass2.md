---
title: "Venture Scan Pass 2 2026-08-31"
tags:
  - doc/strategy
  - topic/decision
---

# Venture Scan — pass 2, scoped to the surviving shape

> Follow-on to [[venture_scan_results_2026-08-31]]. Pass 1 killed 7 of 8
> candidates and left one *shape*: a niche data product on an official
> openly-licensed source, sold self-serve with an opaque key. Pass 2 was scoped
> to find a dataset to put under that shape, aimed at origins 1 and 3.
>
> **Result: origins 1 and 3 are now covered. Neither produced a qualifying
> candidate. The shape still has nothing under it — and the reason is
> structural, not a search failure.**

## The sourcing gap is closed

Pass 1 was blind in two origins because `reddit.com` is unreachable. Replacements
found, all programmatic:

| Source | What it covers | Verdict |
|---|---|---|
| **Open Data StackExchange** (API) | 6,284 questions, 2,092 unanswered, `data-request` tag alone 3,255 — people stating datasets they cannot obtain | Works, but see the calibration below |
| **Freelancer.com** projects API | Origin 3 — live paid jobs with budgets attached | Works. Origin 3 is now genuinely covered |
| Software Recommendations SE, `discuss.okfn.org` | Origins 2 and 5 | Reachable, not yet mined |
| Upwork RSS | Origin 3 | **Dead** — HTTP 410 |

## Calibration: Open Data SE measures desire, not money

Over half of Open Data SE is people asking where to get a dataset. It is
tempting to read that as demand. It is not.

Language counted across the 597 highest-scoring questions from 2022 onward:

| Signal | Count |
|---|---|
| Commercial intent (*commercial use, for my company/startup, our product, licence*) | **4** |
| Free / academic intent (*free, academic, research, thesis, student, training data*) | **26** |

**A 6.5:1 ratio against paying demand.** The corpus is dominated by researchers
and students who want free training data. It is a valid origin-2 source for
*pain* and an invalid origin-1 source for *money moving*. Recorded so that
"thousands of people ask for this dataset" is never again mistaken for a market.

## Origin 3, covered, and empty

387 unique live projects pulled across 8 task-shaped queries.

- Keyword spread: data entry 31, lead-gen 23, excel 22, database 21, list 16
- **37 of 387 are explicitly lead/email/contact/phone work** — personal data, D8 kill on sight
- Typical budgets: **INR 100–400**, AUD 10–30
- No task appeared repeatedly in a productisable, non-PII form

This is a spot market for one-off cheap labour, not evidence of the same manual
task being paid for over and over. **Origin 3 yields no candidate.**

*Limitation:* this is one cross-section of live projects, not a longitudinal
study. It cannot fully establish that nothing recurs over time — only that
nothing recurs visibly right now.

## Candidates examined and killed in pass 2

| Candidate | Origin | Killed by | Evidence |
|---|---|---|---|
| Vehicle make/model/**specification** data | 2 — the only theme asked repeatedly across years on Open Data SE: 2016 (1,516 v), 2023-05, 2023-08 (745 v), 2023-10 (796 v), 2024-02 | **D8 or D3/D6 — no third option** | There is no official openly-licensed source. DataOne "works directly with OEMs to manage build data licensing"; JD Power/ChromeData hold a proprietary 30-year catalogue; JATO licenses 1,000 datapoints per car. Entry requires either OEM licensing deals (persuasion — D3/D6) or scraping manufacturer sites (absolute no-go — D8). **The reason this data is valuable is the same reason it is closed.** |
| Indian company intelligence (MCA filings) | 4 + regional bias; money demonstrably moving (Probe42: 25 lakh companies, 740+ sources; Tofler) | **D8** | Filings carry **director data** — named individuals — plus court and tribunal records. The most commercially proven regional thread found, and it is squarely a personal-data business. |
| GTIN / barcode product database | 2 — Open Data SE, 1,460 v | **D2** | GS1 *is* the registry. GS1 US Data Hub from $500/yr. Competing with the issuing authority on its own identifiers. |
| openFDA mirror / reliability product | 2 — 928 v, 2025-03 | **D1** | A temporary suspension of dataset updates (2025-03-31) is an incident, not a market. No evidence of anyone paying for a mirror. |

## The structural finding

Across both passes, the datasets where money is *demonstrably* moving fall into
three groups, and each is closed to this operator for a different reason:

1. **Data about named individuals** — company directors, property owners, sales
   contacts, patients. Commercially the richest category by a distance. Absolute
   no-go on personal data.
2. **Data licensed from private originators** — OEM vehicle specs, GS1
   identifiers. Entry requires negotiated licensing, which is persuasion-led
   sales. D3/D6.
3. **Data the official body already gives away** — e-invoicing validators, NHTSA
   vPIC, recalls.gov, Grants.gov. D2.

What is left — openly-licensed, non-personal, officially fed — is largely the
data people want **free**, which is exactly what the 6.5:1 ratio measures.

**This does not kill the archetype.** GovCon API ($19/$39, self-serve, on
SAM.gov/FPDS/USAspending) and PermitStack (free/$19/$29/$49) are real businesses
occupying precisely this space, so the intersection is not empty. But it is far
narrower than pass 1 implied, and the binding constraint is no longer *finding a
painful dataset* — those are abundant. It is **finding a buyer who pays for
non-personal open data**, which is rare enough that it must be established first.

One genuinely positive result worth keeping: **GODL-India explicitly permits
commercial reuse, redistribution and derivative products** with attribution. The
regional angle the criteria call for has a clean legal basis, whenever a
non-personal dataset and a paying buyer are found on it.

## What pass 2 changes about what to do next

More scanning will not resolve this. The question that remains — *will anyone pay
for non-personal open data* — is not answerable from artifacts, because the
artifacts that would answer it are the ones that do not exist until someone
tries to sell something.

Continuing to scan from here would be the documented failure pattern of both
previous projects, in a new costume: researching instead of testing demand. Two
projects and roughly nine months died that way.

**The next action is a demand test, not a third pass.** Concretely: pick one
non-personal, officially-fed dataset, put a price and a payment link in front of
strangers who plausibly need it, and see whether anyone pays before any
collection or cleaning work is done. That is the week-3 gate, run at week zero —
which is the only ordering that has never been tried here.

## Amendment 2026-08-31 — what the criteria change reopens

The personal-data no-go was replaced after this pass ran
([[venture_disqualifying_criteria]], amendment of the same date), on the evidence
in this document. Re-scored against the amended rules:

| Candidate | Was killed by | Now |
|---|---|---|
| **Indian company intelligence (MCA)** | D8 — director data | **Reopened, then KILLED on the D2/D3 check the same day — see below.** Personal data was never the binding constraint. |
| Canadian tender aggregation | D8 — no feed, assumed scraping | **Reopened pending a terms check.** Absence of an API was wrongly read as prohibition. |
| EDI / ISO 20022 | D3/D6 — demo-gated | **Reopened as hypotheses.** The demo-gate axiom is demoted; a self-serve, publicly-priced entrant may be the wedge rather than a contradiction. Test once. |
| Property / land records | D8 — owner names | **Conditionally reopened** where published under statutory obligation. |
| Peppol transmission | D8 ×2 + D7 | **Still dead.** The PII half lifts; single-platform dependency and €2,825/yr + ISO 27001 before customer one do not. |
| HL7 / FHIR | D8 + D3/D6 | **Still dead.** Health data remains special-category and out; buyers remain demo-gated. |
| Vehicle specs | D8 or D3/D6 | **Still dead.** Killed by OEM licensing and scraping, neither of which the amendment touches. |

**What does not change:** the recommendation to stop scanning. The amendment
widens the field; it does not answer whether anyone pays. The difference is that
there is now a concrete candidate to point a demand test at rather than a shape.


## D2/D3 check on Indian company intelligence — result: KILL

Run 2026-08-31, immediately after the amendment reopened it. It did not survive
the hour.

**D3 — passes, and instructively.** Tofler sells self-serve: **₹15,000 + GST**
(Pro, 40 credits) and **₹28,000 + GST** (Growth, 100 credits), with a **"Buy
Subscription"** button and immediate signup. Enterprise from ₹1.5 lakh is
quote-based. Probe42, by contrast, is "Contact Us" across all three of its
products. So a stranger *can* buy in this category without talking to anyone.

**D2 — ambiguous, and it stopped mattering.** ~17 named players (Zauba's
competitor set alone lists Dun & Bradstreet, InstaFinancials, Probe and 13 more),
Probe42 funded at only $7.28M over 8 rounds. That reads fragmented rather than
dominated. But the candidate died on a criterion nobody was looking at.

### What actually killed it: the data splits into two layers

| Layer | Contents | Status |
|---|---|---|
| **Open** | CIN, company name, status, class, category, authorised and paid-up capital, registration date, state, RoC, principal business activity, registered address | **Free.** data.gov.in Company Master Data, ~3.67M companies, released under **NDSAP/GODL**, bulk ZIP + API, last updated 2026-07-22. **No directors, no financials, no filings.** |
| **Valuable** | 5-year financials, directors, shareholders, charges, MCA filings | **Defended.** Behind MCA21 at ₹50–500 per document. MCA's terms prohibit bulk automated access and resale as a data product without authorisation; `mca.gov.in` returns **403 to an ordinary browser user-agent**, so this is actively enforced, not merely stated. |

Tofler's ₹15,000 plan sells precisely the second layer.

**Kill, on three independent grounds:**

1. **D8 — ToS-violating scraping. Unamended and absolute.** The valuable layer is
   obtainable at scale only by automated access MCA prohibits. The lawful
   alternative is negotiated authorisation — which is D3/D6 persuasion.
2. **D2 — the open layer is given away by the government itself.** Free, bulk,
   API, current. "A platform giving it away as a loss-leader" is explicitly fatal.
3. **D7 — the economics are absurd anyway.** 3.67M companies × ₹50 minimum is
   ₹18+ crore for a single document each.

### The error worth recording

The amendment reopened this candidate on personal-data grounds and it was called
"the strongest live lead" **without re-running the other eight criteria**. Personal
data was never what was blocking it. Changing one criterion does not reopen a
candidate — it only removes one of its kills, and every other criterion must be
re-checked before the candidate is called live. The hour-long check existed
precisely to catch this, and did.

### The generalisation, which is the real finding

**The valuable layer of official data is usually defended; the layer that is
openly licensed is usually the low-value one.** GovCon API and PermitStack work
because US federal data is genuinely open *at the valuable layer* — SAM.gov, FPDS
and USAspending publish full-fidelity award and contract data free. That is a
jurisdictional fact about the US, not a general property of government data.

**This predicts that the surviving shape is much more jurisdiction-dependent than
pass 2 concluded, and that the Indian regional angle — the one structural
advantage the criteria most want to use — is the *weakest* place to apply it.**
Any third pass should test open-at-the-valuable-layer first and jurisdiction
second, rather than the reverse.

### One positive result

Tofler is a self-serve, publicly-priced entrant in a category where its
best-funded competitor is contact-sales only. That is a working example of the
pattern the demo-gate demotion was betting on. **The EDI and ISO 20022 hypotheses
are strengthened by this kill, not weakened by it.**


## EDI demo-gate test — 2026-08-31. Axiom falsified; candidate still dies

Pre-registered before looking: **H1** — at least one credible EDI vendor lets a
stranger buy at a published price without contacting anyone. **H0 (the axiom)** —
a category where competitors gate behind demos cannot be entered without selling.

### H1 confirmed. The axiom is false.

| Vendor | Published price | Self-serve? |
|---|---|---|
| **Stedi** | $0.30 → $0.08 per eligibility check; $0.30 → $0.10 per claim; ERAs $0.20 → $0.08. **Pay-as-you-go, no monthly minimum** | **Yes** — "Get started" / "Sign up free", no sales contact |
| **Orderful** | Labels **$99/mo**; Web EDI **$189/mo per trading partner**; Integrated from **$399/mo** | Partial — self-serve at Labels; Integrated and Enterprise route to `/book-a-demo` |
| **SignalEDI** | from **$199/mo**, published before any sales call | Yes |

**Why pass 1 got this wrong: sampling error.** It tested TrueCommerce, SPS
Commerce and Cleo — the *legacy incumbents* — found "Request a demo" everywhere,
and generalised to the category. The entrants were never sampled. Demo-gating by
incumbents is a D1 signal that money is moving; it says nothing about whether
self-serve entry is possible.

### The candidate still fails, on criteria pass 1 never reached

**D2 — dominated. Decisively.**
- **SPS Commerce**: ~$445M revenue, public, EDI is its core product, **14
  acquisitions** including Carbon6 at $210M. Morgan Stanley has it "poised to
  capture EDI market share" — actively consolidating.
- **Stedi**: **$142M raised, $50M Series C**, investors including Addition,
  Stripe, Ribbit, USV, First Round, Bloomberg Beta.

The developer-first, self-serve, publicly-priced corner — *the exact position a
new entrant would take* — is already held by a company with $142M and Stripe on
the cap table. D2's "one or two well-funded players whose core product this is"
describes this precisely.

**D9 — maintenance. Independently fatal for one person.** The standard defines
only the outer shape; **each trading partner writes its own rulebook on top**, and
missing one line item gets shipments rejected. Onboarding runs **four days to six
weeks per partner**, and each relationship is "closer to a separate mini-project."
That is unbounded, per-customer, ongoing work — the unpaid night job D9 exists to
refuse.

**D5 — 8 weeks is not credible** once AS2/VAN connectivity, X12 translation and
even a handful of partner maps are required.

**One more thing worth noticing:** Stedi's published pricing is entirely
*healthcare* EDI — eligibility, claims, ERAs, MBI lookups. The best-proven
self-serve position in EDI runs on PHI, which is special-category data and
absolutely out. The corner that most works is one that cannot be entered here
regardless.

### What this test actually bought

The candidate died, but the rule that killed it in pass 1 was wrong, and that
correction is durable:

1. **Delete the axiom.** "Everyone gates behind demos" is not a disqualifier and
   must never again kill a candidate on its own.
2. **When testing D3, sample the newest entrants, never the incumbents.**
   Incumbent demo-gating is a D1 positive.
3. **But self-serve existing is not itself an opportunity.** If a category has a
   self-serve entrant, someone already took the wedge — and D2 then asks how well
   funded they are. In EDI: $142M.

**The refined rule: the demo-gate wedge is only open in the window between "nobody
has done it" and "someone well-funded has done it," and that window is short.**
Absence of a self-serve entrant is the signal worth chasing — not its presence.

**ISO 20022 is not resolved by this.** It was killed by the same bad axiom and
needs its own test, but the prior has changed: check for a self-serve entrant, and
if one exists, check its funding before anything else.


## K8 — WordPress plugin niche. Run 2026-08-31. Result: KILL

The last candidate standing, and the only one deferred rather than killed. It fits
the "compete on execution in markets too small for funded players" thesis exactly,
so it got a full run: 1,000 most-popular plugins plus 2,000 rows across 20
commercial niches, from the WordPress.org API.

### D1 passes, D3/D5/D7 pass easily

Money plainly moves in WP plugins, self-serve licence-key checkout is the native
motion, 8 weeks is credible, and unit economics are excellent — no per-customer
cost. On paper this is the best-shaped candidate in the whole scan.

### It dies on the absence of dissatisfaction

The premise was origin 1: *money moving **and** dissatisfaction in one artifact.*
The money is there. **The dissatisfaction is not.**

Rating distribution across **1,261 independent niche plugins** (≥5k installs, ≥10
ratings):

| p10 | p25 | median | share ≤80% | share ≤70% |
|---|---|---|---|---|
| 82% | 90% | **94%** | **9%** | **4%** |

Median rating is 92–98% in *every* one of the 20 niches sampled. These are
markets with competent, well-liked incumbents.

**Star ratings on WP.org are solicitation-inflated, so this was checked against a
metric that is harder to game — unresolved support threads.** It agrees. The
worst independent backlogs in the entire sample are 8–60 threads: `photo-gallery`
16 threads at 100k installs, `events-manager` 48 at 60k, `tutor` 60 at 100k. A
plugin serving 100,000 sites with 16 open threads is a well-run product, not a
neglected one.

### And where dissatisfaction does exist, it is unmonetisable

Of the 83 popular plugins rated ≤80%, **59 — 71% — are free first-party
integrations published by large platforms**: Meta, Google, Stripe, PayPal,
Automattic, SiteGround, Zapier, Mailchimp, Pinterest, Trustpilot, Square,
Printful. They are badly rated precisely because they are strategic giveaways
nobody's revenue depends on.

**You cannot sell against a free official Stripe plugin.** Merchants take the
first-party one for support and liability reasons regardless of quality. This is
D2's "platform giving it away as a loss-leader," and it accounts for
three-quarters of all the dissatisfaction in the ecosystem.

The independent remainder is mostly single-purpose utilities that are features
rather than businesses (`hide-title`, `title-remover`, `regenerate-thumbnails`),
plugins that are simply abandoned (`hide-title` 2019, `wp-edit` 2018,
`title-remover` 2021), or host-bundled freebies (`hostinger`, `breeze`).

### Honest limits on this kill

- The per-niche concentration measure was **contaminated and is not relied on** —
  WP.org search is keyword-matching, so "security" returned Elementor and
  "migration" returned Yoast. Those numbers are discarded.
- 20 niches were sampled by keyword; unsampled niches exist.
- What the kill rests on is the rating distribution and the support-backlog data,
  which are independent of each other and agree.

### What it means

K8 was the direct test of *"play the same game, better."* WordPress is the
friendliest possible ground for it — fragmented, self-serve, small niches, no
enterprise sales, execution-driven. And the finding is that **execution is
already the norm there.** The median competitor is rated 94% and answers its
support threads. "Better" is not an available wedge against people who are
already good.

## Related
- [[venture_scan_results_2026-08-31]] · [[venture_scan_protocol]] · [[venture_disqualifying_criteria]] · [[build_first_failure_pattern]]
