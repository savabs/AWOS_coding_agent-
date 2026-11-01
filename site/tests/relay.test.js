// node --test site/tests/   (Node >= 20; no dependencies, no network)
// The phone relay (/api/relay/...) against an in-memory KV fake with TTLs.
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

import { onRequest, sha256Hex, _resetRateMemory, TTL_S, MAX_ITEMS, MAX_ENVELOPE, MAX_SMS_TEXT, RATE_PER_MIN }
  from "../functions/api/relay/[[path]].js";

// KV fake that honours expirationTtl against a controllable clock.
class TtlKV {
  constructor() { this.m = new Map(); this.puts = []; }
  async get(k) {
    const e = this.m.get(k);
    if (!e) return null;
    if (e.exp && Date.now() >= e.exp) { this.m.delete(k); return null; }
    return e.v;
  }
  async put(k, v, opts = {}) {
    if (opts.expirationTtl !== undefined && opts.expirationTtl < 60) throw new Error("KV TTL must be >= 60");
    this.puts.push({ k, v: String(v), ttl: opts.expirationTtl });
    this.m.set(k, { v: String(v), exp: opts.expirationTtl ? Date.now() + opts.expirationTtl * 1000 : 0 });
  }
  async delete(k) { this.m.delete(k); }
}

// Controllable clock: Date.now() returns `clock` while a test runs.
let clock = 1_800_000_000_000;
const realNow = Date.now;
beforeEach(() => { _resetRateMemory(); Date.now = () => clock; });
afterEach(() => { Date.now = realNow; });

function b64u(bytes) {
  return Buffer.from(bytes).toString("base64url");
}
const CHANNEL = b64u(new Uint8Array(16).fill(7));    // 22 chars, like the extension's
const OTHER = b64u(new Uint8Array(16).fill(9));
const TOKEN = b64u(new Uint8Array(16).fill(3));

function call(method, path, { body, headers = {}, env, query = "" } = {}) {
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
    if (typeof body !== "string" && !init.headers["content-type"]) init.headers["content-type"] = "application/json";
  }
  const request = new Request(`https://awos.test/api/relay/${path}${query}`, init);
  return onRequest({ request, env, params: { path: path.split("/").filter(Boolean) } });
}

const env = () => ({ RELAY: new TtlKV() });
async function body(res) { return JSON.parse(await res.text()); }

let n = 0;
function envelope(extra = {}) {
  n++;
  return { id: `m${n}`, iv: b64u(new Uint8Array(12).fill(n % 256)), ct: b64u(new Uint8Array(40).fill(1)), ...extra };
}

// ── binding / validation ─────────────────────────────────────────────────────

test("relay: 503 'relay not configured' without the RELAY binding, for every route", async () => {
  for (const [m, p] of [["GET", `${CHANNEL}/to-phone`], ["POST", `${CHANNEL}/to-mac`], ["POST", `${CHANNEL}/sms`],
    ["POST", `${CHANNEL}/register`], ["DELETE", `${CHANNEL}/to-phone/x`]]) {
    const res = await call(m, p, { env: {}, body: m === "POST" ? envelope() : undefined });
    assert.equal(res.status, 503);
    assert.deepEqual(await body(res), { error: "relay not configured" });
  }
});

test("relay: rejects short or non-base64url channels and unknown routes", async () => {
  const e = env();
  assert.equal((await call("GET", "short/to-phone", { env: e })).status, 400);
  assert.equal((await call("GET", `${"a".repeat(21)}/to-phone`, { env: e })).status, 400);
  assert.equal((await call("GET", `${CHANNEL.slice(0, 21)}+/to-phone`, { env: e })).status, 400);
  assert.equal((await call("GET", `${CHANNEL}/elsewhere`, { env: e })).status, 404);
  assert.equal((await call("GET", `${CHANNEL}`, { env: e })).status, 404);
  assert.equal((await call("GET", `${CHANNEL}/to-phone/abc`, { env: e })).status, 404); // only DELETE takes /<id>
  assert.equal((await call("DELETE", `${CHANNEL}/to-phone`, { env: e })).status, 404);
  assert.equal((await call("PUT", `${CHANNEL}/to-phone`, { env: e, body: envelope() })).status, 405);
  assert.equal((await call("GET", `${CHANNEL}/register`, { env: e })).status, 405);
});

test("relay: envelope must be {id, iv(12 bytes), ct}; bodies over 8 KB are refused", async () => {
  const e = env();
  for (const bad of [{}, { id: "x", iv: "short", ct: "A".repeat(30) }, { id: "a b", iv: "A".repeat(16), ct: "A".repeat(30) },
    { id: "x", iv: "A".repeat(16) }, [], "not json"]) {
    assert.equal((await call("POST", `${CHANNEL}/to-phone`, { env: e, body: bad })).status, 400, JSON.stringify(bad));
  }
  const big = envelope({ ct: "A".repeat(MAX_ENVELOPE) });
  assert.equal((await call("POST", `${CHANNEL}/to-phone`, { env: e, body: big })).status, 413);
  assert.deepEqual([...e.RELAY.m.keys()].filter((k) => !k.includes(":rl:")), [], "nothing stored but the rate counter");
});

// ── round trip, since, delete ────────────────────────────────────────────────

test("relay: POST then GET ?since= returns newer envelopes with server 'at'; directions are separate", async () => {
  const e = env();
  const a = envelope(), b = envelope();
  const r1 = await call("POST", `${CHANNEL}/to-phone`, { env: e, body: a });
  assert.equal(r1.status, 201);
  const at1 = (await body(r1)).at;
  assert.equal(at1, clock);
  const r2 = await call("POST", `${CHANNEL}/to-phone`, { env: e, body: b }); // same ms: still strictly later
  const at2 = (await body(r2)).at;
  assert.ok(at2 > at1);

  const all = await body(await call("GET", `${CHANNEL}/to-phone`, { env: e }));
  assert.deepEqual(all.messages, [{ at: at1, ...a }, { at: at2, ...b }]);
  const newer = await body(await call("GET", `${CHANNEL}/to-phone`, { env: e, query: `?since=${at1}` }));
  assert.deepEqual(newer.messages.map((m) => m.id), [b.id]);
  assert.deepEqual((await body(await call("GET", `${CHANNEL}/to-mac`, { env: e }))).messages, []);
  assert.deepEqual((await body(await call("GET", `${OTHER}/to-phone`, { env: e }))).messages, []);
  assert.equal((await call("GET", `${CHANNEL}/to-phone`, { env: e })).headers.get("cache-control"), "no-store");
});

test("relay: re-posting the same id is idempotent", async () => {
  const e = env();
  const a = envelope();
  const at = (await body(await call("POST", `${CHANNEL}/to-mac`, { env: e, body: a }))).at;
  clock += 5;
  const again = await call("POST", `${CHANNEL}/to-mac`, { env: e, body: a });
  assert.equal(again.status, 200);
  assert.deepEqual(await body(again), { ok: true, at, duplicate: true });
  assert.equal((await body(await call("GET", `${CHANNEL}/to-mac`, { env: e }))).messages.length, 1);
});

test("relay: DELETE /<dir>/<id> removes one message", async () => {
  const e = env();
  const a = envelope(), b = envelope();
  await call("POST", `${CHANNEL}/to-mac`, { env: e, body: a });
  await call("POST", `${CHANNEL}/to-mac`, { env: e, body: b });
  const res = await call("DELETE", `${CHANNEL}/to-mac/${a.id}`, { env: e });
  assert.deepEqual(await body(res), { ok: true, deleted: 1 });
  assert.deepEqual((await body(await call("GET", `${CHANNEL}/to-mac`, { env: e }))).messages.map((m) => m.id), [b.id]);
  assert.deepEqual(await body(await call("DELETE", `${CHANNEL}/to-mac/nope`, { env: e })), { ok: true, deleted: 0 });
});

// ── TTL / limits ─────────────────────────────────────────────────────────────

test("relay: messages expire after 600 s, per item and per key", async () => {
  const e = env();
  const a = envelope();
  await call("POST", `${CHANNEL}/to-phone`, { env: e, body: a });
  const put = e.RELAY.puts.find((p) => p.k.endsWith(":to-phone"));
  assert.equal(put.ttl, TTL_S);
  clock += 400_000;
  const b = envelope();
  await call("POST", `${CHANNEL}/to-phone`, { env: e, body: b }); // refreshes the key's TTL
  clock += 250_000; // a is 650 s old, b is 250 s old
  assert.deepEqual((await body(await call("GET", `${CHANNEL}/to-phone`, { env: e }))).messages.map((m) => m.id), [b.id]);
  clock += 600_000;
  assert.deepEqual((await body(await call("GET", `${CHANNEL}/to-phone`, { env: e }))).messages, []);
});

test("relay: at most 50 per direction, the oldest is dropped", async () => {
  const e = env();
  const ids = [];
  for (let i = 0; i < MAX_ITEMS + 5; i++) {
    const m = envelope();
    ids.push(m.id);
    clock += 1000; // stay under the rate limit
    _resetRateMemory();
    assert.equal((await call("POST", `${CHANNEL}/to-phone`, { env: e, body: m })).status, 201);
  }
  const got = (await body(await call("GET", `${CHANNEL}/to-phone`, { env: e }))).messages.map((m) => m.id);
  assert.equal(got.length, MAX_ITEMS);
  assert.deepEqual(got, ids.slice(5));
});

test("relay: rate limit 120 requests/min per channel (reads in memory, writes also in KV)", async () => {
  const e = env();
  for (let i = 0; i < RATE_PER_MIN; i++) {
    assert.equal((await call("GET", `${CHANNEL}/to-phone`, { env: e })).status, 200);
  }
  const limited = await call("GET", `${CHANNEL}/to-phone`, { env: e });
  assert.equal(limited.status, 429);
  assert.ok(limited.headers.get("retry-after"));
  assert.equal((await call("GET", `${OTHER}/to-phone`, { env: e })).status, 200); // per channel
  assert.equal(e.RELAY.puts.length, 0, "polling must not write to KV");
  clock += 60_000;
  assert.equal((await call("GET", `${CHANNEL}/to-phone`, { env: e })).status, 200);

  // Writes are counted in KV too: a fresh isolate (memory reset) still sees them.
  const e2 = env();
  clock = Math.ceil(clock / 60000) * 60000 + 1;
  for (let i = 0; i < RATE_PER_MIN; i++) {
    if (i % 50 === 0) _resetRateMemory();
    assert.equal((await call("POST", `${CHANNEL}/to-mac`, { env: e2, body: envelope() })).status, 201);
  }
  _resetRateMemory();
  assert.equal((await call("POST", `${CHANNEL}/to-mac`, { env: e2, body: envelope() })).status, 429);
});

// ── hashed keys ──────────────────────────────────────────────────────────────

test("relay: KV keys use SHA-256(channel); the raw channel and smsToken never reach KV", async () => {
  const e = env();
  const h = await sha256Hex(CHANNEL);
  await call("POST", `${CHANNEL}/to-phone`, { env: e, body: envelope() });
  await call("POST", `${CHANNEL}/to-mac`, { env: e, body: envelope() });
  await call("POST", `${CHANNEL}/register`, { env: e, body: { tokenHash: await sha256Hex(TOKEN) } });
  await call("POST", `${CHANNEL}/sms`, { env: e, body: { text: "Your code is 123456" }, headers: { "x-awos-token": TOKEN } });
  assert.ok(e.RELAY.puts.length >= 4);
  for (const p of e.RELAY.puts) {
    assert.ok(p.k.startsWith(`relay:${h}:`), p.k);
    assert.ok(!p.k.includes(CHANNEL), p.k);
    assert.ok(!p.v.includes(CHANNEL), "value holds the channel");
    assert.ok(!p.k.includes(TOKEN) && !p.v.includes(TOKEN), "smsToken stored in clear");
  }
  assert.equal(await e.RELAY.get(`relay:${h}:token`), await sha256Hex(TOKEN));
});

// ── register + sms ───────────────────────────────────────────────────────────

test("register: needs a sha256 hex; first registration wins, the same hash is idempotent", async () => {
  const e = env();
  assert.equal((await call("POST", `${CHANNEL}/register`, { env: e, body: { tokenHash: "abc" } })).status, 400);
  assert.equal((await call("POST", `${CHANNEL}/register`, { env: e, body: "nope" })).status, 400);
  const th = await sha256Hex(TOKEN);
  assert.equal((await call("POST", `${CHANNEL}/register`, { env: e, body: { tokenHash: th } })).status, 201);
  assert.equal((await call("POST", `${CHANNEL}/register`, { env: e, body: { tokenHash: th.toUpperCase() } })).status, 200);
  const other = await call("POST", `${CHANNEL}/register`, { env: e, body: { tokenHash: await sha256Hex("x") } });
  assert.equal(other.status, 409);
  assert.equal(await e.RELAY.get(`relay:${await sha256Hex(CHANNEL)}:token`), th);
});

test("sms: refused without registration, without the header, or with a wrong token", async () => {
  const e = env();
  const send = (headers) => call("POST", `${CHANNEL}/sms`, { env: e, body: { text: "code 1234" }, headers });
  assert.equal((await send({ "x-awos-token": TOKEN })).status, 403); // not registered yet
  await call("POST", `${CHANNEL}/register`, { env: e, body: { tokenHash: await sha256Hex(TOKEN) } });
  assert.equal((await send({})).status, 403);
  assert.equal((await send({ "x-awos-token": "wrong-token-wrong-token" })).status, 403);
  assert.equal((await send({ "x-awos-token": TOKEN })).status, 201);
  // a token registered on one channel does not open another
  assert.equal((await call("POST", `${OTHER}/sms`, { env: e, body: { text: "x" }, headers: { "x-awos-token": TOKEN } })).status, 403);
});

test("sms: JSON, plain text and form bodies; cut to 500 chars; GET since; DELETE by id or at; 10 min TTL", async () => {
  const e = env();
  await call("POST", `${CHANNEL}/register`, { env: e, body: { tokenHash: await sha256Hex(TOKEN) } });
  const hdr = { "x-awos-token": TOKEN };
  const r1 = await body(await call("POST", `${CHANNEL}/sms`, { env: e, body: { text: "Your OTP is 482913" }, headers: hdr }));
  const r2 = await body(await call("POST", `${CHANNEL}/sms`, { env: e, body: "Verification code: 7Q2K", headers: { ...hdr, "content-type": "text/plain" } }));
  await call("POST", `${CHANNEL}/sms`, { env: e, body: "text=code+5555", headers: { ...hdr, "content-type": "application/x-www-form-urlencoded" } });
  await call("POST", `${CHANNEL}/sms`, { env: e, body: { text: "x".repeat(2000) }, headers: hdr });
  assert.equal((await call("POST", `${CHANNEL}/sms`, { env: e, body: { text: "   " }, headers: hdr })).status, 400);
  assert.equal((await call("POST", `${CHANNEL}/sms`, { env: e, body: { text: "y".repeat(5000) }, headers: hdr })).status, 413);

  const got = (await body(await call("GET", `${CHANNEL}/sms`, { env: e }))).messages;
  assert.deepEqual(got.map((m) => m.sms.slice(0, 24)), ["Your OTP is 482913", "Verification code: 7Q2K", "code 5555", "x".repeat(24)]);
  assert.equal(got[3].sms.length, MAX_SMS_TEXT);
  for (const m of got) assert.deepEqual(Object.keys(m).sort(), ["at", "id", "sms"]);
  assert.deepEqual((await body(await call("GET", `${CHANNEL}/sms`, { env: e, query: `?since=${r1.at}` }))).messages[0].id, r2.id);

  assert.deepEqual(await body(await call("DELETE", `${CHANNEL}/sms/${r1.id}`, { env: e })), { ok: true, deleted: 1 });
  assert.deepEqual(await body(await call("DELETE", `${CHANNEL}/sms/${r2.at}`, { env: e })), { ok: true, deleted: 1 });
  assert.equal((await body(await call("GET", `${CHANNEL}/sms`, { env: e }))).messages.length, 2);

  const put = e.RELAY.puts.filter((p) => p.k.endsWith(":sms")).pop();
  assert.equal(put.ttl, TTL_S);
  clock += TTL_S * 1000;
  assert.deepEqual((await body(await call("GET", `${CHANNEL}/sms`, { env: e }))).messages, []);
});
