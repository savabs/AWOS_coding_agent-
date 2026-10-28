# AWOS Replay launch kit

**AWOS Replay: show it once, it does it forever.** A Chrome extension that records a web chore once, replays it, and tells you whether it really worked. When a page changes, a small model on your Mac finds the moved control or says it is gone. It never pays, sends or deletes without your click.

Everything here is sent by the owner personally, by hand. Nothing is automated, scheduled or mass-sent.

## Files

| File | What it is |
|---|---|
| [show_hn.md](show_hn.md) | Show HN title, post body, 6 tough questions with honest answers |
| [x_thread.md](x_thread.md) | 8-post X thread with a visual for each post |
| [product_hunt.md](product_hunt.md) | Name, tagline, description, topics, gallery shots, maker comment |
| [demo_scripts.md](demo_scripts.md) | 3 video scripts (hero, self-healing, sharing a recipe), timed shot lists |
| [communities.md](communities.md) | Where to post first and who to DM, with each community's rules |

## What the launch build actually does (check before posting)

The kit only claims what `extension/` does. Read this list on launch morning and cut any claim that is still not true in the build you ship.

| Claim | Where it is in the code (v0.3.0) | Status |
|---|---|---|
| Records clicks, typing and `<select>` changes | `extension/content.js` | in code |
| Secrets (password, card, OTP, SSN, PIN, IBAN...) become `{{secret:...}}` placeholders; replay pauses so you type them | `extension/lib/safety.js`, `background.js` | in code |
| Re-finds moved controls by scoring many attributes; asks a local model only when not confident; model can answer NOT_FOUND | `extension/lib/locator.js`, `lib/model.js` | in code |
| The model endpoint must be loopback (127.0.0.1 / localhost) | `lib/model.js` `isLoopback`, `options.js` | in code |
| Pay / buy / checkout / send / place order / delete / remove / transfer clicks, and navigations to such URLs, pause for Approve | `lib/safety.js`, `background.js` | in code. Note: a button named just "Submit" is **not** gated. The timesheet recipe ends before Submit instead. |
| Verdicts VERIFIED / CHECK / UNVERIFIED / FAILED; the local verifier can only downgrade | `background.js` `finish()` | in code |
| Checks: final URL and expected text | `background.js` | in code. **Download arrived is not checked yet** (`docs/TRY_AWOS_REPLAY.md`, Known limits). |
| Main frame only: no iframes, shadow DOM, file uploads, multi-tab chores | `docs/TRY_AWOS_REPLAY.md` | known limit, say it |
| Recipe links (`/r/#...`) and "Add to AWOS Replay" import | not in `extension/` v0.3.0 | **confirm before launch** |
| Params (`{{month}}`, "ask me each time") | not in `extension/` v0.3.0 (only secrets pause) | **confirm before launch** |
| Schedules (Pro) | not in `extension/` v0.3.0 (no `alarms` permission) | Pro, "coming"; never say it ships today unless it does |
| Claude cloud brain with your own key (Pro) | not in code; model calls are loopback-only today | Pro, "coming" |

If recipe import is not in the build on launch day, the HN post and thread still work: replace "click Add to AWOS Replay" with "the page shows the steps; import lands in the next build".

## ?src= tags

Every link you post carries a source tag so the waitlist shows where people came from.

| Channel | Link |
|---|---|
| Hacker News | `https://awos.tirramind.com/?src=hn` |
| r/LocalLLaMA | `https://awos.tirramind.com/?src=localllama` |
| Other subreddits | `https://awos.tirramind.com/?src=reddit` |
| Product Hunt | `https://awos.tirramind.com/?src=ph` |
| X | `https://awos.tirramind.com/?src=x` |
| Indian dev communities | `https://awos.tirramind.com/?src=devindia` |
| 1:1 DMs and interviews | `https://awos.tirramind.com/?src=interview` |
| Discord, Slack, newsletters, anything else | `https://awos.tirramind.com/?src=other` |

Recipe links (`/r/#...`) don't need a tag. Put tags on the landing page or `/recipes/` link next to them.

## Pre-launch checklist

- [ ] Hero video (demo_scripts.md, script 1) recorded, uploaded, and embedded on `/`. Self-healing and recipe clips uploaded for X and PH.
- [ ] `INSTALL_COMMAND` replaced everywhere: on the site, and in every file in this folder (`grep -rn INSTALL_COMMAND docs/launch/replay site`).
- [ ] Ran `INSTALL_COMMAND` on a clean Mac user account (or the sandbox VM). The model downloads, the extension loads, and Options shows `http://127.0.0.1:8080/v1`.
- [ ] Site deployed. `/`, `/recipes/` and `/r/` load on desktop and phone.
- [ ] Paddle stays **off**. Pro shows "$12/mo, waitlist", and there is no live checkout button anywhere.
- [ ] Waitlist form submits with the "what chore would you hand off?" answer, and `?src=` is stored. Test one tag per channel.
- [ ] Opened one `/r/#...` link from each of the 6 gallery recipes with the extension installed: the steps read correctly, and "Add to AWOS Replay" works (or the page says plainly that import is coming).
- [ ] Confirmed `/r/` sends nothing about the fragment to any server (DevTools Network tab: no request contains the recipe).
- [ ] Ran the hero chore twice end to end on the demo site: one VERIFIED and one forced CHECK or FAILED, so you can show both if asked.
- [ ] The "What the launch build does" table above re-checked against the shipped extension, and every unshipped claim cut.
- [ ] Repo is public, README has the install and known limits, and there's a license.

## Launch-day order of operations

All sent by the owner, by hand, from their own accounts. Times are your local morning; HN is best posted around 8–10am US Eastern on a weekday.

1. **T-1 day:** run the checklist. Post nothing.
2. **T0 (US morning): Show HN** (`show_hn.md`). Post the body as the first comment if HN strips the text. Stay in the thread for the first 3 hours and answer every question plainly. Don't ask anyone to upvote.
3. **T0 + 1h: X thread** (`x_thread.md`), with the hero video on post 1.
4. **T0 + 2h: r/LocalLLaMA** (from `communities.md`), angle: a 2B local model that knows when to say NOT_FOUND. One subreddit per day after that, not all at once.
5. **T0 + 3h: 5–10 personal DMs** to people who already said they hate a specific chore (from discovery notes). One message each, no follow-up blast.
6. **T0 + 1 day: Product Hunt** (`product_hunt.md`), only if HN went fine and nothing big is broken. Otherwise fix first; PH can wait a week.
7. **T0 + 2–7 days:** Discord/Slack communities and newsletters from `communities.md`, one or two a day, each written for that place.
8. **Every evening:** copy every "it broke when..." and every waitlist chore answer, verbatim, into the tracking sheet. Fix the top failure before posting anywhere new.
