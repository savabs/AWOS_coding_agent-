# Which real repetitive tasks to build first: synthesis (2026-10-22)

**Inputs** (four parallel web studies, in this folder):
- `community_pain.md`: what people complain about
- `paid_demand.md`: what people pay software and RPA for
- `freelance_jobs.md`: what people pay humans for (about 110 job posts)
- `segments.md`: which customers fit

## What all four agree on

**"Log in → find it → download it → file it → check it" across many portals with no API** is the strongest and most independently confirmed chore. That covers bank statements, vendor and utility invoices, tax documents and weekly vendor reports.

- **Complaint forums:** it comes up unprompted on Zapier, Make, Hacker News and Keyboard Maestro. Zapier and Make users get stuck at logins and MFA.
- **Paid software:** GetMyInvoices sells exactly this for €19–59/month across 10,000+ portals. Its existence is proof of demand.
- **Paid humans:** bookkeeping and AP/AR prep make up about 13% of the chore posts, often as monthly retainers. Full-time VAs ($300–1,100/month) do it in property management and bookkeeping.
- **Customers:** bookkeepers spend about 1–3 h per client per month on it. Privacy rules (GLBA, §7216, IRS Pub 4557) make a local-only tool a real buying reason.

**Why it fits AWOS:**
- It only reads (low risk).
- The result can be checked: the file arrived, it covers the right period, and the balance carries over from last month. That is exactly the "verified effect" design.
- It runs overnight.
- Each portal can be taught once by demonstration.
- The data is sensitive, so local-first matters.

## First three tasks to build

1. **Portal document collector (read-only).**
   - Taught once per portal.
   - Each night: log in, download the latest statement or invoice, and file it as `Client/Account/YYYY-MM`.
   - Verify the period, that the PDF or CSV parses, and that this month's opening balance equals last month's closing balance.
   - MFA codes go into a morning queue; never bypass MFA.
   - **Users:** bookkeepers, small-business owners, freelancers, property managers.
2. **"Collect → post" bookkeeping prep.**
   - Match what was downloaded against QBO, Xero or FreshBooks, and list the differences.
   - **Ask before posting anything.** This is the first write step, behind the approval gate.
3. **Industry back-office entry, starting with property management** (Buildium, AppFolio, TurboTenant):
   - rent and bill entry, utility bills, tenant records;
   - this work is currently done by full-time VAs at $500–1,100/month, the screens have no API, and the ledger can be read back to check it.
   - **Alternative public demo:** marketplace catalogue work (Seller Central, eBay, partner hubs).

**Skip for now:**
- lead generation (crowded, terms-of-service risk)
- inbox and CRM glue (APIs exist; Zapier and n8n own it)
- healthcare prior-auth and eligibility (deepest pain, but HIPAA and liability; revisit later)

**Always refuse:** forged documents, review or DM spam, account farming, mass applications.

## Pricing anchors

The human alternative costs $300–1,300/month (full-time Philippines VA) or $11–23/h (Upwork). Software comparables: GetMyInvoices €19–59/month, Dext and Hubdoc, Vendoo $12–50/month. **$29–99/month per business is plausible.** The case has to rest on privacy, no hiring or turnover, overnight runs and verified results, not on price per hour.

## Main risks to test early

1. **Windows-heavy segments.** Possible answers: a "Mac mini as the overnight bot box" offer, or Mac-heavy niches (solo lawyers, freelancers, cloud-based bookkeepers).
2. **MFA on bank portals.** Needs a morning code queue, trusted-device sessions, and the user's own authenticator app.
3. **Bank and portal terms of service.** The tool acts in the user's own session on the user's own device. Get a legal review.
4. **Portals change often.** The agent must detect its own failure (balance continuity, file checks) and halt rather than file something wrong.
5. **Competitors:** GetMyInvoices, Dext and Hubdoc (connectors), Simular (Mac agent, $21.5M raised), Skyvern (cloud browser agent).

## Evidence gaps

- Reddit could not be read by the tools; a person should spot-check r/Bookkeeping, r/msp and r/realtors.
- Upwork and Fiverr were seen through search snippets only.
- Most hour estimates come from vendors.
- There is no current data on Mac share in these segments.
