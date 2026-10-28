# Wedge study: collecting documents from investor / GP / fund-admin portals

Date: 2026-10-23. Desk research only: no one was contacted and nothing was signed up for.
Question: should AWOS's first paid product be automated collection of LP documents
(quarterly reports, capital call and distribution notices, K-1s, statements, NAV
statements) from investor, GP, fund-admin, custodian and broker portals?

**Short answer:** the pain is real, documented, and recurring, and buyers already pay
for it. But in the US/EU the category is **crowded and well funded**, and a reseller
already sells Canoe-powered collection to small family offices for about $180 per
holding per year. A tiny India-based vendor can't win head-on there. The opening that
fits AWOS is narrower: the **long tail and the privacy-sensitive buyer**: offices that
refuse to hand portal passwords to a cloud vendor, portals the incumbents don't cover
(Indian AIF/PMS/RTA portals, small GPs, private banks in Singapore, Dubai and
Switzerland), and the founder's own network. Treat it as a strong *design-partner*
wedge. It is not yet proven as a standalone business.

---

## 1. Who does this work today, and how painful is it

| Buyer | Typical portal load | Who does it | Evidence |
|---|---|---|---|
| Single/multi-family office (SFO/MFO) | 25+ portals, 100+ funds is common | Ops/accounting staff, often the COO's team | NY family investment group: 25+ portals, 125+ funds, >10 h/week before automation ([Mantle case study](https://withmantle.com/case-studies/family-offices), undated, vendor) |
| Larger SFO with "hundreds of funds" | hundreds of funds | Ops team | Pilot House chose Canoe to automate collection "across hundreds of funds" ([Canoe, 2021](https://canoeintelligence.com/pilot-house-selects-canoe-intelligence-to-scale-its-alternative-investment-documents-and-data-processes/)) |
| RIA / wealth manager with alts on platform | dozens to hundreds of portals across clients | Junior "alternative investments operations analyst" | Focus Partners job post: download, organise and archive fund docs, capital/distribution notices, K-1s ([Greenhouse](https://job-boards.greenhouse.io/focuspartnerswealth/jobs/6110005004)); NewEdge alts ops analyst posts dated 2025-12-18 and 2026-01-08 ([NewEdge](https://www.newedgewealth.com/?p=51755)) |
| Bank trust / private bank | many client accounts | Ops analysts | KeyBank post: upload statements and K-1s, chase funds for late monthly statements ([dejobs](https://efirstbank.dejobs.org/cleveland-oh/operations-analyst-alternative-investments/7F58E767980049A6AA8366A12029F3EC/job)) |
| CPAs for HNW clients | K-1 season peak (Feb–Sep) | Tax staff | One family office reconciles ~600 K-1s/yr, some up to 150 pages ([K1x case study](https://k1x.io/?p=4434), vendor) |
| Outsourced back offices (often in India) | whatever clients have | Offshore staff | Empaxis (US HQ, ops in Noida/Kolkata) sells "PE statement downloads, file naming and storage, data extraction" as a service ([Empaxis](https://empaxis.com/clients/family-office-outsourcing)) |

What the pain looks like (all vendor-sourced, so possibly overstated):
- Someone "downloads documents from multiple portals, renames files, and routes them";
  every manager uses a different format, and some add watermarks
  ([Masttro](https://masttro.com/insights/capital-call-processing-for-family-offices)).
- Capital calls are time-critical: missing one means missing a wire deadline set by the LPA.
- K-1s arrive late and in bursts, with tiered partnerships delaying them further
  ([K1x blog](https://blog.k1x.io/resources/family-office-and-private-wealth-management-what-you-need-to-know-about-the-digital-k-1)).
- The work is junior, repetitive, and outsourced to India today. That is the clearest
  sign that it can be automated and that buyers will pay to get rid of it.

Gap: I found **no independent survey** of hours per firm and **no Reddit or forum threads**.
The best number is about 10 h/week for 25 portals and 125 funds. Interviews need to confirm it.

## 2. Incumbents and prices

| Vendor | What it does | How it collects | Price (public) | Funding / scale |
|---|---|---|---|---|
| **Canoe Intelligence** (Canoe Connect) | Collection + extraction + validation, pushes to accounting/reporting | "Direct APIs, enhanced RPA, secure document stores"; "expert team reviews exceptions"; says it "seamlessly manages MFA"; 3,000+ portals ([Canoe Connect](https://canoeintelligence.com/solutions/canoe-connect/)) | Sales-led, not published ([rfp.wiki](https://www.rfp.wiki/vendors/canoe-intelligence)) | $36M Series C led by Goldman, Jul 2024 ([PBI](https://www.privatebankerinternational.com/news/goldman-sachs-leads-36m-series-c-funding-round-for-canoe-intelligence/)); ~$82M total (unverified, [Extruct](https://www.extruct.ai/hub/canoeintelligence-com/)); 325 clients in 2024; 45% more new firms in 2025, EMEA = 21% of business ([Canoe, Feb 2026](https://canoeintelligence.com/a-year-of-growth-and-operational-excellence-in-emea/)); SOC 2 Type 2 ([Canoe security](https://info.canoeintelligence.com/implementation-data-security?hsLang=en)) |
| **Arch** (arch.co → arch.com) | "Portal of portals": statements, calls, K-1s "pulled from portals and inboxes"; Arch Pay for capital calls | Portal logins and email, run by Arch; credential handling not disclosed; 800+ portals ([Schwab partner page](https://advisorservices.schwab.com/provider-solutions/Arch)) | Not published | $52M Series B (Oak HC/FT), Sep 2025, $77.5M total ([BusinessWire](https://www.businesswire.com/news/home/20250915156402/en/Arch-Raises-$52M-Series-B-to-Modernize-Private-Markets-Infrastructure)); 700+ customers, 50k+ funds ([arch.com](https://arch.com)); >$500B on platform, Jul 2026 ([fintech.global](https://fintech.global/2026/07/29/arch-tops-500bn-in-assets-as-new-series-b-backers-revealed/)) |
| **Asora** (FO platform) | Reporting platform; resells Canoe for alts | Canoe under the hood | **$15,600/yr** at $50–100M tier + $3,900 onboarding; alts add-on **$3,600/yr per 20 holdings** (~$180/holding) ([Asora pricing](https://asora.com/pricing)) | n/a |
| **Masttro** | Full-stack FO platform; AI module for calls/distributions; partners with Arch for alts feed | Arch + own ingestion | Flat fee, not public; 3–6 month onboarding (per competitor [Asora](https://www.asora.com/compare/masttro)) | $43M from FTV / Citi ([Institutional Investor](https://www.institutionalinvestor.com/index%2Ephp/article/2bdjwejr10tkktxabwtfk/ria-intel/wealthtech-platform-masttro-raises-43-million-from-ftv-capital-citi)) |
| **Copia Wealth Studios** | "Clarity" for FOs with automated document acquisition from investor portals ([PBI](https://www.privatebankerinternational.com/news/copia-wealth-studios-ai-wealth-platform/)) | Not disclosed | Quote-based; GP-side "Connect" at $495/mo ([Mann Report, Aug 2025](https://www.mannpublications.com/mannreport/2025/08/04/copia-unveils-connect-ai-powered-investor-portal/)) | Small (seed ~$1M listed, unverified) |
| **Mantle Portal** | Collects across investor portals for FOs | Not disclosed | Not published | n/a |
| **AltExchange** | Syncs iCapital, Juniper Square and other admin portals | Now **part of iCapital** ([AltExchange](https://www.altexchange.com/?p=3328)) | Was free up to $25M during a promo | iCapital-owned |
| **K1x** | K-1 aggregation and extraction | Upload/feeds | n/a | n/a |
| **SEI Archway, Addepar, Allvue** | Accounting/reporting systems that *consume* the data | Integrate with Canoe/Arch ([SEI–Canoe, 2024](https://canoeintelligence.com/?p=7170)) | Archway ~$3k–10k/yr entry (estimate, [softwarefinder](https://softwarefinder.com/accounting-software/archway)) | Large |
| **Offshore outsourcers** (Empaxis and others) | People doing the downloads | Humans with client credentials | Priced on labour | n/a |

Gaps and complaints I could find:
- **Credential custody.** Every incumbent holds client portal passwords in its own cloud
  and none publish how. That is AWOS's clearest differentiator, but no source shows buyers
  *complaining* about it. It may be a buying criterion or it may be a non-issue. **Must test.**
- **Coverage.** Canoe says "3,000+ portals", Arch "800+". Incumbents focus on US/EU GP
  and fund-admin portals. I found nothing showing they cover Indian AIF/PMS portals,
  RTA portals (CAMS, KFintech) or Gulf/Asian private-bank e-statements.
- **Human-in-the-loop exists anyway.** Canoe uses an exception-handling team, so
  "fully automated" is not the bar. A verified pack plus a short exceptions list is.
- **Cost for small offices.** The Asora resale price ($180/holding/yr) shows that small
  offices *can* already buy it cheaply. "Cheaper" alone is a weak pitch in the US.
- Public reviews are thin: about 86 reviews on one site for Canoe, at 42% confidence
  ([rfp.wiki](https://www.rfp.wiki/vendors/canoe-intelligence)).

## 3. Market size and willingness to pay

| Segment | Count | Source |
|---|---|---|
| Single family offices, world | ~8,030 (2024) → ~10,720 (2030) | [Deloitte 2024](https://www.deloitte.com/global/en/services/deloitte-private/about/defining-the-family-office-landscape.html) |
| …North America / Europe / APAC / Middle East | 3,180 / 2,020 / 2,290 / 290 | Deloitte exec summary; [Dakota](https://www.dakota.com/fundraising-news/deloitte-number-of-family-offices-to-grow-75-by-2030) |
| Singapore FOs | >2,000 (2025, unverified ranking site) | [Altss](https://altss.com/rankings/largest-family-offices-asia) |
| India FOs | ~300 (up from 45 in 2018); alts allocation 18% → 40%+ | [Business Standard / PwC, Jul 2024](https://www.business-standard.com/industry/news/family-offices-rise-to-300-since-2018-tier-ii-iii-cities-in-demand-pwc-124070300714_1.html); [GRI, 2026](https://news.griinstitute.org/en/real-estate/somani-patni-hinduja-family-offices-india-real-estate-allocations-2026) |
| India AIF commitments | ₹12.5 lakh crore+ (Mar 2025); investments ₹5.38 lakh crore | [Outlook Business](https://www.outlookbusiness.com/markets/aifs-gain-traction-as-investments-rise-32-to-rs-538-lakh-crore-by-march-2025) |
| US SEC-registered RIAs | 16,544 (end 2025) | [InvestmentNews / IAA](https://www.investmentnews.com/goria/practice-management/ria-industry-hits-record-highs-across-the-board-in-2025-as-assets-surge-22/266865) |
| Multi-FOs, CPA firms with HNW clients, UK/EU wealth managers | not found | n/a |

Rough sizing: perhaps 10k–20k firms worldwide hold enough private-fund positions to have
this pain. Only a minority are underserved (small, privacy-sensitive, or non-US portals).
At $3k–15k/yr per firm, a few hundred customers is a $1–3M ARR niche business. That is
worth having, but the category won't be a large standalone market for a new entrant.

Willingness to pay, inferred:
- **Anchor:** Asora resells Canoe at ~$180/holding/yr, and a junior ops analyst in the US
  costs ~$70–90k/yr fully loaded (my estimate, not sourced). A 50-fund office saving
  10 h/week can justify **$5k–15k/yr**.
- **India:** FO budgets are smaller, and the cost to beat is an Indian ops executive or
  CA article clerk. Expect **₹1.5–6 lakh/yr ($2k–7k)** per office at first, with
  higher prices only if the product also replaces reconciliation and reporting work.
- "Self-hosted / local" may command a premium from paranoid offices, or it may count
  for nothing. Unknown.

## 4. Fit for AWOS

| Factor | Fit | Notes |
|---|---|---|
| Read-only task | Strong | Login, navigate, download. No money moves, so the blast radius is small. |
| Verifiable output | Strong | Check fund, entity, period, document type, and amounts (capital call amount and due date, NAV, distribution) against the previous quarter and expected schedule. This matches the existing month-end pack verifier. |
| Local credentials | Strong pitch, unproven demand | Incumbents store passwords in their cloud. AWOS can say "passwords and documents never leave your machine." That speaks to SEC Reg S-P breach-notice duties and EU DORA third-party risk ([Kolonell summary, secondary](https://kolonell.com/en/blog/secure-investor-portal-private-equity-new-york-2026)). |
| MFA / OTP | Medium | MFA is standard on fund-admin portals ([Alter Domus](https://technology.alterdomus.com/SolutionDetail/investor-portal/public)). AWOS's pause-for-OTP is honest but needs a human present. Quarterly batches make that acceptable (one 20-minute session per quarter). Authenticator-app TOTP secrets could be stored locally with consent. |
| Terms of service | Medium risk | Many sites ban "robots, scrapers or other automated means" ([H.I.G. terms](https://hig.com/terms-conditions/); [Premier Alts](https://www.premieralts.com/terms)). AWOS runs in the *user's own* browser, on the user's own account, at human pace, and the user starts each run. That is legally similar to an assistant doing it, and incumbents (Canoe RPA, Arch) run at scale on the same footing. Get a short US counsel opinion before charging US clients. |
| Security reviews | Biggest friction | US/UK institutions and RIAs will send vendor questionnaires and ask for SOC 2. A first-year SOC 2 Type II costs ~$25k+ all-in, with audits at $12–20k ([Drata](https://drata.com/blog/soc-2-audit-cost)). Local-first *reduces* scope (no customer data hosted), which is an argument small FOs may accept. Institutions mostly won't. Start with FOs and Indian firms. |
| Windows vs Mac | Risk | Finance back offices are mostly Windows (industry norm; I found no hard data). AWOS is Mac-first today, so a Windows build or a browser-extension form factor is likely needed beyond Mac-using founders and boutique offices. |
| Browser agent | Good | Portals are web apps, often SPAs (Juniper Square, iCapital, Intralinks, Alter Domus). They are accessibility-tree friendly and change slowly. Per-portal "skills" can be cached and replayed cheaply, which matches the AWOS cost-routing design. |
| Path to general computer use | Good | Covers login, MFA, navigation, download, file and verify across many unfamiliar UIs. Next steps (posting to accounting software, reconciling, booking capital calls) are natural computer-use expansions. |

## 5. Selling from India as a tiny team

- **Feasible for FOs, hard for institutions.** Offshore servicing of family offices is
  normal (Empaxis runs ops from Noida and Kolkata), so being in India isn't
  disqualifying. Being two people with no SOC 2 is, for RIAs above ~$1B and for banks.
- **Best first market: the founder's network plus India.** Indian FOs, PMS/AIF investors
  and their CAs get statements from AIF managers, PMS providers, RTAs and brokers.
  Incumbents likely ignore those portals (unverified; check in interviews). There are
  ~300 FOs plus thousands of HNI clients served by CAs and multi-family offices. Also
  try GCC and Singapore offices with India links.
- **Path to the first 10 customers:**
  1. 15–20 interviews from the network (FO COOs, CFOs, ops leads, CAs serving HNIs, one or two RIAs).
  2. Pick 3 design partners whose portal mix overlaps.
  3. Run a **paid pilot**: 60 days, 10–20 portals, one quarter-end cycle, at
     **$1,000–2,500 (₹75k–2L)**, credited to year one. Success means at least 90% of
     expected documents collected and verified on time, and less than 1 h/quarter of
     human effort apart from OTPs.
  4. Convert at **$400–1,200/month** (or ₹25k–75k/month in India), priced by portal or
     entity count.
  5. Use referrals. FO COOs talk to each other, and the CA firms are channel partners.
- **Deliverable:** a desktop app plus a quarterly "LP document pack" (folder tree,
  naming convention, CSV index with amounts and dates, exception list), with export
  to whatever they use (Excel, Addepar/Asora upload, Tally/Zoho for Indian offices).

## 6. Verdict

Scores out of 10, higher is better (for risk, 10 means low risk):

| Dimension | Score | Why |
|---|---|---|
| Business today (standalone) | **5** | Real recurring pain and proven willingness to pay, but crowded by well-funded players ($77M Arch, ~$82M Canoe, iCapital) and a cheap reseller floor |
| Ease of first sale | **6** | Warm network, read-only scope, paid pilot is easy to justify. Security questionnaires and Windows will slow anything beyond small FOs |
| Technical fit | **8** | Almost identical to the month-end pack: login, MFA pause, download, verify amounts and periods. Accessibility-tree portals, quarterly cadence |
| Path to general computer use | **7** | Long tail of different UIs is good training; extends into posting and reconciling |
| Risk (10 = low) | **4** | Incumbent competition, ToS ambiguity, SOC 2 expectations, credential liability if anything leaks, and a quarterly cadence that slows learning and revenue |

**Overall:** a good *second step* from the month-end pack and a strong design-partner
wedge through the founder's network. Pitch it as **"private, local LP-document pack
for offices that won't hand passwords to a cloud vendor, including portals the big
tools don't cover"**. Don't pitch it as a cheaper Canoe. Go ahead only if interviews
show at least 3 buyers who (a) have 15+ portals, (b) say credential custody or
non-covered portals is why they haven't bought Canoe or Arch, and (c) will pay for a pilot.

### Biggest unknowns, and 8 interview questions

1. "Walk me through last quarter-end: how many portals, how many funds and entities,
   who logged in, and how many hours did it take, including chasing missing documents?"
2. "Have you looked at Canoe, Arch, Masttro or Asora? What did you choose, and if you
   didn't buy, why not: price, coverage, giving them passwords, or something else?"
3. "Would you give a cloud vendor your portal logins? Has your compliance team or
   family principal ever refused that?"
4. "Which portals are hardest or not covered today: Indian AIF/PMS/RTA portals,
   private banks, small GPs, emailed PDFs?"
5. "How is MFA set up on these portals: SMS to whose phone, authenticator app, email?
   Could one person sit with the tool for 20 minutes at quarter-end?"
6. "What happens after download: who renames, files, checks amounts, books capital
   calls? Which system does the data go into (Excel, Addepar, Asora, Archway, Tally)?"
7. "What would a vendor need for you to say yes: SOC 2, an NDA, an on-prem install,
   Windows support, an Indian or US entity, references?"
8. "If this saved X hours per quarter and passwords never left your machine, what would
   you pay per year, from which budget, and who signs off?"

Other unknowns: real Windows/Mac split among targets; whether incumbents really skip
Indian/Gulf portals; legal view on portal ToS for user-initiated automation; how often
portals change their UI (maintenance cost per portal).
