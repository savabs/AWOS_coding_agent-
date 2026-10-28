# Freelance demand: repetitive computer chores people pay humans for (research, 2026-10-22)

Research agent, web sources.

**Sample.** Upwork, Fiverr and OnlineJobs.ph (OJ) block direct access, so these come from search snippets only. Freelancer.com pages were read in full:
- 150 posts from Freelancer.com
- about 15 from Upwork
- about 22 from OJ
- plus published Upwork statistics

That makes about 185 posts. After removing site-building jobs, in-person jobs and fraud or spam, about **110 repetitive computer-chore posts** remain.

## Clusters, ranked by money × recurrence × computer-use fit

| Rank | Cluster | Share | Pay | Recurrence | No-API fit | Privacy | Existing SaaS |
|---|---|---|---|---|---|---|---|
| 1 | **Back-office work inside industry software**: property management (Buildium, AppFolio, TurboTenant, Jobber); medical prior-auth and eligibility (Athena, Availity, payer portals); trucking dispatch; county-record lookups | ~13% | OJ $3–8/h full-time ($120/week–$1,100/month); Freelancer $5–19/h | **Very high** (full-time, long-term) | **Very high** | High | Patchy, enterprise-priced RPA |
| 2 | **Bookkeeping, AP/AR and invoice entry** (QBO, Xero, FreshBooks; PDF to Excel; bill posting) | ~13% | Upwork trials that turn into monthly work; Freelancer $17/h or a $1,378/month retainer; OJ $4–6/h full-time | High (monthly or weekly) | Medium-high (the bank, vendor and utility portals have no API) | High | Partial: Dext, Hubdoc, GetMyInvoices |
| 3 | **Marketplace catalogue work** (Seller Central flat files, eBay, Magento, Shopify, Kaufland, Deliveroo, Alibaba) | ~15% | OJ $3–7/h at 40–55 h/week; $52–490 per batch | High | Medium-high (the partner hubs are screen-only) | Low | Partial: feed tools break |
| 4 | **Document, email or form → spreadsheet or CRM entry** | ~32% (largest) | $2–23/h; Upwork median fixed price $33–50 | Mixed: about a third daily or ongoing | Medium (often no screen control needed; the destination may be an ERP) | Medium | Many OCR and Zapier tools |
| 5 | **Lead lists and web research** | ~16% | $3–21/h | Campaign-based | Medium | Low–medium | **Crowded**, with terms-of-service risk |
| 6 | **Inbox, calendar and CRM admin** | ~10% | $4–19/h | Daily | Low–medium (APIs exist) | High | Many |
| 7 | **Portal submissions** (applicant-tracking forms, takedown notices) | ~5% | $4–18/h | Daily | **Very high** | Medium | Few |

## Representative real posts

**Upwork:**
- Shopify Customer Data Entry
- Bookkeeper for monthly QuickBooks cleanup
- QuickBooks Online Bookkeeper for Catch-Up & Monthly Bookkeeping
- Data Entry Specialist for ERP and Spreadsheet Management
- CRM Data Entry (HubSpot/Salesforce)

**OnlineJobs.ph:**
- Amazon Listings VA ($3/h × 40 h)
- eCommerce Product Lister (Amazon/eBay/Magento, 55 h/week)
- Property Manager + Cleaning Ops Admin, Buildium + Jobber ($900–1,100/month)
- Virtual Medical Assistant – Prior Authorization ($6–8/h)
- VA + Bookkeeper full-time ($6/h)

**Freelancer.com:**
- FreshBooks AP/AR Bookkeeper ($17/h, weekly)
- Manage 33 Units on TurboTenant (8–15 h/week)
- 1 hr/day Sheets updates ($100–500/month)
- Deliveroo Grocery Menu Creation (1,200 listings)

## The agent's picks

1. **Property-management back office** (Buildium, AppFolio, TurboTenant):
   - Full-time VA budgets of $500–1,100/month.
   - The screens have no API, and the data is private.
   - Results can be checked by reading the ledger back.
2. **Bookkeeping prep, "collect and post"**:
   - Download from portals, then match in QBO, Xero or FreshBooks.
   - Ask before posting.
3. **Marketplace catalogue work**: good for public demos, but a weaker privacy story.

**Skip:** lead generation (crowded, terms-of-service risk) and healthcare prior-auth for now (HIPAA and BAA overhead).

## Caveats

- **Sampling:** results come from search snippets and are biased toward cheap one-off posts. Fiverr queue counts weren't visible.
- **Price ceiling:** a human costs $2–8/h full-time in the Philippines ($300–1,300/month), or $11–23/h on Upwork. The product has to win on privacy, no hiring or turnover, overnight speed and verification, not on cost per hour.
- **Excluded as fraud or against platform rules:** forged bank statements, Instagram DM spam, mass LinkedIn applications, account sharing. **The product should refuse these kinds of tasks.**

## Sources

- freelancer.com/jobs/{data-entry, virtual-assistant}
- Upwork job pages
- onlinejobs.ph job pages
- investors.upwork.com (In-Demand Skills 2026)
- tryvibeworker.com (252k Upwork posts)
- gigradar.io
- fiverr.com/gigs/data-entry-gig
