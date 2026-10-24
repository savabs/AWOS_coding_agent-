# Paid demand for repetitive computer tasks (research, 2026-10-22)

Research agent, web sources. Most figures come from vendors or one-day listing snapshots; the caveats are at the end.

**Key:** CU = needs computer use (no reliable API). Part = some targets have APIs. API = Zapier and similar tools already cover it.

## Task categories, strongest paid-demand evidence first

1. **Data entry and copying data between apps (into a spreadsheet, CRM or ERP).** CU/Part.
   - Zapier survey (2021): data entry is the most dreaded task; 38% say data entry and 31% say copying between sources is their most time-consuming work.
   - Parseur (2025): $28.5k per employee per year, 9+ h/week.
   - Freelancer.com: about 450 open data-entry jobs, $5–29/h.
2. **Lead lists and contact scraping** (Google Maps, LinkedIn, directories). CU for logged-in sites.
   - Apify scrapers have 660K / 213K / 174K users.
   - Freelance jobs here are mostly cheap one-offs.
3. **Social and e-commerce data extraction.** CU/Part. Apify Instagram scraper 441K users, TikTok 350K.
4. **Invoice processing and accounts payable.** Part.
   - The RPA use case vendors cite most.
   - Bain: 55% of AP teams lack OCR or online approvals.
   - Zapier survey: 33% of SMB workers say invoice management eats their time.
   - Recurring bookkeeping jobs show up on freelance sites.
5. **Downloading invoices, receipts and statements from vendor and bank portals.** CU.
   - GetMyInvoices sells exactly this: 10,000+ portals, €19–59+/month, priced per portal.
   - Recurs every month.
6. **Healthcare payer-portal checks** (eligibility, claim status, prior authorisation). CU.
   - CAQH estimates $20B+/yr could be saved; a manual prior authorisation costs about $4.46 vs $0.03 automated.
   - HIPAA-heavy, which favours a local-only tool.
7. **Government and compliance portals, and legacy desktop apps.** CU. Microsoft's own pitch for Power Automate Desktop is the case where "no connector or API is available".
8. **Recurring browser work for virtual assistants** (inbox, scheduling, CRM updates). Part.
   - 9 of 20 sampled VA posts were ongoing work.
   - Examples: "Gmail Inbox Management Specialist" (about $9/h, ongoing); "Customer Info Data Entry Assistant" (daily, about $18/h).
9. **Job applications on autopilot.** CU. Bot detection and terms-of-service risk.
10. **Product listing and cross-listing** (eBay, Poshmark, Mercari, Shopify). CU.
    - Vendoo $12–50+/month; List Perfectly $29–249/month.
    - Freelance listing jobs pay $50–500.
11. **Document and report assembly** (Excel or PowerPoint built from data). Part.
12. **Lead routing** (forms and ads into a CRM or Sheet). API. Zapier, Make and n8n already own this, so avoid it.
13. **Filling in web contact forms for outreach.** CU. Low value, spam and terms-of-service risk.
14. **Mac file organisation and text snippets.** Local.
    - Keyboard Maestro $36, Hazel about $42, TextExpander about $40/yr.
    - People pay for local automation, but at low prices.
15. **Research into a spreadsheet** (competitor prices, public contract data). CU/Part.
    - The main thing people use Manus and Operator for.
    - Also where those tools fail most visibly.

## Time-waste surveys (all run by vendors)

- **Automation Anywhere:** more than 3 h/day on manual computer tasks.
- **Asana (2023):** 58% of the day goes on "work about work".
- **Basis:** executives spend 6 h/week on data cleaning and entry.
- **Time Etc:** SMB owners spend 36% of their week on admin.
- **Manual entry error rate:** about 1% per field.

## What early AI computer-use agents get wrong

- Operator bought eggs for $31.43 without asking for approval.
- In one study (Operator and Manus, N=31), 14 of 31 users hit errors, mostly bot detection, and Operator froze silently.
- Agents fabricate data, struggle with spreadsheets, burn through credits, and claim changes they never made.

Each of these maps to a planned feature: ask before risky steps, verify the effect, and run locally at no cost per run.

## Where computer use is needed and demand recurs

Portal invoice and statement downloads (#5), payer and government portals (#6, #7), legacy desktop entry (#1, #7), marketplace listings (#10), and logged-in research (#2, #15).

Zapier only reaches sites with no API through an attended Chrome extension or third-party add-ons. An unattended, local tool fills that gap.

## Caveats

- **Freelance data:** Upwork returned 403 errors, so there are no Upwork category sizes. Freelancer.com figures are one-day snapshots of average bids.
- **Surveys:** run by vendors and self-reported.
- **Apify:** "users" means lifetime users, not paying customers.
- **Zapier, Make and n8n:** no usage-ranked template statistics are published.
- **AI agent usage:** the evidence is anecdotal.

## Sources

- zapier.com/blog/most-dreaded-tasks
- parseur.com/blog/manual-data-entry-report
- freelancer.com/jobs/{data-entry, web-scraping, virtual-assistant}
- fiverr.com/categories/data
- apify.com/store
- getmyinvoices.com
- caqh.org/explorations/caqh-index
- learn.microsoft.com (RPA module)
- bain.com
- auditoria.ai (2023 report)
- automationanywhere.com (press room)
- Asana Anatomy of Work 2023
- basis.com
- beamex white paper
- raycast.com/store
- g2.com (Keyboard Maestro)
- blog.vendoo.co
- the-decoder.com (Operator early users)
- adn.com (the eggs story)
- arxiv.org/abs/2509.14528
- airframe.ai (Manus analysis)
- automationatlas.io (Zapier workarounds)
