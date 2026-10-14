// Cloudflare Pages Function: POST /api/license/check  {key, install_id}
// -> {active, plan, current_period_end, grace_until}. Nothing else, ever.
// Unknown and malformed keys get the same inactive answer.
import { json, readJson, readBodyCapped, nowSeconds, LICENCE_KEY_RE } from "../../../lib/common.js";
import { accessFor } from "../../../lib/paddle.js";

export async function onRequestPost({ request, env }) {
  if (!env.LICENSES) return json({ error: "not configured" }, 503);
  const raw = await readBodyCapped(request, 2048);
  let body = null;
  try { body = raw === null ? null : JSON.parse(raw); } catch { body = null; }
  if (!body || typeof body !== "object") return json({ error: "send JSON {key, install_id}" }, 400);
  const key = typeof body.key === "string" ? body.key.trim() : "";
  // install_id is accepted (and validated) for future seat limits; not stored.
  if (body.install_id != null && !(typeof body.install_id === "string" && body.install_id.length <= 64)) {
    return json({ error: "bad install_id" }, 400);
  }
  const rec = LICENCE_KEY_RE.test(key) ? await readJson(env.LICENSES, "lic:" + key) : null;
  return json(accessFor(rec, nowSeconds()));
}
