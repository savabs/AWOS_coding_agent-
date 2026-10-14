// Cloudflare Pages Function: POST /api/ping
// Opt-in anonymous usage counts from the AWOS CLI (scaffold/agent/usage_ping.py).
// Strict schema: unknown fields are dropped, values must come from fixed lists.
// Stores daily counters only, in KV USAGE: one doc per day and event,
//   day:<YYYY-MM-DD>:<event> -> {"total": n, "<field>=<value>": n, ...}
// (one write per ping; the free KV plan allows 1000 writes/day). No IP, no
// install id, no request headers are stored.
import { json, readBodyCapped } from "../../lib/common.js";

const MAX_BODY = 1024;
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const VERSION = /^[0-9]{1,3}\.[0-9]{1,3}(\.[0-9]{1,4})?([a-z0-9.+-]{0,12})?$/i;
const OS = ["darwin", "linux", "windows", "other"];
const PROVIDER = ["anthropic", "local", "other"];

export const SCHEMA = {
  install: { os: OS },
  first_run: { os: OS, provider: PROVIDER },
  job_finished: {
    os: OS,
    provider: PROVIDER,
    outcome: ["solved", "failed", "cancelled"],
    duration: ["lt1m", "1to5m", "5to15m", "15to60m", "gt60m"],
    cost: ["zero", "lt1c", "1to10c", "10cto1", "gt1"],
  },
};

// The counted fields of a ping, or null when the ping is not acceptable.
export function cleanPing(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const fields = Object.hasOwn(SCHEMA, body.event) ? SCHEMA[body.event] : null;
  if (!fields || body.v !== 1 || !UUID4.test(String(body.install_id || ""))) return null;
  const out = { event: body.event };
  for (const [name, allowed] of Object.entries(fields)) {
    if (allowed.includes(body[name])) out[name] = body[name];
  }
  if (typeof body.awos_version === "string" && VERSION.test(body.awos_version)) {
    out.version = body.awos_version;
  }
  return out;
}

export async function onRequestPost({ request, env }) {
  if (!env.USAGE) return json({ ok: false }, 503);
  const raw = await readBodyCapped(request, MAX_BODY);
  let body = null;
  try { body = raw === null ? null : JSON.parse(raw); } catch { body = null; }
  const ping = cleanPing(body);
  if (!ping) return json({ ok: false }, 400);
  const day = new Date().toISOString().slice(0, 10);
  const key = `day:${day}:${ping.event}`;
  let doc = {};
  try { doc = JSON.parse((await env.USAGE.get(key)) || "{}") || {}; } catch { doc = {}; }
  doc.total = (doc.total || 0) + 1;
  for (const [field, value] of Object.entries(ping)) {
    if (field === "event") continue;
    const k = `${field}=${value}`;
    doc[k] = (doc[k] || 0) + 1;
  }
  try {
    await env.USAGE.put(key, JSON.stringify(doc), { expirationTtl: 400 * 86400 });
  } catch {
    // KV allows about 1 write/s per key; a dropped count is acceptable.
  }
  return json({ ok: true }, 202);
}
