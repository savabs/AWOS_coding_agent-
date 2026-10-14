// Cloudflare Pages Function: GET /api/license/claim?txn=txn_...
// The /pro/welcome/ page calls this after checkout. Never trusts the browser:
// asks Paddle's API (server-side key) whether the transaction is completed and
// which subscription it belongs to, then returns that subscription's licence.
// Statuses: claimed (200), pending (202, poll again), not_completed (202),
// unknown_transaction (404), not_awos (404), already_claimed (410),
// rate_limited (429), upstream_error (502), not_configured (503).
import { json, readJson, nowSeconds } from "../../../lib/common.js";
import { fetchTransaction, itemPriceIds, priceIds, accessFor } from "../../../lib/paddle.js";

const TXN_RE = /^txn_[a-z0-9]{10,64}$/i;
const MAX_ATTEMPTS_PER_HOUR = 40;   // the page polls every 3 s for about a minute
const CLAIM_WINDOW_S = 24 * 3600;   // the key can be shown again for a day...
const MAX_CLAIMS = 5;               // ...at most this many times

export async function onRequestGet({ request, env, fetchImpl }) {
  const kv = env.LICENSES;
  if (!kv || !env.PADDLE_API_KEY) return json({ status: "not_configured" }, 503);
  const txn = new URL(request.url).searchParams.get("txn") || "";
  if (!TXN_RE.test(txn)) return json({ status: "unknown_transaction" }, 404);

  const now = nowSeconds();
  const rlKey = "claimrl:" + txn;
  const attempts = parseInt((await kv.get(rlKey)) || "0", 10);
  if (attempts >= MAX_ATTEMPTS_PER_HOUR) return json({ status: "rate_limited" }, 429);
  await kv.put(rlKey, String(attempts + 1), { expirationTtl: 3600 });

  let t;
  try {
    t = await fetchTransaction(env, txn, fetchImpl || fetch);
  } catch {
    return json({ status: "upstream_error" }, 502);
  }
  if (t.status === 404) return json({ status: "unknown_transaction" }, 404);
  if (t.status !== 200) return json({ status: "upstream_error" }, 502);
  const data = t.data;
  if (!itemPriceIds(data).some((p) => priceIds(env).includes(p))) {
    return json({ status: "not_awos" }, 404);
  }
  if (data.status !== "completed") return json({ status: "not_completed" }, 202);
  if (!data.subscription_id) return json({ status: "pending" }, 202);

  const key = await kv.get("sub:" + data.subscription_id);
  const rec = key ? await readJson(kv, "lic:" + key) : null;
  if (!rec) return json({ status: "pending" }, 202);  // webhook not landed yet

  const claim = (await readJson(kv, "claim:" + txn)) || { first: now, count: 0 };
  if (now - claim.first > CLAIM_WINDOW_S || claim.count >= MAX_CLAIMS) {
    return json({ status: "already_claimed" }, 410);
  }
  claim.count += 1;
  await kv.put("claim:" + txn, JSON.stringify(claim), { expirationTtl: 90 * 86400 });
  const access = accessFor(rec, now);
  return json({
    status: "claimed",
    key,
    plan: access.plan,
    active: access.active,
    command: `awos activate ${key}`,
  });
}
