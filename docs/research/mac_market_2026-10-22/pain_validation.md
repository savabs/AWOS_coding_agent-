# Is monthly document collection a real pain? (research, 2026-10-22)

Research agent, web sources. 25 pieces of evidence and 6 counter-points.
- **Read directly:** Xero Ideas, Hacker News, the Keyboard Maestro and DEVONthink forums, OMR reviews of GetMyInvoices, dp.cx, Fastlancer.
- **Seen only in search snippets (S):** Capterra, Indie Hackers, AccountingWEB.
- **Not reachable:** Reddit.

## Evidence

**Bank statements: Xero users and bookkeepers (2026)**
- *"Now we spend several hours a month manually pulling and relabeling statement[s]"* (2026-07-03).
- *"Easily the task that holds our accountants and bookkeepers up the most"* (2026-06-19).
- *"Save us a tremendous amount of time waiting for the client to get around to giving us the monthly statement"* (2026-10-13).
- The Xero idea for this has 37 votes. Source: productideas.xero.com/…/51464143.
- **Hubdoc stopped fetching from bank portals in 2022** because banks and suppliers blocked it (blog.xero.com). Reviewers still ask for it back (S).

**SaaS and vendor invoices (Hacker News, 2013–2025)**
- *"Huge pain to hunt for the invoices/receipts every month."*
- *"Every month you have to go [to] the website, login, go [to] the admin, click download invoice"* (NextDNS, 2025).
- At Cloudflare, an accounts-team login requires an enterprise plan.
- People write their own scripts, and one says it saves "30 min monthly".

**Mac users trying to automate it**
- Keyboard Maestro macros *"broke nearly every month, as the sites change their structure so often"* (2024).
- Credit-union macros are flaky. Safari's page-loaded detection is unreliable for PDFs.
- Hazel and OCR naming fails on some statement issuers. One DEVONthink user was told to just set a calendar reminder.

**GetMyInvoices reviews (German SMBs)**
- 2FA breaks portal connections.
- *"Es fehlen sehr viele Dokumente"* ("a great many documents are missing").
- Frequent connection errors.
- One positive review: about 10 hours saved per quarter.
- Fastlancer (2026-09): connections drop, especially with 2FA, and plans cap the number of portals.

**Founders and firms**
- Founders built BillyBox and similar tools (S).
- One founder lost about $3,200 in deductions for missing SaaS invoices (S).
- Giving the accountant your logins is seen as a security risk (S).
- A Financial Cents survey (n=816) ranks getting documents from clients as firms' #1 workflow problem (second-hand).
- Hellobill (YC 2016) built a desktop app that kept credentials in the local Keychain. It is now inactive.

## Counter-evidence

- Someone with a simple setup enters receipts at month end in about 10 minutes.
- Small SaaS companies may have only a few card charges, with sales already automated in Stripe.
- Marketing claims of "15 minutes" a month.
- About 5 min/week if kept up; about 2 h if left for a quarter.
- Bank feeds cover transactions, **but accountants still need the PDF statements** for year-end balances.

## Verdict: moderate to strong, depending on who

| Who | Pain | Why |
|---|---|---|
| Many portals (agencies, solo founders with 10–30 SaaS subscriptions, several banks and cards, VAT or monthly close) | **Strong** | 30 min to "several hours" a month, plus real money lost on missing invoices |
| Accountants and bookkeepers chasing clients | **Strong** | Their most consistent complaint |
| Simple freelancers (one bank, Stripe) | **Weak** | 10–15 min a month |

**Willingness to pay is shown, not just stated:** people pay €19+/month for GetMyInvoices, Dext and Hubdoc, pay accountants $300–800/month, and build their own fixes.

**The worst sub-chore:**
- **Most painful:** bank and card statement PDFs from bank portals. Hubdoc retired this; 2FA blocks it; accountants still require the PDFs.
- **Most frequent:** monthly SaaS and vendor invoices from billing portals that don't email the PDF.
- **Second-order:** renaming and filing, then sending the bundle to the accountant.

## Why existing tools fail

1. 2FA and repeated re-authentication.
2. Banks block server-side credential scraping.
3. Smaller portals are missing, and plans cap how many portals you can connect.
4. Do-it-yourself Mac macros break when sites change.
5. People don't trust giving out their bank logins.
6. Naming files from PDF content is hard.

**Inferred gap (an inference, not a claim from the sources):** a local agent that logs in through the user's own browser session, copes with 2FA and survives layout changes.

## Other Mac chores

Only weak evidence was found:
- extracting attachments from Mail;
- renaming and filing with Hazel or Finder;
- renaming in Preview.

These mostly appear as part of the same finance workflow. There is almost no evidence of standalone Numbers or Calendar chores.

## Caveats

- The strongest recent quotes are the Xero Ideas comments from 2026. Many of the others date from 2013–2018.
- The time each person loses is modest. The pain is tedium, procrastination, missing deductions and being blocked while waiting.
- Some sources are vendors or founders, so they have a bias.
- Mac use is explicit only in the Keyboard Maestro, Hazel and DEVONthink threads.
- The evidence leans European (German SMBs).
