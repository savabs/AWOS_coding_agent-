# Repetitive computer tasks people complain about (research, 2026-10-22)

Research agent, web sources.

**Limitation:** reddit.com could not be fetched by the tools, so Reddit appears only second-hand. First-hand sources:
- Hacker News (Algolia)
- the Zapier, Make, Shopify and Keyboard Maestro forums
- an ICAI accountant case study

Evidence labels: **P** = first-hand practitioner post; **V** = vendor or survey claim.

## Tasks found

| # | Who | Task | Frequency | API? | Privacy | Evidence | Source |
|---|---|---|---|---|---|---|---|
| 1 | SMB owner | Log into supplier sites and download invoices for the accountant | recurring | No (Zapier staff: "use a browser bot") | med | P | community.zapier.com/…/26171 |
| 2 | Operations | Download a weekly report from a vendor portal | weekly | No | med | P | community.make.com/t/…/1677 |
| 3 | Bookkeepers | Monthly statements from 5–30 client bank portals, each with MFA | monthly, ~3 h per client | Partly (feeds) | high | V + second-hand Reddit | imagetotable.ai; docgenie.cloud |
| 4 | Indian CA firm | Per-client tax/GST portal downloads, then reconcile | daily in peak season | No | high | P | ai.icai.org (use case 188) |
| 5 | Individual / SMB | Bank transaction exports; government portals | recurring | No | high | P | HN item 39566112 |
| 6 | Small-company admin | File invoices into the accountant's Dropbox; 2FA bank payments | monthly | No | high | P | HN item 23140958 |
| 7 | Freelancer | Fetch bills from portals and send them to the bookkeeper | monthly | No (wrote Selenium) | med | P | HN item 22345150 |
| 8 | Employee | Time and expense entry | weekly, 1 h | No | low | P | HN item 23140958 |
| 9 | Data-entry worker | Upload hundreds of items to a web portal | monthly | No | med | P | HN item 20564687 |
| 10 | Junior lawyer | Check court lists for every client name | daily, 30–60 min | No | high | P | HN item 20564687 |
| 11 | Paralegal | Download and rename hundreds of PACER filings | per case | Paid tools exist | high | 2nd-hand | Substack |
| 12 | Office admin | Spreadsheet lookup, then a Word template, then email | daily | Desktop | high | P | HN item 20564687 |
| 13–15 | Analyst / family office / employee | Weekly or monthly reports built from several apps and poor web UIs | weekly / monthly | Partial | med–high | P | HN items 20564687, 22345150, 21373931 |
| 16 | E-commerce | Carrier refund claims | ongoing | No | low | P | HN item 21373931 |
| 17 | Wholesale | Container tracking; ERP with no API | ongoing | No | med | P | HN item 22345150 |
| 18 | Insurance claims staff | Key 100+ claims a day into a legacy Windows app | daily | No | high | P | HN item 26147653 |
| 19 | Shopify dropshipper | Upload tracking numbers by hand | per order | Partial | med | P | community.shopify.com |
| 20 | Amazon sellers | Reimbursement and inventory reports, cases | weekly | SP-API is hard; Amazon's terms are a risk | med | V | sellerapp.com |
| 21 | Insurance agents | Re-key the same applicant into 15–40 carrier portals | daily | Partial | high | V | skyvern.com |
| 22 | Physician practices | About 39 prior authorisations a week, ~13 h | weekly | Mostly from 2027 | very high | AMA survey | ama-assn.org |
| 23 | Therapy clinics | Eligibility checks, 3–5 h a week | per patient | Partial | very high | V | sprypt.com |
| 24 | MSPs | Reconcile vendor-portal licences against the PSA | monthly | Partial | med | V | meetgradient.com |
| 25 | Restaurants | Reconcile delivery-app payouts against the POS | weekly | Partial | med | V | urbanpiper.com |
| 26 | Landlords | Utility portal balances and bills | monthly | No | med | V | lablab.ai, tailride.so |
| 27 | Mac users | Bank statement downloads; invoice PDFs to Mail | recurring | No | high | P | forum.keyboardmaestro.com (37340) |

What people say they actually use computer-use agents for (Hacker News):
- getting data out of legacy banking portals
- polling for visa appointment slots
- Gmail-to-calendar ("hit and miss, mostly miss")
- LinkedIn outreach

## Patterns

1. **"Log in, find it, download it, file it" across many portals.** The strongest pattern: it appears unprompted across unrelated forums. Zapier and Make stop at login, MFA and cookies. It is read-only, easy to verify and sensitive, so it is the best first task.
2. **Re-keying data from one system into another web form.** A lot of evidence, but these are writes, so the ask-before-acting gate is needed.
3. **Scheduled checks and lookups** (court lists, dockets, tracking, appointment slots). Read-only and daily, but the evidence is scattered.
4. **Recurring reports assembled from several apps.** Confidential data favours a local tool.
5. **Healthcare and insurance admin.** The deepest pain, but the weakest fit for a first product (HIPAA, payer portals, liability).

## Evidence quality

- The first-hand posts are anecdotes, and many date from 2017 to 2022.
- The rows about specific industries rely on vendor marketing.
- The Reddit gap matters: a person should spot-check r/Bookkeeping, r/msp, r/AmazonSeller and r/realtors.
