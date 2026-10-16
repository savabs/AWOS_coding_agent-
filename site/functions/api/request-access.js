// Cloudflare Pages Function: /api/request-access (KV namespace ACCESS_REQUESTS).
//
// POST, two forms:
//   - early access (default):   {name, email, role, team, tickets, tools}  -> req:<email>
//   - local-first waitlist:      {form:"waitlist", role, why, note?, email?, src?} -> wl:...
//     role and why must come from fixed lists; the record gets a segment tag
//     (see segmentFor) so discovery can count sign-ups per candidate customer.
// GET ?token=ADMIN_TOKEN (owner only, constant-time compare; wrong token -> 404):
//   (default)                 {count, requests, waitlist_count}
//   &view=waitlist            {count, by_segment, by_role, by_why, by_country, by_src, entries}
//   &view=waitlist&format=csv the waitlist entries as CSV
// No cookies, no IP stored (IP is used only for an hourly rate-limit key).
import { json, adminAuthorized, readBodyCapped } from "../../lib/common.js";

const MAX_FIELD = 2000;
const MAX_BODY = 16 * 1024;           // early-access form: 6 fields x 2000 chars fits
const MAX_WAITLIST_BODY = 4 * 1024;
const MAX_NOTE = 500;
const MAX_EMAIL = 254;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const RATE_PER_HOUR = 5;

export const ROLES = ["solo_dev", "team_dev", "founder", "researcher", "other"];
export const WHY = ["privacy", "cost", "offline", "speed", "curiosity", "other"];
// Where the visitor came from: ?src= on the landing URL (first-party, no cookies).
// Anything else is stored as "".
export const SOURCES = ["hn", "localllama", "devindia", "x", "interview", "other"];
export const SEGMENTS = ["privacy_team", "privacy_individual", "cost_sensitive", "local_builder", "unclassified"];

// Candidate customers from docs/STRATEGY.md, derived only from the two answers.
export function segmentFor(role, why) {
  if (why === "privacy") return role === "team_dev" || role === "founder" ? "privacy_team" : "privacy_individual";
  if (why === "cost") return "cost_sensitive";
  if (why === "offline" || why === "speed" || why === "curiosity") return "local_builder";
  return "unclassified";
}

function clean(v) {
  return String(v ?? "").slice(0, MAX_FIELD).trim();
}

// Strip control characters (keeps newlines and tabs), trim.
function text(v) {
  return String(v).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim();
}

function parse(raw) {
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" && !Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

async function rateLimited(kv, request) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const key = "rl:" + ip;
  const hits = parseInt((await kv.get(key)) || "0", 10);
  if (hits >= RATE_PER_HOUR) return true;
  await kv.put(key, String(hits + 1), { expirationTtl: 3600 });
  return false;
}

// The stored waitlist record, or {error} for a 400.
export function cleanWaitlist(data) {
  for (const f of ["role", "why", "note", "email", "website", "src"]) {
    if (data[f] != null && typeof data[f] !== "string") return { error: `${f} must be text.` };
  }
  const role = data.role || "";
  const why = data.why || "";
  if (!ROLES.includes(role)) return { error: "Pick your role." };
  if (!WHY.includes(why)) return { error: "Pick why you want a local agent." };
  const note = text(data.note || "");
  if (note.length > MAX_NOTE) return { error: `Keep the note under ${MAX_NOTE} characters.` };
  const email = text(data.email || "").toLowerCase();
  if (email && (email.length > MAX_EMAIL || !EMAIL.test(email))) return { error: "That email does not look valid." };
  const src = SOURCES.includes(data.src) ? data.src : "";
  return { rec: { form: "waitlist", segment: segmentFor(role, why), role, why, note, email, src } };
}

function randomId() {
  const b = new Uint8Array(8);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

async function postWaitlist(request, env, data) {
  // Honeypot: a hidden field real visitors never fill. Look successful, store nothing.
  if (typeof data.website === "string" && data.website.trim()) return json({ ok: true });
  const out = cleanWaitlist(data);
  if (out.error) return json({ error: out.error }, 400);
  const kv = env.ACCESS_REQUESTS;
  if (await rateLimited(kv, request)) return json({ error: "Too many requests. Try again in an hour." }, 429);
  const ts = new Date().toISOString();
  const rec = { ...out.rec, ts, country: (request.headers.get("cf-ipcountry") || "").slice(0, 2) };
  // With an email, a resubmit updates the same record; without, each is its own.
  const key = rec.email ? "wl:e:" + rec.email : `wl:a:${ts}:${randomId()}`;
  await kv.put(key, JSON.stringify(rec));
  return json({ ok: true, segment: rec.segment });
}

export async function onRequestPost({ request, env }) {
  if (!env.ACCESS_REQUESTS) return json({ error: "Sign-ups are not open yet." }, 503);
  const raw = await readBodyCapped(request, MAX_BODY);
  if (raw === null) return json({ error: "Request too large." }, 413);
  const data = parse(raw);
  if (!data) return json({ error: "Send the form as JSON." }, 400);

  if (data.form === "waitlist") {
    if (new TextEncoder().encode(raw).length > MAX_WAITLIST_BODY) return json({ error: "Request too large." }, 413);
    return postWaitlist(request, env, data);
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
  if (await rateLimited(env.ACCESS_REQUESTS, request)) {
    return json({ error: "Too many requests. Try again in an hour." }, 429);
  }
  await env.ACCESS_REQUESTS.put("req:" + rec.email, JSON.stringify(rec));
  return json({ ok: true });
}

async function listPrefix(kv, prefix) {
  const out = [];
  let cursor;
  do {
    const page = await kv.list({ prefix, cursor });
    for (const k of page.keys) {
      const v = await kv.get(k.name);
      if (!v) continue;
      try { out.push(JSON.parse(v)); } catch { /* skip a corrupt row */ }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  out.sort((a, b) => (a.ts < b.ts ? 1 : -1));
  return out;
}

function countBy(rows, field, keys = []) {
  const out = Object.fromEntries(keys.map((k) => [k, 0]));
  for (const r of rows) {
    const k = r[field] || "unknown";
    out[k] = (out[k] || 0) + 1;
  }
  return out;
}

const CSV_COLS = ["ts", "segment", "role", "why", "src", "country", "email", "note"];

function csvCell(v) {
  let s = String(v ?? "");
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;   // no formulas when opened in a spreadsheet
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

export function waitlistCsv(rows) {
  return [CSV_COLS.join(","), ...rows.map((r) => CSV_COLS.map((c) => csvCell(r[c])).join(","))].join("\n") + "\n";
}

export async function onRequestGet({ request, env }) {
  if (!adminAuthorized(request, env) || !env.ACCESS_REQUESTS) return json({ error: "Not found." }, 404);
  const params = new URL(request.url).searchParams;
  const kv = env.ACCESS_REQUESTS;
  if (params.get("view") === "waitlist") {
    const rows = await listPrefix(kv, "wl:");
    if (params.get("format") === "csv") {
      return new Response(waitlistCsv(rows), {
        headers: {
          "content-type": "text/csv; charset=utf-8",
          "content-disposition": 'attachment; filename="awos-waitlist.csv"',
          "cache-control": "no-store",
        },
      });
    }
    return json({
      count: rows.length,
      by_segment: countBy(rows, "segment", SEGMENTS),
      by_role: countBy(rows, "role", ROLES),
      by_why: countBy(rows, "why", WHY),
      by_country: countBy(rows, "country"),
      by_src: countBy(rows, "src"),
      entries: rows,
    });
  }
  const out = await listPrefix(kv, "req:");
  let waitlist = 0, cursor;
  do {
    const page = await kv.list({ prefix: "wl:", cursor });
    waitlist += page.keys.length;
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return json({ count: out.length, requests: out, waitlist_count: waitlist });
}
