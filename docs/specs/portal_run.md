# Portal runs (AWOS Replay 0.6)

Status: built (extension 0.6.1). Code: `extension/background.js` (driver, collections, logins), `extension/lib/portal.js`
(page classification, pop-up dismissal, remember me, autofill sign-in), `extension/content.js` (messages),
`extension/popup.js` (checklist), `extension/options.html` (login Options).
Tests: `tests/extension/test_portal_run.py`, `tests/extension/test_fewer_handoffs.py` (0.6.1); fixture portals in
`tests/extension/pages/portal/`.

## Why

Five of six people who answered our Reddit discovery post log into several supplier, vendor, partner or payout
portals every month to download invoices, statements or payout CSVs. What breaks their automation, in their words:

1. a login expiring, or a 2FA code / captcha appearing halfway: "pause, let me handle that bit, and resume without
   downloading everything twice; that handoff matters more than a perfect first-run demo"
2. "new feature" / maintenance pop-ups right after login
3. the PDF opening in a new tab the script never caught
4. silent green runs; "flag anything missing"

0.6 builds exactly these four for recorded replays. Nothing here changes the safety rules: risky clicks wait for
Approve, passwords and codes are typed by the user, bot checks are never solved or bypassed, there is no stealth.

## 1. Handoff and resume

**Detection.** Before every non-goto step the content script classifies the page (`AWOSPortal.pageKind`):

| kind | signal |
|---|---|
| `captcha` | a visible challenge frame (reCAPTCHA, hCaptcha, Turnstile, ... by `src` / `title`, >= 60x40 px, not the invisible-reCAPTCHA corner badge), a visible captcha container (`.g-recaptcha`, `[data-sitekey]`, `[class*=captcha]`, ...), or a text such as "verify you are human", "I'm not a robot", "checking your browser" |
| `otp` | a visible field `AWOSSafety.sensitiveField` flags as a one-time code (`autocomplete=one-time-code`, "verification code", "2FA", "OTP", ...) |
| `login` | a visible password field, or a text such as "your session has expired", "please sign in again" |

The worker hands off when:
- `captcha`: always (AWOS never touches a bot check), except right after the user's Continue on the very page the
  step was recorded on (a solved widget can stay on a form);
- `login` / `otp`: only when the recording was not on that page for this step: the tab's origin + path differs from the
  step's recorded `url`. A recording that includes its own login (password fill on the login page) is not a handoff;
  its password step pauses for the user as before.

A goto that the site redirects (an expired session sends you to `/login`) is accepted and logged; the next step's
check sees the login page.

**Pause.** `needs_user` with `pending.handoff = login | otp | captcha`, the message "Log in / Enter the code / Solve the
check in this tab, then click Continue", the in-page banner and a notification (always, not only for scheduled runs).
Nothing on the page is clicked or typed while paused.

**Continue.** `decide('continue')` sets `run.resume`. The driver then re-observes:
- if the tab is not on the recorded page of the first step not yet done (a re-login usually lands on a dashboard),
  it navigates back to that step's recorded `url` (not when the URL matches the danger vocabulary, or leaves an
  imported recipe's site), logs it, and continues from that step;
- if a human is still needed, it pauses again ("Still waiting: ...").

**No work twice.** `run.done[i]` marks every completed step, and `run.i` is the first step not done, so a resume never
repeats a step. A click step that downloads (recorded with `download: {name}`, or a link whose path is a file such as
`.pdf` / `.csv`) is skipped and logged when a matching download already completed in this run
(`chrome.downloads.search` since the run started; matched by the link's file path, else by the recorded file name,
the same names `expect_download` checks; Chrome's `name (1).pdf` counts as the same file). The key must be unique in
the recipe: two `statement.pdf` links on two accounts are two files and are never deduplicated.

**Worker restarts.** Everything a pause needs (`run`, `pending`, `run.done`, `run.resume`, the collection queue) lives
in `chrome.storage.session`. On start, `restoreAfterRestart()` keeps a paused run paused (a run that was actively
driving is failed as INTERRUPTED, as before: its last click may have half-run). The test stops the worker for real
(CDP `Target.closeTarget`) during a handoff pause and resumes afterwards.

## 2. Collections: the monthly checklist

A collection (`collections` in `chrome.storage.local`) groups recipes, one portal each, each with its expected
download. The download pattern may use `{{year}}`, `{{month}}` (two digits) and `{{month_name}}`, the period's
values (a recipe's own param of the same name wins); e.g. `statement-{{year}}-{{month}}\.pdf$`. `monthOffset: -1`
collects last month's documents.

**Run collection** (`coll_run`): the period is `YYYY-MM` (this month, or last month with the offset). Portals already
**Got** in that period are skipped; the others run one after another, each in its own background tab, through the
normal replay driver (handoffs, pop-ups, new-tab files, approval gates). A user-started collection uses the normal
gate vocabulary; a scheduled one the strict one, like scheduled recipes.

Per portal and period (`collResults[collId][period][recId]`):

| status | when |
|---|---|
| `got` (file) | the download check passed (file name shown); or no download is expected and the run verified |
| `missing` | the portal ran to the end but no file matched the pattern, or the download link itself is gone (NOT_FOUND on a download step) |
| `needs_you` | the portal paused (login, code, check, approval, pop-up). While it waits it shows "Needs you (waiting)"; **Skip this portal** in the popup, or nobody answering within `settings.collectionWaitMin` (default 10 min, a `chrome.alarms` alarm), leaves its tab open for the user and moves on |
| `failed` (reason) | anything else (the reason is the run's code and message) |

At the end: a summary notification, e.g. "4 of 6 got, 1 missing, 1 needs you", and `collection.last`. The popup's
**Collections** section shows the checklist for the current period, the running portal, Run / Skip / Delete, and a
monthly schedule (day 1-28 + time, `chrome.alarms` like recipe schedules; `nextFire` gained `kind: 'monthly'`).
Results are kept for 24 periods.

## 3. Pop-ups after login

When a step's target is not found, or is found but drawn under an overlay (`AWOSPortal.coveredBy`: the topmost
element at the target's centre belongs to a dialog / `aria-modal` / fixed-position box that does not contain the
target), the driver runs one unblock pass (`AWOSPortal.unblock`) and retries the step once.

Blockers: open dialogs / `[role=dialog]` / `[aria-modal=true]`, fixed or sticky overlays covering >= 25% of the
viewport, and cookie / consent banners (id / class / label / text mentions cookie, consent, GDPR, ...). For each, at
most one control is clicked:
- pop-ups: close / x / dismiss / not now / maybe later / skip / got it / no thanks / remind me later / continue to site
- cookie / consent banners: only reject / decline / deny / necessary-only / essential-only / close (reject preferred)
- never: anything with accept, agree, allow all, consent, subscribe, sign up, upgrade, trial, buy, pay, enable, yes;
  anything the strict danger vocabulary matches in the control's own name (submit, confirm, accept, order, ...);
  anything whose context (form / dialog) matches the danger vocabulary (a "Not now" inside "Delete your account?").

A blocker with no allowed control (e.g. a terms modal that only offers "Accept and continue") is not touched: the
run pauses `needs_user` (`pending.popup`) and names the controls it saw; the user deals with it and clicks Continue.
Every dismissal is logged ("dismissed a pop-up (...) with "Not now"") and kept in `run.dismissed`.

## 4. Files that open in a new tab

After every replayed click the driver looks for a tab whose `openerTabId` is the run tab and that did not exist
before the click (it waits up to 5 s when the recording marked the click `opensTab`). If that tab shows a file (the
path has a file extension such as `.pdf`, `.csv`, `.xlsx`, or the page script reports a non-HTML `document.contentType`,
or no page script runs and the title names a file), it is downloaded with `chrome.downloads.download` (the browser's
cookies apply, so the portal session is used), the tab is closed, and the file counts for `expect_download` like any
other download. A file the browser already started downloading itself in this run is not downloaded again.

Auto-download only for the same origin as the portal page, or HTTPS on the same registrable site
(`files.portal.example` from `portal.example`). Any other file pauses `awaiting_approval`: Approve downloads it,
Stop ends the run. A new tab that is not a file is left open and logged.

The recorder marks a click that opened a tab (`opensTab: true`) and the file it showed or downloaded
(`download: {name}`); recipes export these as `opens_tab` / `downloads` booleans without the file name.

## 5. Fewer handoffs: logins (0.6.1)

Most handoffs left in a monthly run are expired logins. 0.6.1 finds them early and puts them in one short sitting.

**Login check (`probePortal`).** Opens the recipe's recorded start URL in a background tab, waits for it to load and go
quiet, classifies it with `AWOSPortal.pageKind` and closes the tab. `login` / `otp` -> **Login needed**; nothing ->
logged in (`ok`); `captcha` and `error` are recorded but are not "Login needed". It clicks nothing and types nothing
(not even the pop-up dismissals the rules would allow). Not opened: a start URL matching the danger vocabulary, or a
recipe that signs in by itself (it has a password step: `self_login`). The result goes to `portalStatus[recId]`
(`chrome.storage.local`: status, why, landed URL without query, time, source). One check at a time (a shared lock),
`keepAliveSpacingMs` (default 8 s) between portals.

**Keep-alive.** Per collection, on by default (`keepAlive`, `keepAliveDays` = 3; popup: Collections -> Logins). An
alarm `awos-keep:<id>` checks every portal of the collection every N days. Skipped, and retried in 30 min, while a
replay, collection, recording or Tell-it run is in progress; a sweep stops before its next portal if one starts. A
portal newly found logged out is notified once (one notification per sweep: "Supplier X logged you out; log in any
time before the month-end run", button **Open the ones that need me**); it is notified again only after it was seen
logged in in between. The checklist shows "Login needed (checked <time>)" until the portal is got for the period.
**Check logins now** runs the same sweep by hand.

**Pre-flight before a scheduled run.** `preflightHours` (default 24, 0 = off) before a monthly scheduled run, an alarm
`awos-pre:<id>` checks the portals not yet got for that period and sends ONE notification naming those that need a
login ("Log in to A and B any time before then"), with **Open the ones that need me**. Busy: retried in 30 min while
the run is more than an hour away.

**Pre-flight at the start of every collection run.** `coll_run` returns at once; the run first checks every portal
in its queue (`preflightSpacingMs`, default 1 s apart). The logged-out ones move to the end of the queue, show "Login
needed (waiting for you)", and ONE notification names them ("1 portal is ready and running now. Log in to A and AWOS
picks it up as soon as you are in."). The ready portals run first, without interruption. When only logged-out
portals are left, the run waits for a login, polling every `loginPollMs` (5 s; a `chrome.alarms` backup wakes the
worker): a login tab the user opened from **Open the ones that need me** is asked for its page kind (a page on the
portal's site that is not a login page counts as logged in; its load also triggers a look right away), otherwise the
start page is checked in a background tab at most every `loginProbeMs` (60 s). The first portal whose login is seen
runs next (`coll.pickedUp`). Nobody logging in within `collectionWaitMin` (10 min, the existing collection wait,
counted from when the run starts waiting) leaves those portals as **Needs you** ("Login needed: still logged out
after 10 min"); a re-run skips the ones got. A portal whose start page is fine but that logs out later still hands
off as in section 1. A worker restart during the pre-flight redoes it; during the wait it resumes the wait.

**Open the ones that need me** (`coll_open_logins`: the notification's button or a click on it, or the popup button):
one tab per portal of the collection whose last check said Login needed and that is not got this period, the first
in front, nothing else. On each, the two helpers below run once.

**Remember me** (Option `rememberMe`, default on). On a login handoff (section 1) and on the tabs opened above, before
the user logs in, `AWOSPortal.rememberMe` ticks ONE visible, unchecked checkbox whose whole label (case-insensitive,
trailing punctuation dropped) is: remember me / keep me signed (logged) in / stay signed (logged) in, optionally "on
this device"; remember this device / trust this device / don't ask (for a code) again (on this device); optionally
"for N days". Anything else (newsletter, terms, "remember me and send me offers", cookie choices) is never touched; a
ticked box is never unticked. Logged: `ticked "keep me signed in" on the login page ...`.

**Chrome-autofilled logins** (experimental; Option `autofillSignin`, "Use Chrome's saved passwords to sign in", default
on). On a login handoff, if the page has exactly one visible password field, a username field (email / text / tel)
before it in the same form, and BOTH match `:autofill` / `:-webkit-autofill` and not `:placeholder-shown`,
`AWOSPortal.autofillSignin` clicks that form's sign-in button (named sign in / log in / log on / continue / next /
submit) once per step, logs it, and the run continues as after the user's Continue (back to the step's page; still a
login page -> the normal handoff, no second click). Anything else -> the normal handoff, with the reason logged
("Chrome has not autofilled the username").
- AWOS never reads, logs, stores, types or sends the password, nor the username: the page script looks only at the
  fields' type and pseudo-classes, and returns only whether it clicked and why. The tests assert the fixture's
  password string appears nowhere in the run log, run state, storage or notifications.
- **Chrome may not let this fire.** Chrome often shows a saved password only as a preview, or keeps the value back
  from the page until a real user gesture (a click or key press in the page); a script's click is not one. Then the
  fields are not `:autofill`, or the form submits without the password and the login page comes back; either way the
  run hands off as before. Two-step logins (username page, then password page) and logins inside iframes are not
  handled. Whether it fires on a given portal has to be found out on real sites; it is not proven.
- Testing: Playwright cannot make Chrome autofill a saved password. The decision logic is tested with
  `pages/portal/login_autofill.html`, which fills its own fields and marks them `data-awos-test-autofill`. The
  extension honours that attribute only when the worker passes `testHook`, which it reads from
  `chrome.storage.session.testAutofillHook` (set by the test through the worker; web pages and content scripts cannot
  read or write session storage). In normal use it is never set.

Settings (Options; `chrome.storage.local.settings`): `rememberMe`, `autofillSignin`, plus the tuning knobs
`keepAliveSpacingMs`, `preflightSpacingMs`, `loginPollMs`, `loginProbeMs` (no UI). Collection fields: `keepAlive`,
`keepAliveDays`, `preflightHours`, `preflightAtStart` (default true), `lastKeepAlive`, `lastPreflight`.

## Permissions

None added in 0.6 or 0.6.1. The keep-alive and pre-flight use `tabs` (background tabs on the recipe's own start
page), `alarms`, and `notifications` (now with a button), all already requested. `downloads` (0.4) now also starts downloads (`chrome.downloads.download`) for files that opened in a new
tab; its store justification already says "Replay starts that download and checks it finished".

## Known gaps

- A login page that appears after the last step (before the final checks) is not handed off; the checks fail.
- A file shown by a viewer that runs no page script and whose URL and title do not name a file is not recognised.
- Page-kind detection is heuristic: a page with an unrelated password field (change-password settings) at a URL other
  than the recorded one hands off unnecessarily; an invisible bot check is not seen (it needs nothing from the user).
- A portal left as "Needs you" restarts from its first step on the next collection run (the dedupe is per run).
- Pop-ups inside cross-origin iframes are not seen (main frame only, as before).
- Keep-alive only helps where visiting a page extends the session. Sites with a hard session lifetime (log in again
  every 24 h whatever you do) still log the user out; the keep-alive then only reports it early. A background tab
  briefly appears in the tab strip during each check.
- Login detection is the same heuristic as the handoff: the username-only first page of a two-step login does not
  look like a login page, so a waiting run may start such a portal too early (it then hands off as usual).
- The autofill sign-in is unproven on real sites (section 5); "remember me" only helps where the portal honours it.
