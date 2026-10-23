# First paying wedge, given the founder's contacts: verdict (2026-10-23)

The founder can reach **technical teams at tech companies** and people in **finance / investment**. Four
parallel desk studies (web, sourced, in this folder) scored the candidate first products:

| Candidate | Report | Business today | Ease of first sale | Tech fit | Path to general CU | Risk (10 = low) |
|---|---|---|---|---|---|---|
| **Company invoice & document collection** (SaaS / vendor / cloud invoices each month) | `saas_admin_ops.md` (c) | 6 | **8** | **9** | 6 | **8** |
| Investor-portal document collection (family offices, wealth managers) | `investor_portal_docs.md` | 5 | 6 | 8 | 7 | 4 |
| AI testing of web apps (tech teams) | `qa_testing.md` | 6 | 6 | 5 | 8 | 4 |
| SaaS onboarding / offboarding in apps without SCIM | `saas_admin_ops.md` (a) | 8 | 4 | 6 | 8 | 4 |
| Compliance-evidence collection (SOC 2 / ISO) | `compliance_evidence.md` | 4 | 4 | 8 | 6 | 4 |

All figures are directional: most come from vendor marketing, aggregators or the agents' own estimates.

## Recommendation

**Start with company invoice & document collection, sold through the founder's finance contacts
(startup CFOs / finance heads and chartered-accountant firms), with India first.**

- **Fit:** it is the month-end document pack we already built, pointed at businesses instead of individuals.
  Read-only, easy to verify (every vendor's invoice for the month, matched against the card statement),
  low risk.
- **India-specific pull:** foreign SaaS is taxed under GST reverse charge, so the invoice is needed for the
  self-invoice and for remittance paperwork (15CA/15CB). A missing invoice can cost a tax credit or delay a
  payment. (Directional; confirm with a CA.)
- **Channel:** one CA firm serves many companies, so CA firms are a multiplier.
- **Price:** about ₹8–15k/month per Indian company, $199–499/month abroad, or ₹2–4k per client per month via
  CA firms. Modest but fast to sell.
- **Competition:** GetMyInvoices (portal connectors, credentials in its cloud), Tailride, developer-focused
  agents. Our angle: any portal, logins stay on the customer's machine, and a reconciliation the accountant
  can trust.

## Expansion path (same engine, rising value)

1. Company invoices (read) -> 2. investor-portal documents for family offices and wealth managers (read,
   higher price, finance contacts) -> 3. user-list exports for access reviews (read, tech contacts) ->
4. onboarding / offboarding in apps without SCIM (writes behind approval; biggest budget, most general
   computer use). AI web testing stays an optional design-partner track for hardening the agent on real
   apps, not the first business.

## Product implications

- **Browser-first, not Mac-only.** Finance and CA staff mostly use Windows. All these chores happen in a
  browser, and the RL harness already drives Chromium, so a Chrome-based agent covers Windows and Mac.
  The Safari/Mac path stays for Mac users.
- **Verification is the product:** "100% of last month's invoices, named, filed, matched to the card
  statement, with a list of what's missing."
- **The RL work keeps paying off:** a reliable small local model is what makes "any portal, no setup,
  private" possible without per-run cloud costs.

## Go / no-go test (before a big build)

Interview **5 startup finance heads / CFOs, 3 CA firms and 2 family offices** with the questions in
`saas_admin_ops.md` and `investor_portal_docs.md`. Go if at least 3 say: it takes them hours each month,
logins/portals are the painful part, they would let a local tool use their browser, and they would pay
for a 30-day pilot.
