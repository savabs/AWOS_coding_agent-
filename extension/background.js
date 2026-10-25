/* AWOS Replay service worker: recording sessions, the replay driver, the
 * approval gate and result verification.
 *
 * Storage (chrome.storage.local only; nothing leaves the machine except the
 * optional loopback model call in lib/model.js):
 *   recordings  {id: {id, name, createdAt, startUrl, steps[], expect:{urlPattern, texts[]}}}
 *   runs        last 20 finished runs (verdict, log)
 *   settings    {endpoint, model, enabled, timeoutMs, findTimeoutMs}
 * Live state (chrome.storage.session, survives worker restarts, not browser restarts):
 *   recState    the recording in progress {tabId, rec}
 *   run         the replay in progress, resumable from run.i
 *
 * Replay of one step: wait for the tab to load -> content 'locate' (attribute
 * scoring) -> if not confident, ask the local model to pick from the shortlist
 * or say NOT_FOUND -> approval gate for dangerous clicks -> content 'act'.
 * Any failure stops the run (FAILED) and is shown in the popup and on the page.
 * Approval: a gated click, or a goto whose URL path/query/hash matches the danger
 * vocabulary (AWOSSafety.urlDanger), pauses with a fresh nonce; only an 'approve' message
 * carrying that nonce *from an extension page* (the popup) resumes it. There is
 * no setting, timeout or code path that approves on its own.
 * Verify: after the last step, check the recorded final-URL pattern and the
 * expected texts -> VERIFIED (all checks pass), UNVERIFIED (finished but no
 * checks recorded), FAILED (a check failed or the run stopped). Then the local
 * verifier model (G7) reads the goal, the actions and the final page; it may only
 * downgrade VERIFIED to CHECK ("look at this one"), and its opinion is logged on
 * every run.
 */
importScripts('lib/safety.js', 'lib/model.js');
var S = self.AWOSSafety, M = self.AWOSModel;

// ---- storage helpers --------------------------------------------------------
async function getLocal(key, dflt) { var o = await chrome.storage.local.get(key); return o[key] === undefined ? dflt : o[key]; }
async function setLocal(key, val) { var o = {}; o[key] = val; await chrome.storage.local.set(o); }
async function getSess(key) { var o = await chrome.storage.session.get(key); return o[key] || null; }
async function setSess(key, val) { var o = {}; o[key] = val; if (val === null) await chrome.storage.session.remove(key); else await chrome.storage.session.set(o); }
async function settings() { return Object.assign({}, M.DEFAULTS, await getLocal('settings', {})); }
function uid(p) { return p + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8); }
function nonce() { var a = new Uint32Array(2); crypto.getRandomValues(a); return a[0].toString(36) + a[1].toString(36); }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

// ---- tab helpers ------------------------------------------------------------
async function waitForLoad(tabId, timeoutMs) {
  var end = Date.now() + (timeoutMs || 15000);
  while (Date.now() < end) {
    var t = await chrome.tabs.get(tabId).catch(function () { return null; });
    if (!t) throw new Error('tab closed');
    if (t.status === 'complete') return t;
    await sleep(100);
  }
  throw new Error('page did not finish loading');
}
async function sendTab(tabId, msg) { return chrome.tabs.sendMessage(tabId, msg); }
/** ping the content script until it answers (optionally on a given URL). */
async function ensureContent(tabId, wantUrl, timeoutMs) {
  var end = Date.now() + (timeoutMs || 8000), last = '';
  while (Date.now() < end) {
    try {
      var r = await sendTab(tabId, { type: 'ping' });
      if (r && r.ok && (!wantUrl || sameUrl(r.url, wantUrl))) return r;
      last = r ? 'on ' + r.url : 'no reply';
    } catch (e) { last = String(e.message || e); }
    await sleep(150);
  }
  throw new Error('page script not ready (' + last + ')');
}
function stripHash(u) { return String(u || '').split('#')[0]; }
function sameUrl(a, b) { return stripHash(a) === stripHash(b); }
/** idempotent page calls survive the old page unloading under us. */
async function sendRetry(tabId, msg, tries) {
  var err;
  for (var k = 0; k < (tries || 3); k++) {
    try { await waitForLoad(tabId); await ensureContent(tabId); return await sendTab(tabId, msg); }
    catch (e) { err = e; await sleep(300); }
  }
  throw err;
}

// ---- recording --------------------------------------------------------------
async function startRecording(tabId, name) {
  var tab = await chrome.tabs.get(tabId);
  if (!/^https?:/.test(tab.url || '')) throw new Error('can only record http(s) pages');
  var rec = { id: uid('rec'), name: name || ('Recording ' + new Date().toLocaleString()), createdAt: Date.now(),
              startUrl: tab.url, steps: [{ action: 'goto', url: tab.url }], expect: { urlPattern: '', texts: [] } };
  await setSess('recState', { tabId: tabId, rec: rec, lastClickAt: 0 });
  await sendTab(tabId, { type: 'rec_state', on: true }).catch(function () {});
  return rec;
}

async function stopRecording() {
  var st = await getSess('recState');
  if (!st) throw new Error('not recording');
  var tab = await chrome.tabs.get(st.tabId).catch(function () { return null; });
  var rec = st.rec;
  if (tab && tab.url) { var u = new URL(tab.url); rec.expect.urlPattern = u.origin + u.pathname; }
  var all = await getLocal('recordings', {}); all[rec.id] = rec; await setLocal('recordings', all);
  await setSess('recState', null);
  if (tab) await sendTab(st.tabId, { type: 'rec_state', on: false }).catch(function () {});
  return rec;
}

var recQueue = Promise.resolve();       // serialise step appends (messages can interleave)
function onRecStep(tabId, msg) {
  recQueue = recQueue.then(async function () {
    var st = await getSess('recState');
    if (!st || st.tabId !== tabId) return;
    var steps = st.rec.steps, last = steps[steps.length - 1];
    if (msg.merge && last && last.action === 'fill' && last.mergeKey === msg.mergeKey) {
      last.value = msg.step.value; last.sensitive = msg.step.sensitive; last.loc = msg.step.loc;
    } else {
      var step = msg.step; if (step.action === 'fill') step.mergeKey = msg.mergeKey;
      steps.push(step);
    }
    if (msg.step.action === 'click') st.lastClickAt = Date.now();
    await setSess('recState', st);
  }).catch(function (e) { console.warn('rec_step', e); });
  return recQueue;
}

chrome.tabs.onUpdated.addListener(function (tabId, info) {
  if (!info.url) return;
  recQueue = recQueue.then(async function () {
    var st = await getSess('recState');
    if (!st || st.tabId !== tabId) return;
    var steps = st.rec.steps, last = steps[steps.length - 1];
    if (last && last.action === 'goto' && sameUrl(last.url, info.url)) return;
    if (Date.now() - (st.lastClickAt || 0) < 3000 && last && last.action === 'click') { last.navigates = true; }
    else steps.push({ action: 'goto', url: info.url });    // typed / bookmark navigation
    await setSess('recState', st);
  });
});

// ---- replay -----------------------------------------------------------------
function logRun(run, text) { run.log.push({ t: Date.now(), step: run.i, text: text }); }
async function saveRun(run) {
  await setSess('run', run);
  var badge = run.status === 'awaiting_approval' || run.status === 'needs_user' ? '!' : run.status === 'running' ? '>' : '';
  chrome.action.setBadgeText({ text: badge }).catch(function () {});
}

async function startRun(recId, tabId) {
  var cur = await getSess('run');
  if (cur && /^(running|awaiting_approval|needs_user)$/.test(cur.status)) throw new Error('a replay is already in progress');
  if (await getSess('recState')) throw new Error('stop the recording before running a replay');
  var rec = (await getLocal('recordings', {}))[recId];
  if (!rec) throw new Error('no such recording');
  var run = { id: uid('run'), recId: recId, recName: rec.name, tabId: tabId, i: 0, status: 'running', startedAt: Date.now(),
              pending: null, approval: null, userDone: {}, log: [], resolved: [], result: null };
  logRun(run, 'start "' + rec.name + '" (' + rec.steps.length + ' steps)');
  await saveRun(run);
  drive(run.id);
  return run;
}

var driving = null;           // run id currently inside drive(); one driver at a time
async function drive(runId) {
  if (driving === runId) return;
  driving = runId;
  try { await driveInner(runId); }
  catch (e) {
    var run = await getSess('run');
    if (run && run.id === runId && run.status === 'running') await fail(run, 'ERROR', String(e && e.message || e));
  } finally { if (driving === runId) driving = null; }
}

async function driveInner(runId) {
  var run = await getSess('run');
  if (!run || run.id !== runId || run.status !== 'running') return;
  var rec = (await getLocal('recordings', {}))[run.recId];
  var cfg = await settings();
  while (run.i < rec.steps.length) {
    var step = rec.steps[run.i], i = run.i;
    if (step.action === 'goto') {
      // a navigation can be the effect of an unrecorded click (div/span handlers set
      // location): /account/delete?confirm=1 replayed as a GET deletes. Gate it like a click.
      var gd = S.urlDanger(step.url);
      if (gd && !approved(run, i, step.url)) {
        return pause(run, 'awaiting_approval', { danger: gd, target: step.url,
          message: 'Step ' + (i + 1) + ' will open ' + step.url + ' (matches "' + gd + '"). Approve to continue.' });
      }
      logRun(run, 'goto ' + step.url);
      await chrome.tabs.update(run.tabId, { url: step.url });
      await sleep(150); await waitForLoad(run.tabId); await ensureContent(run.tabId, step.url);
      run.i++; run.approval = null; await saveRun(run); continue;
    }
    await waitForLoad(run.tabId); await ensureContent(run.tabId);
    if (step.action === 'fill' && S.isPlaceholder(step.value)) {
      if (run.userDone[i]) { logRun(run, 'user typed ' + step.value + ' themselves'); run.i++; await saveRun(run); continue; }
      return pause(run, 'needs_user', { message: 'Type the ' + (step.sensitive || 'secret') + ' for "' + (step.loc.name || step.loc.label || 'this field')
        + '" yourself, then press Continue. AWOS never stores it.' });
    }
    var loc = await sendRetry(run.tabId, { type: 'locate', step: step, timeoutMs: cfg.findTimeoutMs });
    var index = -1, how = '';
    if (loc.status === 'found') { index = loc.index; how = 'score ' + loc.score + ' (margin ' + loc.margin + ')'; }
    else if (!loc.shortlist.length) {
      return fail(run, 'NOT_FOUND', 'step ' + (i + 1) + ': nothing on the page resembles "' + (step.loc.name || step.loc.css) + '"');
    } else {
      logRun(run, 'scoring not confident (' + loc.status + ', best ' + loc.score + '); asking local model over ' + loc.shortlist.length + ' candidates');
      var t0 = Date.now(), ans = await M.ask(cfg, step, loc.shortlist);
      (run.modelCalls = run.modelCalls || []).push({ step: i, status: loc.status, best: loc.score, ms: Date.now() - t0,
        target: { role: step.loc.role, name: step.loc.name || '' },
        shortlist: loc.shortlist.slice(0, 5).map(function (c) { return { role: c.desc.role, name: c.desc.name, score: c.score }; }),
        answer: ans.raw || '', outcome: ans.error ? 'error' : ans.notFound ? 'NOT_FOUND' : 'PICK ' + (ans.choice + 1) });
      if (ans.error) return fail(run, 'NOT_FOUND', 'step ' + (i + 1) + ': no confident match and ' + ans.error);
      if (ans.notFound) return fail(run, 'NOT_FOUND', 'step ' + (i + 1) + ': local model says the control "' + (step.loc.name || '') + '" is gone');
      index = loc.shortlist[ans.choice].index; how = 'model pick #' + (ans.choice + 1) + ' (score ' + loc.shortlist[ans.choice].score + ')';
    }
    var cand = (loc.shortlist.find(function (c) { return c.index === index; }) || {}).desc || {};
    // the same expression names the target when pausing and when checking the approval,
    // so an approved target whose live name is empty (icon-only button) does not re-pause forever.
    var target = cand.name || step.loc.name || step.loc.label || step.loc.css || '';
    var ok = approved(run, i, target);
    if (step.action === 'click') {
      var danger = loc.dangers[index] || step.danger || S.dangerMatch(cand.name || '');
      if (danger && !ok) {
        return pause(run, 'awaiting_approval', { danger: danger, target: target,
          message: 'Step ' + (i + 1) + ' will click "' + target + '" (matches "' + danger + '"). Approve to continue.' });
      }
    }
    var r = await sendTab(run.tabId, { type: 'act', step: step, index: index, approved: ok });
    if (!r || !r.ok) {
      if (r && r.needsApproval) return pause(run, 'awaiting_approval', { danger: r.danger, target: target,
        message: 'Step ' + (i + 1) + ' will click "' + target + '" (matches "' + r.danger + '"). Approve to continue.' });
      return fail(run, 'ERROR', 'step ' + (i + 1) + ': ' + (r ? r.error : 'no reply'));
    }
    run.resolved.push({ step: i, how: how, name: cand.name || '' });
    // the verifier's action list, in the agent's own syntax (verifier_data.action_list)
    var q = function (v) { return JSON.stringify(String(v == null ? '' : v)); };
    var an = step.action === 'fill' ? 'fill(' + (index + 1) + ', ' + (step.sensitive || S.isPlaceholder(step.value) ? '{secret}' : q(step.value)) + ')'
      : step.action === 'select' ? 'select(' + (index + 1) + ', ' + q(step.optionText || step.value) + ')'
      : 'click(' + (index + 1) + ')';
    (run.actions = run.actions || []).push({ action: an, control: (cand.role || step.loc.role || '') + ' ' + q(cand.name || step.loc.name || '') });
    logRun(run, step.action + ' "' + (cand.name || '') + '" via ' + how);
    run.i++; run.approval = null; await saveRun(run);
    if (step.action === 'click') { await sleep(400); await waitForLoad(run.tabId); }
  }
  await finish(run, rec);
}

/** the user approved exactly this step and this target (a name, or a URL for a goto). */
function approved(run, i, target) { return !!(run.approval && run.approval.step === i && run.approval.name === (target || '')); }

async function pause(run, status, pending) {
  run.status = status; run.pending = Object.assign({ nonce: nonce(), step: run.i }, pending);
  logRun(run, 'paused: ' + pending.message);
  await saveRun(run);
  await sendTab(run.tabId, { type: 'banner', text: pending.message + ' (open the AWOS popup)' }).catch(function () {});
}

async function fail(run, code, reason) {
  run.status = 'failed';
  run.result = { verdict: 'FAILED', code: code, reason: reason, checks: [] };
  logRun(run, 'FAILED ' + code + ': ' + reason);
  await archive(run);
  await sendTab(run.tabId, { type: 'banner', text: 'stopped. ' + reason }).catch(function () {});
}

function urlMatches(pattern, url) {
  if (!pattern) return true;
  if (pattern.indexOf('*') >= 0) {
    var re = new RegExp('^' + pattern.split('*').map(function (p) { return p.replace(/[.+?^${}()|[\]\\]/g, '\\$&'); }).join('.*') + '$');
    return re.test(url);
  }
  try { var u = new URL(url); return u.origin + u.pathname === pattern; } catch (e) { return false; }
}

async function finish(run, rec) {
  await sleep(200); await waitForLoad(run.tabId);
  var tab = await chrome.tabs.get(run.tabId), checks = [];
  var ex = rec.expect || {};
  if (ex.urlPattern) checks.push({ kind: 'url', expected: ex.urlPattern, actual: tab.url, ok: urlMatches(ex.urlPattern, tab.url) });
  var texts = (ex.texts || []).filter(Boolean);
  if (texts.length) {
    var c = await sendRetry(run.tabId, { type: 'check', texts: texts });
    texts.forEach(function (t, k) { checks.push({ kind: 'text', expected: t, ok: !!c.present[k] }); });
  }
  var verdict = !checks.length ? 'UNVERIFIED' : checks.every(function (x) { return x.ok; }) ? 'VERIFIED' : 'FAILED';
  var reason = verdict === 'UNVERIFIED' ? 'no expectations recorded' : '';
  // second opinion from the local verifier (G7). It may only DOWNGRADE: a VERIFIED it disagrees with
  // becomes CHECK ("look at this one"); it never turns UNVERIFIED / FAILED into a pass. Its opinion is
  // always recorded (shadow mode for the other verdicts), so exported logs show what it would have caught.
  var cfg = await settings();
  if (cfg.enabled && cfg.verify !== false) {
    var obs = await sendRetry(run.tabId, { type: 'observe' }).catch(function () { return null; });
    var op = obs ? await M.askVerify(cfg, M.buildVerifyMessages(rec.goal || rec.name, (run.actions || []).concat([{ action: 'done("Done.")' }]),
                                                          M.outline(obs), 'done("Done.")'))
                 : { error: 'could not read the final page' };
    run.verifier = op;
    logRun(run, op.error ? 'verifier: ' + op.error : 'verifier says ' + op.label + ': ' + op.reason + ' (' + op.ms + ' ms)');
    if (verdict === 'VERIFIED' && !op.error && op.label !== 'VERIFIED') {
      verdict = 'CHECK';
      reason = 'checks passed, but the local verifier says ' + op.label + ': ' + op.reason;
    }
  }
  run.status = verdict === 'FAILED' ? 'failed' : 'done';
  run.result = { verdict: verdict, code: verdict === 'FAILED' ? 'CHECK_FAILED' : '', checks: checks, reason: reason };
  logRun(run, verdict + ' (' + checks.filter(function (x) { return x.ok; }).length + '/' + checks.length + ' checks)');
  await archive(run);
  await sendTab(run.tabId, { type: 'banner', text: '' }).catch(function () {});
}

async function archive(run) {
  run.pending = null; run.endedAt = Date.now();
  await saveRun(run);
  var runs = await getLocal('runs', []); runs.unshift(run); await setLocal('runs', runs.slice(0, 20));
}

/** user decisions; only accepted from extension pages (popup/options), never from a web page. */
async function decide(kind, runId, n) {
  var run = await getSess('run');
  if (!run || run.id !== runId || !run.pending || run.pending.nonce !== n) throw new Error('stale or unknown request');
  if (kind === 'approve') {
    if (run.status !== 'awaiting_approval') throw new Error('nothing to approve');
    run.approval = { step: run.pending.step, name: run.pending.target || '' };
    logRun(run, 'user approved "' + run.pending.target + '"');
  } else if (kind === 'continue') {
    if (run.status !== 'needs_user') throw new Error('nothing to continue');
    run.userDone[run.pending.step] = true;
  } else {                                    // reject / abort
    run.pending = null;
    return fail(run, 'REJECTED', 'user stopped the run at step ' + (run.i + 1));
  }
  run.status = 'running'; run.pending = null;
  await saveRun(run);
  await sendTab(run.tabId, { type: 'banner', text: '' }).catch(function () {});
  drive(run.id);
}

// ---- shareable run log (redacted) ------------------------------------------------
// What the owner can send back to improve AWOS: per run the verdict, how each step was found
// (score / model pick), every model call (shortlist names + answer). Never typed values (only their
// length), never URL queries or fragments, never anything from a sensitive field.
function redactUrl(u) { try { var x = new URL(u); return x.origin + x.pathname; } catch (e) { return ''; } }
function redactText(s) { return String(s || '').replace(/https?:\/\/[^\s"')]+/g, function (u) { return redactUrl(u); }); }
async function exportLog() {
  var recs = await getLocal('recordings', {}), runs = await getLocal('runs', []), cfg = await settings();
  var recOut = Object.values(recs).map(function (r) {
    return { id: r.id, name: r.name, goal: r.goal || '', start: redactUrl(r.startUrl), createdAt: r.createdAt,
      expect: { url: !!(r.expect && r.expect.urlPattern), texts: ((r.expect && r.expect.texts) || []).length },
      steps: r.steps.map(function (s) {
        if (s.action === 'goto') return { action: 'goto', url: redactUrl(s.url) };
        var l = s.loc || {};
        return { action: s.action, role: l.role, name: l.name, context: l.context || '', danger: s.danger || '',
                 sensitive: s.sensitive || '', valueLen: s.value != null ? String(s.value).length : null, navigates: !!s.navigates };
      }) };
  });
  var runOut = runs.map(function (r) {
    return { recId: r.recId, recName: r.recName, status: r.status, startedAt: r.startedAt, endedAt: r.endedAt,
      verdict: r.result && r.result.verdict, code: r.result && r.result.code, reason: redactText(r.result && r.result.reason),
      checks: ((r.result && r.result.checks) || []).map(function (c) { return { kind: c.kind, ok: c.ok }; }),
      resolved: r.resolved, modelCalls: r.modelCalls || [],
      verifier: r.verifier ? { label: r.verifier.label || '', reason: redactText(r.verifier.reason || ''),
                               error: r.verifier.error || '', ms: r.verifier.ms || 0 } : null,
      log: r.log.map(function (l) { return { t: l.t, step: l.step, text: redactText(l.text) }; }) };
  });
  return { kind: 'awos-replay-runlog/1', exportedAt: Date.now(), version: chrome.runtime.getManifest().version,
           model: { endpoint: redactUrl(cfg.endpoint), name: cfg.model, enabled: cfg.enabled },
           recordings: recOut, runs: runOut };
}

// ---- messages ---------------------------------------------------------------
function fromExtensionPage(sender) { return sender.id === chrome.runtime.id && String(sender.url || '').indexOf(chrome.runtime.getURL('')) === 0; }

var HANDLERS = {
  hello: async function (msg, sender) {
    var st = await getSess('recState');
    return { recording: !!(st && sender.tab && st.tabId === sender.tab.id) };
  },
  rec_step: async function (msg, sender) { if (sender.tab) await onRecStep(sender.tab.id, msg); return { ok: true }; },
  get_state: async function () {
    return { recState: await getSess('recState'), run: await getSess('run'), recordings: await getLocal('recordings', {}),
             runs: await getLocal('runs', []), settings: await settings() };
  },
  start_recording: async function (msg) { return startRecording(msg.tabId, msg.name); },
  stop_recording: async function () { return stopRecording(); },
  save_recording: async function (msg) {
    var all = await getLocal('recordings', {}), rec = all[msg.id];
    if (!rec) throw new Error('no such recording');
    if (typeof msg.name === 'string') rec.name = msg.name;
    if (typeof msg.goal === 'string') rec.goal = msg.goal.slice(0, 300);    // what the chore must achieve (the verifier reads it)
    if (msg.expect) rec.expect = { urlPattern: String(msg.expect.urlPattern || ''), texts: (msg.expect.texts || []).map(String) };
    await setLocal('recordings', all); return rec;
  },
  delete_recording: async function (msg) { var all = await getLocal('recordings', {}); delete all[msg.id]; await setLocal('recordings', all); return { ok: true }; },
  run: async function (msg) { return startRun(msg.recId, msg.tabId); },
  approve: async function (msg) { await decide('approve', msg.runId, msg.nonce); return { ok: true }; },
  continue: async function (msg) { await decide('continue', msg.runId, msg.nonce); return { ok: true }; },
  reject: async function (msg) { await decide('reject', msg.runId, msg.nonce); return { ok: true }; },
  save_settings: async function (msg) { await setLocal('settings', msg.settings || {}); return settings(); },
  export_log: async function () { return exportLog(); },
};
var PAGE_ONLY = { hello: 1, rec_step: 1 };   // the only messages a content script may send

chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  var h = msg && HANDLERS[msg.type];
  if (!h) return false;
  if (!PAGE_ONLY[msg.type] && !fromExtensionPage(sender)) { reply({ error: 'not allowed from a web page' }); return false; }
  h(msg, sender).then(function (r) { reply(r === undefined ? { ok: true } : r); },
                     function (e) { reply({ error: String(e && e.message || e) }); });
  return true;
});

// a worker restart mid-run: a paused run stays paused (resumes on the user's click).
// A run that was actively driving is NOT resumed: the last step may have half-run
// (a click sent but not yet recorded), and repeating it could pay or send twice.
(async function onWorkerStart() {
  var run = await getSess('run');
  if (run && run.status === 'running' && driving !== run.id) {
    await fail(run, 'INTERRUPTED', 'the extension restarted during step ' + (run.i + 1) + '; check the page, then run again');
  }
})();
