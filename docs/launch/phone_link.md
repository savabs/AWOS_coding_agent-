# AWOS phone link (protocol v1)

When an AWOS Replay run on the Mac needs a person (a 2FA code, an approval, a
captcha, a login), the user can handle it from an iPhone or Android phone at
`https://awos.tirramind.com/phone/`.

Pieces:

| Piece | Where | Who |
|---|---|---|
| Relay (Cloudflare Pages Function, KV `RELAY`) | `site/functions/api/relay/[[path]].js` | site |
| Phone page (installable web app) | `site/src/pages/phone.html`, `site/src/assets/phone.js`, `phone-core.js`, `assets/phone/` | site |
| Pairing, sending needs, reading replies and SMS, ntfy alerts | `extension/` | extension |
| Tests | `site/tests/relay.test.js`, `site/tests/phone.test.js` | site |

## Privacy in one paragraph

App messages (needs, codes, approvals, status) are end-to-end encrypted with
AES-GCM. The key is in the pair link after `#`, which browsers never send to a
server. The relay stores only `{id, iv, ct}` under SHA-256 of the channel, for at
most 10 minutes, and each side deletes a message once it has handled it.
**Forwarded SMS texts are the one exception:** an iPhone Shortcut cannot encrypt,
so the text passes the relay in plaintext over HTTPS. It is cut to 500
characters, kept at most 10 minutes and deleted as soon as the Mac reads it.
ntfy alerts carry a fixed generic text only ("AWOS needs you: open the AWOS
phone page"), never codes or portal names.

## Protocol v1

### Pairing

The extension generates:

- `channel`: 16 random bytes, base64url (22 chars);
- `key`: AES-GCM 256 raw key, base64url (43 chars);
- `smsToken`: 16 random bytes, base64url;
- `ntfyTopic`: `"awos-"` + 16 random hex chars.

Pair link: `https://awos.tirramind.com/phone/#c=<channel>&k=<key>&t=<smsToken>&n=<ntfyTopic>`.
The extension calls `POST /api/relay/<channel>/register` with
`{"tokenHash": "<sha256 hex of smsToken>"}` at pairing.

The phone page stores the four values in `localStorage`
(`awos-phone-pair-v1`), removes the fragment from the address bar, and has a
**Forget this Mac** button. The page also accepts a pasted pair link (needed on
iPhone, where a home-screen web app has storage separate from Safari).

### Envelope

`{"id": "<random base64url, ≤ 64 chars>", "iv": "<base64url of 12 bytes>", "ct": "<base64url AES-GCM ciphertext+tag>"}`,
plaintext = UTF-8 JSON, AAD = the direction string, `"to-phone"` or `"to-mac"`.

### Relay API

All under `/api/relay/<channel>/`; `channel` must match `^[A-Za-z0-9_-]{22,128}$`.
Without the `RELAY` binding every route answers `503 {"error":"relay not configured"}`.

| Method + path | Body / query | Answer |
|---|---|---|
| `POST to-phone` / `POST to-mac` | envelope, ≤ 8 KB | `201 {"ok":true,"at":ms}`; same `id` again: `200 {"ok":true,"at":ms,"duplicate":true}` |
| `GET to-phone?since=<ms>` / `GET to-mac?since=<ms>` | | `200 {"messages":[{"at","id","iv","ct"}]}` with `at > since`, oldest first |
| `DELETE to-phone/<id>` / `DELETE to-mac/<id>` | | `200 {"ok":true,"deleted":0|1}` |
| `POST register` | `{"tokenHash":"<64 hex>"}` | `201` first time, `200` same hash again (refreshes it), `409` a different hash (first registration wins) |
| `POST sms` | header `X-AWOS-Token: <smsToken>`; body `{"text":"..."}` (JSON), plain text, or form `text=...`; ≤ 4 KB | `201 {"ok":true,"at","id"}`; `403` wrong/missing token or channel not registered |
| `GET sms?since=<ms>` | | `200 {"messages":[{"at","id","sms"}]}` |
| `DELETE sms/<id or at>` | | `200 {"ok":true,"deleted":n}` |

Rules: `at` is the server's clock in ms and strictly increases within a list,
so polling with `since=<last at seen>` never skips a message. Lists keep at most
50 items per direction (oldest dropped) and items expire after 600 s. Other
errors: `400` bad channel/body, `404` unknown route, `405` method, `413` too
large, `429` rate limited (120 requests/min per channel; `Retry-After: 30`).
No CORS headers: the phone page is same-origin, the extension uses host
permission, Shortcuts/MacroDroid/Tasker are not browsers.

Storage: `relay:<sha256(channel)>:{to-phone|to-mac|sms}` (TTL 600 s),
`relay:<h>:token` (= SHA-256(smsToken), TTL 400 days), `relay:<h>:rl:<minute>`
(write counter). Neither the raw channel nor the raw smsToken is stored.

### Messages (plaintext inside envelopes)

Mac → phone:

- `{"type":"need","req":"<id>","kind":"code"|"approve"|"captcha"|"login","portal":"<name>","message":"<text>","at":ms}`
- `{"type":"resolved","req":"<id>"}`
- `{"type":"status","summary":"5 of 6 got, 1 needs you","items":[{"portal","state","detail"}],"at":ms}`

Phone → Mac:

- `{"type":"code","req":"<id>","code":"<1-12 letters/digits>"}` (spaces and dashes removed)
- `{"type":"approve","req":"<id>","yes":true|false}`
- `{"type":"seen","req":"<id>"}` (sent once per page session when a need is shown; may repeat across reloads)

What the phone page does: polls `GET to-phone` every 4 s while visible (and at
once when it becomes visible), stops while hidden. A `need` shows a card (code:
numeric keypad with a "Code has letters?" switch; approve: Approve/Deny;
captcha/login: "Do this at your Mac: <portal>"). After a reply is sent it
deletes the `need` from `to-phone`. A `resolved` removes the card and deletes
both messages. The latest `status` is shown (and kept in localStorage); older
ones are deleted. Messages that do not decrypt are ignored and counted
("unreadable (re-pair?)"). All text is set with `textContent`.

What the Mac side should do (for the extension): delete each `to-mac` message
after handling it; delete its own `need` when sending `resolved` (the phone
also deletes it); send `resolved` after a code/approval was used or when the
need goes away on the Mac; read `sms`, extract the code locally, then delete
each SMS by `id`.

### Notifications

The extension posts the fixed text "AWOS needs you: open the AWOS phone page"
to `https://ntfy.sh/<ntfyTopic>` with `Click: https://awos.tirramind.com/phone/`.
Nothing else goes to ntfy.

## Phone setup for users

The phone page shows the same guides, with the user's own address and token
and Copy buttons.

### Get alerts (ntfy)

- **iPhone:** App Store → install **ntfy** → open it → **+** → paste the topic
  (`awos-…`, shown on the phone page under "Get alerts") → server stays
  `ntfy.sh` → **Subscribe** → allow notifications.
- **Android:** Google Play or F-Droid → install **ntfy** → **+** → paste the
  topic → **Subscribe** → allow notifications.

Tapping an alert opens the phone page. Anyone who knows the topic can read the
alerts, which is why they carry nothing private.

### Forward SMS codes

You need two values from the phone page ("Forward SMS codes automatically"):
the **address** `https://awos.tirramind.com/api/relay/<channel>/sms` and the
**token** (the `t=` value).

**iPhone (Shortcuts, iOS 17 or later):**

1. Shortcuts → **Automation** → **+** → **Message**.
2. **Message Contains**: `code`. Choose **Run Immediately** (optionally turn
   off *Notify When Run*) → **Next**.
3. **New Blank Automation** → **Add Action** → **Get Contents of URL**.
4. Tap the URL and paste the address.
5. Tap **›**: **Method** POST; **Headers** → add `X-AWOS-Token` = the token.
6. **Request Body** JSON → add a Text field: key `text`, value = the
   **Shortcut Input** variable, then tap it and pick **Content**. **Done**.
7. Repeat for `OTP` and `verification` (one phrase per automation).

**Android (MacroDroid):**

1. Install **MacroDroid** → **Add Macro**.
2. Trigger: **Phone/SMS → SMS Received** → any contact → **Text contains**
   `code` (add triggers for `OTP`, `verification`). Grant the SMS permission.
3. Action: **Connectivity → HTTP Request**, method **POST**, URL = the address.
4. Headers: `X-AWOS-Token` = the token.
5. Body: content type `text/plain`, body = magic text `[sms_message]`.
6. Save. Exempt MacroDroid from battery optimisation.

**Android (Tasker, alternative):**

1. Profile → **Event → Phone → Received Text**, type SMS, content
   `*code*/*OTP*/*verification*`.
2. Task → **Net → HTTP Request**: POST, URL = the address, Headers
   `X-AWOS-Token:<token>`, Body `%SMSRB`, Mime Type `text/plain`.
3. Save and keep the profile enabled.

Test: text yourself "test code 1234"; the Mac shows it within seconds.

## Owner: turn the relay on

```
npx wrangler kv namespace create RELAY      # prints an id
```

Then either add the binding in the dashboard (Pages → awos → Settings →
Bindings → KV namespace: variable `RELAY`, namespace `RELAY`, for Production
and Preview), or uncomment the `RELAY` block in `site/wrangler.toml` with the
real id. Then deploy as usual:

```
python3 site/build.py && python3 site/check_site.py
cd site && npm test && npx wrangler pages deploy public
```

Check: `curl -s https://awos.tirramind.com/api/relay/AAAAAAAAAAAAAAAAAAAAAA/to-phone`
answers `{"messages":[]}` (503 means the binding is missing).

## Limits and known gaps (v1)

- **KV is eventually consistent.** Writes are visible at once at the same
  Cloudflare location, but a phone and Mac that hit different locations can see
  a message up to about 60 s late. Codes usually live 5 to 10 minutes, so v1
  accepts this; a Durable Object relay would remove it.
- **Free KV quota:** 1000 writes and 100 000 reads per day. Each relay POST or
  DELETE is a list write plus a rate-counter write; polling (GET) never writes.
  The phone polls only while the page is visible: 900 reads/hour.
- **Rate limit** for GETs is per Worker isolate (best effort); writes are
  limited across isolates through KV.
- No web push: alerts come through ntfy. Captchas and logins must be done at the Mac.
- On iPhone, a home-screen web app has its own storage: pair in it by pasting
  the pair link (the page explains this).
