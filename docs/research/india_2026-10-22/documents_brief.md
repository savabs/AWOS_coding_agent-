# India field brief: what a small business collects for its CA (research, 2026-10-22)

Plain-language research input, **not tax advice**. Most of it comes from secondary sources.

**Form numbers changed.** The Income-tax Act 2025 and Income-tax Rules 2026 apply from 1 Apr 2026 and renumber forms:

| Old | New |
|---|---|
| 26AS / AIS | Form 168 |
| 16A | 131 |
| 16 | 130 |
| 26Q | 140 |
| 24Q | 138 |
| 27Q | 144 |

FY 2025-26 and earlier still use the old numbers. Check the final mapping on incometaxindia.gov.in.

## 1. GST

Portal: gst.gov.in. Login needs a username, password and captcha; filing needs an EVC OTP or a DSC.

| Document | What it is and why the CA needs it | Frequency / due date | Format | Difficulty |
|---|---|---|---|---|
| **GSTR-2B** | Auto-generated list of the purchase invoices your vendors uploaded. The CA uses it to claim **input tax credit (ITC)**; each purchase must match the vendor's GSTIN | Draft on the 14th. With IMS (the Invoice Management System), any accept/reject action after that means recomputing 2B before filing 3B. Quarterly for QRMP filers | Excel / JSON / PDF | Medium (captcha) |
| GSTR-2A | Live view, used for reconciliation | Ongoing | Excel / JSON | Medium |
| **GSTR-1 / GSTR-3B** | Copies of filed returns (sales; tax summary), with ARN and payment challan | GSTR-1 by the 11th (quarterly: 13th); GSTR-3B by the 20th (quarterly: 22nd/24th by state) | PDF | Medium |
| e-invoice (IRN) | Required if annual turnover is over ₹5 cr. At ₹10 cr+, invoices must be reported within 30 days | Per invoice | JSON + QR code | Separate IRP portals |
| e-way bill | Required for goods movements over ₹50,000 | Per shipment | PDF | ewaybillgst.gov.in |
| **RCM self-invoice for foreign SaaS** | Google, AWS, GitHub, Adobe and Anthropic bill without an Indian GSTIN. A registered business pays 18% GST itself under reverse charge and claims it back. Rule 47A: self-invoice within 30 days | Monthly | Vendor PDF + self-invoice | **High** (dozens of billing pages) |
| OIDAR | Since Oct 2023, foreign vendors charge IGST only to unregistered buyers. Some (Google, AWS) bill from an Indian entity with a GSTIN, so check which entity issued each invoice | — | — | — |
| LUT (RFD-11) | Lets you export services zero-rated | Once a year, before the first export | PDF | Low |
| Rule 37A | ITC must be reversed by 30 Nov if a vendor hasn't filed 3B by 30 Sep | Yearly check | — | — |

## 2. Income tax and TDS

- **AIS/TIS and 26AS (now Form 168).** Tax credits, TDS your clients deducted, and high-value transactions.
  - Portal: incometax.gov.in (login, OTP, captcha).
  - Format: a PDF locked with **lowercase PAN + date of birth (DDMMYYYY)**, or JSON for the offline utility.
  - When: monthly or quarterly, and in full before the ITR.
- **Form 16A (now 131).** TDS certificates for tax *you* deducted from vendors. You download these from **your own TAN's TRACES account**.
  - TRACES needs a login, captcha and KYC (token plus challan details, or a DSC).
  - The bulk ZIP becomes ready only about 2–3 hours after you request it, and needs the Java TRACES converter.
  - Due quarterly, about 15 days after the TDS return. Certificates your clients issue to you arrive by email; AIS is the cross-check.
- **TDS challans.** Pay by the 7th of each month (for March: 30 Apr). Quarterly returns are due 31 Jul, 31 Oct, 31 Jan and 31 May.
- **Advance tax.** Due 15 Jun, 15 Sep, 15 Dec and 15 Mar. Presumptive professionals pay once, by 15 Mar.
- **ITR.** Due 31 Jul (no audit) or 31 Oct (audit). GSTR-9 is due 31 Dec.

## 3. Banks and money from abroad

- **Bank statements** (every current, savings and OD account) and **credit card statements**. These are the base of the books.
  - Each bank's netbanking site looks different, needs an OTP and sometimes a captcha, and limits the date range.
  - The PDFs are often password-locked.
- **FIRA / e-FIRC.** Proof that foreign money arrived, required for zero-rated exports.
  - Payoneer and Wise issue them per transfer (Wise reportedly charges $2.50 each).
  - PayPal has a FIRC page; banks issue them for SWIFT payments.
- **eBRC** (dgft.gov.in, needs an IEC). Mostly for goods and software (SOFTEX) exporters. Many freelancers rely on FIRA alone.

## 4. Payment gateways

Razorpay, Cashfree and PayU each provide:
- settlement reports, matched against bank credits;
- a monthly **fee invoice with GST**, which can be claimed as ITC;
- **TCS/TDS** statements.

PayPal provides statements and fee invoices. All of these have merchant APIs.

## 5. Invoices

- **Purchases:** domestic vendors (must show *our* GSTIN), utilities, rent (plus rent TDS), telecom, foreign SaaS.
- **Sales:** exported from Zoho Books, Tally, Vyapar or Refrens.

## 6. Payroll (if there are staff)

| Item | When | Where |
|---|---|---|
| Salary register and payslips | Monthly | Payroll system |
| EPF ECR + challan | By the 15th | EPFO portal (captcha) |
| ESIC (pay ≤ ₹21k/month) | By the 15th | ESIC portal |
| Professional tax | State-specific | State portals |
| Salary TDS (Form 138, formerly 24Q) | Quarterly | TRACES |
| Form 16 (now 130) | Yearly | TRACES |

## (a) Monthly pack checklists

**(i) Freelancer billing foreign clients** (GST-registered with an LUT, or unregistered if under ₹20 lakh turnover)
- bank statements
- FIRA/FIRC for every receipt
- Payoneer, Wise or PayPal statements and their fee invoices
- sales invoices
- foreign SaaS invoices (RCM if registered)
- card statements
- if registered: GSTR-1 and 3B acknowledgements, and 2B
- quarterly: AIS/26AS and advance tax challans

**(ii) Agency or startup with GST and staff**: everything in (i), plus:
- all bank and card accounts
- purchases matched against GSTR-2B, with IMS actions
- RCM self-invoices
- GSTR-1/3B returns and challans
- e-invoice and e-way bill registers (if over the thresholds)
- gateway reports, fee invoices and TCS statements
- rent, utility and telecom bills
- TDS challans and returns, plus 16A/131
- payroll, PF, ESIC and PT
- yearly: LUT, GSTR-9, audit, ITR

**(iii) CA firm with 20–100 clients**
- Pack (ii) for every client, tracked in a completeness grid: document × month × received / missing / mismatched.
- The pain: chasing clients on WhatsApp and email, and logging into dozens of client GST, IT and TRACES accounts. The firm usually holds the passwords, but **the OTPs go to the client's phone**.

## (b) Hardest to collect by hand

1. **Government portals:** a captcha on every login, OTPs sent to the owner's phone, short sessions. TRACES adds KYC, a wait of hours, and a Java converter.
2. **Foreign SaaS invoices:** 10–40 billing pages, each with its own login and 2FA, plus monthly RCM self-invoices.
3. **Several banks:** different websites, OTPs, date limits, locked PDFs.
4. **A FIRC for every remittance.**
5. **Reconciling 2B against purchases**, now including IMS actions.

## (c) Official routes vs portal-only

| Source | Official route |
|---|---|
| GST | Official APIs only through licensed **GSPs/ASPs** (ClearTax, IRIS, Tally…). The taxpayer enables "Manage API Access" with an OTP; the session lasts 6 hours to 30 days. An independent tool would need a GSP partner |
| Bank statements | The **Account Aggregator** framework (RBI/Sahamati), via a licensed FIU or a partner. Coverage of current accounts varies by bank. GST data was added as an AA data type in 2023; rollout status unclear |
| Income tax | APIs for registered **ERIs**, mainly for filing. **No public bulk API for AIS or TRACES**, so these are portal-only in practice |
| Payment gateways, Zoho Books, Tally | Official APIs |

**Portal-only:** EPFO, ESIC, state PT portals, the DGFT exporter side, most SaaS billing pages.

## (d) Legal and terms-of-service notes (be careful)

- **GSTN.** A notice dated 05-12-2018 says "Scrapping of GST Site is strictly prohibited". The user agreement bans scraping and permits access only through approved interfaces. Verify the current text.
- **IT Act 2000.** Section 43 (unauthorised access, civil liability) and Section 66 (criminal, if done dishonestly). India has no specific scraping law and the case law is unsettled.
- **Captcha and OTP.** Bypassing them automatically could count as circumventing access controls. That is an inference, but it is the riskiest thing the product could do.
- **Safest design:**
  - **the user logs in, solves the captcha and enters the OTP themselves, in their own browser**;
  - the assistant only navigates and downloads that user's own data, at a human pace;
  - no credential storage beyond the OS keychain, and no captcha solving;
  - official routes (GSP, AA, gateway APIs) wherever they exist.
- **CA firms** need each client's authorisation. The DPDP Act 2023 applies to personal data (PAN, payroll).
- **Lawyer:** get a review before launch.

## Sources

- abcaus.in, cleartax.in/s/new-income-tax-forms, taxscan.in (Rules 2026 and form renumbering)
- tallysolutions.com (IMS, 2B/3B; ASP access); GSTN advisory via mahagst.gov.in
- taxmann.com (e-invoice 30-day rule; Rule 37A)
- navimumbai.icai.org and taxguru.in (Rule 47A)
- amlegals.com (OIDAR); india-briefing.com (RCM)
- incometaxindia.gov.in (AIS online/offline; Form 16/16A)
- learn.quicko.com (TRACES)
- skydo.com and vjmglobal.com (FIRA, eBRC)
- paybooks.in (payroll calendar)
- taxguru.in (GSP API access)
- sahamati.org.in (Account Aggregator)
- taxheal.com (GSTN scraping notice); law.asia (data scraping regulation)

**Confidence:** dates and thresholds are well established but secondary. Check the form renumbering, the QRMP dates and the GSTN terms on the official portals.
