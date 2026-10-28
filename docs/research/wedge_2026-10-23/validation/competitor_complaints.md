# Competitor complaints: invoice and receipt collection tools

Date: 2026-10-23. Desk research only. Nothing was signed up for or posted.
Sources: Trustpilot (read directly, sorted to 1–2 stars), Reddit (read through the
Pullpush archive API, because reddit.com blocks fetching), Hacker News (Algolia),
vendor help pages, and search-engine summaries of Capterra/G2/GetApp. Capterra,
G2 and GetApp return 403 when fetched directly, so I saw those reviews only as
summaries. Treat them as weaker evidence. Quotes are short and dated. German
quotes are translated, with the original meaning kept.

Strength key: STRONG = 6 or more independent sources, MEDIUM = 3–5, WEAK = 1–2.
"Independent" means different authors. Vendor marketing is not counted.

---

## 0. Bottom line

1. **The pain is real, and nobody has fully solved it.** The fetch step is
   still unreliable at every incumbent. Hubdoc dropped most of it in 2022.
   AutoEntry's fetch is barely used. A Dext user said in June 2026 that Dext
   is "about to kill off" Fetch (not confirmed). GetMyInvoices (GMI) works for
   many people, but "portals fail all the time" is its most common 1-star complaint.
2. **"Logins stay on your machine" is no longer a differentiator.** At least
   seven products already make that claim: Invoice Radar (350+ portals),
   Booksmate (500+), the GetMyInvoices desktop app, Sapoto, Severino (Chrome
   extension), the Taxing.app extension, and beleg-box.de. Local-first has
   become the expected minimum. It does not set a product apart.
3. **The gaps that remain:** (a) silent failures, meaning nobody notices a
   missing invoice until the accountant does; (b) the long tail of portals and
   portal changes, which needs self-healing rather than hand-written scrapers;
   (c) a smooth way to hand MFA back to the user; (d) bank and card
   statements; (e) prices that don't jump 2–3×, and no annual-contract traps.
4. **Region skew.** Most of the local-first alternatives are German or
   Austrian, and most of the Reddit discussion is in German subreddits. I found
   **no** evidence of a tool that covers Indian vendors (Airtel, Jio, GST
   portal), and no Indian users complaining about this. That gap is untested,
   not proven.

---

## 1. Tool by tool: does it really log into portals, and what breaks

| Tool | Logs into portals? | Coverage claimed | Main breakage reported |
|---|---|---|---|
| GetMyInvoices (DE) | Yes. Cloud scrapers plus a Windows/macOS desktop app that "retrieves your invoices locally on your computer instead of via our servers" | "10,000+ portals" | Connections drop, UI changes break portals, re-entering logins, bank connections, OCR, price hikes |
| Dext (Receipt Bank) Fetch | Yes. Weekly cloud check using the client's portal login | "over 2000 suppliers" (older Dext page). Users call the list "limited" | "majority of the time it doesn't connect" (2023); Microsoft/AT&T fail (2025); breaks when a password changes or 2FA is switched on (Dext help); possible shutdown (2026) |
| Hubdoc (Xero) | Mostly no longer. Xero retired "standard fetch" on 27 Apr 2022 because connections were "often blocked by banks and online suppliers". Only API links remain (BofA, Wells Fargo, Stripe) | "only supports i think 2 banks now" (user, 2024) | Users still expect fetch and find it missing; duplicates; VAT errors |
| AutoEntry | Had "autofetch". Reviews say it doesn't work | Not stated | "hasn't been working for me … I have since paused our subscription" (Capterra summary); 2FA lockout for a month |
| Tailride (ex-GetInvoice) | Mainly email/inbox, with portals listed on the Xero app page | Not stated | One 1★ review: "sending the completely wrong totals to Xero" |
| Ramp | Amazon only (US), using cardholder login; Amazon Business/Uber/TravelPerk via API; Gmail scan | 1 merchant via login | No portal complaints found. It only covers card spend |
| Brex / Expensify / Zoho Expense | No portal login. Gmail add-on, forwarding, SMS, card matching | n/a | Out of scope. They match receipts to cards; they don't fetch from portals |
| Datamolino / Veryfi / Paperless-ngx | No. OCR or email intake only | n/a | n/a. Self-hosters ask for a "GMI alternative" because Paperless can't fetch |
| Invoice Radar (AT) | Yes, locally on the desktop, with an open plugin system | 350+ | "Fetches fail silently, portal connections drop constantly" (1★, 2026-04-06) |
| Booksmate | Yes, locally | 500+ (aggregator) | No negatives yet (2 reviews). $19/49/99 per month |

### Complaint themes, counted across all tools

| Theme | Independent sources | Strength |
|---|---|---|
| Portal connections fail, drop, or silently miss invoices | 16 | **STRONG** |
| Price hikes, annual-contract lock-in, refund refusals | 15+ (GMI 7, Dext 11 on Trustpilot) | **STRONG** |
| Slow or absent support | 13 on the Dext page alone, plus 4 for GMI | **STRONG** (generic) |
| MFA/2FA, bank security, and portal changes block fetch | 9 | **STRONG** |
| Worried about giving logins to a cloud service | 8 for (see §3), 4 against | **MEDIUM** |
| OCR or extraction accuracy (wrong totals, duplicates, VAT) | 9 | MEDIUM (not about fetching) |
| Vendor/bank not covered | 7 (see §2) | MEDIUM |
| Captchas named explicitly | 3 (micro_saas thread x2, r/selfhosted 2021) | WEAK |

### Evidence for "fetch fails" (selection)
- GMI, 1★, 2025-09-30: "Connexions with portals fail all the time. … Support takes 5 to 10 days to answer." https://www.trustpilot.com/review/getmyinvoices.com
- GMI, 1★, 2025-02-25: "the first portal stopped working suddenly because of a change in the portals UI." (same page)
- GMI, 3★, 2025-08-20: "many of the portal connections I have had to enter and delete and re-enter a few times." (same page)
- GMI, Reddit, 2026-04-12: "was using Getmyinvoices but too unreliable overall" https://reddit.com/r/selfhosted/comments/1erzhmy/get_your_invoices_automatically_alternative/ofrtgpt/
- GMI, Reddit, 2025-10-22 (founder of a competitor): portal management "is so tedious and often doesn't work". https://reddit.com/r/Unternehmer/comments/1od3wwy/
- GMI, Reddit, 2025-11-29: "We built our own. … I call our program 'GMI that actually works'." https://reddit.com/r/selfhosted/comments/1ilo5ip/
- Dext, 1★, 2025-12-31: "Connecting to web portals such as Microsoft and AT&T does not work." https://www.trustpilot.com/review/dext.com?stars=1&stars=2
- Dext, 1★, 2025-08-25: "the fetch option is hopeless". (same page)
- Dext, Reddit, 2023-07-22: "taking ages to connect (with the majority of the time it doesn't connect." https://reddit.com/r/Accounting/comments/156p9zk/
- Dext, Reddit, 2026-06-05: "I use DEXT but they're about to kill off their Fetch service due to increased security on these platforms." https://reddit.com/r/MicrosoftFlow/comments/1tvufvb/beginner_question_what_should_i_automate_first/opvvnzg/ (a single user; I found no Dext announcement)
- Hubdoc, Reddit, 2026-07-16: "the fetch reliability being hit or miss is one of the more common Hubdoc complaints." https://reddit.com/r/xero/comments/1uw59fh/
- Invoice Radar, 1★, 2026-04-06: "Fetches fail silently, portal connections drop constantly." The reviewer only found the gaps after the accountant flagged them. https://www.trustpilot.com/review/invoiceradar.com

### Evidence for "MFA and security kill fetch"
- Xero, official notice (2022): connections "have become outdated and are often blocked by banks and online suppliers". https://blog.xero.com/product-updates/hubdoc-importing-docs-changing/
- Reddit, 2025-10-09: "Basically what Hubdoc did before 2FA became the norm." https://reddit.com/r/Bookkeeping/comments/1o150op/so_many_portal_logins_so_many_statements/nih9dxv/
- Reddit, 2026-06-14 (CPA firm): "every month we still need to call clients for MFA/2FA codes to download statements." https://reddit.com/r/Accounting/comments/1u58s0h/
- Reddit r/micro_saas, 2026-06-29, three separate commenters: "every vendor has a different login flow, changes their ui randomly, and throws 2fa or captchas into the mix"; "That version probably turns into a maintenance company." https://reddit.com/r/micro_saas/comments/1uic9iw/
- Dext help: a connection breaks if "the portal password changes or two-factor authentication is turned on".

---

## 2. Vendors and portals people say are NOT covered or keep failing

- **Cloud and SaaS billing (portal-only, no PDF by email):** AWS, Vercel, GitHub, Google Cloud, Twilio, Slack (2026-08-12, https://reddit.com/r/AccountingDepartment/comments/1vm8o9g/); AWS, Figma, Notion (2026-06-29, micro_saas thread); 20+ SaaS tools with "3-15 invoices a month EACH" (2026-08-13).
- **Ads platforms:** Google Ads (Dext user, 2026-06-05; GMI user says Amazon and Google Ads are the reason to pay). Meta and LinkedIn (a Chrome-extension builder, 2026-05-01).
- **Amazon:** GMI says Amazon is its most-connected source (r/u_GetMyInvoices, 2025-06-23). Ramp supports only Amazon. It is the most-requested portal everywhere.
- **Telecom:** AT&T (fails in Dext, 2025-12-31), Telekom, Freenet (German users have to click through menus), and Brightspeed (billing page unavailable, 2026-06-19).
- **Utilities and municipal portals:** Berliner Wasserbetriebe and BSR (property manager, 2026-02-24); "stadtwerke" (2026-06-27); US property managers want utility portals → PMS (2025-11-12).
- **Microsoft** (fails in Dext). **Logistics and wholesale:** FedEx, Amazon Business, Sysco, McKesson (2026-03/07 posts).
- **Banks:** Citi ("QBO can't pull statements from Citi anymore", 2025-10-11), Scotiabank, BMO (Canada), TD/RBC (older Hubdoc docs); GMI "only offer US banks" (1★, 2023-07-28).
- **India:** no complaints or tools found (Airtel, Jio, GST portal, AWS India). Only GST e-invoice JSON download exists. **Gap in the evidence, not a confirmed opportunity.**

---

## 3. Giving login credentials to a cloud service

**For a "logins stay on your machine" pitch (8 independent sources, MEDIUM):**
- GMI 1★, 2023-07-28: "I'm not sure I would trust them to store properly my sensitive credentials for all portals." https://www.trustpilot.com/review/getmyinvoices.com?stars=1&stars=2
- Reddit, 2025-10-22: "The only advantage this browser extension would have is that you don't have to share your login data. Security-wise I find GMI really difficult." https://reddit.com/r/Unternehmer/comments/1od3wwy/ (comment nks2mp5)
- Invoice Radar founder, 2026-06-30: "GetMyInvoices was out of the question, because you have to hand over all your login data to the cloud." https://reddit.com/r/StartupDACH/comments/1ujrzja/ (ouqknga)
- Reddit, 2026-06-30: "I wouldn't trust anyone else with my credentials and MFA details." https://reddit.com/r/micro_saas/comments/1uic9iw/ (ounhmrt)
- Reddit, 2024-08-15: "I would never use an automation like this to access my bank unless my bank offers an API that can be restricted to view only". https://reddit.com/r/selfhosted/comments/1erzhmy/
- Reddit, 2026-08-22: the advice frames the goal as solving it "without handing over your main login". https://reddit.com/r/Invoice/comments/1vu3i85/
- A tax firm in 2026-05 vetoed GMI over where its servers are located (https://reddit.com/r/Steuerberater/comments/1tfsq9e/).
- Reviewers of local tools name it as the reason they bought: "It's local, so no need to share credentials with external servers" (Invoice Radar 5★, 2026-01-22); "Love the fact credentials remain on my machine" (Booksmate 5★, 2026-04-06).

**Against (4 sources):** thousands of Dext/GMI users hand over credentials without
complaint. In the tax-firm thread, commenters called the boss "paranoid". One
comment says "Data protection? I couldn't care less here" (r/FinanzenAT,
2026-09-29). Another says the risks are "far too small" to give up the benefit of
Claude (r/StartupDACH, 2026-07-07). A GMI user prefers cloud: "I don't want a
browser extension; I want to set it up once" (2025-10-22).
**Read:** privacy works as a reason to buy for a minority (DACH and privacy-minded
users). Most users care more that the tool runs unattended and reliably. A local
agent has to run without the user watching (a scheduled run, laptop asleep → resume),
or it loses to the cloud. Invoice Radar's own blog admits "your computer needs to
be available".

---

## 4. Pricing users mention, and why they churn

- **GetMyInvoices:** "from 228 to 444 net" per year for the Small plan, no new features (2024-09-18, 2 users: https://reddit.com/r/selbststaendig/comments/1fj8ye5/); "cheekily raised prices x3" (2026-01-26); "paying €1 per invoice" (2025-12-01); line-item extraction "almost 5€ per invoice" (2025-11-29); "€19.99 for mail + 2 portals", replaced by an MCP server (2026-09-16). Listed plans: about €19/mo (20 docs, 5 portals) up to €179/mo (500 docs). Churn reasons: price hikes, an annual contract billed monthly ("I need to pay 11 more months", 1★ 2025-07-09), refund refused, unreliable portals, bad OCR, poor UX. People also cancel to build their own with Claude (2026-09-28/29).
- **Dext:** 11 of 20 recent 1★ reviews are about cancellation or price: "recurring pricing changes", "substantial exit costs", "renew your contract for another 12 months". "Dext has crept up significantly … priced for mid-market" (r/ukaccounting, 2026-05-18); "will cost you a ton if you have a bunch of clients" (r/taxpros, 2024-02-07).
- **Hubdoc:** free with Xero (about $15/mo otherwise), so price is not the complaint. Lost fetch is.
- **Booksmate:** $19 / $49 / $99 per month. **Taxing.app:** a $250 lifetime plan was offered, with the pitch of being "10x cheaper" than GMI. Willingness to pay from personal users: "10 to 15" per year, with a preference for lifetime (r/selfhosted, 2024-08-14).
- **Worth it?** Happy users exist: GMI 4.4★ from 854 reviews; "24 times less manual work vs before" (5★, 2026-03-26). Unhappy users mostly say the price/reliability ratio is wrong, not that the job isn't worth paying for.

---

## 5. AI browser-agent products for invoice collection (2025–2026)

| Product | What | Traction evidence | Strength |
|---|---|---|---|
| Twin "Invoice Operator" + Qonto (FR) | OpenAI CUA agent in a server-side Chromium. Finds transactions with no invoice, fetches the invoice, reconciles it. User types logins when asked | Launched 2025-03-27 (TechCrunch); "1,500 beta testers"; careers page claims "deployed to 500k SMBs, 95%+ accuracy" (self-reported) | MEDIUM: the strongest sign of traction, but bundled with a bank |
| Money Forward (JP) | "AI Invoice Download Agent" for its accounts-payable product. Reads invoice emails, follows download links, uses passwords | Launched 2025-09-30 to "a limited number of users" | WEAK |
| Skyvern (YC, open source) | General browser agent with a "bulk invoice downloader" cookbook and 2FA/TOTP | No named invoice customer. A Skyvern employee promotes it on Reddit. One freelancer: "claude + skyvern handled the login/2fa/download dance across sites that changed weekly and was pretty solid" (2026-04-16) | WEAK–MEDIUM |
| Anchor Browser, Gabriel Operator, Deck, Sapoto, Severino, beleg-box, Taxing.app, DocGenie | Agents or extensions for portal downloads | Mostly founders posting on Reddit/HN, with no user reviews found | WEAK |
| GMI "AI agent" | A property manager was told GMI has an AI agent for portals | Reply: "Getmyinvoices does not use AI and is not an AI agent" (2026-02-24) | n/a |

Reality check from builders: "I would not start with an agent that logs into
every vendor portal. That version probably turns into a maintenance company."
(r/micro_saas, 2026-06-29). There is also a growing do-it-yourself crowd
("Claude saved me more than it costs", r/ClaudeAI 2026-09-28). This is both a
competitor and a sign of demand.

---

## 6. Evidence that the problem is ALREADY SOLVED (read before building)

1. GMI 4.4★ / 854 Trustpilot reviews, 10,000+ portals, a local desktop app with 2FA prompts, and an AmEx Platinum Business perk. "getmyinvoices is worth its weight in gold for this" (r/selbststaendig, 2026-07-10).
2. Local-first is already shipped by Invoice Radar (4.4★/20), Booksmate, Sapoto, Severino, and the GMI desktop app.
3. Twin/Qonto bundle the agent version into a bank, with large claimed reach.
4. Many invoices can come by email instead: "a dedicated billing inbox … kills most of the log-in-and-download jobs" (2026-08-22). QBO fetches statements for "most banks". German e-invoicing (e-Rechnung) is making the problem smaller ("the problem goes away by itself", 2026-06-30).
5. Several bookkeepers say collecting receipts "is not a bookkeeper responsibility" (r/Bookkeeping, 2025-05-17). In the US, cash-basis bookkeepers often skip receipts entirely.
6. A crowded field: "Not a solved problem, but the market is full: Getmyinvoices, Belegbox, Lexoffice Scan…" (r/Gastronomiebetreiber, 2026-04-09). A German subreddit called one more entrant "the 729th tool of this kind" (2026-06-28).

**Not solved:** reliable unattended fetching across the long tail, detecting
missing invoices before the accountant does, bank and card statements behind
MFA, and fair pricing. A new product would have to win on **reliability and
completeness** (every expected invoice accounted for, failures reported loudly,
portals that repair themselves). "Local" alone is not enough.

---

## 7. Limits of this research
- Capterra, G2, GetApp and Software Advice blocked direct fetching; I relied on search summaries of them.
- Reddit was read through an archive. Thread selection leans toward German-speaking subreddits and founder self-promotion; I excluded promotional posts from the counts.
- I did not confirm that Dext Fetch is shutting down. I found no complaints about Zoho, Expensify or Brex fetching, because they don't fetch from portals.
- No data at all for India.
