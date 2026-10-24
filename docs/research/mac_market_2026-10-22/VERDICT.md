# Mac-first verdict (2026-10-22)

Based on `market_and_money.md`, `competition.md` and `pain_validation.md` in this folder, plus `../task_demand_2026-10-22/`.

## 1. Is the pain real?

**Yes, but not for everyone.**

**Strong** for:
- people with many portals: agencies and solo founders with 10–30 SaaS subscriptions and several banks and cards;
- bookkeepers chasing clients for statements. In 2026, Xero users wrote "several hours a month" and "the task that holds our bookkeepers up the most".

**Weak** for simple freelancers: 10–15 minutes a month.

**The clearest unsolved gap is bank-statement PDFs:**
- Hubdoc dropped fetching them in 2022 because banks blocked server-side scraping.
- 2FA breaks the cloud connectors.
- Accountants still need the PDFs at year end.

People already pay for this: €19+/month for GetMyInvoices, Dext and Hubdoc, and they build their own scripts.

## 2. Is Mac-first a good market?

**A real niche, not a mass market:**
- about 6M Mac-first small businesses and professionals (US+UK+EU);
- about 60–160k plausible payers;
- a realistic business of **$0.6–10M a year**.

**Price is the danger.** Mac utilities sell at $3–10/month. $29–99 only works when priced on hours saved, the way bookkeeping software is (Dext is about $25).

## 3. Competition

**Crowded:**
- "An agent that uses your Mac": OpenAI, Anthropic, Perplexity, Simular and Manus all have one.
- Record-then-replay: Codex Record & Replay and Claude in Chrome's scheduled shortcuts.
- **"Local and private":** Perplexity's fully local mode shipped 2026-10-20, using the same Qwen model family as AWOS.

**Real but time-limited gaps:**
1. Replay with zero model calls, plus local-model repair when a site changes and a re-check. Keyboard Maestro breaks; Codex and Claude pay for a model call on every run.
2. Credentials and statements never leave the Mac. GetMyInvoices, Skyvern and Hubdoc hold logins in their cloud, and Claude blocks finance apps by default.
3. Long-tail portals plus filing on the Mac, period and amount checks, and a "missing documents" report for the accountant.
4. Runs in the user's own logged-in session, so it copes with 2FA. "Semi-unattended": one phone tap for the occasional code.

**Biggest threats in the next 12 months:**
1. Perplexity adds scheduling and recorded skills to its local mode.
2. OpenAI makes Record & Replay schedulable and cached.
3. GetMyInvoices or Dext add an agent for portals they don't cover.

## 4. Recommendation

**Wedge product: a "month-end document pack" for Mac.**
- Every month it collects bank and card statements plus SaaS and vendor invoices, using the user's own session.
- It checks them (period, balance carry-over, missing items), files them, and hands the accountant a complete pack, with a list of what is still missing.
- Credentials never leave the Mac.

**Who:**
- Primary: Mac-using agencies, solo founders and consultants with many portals.
- Channel: their bookkeepers and accountants, who feel the pain most and can recommend it.

**Price:** about $15–30/month for an individual, more per client when sold through bookkeepers. Pitch it on hours saved and deductions not lost, not as a Mac utility.

**Move fast:** about 80% of the technology already exists (teach-once, verified effects, secrets broker, sandbox, interruption handling). The gaps are time-limited.

**Before a big build, check with 5–10 people from the target group:**
- How many portals do they have?
- How long does month end take them?
- Would they pay $15–30/month?
- Would their bookkeeper recommend it?

**Honest risks:**
- The value per person is modest, which caps the price.
- Bookkeepers mostly run Windows.
- Big players may close the gap within a year.
- Bank terms of service and bot detection.
