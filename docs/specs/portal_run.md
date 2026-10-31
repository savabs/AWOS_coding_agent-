# Portal runs (AWOS Replay 0.6)

Status: built (extension 0.6.0). Code: `extension/background.js` (driver, collections), `extension/lib/portal.js`
(page classification, pop-up dismissal), `extension/content.js` (messages), `extension/popup.js` (checklist).
Tests: `tests/extension/test_portal_run.py` (fixture portals in `tests/extension/pages/portal/`).

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

## Permissions

None added. `downloads` (0.4) now also starts downloads (`chrome.downloads.download`) for files that opened in a new
tab; its store justification already says "Replay starts that download and checks it finished".

## Known gaps

- A login page that appears after the last step (before the final checks) is not handed off; the checks fail.
- A file shown by a viewer that runs no page script and whose URL and title do not name a file is not recognised.
- Page-kind detection is heuristic: a page with an unrelated password field (change-password settings) at a URL other
  than the recorded one hands off unnecessarily; an invisible bot check is not seen (it needs nothing from the user).
- A portal left as "Needs you" restarts from its first step on the next collection run (the dedupe is per run).
- Pop-ups inside cross-origin iframes are not seen (main frame only, as before).
