# Portal runs: product plan (2026-11-02)

From 12 logged discovery conversations (Reddit: r/nocode, r/RPA, r/zapier, and replies; tracker = claude.ai artifact "Talk to Users
Kit", collection `convos`).

**Product.** For people who log into many supplier / partner portals every month to collect invoices, statements or
payout files: it gets every document, handles the logins, codes and pop-ups that break scripts, says exactly what is
missing, and never fails silently.

## Needs vs. status
| Need (how many raised it) | Status |
|---|---|
| Pause on login / code / captcha, resume without redoing work (3) | built (0.6) |
| 2FA from the phone; SMS-only MFA blocks even RPA teams (4) | built (0.8); not yet tested for real |
| Unexpected pop-ups, incl. "accept new terms" (5) | built (0.9): agent brain reads unknown pop-ups; terms / consent / payment always stop |
| Never trust a green run: the result really landed (4) | built (0.9): size + content checks, "Not a document" |
| Flag what is missing (2) | built (collection checklist) |
| New-tab PDFs (2) | built (0.6) |
| One sitting, no nagging every few days (1, strongly) | built (0.8) |
| Never miss a cycle (portal keeps only 3 months) (1) | built (0.9): catch-up of missed months in collections |
| Fast inside short sessions (5-min idle logout) (2) | mostly; unmeasured on a real portal |
| Wait for a state (spinner gone, button enabled), not delays (3) | built (0.9): wait until usable before each action |
| E-mail as the cheap door (codes and documents) (2) | gap |
| Keyboard / Enter fallback (1) | gap (small) |
| Pull values into a sheet / CSV, not just download files (daily ops stats; event details) (2) | not in this plan yet: runs are built around documents |
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
