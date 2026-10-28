# Invoice-collection agent for India: what real people say (validation pass)

Date: 2026-10-23. Idea tested: a local agent that each month logs into a company's
SaaS, cloud, telecom and utility portals in the user's own browser, downloads every
invoice, files it, matches it to bank and card lines, and reports gaps. Buyers would be
startup CFOs and CA firms.

**Verdict: MIXED, leaning NOT VALIDATED as pitched.** The underlying pain (chasing
documents) is real and widely acknowledged. But the specific "foreign SaaS invoice / RCM"
angle is weaker than it looks, the price ceiling is very low, and Indian norms on sharing
logins work against the delivery model. The best evidence is in CA forums. Reddit was not
reachable (see Method), so the "real people" sample is thinner than planned.

## Method and limits (read first)

- **Reddit was blocked on every route.** `reddit.com` and `old.reddit.com` returned HTTP 403
  to curl. The web-fetch tool and domain-filtered web search refused the domain.
  `site:reddit.com` searches returned no Reddit hits. The pullpush mirror rate-limited us,
  and Redlib returned 429. **No Reddit post is quoted here.** That is a real gap, because
  r/IndiaTax, r/CharteredAccountants and r/IndianStartups would carry the founder voice.
- LinkedIn and Quora answer bodies were also not readable (Quora returned 403; only search
  snippets came through).
- The sources actually used: CAclubindia forum and expert threads (practitioners and small
  business owners), TaxTMI forum (via search summaries), Quora titles and snippets, TaxGuru
  articles (none had reader comments), ICAI's AI use-case portal, bank forms, and official
  rule text (CBIC, GSTN advisory, DPDP notification coverage).
- Evidence strength: **STRONG** = 3 or more independent first-person threads; **MEDIUM** =
  1–2 threads plus official or secondary support; **WEAK** = vendor marketing or inference only.
- Many hits were vendor blogs (Suvit/Vyapar TaxOne, RichAutomate, AI Accountant, Innoviti).
  They are kept out of the evidence counts.

## Q1. Foreign SaaS, RCM, ITC, 15CA/15CB, bank asks

### What the rules actually say (authoritative or near-authoritative)

1. **Foreign B2B SaaS is under reverse charge.** A foreign vendor with no Indian GSTIN billing
   a GST-registered Indian business falls under reverse charge: IGST Act s.5(3) read with
   Notification 10/2017-Integrated Tax (Rate). The recipient pays IGST in cash through the
   electronic cash ledger, reports it in GSTR-3B Table 3.1(d), and takes the credit in Table 4.
   Summary: TaxGuru, 27 Sep 2026, https://taxguru.in/?p=1083001
2. **The ITC document is the company's own self-invoice, not the vendor's PDF.** CGST Rule
   36(1)(b) allows ITC on "an invoice issued in accordance with ... clause (f) of sub-section (3)
   of section 31, subject to the payment of tax". That clause covers the self-invoice.
   Source: https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter5/rule36_v1.00.html
   **So the vendor invoice is supporting evidence, not the legal key to ITC.** You still need
   it to support the value, the exchange rate and the date, and for audit.
3. **The self-invoice is due within 30 days of receiving the supply** (Rule 47A, Notification
   20/2024-CT, in force from 1 Nov 2024). Time of supply for imported services is the earlier
   of the payment date or 60 days after the vendor's invoice (s.13(3)).
4. **The net cash cost is usually zero.** RCM paid is creditable in the same month, the
   GSTR-2B match is not required, and the Rule 37 180-day payment rule does not apply
   (TaxGuru above). **The real exposure is penalty, interest and audit findings, not lost
   tax.** TaxTMI threads describe demands under s.74 alleging that ITC was taken in breach of
   Rule 36 when the self-invoice was missing, and practitioners argue the issue is
   "revenue-neutral" (https://www.taxtmi.com/forum/issue?id=119290 and id=116173, via
   search summary).
5. **New, and the strongest tailwind:** GSTN Advisory 678 (16 Oct 2026) adds e-invoice
   (IRN) types RCMIMP and RCMDOM for self-invoices. It is aimed at turnover of ₹5 crore or
   more and is sandbox-only for now, with production pending notification
   (https://www.corplawupdates.in/updates/gstn-e-invoicing-rcm-self-invoices-rcmdom-rcmimp-sandbox-october-2026).
   If it goes live, self-invoicing becomes systematic, and the vendor invoice data needed to
   generate it becomes more valuable.
6. **Big vendors increasingly bill from India with GST, which removes RCM for them.** AWS
   bills Indian accounts through Amazon Internet Services Pvt Ltd (AISPL) in INR with GST.
   Google Cloud sells through Google Cloud India Pvt Ltd at 18% GST, and Google warns that
   without a GSTIN on the profile "you may be unable to claim credit"
   (https://support.google.com/paymentscenter/answer/7398224). Zoom invoices India through
   ZVC India Pvt Ltd under e-invoicing and "will not retrospectively adjust" invoices if the
   GSTIN is given late (https://www.zoom.com/en/trust/zvc-india-addendum/).
   **Implication:** for these vendors the pain is a *domestic* one: the GSTIN must be on the
   profile, and the invoice must appear in GSTR-2B (s.16(2)(aa)). That data is already
   visible to CAs from the GST portal.
7. **15CA/15CB, renamed Forms 145/146 from 1 Apr 2026** under the Income-tax Act 2025 and
   Rule 220 (multiple secondary sources agree; the official text was not read). For
   software/SaaS, practitioners generally treat end-user licence payments as **not royalty**
   (no TDS). Whether a Form 15CA/145 filing is still needed for a card payment is
   **genuinely unsettled**. A 2011 CAclubindia post noted that card payments bypass the AD
   bank. A 2024 expert answer says "TDS not applicable form 15CA required"
   (https://www.caclubindia.com/experts/tds-on-midjourney-inc-software-subscription-charges-2918069.asp,
   2 Feb 2024), while the same page's site summary says not required. In practice, card
   payments rarely trigger any bank paperwork.
8. **Wire transfers are different: the bank does ask for invoices.** IDFC First's import
   procedure lists an invoice and "Form 145-146 (for services)" for direct import payments
   (https://www.idfcfirst.bank.in/content/dam/idfcfirstbank/footer/Procedures-on-Import-Export-of-Goods-and-Services-Website.pdf).
   Axis's Form A2 asks for evidence of services availed (https://www.axisbank.com/docs/default-source/default-document-library/forex-card/application-cum-form-a2-for-current-account-customers-for-outward-remittance.pdf).
   But a company wiring money already has the invoice in hand, since it is the trigger for
   payment. **This is not a "missing invoice" pain.**

### Where real people are confused (MEDIUM: 6 independent CAclubindia threads, 2017–2024)

- "Facebook and Google will invoice you from their foreign entities and avoided the need of
  GST" (KONDAL, 6 Sep 2017; https://www.caclubindia.com/forum/how-do-i-manage-facebook-invoices-under-gst--423151.asp). This is wrong today.
- "If you are registered under GST & paid ... to FB and Google directly than, RCM is
  applicable" (Sagar Patel, 17 Jul 2020; https://www.caclubindia.com/forum/payments-paid-to-fb-and-google--551944.asp).
- A DigitalOcean user asked: "Do we have to raise self invoice and mentioned the self invoice
  no only in GSTR 1?" (Mahesh, 9 Jan 2022; https://www.caclubindia.com/forum/import-of-services-rcm-585437.asp).
  A follow-up asking what happens when GST is charged on the invoice went unanswered (15 Feb 2023).
- "Yes, RCM applicable." That one-line answer to a foreign services payment is typical
  (25 Feb 2022; https://www.caclubindia.com/experts/is-rcm-applicable-for-the-foreign-payments--2865144.asp).
- The Midjourney thread above shows confusion over 15CA, with conflicting answers on the same page.
- A CAclubindia article notes that "RCM is one of the first area inspectors dig into"
  (15 May 2025; https://www.caclubindia.com/articles/rcm-and-self-invoicing-challenges-for-indian-corporates-53505.asp).

**Read of Q1:** people are confused about *whether* RCM applies and *which entity bills
them*. Nobody in the threads we could read complains that they *could not obtain the
foreign invoice*. ITC loss from a missing invoice shows up in **domestic** cases: a GSTIN
not on the invoice, or a supplier who did not file. Example: "If airtel is not willing to
co-operate then you have no option but to forgo the itc" (CA Altamush Zafar, 16 Jun 2021;
https://www.caclubindia.com/forum/e-invoice-airtel--576219.asp). Similar threads cover
telephone and electricity bills in an employee's or landlord's name
(https://www.caclubindia.com/forum/itc-on-mobile-bills-standing-in-the-names-of-the-employees-408023.asp).
**Evidence that missing foreign invoices cost real money: WEAK. Evidence of confusion: MEDIUM.**

## Q2. How CA firms collect invoices today

- **Channels:** WhatsApp and email first, then Google Drive, Zoho WorkDrive and Hubdoc.
  "Document collection can take 30–40% of total effort" (Aashok FC Advisory, 26 Mar 2026,
  paraphrased by the fetch tool; https://www.caclubindia.com/forum/how-do-you-collect-documents-invoices-from-clients-every-month--615026.asp).
  A second reply: mostly WhatsApp and email, which "often gets messy over time" (2 Apr 2026;
  that poster then pitched his own AI agent).
- **The same reply adds a counterpoint:** "process and client discipline matter more than
  tools".
- **The CA's own view (ICAI AI use-case portal):** "Each filing cycle requires repeated
  follow-ups, phone calls, and manual data collection from clients" (CA Shikha Sharma,
  undated; https://ai.icai.org/usecases_details.php?id=306). She built her own tool, which
  shows CAs are DIY-ing this.
- **Tools named in threads:** Tally (the default for CAs), Zoho Books (startups), and
  Vyapar TaxOne (formerly Suvit) for data entry and WhatsApp follow-ups. The friction
  between them is itself a pain: "finding it difficult to reconcile zoho books to Tally"
  (Gooly Consultancy, 26 Dec 2025; https://www.caclubindia.com/forum/reconciling-zoho-books-with-tally-614656.asp).
- **Worst clients:** no thread ranks them. The pattern across threads is small owners who
  forward everything on WhatsApp, utility and telecom bills without a GSTIN, and suppliers
  who file late (the GSTR-2B gap; https://www.caclubindia.com/forum/gstr-2b-is-not-showing--582177.asp).
  Vendor blogs claim "~14 days between asking and receiving" and "22–32% of returns filed
  in the last 48 hours" (RichAutomate). Those figures are WEAK and unverified.
- **Hours:** no first-person hour counts were found beyond the "30–40% of effort" estimate.
- **Strength:** the pain of chasing documents is **STRONG in kind** (every source agrees) but
  **MEDIUM in first-person count** (about 4 independent practitioner voices). Note what the
  pain actually is: getting *the client* to send things, mostly paper or WhatsApp bills and
  bank statements. It is *not* mainly about logging into SaaS portals.

## Q3. Would they pay, and how much? Price anchors

| Anchor | Price | Source |
|---|---|---|
| Vyapar TaxOne (ex-Suvit), unlimited automation + WhatsApp follow-ups | **₹10,000 per year** (shown as 50% off ₹20k), plus GST | taxone.vyapar.com/pricing |
| Zoho Books Standard / Professional | ~₹749–899 / ~₹1,249–1,499 per org per month | patronaccounting.com (3rd party) |
| CA monthly GST filing, small business | ₹500–3,000 per month; "₹5000/month and its too much for us" (Bangalore small company, Quora title) | quora.com (titles and snippets) |
| Startup bookkeeping + compliance packages | ₹3,000–10,000 per month (Qwikfilings ₹5k/₹10k; IncorpX from ₹2,999) | vendor pages |
| AI Accountant virtual accounting | ₹4,000–6,000 per month | aiaccountant.com blog |
| Fresher accounts executive | ~₹10,000–20,000 per month (₹20–30k in Gurgaon) | WorkIndia listings |

**Read:** a full-time accounts junior costs about ₹15–20k a month. A CA firm pays about
₹830 a month for *unlimited* automation (TaxOne). A small company resents ₹5k a month for
its *entire* GST service. A tool that only fetches invoices would have to sit **well under
₹1–2k per company per month**, or about ₹10–30k per year for a CA firm, to be an easy yes.
Funded startups with 20+ SaaS vendors and a finance head can pay more (₹3–8k a month is
plausible), but **no first-person evidence of willingness to pay was found** for this
specific job. Strength: anchors MEDIUM, willingness to pay WEAK.

## Q4. Trust in a tool that uses their logins

- **The norm runs against sharing logins.** RBI: never share "Password, PIN, OTP ..."
  (https://website.rbi.org.in/web/rbi/rbi-kehta-hai/safeguards-for-digital-banking). A
  personal-finance blog argues that a CA asking for your income-tax login is a red flag
  (https://arthgyaan.com/blog/do-not-share-login-and-password-of-income-tax-portal-with-your-ca-this-is-what-you-should-do-instead.html).
  Even GST software vendors advertise that they do *not* take portal passwords.
- **But practice is lax.** Commentators say CA firms pass credentials over WhatsApp and keep
  shared spreadsheets of them (koinx.com, vendor; WEAK). Okta found 1 in 5 Indians reuse one
  password everywhere, and 81% worry about identity fraud.
- **OTP friction is real:** the GST portal's OTP goes only to the authorised signatory, and
  a CA body (KSCAA) complained that OTPs "are frequently not delivered"
  (https://taxguru.in/goods-and-service-tax/kscaa-calls-professional-tax-portal-upgrades-resolve-otp-payment-issues.html).
  An agent that must log into 20 portals, each with OTP or 2FA going to the founder's phone,
  hits the same wall. **This argues for running in the founder's own browser** (that is
  where the logged-in sessions already are) and against a CA running it for many clients.
- **DPDP:** the DPDP Rules were notified 13–14 Nov 2025, and data-fiduciary duties apply
  from about May 2027. The ICAI CA Journal now links the confidentiality duty, the IT Act
  standard of "reasonable security practices" and DPDP to CA firms
  (https://cajournal.icai.org/article-details/securing-the-trust-quotient-a-cybersecurity-and-data-protection-framework-for-modern-ca-practices).
  A local-only agent that never sends credentials off the machine is a genuine selling
  point here. It is still untested with buyers.
- **Strength:** WEAK on the direct question. No first-person "I would / would not let an
  agent use my logins" posts were readable.

## Evidence AGAINST the idea (explicit)

1. **Foreign RCM pain is mostly about knowledge, not documents.** Threads ask *whether* RCM
   applies, not how to get invoices. The net tax effect is zero, so a missing foreign
   invoice rarely costs cash.
2. **The foreign share is shrinking.** AWS (AISPL), Google Cloud India, Zoom (ZVC India) and
   Atlassian India LLP bill with Indian GST. Their invoices flow into GSTR-2B, which CA tools
   (TaxOne, Cleartax and others) already reconcile through GST APIs, with no portal login needed.
3. **The CA's real bottleneck is the client.** That means WhatsApp photos, paper bills and
   bank statements, not SaaS portals. "Process and client discipline matter more than tools."
4. **The price ceiling is tiny.** The incumbent automation suite costs ₹10k a year,
   unlimited. Owners balk at ₹5k a month for full GST service.
5. **Login-sharing norms and OTP friction** block a CA-firm-operated model across many clients.
6. **Card payments create no bank-side invoice demand.** Wires already come with the invoice.
7. **Inbox-based capture is a cheaper substitute.** Most SaaS vendors email the invoice;
   forwarding rules and inbox parsing tools (Hubdoc, Zoho Expense, Vyapar) cover much of it
   without browser automation.

## Evidence FOR (kept honest)

- Chasing documents is a universally acknowledged CA pain: 30–40% of effort (one
  practitioner estimate), plus repeated follow-ups (an ICAI member).
- GSTIN-missing and B2C-tagged domestic invoices (Airtel, Google, Zoom) cause **permanent**
  ITC loss, and vendors refuse to fix them retroactively. A monthly agent that *checks each
  portal's GSTIN setting and invoice entity* could prevent real losses.
- The coming RCMIMP e-invoice regime (Advisory 678, Oct 2026) makes systematic
  self-invoicing more likely, so a feed of foreign invoices with exchange rates gains value.
- GetMyInvoices (Germany) sells portal-login invoice fetching across "10,000+ portals",
  which shows the job has buyers somewhere. India pricing and coverage are unknown.

## What to change before talking to customers

- **Lead with "ITC leak finder" for funded startups**, not "invoice downloader". Each month:
  check that every vendor bills with the company GSTIN; flag invoices missing from
  GSTR-2B; draft RCM self-invoices with exchange rates for foreign vendors; list unmatched
  card lines. Sell it to the finance head and keep the CA as the recipient of the output.
- **Run it on the founder's laptop in their own logged-in browser**, with credentials never
  stored. That sidesteps OTP routing and DPDP worries.
- **Validate by interview, not forums.** Ask 5–10 startup finance heads: how many vendors
  bill you from abroad versus India; what ITC was lost last year; who downloads the invoices
  today and how long it takes. Kill criterion: fewer than 3 of 10 report a quantified
  ITC loss or more than 4 hours a month on this.

## Summary scorecard

| Question | Answer | Strength |
|---|---|---|
| Q1 RCM/ITC/15CA pain | Real confusion; ITC loss real for *domestic* GSTIN gaps; foreign missing-invoice cost low (revenue-neutral); card 15CA unsettled; banks ask for invoices only on wires | Confusion MEDIUM; foreign-invoice pain WEAK |
| Q2 CA collection | WhatsApp/email chaos, 30–40% of effort (1 estimate); bottleneck is client discipline, not portals | STRONG in kind, MEDIUM in count |
| Q3 Pay / price | Anchors low: ₹10k/yr unlimited suite; ₹0.5–5k/month CA retainers; ₹15–20k/month junior | Anchors MEDIUM, WTP WEAK |
| Q4 Trust | Norms against sharing logins; OTP friction; DPDP from 2027; local-only is a plus | WEAK (no direct posts) |
| Overall | **MIXED, not validated as pitched.** Reframe to an ITC-leak finder for startups and test by interview | Reddit gap noted |
