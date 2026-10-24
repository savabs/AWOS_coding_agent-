// node --test site/tests/   (Node >= 20; no dependencies)
// Fake KV + fake fetch; exercises the Pages Functions as Cloudflare calls them.
import { test } from "node:test";
import assert from "node:assert/strict";

import { hmacSha256Hex, base32, newLicenceKey, LICENCE_KEY_RE, safeEqual } from "../lib/common.js";
import { verifySignature, accessFor, fulfilmentMissing } from "../lib/paddle.js";
import { onRequestPost as webhook } from "../functions/api/paddle/webhook.js";
import { onRequestGet as config } from "../functions/api/paddle/config.js";
import { onRequestGet as claim } from "../functions/api/license/claim.js";
import { onRequestPost as check } from "../functions/api/license/check.js";
import { onRequestPost as ping, cleanPing } from "../functions/api/ping.js";
import { onRequestGet as usage } from "../functions/api/usage.js";
import { onRequestPost as accessPost, onRequestGet as accessGet, segmentFor, cleanWaitlist, waitlistCsv }
  from "../functions/api/request-access.js";

class FakeKV {
  constructor() { this.m = new Map(); this.puts = 0; }
  async get(k) { return this.m.has(k) ? this.m.get(k) : null; }
  async put(k, v) { this.puts++; this.m.set(k, String(v)); }
  async delete(k) { this.m.delete(k); }
  async list({ prefix = "" } = {}) {
    return { keys: [...this.m.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })),
             list_complete: true };
  }
}

const SECRET = "pdl_ntfset_test_secret";
const PRICE = "pri_01awosprotest";

function env(extra = {}) {
  return {
    LICENSES: new FakeKV(), USAGE: new FakeKV(),
    PADDLE_WEBHOOK_SECRET: SECRET, PADDLE_PRICE_IDS: PRICE, PADDLE_API_KEY: "pdl_sdbx_apikey_x",
    PADDLE_ENV: "sandbox", ...extra,
  };
}

const now = () => Math.floor(Date.now() / 1000);

async function signed(body, { ts = now(), secret = SECRET } = {}) {
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  const h1 = await hmacSha256Hex(secret, `${ts}:${raw}`);
  return new Request("https://awos.test/api/paddle/webhook", {
    method: "POST", body: raw, headers: { "paddle-signature": `ts=${ts};h1=${h1}` },
  });
}

function subEvent(type, { id = "evt_1", sub = "sub_01abc", status = "active", price = PRICE,
  ends = "2026-11-15T00:00:00Z", occurred = new Date().toISOString() } = {}) {
  return {
    event_id: id, event_type: type, occurred_at: occurred,
    data: { id: sub, status, customer_id: "ctm_01x", items: [{ price: { id: price } }],
            current_billing_period: ends ? { starts_at: "2026-10-15T00:00:00Z", ends_at: ends } : null },
  };
}

async function body(res) { return JSON.parse(await res.text()); }

// ── signature ────────────────────────────────────────────────────────────────

test("signature: matches the tirramind scheme (HMAC-SHA256 over ts:rawBody, hex)", async () => {
  // Vector from tirramind's Python signer (tests/test_payments.py::_sign_body):
  // hmac.new(secret.encode(), f"{ts}:{body}".encode(), sha256).hexdigest()
  assert.equal(await hmacSha256Hex("pdl_ntfset_test_01secretkeybytes", '1700000000:{"event_type":"x"}'),
    "c1cc8dcfd8ff9f6141b43c7cabbc0772bd65a1f54bba68e3a3deaf0a478639ff");
  assert.equal((await verifySignature({ rawBody: '{"event_type":"x"}', secret: "pdl_ntfset_test_01secretkeybytes",
    header: "ts=1700000000;h1=c1cc8dcfd8ff9f6141b43c7cabbc0772bd65a1f54bba68e3a3deaf0a478639ff",
    nowS: 1700000100 })).ok, true);
  const raw = '{"a":1}';
  const ts = now();
  const h1 = await hmacSha256Hex(SECRET, `${ts}:${raw}`);
  assert.deepEqual(await verifySignature({ rawBody: raw, header: `ts=${ts};h1=${h1}`, secret: SECRET }), { ok: true });
});

test("signature: rejects wrong secret, tampered body, stale ts, missing parts, no secret", async () => {
  const raw = '{"a":1}';
  const ts = now();
  const good = await hmacSha256Hex(SECRET, `${ts}:${raw}`);
  const bad = await hmacSha256Hex("other", `${ts}:${raw}`);
  assert.equal((await verifySignature({ rawBody: raw, header: `ts=${ts};h1=${bad}`, secret: SECRET })).ok, false);
  assert.equal((await verifySignature({ rawBody: raw + " ", header: `ts=${ts};h1=${good}`, secret: SECRET })).ok, false);
  const old = ts - 301;
  const oldSig = await hmacSha256Hex(SECRET, `${old}:${raw}`);
  const stale = await verifySignature({ rawBody: raw, header: `ts=${old};h1=${oldSig}`, secret: SECRET });
  assert.equal(stale.reason, "stale timestamp");
  assert.equal((await verifySignature({ rawBody: raw, header: `h1=${good}`, secret: SECRET })).ok, false);
  assert.equal((await verifySignature({ rawBody: raw, header: "", secret: SECRET })).ok, false);
  assert.equal((await verifySignature({ rawBody: raw, header: `ts=${ts};h1=${good}`, secret: "" })).ok, false);
  // rotation: two h1 values, one valid
  assert.equal((await verifySignature({ rawBody: raw, header: `ts=${ts};h1=${bad};h1=${good}`, secret: SECRET })).ok, true);
});

test("webhook endpoint: 401 on bad or stale signature, nothing stored", async () => {
  const e = env();
  const evt = subEvent("subscription.activated");
  const forged = await signed(evt, { secret: "nope" });
  assert.equal((await webhook({ request: forged, env: e })).status, 401);
  const stale = await signed(evt, { ts: now() - 3600 });
  assert.equal((await webhook({ request: stale, env: e })).status, 401);
  assert.equal(e.LICENSES.m.size, 0);
});

// ── lifecycle + idempotency ──────────────────────────────────────────────────

test("activation creates one awos_ base32 key; replays and duplicates are no-ops", async () => {
  const e = env();
  const evt = subEvent("subscription.activated");
  const r1 = await webhook({ request: await signed(evt), env: e });
  assert.equal(r1.status, 200);
  const b1 = await body(r1);
  assert.equal(b1.handled, true);
  assert.ok(!JSON.stringify(b1).includes("awos_"), "webhook response must not echo the key");
  const key = await e.LICENSES.get("sub:sub_01abc");
  assert.match(key, LICENCE_KEY_RE);
  const rec = JSON.parse(await e.LICENSES.get("lic:" + key));
  assert.equal(rec.status, "active");
  assert.equal(rec.subscription_id, "sub_01abc");
  assert.equal(rec.current_period_end, "2026-11-15T00:00:00Z");

  // exact replay (same event id)
  const r2 = await body(await webhook({ request: await signed(evt), env: e }));
  assert.equal(r2.reason, "duplicate event");
  // a different event for the same subscription keeps the same key
  await webhook({ request: await signed(subEvent("subscription.updated", { id: "evt_2" })), env: e });
  await webhook({ request: await signed({ event_id: "evt_3", event_type: "transaction.completed",
    occurred_at: new Date().toISOString(),
    data: { id: "txn_01aaaaaaaaaa", subscription_id: "sub_01abc", status: "completed",
            items: [{ price: { id: PRICE } }] } }), env: e });
  assert.equal(await e.LICENSES.get("sub:sub_01abc"), key);
  assert.equal([...e.LICENSES.m.keys()].filter((k) => k.startsWith("lic:")).length, 1);
});

test("transaction.completed before subscription.activated still mints exactly one key", async () => {
  const e = env();
  await webhook({ request: await signed({ event_id: "evt_t", event_type: "transaction.completed",
    occurred_at: new Date().toISOString(),
    data: { id: "txn_01bbbbbbbbbb", subscription_id: "sub_02", status: "completed",
            items: [{ price: { id: PRICE } }], billing_period: { ends_at: "2026-11-20T00:00:00Z" } } }), env: e });
  const key = await e.LICENSES.get("sub:sub_02");
  assert.match(key, LICENCE_KEY_RE);
  await webhook({ request: await signed(subEvent("subscription.activated", { id: "evt_a", sub: "sub_02" })), env: e });
  assert.equal(await e.LICENSES.get("sub:sub_02"), key);
});

test("events for other price IDs (e.g. old tirramind prices) are ignored", async () => {
  const e = env();
  const res = await body(await webhook({ request: await signed(subEvent("subscription.activated",
    { price: "pri_01tirramindold" })), env: e }));
  assert.equal(res.handled, false);
  assert.equal(e.LICENSES.m.size, 0);
  const none = env({ PADDLE_PRICE_IDS: "" });
  await webhook({ request: await signed(subEvent("subscription.activated")), env: none });
  assert.equal(none.LICENSES.m.size, 0);
});

test("past_due gets a grace window, canceled keeps access to period end, older events cannot undo newer", async () => {
  const e = env();
  const t0 = Date.now();
  const at = (s) => new Date(t0 + s * 1000).toISOString();
  await webhook({ request: await signed(subEvent("subscription.activated", { id: "e1", occurred: at(0) })), env: e });
  const key = await e.LICENSES.get("sub:sub_01abc");
  await webhook({ request: await signed(subEvent("subscription.past_due",
    { id: "e2", status: "past_due", occurred: at(10) })), env: e });
  let rec = JSON.parse(await e.LICENSES.get("lic:" + key));
  assert.equal(rec.status, "past_due");
  const nowS = Math.floor(t0 / 1000) + 20;
  let a = accessFor(rec, nowS);
  assert.equal(a.active, true);
  assert.ok(a.grace_until);
  assert.equal(accessFor(rec, nowS + 4 * 86400).active, false);

  // a late, older "activated" must not flip it back
  const late = await body(await webhook({ request: await signed(subEvent("subscription.updated",
    { id: "e0", occurred: at(5) })), env: e }));
  assert.equal(late.reason, "older than stored state");

  const end = new Date(t0 + 10 * 86400 * 1000).toISOString();
  await webhook({ request: await signed(subEvent("subscription.updated",
    { id: "e3", status: "active", ends: end, occurred: at(30) })), env: e });
  await webhook({ request: await signed(subEvent("subscription.canceled",
    { id: "e4", status: "canceled", ends: null, occurred: at(40) })), env: e });
  rec = JSON.parse(await e.LICENSES.get("lic:" + key));
  assert.equal(rec.status, "canceled");
  assert.equal(rec.current_period_end, end);
  a = accessFor(rec, nowS + 86400);
  assert.equal(a.active, true);
  assert.equal(a.grace_until, end);
  assert.equal(accessFor(rec, nowS + 11 * 86400).active, false);
});

test("an immediate cancel (canceled_at before the period end) ends access at canceled_at", async () => {
  const e = env();
  const t0 = Date.now();
  const end = new Date(t0 + 20 * 86400 * 1000).toISOString();
  await webhook({ request: await signed(subEvent("subscription.activated", { id: "c1", ends: end,
    occurred: new Date(t0).toISOString() })), env: e });
  const evt = subEvent("subscription.canceled", { id: "c2", status: "canceled", ends: null,
    occurred: new Date(t0 + 1000).toISOString() });
  evt.data.canceled_at = new Date(t0 + 1000).toISOString();
  await webhook({ request: await signed(evt), env: e });
  const key = await e.LICENSES.get("sub:sub_01abc");
  const rec = JSON.parse(await e.LICENSES.get("lic:" + key));
  assert.equal(rec.current_period_end, evt.data.canceled_at);
  assert.equal(accessFor(rec, Math.floor(t0 / 1000) + 60).active, false);
});

// ── claim ────────────────────────────────────────────────────────────────────

function fakePaddle(txns, calls = []) {
  return async (url, init) => {
    calls.push({ url, auth: init.headers.Authorization });
    const id = url.split("/").pop();
    if (!txns[id]) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify({ data: txns[id] }), { status: 200 });
  };
}

const claimReq = (txn) => new Request(`https://awos.test/api/license/claim?txn=${txn}`);

test("claim: confirms with Paddle server-side, pending until webhook lands, then returns the key", async () => {
  const e = env();
  const calls = [];
  const txns = { txn_01cccccccccc: { status: "completed", subscription_id: "sub_01abc", items: [{ price: { id: PRICE } }] } };
  const fetchImpl = fakePaddle(txns, calls);
  let r = await claim({ request: claimReq("txn_01cccccccccc"), env: e, fetchImpl });
  assert.equal(r.status, 202);
  assert.equal((await body(r)).status, "pending");
  assert.equal(calls[0].url, "https://sandbox-api.paddle.com/transactions/txn_01cccccccccc");
  assert.equal(calls[0].auth, "Bearer pdl_sdbx_apikey_x");

  await webhook({ request: await signed(subEvent("subscription.activated")), env: e });
  r = await claim({ request: claimReq("txn_01cccccccccc"), env: e, fetchImpl });
  const b = await body(r);
  assert.equal(r.status, 200);
  assert.equal(b.status, "claimed");
  assert.match(b.key, LICENCE_KEY_RE);
  assert.equal(b.command, `awos activate ${b.key}`);
});

test("claim: live env uses api.paddle.com; unknown, incomplete, foreign-price and malformed txns refused", async () => {
  const e = env({ PADDLE_ENV: "live" });
  const calls = [];
  const txns = {
    txn_01dddddddddd: { status: "billed", subscription_id: "sub_x", items: [{ price: { id: PRICE } }] },
    txn_01eeeeeeeeee: { status: "completed", subscription_id: "sub_y", items: [{ price: { id: "pri_other" } }] },
  };
  const fetchImpl = fakePaddle(txns, calls);
  assert.equal((await claim({ request: claimReq("txn_01dddddddddd"), env: e, fetchImpl })).status, 202);
  assert.ok(calls[0].url.startsWith("https://api.paddle.com/"));
  assert.equal((await claim({ request: claimReq("txn_01eeeeeeeeee"), env: e, fetchImpl })).status, 404);
  assert.equal((await claim({ request: claimReq("txn_01ffffffffff"), env: e, fetchImpl })).status, 404);
  assert.equal((await claim({ request: claimReq("../etc"), env: e, fetchImpl })).status, 404);
  const broken = async () => { throw new Error("network"); };
  assert.equal((await claim({ request: claimReq("txn_01dddddddddd"), env: e, fetchImpl: broken })).status, 502);
});

test("claim: rate-limited per txn and a key is shown at most 5 times", async () => {
  const e = env();
  await webhook({ request: await signed(subEvent("subscription.activated")), env: e });
  const fetchImpl = fakePaddle({ txn_01gggggggggg: { status: "completed", subscription_id: "sub_01abc",
    items: [{ price: { id: PRICE } }] } });
  const statuses = [];
  for (let i = 0; i < 6; i++) statuses.push((await claim({ request: claimReq("txn_01gggggggggg"), env: e, fetchImpl })).status);
  assert.deepEqual(statuses, [200, 200, 200, 200, 200, 410]);
  for (let i = 0; i < 40; i++) await claim({ request: claimReq("txn_01gggggggggg"), env: e, fetchImpl });
  assert.equal((await claim({ request: claimReq("txn_01gggggggggg"), env: e, fetchImpl })).status, 429);
});

// ── check ────────────────────────────────────────────────────────────────────

const checkReq = (obj) => new Request("https://awos.test/api/license/check",
  { method: "POST", body: typeof obj === "string" ? obj : JSON.stringify(obj) });

test("check: returns only {active, plan, current_period_end, grace_until}", async () => {
  const e = env();
  await webhook({ request: await signed(subEvent("subscription.activated")), env: e });
  const key = await e.LICENSES.get("sub:sub_01abc");
  const b = await body(await check({ request: checkReq({ key, install_id: "x" }), env: e }));
  assert.deepEqual(Object.keys(b).sort(), ["active", "current_period_end", "grace_until", "plan"]);
  assert.equal(b.active, true);
  assert.equal(b.plan, "pro");
  for (const k of ["unknown", "awos_" + "A".repeat(32), "lic:" + key]) {
    const u = await body(await check({ request: checkReq({ key: k }), env: e }));
    assert.equal(u.active, false);
    assert.ok(!JSON.stringify(u).includes("sub_"));
  }
  assert.equal((await check({ request: checkReq("nope"), env: e })).status, 400);
});

// ── config ───────────────────────────────────────────────────────────────────

test("config: disabled by default and until every fulfilment piece exists", async () => {
  assert.deepEqual(await body(await config({ env: {} })), { enabled: false });
  const partial = env({ PADDLE_CLIENT_TOKEN: "test_abc" });  // no PADDLE_CHECKOUT_ENABLED
  assert.deepEqual(await body(await config({ env: partial })), { enabled: false });
  const noHook = env({ PADDLE_CHECKOUT_ENABLED: "1", PADDLE_CLIENT_TOKEN: "test_abc", PADDLE_WEBHOOK_SECRET: "" });
  assert.deepEqual(await body(await config({ env: noHook })), { enabled: false });
  const wrongToken = env({ PADDLE_CHECKOUT_ENABLED: "1", PADDLE_CLIENT_TOKEN: "live_abc" });  // sandbox env
  assert.deepEqual(await body(await config({ env: wrongToken })), { enabled: false });
  const ready = env({ PADDLE_CHECKOUT_ENABLED: "1", PADDLE_CLIENT_TOKEN: "test_abc" });
  assert.deepEqual(fulfilmentMissing(ready), []);
  assert.deepEqual(await body(await config({ env: ready })),
    { enabled: true, env: "sandbox", client_token: "test_abc", price_id: PRICE });
});

// ── ping + usage ─────────────────────────────────────────────────────────────

const IID = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b";
const pingReq = (obj) => new Request("https://awos.test/api/ping",
  { method: "POST", body: JSON.stringify(obj), headers: { "cf-connecting-ip": "203.0.113.9" } });

test("ping: unknown fields dropped, bad values dropped, bad events rejected", () => {
  const c = cleanPing({ v: 1, event: "job_finished", install_id: IID, awos_version: "0.2.2", os: "darwin",
    outcome: "solved", duration: "1to5m", cost: "lt1c", provider: "anthropic",
    goal: "fix the bug", repo: "/Users/me/secret", cost_usd: 0.004 });
  assert.deepEqual(c, { event: "job_finished", os: "darwin", provider: "anthropic", outcome: "solved",
    duration: "1to5m", cost: "lt1c", version: "0.2.2" });
  assert.deepEqual(cleanPing({ v: 1, event: "job_finished", install_id: IID, outcome: "rm -rf",
    awos_version: "x".repeat(100) }), { event: "job_finished" });
  assert.equal(cleanPing({ v: 1, event: "hack", install_id: IID }), null);
  assert.equal(cleanPing({ v: 1, event: "toString", install_id: IID }), null);
  assert.equal(cleanPing({ v: 1, event: "install", install_id: "not-a-uuid" }), null);
  assert.equal(cleanPing({ v: 2, event: "install", install_id: IID }), null);
  assert.equal(cleanPing([1]), null);
});

test("ping endpoint: counts per day/event, stores no IP or install id; usage needs the admin token", async () => {
  const e = env({ ADMIN_TOKEN: "owner-token" });
  for (let i = 0; i < 3; i++) {
    const r = await ping({ request: pingReq({ v: 1, event: "job_finished", install_id: IID, os: "linux",
      outcome: i ? "solved" : "failed", provider: "local" }), env: e });
    assert.equal(r.status, 202);
  }
  assert.equal((await ping({ request: pingReq({ v: 1, event: "nope", install_id: IID }), env: e })).status, 400);
  const big = new Request("https://awos.test/api/ping", { method: "POST", body: "x".repeat(5000) });
  assert.equal((await ping({ request: big, env: e })).status, 400);
  const dump = JSON.stringify([...e.USAGE.m.entries()]);
  assert.ok(!dump.includes("203.0.113.9"));
  assert.ok(!dump.includes(IID));
  const day = new Date().toISOString().slice(0, 10);
  const doc = JSON.parse(await e.USAGE.get(`day:${day}:job_finished`));
  assert.equal(doc.total, 3);
  assert.equal(doc["outcome=solved"], 2);
  assert.equal(doc["provider=local"], 3);

  const denied = await usage({ request: new Request("https://awos.test/api/usage?token=wrong"), env: e });
  assert.equal(denied.status, 404);
  const unset = await usage({ request: new Request("https://awos.test/api/usage?token="), env: env() });
  assert.equal(unset.status, 404);
  const ok = await body(await usage({ request: new Request("https://awos.test/api/usage?token=owner-token"), env: e }));
  assert.equal(ok.days[day].job_finished.total, 3);
});

// ── optional bindings unbound (USAGE / LICENSES not in wrangler.toml) ────────

test("unbound USAGE: ping is accepted and dropped (204), usage is 404", async () => {
  const bare = { ADMIN_TOKEN: "owner-token" };
  const r = await ping({ request: pingReq({ v: 1, event: "install", install_id: IID, os: "linux" }), env: bare });
  assert.equal(r.status, 204);
  assert.equal(await r.text(), "");
  const garbage = new Request("https://awos.test/api/ping", { method: "POST", body: "not json" });
  assert.equal((await ping({ request: garbage, env: bare })).status, 204);
  const u = await usage({ request: new Request("https://awos.test/api/usage?token=owner-token"), env: bare });
  assert.equal(u.status, 404);
});

test("unbound LICENSES or secrets: licence endpoints say 503 licensing not configured; config disabled", async () => {
  const bare = {};
  const c = await check({ request: new Request("https://awos.test/api/license/check",
    { method: "POST", body: JSON.stringify({ key: "awos_" + "A".repeat(32) }) }), env: bare });
  assert.equal(c.status, 503);
  assert.equal((await body(c)).error, "licensing not configured");
  const cl = await claim({ request: new Request("https://awos.test/api/license/claim?txn=txn_01abcdefghij"), env: bare });
  assert.equal(cl.status, 503);
  assert.deepEqual(await body(cl), { status: "not_configured", error: "licensing not configured" });
  const noKey = await claim({ request: new Request("https://awos.test/api/license/claim?txn=txn_01abcdefghij"),
    env: { LICENSES: new FakeKV() } });
  assert.equal(noKey.status, 503);
  const wh = await webhook({ request: await signed(subEvent("subscription.activated")), env: bare });
  assert.equal(wh.status, 503);
  assert.equal((await body(wh)).error, "licensing not configured");
  const whNoSecret = await webhook({ request: await signed(subEvent("subscription.activated")),
    env: { LICENSES: new FakeKV() } });
  assert.equal(whNoSecret.status, 503);
  assert.deepEqual(await body(await config({ env: bare })), { enabled: false });
  assert.deepEqual(await body(await config({ env: { PADDLE_CHECKOUT_ENABLED: "1", PADDLE_CLIENT_TOKEN: "test_x",
    PADDLE_API_KEY: "k", PADDLE_WEBHOOK_SECRET: "s", PADDLE_PRICE_IDS: PRICE } })), { enabled: false });
});

// ── request-access + local-first waitlist ───────────────────────────────────

const accessReq = (obj, { ip = "198.51.100.7", country = "IN" } = {}) =>
  new Request("https://awos.test/api/request-access", {
    method: "POST", body: typeof obj === "string" ? obj : JSON.stringify(obj),
    headers: { "cf-connecting-ip": ip, "cf-ipcountry": country, "content-type": "application/json" },
  });
const adminGet = (q) => new Request("https://awos.test/api/request-access?" + q);

test("waitlist: segment tag comes only from role + why", () => {
  assert.equal(segmentFor("team_dev", "privacy"), "privacy_team");
  assert.equal(segmentFor("founder", "privacy"), "privacy_team");
  assert.equal(segmentFor("solo_dev", "privacy"), "privacy_individual");
  assert.equal(segmentFor("solo_dev", "cost"), "cost_sensitive");
  assert.equal(segmentFor("researcher", "offline"), "local_builder");
  assert.equal(segmentFor("other", "curiosity"), "local_builder");
  assert.equal(segmentFor("other", "other"), "unclassified");
});

test("waitlist: strict validation and size caps", async () => {
  assert.ok(cleanWaitlist({ role: "solo_dev" }).error);
  assert.ok(cleanWaitlist({ role: "ceo", why: "cost" }).error);
  assert.ok(cleanWaitlist({ role: "solo_dev", why: "money" }).error);
  assert.ok(cleanWaitlist({ role: ["solo_dev"], why: "cost" }).error);
  assert.ok(cleanWaitlist({ role: "solo_dev", why: "cost", note: "x".repeat(501) }).error);
  assert.ok(cleanWaitlist({ role: "solo_dev", why: "cost", email: "nope" }).error);
  assert.ok(cleanWaitlist({ role: "solo_dev", why: "cost", email: "a@" + "b".repeat(260) + ".com" }).error);
  const ok = cleanWaitlist({ role: "solo_dev", why: "cost", note: " hi\u0007 ", email: "A@B.io", extra: "dropped" });
  assert.deepEqual(ok.rec, { form: "waitlist", segment: "cost_sensitive", role: "solo_dev", why: "cost",
    note: "hi", email: "a@b.io", src: "" });
  assert.equal(cleanWaitlist({ role: "solo_dev", why: "cost", src: "hn" }).rec.src, "hn");
  assert.equal(cleanWaitlist({ role: "solo_dev", why: "cost", src: "evil.com" }).rec.src, "");
  assert.ok(cleanWaitlist({ role: "solo_dev", why: "cost", src: 7 }).error);

  const e = { ACCESS_REQUESTS: new FakeKV() };
  const bad = await accessPost({ request: accessReq({ form: "waitlist", role: "solo_dev" }), env: e });
  assert.equal(bad.status, 400);
  const huge = await accessPost({ request: accessReq({ form: "waitlist", role: "solo_dev", why: "cost",
    note: "x".repeat(5000) }), env: e });
  assert.equal(huge.status, 413);
  const tooBig = await accessPost({ request: accessReq("x".repeat(20000)), env: e });
  assert.equal(tooBig.status, 413);
  assert.equal((await accessPost({ request: accessReq("[1,2]"), env: e })).status, 400);
  assert.equal([...e.ACCESS_REQUESTS.m.keys()].filter((k) => k.startsWith("wl:")).length, 0);
  // honeypot: looks fine to the bot, nothing stored
  const bot = await accessPost({ request: accessReq({ form: "waitlist", role: "solo_dev", why: "cost",
    website: "http://spam" }), env: e });
  assert.equal(bot.status, 200);
  assert.equal([...e.ACCESS_REQUESTS.m.keys()].filter((k) => k.startsWith("wl:")).length, 0);
  // KV unbound
  assert.equal((await accessPost({ request: accessReq({ form: "waitlist", role: "solo_dev", why: "cost" }),
    env: {} })).status, 503);
});

test("waitlist: stores a segment-tagged record; email optional; rate-limited per IP", async () => {
  const e = { ACCESS_REQUESTS: new FakeKV(), ADMIN_TOKEN: "owner-token" };
  const r1 = await accessPost({ request: accessReq({ form: "waitlist", role: "team_dev", why: "privacy",
    email: "Dev@Corp.example", note: "code cannot leave the VPN" }), env: e });
  assert.equal(r1.status, 200);
  assert.deepEqual(await body(r1), { ok: true, segment: "privacy_team" });
  const stored = JSON.parse(await e.ACCESS_REQUESTS.get("wl:e:dev@corp.example"));
  assert.equal(stored.segment, "privacy_team");
  assert.equal(stored.country, "IN");
  assert.ok(!JSON.stringify(stored).includes("198.51.100.7"));
  const r2 = await accessPost({ request: accessReq({ form: "waitlist", role: "solo_dev", why: "cost" },
    { ip: "198.51.100.8" }), env: e });
  assert.equal(r2.status, 200);
  for (let i = 0; i < 4; i++) {
    await accessPost({ request: accessReq({ form: "waitlist", role: "researcher", why: "offline" },
      { ip: "198.51.100.9" }), env: e });
  }
  const limited = await accessPost({ request: accessReq({ form: "waitlist", role: "researcher", why: "offline" },
    { ip: "198.51.100.9" }), env: e });
  assert.equal(limited.status, 200);  // 5th from this IP still allowed
  const sixth = await accessPost({ request: accessReq({ form: "waitlist", role: "researcher", why: "offline" },
    { ip: "198.51.100.9" }), env: e });
  assert.equal(sixth.status, 429);
});

test("waitlist admin: token-protected counts by segment and CSV export; legacy form unchanged", async () => {
  const e = { ACCESS_REQUESTS: new FakeKV(), ADMIN_TOKEN: "owner-token" };
  const sign = (o, ip) => accessPost({ request: accessReq({ form: "waitlist", ...o }, { ip }), env: e });
  await sign({ role: "team_dev", why: "privacy", email: "a@x.io", src: "devindia" }, "1");
  await sign({ role: "solo_dev", why: "cost", note: "=HYPERLINK(\"evil\")" }, "2");
  await sign({ role: "solo_dev", why: "cost", note: "rent, food, \"tokens\"" }, "3");
  await sign({ role: "researcher", why: "curiosity" }, "4");
  const legacy = await accessPost({ request: accessReq({ name: "Ann", email: "ann@x.io", role: "Founder" },
    { ip: "5" }), env: e });
  assert.equal(legacy.status, 200);

  for (const q of ["view=waitlist", "view=waitlist&token=wrong", "view=waitlist&format=csv&token="]) {
    assert.equal((await accessGet({ request: adminGet(q), env: e })).status, 404);
  }
  assert.equal((await accessGet({ request: adminGet("view=waitlist&token=owner-token"),
    env: { ACCESS_REQUESTS: e.ACCESS_REQUESTS } })).status, 404);  // no ADMIN_TOKEN set

  const v = await body(await accessGet({ request: adminGet("view=waitlist&token=owner-token"), env: e }));
  assert.equal(v.count, 4);
  assert.deepEqual(v.by_segment, { privacy_team: 1, privacy_individual: 0, cost_sensitive: 2,
    local_builder: 1, unclassified: 0 });
  assert.equal(v.by_role.solo_dev, 2);
  assert.equal(v.by_why.cost, 2);
  assert.equal(v.by_country.IN, 4);
  assert.equal(v.by_src.devindia, 1);

  const csvRes = await accessGet({ request: adminGet("view=waitlist&format=csv&token=owner-token"), env: e });
  assert.match(csvRes.headers.get("content-type"), /text\/csv/);
  const csv = await csvRes.text();
  assert.equal(csv.split("\n")[0], "ts,segment,role,why,src,country,email,note");
  assert.ok(csv.includes(`"'=HYPERLINK(""evil"")"`));
  assert.ok(csv.includes(`"rent, food, ""tokens"""`));
  assert.equal(waitlistCsv([]), "ts,segment,role,why,src,country,email,note\n");

  const all = await body(await accessGet({ request: adminGet("token=owner-token"), env: e }));
  assert.equal(all.count, 1);
  assert.equal(all.requests[0].email, "ann@x.io");
  assert.equal(all.waitlist_count, 4);
});

// ── helpers ──────────────────────────────────────────────────────────────────

test("licence keys: awos_ + 32 base32 chars, unique; base32 matches RFC 4648", () => {
  assert.equal(base32(new TextEncoder().encode("foobar")), "MZXW6YTBOI");
  const a = newLicenceKey(), b = newLicenceKey();
  assert.match(a, LICENCE_KEY_RE);
  assert.notEqual(a, b);
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "abcd"), false);
});
