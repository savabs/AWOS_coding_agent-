# Which segment first for a local Mac computer-use assistant (research, 2026-10-22)

Research agent, from web sources. Read these caveats first:
- **Time figures:** most come from automation vendors, so treat them as rough direction.
- **Mac share:** no current survey exists per segment. The figures below are old or come from nearby populations.

## Segment comparison

| Segment | Top recurring portal chores | How often / time | API gap | Privacy need | Mac share | Willingness to pay |
|---|---|---|---|---|---|---|
| **Bookkeepers / small accounting firms** | Pull statement PDFs/CSVs from client bank and card portals (no feed, or a broken one); fetch vendor and utility bills; prepare QBO/Xero reconciliation; collect documents at tax time | Monthly per client; about 1–3 h per client per month (vendor estimates). One bookkeeper spent 10+ h a month for 18 clients | **High** for the long tail of small banks, credit unions and utilities. Feeds (Plaid, Dext's "11,500 banks") cover the big ones but break | **High:** GLBA/FTC Safeguards Rule, IRC §7216, IRS Pub 4557. 50% of CPAs name privacy as the top barrier to AI (VSCPA 2026) | Low and not really known: 2.3% of web traffic in 2009; 5 of 140 firms bought Macs (Rightworks). Cloud QBO/Xero makes Mac workable | Firms charge about $300/month per client; they already pay for Dext, Hubdoc and offshore staff; 331k US payroll/bookkeeping businesses |
| Medical / dental / therapy | Eligibility and benefits checks on payer portals; claim status; prior authorisations; credentialing | Daily. Dental: 7 min per patient via portal, 12 min by phone; one practice spent 6+ h a day calling payers | Medium: clearinghouses exist but return incomplete detail | **Highest** (HIPAA BAAs) | Low for dental (Dentrix and Eaglesoft are Windows-only) | Dental high but a crowded market; therapists low |
| Real estate / property managers | MLS entry, transaction paperwork, utility portals, AppFolio/Buildium statements | Weekly / monthly | Medium | Low–medium | About 18% Mac (c. 2013) | Agents low; property managers medium |
| E-commerce sellers | Reimbursement claims, listing sync, Seller Central reports | Daily / weekly | Low (SP-API and Shopify APIs exist) | Low | Probably higher | Proven (GETIDA takes 25% of recovered funds). Amazon's 2026 agent rules add risk |
| Small law / immigration | Re-keying USCIS forms, case-status checks, court e-filing, intake | Per case | Medium (Docketwise and Filevine already fill forms) | High | Best Mac signal: 15.5% of solos (ABA 2015); 55% of a Clio small-firm sample use Apple | High but fragmented |
| Freelancers / agencies | Invoicing, client portals, timesheets | Weekly | Low (APIs exist) | Low | Highest | Low |
| Insurance agents | Re-keying the same client into each carrier's quoting portal; renewals | Daily | **High**: raters cover only 6–8 of 20 carriers | Medium (GLBA) | Low (Windows agency-management systems) | High: 77% name carrier portals as a pain. Simular is targeting insurance at about $500/month |

## Recommendation: bookkeepers on QBO/Xero who use Macs (solo to 10 people)

- **Why it fits:**
  - The work happens in portals.
  - It is a repetitive monthly batch that can run overnight.
  - The results are mechanically checkable: statement period, balance continuity.
  - The rules give "data never leaves the device" a real buying reason.
- **Return:** saving a third of 1–3 h per client per month is worth 10–40 h a month to a firm.
- **Windows offices:** sell a Mac mini as the overnight machine, with AWOS on it.
- **Runner-up:** dental eligibility checks (most pain, but the software is Windows-only and the AI market is crowded).

## First three tasks

1. **Overnight statement retrieval, learned from one demo per bank.**
   - Download the statement PDF/CSV and file it under client, account and YYYY-MM.
   - Verify the statement period and that this month's opening balance equals last month's closing balance.
   - When the bank asks for an MFA code, queue it for a person.
2. **Reconciliation prep in QBO/Xero.**
   - Compare the statement with the register and list the differences.
   - Ask before posting anything.
3. **Vendor and utility bill collection.**
   - Pull invoices from portals that never email them.
   - Push them to Dext, Hubdoc or QBO.
   - Check the amount and date against the bank line.

## Risks

- **Low Mac share:** to be checked in discovery, or answered with the Mac-mini overnight machine.
- **MFA:** needs a morning code queue, trusted-device sessions, and the bookkeeper's own authenticator.
- **Bank and portal terms of service:** credential-sharing clauses. Position the product as working in the user's own session on the user's own device. Get a legal review.
- **Competition:** Plaid and Dext coverage keeps growing; Simular (Mac, $21.5M raised); Skyvern.
- **Portal changes:** pages change, so the agent must detect its own failure. Balance continuity is the check.

## Sources

fintegrationfs.com (bank statement collection); imagetotable.ai; taprun.dev; help.dext.com; journalofaccountancy.com (FTC Safeguards); vscpa.com 2026 AI survey; accountingweb.co.uk; rightworks.com; nerdwallet.com (bookkeeping pricing); ibisworld.com; stealthagents.com (dental verification); dentrixascend.com; joinheard.com (HIPAA AI); siotek.net; carepatron.com; dsnews.com; nar.realtor (2014 technology survey); upwork.com; revenuegeeks.com; docketwise.com; attorneyatwork.com; mactech.com; quotesweep.com; vertafore.com; techcrunch.com (Simular, 2025-12-02); theregister.com; clerk.chat; fcaa.gov.sk.ca; thelogic.co; ecomcrew.com (Amazon agent rules, March 2026); skyvern.com.

Full URLs are in the agent report quoted in session e65f3328.
