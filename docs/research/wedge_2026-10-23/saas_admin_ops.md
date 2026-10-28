# Back-office SaaS admin chores: which one should be AWOS's first paying wedge?

Desk research, 2026-10-23. Web sources only; nobody was contacted. Most numbers come from
vendors (their marketing, or surveys they ran themselves), so treat them as directional. Where a
figure is my own estimate, it says **(est.)**.

**Bottom line:** start with **(c) monthly SaaS/vendor/cloud invoice collection**. It only reads
data, it is easy to check, the buyer is reachable, and Indian tax rules make the pain worse for
Indian companies. Use it to earn trust, then move into **read-only access-review exports** and later
**long-tail offboarding** (writes, more money, harder to sell). License cleanup (write) and India
government portals come last.

---

## 1. The candidates: who does the work, how much, the evidence, and today's fix

### (a) Onboarding/offboarding in apps without SCIM (the "long tail")
| Item | Finding |
|---|---|
| Who does it | IT admin/helpdesk. At companies under 300 people this is often one IT person, a DevOps engineer or an ops generalist. App owners in each department handle their own tools. |
| How common | Stitchflow looked at 721 SaaS apps: **57% have no SCIM at any price**, about 42% put SCIM behind an enterprise tier, and only 9 apps (1.2%) include it on the base tier. Page last updated 2026-01-29. https://www.stitchflow.com/blog/scim-vs-sso-tax-manual-provisioning |
| Hours | Nudge Security survey of 375 IT pros (2023-06): about **5 h per departing employee** just to find and remove cloud/SaaS access, and 70% had seen problems from incomplete offboarding. https://www.nudgesecurity.com/post/employee-offboarding-by-the-numbers . Stitchflow puts the cost at about **$12k per app per year** (labour, unused licences, compliance). |
| Pain evidence | Beyond Identity survey (cited by IDEN): 89% of former employees still had access to at least one app. https://www.idenhq.com/it-employee-offboarding-checklist . Vanta community: auditors often find "terminated employees missed because they had direct app logins outside SSO". https://community.vanta.com/x/grc/jpsl640ola9o/soc-2-type-ii-access-reviews-auditor-expectations . Unixi says HR→IdP triggers cover only about 50% of the stack. https://unixi.io/blog/top-5-ways-to-offboard-saas-apps/ |
| Today's fix | IdP (Okta/Entra/Google) plus SCIM where it exists. For everything else: checklists and screenshots (https://www.manifest.ly/use-cases/systems-administration/user-offboarding-checklist), a "Create a Task" step for a human in Zluri, or an "app owner task" in Josys (https://help.josys.com/hc/en-us/articles/4860822476574). **Stitchflow sells exactly this gap:** browser automation backed by a 24/7 human-in-the-loop team, about **<$5k per app per year flat**, and it holds credentials in *its own* GCP/1Password. https://www.stitchflow.com/blog/managed-scim-integration-for-disconnected-apps , https://www.stitchflow.com/scim/appdynamics |

### (b) License/seat cleanup and access reviews
| Item | Finding |
|---|---|
| Who does it | IT, plus whoever owns SOC 2/ISO (often a founder, CTO or ops lead at 50–300 people). Finance gets involved at renewals. |
| Hours | Mostly concentrated around audits. Quarterly reviews mean exporting the user list for each app, checking roles and MFA, and saving screenshots and spreadsheets. A practitioner calls this "time-consuming and unsustainable". https://yomotherboard.com/question/how-are-companies-streamlining-soc2-access-reviews-and-evidence-collection/ . Wing Security makes the same point. https://wing.security/resources/saas-security/save-hours-on-access-review-with-sspm |
| What auditors want | Exports from the IdP and from each app at the review date, change logs, and screenshots of permission levels (Torii guide). https://articles.toriihq.com/articles/blog/soc2-access-review-requirements-legal-guide.txt |
| Today's fix | Vanta/Drata/Secureframe plus SMPs (Zluri, Torii, Lumos, Zylo) work through API connectors. Apps that are not connected still need manual CSV exports. |
| Note | The **read half** (exporting user lists and screenshots from apps that are not connected) carries little risk and fits AWOS well. The **write half** (removing seats, downgrading plans) is the same risk as (a). |

### (c) Collecting SaaS/vendor/cloud invoices each month (AP documents)
| Item | Finding |
|---|---|
| Who does it | Finance/AP executive, an outsourced bookkeeper or CA firm, or the founder at smaller companies. Card spend at tech companies is mostly SaaS. |
| Hours | Tailride (vendor) claims **about 11 h/month** of invoice chasing for SaaS finance teams. https://tailride.so/it/invoice-management-for/saas . GetMyInvoices case study: "several hours per month". https://www.casestudies.com/company/get-my-invoices/case-study/ehrenmuller-gmbh-saves-several-hours-per-month-with-get-my-invoices . **(est.)** 2–5 minutes per portal (log in, pass 2FA, find the invoice, download, rename, upload) × 50–150 portals = **3–12 h/month**, plus chasing the missing ones. |
| Pain evidence | Portal-only vendors don't email their invoices, so they "slip past an AP inbox process". Staff log in, handle 2FA, rename files and upload. https://invoicedataextraction.com/blog/vendor-portal-invoice-collection-automation . DEV post: "bookkeeping automation is solid until one vendor decides not to email their invoice." https://dev.to/richard_smith_154156d471ef/found-a-gap-in-the-bookkeeping-automation-chain-1l9i |
| India multiplier | Foreign SaaS counts as an import of services. The buyer pays **18% IGST under reverse charge**, issues a **self-invoice** (Rule 47A, 30-day limit, per secondary sources), and banks may ask for the **tax invoice plus 15CA/15CB** before remitting. So every foreign SaaS invoice has to be *obtained*, and on time. https://setmycompany.com/?p=2015 , https://taxguru.in/?p=1083001 , https://ebizfiling.com/blog/gst-implications-on-foreign-digital-service-purchases-by-indian-businesses/ (blogs; check with a CA). |
| Today's fix | Doing it by hand; Ramp/Brex receipt matching from email (Ramp auto-captures from *some* merchants and email inboxes; unmatched receipts are attached by hand) https://support.ramp.com/hc/en-us/articles/360042588454-Submitting-receipts-memos-and-accounting-for-your-Ramp-transactions , https://community.ramp.com/t/where-do-unmatched-receipts-go/431 ; portal fetchers (below). |

### (d) Other chores found
- **India GST e-invoice/IRN downloads:** a bulk JSON download and GSP APIs already exist, so this is a weak target for a browser agent. https://sovos.com/regulatory-updates/vat/india-new-e-invoice-download-functionality/
- **Vendor-portal invoice *submission*** (uploading your own invoices to customers' AP portals such as Coupa or Ariba) is a known pain for B2B sellers, but writes are required and the buyers are larger enterprises. I found no strong primary evidence in this pass.
- **Investor/LP portal documents:** covered separately in `investor_portal_docs.md` in this folder.
- I found nothing stronger than (a)–(c) for repetitive console chores at tech companies.

---

## 2. Incumbents and prices

| Vendor | What it is | Price (source, date) | Gap relevant to AWOS |
|---|---|---|---|
| BetterCloud | SaaS ops/SMP | No public list price. Vendr median **$45k/yr** (range $17k–105k, 196 deals). https://www.vendr.com/marketplace/bettercloud | Connector-based; apps without connectors are left out |
| Zluri (India) | SMP + lifecycle | About $4–8/user/month (TechRadar review, undated) https://techradar.com/pro/zluri-review ; claims 300+ API integrations | Apps that lack an API become a "Create a Task" step for a human https://blog.reviewaitool.com/tag/zluri-tips-and-tricks-2026/ |
| Torii | SMP | Basic about $2.50/employee/month; median about **$30–32k/yr** (CostBench/Vendr, 2026) https://www.costbench.com/compare/productiv-vs-torii/ | Browser extension used for *discovery*, not for actions |
| Productiv | SMP | Median about $40–50k/yr; **reportedly shut down on 2026-08-06** https://www.zluri.com/blog/productiv-shuts-down | Its customers are moving to other tools now; a window to reach them |
| Lumos | Access governance/lifecycle | Quote only (Capterra) https://www.capterra.co.uk/software/1030757/lumos ; release notes 2026-08 cover reusable deprovisioning workflows https://releasebot.io/updates/lumos | Connector/API based; no browser fallback found |
| Zylo | SMP (enterprise) | Median about $35k/yr (Vendr via electroiq) https://electroiq.com/stats/zylo-statistics/ | Enterprise-focused |
| Okta Workflows | IdP automation | Starter $6/user/month (5 flows), Essentials $17 (50 flows); $1.5k minimum contract (2025–26) https://pricingsaas.com/companies/okta/diffs/2025Q3 | Needs APIs/connectors |
| Rippling IT | HRIS + IT add-on | $8/employee/month base plus modules; full stack about $20–50/employee/month https://www.outsail.co/post/is-rippling-right-for-your-business-2026 | Same SCIM/API limit |
| **Stitchflow** | Managed browser automation for apps without SCIM | **<$5k per app per year**, humans in the loop, credentials held in the vendor's cloud | **Closest competitor to AWOS for (a).** Shows that people pay per app. AWOS can differ by running locally and costing less. |
| GetMyInvoices | Portal invoice fetcher (DE) | About €19–179/month by document volume; claims 10k+ portals, 2FA and captcha handling https://www.fastlancer.org/en/fastlancer-blog/getmyinvoices-review/ | Cloud-stored credentials; built around DATEV/EU |
| Tailride | Extension + desktop fetcher | Extension free; per-document plans from about €19; **"Done-For-You" human portal extraction $500/month** https://tailride.so/pricing | 100+ named portals; the extension uses your existing browser session (the same idea as AWOS) https://tailride.so/manual-portal-extraction |
| Hellobill (DE) | Desktop bill fetcher | Credentials kept in the local keychain (per its site; not independently checked) | Shows a local-credentials precedent |
| Dext / Hubdoc / Expensify | Receipt capture/OCR | Per user/client | Mostly email/upload capture; they don't log into portals |
| Ramp / Brex | Cards + receipt matching | Free card platforms | Auto-capture limited to supported merchants and email; gaps are filled by hand |
| Skyvern / Simular | General browser/computer-use agents | Skyvern $0–149/month plus enterprise, credit-based (2026) https://costbench.com/software/browser-automation/skyvern/ ; published "bulk invoice downloader" cookbook https://www.skyvern.com/docs/cookbooks/bulk-invoice-downloader ; Simular offers vendor invoice tracking https://www.simular.ai/solutions/vendor-invoice-tracking | **Computer-use agents already target (c)**, but as developer tools or in the cloud, not as a local product for finance teams |

**What is still manual:** (a) roughly half or more of apps (no SCIM). (b) user-list exports from apps that aren't connected. (c) portal-only vendors, 2FA, long-tail vendors outside fetcher catalogues, and the India RCM paperwork.

---

## 3. Market, buyer, willingness to pay, sales cycle, trust

| | (a) Offboarding long tail | (b) Access-review exports (read) | (c) Invoice collection |
|---|---|---|---|
| Market signal | SMP market about $4.6–5.6B in 2025 (analyst estimates, https://www.barchart.com/story/news/36401236/ ; https://marketintelo.com/report/saas-management-platform-market). Stitchflow prices at <$5k/app/yr. | Part of GRC/SMP spend; peaks at audit time | Small budgets: fetchers cost €19–179/month; human done-for-you service $500/month |
| Buyer | IT/security lead, CTO | Compliance owner, CTO | Finance controller, founder, outsourced CA/bookkeeper |
| WTP (est.) | $1–3k per app per year at <300 people | $300–1,000 per audit cycle | **$50–300/month** for a company; CA firms pay per client |
| Sales cycle (est.) | 1–3 months; security review | 2–6 weeks around an audit | **Days to 2 weeks**; founder or controller decides alone |
| Trust hurdle | **High.** Admin credentials and write access; SOC 2 questions for a 2-person vendor | Medium (admin read access) | **Low–medium.** Read-only billing pages; credentials are still sensitive |

**Trust and AWOS's local-first answer:** cloud vendors keep customer admin credentials on their
servers (Stitchflow uses GCP plus 1Password; GetMyInvoices is cloud-based). AWOS runs in the
user's own browser session, so no credentials reach AWOS. That answers the first question in
every security review ("where are our credentials stored?"). Things still to provide: a short
security note, a read-only mode, local audit logs and screenshots for each action, no
telemetry by default, and a signed binary. SOC 2 is not needed for finance pilots at small
companies, but IT buyers in (a) will ask for it.

---

## 4. Fit with AWOS's current capabilities

| Criterion | (a) | (b-read) | (c) |
|---|---|---|---|
| Read vs write | Write (remove or suspend a user). Errors can lock out real users or leave access in place | Read | Read (download a PDF) |
| Verification | Good: re-open the user list and confirm the user is gone, then screenshot it | Good: compare row count with what the UI shows | **Very good:** PDF exists, its amount and date match the card or bank line, no duplicate hash |
| Approval gates | Each removal needs approval; a destructive-action refusal is required | No gate needed | No gate (optional: gate the "upload to accounting" step) |
| OTP/captcha pause | Admin consoles often ask to re-authenticate | Same | Common (AWS, Google Ads, banks), and AWOS already supports it |
| Matches existing prototypes | The "create API key in dashboard" prototype is a write flow in an admin console | Close | **Matches the existing "download/file documents from portals" prototype directly** |
| Windows vs Mac | Developer polls show about 62–66% macOS (Pragmatic Engineer, about 6k LinkedIn plus 4k X votes) https://borncity.com/news/entwickler-umfrage-macos-dominiert-mit-616-unter-tech-profis/ . **(est.)** IT admins at startups are mostly on Mac. Finance/AP staff and Indian CA firms are **mostly on Windows**. | Mac | **Needs Windows** for finance users and CAs |
| Browser fit | All three are web consoles, so a Chrome/Edge accessibility-tree approach works on both operating systems. That makes Windows support much cheaper than full desktop computer use. | | |

**Path to general computer use:** (c) covers *many different* sites every month (navigation,
logins, 2FA, downloads), which makes it good breadth training data, and it runs on a recurring
schedule. (a) adds safe *write* actions with approvals, which is the next skill AWOS needs. The
order (c) → (b-read) → (a) builds trust and capability one step at a time.

---

## 5. Selling from India as a tiny team

- **First 10 customers through contacts:**
  - *Finance/investment contacts* → (1) portfolio-company CFOs and controllers of Indian startups
    with 50–300 people paying foreign SaaS in USD (the RCM self-invoice and 15CA pain), and
    (2) **CA/bookkeeping firms** that do this for 20–100 clients each. One firm can be worth
    5–20 companies.
  - *Tech-team contacts* → founder or CTO at their companies, for (c) first (the founder often
    signs off on it), then a conversation about (b)/(a) with their IT lead.
- **Pilot structure (suggested):** 30 days free, then paid. Scope it as "your top 30 portals, all
  invoices for last month plus this month, filed into Drive/Zoho Books/Tally folder names you
  choose, with a reconciliation sheet against the card statement." Success means ≥90% of
  invoices found without help and every miss explained.
- **Price (est.):** India companies ₹8–15k/month (about $100–180) for up to 100 portals; US/EU
  companies $199–499/month; CA firms ₹2–4k per client per month. That undercuts Tailride's $500
  human service and charges for the long tail, local credentials and India paperwork that
  GetMyInvoices doesn't cover. Later, for (a): about $1–2k per app per year against
  Stitchflow's <$5k.
- **Logistics:** invoice in USD through Stripe/Razorpay international. Exports of services are
  zero-rated under LUT, so get a CA to confirm. Support should be async; the local agent keeps
  per-customer infrastructure close to zero, which fits the no-spend constraint.

---

## 6. Verdict

### Ranking
1. **(c) Monthly invoice collection.** Read-only, easy to verify, quick to sell, and the founder's finance contacts reach the buyers. The India RCM rules add pain. Downsides: low price per account and many cheap competitors, so AWOS has to win on covering any portal, keeping credentials local, and producing a reconciliation output.
2. **(b-read) Access-review evidence exports** from apps that aren't connected. Read-only, audit-driven, and sold to the same tech-company contacts. Good second product.
3. **(a) Long-tail onboarding/offboarding.** The most money (Stitchflow proves people pay per app), but it needs writes, admin credentials and a security review. Do it after AWOS has a reliability record.
4. **(b-write) License/seat cleanup.** Value only shows up at renewals, and SMPs already cover most of it through APIs.
5. **(d) India government/vendor portals.** APIs and GSPs already exist, and the evidence is thin.

### Scores for (c), 1–10 (higher is better; for "risk", higher means *safer*)
| Business today | Ease of first sale | Technical fit | Path to general computer use | Risk (safety) |
|---|---|---|---|---|
| **6** (real but small budgets; crowded) | **8** (one decision-maker, contacts in place, pilot in days) | **9** (matches the existing prototype, read-only, verifiable) | **6** (breadth across many sites, but no writes) | **8** (read-only; main risk is credential handling, reduced by running locally) |

For comparison, (a) would score about: business 8, first sale 4, technical fit 6, path 8, risk 4.

### 8 customer-interview questions (for finance/CA contacts; Q7–8 also for tech leads)
1. Walk me through last month's close: which invoices did you have to *log in* somewhere to get, and how many vendors was that?
2. Who does it, how long did it take last month, and what slipped (late, missing, chased by email)?
3. For foreign SaaS: how do you handle the RCM self-invoice and the 15CA/15CB documents, and has a missing invoice ever delayed a payment or a GST credit?
4. Which portals are the worst (2FA, captchas, shared logins), and who holds those logins today?
5. What have you tried (Ramp/Brex matching, GetMyInvoices, Dext, an intern), and why did it stop working or not stick?
6. Would you let software run in *your* browser with your logins if nothing left your laptop? What would you need to see first?
7. If this delivered 100% of last month's invoices, named and filed, plus a reconciliation sheet, what would that be worth per month, and who would approve the spend?
8. (Tech leads) When someone leaves, which apps are outside SSO/SCIM, how do you remove them today, and what did your last SOC 2 access review cost you in time?
