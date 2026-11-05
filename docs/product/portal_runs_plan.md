# Portal runs: product plan (2026-11-02)

From 26 logged discovery conversations (Reddit: r/nocode, r/RPA, r/zapier, and replies; tracker = claude.ai artifact "Talk to Users
Kit", collection `convos`).

**Product.** For people who log into many supplier / partner portals every month to collect invoices, statements or
payout files: it gets every document, handles the logins, codes and pop-ups that break scripts, says exactly what is
missing, and never fails silently.

## Needs vs. status
| Need (how many raised it) | Status |
|---|---|
| Pause on login / code / captcha, resume without redoing work (3) | built (0.6) |
| 2FA from the phone; SMS-only MFA blocks even RPA teams; sometimes asked again right at the export button, not only at login; "the main reason I've never tried to automate it" (8) | built (0.8); not yet tested for real |
| Unexpected pop-ups, incl. "accept new terms"; on cloud apps mostly the vendor's new-feature and cookie notices, with accept / decline / OK / cancel buttons (6) | built (0.9): agent brain reads unknown pop-ups; terms / consent / payment always stop |
| Never trust a green run: the result really landed (5); per step too: expect X (e.g. "success"), and if something else appeared, read it, class it error / warning, act on it; capped retries and scrolls so a run never loops, caps set per portal while testing it or read from the page ("page 1 of 5") | files: built (0.9) size + content checks, "Not a document"; end of run: final URL + expected texts, retried once; caps: one retry after a pop-up, 5 model-chosen steps. Gap: no check after each step, and no reading of the error / warning that appeared instead |
| Flag what is missing (2) | built (collection checklist) |
| When the bank feed breaks, expires or doesn't cover a card / entity, get the statements from the bank's own site (seen in ~8 recent bookkeeping threads: Chase employee cards not pulled in, feeds expiring, "feeds disconnect silently"; not yet a conversation) | core portal run already; untested on a bank site |
| Before retrying a step that changes something (submit, upload), check whether it already happened: "a Submit timeout doesn't prove nothing was submitted" (1, strong, tool maker) | downloads: built (a completed file isn't fetched again); submits / uploads: not checked yet |
| Survive redesigns: the download button moves, ids change or get shuffled (5: mileage log, daily ops sheet, r/RPA, a legacy-site RPA dev, monthly statements) | built: multi-attribute locator re-finds a moved control but its name / label must still match |
| Remember the chore: people keep a calendar reminder for it (3) | built: schedules (0.4), collections on a schedule (0.6) |
| Fill this month's date range; the picker resets every visit (2) | partial: month / year placeholders fill typed dates; no last-day-of-month; click-only calendars untested |
| One portal's problem never stops the rest: mark it and go on with the next; whoever decides answers within minutes (2, RPA devs) | built: "Skip this portal (leave it for me)" + a wait alarm that leaves a waiting portal and starts the next |
| New-tab PDFs, incl. tabs opening behind the main window (3) | built (0.6) |
| One sitting, no nagging every few days (1, strongly) | built (0.8) |
| Never miss a cycle (portal keeps only 3 months; bit one user twice a year after time away, support took 4-5 days while accounting chased) (1, strong) | built (0.9): catch-up of missed months in collections |
| Fast inside short sessions (5-min idle logout; a timeout kills the code too, so login + new code + wait, up to 3 loops) (2) | mostly; unmeasured on a real portal |
| Wait for a state (spinner gone, button enabled), not delays (3) | built (0.9): wait until usable before each action |
| Look for a machine-readable door before clicking: e-mailed / scheduled report, the JSON request behind the page, embedded structured data (ld+json), feeds and calendar files (4 practitioners) | gap: Replay reads none of these today; e-mail is the row below |
| E-mail as the cheap door (codes, documents, "your invoice is ready" links that land on a login; many vendor / payroll sites hide a scheduled e-mailed report in settings, so look there first when setting up a portal) (4) | gap |
| Move content from a portal into another app, not just download it (daily portal stats into an ops sheet; call recordings + AI summaries into a Mac CRM; the same report exported and pasted into a sheet again and again) (3) | gap: run values are typed in at start, nothing carries a value from one site to another; the CRM here (Daylite) is a Mac desktop app, outside a browser extension |
| Keyboard / Enter fallback, as a backup blended with clicks (arrow keys to scroll, visual match to find the item) (1, RPA dev) | gap (small) |
| What happens to the files after (2: rename + drop in the accounting / bookkeeper folder) | gap: downloads keep the site's file name, no folder choice (P1 "finish the job") |
| Will they pay; why not Dext / Hubdoc (1, strong: paid, dropped after 2 months) | one answer: it covered ~4 of 12 vendors (big utilities), not the small / 2FA / timeout ones, "paying for the easy part"; price not asked yet |
| Logins stay on the user's computer (an incumbent storing logins on its servers made the finance person nervous) (1, strong) | by design: runs in the user's own browser |

## Priorities
- **P0** (before a real tester) **- done in 0.9** (tests: `tests/extension/test_portal_p0.py`; untested on a real portal): real file checks (bytes, PDF/CSV by content, not a saved login/error page); AI-read
  unknown pop-ups (close if harmless, stop if it asks for a decision; "accept terms" always stops); catch up on missed
  months; wait until the target is enabled/visible.
- **P1** (after a real tester confirms the core): e-mail route (codes + e-mailed documents); finish the job (rename,
  folder, accountant hand-off); recipe setup from one sentence via Tell it.
- **P2**: keyboard fallback; network-level downloads; locked-screen runs.
- **Not code**: switch on the relay; test end to end on one real portal; get the Reddit test-case user running it;
  ask about Dext/Hubdoc and price.

## Competitors (discovery + desk research 2026-11-04)
| Who | What it is | Where it falls short for our user |
|---|---|---|
| Dext / Hubdoc-type fetchers | fetch invoices from supported suppliers | one user: covered ~4 of 12 vendors (big utilities), not the 2FA / timeout ones; logins stored on their servers made finance nervous; dropped after 2 months |
| **Tailride** (closest) | invoice collection from portals, runs ON THE USER'S COMPUTER (desktop app Mac/Win/Linux, or browser extension), "passwords stay there"; fills authenticator-app codes; free for 10 invoices/mo, $19/mo for 75 (tailride.so compare page, checked 2026-11-04) | local + logins-stay-local is NOT unique to us. Says nothing about SMS / e-mail codes; invoices only |
| GetMyInvoices | cloud fetching, "10,000+ portals" (vendor claim); desktop app only for authenticator 2FA; EUR 19-179/mo by portals, 499 business | SMS / e-mail codes not automated; logins in their cloud; reviews: connections often break and need manual work, slow setup and support |
| Skyvern / Browser Use | general cloud browser agents for developers; authenticator codes automatic, SMS pushed in by API or paid number; $29-149/mo or per step | developer tools, logins in their cloud or a password vault |
| Hubdoc / AutoEntry | Hubdoc retired portal fetching (API links only); AutoEntry reportedly stopped | their users are left doing it by hand (Xero ideas forum, Sage forum) |
| Skydive | cloud browser agents, each with its own cloud computer; scheduled, drops files in a folder, flags page changes; ~$20-49/mo, free tier (third-party sources disagree) | runs in their cloud, so logins and documents leave the user's machine; 2FA handling unconfirmed |

**What can still set us apart** (given Tailride): text-message / e-mail codes via the human's phone; recovering after redesigns; a missing-document checklist; chores beyond invoices (statements per account, daily portal figures, courier and partner portals). People do pay: single-purpose fetchers cost EUR 19-179/mo.

## Parking lot (expansion ideas, not now)
- **Start runs from Siri Shortcuts** (Mac): expose each saved run as a Shortcuts action, so Siri, schedules and people's
  own automations can start it. Source: a home-automation user who already bridges a legacy device with Shortcuts + IFTTT.
- **Device dashboards with no integration**: a brand with a web dashboard but no API turns device admin into a portal
  chore (slow jobs: usage reports, schedules; not instant on/off). Same source.
- **Competitor pricing behind calculators / multi-step forms**: fill a form to read the numbers; rare and different on
  every site, so "a custom script for each one doesn't pay off". Only works for us if setting up a new site is near
  zero effort (supports P1 recipe setup from one sentence). Source: r/nocode reply.
