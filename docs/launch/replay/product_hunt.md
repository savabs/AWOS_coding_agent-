# Product Hunt

Launch one day after HN, and only if nothing big broke there. The owner submits it from their own account. Don't ask for upvotes anywhere; PH penalises that and it's against their rules.

## Fields

**Name:** AWOS Replay

**Tagline (49 chars):**
> Show a web chore once. It replays it, and checks.

Alternative (52 chars): `Record a web chore once, replay it with a local model`

**Link:** `https://awos.tirramind.com/?src=ph`

**Description (about 240 chars):**
> A Chrome extension that records a web chore once and replays it on demand. A small model on your Mac finds controls that moved, says when one is gone, and checks the result. Pay, send and delete steps wait for your OK. Free and open source.

**Topics:** Chrome Extensions, Productivity, Artificial Intelligence, Open Source, Privacy

**Pricing:** Free (open source). Pro is waitlist-only. Don't select "Paid" or show a price as live.

## Gallery (in order, 1270x760)

1. **Hero video** (demo_scripts.md, script 1) as the first gallery item.
2. **Record:** popup with "Recording: 4 actions" over the fictional billing page.
3. **Verdict:** popup with a green VERIFIED badge, checks listed (url ✓, text "Invoice INV-2026-10 downloaded" ✓).
4. **Self-healing:** before/after of the renamed and moved button, with the log line "model pick #2".
5. **Stops instead of guessing:** FAILED run, "local model says the control 'Export CSV' is gone".
6. **Approval gate:** "Step 5 will click 'Pay now'. Approve to continue."
7. **Recipe page:** `/r/` view of "Fill weekly timesheet (stops before Submit)" with steps in plain language and the "Add to AWOS Replay" button.
8. **Privacy:** Options page showing the loopback endpoint `http://127.0.0.1:8080/v1`, with the caption "The model runs here. Nothing else is allowed."

All screenshots come from the fictional demo sites in demo_scripts.md. No real accounts, emails or invoices.

## Maker first comment (about 210 words)

> Hi PH, I'm the maker.
>
> I built AWOS Replay for the web chores that come back every month: downloading an invoice from a billing page, exporting the same report, filling a timesheet. Recorders I tried broke quietly when a site changed. Browser agents worked but re-thought everything on every run.
>
> So Replay does a few specific things:
>
> - You record the chore once, and Replay repeats your clicks, typing and dropdown choices.
> - If a control moved or got renamed, it re-finds it. A small model on your Mac helps only when it isn't sure, and it's allowed to answer "this is gone". It stops instead of guessing.
> - Every run ends VERIFIED, CHECK, UNVERIFIED or FAILED, based on checks you set. The local verifier can only make a verdict more cautious.
> - Pay, send and delete steps wait for your Approve. Passwords and card numbers are never recorded.
> - Nothing leaves your machine.
>
> What it can't do yet: iframes, file uploads, multi-tab chores, or confirming a download landed.
>
> It's free and open source. Schedules and a Claude option (with your own key) are planned for a Pro tier, waitlist only for now.
>
> I'd love to hear which chore you'd try first, and where it broke.
