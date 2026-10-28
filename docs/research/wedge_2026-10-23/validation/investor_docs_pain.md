# Validation: investor-portal document collection (K-1s, statements, capital calls)

Date: 2026-10-23. Candidate idea: a local agent that logs into LP / fund-admin /
custodian / broker / private-bank portals in the user's own browser each month
or quarter, downloads statements, capital-call and distribution notices and
K-1s / tax documents, then files and checks them.

**Overall verdict: MEDIUM. The pain is real but the market already has well-funded vendors.
Not a fast self-serve consumer market.** Family offices clearly feel the pain: "thousands of
documents" and "everything is manual". They already pay $40k–$100k+ a year for
Canoe / Arch / Masttro / Addepar. A local, no-credential-custody agent has
a plausible opening at the low end: small single-family offices (SFOs), multi-family offices (MFOs), RIAs and CPA firms for
whom $40k "is absurd". The consumer version (HNW individuals at tax time) and the
Indian AIF/PMS version have weak evidence.

## Method and source limits

- **Reddit:** reddit.com, old.reddit, `.json` and `site:` searches were all blocked
  (403 / crawler block). The read-only Arctic Shift archive API worked for part of the time.
  I pulled about 300 r/FamilyOffice posts and 725 comments from 2021–2026, plus title searches in
  r/fatFIRE, r/Accounting and r/tax. Searches of r/IndiaInvestments, r/IndiaTax and r/Bogleheads
  all timed out, so **Indian Reddit is not covered**.
- **Bogleheads forum:** fetches were blocked (403). Its content comes only through search-engine summaries
  of the threads, so I have no exact post dates and the quotes are close paraphrases.
- **Hacker News:** full access through the Algolia API.
- **Wall Street Oasis, CAclubindia and ValuePickr:** no relevant threads were indexed.
- **Review sites:** G2 has 0 Canoe reviews and 5 Addepar reviews. The rfp.wiki scores look
  AI-generated. Review evidence is thin.
- I did not sign up for or post anything.

Strength key: STRONG = 4+ independent non-vendor sources agree. MEDIUM = 2–3, or
mostly vendor-adjacent. WEAK = 0–1, or contradicted.

---

## 1. How painful is it? Volume, cadence, who does it

**Family offices / RIAs: STRONG (6 independent sources, 2 of them vendors)**

- "Our office spends a tremendous amount of time clicking through bank portals to
  download 1099s and re-upload them ... Same deal with K-1s. Literally thousands of
  documents." This SFO staffer also wanted one tool covering "monthly bank statements and other
  liquid securities documents" as well as K-1s.
  — r/FamilyOffice, 2023-01-24, https://reddit.com/r/familyoffice/comments/10k857o/digital_tax_admin_solutions/
- "The immediate needs are on the private investment side where everything is
  manual today." This SFO was evaluating Addepar, Arch, Masttro, Canoe and SEI Archway.
  — r/FamilyOffice, 2026-04-23, https://reddit.com/r/familyoffice/comments/1ssw8t8/document_collection_and_performance_reporting/
- A founder in the category, replying in the same thread (disclosed as a vendor), named four layers: collection
  ("email, portal, snail mail"), extraction, storage and reporting. "You'll still have a 15–20%
  exception tail ... 3–6 months [onboarding] is normal for this category."
  — 2026-10-08, https://reddit.com/r/familyoffice/comments/1ssw8t8/document_collection_and_performance_reporting/peo6ecv/
- A family office doing monthly closes across many entities: "consolidating 40+ entities and bank accounts ...
  Manual JE downloads/imports between systems work, but they're a reconciliation
  risk every close." — 2026-07-10, https://reddit.com/r/familyoffice/comments/1pl2pn2/family_office_accounting/owqllik/
- Vendor numbers. Canoe: "a typical ratio is one unique portal login for every 10
  commitments"; "an average of five and a half minutes to access a single document from
  a single portal" (2022-01-11, https://canoeintelligence.com/from-fragmentation-to-automation-streamlining-document-retrieval-for-alternatives-with-canoe-connect/).
  Masttro / industry: 20 funds produce 200+ documents a year in different formats, and investors
  usually have 10–14 days to wire after a capital-call notice
  (https://masttro.com/insights/capital-call-processing-for-family-offices).

Cadence: monthly (bank and custodian statements), quarterly (capital-account statements),
ad hoc (capital calls and distributions, which have 10–14-day deadlines) and a tax-season spike
(1099s from February to March, K-1s from March to September). Who does it: ops or accounting associates at
SFOs and MFOs, and CPA staff for HNW clients.

**The tax-season K-1 chase: STRONG, but the pain is lateness more than downloading
(7+ sources)**

- An LP got a $50k VC-fund K-1 "for tax year 2024 last week. In June," after
  filing an extension. "My CPA is charging me $850 for the amendment ... this is the
  second year in a row from the same fund." — r/fatFIRE, 2026-06-15, https://reddit.com/r/fatFIRE/comments/1u6ei91/
- "Somehow all of my K-1s came early this year. It's the first time in 20 years
  that I didn't have to file an extension!" — HN, 2025-04-07, https://news.ycombinator.com/item?id=43610006
- "Had one K-1 come late and my CPAs strongly advised filing for an extension." —
  HN, 2025-04-07, https://news.ycombinator.com/item?id=43615239
- AngelList: "They screwed up k-1s for me two years in a row, which cost me a ton
  of money with accountants to fix." — HN, 2021-09-28, https://news.ycombinator.com/item?id=28688672
- Bogleheads threads ("K1 delay and taxes", "Late K1", "K1 headache for real estate fund",
  https://www.bogleheads.org/forum/viewtopic.php?t=258383, t=278814, t=362106) agree that people extend every year.
  Several posters said they sold their funds to escape the K-1 hassle (search summaries; dates not retrieved).
- CPA side: "I can't count how many times we're in review and the client says
  something looks off and proceeds to pull out more 1099-B statements or K-1s." —
  r/Accounting, 2025-04-19, https://reddit.com/r/Accounting/comments/1k2j6d9/
  Another CPA described one client who "received 30 k-1's, five brokerage statements, and royalties from 85
  Oil and Gas properties." — r/Accounting, 2025-09-19, https://reddit.com/r/Accounting/comments/1nko5fk/

Implication: an agent **cannot make a K-1 arrive sooner**. What it can do is watch every portal,
alert you within hours of a posting, track expected vs. received documents, and hand the CPA a complete
folder. Arch already sells exactly this kind of expected-vs-received K-1 tracking. In r/Accounting, CPAs complain
far more about *entering* K-1 data (footnotes, state allocations) than about fetching the forms.

## 2. Which portals are worst

**WEAK–MEDIUM. Users rarely name specific portals.**

- MFA and key-person risk: "most portals now require multi-factor authentication
  (MFA)". "If an operations employee leaves the company without adjusting who the
  MFA requests are directed to, it can result in ... missed documents." — Canoe (vendor), 2022-01-11, URL above.
- "Every weird statement format becomes your problem." — category founder, 2026-10-08, URL above.
- Bank portals are named as the source of 1099s (r/FamilyOffice, 2023-01-24, above).
- India (AMC registrars): requests are tied to each PAN with an OTP to the Aadhaar-linked mobile. Joint folios are
  not aggregated, so each family member needs a separate request
  (https://www.share.market/buzz/learn/cas/, https://scripbox.com/mf/consolidated-mutual-fund-statement/; guides, not users).
  A TradingQnA user found that the KFintech gain-statement page "showed no investments" although the
  CAS worked (https://tradingqna.com/t/where-can-i-get-capital-gains-statements/118627).
- I found **no** user quotes on private-bank portals, AIF/PMS portals or session timeouts.
  That needs discovery calls.

## 3. What they have tried, and complaints

**MEDIUM–STRONG. Plenty of vendors, real complaints about price and implementation.**

| Tool | What users said | Source |
|---|---|---|
| Canoe | "Have heard Canoe in the industry but seem to overpromise & underdeliver" | r/FamilyOffice 2024-07-11 https://reddit.com/r/familyoffice/comments/1e0a26e/arch_vs_canoe/ |
| Canoe → Masttro | "We were prev on Canoe for privates and switched to Masttro for a fraction of the price + much better data qual" | 2026-05-13 .../1ssw8t8/.../olfbclt/ |
| Arch | "Arch seems like they've actually solved the problem of K-1s, capital calls / distributions"; "I prefer Arch myself." | 2024-07-11 (above); 2026-04-25 .../oi7cjtf/ |
| Addepar | "I HATE ADDEPAR"; "a poor piece of software for a very high price tag"; "Implementation took way longer than they quoted and requires dedicated staff" | 2025-12-13 .../1pl2pn2/.../ntqsp9h/; 2026-04-18 .../ogujjin/; 2026-02-26 .../1nl1s2o/.../o7jblom/ |
| Addepar sales | CTO of a $3B+ RIA: "Have spent hours with their sales team and have lost my patience on how many zeros will be on the quote." | 2024-08-09 https://reddit.com/r/familyoffice/comments/1eo2eyp/ |
| DIY | "Canoe for privates and bank APIs for public. Pull into excel -> power BI." | 2026-04-23 .../ohpb969/ |
| Masttro | "can be complex setup" | 2026-06-07 .../oq7frcw/ |
| Orca, Aleta, Asora, Asset Vantage, MyFO, SEI Archway | alternatives people named; Asora was called "a bit too narrow" | various 2023–2026 threads |
| G2 / reviews | Addepar: integration "can take longer time than expected", slow integration team (4.1/5, 5 reviews); Canoe: 0 G2 reviews | https://www.g2.com/products/addepar/reviews |

Coverage complaints were inferred but never stated outright. The founder's "15–20% exception tail" and
"weird statement format" comments point to gaps in GP-side API coverage. Gaps like that are exactly what a
browser agent can fill using the user's own logins. That is the opening. But **no user anywhere
complained about handing credentials to these vendors.** Credential custody is my hypothesis,
not something the evidence shows.

## 4. Willingness to pay; would they let a local agent use their logins?

**Pay: STRONG at the family-office tier (6+ price signals). Logins: WEAK (no direct evidence).**

- "you don't spend $100k for Addepar (or the cheaper but still absurd $40k for
  masttro)" — 2026-04-23, https://reddit.com/r/familyoffice/comments/1ssw8t8/document_collection_and_performance_reporting/ohpb969/
- Addepar is "based on AUM (1-1.5bps)" plus a mandatory service contract; Black Diamond is "$25k for 500
  accounts". — 2024-12-05, .../1eo2eyp/.../m0k18jc/
- A Nordic vendor quoted "35K/year, 7K in implementation cost". — 2024-11-12, .../lwrg0nd/
- Asora's CEO: "plans start at $900/month". He also mentioned FO clients "in the same $50K quote boat".
  — 2025-07-07, .../1aech5o/.../n1sbizi/
- Capital: Canoe raised about $82M (https://www.extruct.ai/hub/canoeintelligence-com/). Arch
  raised a $20M Series A and a $52M Series B
  (https://www.fintechfutures.com/venture-capital-funding/arch-raises-52m-series-b).
  Investors believe in this category.
- Consumer: an LP pays an **$850** CPA fee for each late-K-1 amendment (fatFIRE, above). Equity-crowdfunding
  K-1s cost "like $500 a form" at a preparer (HN 2025-12-08, https://news.ycombinator.com/item?id=46189418).
  These are costs of lateness and complexity, which a downloader does not remove.
- Logins and agents: "things that absolutely cannot afford to be hallucinated ... Taxes.
  So it needs a lot of sensitive information ... it's not like I can just let it
  run wild." — HN 2026-05-05, https://news.ycombinator.com/item?id=48025950.
  On Bogleheads, one poster argues that even *saving* statements locally is risky (search summary of
  https://www.bogleheads.org/forum/viewtopic.php?t=413272). On the positive side, a hobbyist "used LLMs and
  browsermcp to download all my statements from all of my banks" — HN 2026-01-02,
  https://news.ycombinator.com/item?id=46466058. The sibling file `trust_agents_logins.md` covers this
  question more broadly.

## 5. Consumer-ish version (HNW individuals with 10+ accounts)

**WEAK as a fast self-serve market.**

For:
- "~14 accounts ... across 7 financial institutions. It's tedious to go download
  statements from all of them manually." — HN 2024-02-27, https://news.ycombinator.com/item?id=39529645
- "it's always 15 minutes to a half hour tediously downloading things statement by
  statement, form by form, argghh." — HN 2018-02-15, https://news.ycombinator.com/item?id=16388855
- Open-source scrapers already exist (finance-dl, Selenium, with a manual MFA fallback;
  https://github.com/jbms/finance-dl). Hackers build this for themselves.
- Angel and syndicate investors receive many late K-1s every year (HN 2023-04-07,
  https://news.ycombinator.com/item?id=35485798).

Against:
- Bogleheads members describe statement retention as a small annual chore: "might cost me 30 minutes a year".
  Pulling a household statement takes "10 seconds"
  (search summaries of https://www.bogleheads.org/forum/viewtopic.php?t=471927 and t=413272).
- Brokers already publish consolidated 1099s, and tax software imports them. The individual's
  real pain is K-1 *timing*, which an agent can only monitor.
- India: CAMS/KFintech already email a consolidated statement (CAS) for each PAN on request, and
  the open-source `casparser` parses it (https://github.com/codereverser/casparser).
  Brokers and Quicko/ClearTax already pull capital-gains statements. I found **no** user
  evidence of AIF/PMS statement-collection pain, so treat it as unvalidated.

## Evidence AGAINST the idea (summary)

1. **A crowded, funded field:** Canoe, Arch, Masttro, Addepar, Asora, Aleta, Asset Vantage,
   SEI Archway and Orca. Many of them use GP-side feeds and APIs rather than screen-scraping, which
   is more reliable than a browser agent.
2. **K-1 pain is mostly lateness and data entry, not fetching.** An agent does not fix
   either one (though extraction is possible).
3. **Consumers find downloading tolerable** (30 min/yr). Tax software and broker consolidated
   1099s already cover most of it.
4. **Trust:** people say they would not let an agent "run wild" on tax and financial accounts. There is no
   direct evidence that people would give logins to a local agent.
5. **Ops buyers want a system of record and reporting**, not just files. Downloading is
   layer 1 of 4, and you "still have a 15–20% exception tail".
6. **India evidence is missing.** Registrars email statements, and the AIF/PMS pain is unproven.

## Where a local agent could still win

- **Small SFOs, MFOs, RIAs and CPA firms below the $40k tier.** Several posters called the price "absurd",
  and Asora's entry tier is $900/month. A local agent at roughly $100–300/month that collects files into
  a folder, plus Canoe-style "expected vs. received" tracking, would fill the gap.
- **The long-tail portals that vendors' GP feeds miss** (the 15–20% exception tail),
  bank and custodian 1099s, and statements from small funds.
- **Key-person MFA risk:** the MFA codes stay on the firm's own machine and phone, with no third-party vault.
- This uses the same mechanism as the current wedge (month-end bank-statement and invoice
  collection), so it could be a **vertical extension** rather than a separate product.

## What to validate next (not done here)

- Talk to 5 FO or CPA ops people. Ask how many portals they use, the minutes per portal, whether they use Canoe or Arch
  and what that tool misses, and whether they would run a local agent on their own logins.
- Ask Indian CAs about the AIF/PMS statement chase: is it a real hours sink, or is it emailed?
