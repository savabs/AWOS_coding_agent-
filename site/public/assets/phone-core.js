// AWOS phone link, protocol v1: pairing, envelopes and message checks.
// Shared by the /phone/ page (phone.js) and the node tests. No DOM here.
// Spec: docs/launch/phone_link.md.
//
// Pair link: https://awos.tirramind.com/phone/#c=<channel>&k=<key>&t=<smsToken>&n=<ntfyTopic>
// Envelope:  {id, iv, ct}: AES-GCM 256, 12-byte iv, AAD = "to-phone" | "to-mac", plaintext JSON.

export const DIR_TO_PHONE = "to-phone";
export const DIR_TO_MAC = "to-mac";
const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64uEncode(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64uDecode(str) {
  if (typeof str !== "string" || !/^[A-Za-z0-9_-]*$/.test(str) || str.length % 4 === 1) {
    throw new Error("not base64url");
  }
  const s = str.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((str.length + 3) % 4);
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function randomId(bytes = 16) {
  const b = new Uint8Array(bytes);
  crypto.getRandomValues(b);
  return b64uEncode(b);
}

const CHANNEL_RE = /^[A-Za-z0-9_-]{22,128}$/;
const TOPIC_RE = /^awos-[0-9a-f]{16}$/;

// Pairing from a fragment ("#c=..&k=..&t=..&n=..") or a whole pair link.
// Returns {ok: true, pair: {c, k, t, n}} or {ok: false, error}.
export function parsePairing(input) {
  let text = String(input || "").trim();
  const hash = text.indexOf("#");
  if (hash >= 0) text = text.slice(hash + 1);
  if (!text) return { ok: false, error: "empty" };
  const p = new URLSearchParams(text);
  const pair = { c: p.get("c") || "", k: p.get("k") || "", t: p.get("t") || "", n: p.get("n") || "" };
  if (!CHANNEL_RE.test(pair.c)) return { ok: false, error: "The link has no valid channel (c=)." };
  let keyLen = 0;
  try { keyLen = b64uDecode(pair.k).length; } catch { keyLen = 0; }
  if (keyLen !== 32) return { ok: false, error: "The link has no valid key (k=)." };
  if (!CHANNEL_RE.test(pair.t)) return { ok: false, error: "The link has no valid SMS token (t=)." };
  if (!TOPIC_RE.test(pair.n)) return { ok: false, error: "The link has no valid notification topic (n=)." };
  return { ok: true, pair };
}

export function importKey(k) {
  return crypto.subtle.importKey("raw", b64uDecode(k), { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

export async function seal(key, dir, obj, id = randomId()) {
  const iv = new Uint8Array(12);
  crypto.getRandomValues(iv);
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: enc.encode(dir) }, key,
    enc.encode(JSON.stringify(obj)));
  return { id, iv: b64uEncode(iv), ct: b64uEncode(new Uint8Array(ct)) };
}

// Decrypt an envelope for `dir`; throws when the key, iv, AAD or ciphertext is wrong.
export async function open(key, dir, envelope) {
  const iv = b64uDecode(envelope.iv);
  if (iv.length !== 12) throw new Error("bad iv");
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv, additionalData: enc.encode(dir) }, key,
    b64uDecode(envelope.ct));
  return JSON.parse(dec.decode(pt));
}

const KINDS = new Set(["code", "approve", "captcha", "login"]);
const str = (v, max) => (typeof v === "string" ? v.slice(0, max) : "");

// A checked copy of a mac->phone message, or null. Unknown fields are dropped.
export function cleanIncoming(m) {
  if (!m || typeof m !== "object" || Array.isArray(m)) return null;
  const req = str(m.req, 64);
  const at = Number.isFinite(m.at) ? m.at : 0;
  if (m.type === "need") {
    if (!req || !KINDS.has(m.kind)) return null;
    return { type: "need", req, kind: m.kind, portal: str(m.portal, 120) || "a portal",
             message: str(m.message, 600), at };
  }
  if (m.type === "resolved") return req ? { type: "resolved", req } : null;
  if (m.type === "status") {
    const items = Array.isArray(m.items) ? m.items.slice(0, 50).map((i) => ({
      portal: str(i && i.portal, 120), state: str(i && i.state, 40), detail: str(i && i.detail, 300),
    })) : [];
    return { type: "status", summary: str(m.summary, 200), items, at };
  }
  return null;
}

export const CODE_RE = /^[A-Za-z0-9]{1,12}$/;

// phone->mac replies (plaintext before sealing).
export function codeReply(req, code) {
  const c = String(code || "").replace(/[\s-]/g, "");
  if (!CODE_RE.test(c)) throw new Error("A code is 1 to 12 letters or digits.");
  return { type: "code", req, code: c };
}
export const approveReply = (req, yes) => ({ type: "approve", req, yes: Boolean(yes) });
export const seenReply = (req) => ({ type: "seen", req });
