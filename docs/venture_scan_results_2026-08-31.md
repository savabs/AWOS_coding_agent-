---
title: "Venture Scan Results 2026-08-31"
tags:
  - doc/strategy
  - topic/decision
---

# Venture Scan — run 2026-08-31

> Execution of [[venture_scan_protocol]] against [[venture_disqualifying_criteria]].
> Immutable record of what was examined and what killed it.
>
> **Result: 8 candidates examined, 7 killed, 1 shape survives — conditionally,
> and it is a shape, not yet a candidate.** No build is authorised by this scan.

## Method, and where it was weaker than the protocol asks

Discovery ran against artifact sources directly (GitHub API, StackExchange API,
WordPress.org plugin API, vendor pricing pages) rather than search summaries,
because generic search returns SEO listicles rather than evidence.

**Two of the five permitted candidate origins were under-covered. This is a
known defect in this run, not a clean sweep:**

| Origin | Coverage | Why |
|---|---|---|
| 1 — paid products, poor review sentiment | **partial** | WordPress.org API gave install counts + ratings. Capterra/G2 not directly fetchable; their data reached me only via search summaries. |
| 2 — repeated public complaints | **partial** | StackExchange API worked well. **reddit.com is blocked to the fetcher entirely** — a large share of small-business and self-hosted complaint traffic was unreachable. |
| 3 — repeatedly posted jobs | **not covered** | No job board proved queryable. Searches returned listing pages, not evidence of the same manual task recurring. |
| 4 — painful official data sources | **good** | GitHub structural queries surfaced domains rather than confirming pre-picked ones. |
| 5 — self-hosted demand | **weak** | Depends heavily on reddit, which is blocked. |

**Consequence:** absence of a candidate from this list is not evidence that none
exists. The kills below are sound; the coverage is not complete.

A second caveat on evidence quality: figures marked *(2nd-hand)* come from
comparison sites or search summaries, not the vendor's own page. They are good
enough to kill a candidate, not good enough to commit to one.

## Kill log

| # | Candidate | Origin | Killed by | Evidence |
|---|---|---|---|---|
| K1 | EDI translation / VAN (X12, EDIFACT) | 2: SO `edi` 490 q, `edifact` 119 q; 1: many paid Capterra vendors | **D3 / D6** | TrueCommerce routes to "Request a demo", pricing "customized"; SPS Commerce and Cleo publish no price either. Every route to a customer is a sales conversation. |
| K2 | E-invoicing network transmission (Peppol access point) | 4: EN16931 GitHub cluster; 1: Storecove, ecosio | **D8 twice, + D7** | (a) Single-platform dependency: an AP must be an accredited OpenPeppol member — revocable governance. (b) Personal data: invoices carrying customer names/addresses pass through your infrastructure. (c) €1,025 sign-up + €1,800/yr membership *(1-50 staff)* plus expected ISO 27001, all before customer one. |
| K3 | E-invoicing format validation / conversion, self-hosted licence | 4: `itplr-kosit/validator` 172★ 25 open issues, pushed 2026-08; `ConnectingEurope/eInvoicing-EN16931` 254★ 37 oi | **D2** | The authoritative validators are published free by the German government (KoSIT) and the European Commission (`ISAITB/xml-validator`, DIGIT). A platform giving the core away as a loss-leader is explicitly fatal. |
| K4 | ISO 20022 / SEPA payment message tooling | 4: `iso20022.js` 394★, `prowide-iso20022` 236★ | **D3 / D6**, and weak D1 signal | Buyers are banks and PSPs; acquisition is relationship-led. Dissatisfaction signal is near-absent: SO `sepa` 9 questions, `iso20022` 0. |
| K5 | HL7 / FHIR healthcare interop | 2: SO `fhir` 1063 q, `hl7` 514 q — **the highest pain found** | **D8 + D3 / D6** | Health records are the most regulated personal-data category there is. Buyers are hospitals and health-IT vendors — demo-gated procurement. Highest pain in the sweep, and unreachable. |
| K6 | GTFS / transit feed tooling | 2: SO `gtfs` 277 q; 4 | **D3 / D6 + D1** | Buyers are transit agencies — public procurement, the purest form of persuasion-led sales. Core tooling already free (MobilityData, `google/transitfeed`). |
| K7 | GIS / shapefile tooling | 2: SO `shapefile` 2017 q — **highest question volume found** | **D2** | GDAL and QGIS are free and excellent; Esri dominates the paid end. Volume of pain does not survive a dominated market. |
| K8 | WordPress plugin niche, licence-key | 1: WP.org API — high installs, sub-80% ratings (e.g. `uk-cookie-consent` 80k installs / 72% / 203 ratings; `wp-import-export-lite` 40k / 80% / 113; `mollie-payments-for-woocommerce` 100k / 70% / 70) | **not killed — not finished** | Fragmented (D2 favourable) and self-serve by default (D3/D6 favourable). But every niche has 5-20 incumbents, the strongest dissatisfaction signal found sits in cookie-consent which is a **D8 personal-data niche**, and WP.org-directory-only distribution strains D3. Deferred, needs its own pass. Recorded so it is not silently re-discovered. |

## The one shape that survived

**A niche data product built on an official, openly-licensed source, sold
self-serve with public pricing and an opaque API key.**

This is the archetype the four no-gos already predicted. What the scan adds is
that the shape is *commercially demonstrated*, not merely permitted:

| Product | Price | Self-serve? | Source |
|---|---|---|---|
| GovCon API | $0 trial (14d, 25 req/day), **$19/mo**, **$39/mo**, cancel anytime | **Yes** — Subscribe button, no sales contact | SAM.gov, FPDS, USAspending.gov |
| PermitStack | free (100 req/day, no card), **$19 / $29 / $49**, custom enterprise | **Yes** — no sales call *(2nd-hand)* | US building permits |
| Instrumentl | **$299–499/mo** *(2nd-hand)* | — | Grants.gov |
| Jorpex | from **$49/mo** *(2nd-hand)* | — | public tenders |
| Tendium / TenderLake | from **€300/mo** / **£200/mo** *(2nd-hand)* | — | EU / UK tenders |

Against the nine criteria:

- **D1 ✅** Multiple products at published prices across several jurisdictions.
- **D2 ✅** Genuinely fragmented — no dominant player per jurisdiction or vertical. This is the shape D2 calls ideal.
- **D3 / D6 ✅** Self-serve checkout is *demonstrated* in this category, which is exactly what EDI and ISO 20022 failed.
- **D4 ✅** Falsifiable in days: pull the source and see whether it is obtainable, complete and clean.
- **D5 ✅** One dataset, one API, documentation — 8 weeks is credible.
- **D7 ✅** Best economics in the scan. Source data free or openly licensed; marginal cost is hosting. No token resale, no upstream per-customer fee.
- **D9 ⚠️** Format churn is real and is the moat. TED's eSender survey records developer complaints about breaking changes, backwards compatibility and documentation quality; API v2 support ended 30 Sep 2025. Survivable, but must be judged per dataset.

### D8 is a binding constraint on which dataset, not a general pass

Two hard limits, both evidenced:

1. **Open licence does not mean open personal data.** The UK Open Government
   Licence explicitly does not cover personal data; re-users must satisfy GDPR
   Article 5 independently. Since handling personal data is an absolute no-go,
   **any dataset carrying named individuals is out** — which removes procurement
   notices with contact names, most business-registry data, and permit records
   naming owners, unless names are provably strippable at ingest.
2. **No source without an official feed.** Canadian tender data is scattered
   across CanadaBuys, Québec's SEAO and separate Ontario / BC / Alberta /
   Saskatchewan portals with *no public data feeds* — aggregating it would
   require scraping, which is an absolute no-go. **The fragmentation that makes a
   market attractive is often the same fact that makes it illegal to enter.**

### Why no specific dataset is named here

Naming one now would be the idea-first ordering this protocol exists to prevent.
The candidates that reached the surface — recalls (RecallStream already runs a
cross-agency feed over openFDA, CPSC, NHTSA, EU Safety Gate, RappelConso, Health
Canada, UK OPSS), tariff and HS-code data (Interzoid, Easyship, TariffsChart,
GlobalTariffRates), weather and aviation (**dominated** — The Weather Company,
aviationstack; D2 kill) — were surfaced by verification searches, not by a
discovery pass built to find them.

Two further flags for whoever picks the dataset:
- **Tariff/duty data sits adjacent to regulated advice.** Supplying the rate is
  data; being relied on for classification is where being wrong carries penalty
  liability. That line needs deciding before, not after.
- Every niche examined already had someone in it. That is D1 evidence, not a
  reason to stop — but it means the wedge must be jurisdiction or vertical depth,
  and that must be established by evidence too.

## What this scan does and does not authorise

**Does:** it closes seven candidates with evidence, and it establishes that the
surviving archetype has paying customers at public prices — which was previously
an assumption.

**Does not:** authorise a build. The shape has survived; no candidate has. The
week-3 gate binds from the day a dataset is chosen: *the data is obtainable* **and**
*someone has shown intent to pay*. The first half is a day's work. The second is
the half that killed both previous projects, and the half no amount of scanning
can substitute for.

## Next step

**Run 2026-08-31 as pass 2 — see [[venture_scan_results_2026-08-31_pass2]].**
Origins 1 and 3 are now covered and produced no qualifying candidate. The shape
below still has nothing under it, for structural reasons recorded there.

## Related
- [[venture_scan_protocol]] · [[venture_disqualifying_criteria]] · [[checkpoint_2026-08-31_bar_test_and_pivot]]
