# AWOS Pro on Paddle: owner setup

Sandbox first, then live. Do the steps in order; each ends with a check.
Code: `site/functions/api/paddle/*.js`, `site/functions/api/license/*.js`,
`site/lib/paddle.js`, `scaffold/agent/licence.py`. Tests: `node --test` in
`site/`, `pytest tests/test_licence.py tests/test_usage_ping.py`.

Rule carried over from tirramind (`docs/research/paddle_checkout_exposure.md`
there): a checkout must never be reachable before fulfilment exists server-side,
and a client-side flag is not a gate. Here the page holds no token or price ID;
`/api/paddle/config` hands them out only when `PADDLE_CHECKOUT_ENABLED=1` and
every secret and the `LICENSES` store are configured. Paddle itself ignores the
webhook for any price not listed in `PADDLE_PRICE_IDS`.

## 0. Close the old tirramind exposure (live account, do this first)

The same Paddle account still has the four old tirramind live prices, which
were published in a public repo with a live client token.

1. Paddle (live) → Catalog → Products → each old tirramind product → archive
   all 4 prices, then archive the products.
2. Check: in a browser console on any page with Paddle.js loaded in live mode,
   `Paddle.Checkout.open({items:[{priceId:"<old pri_...>",quantity:1}]})` is
   rejected. (Or: Catalog shows them under "Archived".)
3. Optional: Developer tools → Authentication → revoke the old live client-side
   token `live_f2e8…` if nothing else uses it.

## 1. Sandbox account: product, price, keys

Log in at https://sandbox-vendors.paddle.com (separate login from live).

1. Catalog → Products → New product: name `AWOS Pro`, tax category
   "Standard digital goods" (SaaS). Save.
2. Add price: recurring, monthly, the amount you choose (open question:
   the price), no trial. Copy the price id `pri_...`.
3. Developer tools → Authentication:
   - API key: create one with read access to transactions (and customers,
     subscriptions). Copy it (`pdl_sdbx_apikey_...`). This is `PADDLE_API_KEY`.
   - Client-side token: create one (`test_...`). This is `PADDLE_CLIENT_TOKEN`.
4. Checkout → Checkout settings → Default payment link:
   `https://awos.tirramind.com/pro/`. Paddle refuses to open checkouts without it.

## 2. Sandbox webhook destination

Developer tools → Notifications → New destination:

- URL: `https://awos.tirramind.com/api/paddle/webhook`
- Type: webhook
- Events (exactly these):
  - `subscription.created`
  - `subscription.activated`
  - `subscription.updated`
  - `subscription.canceled`
  - `subscription.past_due`
  - `transaction.completed`
- Save, open the destination, copy the secret key (`pdl_ntfset_...`).
  This is `PADDLE_WEBHOOK_SECRET`.

## 3. Cloudflare: KV namespaces and secrets

From the repo root:

```
cd site
npx wrangler kv namespace create USAGE
npx wrangler kv namespace create LICENSES
```

Paste the two ids into `site/wrangler.toml` in place of
`REPLACE_WITH_USAGE_NAMESPACE_ID` and `REPLACE_WITH_LICENSES_NAMESPACE_ID`.
A deploy fails while the placeholders are there, which is the point.

Secrets (each command prompts for the value; nothing goes in the repo):

```
cd site
npx wrangler pages secret put PADDLE_API_KEY
npx wrangler pages secret put PADDLE_WEBHOOK_SECRET
npx wrangler pages secret put PADDLE_CLIENT_TOKEN
npx wrangler pages secret put PADDLE_PRICE_IDS      # pri_... (comma-separate several)
```

`ADMIN_TOKEN` already exists (request-access); it also guards `/api/usage`.
Leave `PADDLE_ENV = "sandbox"` and `PADDLE_CHECKOUT_ENABLED = "0"` in
`wrangler.toml` for now.

Build, check and deploy:

```
python3 site/build.py && python3 site/check_site.py
(cd site && node --test tests/*.test.js)
cd site && npx wrangler pages deploy public
```

Checks after deploy:

- `curl -s https://awos.tirramind.com/api/paddle/config` → `{"enabled":false}`
- `curl -s -X POST https://awos.tirramind.com/api/paddle/webhook -d '{}'` → 401
- Paddle → Developer tools → Simulations → new single-event simulation,
  `subscription.activated`, to this destination → run: Paddle logs HTTP 200
  and the response body
  `{"ok":true,"handled":false,"reason":"price not in PADDLE_PRICE_IDS"}` (the
  sample event uses another price; that is the price filter working).

## 4. Turn the sandbox checkout on and buy with a test card

1. In `site/wrangler.toml` set `PADDLE_CHECKOUT_ENABLED = "1"`, deploy again.
2. `curl -s https://awos.tirramind.com/api/paddle/config` →
   `{"enabled":true,"env":"sandbox","client_token":"test_...","price_id":"pri_..."}`
3. Open https://awos.tirramind.com/pro/ → "Get AWOS Pro" → overlay checkout.
   Card `4242 4242 4242 4242`, any future expiry, CVC `100`, any name,
   any email you can read. (Paddle's sandbox test cards:
   https://developer.paddle.com/concepts/payment-methods/credit-debit-card)
4. On success the page goes to `/pro/welcome/?txn=txn_...`, polls, and shows
   `awos activate awos_...`.
5. On your laptop: `awos activate awos_...` → "AWOS Pro is active".
   `awos licence status` → Plan: AWOS Pro.
6. Lifecycle checks in sandbox (Paddle → Subscriptions → the subscription).
   The CLI revalidates at most daily; to force a fresh check run
   `awos activate <key>` again.
   - Cancel at period end → still active until `current_period_end`.
   - Cancel immediately → inactive (the paid period is over).
   - Past due (Developer tools → Simulations → subscription past due scenario
     against a test subscription) → still active for 3 days
     (`PAST_DUE_GRACE_DAYS`), then inactive unless payment recovers.
7. Usage counts: on the laptop `awos telemetry on`, run one job, then
   `curl -s "https://awos.tirramind.com/api/usage?token=$ADMIN_TOKEN"`.

## 5. Go live (checklist)

Do not start until every sandbox check above passed.

- [ ] Step 0 done: the 4 old tirramind live prices are archived.
- [ ] Live account → Checkout → Website approval: add `awos.tirramind.com`
      and wait for approval (Paddle reviews the site: pricing page, terms,
      refund policy and privacy pages must be reachable from it).
- [ ] Terms, refund policy and privacy pages exist on awos.tirramind.com and are
      linked from `/pro/` (open question: not built yet).
- [ ] Live: create the `AWOS Pro` product and price; copy the live `pri_...`.
- [ ] Live: API key (`pdl_live_apikey_...`) and client token (`live_...`).
- [ ] Live: default payment link `https://awos.tirramind.com/pro/`.
- [ ] Live: notification destination, same URL and the same 6 events; copy its secret.
- [ ] `PADDLE_CHECKOUT_ENABLED = "0"` and `PADDLE_ENV = "live"` in `wrangler.toml`.
- [ ] `wrangler pages secret put` all four PADDLE_* secrets again with the live values.
      (The config endpoint refuses a `test_` token when `PADDLE_ENV=live`.)
- [ ] Deploy; `/api/paddle/config` still `{"enabled":false}`; a POST to
      `/api/paddle/webhook` without a signature returns 401.
- [ ] Set `PADDLE_CHECKOUT_ENABLED = "1"`, deploy, buy once with a real card,
      activate the key, then refund yourself from the Paddle dashboard and
      confirm the licence follows the cancellation.
- [ ] Note: the sandbox licences in KV `LICENSES` stay valid against the live
      site. Delete `lic:*` / `sub:*` keys created during sandbox testing
      (`npx wrangler kv key list --binding LICENSES`) before announcing.

## Reference: what each endpoint stores

KV `LICENSES`:

| key | value |
|-----|-------|
| `lic:<awos_key>` | `{subscription_id, customer_id, status, plan, price_id, current_period_end, grace_until, updated, occurred_at}` |
| `sub:<sub_id>` | `<awos_key>` |
| `evt:<event_id>` | processed webhook ids (30-day TTL) |
| `claim:<txn_id>`, `claimrl:<txn_id>` | how often a key was shown / claim attempts this hour |

KV `USAGE`: `day:<YYYY-MM-DD>:<event>` → `{"total": n, "outcome=solved": n, ...}`.
No IP addresses or install ids.
