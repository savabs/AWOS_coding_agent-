// /phone/: answer AWOS Replay from a phone (protocol v1, docs/launch/phone_link.md).
// Everything from the relay is untrusted: text goes into the page with
// textContent only. The pair secrets live in localStorage on this phone; the
// pair link's #fragment is never sent to a server and is wiped from the address bar.
import { parsePairing, importKey, seal, open, cleanIncoming, codeReply, approveReply, seenReply,
  DIR_TO_PHONE, DIR_TO_MAC } from "./phone-core.js";

const STORE = "awos-phone-pair-v1";
const STATUS_STORE = "awos-phone-status-v1";
const POLL_MS = 4000;

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

let pair = null;     // {c, k, t, n}
let key = null;      // CryptoKey
let since = 0;       // newest relay `at` seen this session
let timer = null;
let polling = false;
const needs = new Map();    // req -> {need, msgId, el, state}
const seenSent = new Set(); // req
const handled = new Set();  // relay message ids already processed this session
let latestStatus = null;    // {status, msgId, at}
let badCount = 0;

function say(text, cls) {
  const m = $("ph-msg");
  m.className = "status" + (cls ? " " + cls : "");
  m.textContent = text || "";
}

function live(text) { $("ph-live").textContent = text; }

const api = (dir) => `/api/relay/${encodeURIComponent(pair.c)}/${dir}`;

async function relay(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
    credentials: "omit",
    referrerPolicy: "no-referrer",
  });
  if (!res.ok) throw new Error(`relay ${res.status}`);
  return res.status === 204 ? null : res.json();
}

async function send(obj) {
  await relay("POST", api(DIR_TO_MAC), await seal(key, DIR_TO_MAC, obj));
}

async function drop(msgId) {
  if (!msgId) return;
  try { await relay("DELETE", `${api(DIR_TO_PHONE)}/${encodeURIComponent(msgId)}`); } catch { /* expires anyway */ }
}

// ── rendering ────────────────────────────────────────────────────────────────

const KIND_TITLE = { code: "Enter a code", approve: "Approve a step", captcha: "Captcha at your Mac", login: "Log in at your Mac" };

function timeText(at) {
  if (!at) return "";
  try { return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch { return ""; }
}

function cardFor(entry) {
  const { need } = entry;
  const card = el("article", "ph-card ph-" + need.kind);
  const head = el("div", "ph-card-head");
  head.append(el("span", "eyebrow", KIND_TITLE[need.kind]), el("span", "ph-at", timeText(need.at)));
  card.append(head, el("h2", "ph-portal", need.portal));
  if (need.message) card.append(el("p", "ph-text", need.message));
  const note = el("p", "status");
  const setNote = (t, cls) => { note.className = "status" + (cls ? " " + cls : ""); note.textContent = t; };

  if (need.kind === "code") {
    const form = el("form", "ph-code");
    form.autocomplete = "off";
    const id = "code-" + need.req.replace(/[^A-Za-z0-9_-]/g, "");
    const label = el("label", null, "Code");
    label.htmlFor = id;
    const input = el("input");
    Object.assign(input, { id, type: "text", inputMode: "numeric", autocomplete: "one-time-code",
      maxLength: 14, required: true, spellcheck: false, placeholder: "123456" });
    input.setAttribute("autocapitalize", "characters");
    input.setAttribute("enterkeyhint", "send");
    const btn = el("button", "btn primary", "Send code");
    btn.type = "submit";
    const letters = el("button", "linkish", "Code has letters?");
    letters.type = "button";
    letters.addEventListener("click", () => { input.inputMode = "text"; input.placeholder = "A1B2C3"; input.focus(); });
    form.append(label, input, btn, letters);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      let reply;
      try { reply = codeReply(need.req, input.value); } catch (e) { setNote(e.message, "err"); return; }
      btn.disabled = true;
      try {
        await send(reply);
        entry.state = "sent";
        input.disabled = true;
        setNote("Sent. Waiting for your Mac…", "ok");
        await drop(entry.msgId);
      } catch {
        btn.disabled = false;
        setNote("Could not reach the relay. Try again.", "err");
      }
    });
    card.append(form);
  } else if (need.kind === "approve") {
    const row = el("div", "cta");
    const yes = el("button", "btn primary", "Approve");
    const no = el("button", "btn ghost", "Deny");
    yes.type = no.type = "button";
    const answer = async (ok) => {
      yes.disabled = no.disabled = true;
      try {
        await send(approveReply(need.req, ok));
        entry.state = "sent";
        setNote(ok ? "Approved. Waiting for your Mac…" : "Denied. Your Mac will skip this step.", "ok");
        await drop(entry.msgId);
      } catch {
        yes.disabled = no.disabled = false;
        setNote("Could not reach the relay. Try again.", "err");
      }
    };
    yes.addEventListener("click", () => answer(true));
    no.addEventListener("click", () => answer(false));
    row.append(yes, no);
    card.append(row);
  } else {
    const at = el("p", "ph-mac");
    at.append("Do this at your Mac: ", el("b", null, need.portal));
    card.append(at, el("p", "sub", need.kind === "captcha"
      ? "Captchas cannot be solved from the phone yet. The run waits for you."
      : "Logins stay on your Mac. Sign in there and the run continues."));
    const ok = el("button", "btn ghost", "Got it");
    ok.type = "button";
    ok.addEventListener("click", () => { entry.el.classList.add("ph-dim"); ok.disabled = true; });
    card.append(ok);
  }
  card.append(note);
  return card;
}

function renderEmpty() {
  $("ph-empty").hidden = needs.size > 0;
  const n = needs.size;
  document.title = n ? `(${n}) AWOS on your phone` : "AWOS on your phone";
}

function renderStatus() {
  const box = $("ph-status-box");
  if (!latestStatus) { box.hidden = true; return; }
  const s = latestStatus.status;
  box.hidden = false;
  $("ph-summary").textContent = s.summary || "Run update";
  const ul = $("ph-items");
  ul.replaceChildren();
  for (const it of s.items) {
    const li = el("li");
    li.append(el("span", "pill " + stateClass(it.state), it.state || "?"), " ", el("b", null, it.portal));
    if (it.detail) li.append(el("span", "ph-detail", it.detail));
    ul.append(li);
  }
  $("ph-status-at").textContent = s.at ? "As of " + timeText(s.at) : "";
}

function stateClass(state) {
  const s = String(state || "").toLowerCase();
  if (/^(got|done|ok|pass|saved)/.test(s)) return "pass";
  if (/(need|wait|blocked)/.test(s)) return "wait";
  if (/(fail|error)/.test(s)) return "fail";
  return "";
}

// ── message handling ─────────────────────────────────────────────────────────

async function handle(env) {
  if (handled.has(env.id)) return;
  let msg;
  try {
    msg = cleanIncoming(await open(key, DIR_TO_PHONE, env));
  } catch {
    msg = null;
  }
  handled.add(env.id);
  if (!msg) { badCount++; return; }

  if (msg.type === "need") {
    const old = needs.get(msg.req);
    if (old) { old.el.remove(); if (old.msgId !== env.id) drop(old.msgId); }
    const entry = { need: msg, msgId: env.id, state: "open", el: null };
    entry.el = cardFor(entry);
    needs.set(msg.req, entry);
    $("ph-needs").prepend(entry.el);
    if (!seenSent.has(msg.req)) {
      seenSent.add(msg.req);
      send(seenReply(msg.req)).catch(() => seenSent.delete(msg.req));
    }
    if (navigator.vibrate) { try { navigator.vibrate(200); } catch { /* ignore */ } }
  } else if (msg.type === "resolved") {
    const old = needs.get(msg.req);
    if (old) { old.el.remove(); needs.delete(msg.req); drop(old.msgId); }
    drop(env.id);
  } else if (msg.type === "status") {
    const at = msg.at || env.at;
    if (!latestStatus || at >= latestStatus.at) {
      if (latestStatus && latestStatus.msgId !== env.id) drop(latestStatus.msgId);
      latestStatus = { status: msg, msgId: env.id, at };
      try { localStorage.setItem(STATUS_STORE, JSON.stringify({ status: msg, at })); } catch { /* private mode */ }
      renderStatus();
    } else {
      drop(env.id);
    }
  }
  renderEmpty();
}

async function poll() {
  if (!pair || polling || document.visibilityState === "hidden") return;
  polling = true;
  try {
    const data = await relay("GET", `${api(DIR_TO_PHONE)}?since=${since}`);
    const list = Array.isArray(data && data.messages) ? data.messages : [];
    for (const m of list) {
      if (m && typeof m.at === "number") since = Math.max(since, m.at);
      if (m && typeof m.id === "string") await handle(m);
    }
    live(badCount ? `Connected · ${badCount} unreadable (re-pair?)` : "Connected");
  } catch (e) {
    live(String(e.message || "").includes("503") ? "Relay not set up yet" : "Offline, retrying…");
  } finally {
    polling = false;
  }
}

function startPolling() {
  stopPolling();
  poll();
  timer = setInterval(poll, POLL_MS);
}
function stopPolling() { if (timer) clearInterval(timer); timer = null; }

document.addEventListener("visibilitychange", () => {
  if (!pair) return;
  if (document.visibilityState === "visible") startPolling();
  else stopPolling();
});

// ── pairing ──────────────────────────────────────────────────────────────────

function loadPair() {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) return null;
    const p = JSON.parse(raw);
    const r = parsePairing(`c=${p.c}&k=${p.k}&t=${p.t}&n=${p.n}`);
    return r.ok ? r.pair : null;
  } catch {
    return null;
  }
}

function savePair(p) {
  try {
    localStorage.setItem(STORE, JSON.stringify({ ...p, pairedAt: Date.now() }));
    return true;
  } catch {
    return false;
  }
}

async function showPaired(p) {
  pair = p;
  key = await importKey(p.k);
  $("ph-unpaired").hidden = true;
  $("ph-paired").hidden = false;
  $("ph-title").textContent = "AWOS on your phone";
  $("ph-topic").textContent = p.n;
  $("ph-sms-url").textContent = `${location.origin}/api/relay/${p.c}/sms`;
  $("ph-sms-token").textContent = p.t;
  try {
    const s = JSON.parse(localStorage.getItem(STATUS_STORE) || "null");
    if (s && s.status && !latestStatus) {
      const clean = cleanIncoming({ type: "status", ...s.status });
      if (clean) { latestStatus = { status: clean, msgId: null, at: s.at || 0 }; renderStatus(); }
    }
  } catch { /* ignore */ }
  renderEmpty();
  startPolling();
}

function showUnpaired() {
  stopPolling();
  pair = key = null;
  since = 0;
  needs.clear(); handled.clear(); seenSent.clear();
  latestStatus = null; badCount = 0;
  $("ph-needs").replaceChildren();
  $("ph-paired").hidden = true;
  $("ph-unpaired").hidden = false;
  $("ph-title").textContent = "Pair your phone";
  live("");
}

async function pairFrom(text) {
  const r = parsePairing(text);
  if (!r.ok) { $("ph-pair-err").textContent = r.error; return false; }
  $("ph-pair-err").textContent = "";
  if (!savePair(r.pair)) say("This browser would not save the pairing (private mode?). It works until you close the page.", "err");
  else say("Paired with your Mac.", "ok");
  await showPaired(r.pair);
  return true;
}

$("ph-paste").addEventListener("submit", (ev) => {
  ev.preventDefault();
  pairFrom($("ph-link").value).then((ok) => { if (ok) $("ph-link").value = ""; });
});

$("ph-forget").addEventListener("click", () => {
  if (!confirm("Forget this Mac? You will need a new pair link from AWOS Replay.")) return;
  try { localStorage.removeItem(STORE); localStorage.removeItem(STATUS_STORE); } catch { /* ignore */ }
  showUnpaired();
  say("Forgotten. This phone no longer has the key.", "ok");
});

for (const b of document.querySelectorAll("[data-copy]")) {
  b.addEventListener("click", async () => {
    const text = $(b.getAttribute("data-copy")).textContent;
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; }
    catch { b.textContent = "Select and copy"; }
    setTimeout(() => { b.textContent = "Copy"; }, 2000);
  });
}

async function init() {
  const frag = location.hash.slice(1);
  if (frag.includes("c=") || frag.includes("k=")) {
    // Wipe the secrets from the address bar and history before anything else.
    history.replaceState(null, "", location.pathname + location.search);
    if (await pairFrom(frag)) return;
    const err = parsePairing(frag).error;
    showUnpaired();
    $("ph-pair-err").textContent = err;
    return;
  }
  const p = loadPair();
  if (p) await showPaired(p);
  else showUnpaired();
}

init();
