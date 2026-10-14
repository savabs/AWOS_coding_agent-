// Cloudflare Pages Function: GET /api/usage?token=...[&days=30]
// Owner-only read of the daily usage counters (same ADMIN_TOKEN check as
// /api/request-access, compared in constant time). Wrong token -> 404.
import { json, adminAuthorized } from "../../lib/common.js";

export async function onRequestGet({ request, env }) {
  if (!adminAuthorized(request, env) || !env.USAGE) return json({ error: "Not found." }, 404);
  const asked = parseInt(new URL(request.url).searchParams.get("days") || "30", 10) || 30;
  const days = Math.min(400, Math.max(1, asked));
  const since = new Date(Date.now() - (days - 1) * 86400 * 1000).toISOString().slice(0, 10);
  const out = {};
  let cursor;
  do {
    const page = await env.USAGE.list({ prefix: "day:", cursor });
    for (const k of page.keys) {
      const [, day, event] = k.name.split(":");
      if (!day || !event || day < since) continue;
      const v = await env.USAGE.get(k.name);
      if (!v) continue;
      (out[day] ||= {})[event] = JSON.parse(v);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return json({ since, days: out });
}
