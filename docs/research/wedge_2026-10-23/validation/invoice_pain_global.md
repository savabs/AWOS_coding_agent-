# Invoice-collection pain: what real people say (global)

Date: 2026-10-23. Question: is "a local agent that logs into SaaS, vendor, cloud and utility
portals each month, downloads every invoice, files it, matches it to the card statement and
reports what's missing" a real, paid-for pain?

## How this was gathered (and what failed)

- **Reddit direct was blocked**: reddit.com, old.reddit.com, `.json` endpoints and `site:reddit.com`
  web searches all failed or returned no Reddit pages. I reached Reddit posts through the public
  **Arctic Shift archive API** (arctic-shift.photon-reddit.com). It returns the original post and
  comment text with dates and permalinks. Its post search worked but its comment full-text search
  timed out, so I found threads by searching post titles and bodies, then pulled their comments.
  Coverage is therefore partial: r/Bookkeeping, r/Accounting, r/startups, r/CFO, r/xero,
  r/smallbusiness and r/sysadmin were searched; r/SaaS and r/msp hit rate limits.
- **Hacker News**: searched through the Algolia API (comments).
- **AccountingWEB, Capterra, G2**: blocked (403). Review-site points below come second-hand through
  search summaries and are marked as such.
- I signed up for nothing and posted nothing.

**Caveat on the evidence pool.** In 2026, r/Bookkeeping and r/Accounting are full of founders
"validating" document-collection tools. Many threads below are partly or wholly that. Regulars
call it out: "Incoming alt account responding to this post suggesting some start up vibe coded
shitty product" (19 upvotes) and "This sub is turning into a ad platform" ([r/Accounting, 2026-07-14](https://www.reddit.com/r/Accounting/comments/1uwj739/company_that_pulls_invoices/)).
I weighted top-voted replies from regulars above original posts that read like discovery bait.

Evidence grades: **STRONG** = 4+ independent threads, including organic (non-vendor) voices.
**MEDIUM** = 2–3 threads, or one high-signal thread. **WEAK** = 1 thread, or second-hand only.

---

## Q1. How painful is it? How many vendors, how many hours, and who does it?

**Finding 1.1: For founder-run small companies it is a real but modest chore, roughly 1 to a
few hours a month across 20–30 vendors. MEDIUM (5 threads).**

- "I spend about an hour every month logging into all our vendor accounts to grab receipts for
  the accountant. Two factor auth, SMS codes, all that." The founder does it.
  ([r/startups, 2026-04-29](https://www.reddit.com/r/startups/comments/1sz5jvo/monthly_receipts_i_will_not_promote/))
- "I have three businesses - each of them uses 20-30 SaaS/Hosting/Marketing services… I spend
  hours every month on this shit." Founder again.
  ([r/startups, 2025-10-16](https://www.reddit.com/r/startups/comments/1o7zfte/stripe_please_let_us_autoforward_invoices_to_our/))
- "I need to upload invoices every month from all ~20 SaaS products I subscribe to… I have
  browser-automated all of these laborious ones."
  ([HN, 2017-07-16](https://news.ycombinator.com/item?id=14783826))
- "Our bottleneck is not paying invoices… but filing incoming invoices for our accountant."
  ([HN, 2021-09-03](https://news.ycombinator.com/item?id=28408820))
- "I collect all the receipts, bank account statements etc and send them to my accountant… It
  takes me an hour or two." ([HN, 2024-04-18](https://news.ycombinator.com/item?id=40079456))

**Finding 1.2: For companies with telecom and utility accounts, one post claims it is "half of
someone's job", but the community doubted it. WEAK (1 thread, disputed).**

- "We have portals with Comcast, Charter, Dish, Utility companies… Most use multi-factor
  authorization… it is half of someone's job. I am looking for a vendor that we can pay."
  ([r/Accounting, 2026-07-14](https://www.reddit.com/r/Accounting/comments/1uwj739/company_that_pulls_invoices/))
  The replies: "20/hrs a wk seems insane just downloading invoices", and the alt-account accusation
  above.

**Finding 1.3: Who does it.** Founders or owners (startups threads, HN), an ops team (Cloudflare
example in Q2), AP clerks at multi-site firms, and outside bookkeepers who hold client logins
(2FA threads). Outside bookkeepers mostly don't log into SaaS portals. Their main pain is chasing
clients for documents (see 2.4). **MEDIUM.**

## Q2. What specifically hurts?

**2.1 MFA/2FA on portals is the most consistent complaint. STRONG (5 threads).**
- "2FA is the bane of my existence. So frustrating for businesses with multiple employees who
  need access to an account."
  ([r/Bookkeeping, 2025-11-13](https://www.reddit.com/r/Bookkeeping/comments/1ovdd15/question_how_do_you_guys_deal_with_2fa_with/))
- A bookkeeper's script of texting a client for codes: "Client: 0382628 / Me: Wait it timed
  out…ok try again" (same thread).
- "every month we still need to call clients for MFA/2FA codes to download statements."
  Replies: "Borrowed bank logins age badly with MFA."
  ([r/Accounting, 2026-06-14](https://www.reddit.com/r/Accounting/comments/1u58s0h/how_do_cpa_firms_automate_bank_statement/))
- Both r/startups threads above, and the Comcast/utilities thread, name 2FA explicitly.

**2.2 Invoices that live only in a portal and are never emailed. MEDIUM (4 threads).**
- "Take OpenAI as an example. They don't send receipts through email… Every month, I have to go
  through the steps… (x3 times)." Also: "I can't even outsource this work as it's usually behind
  2FA." ([r/startups, 2025-10-16](https://www.reddit.com/r/startups/comments/1o7zfte/stripe_please_let_us_autoforward_invoices_to_our/))
- "Some send them in emails but others dont." ([r/startups, 2026-04-29](https://www.reddit.com/r/startups/comments/1sz5jvo/monthly_receipts_i_will_not_promote/))
- "now you have to create an account on some portal so that you can download your invoice. So
  that's one userid/password combo per business relationship."
  ([HN, 2018-12-04](https://news.ycombinator.com/item?id=18596025))
- Seat-gated billing access: "the one feature we wanted was for our accounts team to have their
  own login so the ops team didn't have to download invoices every month… required an enterprise
  plan… $4,000 a month." ([HN, 2022-06-21](https://news.ycombinator.com/item?id=31821742))

**2.3 Shared logins and terms of service. MEDIUM (2 threads).** The top reply (28 upvotes) to the
2FA thread: "It's against most services TOS to be using the clients' login details to access."
Regulars insist on their own accountant login
([r/Bookkeeping 2025-11-12](https://www.reddit.com/r/Bookkeeping/comments/1ovdd15/question_how_do_you_guys_deal_with_2fa_with/);
[r/Accounting 2026-06-14](https://www.reddit.com/r/Accounting/comments/1u58s0h/how_do_cpa_firms_automate_bank_statement/)).
This supports running the agent in the owner's own browser rather than a shared cloud login.

**2.4 Chasing people (clients and employees) for receipts is the bigger, louder pain, but it is
not portal-shaped. STRONG (6+ threads, many of them vendor bait).**
- "Is it just me, or is getting receipts from clients still a nightmare in 2026" (47 upvotes):
  "My clients… still just send me blurry photos via text or keep them in a shoebox."
  ([r/Bookkeeping, 2026-03-15](https://www.reddit.com/r/Bookkeeping/comments/1run75a/is_it_just_me_or_is_getting_receipts_from_clients/))
- "I'm spending half my week chasing down small business clients via text and email."
  ([r/Bookkeeping, 2026-05-21](https://www.reddit.com/r/Bookkeeping/comments/1tj7d1u/anyone_else_losing_their_minds_getting_receipts/))
- Similar: "Am I a bookkeeper or a full-time text-message chaser" (r/Accounting 2026-07-15),
  "How much time does your firm actually spend chasing clients for documents?" (r/Accounting
  2026-06-09). Both read partly like discovery posts.
- A portal-scraping agent does not fix paper receipts from tradespeople. That is a different product.

**2.5 Card-statement reconciliation and missing-invoice reports.** This came up only as a side
note. Nobody organically asked for "tell me which charges have no invoice". The r/Bookkeeping
workaround (code unknowns to an uncategorised or non-deductible account, then send one list) is
the de facto "missing report". **WEAK.**

## Q3. What have they tried, and why didn't it stick?

| Tried | What happened | Evidence |
|---|---|---|
| **Hubdoc auto-fetch** (logged into supplier/utility portals) | Xero **retired it on 27 Apr 2022**: connections "often blocked by banks and online suppliers… performance has declined to a point where it no longer meets the needs of Xero or our customers" ([Xero blog, 2021-11-30](https://blog.xero.com/product-updates/hubdoc-importing-docs-changing/)). A 2025 Capterra review (second-hand) misses it. 2026 Reddit posts still talk about "post-Hubdoc" collection. | STRONG (official) |
| **GetMyInvoices** (10,000+ portals, also a desktop app that keeps credentials local) | Second-hand reviews: portal connections "occasionally drop… especially with two-factor-protected portals"; "many of the offered portals don't work properly" (GetApp); "pay between 50c and 1€ per invoice" (G2). An HN user meant to try it but "haven't found the time to test it yet" ([HN 2021-09-03](https://news.ycombinator.com/item?id=28408820)). | MEDIUM (second-hand) |
| **Dext / Hubdoc / QBO app** (capture) | "I've tried Dext, Hubdoc, and the QBO app" and clients still don't comply (1run75a). Dext pricing complaints ("expensive enterprise suite", "billable credit structures like AutoEntry", 1tj7d1u). | MEDIUM |
| **Ramp / Brex / Expensify** | Loved for receipt capture and card coding: "Ramp is the best platform I've ever used"; "you'd need to take it from my cold, dead hands" ([r/CFO, 2025-11-04](https://www.reddit.com/r/CFO/comments/1onz6wc/ramp_vs_brex_vs_rippling_for_cc_spend_accounting/)). Ramp scans Gmail and connects Amazon Business, Uber and others ([Ramp docs](https://support.ramp.com/employee-mailbox-integration)). Its docs mention no portal login for SaaS invoices that are never emailed. | STRONG that it's the incumbent |
| **Email rules / dedicated invoice inbox** | Most common advice: "Just make a mailbox rule?"; "pushing every vendor to email invoices/receipts to one dedicated inbox… Not magic, but less logging in" (1sz5jvo); IFTTT to Dropbox ([HN 2018-04-19](https://news.ycombinator.com/item?id=16875521)). Covers the emailed ones only. | STRONG |
| **Home-made scripts / browser automation** | "I have browser-automated all of these laborious ones" ([HN 2017](https://news.ycombinator.com/item?id=14783826)); a phone-bill download script ([HN 2013](https://news.ycombinator.com/item?id=6689439)). A browser-agent builder admits it is "bad at: CAPTCHAs, 2FA, wildly changing DOMs" ([HN 2025-10-07](https://news.ycombinator.com/item?id=45499577)). | MEDIUM |
| **2FA plumbing** | Twilio/Google Voice numbers (~$1–10/mo), Power Automate forwarding codes, shared 1Password/Bitwarden TOTP (1ovdd15). People will build plumbing rather than buy a product. | MEDIUM |
| **People** | "Just give your AP clerk a raise and add it to their duties" (1uwj739). One commenter offered to do it for "$2,000 a month" (an offer, not a sale). | WEAK |

## Q4. Signals of willingness to pay

- **Explicit "I want to pay someone"**: only one post ("looking for a vendor that we can pay",
  utilities/telecom, 1uwj739), and it was treated as a possible plant. **WEAK.**
- **Existing spend**: GetMyInvoices lists about €19–179/month (second-hand: Fastlancer, GetApp).
  Its 27 Capterra reviews suggest a small, mostly German-speaking, paying niche that has existed
  for years. Ramp and Brex are free with a card, which anchors "receipt matching" at $0. **MEDIUM.**
- **Price ceiling signals**: a solo bookkeeper said "without spending hundreds a month on tech
  stacks". Builders replying offer flat $19/month (1tj7d1u). The Cloudflare example shows that
  paying $4k/month to skip downloads was rejected, and another HN user mocked the pain: "they had
  to download a few invoices to save $4k… My heart bleeds for them"
  ([HN 2022-06-21](https://news.ycombinator.com/item?id=31821945)). **MEDIUM:** WTP looks like
  roughly $10–50/month for founders, not hundreds.
- **Old tiny-value signal**: an auto-parse-SaaS-receipts setup "would already save me (or, well, my
  bookkeeper) 5+ hours a year" ([HN 2014-03-04](https://news.ycombinator.com/item?id=7338099)).

## Evidence AGAINST the idea (explicit)

1. **It's small for most people.** "about an hour every month" (1sz5jvo). "Just enough to waste a
   grueling hour on it. Maybe not that much for someone but enough for me." (same thread). "an hour
   or two" (HN 40079456).
2. **"Everything is emailed / fix it at the vendor."** "Who on earth doesn't provide email invoice
   payment receipts in 2026." "Just make a mailbox rule?" (1sz5jvo, 1o7zfte).
3. **Users distrust AI handling their 2FA.** The poster with the pain wrote: "if anyone suggests a
   web crawler or browser extension, that would just bypass/defeat the whole purpse of 2FAs… I'm
   not comfortable with." The top reply: "No, and I don;t want some AI bypassing my 2FA." Another:
   either there is an integration, or "you are willing to allow a tool to impersonate you" (1sz5jvo).
4. **Accountants often don't need the documents.** "Accountants tend to use bank and credit card
   statements for source docs" and "Everything should be through the bank credit card feed. I
   insist on this." (1run75a, 17 and 8 upvotes). Counterpoint in the same thread: Canada (CRA) and
   VAT/GST regimes require source documents. The pain is stronger in VAT countries (UK/EU/AU/CA)
   than in the US.
5. **Portal scraping has already failed at scale.** Xero killed Hubdoc fetch because suppliers
   blocked it. GetMyInvoices reviews complain of broken portal connections. A local, in-browser
   agent avoids some blocking (real user, real session), but every portal redesign is ongoing
   maintenance.
6. **Ramp/Brex are loved and free.** They own receipt matching for the card side. The gap they
   leave is portal-only invoices, which is narrower.
7. **Saturated, cynical channel.** r/Bookkeeping and r/Accounting are flooded with "I'm building a
   document chase tool" posts, and regulars are hostile. Selling through Reddit will be hard.

## Synthesis

| Claim | Grade |
|---|---|
| Logging into portals for invoices is a recurring monthly chore for founder-led SMBs (20–30 SaaS) | MEDIUM |
| 2FA is the sharpest part of the pain | STRONG |
| Some invoices exist only behind portals (OpenAI/Stripe-hosted, telecom, utilities, cloud) | MEDIUM |
| Hours are large (10+/month) for typical small companies | WEAK (one disputed post) |
| Bookkeepers' main pain is chasing *clients/employees*, not portals | STRONG |
| Existing tools (Hubdoc fetch, GetMyInvoices) failed or degraded on portal reliability | STRONG/MEDIUM |
| People pay today for portal fetching | MEDIUM (GetMyInvoices exists; small) |
| WTP is high (> $100/mo) for SMBs | not supported |
| Users trust an AI agent with their logins and 2FA | **contradicted** in the one thread that asked |

**Verdict: MIXED, leaning not validated as stated.** The pain is real and recurring: portal-only
invoices plus 2FA, felt most by founders with 20–30 subscriptions and by firms with
telecom/utility accounts. But for most people it costs about 1–3 hours a month. The obvious
workarounds (inbox rules, Ramp, coding to an uncategorised account) are "good enough". WTP signals
sit around $10–50/month, and the first users who voice the pain also voice distrust of an AI
touching their 2FA.

What could still work:
1. Make the local, user-present design the selling point. "Runs in your browser, you approve each
   2FA prompt, credentials never leave your Mac" answers objection 3 directly.
2. Target VAT jurisdictions (UK/EU/AU/CA), where every invoice is legally needed. GetMyInvoices'
   German base supports this.
3. Target accountants or fractional CFOs who serve many founder clients, where 1 hour × N clients
   adds up.
4. Lead with the "missing invoices vs card statement" report, which Ramp's Gmail scan does not cover.

Validate with 5–10 direct interviews before building. The forum evidence alone is not enough.
