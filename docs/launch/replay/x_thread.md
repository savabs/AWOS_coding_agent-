# X thread (8 posts)

Every post is 280 characters or fewer (checked; a URL counts as 23 on X). Post from the owner's account by hand. Reply to replies yourself; no auto-DMs.

---

**1/8**

> I built AWOS Replay: show a web chore once, and it does it again whenever you ask.
>
> Record the clicks once. Replay. At the end it tells you whether it actually worked, and it says so when it didn't.
>
> Free, open source, and the model runs on your Mac.

Visual: **hero video** (demo_scripts.md, script 1), cut to 30–45 s with burned-in captions. Autoplays muted, so the captions carry it.

---

**2/8**

> The chores I mean: downloading this month's invoice, exporting the same CSV report, filling a weekly timesheet, checking an order status.
>
> Each takes 3 minutes, and each happens again next month.

Visual: screenshot of `/recipes/` showing the 6 example recipe cards.

---

**3/8**

> Old recorders break silently when the site changes. Browser agents re-plan the whole task every run.
>
> Replay records each control as many attributes. If a button moved or got renamed, it scores the page to find it again, and asks a small local model only when unsure.

Visual: still from script 2. Left: recorded "Download invoice" button. Right: the changed page with the button renamed "Get PDF" and moved, highlighted.

---

**4/8**

> The model's main job is to say "it's gone".
>
> It picks one of at most 5 candidates, or answers NOT_FOUND. A wrong click on a similar-looking neighbour is worse than stopping, so when it isn't sure, it stops and tells you why.

Visual: popup screenshot of a FAILED run with the reason "local model says the control ... is gone".

---

**5/8**

> Every run ends with a verdict: VERIFIED, CHECK, UNVERIFIED or FAILED.
>
> Your checks (expected text, final URL) decide pass or fail. A local verifier reads the final page against your goal. It can only downgrade a pass to CHECK, never upgrade a failure.

Visual: two popup screenshots side by side, a green VERIFIED badge and an amber CHECK badge with its reason.

---

**6/8**

> Things it won't do on its own: pay, buy, check out, send, place an order, delete, transfer. Those steps pause until you press Approve.
>
> Passwords, card numbers and one-time codes are never recorded. Replay pauses and you type them.

Visual: page banner plus popup showing "Step 5 will click 'Pay now' (matches 'pay now'). Approve to continue."

---

**7/8**

> Share a chore as a link. The recipe lives in the URL after the #, which browsers never send to a server. The page lists the steps in plain English before you add it.
>
> 6 examples: https://awos.tirramind.com/recipes/?src=x

Visual: script 3 clip (10–15 s): open a `/r/` link, steps listed, "Add to AWOS Replay". If import isn't in the shipped build, use a still of the `/r/` page and change the last sentence to "Import is coming in the next build."

---

**8/8**

> Limits: main frame only, no iframes or file uploads, and it doesn't yet check that a download arrived. The model was trained on synthetic sites, so real ones will find its gaps.
>
> Tell me which chore you'd hand off: https://awos.tirramind.com/?src=x

Visual: none, or a plain text card listing the limits. Keep the last post quiet.
