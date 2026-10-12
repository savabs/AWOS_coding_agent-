// Cloudflare Pages Function: POST /api/request-access
// Stores early-access requests in the KV namespace bound as ACCESS_REQUESTS.
// GET /api/request-access?token=... lists them (token = env.ADMIN_TOKEN).
const MAX_FIELD = 2000;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function clean(v) {
  return String(v ?? "").slice(0, MAX_FIELD).trim();
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export async function onRequestPost({ request, env }) {
  let data;
  try {
    data = await request.json();
  } catch {
    return json({ error: "Send the form as JSON." }, 400);
  }
  const rec = {
    name: clean(data.name),
    email: clean(data.email).toLowerCase(),
    role: clean(data.role),
    team: clean(data.team),
    tickets: clean(data.tickets),
    tools: clean(data.tools),
    ts: new Date().toISOString(),
    country: request.headers.get("cf-ipcountry") || "",
  };
  if (!rec.name || !EMAIL.test(rec.email)) {
    return json({ error: "Name and a valid email are required." }, 400);
  }
  // One stored request per email (a resubmit updates it); simple per-IP rate limit.
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const rlKey = "rl:" + ip;
  const hits = parseInt((await env.ACCESS_REQUESTS.get(rlKey)) || "0", 10);
  if (hits >= 5) return json({ error: "Too many requests. Try again in an hour." }, 429);
  await env.ACCESS_REQUESTS.put(rlKey, String(hits + 1), { expirationTtl: 3600 });
  await env.ACCESS_REQUESTS.put("req:" + rec.email, JSON.stringify(rec));
  return json({ ok: true });
}

export async function onRequestGet({ request, env }) {
  const token = new URL(request.url).searchParams.get("token");
  if (!env.ADMIN_TOKEN || token !== env.ADMIN_TOKEN) return json({ error: "Not found." }, 404);
  const out = [];
  let cursor;
  do {
    const page = await env.ACCESS_REQUESTS.list({ prefix: "req:", cursor });
    for (const k of page.keys) {
      const v = await env.ACCESS_REQUESTS.get(k.name);
      if (v) out.push(JSON.parse(v));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  out.sort((a, b) => (a.ts < b.ts ? 1 : -1));
  return json({ count: out.length, requests: out });
}
