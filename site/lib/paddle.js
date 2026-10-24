// Paddle Billing (v2) helpers for the AWOS Pro licence: signature check,
// environment switch, and the subscription -> licence state machine.
// Ported from tirramind agent/payments/{webhook,handler,claim}.py.
//
// KV namespace LICENSES:
//   lic:<key>        -> {subscription_id, customer_id, status, plan, price_id,
//                        current_period_end, grace_until, updated, occurred_at}
//   sub:<sub_id>     -> <key>
//   evt:<event_id>   -> "1"        (processed webhook ids, 30-day TTL)
//   claim:<txn_id>   -> {first, count}
//   claimrl:<txn_id> -> attempts this hour

import { hmacSha256Hex, safeEqual, newLicenceKey, readJson } from "./common.js";

// Paddle's SDKs default to 5 s; tirramind settled on 300 s (queueing jitter
// must not drop a paying customer), with the event-id ledger as the real
// replay guard. Same here.
export const MAX_SIGNATURE_AGE_S = 300;
export const PAST_DUE_GRACE_DAYS = 3;
export const EVENT_TTL_S = 30 * 24 * 3600;

export const HANDLED_EVENTS = new Set([
  "subscription.created",
  "subscription.activated",
  "subscription.updated",
  "subscription.canceled",
  "subscription.past_due",
  "transaction.completed",
]);

const SUB_STATUSES = new Set(["active", "trialing", "past_due", "paused", "canceled"]);

export function paddleEnv(env) {
  return env.PADDLE_ENV === "live" ? "live" : "sandbox";
}

export function apiBase(env) {
  return paddleEnv(env) === "live" ? "https://api.paddle.com" : "https://sandbox-api.paddle.com";
}

export function priceIds(env) {
  return String(env.PADDLE_PRICE_IDS || "")
    .split(",").map((s) => s.trim()).filter((s) => /^pri_[a-z0-9]+$/i.test(s));
}

// What is still missing before checkout may be shown. Empty = ready.
export function fulfilmentMissing(env) {
  const missing = [];
  if (env.PADDLE_CHECKOUT_ENABLED !== "1") missing.push("PADDLE_CHECKOUT_ENABLED=1");
  if (!env.LICENSES) missing.push("KV binding LICENSES");
  if (!env.PADDLE_API_KEY) missing.push("PADDLE_API_KEY");
  if (!env.PADDLE_WEBHOOK_SECRET) missing.push("PADDLE_WEBHOOK_SECRET");
  if (!priceIds(env).length) missing.push("PADDLE_PRICE_IDS");
  const token = String(env.PADDLE_CLIENT_TOKEN || "");
  const want = paddleEnv(env) === "live" ? "live_" : "test_";
  if (!token.startsWith(want)) missing.push(`PADDLE_CLIENT_TOKEN (${want}...)`);
  return missing;
}

export function parseSignatureHeader(header) {
  const out = { ts: null, h1: [] };
  for (const part of String(header || "").split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k === "ts") out.ts = v;
    else if (k === "h1" && v) out.h1.push(v);
  }
  return out;
}

// Paddle-Signature: ts=<unix>;h1=<hex HMAC-SHA256(secret, `${ts}:${rawBody}`)>.
// Several h1 values can appear while a secret is being rotated; any match passes.
// Always fail-closed: no secret, no acceptance (unlike tirramind's sandbox fail-open).
export async function verifySignature({ rawBody, header, secret, nowS, maxAgeS = MAX_SIGNATURE_AGE_S }) {
  if (!secret) return { ok: false, reason: "no webhook secret configured" };
  const { ts, h1 } = parseSignatureHeader(header);
  if (!ts || !h1.length) return { ok: false, reason: "missing ts or h1" };
  if (!/^\d{1,12}$/.test(ts)) return { ok: false, reason: "invalid ts" };
  const now = nowS ?? Math.floor(Date.now() / 1000);
  if (Math.abs(now - parseInt(ts, 10)) > maxAgeS) return { ok: false, reason: "stale timestamp" };
  const expected = await hmacSha256Hex(secret, `${ts}:${rawBody}`);
  let match = false;
  for (const sig of h1) match = safeEqual(expected, sig.toLowerCase()) || match;
  return match ? { ok: true } : { ok: false, reason: "invalid signature" };
}

function itemPriceIds(data) {
  return (data.items || [])
    .map((it) => (it && ((it.price && it.price.id) || it.price_id)) || null)
    .filter(Boolean);
}

function toIso(seconds) {
  return new Date(seconds * 1000).toISOString();
}

function isoToS(v) {
  const t = Date.parse(v || "");
  return Number.isFinite(t) ? t / 1000 : null;
}

// Access decision for one stored licence record.
export function accessFor(rec, nowS) {
  if (!rec) return { active: false, plan: null, current_period_end: null, grace_until: null };
  const periodEnd = isoToS(rec.current_period_end);
  const graceEnd = isoToS(rec.grace_until);
  let active = false;
  let graceUntil = null;
  if (rec.status === "active" || rec.status === "trialing") {
    active = true;
  } else if (rec.status === "past_due") {
    active = graceEnd !== null && nowS < graceEnd;
    graceUntil = rec.grace_until || null;
  } else if (rec.status === "canceled" || rec.status === "paused") {
    // Paid-through: access runs to the end of the period already paid for.
    active = periodEnd !== null && nowS < periodEnd;
    graceUntil = active ? rec.current_period_end : null;
  }
  return {
    active,
    plan: rec.plan || "pro",
    current_period_end: rec.current_period_end || null,
    grace_until: graceUntil,
  };
}

// Apply one verified webhook payload. Returns a summary (never the key).
export async function applyEvent(env, payload, nowS) {
  const kv = env.LICENSES;
  const type = String(payload.event_type || "");
  const eventId = String(payload.event_id || "");
  const data = payload.data || {};
  if (!HANDLED_EVENTS.has(type)) return { handled: false, reason: "unhandled event" };

  const allowed = priceIds(env);
  const prices = itemPriceIds(data);
  if (!allowed.length || !prices.some((p) => allowed.includes(p))) {
    return { handled: false, reason: "price not in PADDLE_PRICE_IDS" };
  }

  const subId = type.startsWith("subscription.") ? data.id : data.subscription_id;
  if (!subId || !/^sub_[a-z0-9]+$/i.test(subId)) return { handled: false, reason: "no subscription id" };

  if (eventId && (await kv.get("evt:" + eventId))) {
    return { handled: false, reason: "duplicate event" };
  }

  const occurredAt = payload.occurred_at || toIso(nowS);
  let key = await kv.get("sub:" + subId);
  let rec = key ? await readJson(kv, "lic:" + key) : null;

  // Paddle does not guarantee order: never let an older event overwrite a newer one.
  if (rec && rec.occurred_at && isoToS(occurredAt) !== null
      && isoToS(occurredAt) < isoToS(rec.occurred_at)) {
    if (eventId) await kv.put("evt:" + eventId, "1", { expirationTtl: EVENT_TTL_S });
    return { handled: false, reason: "older than stored state" };
  }

  const prev = rec ? rec.status : null;
  let status;
  if (type === "transaction.completed") {
    // A completed payment means paid up, unless the subscription was canceled.
    status = !prev || prev === "past_due" || prev === "active" || prev === "trialing" ? "active" : prev;
  } else if (SUB_STATUSES.has(data.status)) {
    status = data.status;
  } else {
    status = { "subscription.canceled": "canceled", "subscription.past_due": "past_due" }[type] || "active";
  }

  let periodEnd = (data.current_billing_period && data.current_billing_period.ends_at)
    || (data.billing_period && data.billing_period.ends_at) || null;
  // Paddle fires subscription.canceled when the cancel takes effect: at period
  // end for a scheduled cancel, right away for an immediate one (e.g. refund).
  // Access ends at canceled_at, never later than the paid-through date.
  if (status === "canceled" && isoToS(data.canceled_at) !== null) {
    const known = isoToS(periodEnd || (rec && rec.current_period_end));
    if (known === null || isoToS(data.canceled_at) < known) periodEnd = data.canceled_at;
  }

  if (!rec) {
    if (status !== "active" && status !== "trialing") {
      if (eventId) await kv.put("evt:" + eventId, "1", { expirationTtl: EVENT_TTL_S });
      return { handled: false, reason: "no licence yet and not active" };
    }
    key = newLicenceKey();
    rec = { subscription_id: subId, created: toIso(nowS) };
  }

  let graceUntil = rec.grace_until || null;
  if (status === "past_due" && prev !== "past_due") {
    const days = parseFloat(env.PAST_DUE_GRACE_DAYS || "") || PAST_DUE_GRACE_DAYS;
    graceUntil = toIso(nowS + days * 86400);
  } else if (status === "active" || status === "trialing") {
    graceUntil = null;
  }

  rec = {
    ...rec,
    subscription_id: subId,
    customer_id: data.customer_id || rec.customer_id || null,
    status,
    plan: "pro",
    price_id: prices.find((p) => allowed.includes(p)) || rec.price_id || null,
    current_period_end: periodEnd || rec.current_period_end || null,
    grace_until: graceUntil,
    occurred_at: occurredAt,
    updated: toIso(nowS),
  };

  if (prev === null) {
    // First delivery for this subscription. Two racing first deliveries:
    // KV has no transactions, so re-read and keep whichever mapping won.
    const existing = await kv.get("sub:" + subId);
    if (existing) {
      key = existing;
    } else {
      await kv.put("sub:" + subId, key);
      key = (await kv.get("sub:" + subId)) || key;
    }
  }
  await kv.put("lic:" + key, JSON.stringify(rec));
  if (eventId) await kv.put("evt:" + eventId, "1", { expirationTtl: EVENT_TTL_S });
  return { handled: true, subscription_id: subId, status, created: prev === null };
}

// GET /transactions/{id} with the server-side API key.
export async function fetchTransaction(env, txnId, fetchImpl = fetch) {
  const res = await fetchImpl(`${apiBase(env)}/transactions/${encodeURIComponent(txnId)}`, {
    headers: { Authorization: `Bearer ${env.PADDLE_API_KEY}`, Accept: "application/json" },
  });
  if (res.status === 404) return { status: 404 };
  if (!res.ok) return { status: res.status };
  const body = await res.json().catch(() => ({}));
  return { status: 200, data: (body && body.data) || {} };
}

export { itemPriceIds };
