---
title: "Venture Scan Protocol"
tags:
  - doc/strategy
  - topic/decision
---

# Venture Scan Protocol

> Companion to [[venture_disqualifying_criteria]]. That file says what kills a
> candidate; this one says **where the evidence comes from**, so the scan cannot
> quietly become a list of things that merely sounded good.

## The rule that makes this a scan and not a brainstorm

**Candidates are generated from evidence sources, then filtered. They are not
generated from imagination and then justified.**

If a candidate cannot be traced to a specific source below, it does not enter the
list. Note the origin of every candidate; "seemed plausible" is not an origin.

## Evidence source per criterion

| # | Criterion | Evidence | Where |
|---|---|---|---|
| D1 | money moving | ≥3 products with public prices; review counts as a proxy for volume | pricing pages, marketplace listings, repeat job postings |
| D2 | not dominated | competitor count and concentration; is this anyone's flagship or a platform freebie | category listings, funding records |
| D3 | no persuasion needed | **does a competitor let a stranger buy without contacting anyone** — self-serve checkout vs "Contact sales" | competitor sign-up flow, fetched directly |
| D4 | falsifiable < 1 week | can the core input be obtained and tested in a day | API docs, sample data, rate limits |
| D5 | 8-week v1 | scope estimate against the archetype | judgement, recorded with reasoning |
| D6 | support ok, selling not | same signal as D3 — demo-gated means disqualified | competitor onboarding |
| D7 | unit economics | cost of the underlying input per customer per month | upstream API/data pricing pages |
| D8 | four no-gos | source terms of service; is PII involved; is there a second source for the same data | ToS pages, API docs |
| D9 | maintenance | how often the upstream format or endpoint changes | changelogs, issue trackers |

**D3/D6 note:** "Contact sales" on every competitor is a *positive* market signal
(money is moving) but an automatic disqualifier for this operator. A category
where everyone gates behind demos cannot be entered without selling.

## Candidate origins that count

1. Existing paid products with poor review sentiment — money moving *and*
   dissatisfaction in one artifact. Strongest single source.
2. Repeated public complaints — forum threads, issue trackers, StackOverflow
   questions asked the same way for years.
3. Jobs posted repeatedly for the same manual task.
4. Official data sources that are legal to use but painful to consume.
5. Self-hosted demand — people publicly asking for a non-cloud alternative.

## Ranking

```
score = money already moving  ×  dissatisfaction  ×  annoying-to-build
```

Annoying is a **positive** weight. Bias toward small, boring, regional.

## Kill log

Every candidate examined gets one line: what it was, which criterion killed it,
and the evidence. Including the ones killed fast — the log is what stops the same
idea being reconsidered in three weeks because it "felt different that time".

| Candidate | Origin | Killed by | Evidence |
|---|---|---|---|
| *(to be filled during the scan)* | | | |

## Status

**Not yet run.** Blocked 2026-08-31: the session's web-search budget was
exhausted (200/200), and search is what generates candidates. Direct URL fetching
still works, so verification is available but discovery is not.

Running it with discovery unavailable would mean generating candidates from
memory and then confirming them — which is the idea-first ordering these
documents exist to prevent. Deferred rather than degraded.
