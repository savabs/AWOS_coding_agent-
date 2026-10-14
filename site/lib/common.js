// Shared helpers for the Pages Functions (imported, never routed: it lives
// outside site/functions/). Runs on Workers and on Node >= 20 (tests).

export function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...extra,
    },
  });
}

const enc = new TextEncoder();

// Constant-time string compare (length leak only).
export function safeEqual(a, b) {
  a = String(a ?? "");
  b = String(b ?? "");
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

// Same admin-token check as /api/request-access (env.ADMIN_TOKEN via ?token=),
// compared in constant time. Unset token = endpoint does not exist.
export function adminAuthorized(request, env) {
  const token = new URL(request.url).searchParams.get("token");
  return Boolean(env.ADMIN_TOKEN) && safeEqual(token, env.ADMIN_TOKEN);
}

export async function hmacSha256Hex(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
  return Array.from(sig, (b) => b.toString(16).padStart(2, "0")).join("");
}

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

// RFC 4648 base32 without padding.
export function base32(bytes) {
  let out = "", bits = 0, value = 0;
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += B32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}

export const LICENCE_KEY_RE = /^awos_[A-Z2-7]{32}$/;

// awos_ + 32 base32 chars = 160 random bits.
export function newLicenceKey() {
  const bytes = new Uint8Array(20);
  crypto.getRandomValues(bytes);
  return "awos_" + base32(bytes);
}

export async function readJson(kv, key) {
  const raw = await kv.get(key);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

// Read at most `limit` bytes of the body as text; null when larger.
export async function readBodyCapped(request, limit) {
  const len = parseInt(request.headers.get("content-length") || "0", 10);
  if (len > limit) return null;
  const text = await request.text();
  return enc.encode(text).length > limit ? null : text;
}

export function nowSeconds() {
  return Math.floor(Date.now() / 1000);
}
