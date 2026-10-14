// Cloudflare Pages Function: POST /api/paddle/webhook
// Paddle Billing notifications -> AWOS Pro licences in KV LICENSES.
// Verifies Paddle-Signature with PADDLE_WEBHOOK_SECRET (fail-closed), dedupes by
// event_id, ignores prices not in PADDLE_PRICE_IDS. The response never carries a key.
import { json, readBodyCapped, nowSeconds } from "../../../lib/common.js";
import { verifySignature, applyEvent } from "../../../lib/paddle.js";

const MAX_BODY = 256 * 1024;

export async function onRequestPost({ request, env }) {
  if (!env.LICENSES) return json({ error: "not configured" }, 503);
  const raw = await readBodyCapped(request, MAX_BODY);
  if (raw === null) return json({ error: "body too large" }, 413);
  const check = await verifySignature({
    rawBody: raw,
    header: request.headers.get("paddle-signature"),
    secret: env.PADDLE_WEBHOOK_SECRET,
    nowS: nowSeconds(),
  });
  if (!check.ok) return json({ error: "signature rejected" }, 401);
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }
  // A KV failure throws -> 500 -> Paddle retries the delivery (same event_id).
  const out = await applyEvent(env, payload, nowSeconds());
  return json({ ok: true, handled: out.handled, reason: out.reason || null });
}
