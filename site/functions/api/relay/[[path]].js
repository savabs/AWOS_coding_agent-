// Cloudflare Pages Function: the AWOS phone relay, /api/relay/<channel>/...
// (protocol v1: docs/launch/phone_link.md). It carries end-to-end encrypted
// envelopes between the AWOS Replay extension on the Mac and the phone page
// (/phone/). The relay never sees the key: it only stores {id, iv, ct} blobs.
//
//   POST   /api/relay/<channel>/to-phone | to-mac        body = {id, iv, ct} (<= 8 KB)
//   GET    /api/relay/<channel>/to-phone | to-mac?since=<ms>
//   DELETE /api/relay/<channel>/to-phone | to-mac/<id>
//   POST   /api/relay/<channel>/register                 {"tokenHash": "<sha256 hex of smsToken>"}
//   POST   /api/relay/<channel>/sms                      X-AWOS-Token: <smsToken>; {"text"} or plain text
//   GET    /api/relay/<channel>/sms?since=<ms>
//   DELETE /api/relay/<channel>/sms/<id or at>
//
// The sms path is the only plaintext one (an iPhone Shortcut cannot encrypt):
// the text is cut to 500 characters, kept at most 10 minutes and deleted once read.
//
// Storage (KV binding RELAY; 503 without it). Keys use h = SHA-256(channel) in hex,
// never the raw channel:
//   relay:<h>:to-phone, relay:<h>:to-mac, relay:<h>:sms   JSON lists, TTL 600 s, max 50 (oldest dropped)
//   relay:<h>:token                                       SHA-256(smsToken) hex, TTL 400 days
//   relay:<h>:rl:<minute>                                 write counter for the rate limit, TTL 120 s
// Rate limit: 120 requests/min per channel. Reads are counted in this isolate's
// memory (best effort, no KV write per poll: the free plan allows 1000 KV
// writes/day); writes are also counted in KV, so they are limited across isolates.
import { json, readBodyCapped, safeEqual } from "../../../lib/common.js";

export const TTL_S = 600;
export const MAX_ITEMS = 50;
export const MAX_ENVELOPE = 8 * 1024;
export const MAX_SMS_BODY = 4 * 1024;
export const MAX_SMS_TEXT = 500;
export const RATE_PER_MIN = 120;
const TOKEN_TTL_S = 400 * 86400;

const CHANNEL_RE = /^[A-Za-z0-9_-]{22,128}$/;
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const IV_RE = /^[A-Za-z0-9_-]{16}$/; // 12 bytes, base64url without padding
const CT_RE = /^[A-Za-z0-9_-]{22,}$/; // at least the 16-byte GCM tag
const HEX64_RE = /^[0-9a-f]{64}$/;
const DIRS = new Set(["to-phone", "to-mac"]);

const enc = new TextEncoder();

export async function sha256Hex(text) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(String(text))));
  return Array.from(d, (b) => b.toString(16).padStart(2, "0")).join("");
}

function randomId() {
  const b = new Uint8Array(12);
  crypto.getRandomValues(b);
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function readList(kv, key, now) {
  let list = [];
  try { list = JSON.parse((await kv.get(key)) || "[]"); } catch { list = []; }
  if (!Array.isArray(list)) return [];
  // KV expiry is per key and every write refreshes it, so also expire per item.
  return list.filter((m) => m && typeof m.at === "number" && now - m.at < TTL_S * 1000);
}

async function writeList(kv, key, list) {
  if (!list.length) return kv.delete(key);
  return kv.put(key, JSON.stringify(list), { expirationTtl: TTL_S });
}

// A strictly increasing timestamp within the list, so ?since=<last at> never skips one.
function nextAt(list, now) {
  const last = list.length ? list[list.length - 1].at : 0;
  return Math.max(now, last + 1);
}

function sinceParam(url) {
  const n = Number(url.searchParams.get("since") || "0");
  return Number.isFinite(n) && n > 0 ? n : 0;
}

// ── rate limit ───────────────────────────────────────────────────────────────

const memHits = new Map(); // `${h}:${minute}` -> count, this isolate only

function memCount(h, minute) {
  const k = `${h}:${minute}`;
  const n = (memHits.get(k) || 0) + 1;
  memHits.set(k, n);
  if (memHits.size > 5000) {
    for (const key of memHits.keys()) if (!key.endsWith(`:${minute}`)) memHits.delete(key);
  }
  return n;
}

export function _resetRateMemory() { memHits.clear(); }

async function overLimit(kv, h, isWrite, now) {
  const minute = Math.floor(now / 60000);
  if (memCount(h, minute) > RATE_PER_MIN) return true;
  if (!isWrite) return false;
  const key = `relay:${h}:rl:${minute}`;
  const n = (parseInt((await kv.get(key)) || "0", 10) || 0) + 1;
  if (n > RATE_PER_MIN) return true;
  try { await kv.put(key, String(n), { expirationTtl: 120 }); } catch { /* best effort */ }
  return false;
}

// ── handlers ─────────────────────────────────────────────────────────────────

function validEnvelope(e) {
  return e && typeof e === "object" && !Array.isArray(e)
    && typeof e.id === "string" && ID_RE.test(e.id)
    && typeof e.iv === "string" && IV_RE.test(e.iv)
    && typeof e.ct === "string" && CT_RE.test(e.ct);
}

async function postEnvelope(kv, key, request, now) {
  const raw = await readBodyCapped(request, MAX_ENVELOPE);
  if (raw === null) return json({ error: "body too large" }, 413);
  let e = null;
  try { e = JSON.parse(raw); } catch { e = null; }
  if (!validEnvelope(e)) return json({ error: "body must be {id, iv, ct}" }, 400);
  const list = await readList(kv, key, now);
  const dup = list.find((m) => m.id === e.id);
  if (dup) return json({ ok: true, at: dup.at, duplicate: true });
  const at = nextAt(list, now);
  list.push({ at, id: e.id, iv: e.iv, ct: e.ct });
  while (list.length > MAX_ITEMS) list.shift();
  await writeList(kv, key, list);
  return json({ ok: true, at }, 201);
}

async function getList(kv, key, url, now, shape) {
  const since = sinceParam(url);
  const list = await readList(kv, key, now);
  return json({ messages: list.filter((m) => m.at > since).map(shape) });
}

async function deleteItem(kv, key, ref, now) {
  const list = await readList(kv, key, now);
  const asAt = /^[0-9]{1,16}$/.test(ref) ? Number(ref) : NaN;
  const keep = list.filter((m) => m.id !== ref && m.at !== asAt);
  if (keep.length !== list.length) await writeList(kv, key, keep);
  return json({ ok: true, deleted: list.length - keep.length });
}

async function register(kv, h, request) {
  const raw = await readBodyCapped(request, 1024);
  let b = null;
  try { b = raw === null ? null : JSON.parse(raw); } catch { b = null; }
  const tokenHash = b && typeof b.tokenHash === "string" ? b.tokenHash.toLowerCase() : "";
  if (!HEX64_RE.test(tokenHash)) return json({ error: "body must be {tokenHash: <sha256 hex>}" }, 400);
  const key = `relay:${h}:token`;
  const have = await kv.get(key);
  // First registration wins; the same hash again is fine (and refreshes the TTL).
  if (have && !safeEqual(have, tokenHash)) return json({ error: "channel already registered" }, 409);
  await kv.put(key, tokenHash, { expirationTtl: TOKEN_TTL_S });
  return json({ ok: true }, have ? 200 : 201);
}

async function smsText(request) {
  const raw = await readBodyCapped(request, MAX_SMS_BODY);
  if (raw === null) return { error: json({ error: "body too large" }, 413) };
  const type = (request.headers.get("content-type") || "").toLowerCase();
  let text = raw;
  const trimmed = raw.trim();
  if (type.includes("application/json") || trimmed.startsWith("{")) {
    try {
      const b = JSON.parse(trimmed);
      if (b && typeof b === "object" && typeof b.text === "string") text = b.text;
      else if (type.includes("application/json")) return { error: json({ error: 'body must be {"text": "..."}' }, 400) };
    } catch {
      if (type.includes("application/json")) return { error: json({ error: "bad JSON" }, 400) };
    }
  } else if (type.includes("application/x-www-form-urlencoded")) {
    text = new URLSearchParams(raw).get("text") || "";
  }
  text = String(text).replace(/\u0000/g, "").trim();
  if (!text) return { error: json({ error: "empty text" }, 400) };
  return { text: Array.from(text).slice(0, MAX_SMS_TEXT).join("") };
}

async function postSms(kv, h, request, now) {
  const stored = await kv.get(`relay:${h}:token`);
  const given = request.headers.get("x-awos-token") || "";
  if (!stored || !given || !safeEqual(await sha256Hex(given), stored)) {
    return json({ error: "bad or unregistered token" }, 403);
  }
  const { text, error } = await smsText(request);
  if (error) return error;
  const key = `relay:${h}:sms`;
  const list = await readList(kv, key, now);
  const at = nextAt(list, now);
  const id = randomId();
  list.push({ at, id, sms: text });
  while (list.length > MAX_ITEMS) list.shift();
  await writeList(kv, key, list);
  return json({ ok: true, at, id }, 201);
}

export async function onRequest({ request, env, params }) {
  const kv = env.RELAY;
  if (!kv) return json({ error: "relay not configured" }, 503);
  const parts = Array.isArray(params?.path) ? params.path : String(params?.path || "").split("/").filter(Boolean);
  const [channel, what, ref, ...rest] = parts;
  if (!channel || !CHANNEL_RE.test(channel)) return json({ error: "bad channel" }, 400);
  if (rest.length || !what) return json({ error: "not found" }, 404);
  const method = request.method.toUpperCase();
  const url = new URL(request.url);
  const now = Date.now();
  const h = await sha256Hex(channel);

  const isDir = DIRS.has(what);
  const isSms = what === "sms";
  if (!isDir && !isSms && what !== "register") return json({ error: "not found" }, 404);
  if (ref !== undefined && (!(isDir || isSms) || method !== "DELETE" || !ID_RE.test(ref))) {
    return json({ error: "not found" }, 404);
  }
  if (await overLimit(kv, h, method !== "GET", now)) {
    return json({ error: "rate limited" }, 429, { "retry-after": "30" });
  }

  if (what === "register") {
    if (method !== "POST") return json({ error: "method not allowed" }, 405, { allow: "POST" });
    return register(kv, h, request);
  }
  const key = `relay:${h}:${what}`;
  if (method === "DELETE") {
    if (ref === undefined) return json({ error: "DELETE needs /<id>" }, 404);
    return deleteItem(kv, key, ref, now);
  }
  if (method === "GET") {
    return getList(kv, key, url, now, isSms
      ? (m) => ({ at: m.at, id: m.id, sms: m.sms })
      : (m) => ({ at: m.at, id: m.id, iv: m.iv, ct: m.ct }));
  }
  if (method === "POST") return isSms ? postSms(kv, h, request, now) : postEnvelope(kv, key, request, now);
  return json({ error: "method not allowed" }, 405, { allow: "GET, POST, DELETE" });
}
