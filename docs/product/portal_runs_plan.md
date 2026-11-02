# Portal runs: product plan (2026-11-02)

From 13 logged discovery conversations (Reddit: r/nocode, r/RPA, r/zapier, and replies; tracker = claude.ai artifact "Talk to Users
Kit", collection `convos`).

**Product.** For people who log into many supplier / partner portals every month to collect invoices, statements or
payout files: it gets every document, handles the logins, codes and pop-ups that break scripts, says exactly what is
missing, and never fails silently.

## Needs vs. status
| Need (how many raised it) | Status |
|---|---|
| Pause on login / code / captcha, resume without redoing work (3) | built (0.6) |
| 2FA from the phone; SMS-only MFA blocks even RPA teams (5) | built (0.8); not yet tested for real |
| Unexpected pop-ups, incl. "accept new terms" (5) | built (0.9): agent brain reads unknown pop-ups; terms / consent / payment always stop |
| Never trust a green run: the result really landed (5); per step too: expect X (e.g. "success"), and if something else appeared, read it, class it error / warning, act on it; capped retries and scrolls so a run never loops | files: built (0.9) size + content checks, "Not a document"; end of run: final URL + expected texts, retried once; caps: one retry after a pop-up, 5 model-chosen steps. Gap: no check after each step, and no reading of the error / warning that appeared instead |
| Flag what is missing (2) | built (collection checklist) |
| New-tab PDFs, incl. tabs opening behind the main window (3) | built (0.6) |
| One sitting, no nagging every few days (1, strongly) | built (0.8) |
| Never miss a cycle (portal keeps only 3 months) (1) | built (0.9): catch-up of missed months in collections |
| Fast inside short sessions (5-min idle logout) (2) | mostly; unmeasured on a real portal |
| Wait for a state (spinner gone, button enabled), not delays (3) | built (0.9): wait until usable before each action |
| E-mail as the cheap door (codes, documents, "your invoice is ready" links that land on a login) (3) | gap |
| Keyboard / Enter fallback, as a backup blended with clicks (arrow keys to scroll, visual match to find the item) (1, RPA dev) | gap (small) |
| What happens to the files after (rename, folder, accountant) | unknown: answers pending |
| Will they pay; why not Dext / Hubdoc | unknown: answers pending |

## Priorities
- **P0** (before a real tester) **- done in 0.9** (tests: `tests/extension/test_portal_p0.py`; untested on a real portal): real file checks (bytes, PDF/CSV by content, not a saved login/error page); AI-read
  unknown pop-ups (close if harmless, stop if it asks for a decision; "accept terms" always stops); catch up on missed
  months; wait until the target is enabled/visible.
- **P1** (after a real tester confirms the core): e-mail route (codes + e-mailed documents); finish the job (rename,
  folder, accountant hand-off); recipe setup from one sentence via Tell it.
- **P2**: keyboard fallback; network-level downloads; locked-screen runs.
- **Not code**: switch on the relay; test end to end on one real portal; get the Reddit test-case user running it;
  ask about Dext/Hubdoc and price.
