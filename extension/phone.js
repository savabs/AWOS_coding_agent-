/* AWOS Replay phone link, service-worker side (v0.8, docs/specs/phone_link.md). Loaded by background.js with
 * importScripts after everything else; uses its helpers (getLocal, getSess, logRun, saveRun, decide, sendTab, ...).
 *
 * Pairing (Options -> Phone): a channel, an AES-GCM key, an SMS token and an ntfy topic are made here and kept in
 * chrome.storage.local `phone` {channel, key, smsToken, ntfyTopic, base, ntfyBase, pairedAt, register:{ok, status,
 * at}}. The pair link (QR in Options) carries them to the phone page in the URL fragment, which no server sees. The
 * relay only ever gets the channel, sha256(smsToken), and AES-GCM envelopes it cannot read.
 *
 * When paired, a replay run that pauses for a human (one-time code, approval, bot check, login) sends ONE encrypted
 * `need` to the phone and one generic ntfy push ("AWOS needs you: open the AWOS phone page": never a code, a portal
 * name or page text). While the run waits, the relay is polled every phonePollMs (5 s): `to-mac` for the phone's
 * answer, and `sms` (only while a code is needed) for a forwarded SMS. A code (typed on the phone, or read from an SMS
 * that arrived after the need) is typed into the page's one-time-code field and the form is submitted the way a person
 * would (Enter / the form's default button); then the run goes on exactly as after the user's Continue. Approve yes /
 * no = the popup's Approve / Stop. Bot checks and logins stay with the human at the Mac: the phone only shows them.
 * When the pause ends by any route, `resolved` is sent; at the end of every run / collection a `status`.
 * Codes are never logged, stored or exported: they live in local variables for the few hundred ms it takes to type them.
 * A relay answering 503 (not configured) or not reachable changes nothing on the Mac: the normal handoff stays.
 * Session state: phoneNeed {req, runId, nonce, kind, at, envId}, phoneRelay {ok, status, error, at, what}.
 */
var PH = self.AWOSPhone;
var PHONE_DEFAULT_BASE = 'https://awos.tirramind.com', NTFY_DEFAULT = 'https://ntfy.sh';
var PHONE_POLL_ALARM = 'awos-phone-poll';
var PHONE_NEED_KIND = { otp: 'code', login: 'login', captcha: 'captcha' };

async function phoneCfg() { var p = await getLocal('phone', null); return p && p.channel && p.key ? p : null; }
async function phoneBase() { var p = await getLocal('phone', null); return String((p && p.base) || (await getLocal('phoneBase', '')) || PHONE_DEFAULT_BASE).replace(/\/+$/, ''); }
function relayUrl(p, tail) { return String(p.base || PHONE_DEFAULT_BASE).replace(/\/+$/, '') + '/api/relay/' + encodeURIComponent(p.channel) + tail; }

/** one relay call; never throws: {ok, status, data, error}. 503 = relay not configured. */
async function relayFetch(p, method, tail, body) {
  var out = { ok: false, status: 0, data: null, error: '' };
  try {
    var r = await fetch(relayUrl(p, tail), { method: method, headers: body ? { 'Content-Type': 'application/json' } : {},
                                             body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
    out.status = r.status; out.ok = r.ok;
    if (r.status === 429 || r.status >= 500) out.retryMs = Math.min(300, Math.max(1, parseInt(r.headers.get('Retry-After'), 10) || 30)) * 1000;
    var txt = await r.text();
    try { out.data = txt ? JSON.parse(txt) : null; } catch (e) { out.data = null; }
    if (!r.ok) out.error = r.status === 503 ? 'the phone relay is not configured (503)' : r.status === 409 ? 'this phone channel belongs to another pairing (409): Unpair and pair again'
      : r.status === 429 ? 'the relay asked AWOS to slow down (429)' : 'relay answered ' + r.status;
  } catch (e) { out.error = 'relay not reachable (' + String(e && e.message || e) + ')'; }
  if (method !== 'DELETE') await setSess('phoneRelay', { ok: out.ok, status: out.status, error: out.error, at: Date.now(), what: method + ' ' + tail.split('?')[0] });
  return out;
}

/** the generic push. Body, title and click link are fixed text: nothing from the run goes to ntfy. */
async function phonePush(p) {
  try {
    var r = await fetch(String(p.ntfyBase || NTFY_DEFAULT).replace(/\/+$/, '') + '/' + encodeURIComponent(p.ntfyTopic), {
      method: 'POST', body: 'AWOS needs you: open the AWOS phone page',
      headers: { 'Title': 'AWOS Replay', 'Click': String(p.base || PHONE_DEFAULT_BASE).replace(/\/+$/, '') + '/phone/', 'Tags': 'bell' } });
    return r.ok;
  } catch (e) { return false; }
}

// ---- pairing --------------------------------------------------------------------------------------------------
async function phonePair(msg) {
  var base = String((msg && msg.base) || (await getLocal('phoneBase', '')) || PHONE_DEFAULT_BASE).trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s/]+/.test(base)) throw new Error('relay address must be http(s)://host');
  var p = Object.assign(PH.newPairing(), { base: base, ntfyBase: String((msg && msg.ntfyBase) || NTFY_DEFAULT).replace(/\/+$/, ''), pairedAt: Date.now() });
  var reg = await relayFetch(p, 'POST', '/register', { tokenHash: await PH.sha256Hex(p.smsToken) });
  p.register = { ok: reg.ok, status: reg.status, error: reg.error, at: Date.now() };
  await setLocal('phone', p);
  return phoneState();
}
async function phoneUnpair() {
  await chrome.storage.local.remove('phone');
  await setSess('phoneNeed', null); await setSess('phoneRelay', null);
  chrome.alarms.clear(PHONE_POLL_ALARM).catch(function () {});
  return { paired: false };
}
/** what Options / the popup show. The link (it carries the key) only goes to extension pages. */
async function phoneState() {
  var p = await getLocal('phone', null);
  var out = { paired: !!(p && p.channel), base: await phoneBase(), relay: await getSess('phoneRelay'), need: null };
  if (out.paired) {
    out.link = PH.pairLink(p.base, p); out.pairedAt = p.pairedAt; out.register = p.register || null; out.ntfyTopic = p.ntfyTopic;
    var n = await getSess('phoneNeed'); if (n) out.need = { kind: n.kind, at: n.at, seen: !!n.seen };
  }
  return out;
}

/** the popup's line: paired?, the relay's last answer, an open need (no link, no key). */
async function phoneSummary() {
  var p = await getLocal('phone', null);
  if (!(p && p.channel)) return { paired: false };
  var n = await getSess('phoneNeed');
  return { paired: true, relay: await getSess('phoneRelay'), register: p.register || null, need: n ? { kind: n.kind, at: n.at, seen: !!n.seen } : null };
}

// ---- needs ------------------------------------------------------------------------------------------------------
/** called by pause(): a human is needed. A code / approval / check / login handoff goes to the phone. */
async function phoneOnPause(run) {
  var p = await phoneCfg(), pd = run.pending || {};
  if (!p) return;
  var kind = run.status === 'awaiting_approval' ? 'approve' : PHONE_NEED_KIND[pd.handoff] || '';
  if (!kind) return;
  var old = await getSess('phoneNeed');
  if (old && old.runId === run.id && old.nonce === pd.nonce) return;
  if (old) await phoneResolve(old);
  var need = { req: PH.b64u(crypto.getRandomValues(new Uint8Array(12))), runId: run.id, nonce: pd.nonce, kind: kind, at: Date.now() };
  var env = await PH.seal(p.key, 'to-phone', { type: 'need', req: need.req, kind: kind, portal: run.recName || '', message: pd.message || '', at: need.at });
  var r = await relayFetch(p, 'POST', '/to-phone', env);
  if (!r.ok) {
    logRun(run, 'phone: could not send this to your phone (' + r.error + '); answer it here on the Mac as usual');
    await saveRun(run);
    return;
  }
  need.envId = env.id;
  // relay clock (the relay stamps `at` itself): SMS / replies are compared with this, not with the Mac's clock
  need.relayAt = (r.data && +r.data.at) || Date.now();
  need.macSince = need.relayAt - 1000;
  await setSess('phoneNeed', need);
  var cfg = await settings(), last = (await getSess('phonePushAt')) || 0;
  var pushed = Date.now() - last >= Math.max(0, cfg.phonePushGapMs == null ? 60000 : +cfg.phonePushGapMs) && await phonePush(p);
  if (pushed) await setSess('phonePushAt', Date.now());
  logRun(run, 'phone: sent this ' + ({ code: 'code request', approve: 'approval request', captcha: 'bot check', login: 'login request' })[kind]
              + ' to your phone (end-to-end encrypted)' + (pushed ? ' and a push' : '')
              + (kind === 'code' ? '; waiting for a code from the phone or a forwarded SMS' : kind === 'approve' ? '; you can approve or stop it there' : '; it needs you at the Mac'));
  await saveRun(run);
  chrome.alarms.create(PHONE_POLL_ALARM, { periodInMinutes: 0.5 }).catch(function () {});
  phoneWatch();
}

/** the pause is over (any route): tell the phone, forget the need. */
var phoneResolveChain = Promise.resolve();
function phoneResolve(need) { var r = phoneResolveChain.then(function () { return phoneResolveInner(need); }); phoneResolveChain = r.catch(function () {}); return r; }
async function phoneResolveInner(need) {
  var cur = await getSess('phoneNeed');
  if (!need || !cur || cur.req !== need.req) return;              // already resolved (several routes can notice it)
  var p = await phoneCfg();
  await setSess('phoneNeed', null);
  chrome.alarms.clear(PHONE_POLL_ALARM).catch(function () {});
  if (!p || !need) return;
  await relayFetch(p, 'POST', '/to-phone', await PH.seal(p.key, 'to-phone', { type: 'resolved', req: need.req }));
}
/** called after a decision / when a run ended: is the open need still open? */
async function phoneCheckResolved() {
  var need = await getSess('phoneNeed');
  if (!need) return;
  var run = await getSess('run');
  if (!run || run.id !== need.runId || !run.pending || run.pending.nonce !== need.nonce || !/^(needs_user|awaiting_approval)$/.test(run.status)) await phoneResolve(need);
}

var phoneWatching = false;
/** poll while a need is open (every phonePollMs, 5 s); the alarm is the backup wake-up after a worker restart. */
async function phoneWatch() {
  if (phoneWatching) return;
  phoneWatching = true;
  try {
    for (;;) {
      var need = await getSess('phoneNeed');
      if (!need) break;
      await phoneCheckResolved();
      if (!(await getSess('phoneNeed'))) break;
      var acted = await phonePollOnce(need);
      if (acted === true) continue;
      var cfg = await settings();
      // a 429 / 5xx: back off (Retry-After, else 30 s) and poll again; a waiting run never gives up on its own
      await sleep(Math.max(100, cfg.phonePollMs == null ? 5000 : +cfg.phonePollMs, +acted || 0));
    }
  } catch (e) { console.warn('phone watch', e); }
  finally { phoneWatching = false; }
}

/** one look at the relay for the open need. -> true when it acted on an answer, a number (ms) to back off, else false.
 *  Every to-mac message read is deleted once handled (a stale one for an older request, or one that does not decrypt,
 *  is just deleted), and every SMS read is deleted; only an SMS stamped at or after the need (minus a short grace,
 *  phoneSmsGraceMs, 30 s: the portal often texts the code while the page that asks for it is still loading) is used. */
async function phonePollOnce(need) {
  var p = await phoneCfg();
  if (!p) { await setSess('phoneNeed', null); return false; }
  var r = await relayFetch(p, 'GET', '/to-mac?since=' + (need.macSince != null ? need.macSince : need.at - 60000));
  if (r.retryMs) return r.retryMs;
  var msgs = (r.ok && r.data && r.data.messages) || [];
  var top = Math.max.apply(null, [need.macSince || 0].concat(msgs.map(function (m) { return +m.at || 0; })));
  if (top > (need.macSince || 0)) { var n0 = await getSess('phoneNeed'); if (n0 && n0.req === need.req) { n0.macSince = top; await setSess('phoneNeed', n0); } }
  for (var k = 0; k < msgs.length; k++) {
    var m = msgs[k], body = null;
    try { body = await PH.open(p.key, 'to-mac', m); } catch (e) { body = null; }
    await relayFetch(p, 'DELETE', '/to-mac/' + encodeURIComponent(m.id));
    if (!body || body.req !== need.req) continue;                  // unreadable, or for a request already over
    if (body.type === 'seen') { await phoneSeen(need); continue; }
    if (body.type === 'code' && need.kind === 'code') { var ok = await phoneUseCode(need, String(body.code || ''), 'your phone'); body.code = ''; if (ok) return true; continue; }
    if (body.type === 'approve' && need.kind === 'approve') { await phoneApprove(need, body.yes === true); return true; }
  }
  if (need.kind === 'code') {
    var cfg = await settings(), since = (need.relayAt || need.at) - Math.max(0, cfg.phoneSmsGraceMs == null ? 30000 : +cfg.phoneSmsGraceMs);
    var s = await relayFetch(p, 'GET', '/sms?since=' + since);
    if (s.retryMs) return s.retryMs;
    var list = (s.ok && s.data && s.data.messages) || [];
    var hit = PH.smsCode(list, since);                    // an SMS older than the need is never used
    for (var j = 0; j < list.length; j++) await relayFetch(p, 'DELETE', '/sms/' + encodeURIComponent(list[j].id));   // read: gone
    if (hit) {
      var used = await phoneUseCode(need, hit.code, 'a forwarded SMS');
      hit.code = '';
      if (used) return true;
    }
  }
  return false;
}

async function phoneSeen(need) {
  var n = await getSess('phoneNeed');
  if (!n || n.req !== need.req || n.seen) return;
  n.seen = true; await setSess('phoneNeed', n);
  var run = await getSess('run');
  if (run && run.id === need.runId) { logRun(run, 'phone: you opened this on your phone'); await saveRun(run); }
}

/** the open need's run, still paused on that very request, or null. */
async function needRun(need) {
  var run = await getSess('run');
  return run && run.id === need.runId && run.pending && run.pending.nonce === need.nonce && /^(needs_user|awaiting_approval)$/.test(run.status) ? run : null;
}

/** type the code into the page's one-time-code field and submit it, then go on as after Continue. The code is never
 *  logged or stored: the log says where it came from, not what it was. */
async function phoneUseCode(need, code, from) {
  var run = await needRun(need);
  if (!run || run.pending.handoff !== 'otp') return false;
  if (!PH.validCode(code)) { logRun(run, 'phone: ignored a code from ' + from + ' that is not 4-8 letters or digits'); await saveRun(run); return false; }
  var urlBefore = ((await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url) || '';
  var r = await sendTab(run.tabId, { type: 'phone_code', code: code }).catch(function (e) { return { ok: false, error: String(e && e.message || e) }; });
  code = '';
  if (!r || !r.ok) { logRun(run, 'phone: could not type the code from ' + from + ' (' + ((r && r.error) || 'no reply') + '); type it here yourself'); await saveRun(run); return false; }
  logRun(run, 'phone: typed the code from ' + from + ' into "' + (r.field || 'the code field') + '" and submitted it (the code is not logged)');
  await saveRun(run);
  try { await settleNavigation(run.tabId, urlBefore, 5000); } catch (e) { /* the run's own checks follow */ }
  await decide('continue', run.id, run.pending.nonce);
  return true;
}

async function phoneApprove(need, yes) {
  var run = await needRun(need);
  if (!run || run.status !== 'awaiting_approval') return;
  logRun(run, 'phone: you ' + (yes ? 'approved' : 'declined') + ' this on your phone');
  await saveRun(run);
  await decide(yes ? 'approve' : 'reject', run.id, run.pending.nonce);
}

// ---- status (end of every run / collection) -------------------------------------------------------------------
/** items [{portal, state, detail}] to the phone. `push`: also the generic ntfy push (something needs the user). */
async function phoneStatus(summary, items, push) {
  var p = await phoneCfg();
  if (!p) return;
  var env = await PH.seal(p.key, 'to-phone', { type: 'status', summary: summary, items: items, at: Date.now() });
  var r = await relayFetch(p, 'POST', '/to-phone', env);
  if (r.ok && push) await phonePush(p);
}
async function phoneRunStatus(run) {
  if (run.collection) return;
  var v = (run.result && run.result.verdict) || 'FAILED', slow = run.result && run.result.slow;
  var detail = (run.result && run.result.reason) || '';                 // already says "slower than usual" when it was
  await phoneStatus(run.recName + ': ' + v + (slow ? ' (slower than usual)' : ''), [{ portal: run.recName, state: v, detail: detail }],
                    !/^(VERIFIED|UNVERIFIED)$/.test(v));
}
async function phoneCollStatus(c, col) {
  var res = await periodResults(c.collId, c.period), recs = await getLocal('recordings', {}), runs = await getLocal('runs', []);
  var items = ((col && col.recIds) || []).map(function (id) {
    var r = res[id] || {}, run = r.runId ? runs.find(function (x) { return x.id === r.runId; }) : null;
    var slow = run && run.result && run.result.slow;
    return { portal: (recs[id] && recs[id].name) || r.recName || 'a portal', state: r.status || 'not run',
             detail: [r.file || r.reason || '', slow ? slow.text : ''].filter(Boolean).join('; ') };
  });
  var bad = items.some(function (it) { return it.state !== 'got'; });
  await phoneStatus(c.name + ' (' + c.period + '): ' + (c.summary || ''), items, bad);
}
/** the batched login notice (pre-flight / run start): the same moment as the Mac notification, one push. */
async function phoneLoginNotice(colName, names, count) {
  await phoneStatus(colName + ': ' + count + ' portal' + (count === 1 ? ' needs' : 's need') + ' a login',
                    names.map(function (n) { return { portal: n, state: 'login needed', detail: 'log in on the Mac before the run' }; }), true);
}

chrome.alarms.onAlarm.addListener(function (a) { if (a.name === PHONE_POLL_ALARM) phoneWatch(); });
getSess('phoneNeed').then(function (n) { if (n) phoneWatch(); }).catch(function () {});     // a worker restart mid-wait

Object.assign(HANDLERS, {
  phone_state: async function () { return phoneState(); },
  phone_pair: async function (msg) { return phonePair(msg); },
  phone_unpair: async function () { return phoneUnpair(); },
  phone_set_base: async function (msg) {
    var b = String(msg.base || '').trim().replace(/\/+$/, '');
    if (b && !/^https?:\/\/[^\s/]+/.test(b)) throw new Error('relay address must be http(s)://host');
    await setLocal('phoneBase', b);
    return phoneState();
  },
});
