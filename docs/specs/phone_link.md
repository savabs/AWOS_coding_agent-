# AWOS Replay phone link (v0.8): extension side and protocol v1

Status: implemented in `extension/` 0.8.0 (`phone.js`, `lib/phone.js`, `lib/qr.js`, `shot.html`).
The relay and the phone page live in `site/` (another branch; see `docs/launch/phone_link.md` there).
Tests: `tests/extension/test_phone_link.py` (fake relay + fake ntfy in `tests/extension/conftest.py`),
`tests/extension/test_qr_unit.py`.

## 1. Why

A portal run stops for a human (a one-time code, an approval, a bot check, a login) while the user is away
from the Mac. With the phone link the user answers codes and approvals from the phone, and sees each run's
result there. The user also asked for fewer pings: "I'd rather lose one morning than get pinged every few days".

## 2. Pairing

Options -> **Phone** -> **Pair a phone** makes, in the extension (`AWOSPhone.newPairing`):

| field | value |
|---|---|
| `channel` | 16 random bytes, base64url (22 chars) |
| `key` | AES-GCM 256 raw key, base64url (43 chars) |
| `smsToken` | 16 random bytes, base64url (22 chars) |
| `ntfyTopic` | `"awos-"` + 16 random hex |

These are stored in `chrome.storage.local.phone`, together with `base` (the relay, default `https://awos.tirramind.com`,
editable in Options for testing), `ntfyBase` (default `https://ntfy.sh`), `pairedAt` and `register` (the last
register answer).

- Pair link: `<base>/phone/#c=<channel>&k=<key>&t=<smsToken>&n=<ntfyTopic>`. It is shown as a QR code drawn in the
  extension (`lib/qr.js`: byte mode, ECC M, no external code) plus **Copy pair link**. The secrets are in the
  fragment, which no server receives.
- Register: `POST <base>/api/relay/<channel>/register {"tokenHash": sha256hex(smsToken)}`. The first registration
  wins. On 409 Options says: Unpair, then pair again (that makes a new channel). On 503 it says the relay is not
  configured yet. Pairing is kept in both cases and runs work as before.
- **Unpair** removes `phone`, any open need, and the poll alarm.

## 3. Envelope and relay API (as implemented)

- Envelope: `{"id","iv","ct"}`, all base64url without padding. `id` is 16 random bytes and `iv` is 12 bytes
  (16 chars). The cipher is AES-GCM with AAD = the direction (`"to-phone"` / `"to-mac"`); the plaintext is JSON.
- `POST <base>/api/relay/<ch>/to-phone` with the envelope. The relay answers 201 `{"ok":true,"at":ms}`, or 200
  `{"duplicate":true}` for an id it already has. `at` is server time and is monotonic per list.
- `GET .../to-mac?since=<ms>` returns `{"messages":[{"at",...envelope}]}`. The Mac starts at the need's relay
  `at` - 1 s, then moves `since` to the last `at` it has seen.
- `DELETE .../to-mac/<id>`: every to-mac message the Mac reads is deleted once it is handled. That includes one for
  an earlier request and one that does not decrypt.
- `GET .../sms?since=<ms>` returns `{"messages":[{"at","id","sms"}]}`. These are the plaintext SMS the phone
  forwarded. The Mac polls it only while a `code` need is open, and deletes every SMS it reads (`DELETE .../sms/<id>`).
- 503 means the relay is not configured. 429 and 5xx mean back off for `Retry-After` seconds (else 30 s) and poll
  again. A waiting run never gives up on the phone by itself; a collection still moves on after `collectionWaitMin`.
- Host permission: the relay sends no CORS headers, so the service worker reaches it through the `<all_urls>` host
  permission (section 7).

## 4. Messages

mac -> phone (`to-phone`):
- `{"type":"need","req","kind":"code"|"approve"|"captcha"|"login","portal","message","at"}`. `req` is 12 random bytes
  (base64url), never the run's approval nonce. `portal` is the recipe name. `message` is the popup's pause text.
- `{"type":"resolved","req"}` is sent exactly once per need when the pause ends by any route: the phone's answer,
  Continue / Approve / Stop in the popup, the run failing, or the collection moving on.
- `{"type":"status","summary","items":[{"portal","state","detail"}],"at"}` is sent at the end of every run outside a
  collection, at the end of every collection (one item per portal: got / missing / needs_you / ...), and at the
  batched login moment (section 6).

phone -> mac (`to-mac`):
- `{"type":"code","req","code"}`: valid for a `code` need. The code must be 4-8 letters / digits.
- `{"type":"approve","req","yes"}`: valid for an `approve` need. `yes: true` is the popup's Approve; anything else is
  Stop (the run ends REJECTED, nothing is clicked).
- `{"type":"seen","req"}`: logged once ("you opened this on your phone"). It is idempotent.

## 5. Behaviour when paired

- `pause()` sends one encrypted `need` for a one-time-code handoff (`code`), an approval gate (`approve`), a bot check
  (`captcha`) or a login handoff (`login`), plus one ntfy push. Pushes are at most one per `phonePushGapMs` (60 s).
  Other pauses (missing param, a pop-up, a secret field to type) stay on the Mac.
- While the need is open, the worker polls `to-mac` every `phonePollMs` (5 s). For a `code` need it also polls `sms`.
  A `chrome.alarms` backup (30 s) restarts the poll after a worker restart; the need is in `storage.session`.
- **A code** comes from the phone's `code`, or from a forwarded SMS. For an SMS, `AWOSPhone.extractCode` takes a 4-8
  digit number, or a 6-character code with a digit, closest to a cue word (code / OTP / verification / passcode /
  PIN / 2FA / security / login / sign-in, within 80 chars). The newest SMS with a code wins.
  - Only an SMS stamped at or after the need's relay time minus `phoneSmsGraceMs` (30 s) is used: see section 9.
  - The content script (`phone_code`) puts the code into the page's one-time-code field (`AWOSPortal.otpField`, the
    same rule that classifies the page as `otp`). It then submits as a person would (`pressEnter`: key events, then
    the form's default button or `requestSubmit`).
  - The worker waits for the navigation and then goes through exactly the popup's Continue path
    (`decide('continue')`): it re-observes the page, goes back to the step's page if needed, and continues.
  - A code arriving while the pause is not a one-time-code handoff is ignored.
- **Bot checks and logins** stay human-at-the-Mac in v1: the phone shows them and the run waits as before.
- **Codes are never stored**: not in the run log (it says "typed the code from your phone / a forwarded SMS ... (the
  code is not logged)"), not in run state, storage, the export, notifications or ntfy. The test asserts this over
  `get_state`, all of `storage.local` and `storage.session`, and `export_log`.
- **Relay down / 503**: the run log says the phone could not be reached and the handoff stays on the Mac. The
  popup's phone line says "the relay is not configured (503): handoffs stay here on the Mac".
- **ntfy**: `POST <ntfyBase>/<ntfyTopic>`, body `AWOS needs you: open the AWOS phone page`, headers
  `Title: AWOS Replay`, `Click: <base>/phone/`, `Tags: bell`. The text is fixed: never a code, a portal name or page
  text. A push goes with a need, with a status that has something not fine (any verdict other than
  VERIFIED / UNVERIFIED, any portal not got), and with the batched login notice. A fully good result does not ring.

## 6. Fewer pings (batching, default on)

- Keep-alive sweeps no longer notify a logout. The portal shows **Login needed** in the checklist, `portalStatus`
  stays un-notified, and `keepAliveSweep` returns `batched`.
- Logouts are summarised ONCE at the pre-flight alarm or the collection's run-start check: "N portal(s) need a
  login. Log in to ...". That is one Mac notification, plus one phone `status` and one push when paired.
- Options -> Notifications -> "Notify each logout right away" (`settings.immediateNotices`) restores the 0.6.1
  per-logout notice.

## 7. Failure evidence

- **Slow runs**: `rec.durations` keeps the last 10 active run times. Active time is end minus start minus the time
  paused for a human. Only VERIFIED / CHECK / UNVERIFIED runs are counted. The check starts once 3 runs are recorded.
  A run longer than `max(2 x median, median + slowRunExtraMs)` (3 min by default) gets `result.slow` and
  "slower than usual: X (usually Y) ..." in `result.reason`. That shows in the popup, the recipe's last run, the run
  log and the phone status.
- **Screenshots**: when a run pauses with needs_user, or ends FAILED / MISSING / WRONG_PERIOD (NEEDS_YOU included),
  `chrome.tabs.captureVisibleTab` (JPEG q60) of the run tab is taken. This only happens if the run tab is the active
  tab of a window that is not minimised; otherwise the log says "tab not visible".
  - Storage: `chrome.storage.local.shots`, the last 20, and at most 5 MB.
  - `run.shot = {id, note, step, why}`. The popup shows "Screenshot of the failure", which opens `shot.html?id=`.
  - Screenshots are never sent to the phone, the relay or a model. Password fields render as dots, but anything else
    on screen is in the picture.
- **Permission**: `captureVisibleTab` needs `<all_urls>` or `activeTab`. Neither was in 0.7: the content script's
  http/https matches do not count, and Chrome said "Either the '<all_urls>' or 'activeTab' permission is required"
  on a host-permitted 127.0.0.1 page. 0.8 adds `"<all_urls>"` to `host_permissions`. It adds no new install warning,
  because the content script already runs on every http(s) site and Chrome already shows "read and change all your
  data on all websites". It also covers the relay and ntfy.sh, which send no CORS headers. Justification is in
  `packaging/permission_justifications.json`.

## 8. Settings (chrome.storage.local.settings)

`immediateNotices` (false), `phonePollMs` (5000), `phonePushGapMs` (60000), `phoneSmsGraceMs` (30000),
`slowRunExtraMs` (180000). Only `immediateNotices` is in the Options UI; the tests use the others.

## 9. Limits and open points

- SMS timing: the protocol says never to use an SMS older than the need. The portal usually texts the code as the code
  page loads, which can be before AWOS notices the page and posts the need. The extension therefore accepts SMS
  stamped up to 30 s before the need's relay time (`phoneSmsGraceMs`; 0 = strict). Both times are relay clock, so
  Mac clock skew does not matter.
- Single-field codes only: a 6-box code input (one character per box) is not filled; the run waits for the user.
- Only replay runs and collections use the phone. Tell-it runs do not yet.
- Screenshots of background-tab runs (scheduled, collection) are not possible with `captureVisibleTab`; they log
  "tab not visible".
- One open need at a time (one run at a time). A new pause resolves the previous need first.
