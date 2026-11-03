# Portal runs: product plan (2026-11-02)

From 16 logged discovery conversations (Reddit: r/nocode, r/RPA, r/zapier, and replies; tracker = claude.ai artifact "Talk to Users
Kit", collection `convos`).

**Product.** For people who log into many supplier / partner portals every month to collect invoices, statements or
payout files: it gets every document, handles the logins, codes and pop-ups that break scripts, says exactly what is
missing, and never fails silently.

## Needs vs. status
| Need (how many raised it) | Status |
|---|---|
| Pause on login / code / captcha, resume without redoing work (3) | built (0.6) |
| 2FA from the phone; SMS-only MFA blocks even RPA teams (6) | built (0.8); not yet tested for real |
| Unexpected pop-ups, incl. "accept new terms" (5) | built (0.9): agent brain reads unknown pop-ups; terms / consent / payment always stop |
| Never trust a green run: the result really landed (5); per step too: expect X (e.g. "success"), and if something else appeared, read it, class it error / warning, act on it; capped retries and scrolls so a run never loops, caps set per portal while testing it or read from the page ("page 1 of 5") | files: built (0.9) size + content checks, "Not a document"; end of run: final URL + expected texts, retried once; caps: one retry after a pop-up, 5 model-chosen steps. Gap: no check after each step, and no reading of the error / warning that appeared instead |
| Flag what is missing (2) | built (collection checklist) |
| One portal's problem never stops the rest: mark it and go on with the next; whoever decides answers within minutes (2, RPA devs) | built: "Skip this portal (leave it for me)" + a wait alarm that leaves a waiting portal and starts the next |
| New-tab PDFs, incl. tabs opening behind the main window (3) | built (0.6) |
| One sitting, no nagging every few days (1, strongly) | built (0.8) |
| Never miss a cycle (portal keeps only 3 months; bit one user twice a year after time away, support took 4-5 days while accounting chased) (1, strong) | built (0.9): catch-up of missed months in collections |
| Fast inside short sessions (5-min idle logout; a timeout kills the code too, so login + new code + wait, up to 3 loops) (2) | mostly; unmeasured on a real portal |
| Wait for a state (spinner gone, button enabled), not delays (3) | built (0.9): wait until usable before each action |
| E-mail as the cheap door (codes, documents, "your invoice is ready" links that land on a login; many vendor / payroll sites hide a scheduled e-mailed report in settings, so look there first when setting up a portal) (4) | gap |
| Move content from a portal into another app, not just download it (daily portal stats into an ops sheet; call recordings + AI summaries into a Mac CRM) (2) | gap: run values are typed in at start, nothing carries a value from one site to another; the CRM here (Daylite) is a Mac desktop app, outside a browser extension |
| Keyboard / Enter fallback, as a backup blended with clicks (arrow keys to scroll, visual match to find the item) (1, RPA dev) | gap (small) |
| What happens to the files after (rename, folder, accountant) (1: rename + drop in the accounting folder) | gap: downloads keep the site's file name, no folder choice (P1 "finish the job") |
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

## Parking lot (expansion ideas, not now)
- **Start runs from Siri Shortcuts** (Mac): expose each saved run as a Shortcuts action, so Siri, schedules and people's
  own automations can start it. Source: a home-automation user who already bridges a legacy device with Shortcuts + IFTTT.
- **Device dashboards with no integration**: a brand with a web dashboard but no API turns device admin into a portal
  chore (slow jobs: usage reports, schedules; not instant on/off). Same source.
