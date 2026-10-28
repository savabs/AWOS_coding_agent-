# Show HN

## Title (76 chars)

> Show HN: AWOS Replay – record a web chore once, replay it with a local model

Alternative (if the first reads too long): `Show HN: AWOS Replay – browser macros that say when they didn't work` (68 chars).

Link: `https://awos.tirramind.com/?src=hn`

## Post body

> I kept losing time to small web chores: the month's invoice, the same CSV export, a timesheet. Recorders I tried broke silently when a site changed, and browser agents re-plan the whole task on every run.
>
> AWOS Replay is a Chrome extension. You record a chore once; later you click Run and it repeats your clicks, typing and dropdown choices.
>
> Three things I cared about:
>
> 1. **It tells you whether it worked.** You set a goal and an expected text or final URL. Each run ends VERIFIED, CHECK, UNVERIFIED (no checks set) or FAILED. A small local verifier model reads the final page against your goal, but it can only downgrade a pass to CHECK, never upgrade a failure.
> 2. **When the page changes, it doesn't guess.** Each control is recorded as many attributes. If scoring isn't confident, a ~2B model on your Mac (llama.cpp) picks from at most 5 candidates or answers NOT_FOUND. A wrong click is worse than stopping.
> 3. **Dangerous steps wait for you.** Clicks or navigations that look like pay, buy, checkout, send, place order, delete or transfer pause until you press Approve in the extension. Passwords, card numbers and one-time codes are never recorded; replay pauses and you type them.
>
> Nothing leaves your machine. The extension refuses any model endpoint that isn't 127.0.0.1/localhost, and recordings live in Chrome's local storage. You can share a chore as a "recipe" link; the recipe sits in the URL fragment, which browsers don't send to servers.
>
> Limits, plainly: main frame only (no iframes, shadow DOM, file uploads or multi-tab chores), it doesn't yet check that a download actually arrived, and the model was trained on synthetic apps, so real sites will find its blind spots. It's open source and free. A paid tier (schedules, Claude with your own key) is waitlist-only.
>
> Install: `INSTALL_COMMAND`
>
> What I'd like to hear: which chore you'd try first, and where it broke. The popup has "Export run log", which strips typed values and URL queries.

Before posting, cut the recipe sentence if recipe import is not in the shipped build (see README.md).

## Tough questions, honest answers

**1. How is this different from Zapier, UI.Vision, Selenium IDE or a browser agent (Operator, Claude for Chrome, browser-use)?**
Zapier works through APIs; this is for sites that only have a web UI. UI.Vision and Selenium IDE record and replay too, but when a selector stops matching they fail, or worse, click whatever matches now. Replay records many attributes per control, re-finds by scoring, and only when that isn't confident asks a small model, which is trained to say "it's gone". Browser agents plan every step from scratch on every run. That's flexible, but slower, costs tokens each time, and is harder to trust. Replay is the opposite trade: a fixed recipe, a model only for the step that drifted, and a verdict at the end.

**2. Why a local model and not GPT/Claude?**
Privacy and cost. The model sees your logged-in pages (names, invoices, account numbers). Keeping it on 127.0.0.1 means none of that goes anywhere, and the extension enforces it in code. The job is also small: choose one of five candidates or say NOT_FOUND, which a ~2B model can do on a laptop. A Claude option with your own API key is planned for Pro, but it is not in the current build.

**3. What breaks?**
Iframes, shadow DOM, drag and drop, file uploads, chores that span tabs, CAPTCHAs and anything that needs a fresh 2FA code (it pauses for you). Big redesigns where the feature moved to another page will end in NOT_FOUND, which is by design. Download arrival isn't verified yet. And a run with no checks set can only end UNVERIFIED, so a lazy recording gives weak answers.

**4. Is it safe to open someone else's recipe?**
A recipe is data, not code: a list of goto/click/fill/select steps that the page shows in plain language before you add it. It holds no secrets (those are placeholders), and it lives in the URL fragment, so our server never sees it. The same safety gate applies to imported recipes: pay/send/delete-type clicks and navigations still need your Approve. Still, treat a recipe from a stranger like a shell script from a stranger: read the steps first, especially the URLs.

**5. Why trust VERIFIED?**
VERIFIED means the checks you set passed (final URL, expected text) and the local verifier agreed. That's only as strong as the checks. It doesn't yet confirm a downloaded file exists. The verifier is downgrade-only on purpose: a model can make us more cautious, never more confident.

**6. What's the business model?**
The extension and local model are free and open source. Pro would add schedules, unlimited saved recipes and a Claude cloud brain using your own API key, at a placeholder $12/month. Checkout isn't open; there's a waitlist. I'd rather learn which chores people hand off before charging for anything.
