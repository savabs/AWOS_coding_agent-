# Wedge check: automated compliance-evidence collection (SOC 2 / ISO 27001 / HIPAA / PCI)

Date: 2026-10-23. Method: web desk research only (no contact, no sign-ups).
Caveat: most sources are vendor blogs or comparison sites that sell compliance
software. Reddit and HN threads did not come up in search, so "pain" evidence is
second-hand. Treat the numbers as ranges, not facts.

## TL;DR

- The pain is real but small per customer. The manual remainder is often put at
  ~20% of evidence, about 40-60 hours per audit, for a company that already pays
  $8-60k/yr for a platform.
- **The gap is closing fast.** Vanta, the market leader (~$300M ARR), shipped an
  agent that drives a browser and screenshots evidence in **July 2026**. Comp AI
  (open source) runs scheduled "browser automations" that log in through 2FA.
  Strac sells a Chrome-extension evidence agent. Screenata sells extension and
  desktop capture. AWOS would be the 5th+ entrant into a feature that incumbents
  now bundle.
- AWOS's one real point of difference is that it runs **locally in the user's own browser
  session, so credentials never leave the machine**. Vanta's agent runs in a remote
  browser, and you sign in through a live view. That difference matters to some security
  teams, but it is not yet shown to be a reason to buy.
- **Verdict: not a strong *first* paying business on its own.** It may work as
  (a) a cheap service-led pilot for consultant-run companies that have no platform,
  or (b) one demo use case that shows AWOS's "local, credentials stay home"
  reliability. Do the interviews below before building anything.

## 1. How evidence is gathered today

| Item | Finding | Source |
|---|---|---|
| Volume | A typical SOC 2 Type II needs ~120+ evidence items, such as dated screenshots, exports and lists | [amitkoth.com](https://amitkoth.com/soc-2-evidence-collection-busywork/) |
| Without a platform | 200-400 hours over 3-6 months | [scrut.io cost guide, 2026](https://www.scrut.io/post/cost-of-soc-2-audit) |
| With a platform (residual) | Drata is said to automate ~80% via API. The remaining 20% (in-app RBAC screenshots, UI workflows, access reviews, attestations) takes ~40-60 h per audit, ~50 h/quarter for 50 controls | [Screenata, 2025-06-25](https://screenata.com/resources/blog/automate-soc2-evidence-collection-screenshots) (competitor claim) |
| Screenshot share | Vanta: 36% of 650k images uploaded in 6 months were screenshots | [Screenata citing Vanta, 2026-08-18](https://screenata.com/resources/blog/do-auditors-accept-ai-captured-screenshots-for-soc-2) |
| Who does it | Engineers pulled off product work. At larger companies, GRC analysts ("manage evidence collection and control testing") | [secure.com blog](https://www.secure.com/blog/pass-soc-2-quickly); [YipitData GRC job](https://job-boards.greenhouse.io/yipitdatajobs/jobs/7299088); [DataRobot GRC Analyst (India)](https://jobs.techstars.com/companies/datarobot/jobs/79042367-grc-analyst) |
| User complaints | Vanta reviewers: device inventory inaccurate, "still chase manual evidence"; auditors ask for screenshot checks the platform doesn't do; "back and forth outside the platform" | [AWS Marketplace reviews of Vanta](https://aws.amazon.com/marketplace/reviews/reviews-list/prodview-5ophamrbfxt44?page=36) |
| Type II nuance | A point-in-time screenshot does not prove a control operated all period. Auditors want populations and samples (for example joiners/leavers lists plus the filter used to export them) | [Strike Graph sampling guide](https://help.strikegraph.com/articles/4056301-soc-2-type-2-population-and-sampling-guide); [Sensiba Drata Type 2 guide](https://knowledge.sensiba.com/type-2-quickstart-guide) |
| India GRC labour cost | GRC analyst ~$6.3-12.5k/yr base in India (staffing-firm estimate) | [Wisemonk](https://www.wisemonk.io/blogs/grc-identity-access-reviewers-india) |

Takeaway: the work is "tedious, not hard." For a platform customer it is tens of
hours per quarter. In India, that is a few thousand dollars of analyst time a
year, so a tool replacing it has a low price ceiling there. US pricing is better.

## 2. Incumbents and what is still manual

| Vendor | Price (est., quote-based) | Browser/computer-use evidence agent? | Notes |
|---|---|---|---|
| **Vanta** | SMB avg ~$29k/yr, enterprise ~$57k ([SpendHound](https://www.spendhound.com/marketplace/vanta-pricing)) | **Yes, shipped wk of 2026-07-26** ([release notes](https://help.vanta.com/en/articles/11345422-product-updates); [help article, 2026-09-01](https://help.vanta.com/en/articles/13146167-collecting-screenshot-evidence-with-the-vanta-agent)) | Remote browser with a live view. It pauses for login (password, MFA code, SSO). It does not support biometrics or hardware keys. It is read-only, a human approves each screenshot, and Vanta recommends a read-only service account. Pairing it with an evaluator agent "doubled task completion" ([Vanta](https://www.vanta.com/resources/vantas-agent-development-principles)) |
| **Drata** | ~$7.5-100k/yr ([soc2auditors.org](https://soc2auditors.org/insights/drata-pricing/)) | Not found. Agentic AI is for vendor risk (Aug 2025) ([review](https://soc2auditors.org/insights/drata-review/)) | Manual evidence goes into the Evidence Library as a file, URL or ticket ([Drata help](https://help.drata.com/en/articles/8288579-evidence-library-overview)). ~$98M ARR (Jan 2025) |
| **Sprinto** (India) | ~$4-30k/yr | AI layer relaunched Mar 2026 as "Autonomous Trust Platform". A vendor blog claims timestamped screenshot capture (unverified) ([Sprinto vs Drata](https://sprinto.com/blog/sprinto-vs-drata/)) | $31.8M raised, Series B $20M Apr 2024 ([TechCrunch](https://techcrunch.com/2024/04/09/sprinto-funding-security-compliance-management/)). Builds 200+ integrations via Truto ([Truto](https://truto.one/blog/sprinto-and-truto-partnership/)). No public partner program found |
| **Scrut** (India) | n/a | "Scrut Teammates" agents for evidence validation, not capture | ~$20M raised, $10M Apr 2024 ([YourStory](https://yourstory.com/2024/04/scrut-automation-funding-lightspeed-massmutual-ventures-endiya)). **Has a technology-partner program** ([scrut.io/partners](https://www.scrut.io/partners/program-overview)) |
| **Comp AI** (open source, AGPL) | low / self-host | **Yes: scheduled browser automations, log in through 2FA, shared org logins** ([docs](https://www.trycomp.ai/docs/browser-automations.md); [Help Net Security 2026-04-07](https://www.helpnetsecurity.com/2026/04/07/comp-ai-open-source-compliance-platform/)) | Free and self-hostable, so it sets the price floor |
| **Strac** | n/a | **Yes: Chrome extension "drives your admin panels — AWS, Okta, GitHub, Workspace"**, watermarked screenshots ([Strac](https://comply.strac.io/evidence-agent)) | Closest to AWOS's "user's own browser" model |
| **Screenata** | quote-based | Yes: browser extension with DOM snapshots, macOS/Windows desktop recorder, signed and hashed artifacts ([pricing](https://screenata.com/pricing)) | Now a full platform, not just an add-on |
| Secureframe, Thoropass, Hyperproof, AuditBoard | — | Not found. Hyperproof says its "AI agents automate evidence collection" with no detail ([Hyperproof](https://hyperproof.io/product/hyperproof-ai/)) | AuditBoard sold to Hg for $3B+ (enterprise end) |

**Auditor acceptance.** No AICPA statement on agent-captured evidence was found. The
practitioner view is that auditors judge the artifact, not who captured it. It needs a
visible timestamp, system and account in frame, scope, and a link to the control. But
capture automation does not prove period coverage or population completeness
([Screenata](https://screenata.com/resources/blog/do-auditors-accept-ai-captured-screenshots-for-soc-2)).

**Trust shock.** In March 2026 Delve (YC, AI-native SOC 2, $32M from Insight) was
accused of fabricating evidence and templated reports. It was reportedly asked to leave
YC ([certpro summary of TechCrunch, 2026-03-21](https://certpro.com/delve-fake-compliance-allegations/)).
Since then buyers and auditors are **more suspicious of "AI-generated evidence"**, and
established players benefit ([Sacra](https://sacra.com/research/vanta-at-300m-year/)).
A newcomer must show provenance (signed, timestamped, replayable capture) from day one.

## 3. Market, buyer, willingness to pay

- Compliance-automation ("trust stack") market is ~$1.3B, growing 18-22%/yr. Broad GRC
  software is ~$23B ([SaaSRise](https://www.saasrise.com/news/vanta-reaches-300m-arr-as-truststack-market-swells-to-13b-cfd77d0d-42d1-4a87-921e-691a694ff7fc); [yespress](https://yespress.io/soc-2-is-a-23-billion-business-now)).
  Vanta ~$300M ARR and ~15-16k customers in Apr 2026 ([Sacra](https://sacra.com/c/vanta/)).
- First-year SOC 2 total spend: $8-20k (platform + audit), $25-60k with a consultant,
  $45-100k+ full-service ([getagency](https://blog.getagency.com/articles/how-much-does-soc-2-compliance-cost-2026)).
- **Buyer:** Head of Security / GRC lead, or the CTO at <100 people. Budget sits in
  the compliance line already spent on the platform, so a gap-filler add-on competes
  with "ask Vanta's agent" (bundled at no extra cost).
- **Willingness to pay (inferred, not validated):** a gap-filler is worth about the
  hours saved. 50 h/quarter × $50-100/h ≈ $10-20k/yr in US, but more like $1-3k/yr
  in India. Realistic add-on price is $200-800/month per company, and lower once
  incumbents bundle it.
- **Underserved segment:** companies that use **consultants/vCISOs instead of a
  platform** (or Excel + Drive). Here the consultant is the buyer and gains margin per
  client if evidence capture is automated. This is the most plausible channel for a
  tiny vendor (one consultant brings 5-20 clients).
- **India angle:** Indian SaaS selling to US/EU needs SOC 2/ISO 27001. Sprinto and Scrut
  sell to them. Scrut has a technology-partner track, which is a possible integration
  route (export to Scrut evidence). They are more likely to build or buy agents
  themselves once Vanta's feature is table stakes, so partnership leverage is low.

## 4. Fit for AWOS

| Factor | Fit | Why |
|---|---|---|
| Technical match | **High** | AWOS already logs in, navigates, downloads and verifies, and pauses for OTP. Evidence capture is mostly read-only, so it is low blast radius. Results can be verified (does the screenshot show setting X?) |
| Differentiator | **Medium** | Runs locally in the user's real browser session. No cloud browser holds admin cookies, and hardware keys and biometrics work because the human is right there (Vanta's remote browser cannot use them). Strac's extension already offers similar locality |
| Trust of tiny vendor | **Low-medium** | Admin-console access to AWS/Okta/GitHub is the most sensitive possible. Local execution plus open/inspectable code plus a read-only account helps. Still, security teams will ask for SOC 2 *of AWOS itself* (a chicken-and-egg problem) |
| Mac vs Windows | **OK** | Tech-company engineers and GRC staff are mostly on Macs (anecdotal; no current survey found). The work happens in browser consoles anyway, so the OS matters little |
| ToS risk | **Low** | The customer automates their own accounts with their own credentials in their own browser. Main risk is a console flagging bot behaviour or MFA re-prompts, not ToS |
| Type II value | **Limited** | Screenshots alone don't prove a control held over time. The real value is scheduled capture plus populations (user lists, ticket exports), which APIs often do better |
| Path to general computer use | **Medium** | It exercises many web consoles, auth and verification, all of which carry over. But it is read-only and browser-only, so it doesn't push desktop control or write actions |

## 5. Selling from India as a tiny team

- **First 10 customers through engineering contacts:** target (1) engineering leads at
  50-300 person Indian/US SaaS companies going through their *first* SOC 2 or ISO 27001,
  and (2) 2-3 independent compliance consultants/vCISOs, each with several clients.
  Engineering contacts are the people forced to take screenshots, but often not the budget
  owner. Ask them to introduce you to their GRC lead or CTO.
- **Pilot structure:** 4 weeks, fixed scope of "the 20-30 evidence items your platform
  marks manual". AWOS runs on the engineer's own Mac with a read-only account. Output goes
  into a folder named by control ID plus a signed manifest. Success is measured by
  *auditor accepts without rework* and *hours saved vs last cycle*.
- **Price:** pilot $500 (India ₹25-40k) credited to year one. Then $3-6k/yr per company
  (US) or ₹1.5-3 lakh/yr (India), or $150-300/month per client for consultants.
  Expect long cycles, since sales are tied to the audit calendar (a pilot only lands
  1-3 months before audit).
- **Risks to selling:** the incumbent bundle ("Vanta does this now"), security review of
  a 1-2 person vendor, and the post-Delve suspicion of AI evidence.

## 6. Verdict

| Dimension | Score /10 | Reason |
|---|---|---|
| Business today | **4** | Real but shrinking gap. Vanta, Comp AI, Strac and Screenata already ship browser evidence agents. Add-on price ceiling is low |
| Ease of first sale | **4** | Warm engineering contacts help, but the buyer is GRC/CTO, the timing depends on the audit calendar, and a tiny vendor faces a security review |
| Technical fit | **8** | Read-only, verifiable, web consoles, OTP pauses, local credentials. It matches the prototype almost exactly |
| Path to general computer use | **6** | Broad web-console coverage and verification carry over. It is browser-only and read-only |
| Risk (10 = low risk) | **4** | Bundling by incumbents, trust/AI-evidence backlash, and seasonal demand |

**Overall: not a strong first paying business on its own (≈5/10).** Best use:
run 8-10 interviews. If consultant-run (no platform) companies or Vanta users with
hardware-key/SSO-only consoles show strong pull, run a cheap paid pilot.
Otherwise use evidence collection as a showcase workload for local computer use,
not as the company's wedge.

## 8 customer-interview questions

1. Walk me through your last audit. Which evidence items did you capture by hand, and how many hours did it take, and whose hours?
2. Which compliance platform (if any) do you use, and which controls does it still mark "manual" or need uploads for? Can you show me the list?
3. Have you tried Vanta's screenshot agent (or Comp AI/Strac)? What happened, and if you haven't, why not?
4. Did your auditor ever reject or ask to redo a screenshot or export? What was wrong with it?
5. Would your security team let a tool drive your AWS/Okta/GitHub admin consoles? What would it need (local-only, read-only account, open source, our SOC 2)?
6. Which consoles require hardware keys, SSO-only, or VPN/IP allowlists, so a cloud browser could not reach them?
7. If this cut those hours to near zero, what would you pay per year, and from whose budget? Would you pay before the next audit?
8. (For consultants) How many clients do you run evidence collection for, how many hours per client, and would you resell or white-label a tool that does it?
