/* AWOS Replay service worker: recording sessions, the replay driver, the
 * approval gate and result verification.
 *
 * Storage (chrome.storage.local only; nothing leaves the machine except the
 * optional loopback model call in lib/model.js, or the Claude call when the user opted in):
 *   recordings  {id: {id, name, createdAt, startUrl, steps[], expect:{urlPattern, texts[]},
 *                     goal, description, params[], expectDownload:{nameRegex}, schedule, lastParams, lastRun,
 *                     imported, site, approvedDomains[]}}
 *   runs        last 20 finished runs (verdict, log)
 *   collections {id: {id, name, recIds[], monthOffset, schedule, createdAt, last:{period, summary, at}}}  (v0.6)
 *   collResults {collId: {'YYYY-MM': {recId: {status: got|missing|needs_you|failed, file, reason, at, runId}}}}
 *   settings    {endpoint, model, enabled, timeoutMs, findTimeoutMs, brain, cloudConsent}
 *   claudeKey   the user's Anthropic API key (only if they chose the Claude brain); never exported
 * Live state (chrome.storage.session, survives worker restarts, not browser restarts):
 *   recState    the recording in progress {tabId, rec}
 *   run         the replay in progress, resumable from run.i; run.done{i} = steps completed in this run
 *   coll        the collection run in progress {collId, period, queue[], idx, current:{recId, tabId}, status, summary}
 *   pendingImport  a validated recipe waiting for the user's Import click in the popup
 *
 * v0.4: params ({{name}} values asked at run time), schedules (chrome.alarms; a scheduled run opens its own
 * tab, any human-needed step pauses + notifies), download checks (chrome.downloads), recipe share/import
 * (lib/recipe.js; imported recipes are untrusted: domain-locked to start_url's site, stricter gate vocabulary).
 * Permissions added in 0.4, each for one feature: alarms (run a recipe daily / weekly), downloads (read-only
 * chrome.downloads.search to verify expect_download; since 0.6 also .download for a file a click opened in a new tab),
 * notifications (a scheduled run that needs a human, or its verdict). No new host permissions: the Claude call
 * relies on Anthropic's CORS support, and the recipe bridge is a content script on the three /r/ origins only.
 *
 * v0.6 portal runs (docs/specs/portal_run.md), no new permissions:
 *  - handoff: before each step the page is classified (lib/portal.js pageKind). A login / session-expired page or a
 *    one-time-code field the recording did not have here (the step's recorded page is another one), or any bot
 *    check, pauses the run (needs_user, notification) and is never touched. Continue re-observes, goes back to the
 *    recorded page of the first step not yet done, and goes on from there; steps done (run.done) are not repeated,
 *    and a download step whose file already completed in this run (chrome.downloads, same name rule as the
 *    expect_download check) is skipped and logged. All of it lives in storage.session, so a worker restart during
 *    the pause loses nothing.
 *  - pop-ups: a target that is not found or sits under an overlay gets one unblock pass (close / not now / reject on
 *    cookie banners only, never accept / agree / danger words), then one retry; a pop-up with no allowed control
 *    pauses for the user. Every dismissal is logged.
 *  - new-tab files: a click that opens a tab (openerTabId = the run tab) on a PDF / CSV / ... is downloaded with
 *    chrome.downloads.download (the browser's cookies apply), the tab closed, and the file counts for
 *    expect_download. Same origin, or HTTPS on the same site, only; any other file asks first. The recorder marks
 *    such clicks (opensTab, download) so replay waits for the tab.
 *  - collections: recipes grouped into a monthly checklist ({{month}} / {{year}} in the download pattern), run one
 *    after another in their own tabs; results per period YYYY-MM (Got / Missing / Needs you / Failed), a re-run
 *    skips the portals already Got, a summary notification at the end, optional monthly schedule (chrome.alarms).
 *
 * v0.6.1 fewer handoffs (docs/specs/portal_run.md section 5), no new permissions:
 *  - keep-alive: per collection (on by default, every keepAliveDays = 3), each portal's start page is opened in a
 *    background tab, one at a time, classified logged in / login page, and closed; nothing is clicked or typed. A
 *    logout is notified once, early, and shows as "Login needed". Skipped while anything else runs.
 *  - pre-flight: the same check preflightHours (24) before a scheduled collection run and at the start of every
 *    collection run: ONE notification naming the logged-out portals, "Open the ones that need me" opens just those;
 *    the run does the ready portals first and picks the others up as soon as their login is seen.
 *  - on a login page: tick an unchecked "remember me / keep me signed in / trust this device" checkbox (Option
 *    rememberMe), and when Chrome autofilled both the username and the password (the :autofill state, the value is
 *    never read), click the sign-in button once (Option autofillSignin, experimental); else the normal handoff.
 *  portalStatus (chrome.storage.local), loginTabs / probeTab (chrome.storage.session).
 *
 * Replay of one step: wait for the tab to load -> content 'locate' (attribute
 * scoring) -> if not confident, ask the local model to pick from the shortlist
 * or say NOT_FOUND -> approval gate for dangerous clicks -> content 'act'.
 * Any failure stops the run (FAILED) and is shown in the popup and on the page.
 * Approval: a gated click, or a goto whose URL path/query/hash matches the danger
 * vocabulary (AWOSSafety.urlDanger), pauses with a fresh nonce; only an 'approve' message
 * carrying that nonce *from an extension page* (the popup) resumes it. There is
 * no setting, timeout or code path that approves on its own.
 * Navigation: a click / Enter recorded as navigating waits (<= 3 s) for the tab URL to change (pushState
 * counts) and for the DOM to go quiet before the next step; final checks run after a quiet wait and are
 * retried once after a short wait before FAILED (SPA routes render after the click returns).
 * Verify: after the last step, check the recorded final-URL pattern and the
 * expected texts -> VERIFIED (all checks pass), UNVERIFIED (finished but no
 * checks recorded), FAILED (a check failed or the run stopped). Then the local
 * verifier model (G7) reads the goal, the actions and the final page; it may only
 * downgrade VERIFIED to CHECK ("look at this one"), and its opinion is logged on
 * every run.
 */
importScripts('lib/safety.js', 'lib/model.js', 'lib/recipe.js', 'lib/agent.js');
var S = self.AWOSSafety, M = self.AWOSModel, R = self.AWOSRecipe;

// ---- storage helpers --------------------------------------------------------
async function getLocal(key, dflt) { var o = await chrome.storage.local.get(key); return o[key] === undefined ? dflt : o[key]; }
async function setLocal(key, val) { var o = {}; o[key] = val; await chrome.storage.local.set(o); }
async function getSess(key) { var o = await chrome.storage.session.get(key); return o[key] || null; }
async function setSess(key, val) { var o = {}; o[key] = val; if (val === null) await chrome.storage.session.remove(key); else await chrome.storage.session.set(o); }
async function settings() { var s = Object.assign({}, M.DEFAULTS, await getLocal('settings', {})); delete s.claudeKey; return s; }
/** brainSettings for a replay, with the "never use cloud" lock applied: a locked recipe or a locked site (its start
 *  page) never goes to Claude; repair / verify use the local model instead. The lock always wins. */
async function lockedBrain(run, rec) {
  var cfg = await brainSettings();
  if (cfg.brain !== 'claude') return cfg;
  var site = R.siteOf(rec.startUrl || '');
  var why = rec.noCloud ? 'this recipe' : (site && (cfg.noCloudSites || []).indexOf(site) >= 0 ? site : '');
  if (why) {
    cfg.brain = 'local'; delete cfg.claudeKey;
    if (!run.cloudLockLogged) { run.cloudLockLogged = true; logRun(run, 'cloud is locked off for ' + why + ': repair / verify use the local model only'); }
  }
  return cfg;
}
/** settings plus the API key, only for the model call itself. */
async function brainSettings() { var s = await settings(); if (s.brain === 'claude') s.claudeKey = await getLocal('claudeKey', ''); return s; }
async function getRec(id) { return (await getLocal('recordings', {}))[id] || null; }
async function putRec(rec) { var all = await getLocal('recordings', {}); all[rec.id] = rec; await setLocal('recordings', all); return rec; }
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
/** same page: origin + path (no query, no trailing slash). */
function samePage(a, b) {
  try { var x = new URL(a), y = new URL(b); return x.origin === y.origin && x.pathname.replace(/\/+$/, '') === y.pathname.replace(/\/+$/, ''); }
  catch (e) { return false; }
}
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
  if (!actionCount(rec)) rec.warning = NO_ACTIONS;
  var all = await getLocal('recordings', {}); all[rec.id] = rec; await setLocal('recordings', all);
  await setSess('recState', null);
  if (tab) await sendTab(st.tabId, { type: 'rec_state', on: false }).catch(function () {});
  return rec;
}

var NO_ACTIONS = 'Nothing was recorded on this page: only page addresses were captured, no click or typing. '
  + 'A replay would just reopen the last address. Record again, or the controls may be in a frame AWOS cannot see.';
function actionCount(rec) { return (rec.steps || []).filter(function (s) { return s.action !== 'goto'; }).length; }

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
    if (msg.step.action === 'click' || msg.step.action === 'press') st.lastClickAt = Date.now();
    await setSess('recState', st);
  }).catch(function (e) { console.warn('rec_step', e); });
  return recQueue;
}

chrome.tabs.onUpdated.addListener(function (tabId, info) {
  if (!info.url) return;
  recQueue = recQueue.then(async function () {
    var st = await getSess('recState');
    if (st && st.newTabs && st.newTabs[tabId] != null) {           // a tab a recorded click opened: is it a file?
      var ns = st.rec.steps[st.newTabs[tabId]];
      if (ns && fileUrl(info.url) && !(ns.download && ns.download.name)) { ns.download = { name: urlName(info.url) }; await setSess('recState', st); }
      return;
    }
    if (!st || st.tabId !== tabId) return;
    var steps = st.rec.steps, last = steps[steps.length - 1];
    if (last && last.action === 'goto' && sameUrl(last.url, info.url)) return;
    if (Date.now() - (st.lastClickAt || 0) < 3000 && last && (last.action === 'click' || last.action === 'press')) { last.navigates = true; }
    else steps.push({ action: 'goto', url: info.url });    // typed / bookmark navigation
    await setSess('recState', st);
  });
});

// ---- recording: downloads and new tabs a recorded click caused (v0.6) ------------------------------------
var FILE_EXT = /\.(pdf|csv|tsv|xlsx?|xlsm|ods|docx?|odt|zip|txt|xml|json|ofx|qfx|qif|sta|mt940|png|jpe?g)$/i;
function fileUrl(u) { try { var x = new URL(u); return /^https?:$/.test(x.protocol) && FILE_EXT.test(x.pathname); } catch (e) { return false; } }
/** the click step that just ran in the recorded tab (<= 5 s ago), or -1. */
function lastClickIndex(st) {
  var k = st.rec.steps.length - 1, last = st.rec.steps[k];
  return last && last.action === 'click' && Date.now() - (st.lastClickAt || 0) < 5000 ? k : -1;
}
chrome.downloads.onCreated.addListener(function (d) {
  setTimeout(function () {                 // the click's rec_step message may still be on its way
    recQueue = recQueue.then(async function () {
      var st = await getSess('recState');
      if (!st) return;
      var k = lastClickIndex(st);
      if (k < 0 || st.rec.steps[k].download) return;
      st.rec.steps[k].download = { name: fileName(d) || '' };
      await setSess('recState', st);
    }).catch(function (e) { console.warn('rec download', e); });
  }, 300);
});
chrome.tabs.onCreated.addListener(function (tab) {
  if (tab.openerTabId == null) return;
  setTimeout(function () {                 // the click's rec_step message may still be on its way
    recQueue = recQueue.then(async function () {
      var st = await getSess('recState');
      if (!st || st.tabId !== tab.openerTabId) return;
      var k = lastClickIndex(st);
      if (k < 0) return;
      var now = await chrome.tabs.get(tab.id).catch(function () { return tab; });
      var step = st.rec.steps[k], u = now.url || now.pendingUrl || tab.pendingUrl || tab.url || '';
      step.opensTab = true;
      if (fileUrl(u) && !step.download) step.download = { name: urlName(u) };
      (st.newTabs = st.newTabs || {})[tab.id] = k;
      await setSess('recState', st);
    }).catch(function (e) { console.warn('rec new tab', e); });
  }, 300);
});

// ---- replay -----------------------------------------------------------------
function logRun(run, text) { run.log.push({ t: Date.now(), step: run.i, text: text }); }
async function saveRun(run) {
  await setSess('run', run);
  var badge = run.status === 'awaiting_approval' || run.status === 'needs_user' ? '!' : run.status === 'running' ? '>' : '';
  chrome.action.setBadgeText({ text: badge }).catch(function () {});
}

async function busyReason(inCollection) {
  if (!inCollection) {
    var co = await getSess('coll');
    if (co && co.status === 'running') return 'a collection is running ("' + co.name + '")';
  }
  var cur = await getSess('run');
  if (cur && /^(running|awaiting_approval|needs_user)$/.test(cur.status)) return 'a replay is already in progress';
  if (await getSess('recState')) return 'stop the recording before running a replay';
  var tl = await getSess('tell');
  if (tl && /^(running|paused|awaiting_approval|needs_user|confirm)$/.test(tl.status)) return 'a Tell-it run is in progress';
  return '';
}

/** opts: {params: {name: value}, scheduled: bool}. Param values given here become the recipe's last-used values. */
async function startRun(recId, tabId, opts) {
  opts = opts || {};
  var busy = await busyReason(!!opts.collection);
  if (busy) throw new Error(busy);
  var rec = await getRec(recId);
  if (!rec) throw new Error('no such recording');
  var params = cleanParams(rec, opts.params);
  if (opts.params && Object.keys(params).length) { rec.lastParams = Object.assign({}, rec.lastParams || {}, params); await putRec(rec); }
  var run = { id: uid('run'), recId: recId, recName: rec.name, tabId: tabId, i: 0, status: 'running', startedAt: Date.now(),
              pending: null, approval: null, userDone: {}, log: [], resolved: [], result: null,
              params: Object.assign({}, rec.lastParams || {}, params), scheduled: !!opts.scheduled, imported: !!rec.imported,
              done: {}, unblocked: {}, period: (opts.collection && opts.collection.period) || periodOf(Date.now(), 0),
              collection: opts.collection || null };
  logRun(run, 'start "' + rec.name + '" (' + rec.steps.length + ' steps)' + (run.scheduled ? ' [scheduled]' : '') + (run.imported ? ' [imported recipe]' : '')
              + (run.collection ? ' [collection, ' + run.period + ']' : ''));
  if (!actionCount(rec)) logRun(run, 'note: this recording has no clicks or typing, only page addresses; nothing will be done on the page');
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
  var rec = await getRec(run.recId);
  if (!rec) return fail(run, 'ERROR', 'the recording was deleted');
  var cfg = await lockedBrain(run, rec);
  var strict = run.scheduled || !!rec.imported;      // nobody watching / someone else's recipe: stricter gate vocabulary
  run.done = run.done || {}; run.unblocked = run.unblocked || {};
  while (run.i < rec.steps.length) {
    var i = run.i;
    var missing = missingParams(rec, rec.steps[i], run.params);
    if (missing.length) {
      return pause(run, 'needs_user', { needParams: missing.map(function (p) { return { name: p.name, label: p.label, example: p.example || '' }; }),
        message: 'Step ' + (i + 1) + ' needs: ' + missing.map(function (p) { return p.label || p.name; }).join(', ') + '. Enter it in the AWOS popup, then press Continue.' });
    }
    var step = resolveStep(rec.steps[i], run.params);
    if (rec.imported) {               // imported recipes stay on start_url's site unless the user approves another one
      var where = step.action === 'goto' ? step.url : ((await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url || '');
      var site = R.siteOf(where);
      if (where && /^https?:/.test(where) && !siteAllowed(rec, where)) {
        return pause(run, 'awaiting_approval', { danger: 'other site', domain: site, target: site,
          message: 'This imported recipe wants to ' + (step.action === 'goto' ? 'open ' + R.stripUrl(where) : 'act on ' + site)
                 + ', outside its own site ' + rec.site + '. Approve ' + site + ' for this recipe?' });
      }
    }
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
      await sleep(150); await waitForLoad(run.tabId);
      try { await ensureContent(run.tabId, step.url, 4000); }
      catch (e) {                       // the site redirected (an expired session sends you to its login page)
        var landed = (await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url || '';
        if (!landed || sameUrl(landed, step.url)) throw e;
        await ensureContent(run.tabId);
        logRun(run, 'the site redirected to ' + R.stripUrl(landed));
      }
      run.resume = null;                // a goto puts the run where it should be by itself
      run.done[i] = true; run.i++; run.approval = null; await saveRun(run); continue;
    }
    await waitForLoad(run.tabId); await ensureContent(run.tabId);
    // handoff (v0.6): after a Continue, first get back to this step's page; then is a human needed here?
    var again = !!run.resume && !run.resume.auto;
    if (run.resume) await resumeAfterHandoff(run, rec, step, i);
    var hk = await handoffNeeded(run, step, i);
    // v0.6.1: on a login page, tick "remember me", and sign in once if Chrome autofilled both fields; else hand off
    if (hk && hk.kind === 'login' && await assistLogin(run, i)) continue;
    if (hk) return pause(run, 'needs_user', handoffPending(hk, i, again));
    if (step.action === 'fill' && (step.ask || S.isPlaceholder(step.value))) {
      if (run.userDone[i]) { logRun(run, 'user typed ' + (step.sensitive ? step.value : 'the value') + ' themselves'); run.done[i] = true; run.i++; await saveRun(run); continue; }
      return pause(run, 'needs_user', { message: 'Type the ' + (step.sensitive || (step.ask ? 'value' : 'secret')) + ' for "' + (step.loc.name || step.loc.label || 'this field')
        + '" yourself, then press Continue. AWOS never stores it.' });
    }
    if (downloadStep(step) && !step.navigates) {          // its file already arrived in this run: never download it twice
      var dup = await alreadyDownloaded(run, rec, step);
      if (dup) {
        logRun(run, 'step ' + (i + 1) + ': ' + dup + ' was already downloaded in this run; skipped (not downloaded again)');
        run.done[i] = true; run.i++; run.approval = null; await saveRun(run); continue;
      }
    }
    var loc = await sendRetry(run.tabId, { type: 'locate', step: step, timeoutMs: cfg.findTimeoutMs });
    if (loc.scrolled) logRun(run, 'scrolled ' + loc.scrolled.steps + ' step(s) looking for a lazy-loaded target (' + loc.scrolled.ms + ' ms, '
                                 + (loc.status === 'found' ? 'found' : 'not found') + ')');
    // pop-ups (v0.6): a target that is not found, or is drawn under an overlay, gets one unblock pass + one retry
    if ((loc.covered || loc.status !== 'found') && !run.unblocked[i]) {
      run.unblocked[i] = true;
      var ub = await sendRetry(run.tabId, { type: 'unblock' }, 2).catch(function () { return null; });
      if (ub && ub.dismissed && ub.dismissed.length) {
        ub.dismissed.forEach(function (d) {
          logRun(run, 'step ' + (i + 1) + ': dismissed a ' + (d.kind === 'consent' ? 'cookie / consent banner' : 'pop-up') + ' ("' + d.text.slice(0, 60) + '") with "' + d.name + '"');
          (run.dismissed = run.dismissed || []).push({ step: i, kind: d.kind, control: d.name });
        });
        await saveRun(run);
        await sleep(300); await waitQuiet(run.tabId, 1500);
        loc = await sendRetry(run.tabId, { type: 'locate', step: step, timeoutMs: cfg.findTimeoutMs });
        logRun(run, 'step ' + (i + 1) + ': retried after the dismissal: ' + (loc.status === 'found' && !loc.covered ? 'found' : loc.status + (loc.covered ? ', still covered' : '')));
      }
      if (ub && ub.blocked && (loc.covered || loc.status !== 'found')) {
        var b = ub.blocked, what = b.kind === 'consent' ? 'cookie / consent banner' : 'pop-up';
        return pause(run, 'needs_user', { popup: b.kind, message: 'Step ' + (i + 1) + ': a ' + what + ' ("' + b.text.slice(0, 80) + '") is in the way. AWOS only clicks close / not now'
          + (b.kind === 'consent' ? ' / reject' : '') + ' on these' + (b.controls.length ? ', and this one only offers: ' + b.controls.join(', ') : '')
          + '. Deal with it yourself in this tab, then press Continue.' });
      }
    }
    var index = -1, how = '';
    if (loc.status === 'found') {
      index = loc.index; how = 'score ' + loc.score + ' (margin ' + loc.margin + ')' + (loc.dupes ? ' [' + (loc.dupes + 1) + ' identical copies, picked by position]' : '');
    }
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
    var verb = step.action === 'press' ? 'press Enter in' : 'click';
    if (step.action === 'click' || step.action === 'press') {
      var danger = loc.dangers[index] || step.danger || S.dangerMatchStrict([cand.name || '', step.loc.name || ''].join(' | '), strict);
      if (danger && !ok) {
        return pause(run, 'awaiting_approval', { danger: danger, target: target,
          message: 'Step ' + (i + 1) + ' will ' + verb + ' "' + target + '" (matches "' + danger + '"). Approve to continue.' });
      }
    }
    var urlBefore = ((await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url) || '';
    var tabsBefore = step.action === 'click' ? (await chrome.tabs.query({}).catch(function () { return []; })).map(function (t) { return t.id; }) : null;
    var r = await sendTab(run.tabId, { type: 'act', step: step, index: index, approved: ok, strict: strict });
    if (!r || !r.ok) {
      if (r && r.needsApproval) return pause(run, 'awaiting_approval', { danger: r.danger, target: target,
        message: 'Step ' + (i + 1) + ' will ' + verb + ' "' + target + '" (matches "' + r.danger + '"). Approve to continue.' });
      return fail(run, 'ERROR', 'step ' + (i + 1) + ': ' + (r ? r.error : 'no reply'));
    }
    run.resolved.push({ step: i, how: how, name: cand.name || '' });
    // the verifier's action list, in the agent's own syntax (verifier_data.action_list)
    var q = function (v) { return JSON.stringify(String(v == null ? '' : v)); };
    var an = step.action === 'fill' ? 'fill(' + (index + 1) + ', ' + (step.sensitive || step.ask || S.isPlaceholder(step.value) ? '{secret}' : q(step.value)) + ')'
      : step.action === 'select' ? 'select(' + (index + 1) + ', ' + q(step.optionText || step.value) + ')'
      : step.action === 'press' ? 'press(' + (index + 1) + ', "Enter")'
      : 'click(' + (index + 1) + ')';
    (run.actions = run.actions || []).push({ action: an, control: (cand.role || step.loc.role || '') + ' ' + q(cand.name || step.loc.name || '') });
    logRun(run, (step.action === 'press' ? 'press Enter in' : step.action) + ' "' + (cand.name || '') + '" via ' + how);
    run.done[i] = true; run.i++; run.approval = null; await saveRun(run);
    if (step.action === 'click' || step.action === 'press') {
      if (step.navigates) {
        var nav = await settleNavigation(run.tabId, urlBefore, 3000);
        logRun(run, nav.changed ? 'page changed to ' + R.stripUrl(nav.url) + ' (' + nav.ms + ' ms)' : 'recorded as navigating, but the address did not change within 3 s');
      } else { await sleep(400); await waitForLoad(run.tabId); }
    }
    if (tabsBefore) {                 // new-tab files (v0.6): did this click open a tab on a PDF / CSV / ...?
      var nt = await newTabFrom(run.tabId, tabsBefore, step.opensTab ? 5000 : 0);
      if (nt != null) {
        var ft = await handleNewTab(run, nt, i);
        await saveRun(run);
        if (ft && ft.pause) return pause(run, 'awaiting_approval', ft.pause);
      }
    }
  }
  await finish(run, rec);
}

// ---- handoff: a human is needed (login again, one-time code, bot check) (v0.6) ----------------------------
var HANDOFF_ASK = { login: 'Log in', otp: 'Enter the code', captcha: 'Solve the check' };
/** the page kind when a human is needed before step i, else null. A bot check always needs one (AWOS never touches
 *  it); a login / one-time-code page only when the recording was not on that page for this step. */
async function handoffNeeded(run, step, i) {
  var pk = await sendRetry(run.tabId, { type: 'page_kind' }, 2).catch(function () { return null; });
  if (!pk || !pk.kind) return null;
  if (pk.kind === 'captcha') {
    var ack = run.handoffAck;
    return ack && ack.step === i && samePage(ack.url, pk.url) ? null : pk;
  }
  var expected = step.url ? samePage(pk.url, step.url) : (step.action === 'fill' && (!!step.sensitive || S.isPlaceholder(step.value)));
  return expected ? null : pk;
}
function handoffPending(pk, i, again) {
  var what = pk.kind === 'captcha' ? 'shows a bot check (' + pk.why + '). AWOS never solves or skips these.'
    : pk.kind === 'otp' ? 'asks for a one-time code (' + pk.why + ').'
    : 'asks you to log in (' + pk.why + ').';
  return { handoff: pk.kind, url: R.stripUrl(pk.url),
    message: (again ? 'Still waiting: ' : '') + 'Step ' + (i + 1) + ': the site ' + what + ' ' + HANDOFF_ASK[pk.kind]
      + ' in this tab, then click Continue. AWOS picks up at step ' + (i + 1) + ' without redoing earlier steps or downloads.' };
}
/** login helpers on a page the user is about to log in on (v0.6.1). Ticks a "remember me" style checkbox (Option
 *  rememberMe, default on) and, when Chrome has autofilled both the username and the password (Option autofillSignin,
 *  default on), clicks the form's sign-in button once. The password is never read, typed, stored or logged: the page
 *  script only checks the fields' :autofill state. -> {ticked, clicked, why} */
async function assistTab(tabId, cfg, tries) {
  var out = { ticked: '', clicked: '', why: '' };
  if (cfg.rememberMe !== false && !tries.remember) {
    tries.remember = true;
    var rm = await sendTab(tabId, { type: 'remember_me' }).catch(function () { return null; });
    if (rm && rm.ticked) out.ticked = rm.ticked;
  }
  if (cfg.autofillSignin !== false && !tries.autofill) {
    tries.autofill = true;
    var hook = !!(await getSess('testAutofillHook'));           // tests only: set from the worker, pages cannot reach it
    var af = await sendTab(tabId, { type: 'autofill_signin', testHook: hook }).catch(function () { return null; });
    out.clicked = (af && af.clicked) || ''; out.why = af ? af.why : 'the page did not answer';
  }
  return out;
}
/** a login handoff before step i: true when Chrome's autofill signed in (the loop then re-observes the page, as after
 *  the user's Continue), false when the run must pause for the user. Each helper runs once per step. */
async function assistLogin(run, i) {
  var cfg = await settings();
  run.loginTries = run.loginTries || {};
  var tries = run.loginTries[i] = run.loginTries[i] || {};
  if (tries.remember && tries.autofill) return false;
  var urlBefore = ((await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url) || '';
  var r = await assistTab(run.tabId, cfg, tries);
  if (r.ticked) logRun(run, 'step ' + (i + 1) + ': ticked "' + r.ticked + '" on the login page, so the portal keeps you signed in longer (Options can turn this off)');
  if (cfg.autofillSignin !== false && !r.clicked && r.why) logRun(run, 'step ' + (i + 1) + ': not signing in with Chrome\'s saved password: ' + r.why);
  if (!r.clicked) { await saveRun(run); return false; }
  logRun(run, 'step ' + (i + 1) + ': ' + r.why + '; clicked "' + r.clicked + '" once (AWOS never read, typed or stored the password)');
  run.resume = { step: i, kind: 'login', auto: true };
  await saveRun(run);
  try { await settleNavigation(run.tabId, urlBefore, 5000); } catch (e) { /* the loop's own checks follow */ }
  return true;
}
/** after the user's Continue: if the tab is not on step i's recorded page (a re-login lands on a dashboard), go back
 *  to it. Steps before i are done (run.done) and are not repeated. */
async function resumeAfterHandoff(run, rec, step, i) {
  var rs = run.resume; run.resume = null;
  var pk = await sendRetry(run.tabId, { type: 'page_kind' }, 2).catch(function () { return {}; });
  if (rs.kind === 'captcha' && pk.kind === 'captcha' && step.url && samePage(pk.url, step.url)) {
    // the check is part of the very page this step was recorded on (a solved widget stays on a form): go on
    run.handoffAck = { step: i, url: pk.url };
    logRun(run, 'continuing on the page with the check as you said it is done; AWOS did not touch the check');
    return;
  }
  var tab = await chrome.tabs.get(run.tabId).catch(function () { return {}; });
  var doneN = Object.keys(run.done || {}).length;
  if (!step.url || samePage(tab.url || '', step.url)) { logRun(run, 'after the handoff the tab is on step ' + (i + 1) + "'s page; continuing"); return; }
  var d = S.urlDanger(step.url);
  if (d || (rec.imported && !siteAllowed(rec, step.url))) { logRun(run, 'not reopening ' + R.stripUrl(step.url) + (d ? ' (matches "' + d + '")' : ' (another site)')); return; }
  logRun(run, 'after the handoff the tab is on ' + R.stripUrl(tab.url || '') + '; going back to ' + R.stripUrl(step.url) + ' for step ' + (i + 1)
              + ' (' + doneN + ' step(s) already done are not repeated)');
  await chrome.tabs.update(run.tabId, { url: step.url });
  await sleep(150); await waitForLoad(run.tabId); await ensureContent(run.tabId); await waitQuiet(run.tabId, 1500);
}

// ---- downloads: never twice in one run; files that open in a new tab (v0.6) -------------------------------
function urlPath(u) { try { return new URL(u).pathname; } catch (e) { return ''; } }
/** a click that downloads: recorded as such, or a link to a file. */
function downloadStep(step) { return !!step && step.action === 'click' && (!!step.download || FILE_EXT.test((step.loc && step.loc.href) || '')); }
/** chrome renames a repeat "statement (1).pdf": the same file for this check. */
function sameFile(a, b) {
  var f = function (s) { return String(s || '').toLowerCase().replace(/ \(\d+\)(?=\.[^.]*$|$)/, ''); };
  return !!a && !!b && f(a) === f(b);
}
/** the name of a download completed in this run that this step would trigger again, else ''. Matched by the link's
 *  file path when it names a file, else by the recorded download name; only when that key is unique in the recipe
 *  (two "statement.pdf" links on two accounts are two files). */
async function alreadyDownloaded(run, rec, step) {
  var href = (step.loc && step.loc.href) || '', name = (step.download && step.download.name) || '';
  var byHref = FILE_EXT.test(href);
  if (!byHref && !name) return '';
  var twins = rec.steps.filter(function (s) {
    return downloadStep(s) && (byHref ? (s.loc && s.loc.href) === href : !!(s.download && sameFile(s.download.name, name)));
  }).length;
  if (twins > 1) return '';
  var items = await chrome.downloads.search({ startedAfter: new Date(run.startedAt - 1000).toISOString() }).catch(function () { return []; });
  var hit = items.find(function (d) {
    if (d.state !== 'complete') return false;
    if (byHref) return urlPath(d.url) === href || urlPath(d.finalUrl || '') === href;
    return downloadNames(d).some(function (n) { return sameFile(n, name); });
  });
  return hit ? (fileName(hit) || 'the file') : '';
}
/** a tab opened from `opener` that was not there before the click (waits up to waitMs for it). */
async function newTabFrom(opener, before, waitMs) {
  var end = Date.now() + (waitMs || 0);
  for (;;) {
    var ts = await chrome.tabs.query({}).catch(function () { return []; });
    var t = ts.find(function (x) { return x.openerTabId === opener && before.indexOf(x.id) < 0; });
    if (t) return t.id;
    if (Date.now() >= end) return null;
    await sleep(200);
  }
}
/** a file opened in a new tab: by its extension, else by the document type the page script reports. */
async function fileKind(tabId, url, t) {
  if (FILE_EXT.test(urlPath(url))) return 'file';
  for (var k = 0; k < 2; k++) {
    try {
      var r = await sendTab(tabId, { type: 'ping' });
      if (r) return r.contentType && !/html|xml|svg/i.test(r.contentType) ? r.contentType : '';
    } catch (e) { /* no page script: a built-in viewer (PDF) runs none */ }
    await sleep(800);
  }
  return FILE_EXT.test((t && t.title) || '') ? 'viewer' : '';
}
/** same origin as the portal page, or HTTPS on the same site (files.portal.example from portal.example). */
function autoDownloadOk(url, from) {
  var a, b; try { a = new URL(url); b = new URL(from); } catch (e) { return false; }
  if (a.origin === b.origin) return true;
  return a.protocol === 'https:' && !!R.siteOf(url) && R.siteOf(url) === R.siteOf(from);
}
async function handleNewTab(run, tabId, i) {
  var t = null, url = '', end = Date.now() + 8000;
  while (Date.now() < end) {
    t = await chrome.tabs.get(tabId).catch(function () { return null; });
    if (!t) { logRun(run, 'step ' + (i + 1) + ' opened a tab that closed by itself (the browser may have downloaded the file)'); return null; }
    url = t.url || t.pendingUrl || '';
    if (/^https?:/.test(url) && t.status === 'complete') break;
    await sleep(150);
  }
  if (!/^https?:/.test(url)) { logRun(run, 'step ' + (i + 1) + ' opened a new tab with no web address; left it open'); return null; }
  if (!(await fileKind(tabId, url, t))) { logRun(run, 'step ' + (i + 1) + ' opened a new tab (' + R.stripUrl(url) + '), not a file; left it open'); return null; }
  var from = ((await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url) || '';
  if (!autoDownloadOk(url, from)) {
    var host = ''; try { host = new URL(url).host; } catch (e) {}
    return { pause: { fileTab: { tabId: tabId, url: url, step: i }, danger: 'file from another site', target: R.stripUrl(url),
      message: 'Step ' + (i + 1) + ' opened a file in a new tab from ' + host + ', which is not this portal (or not HTTPS). Approve to download it with your browser, or stop the run.' } };
  }
  await downloadFromTab(run, tabId, url, i);
  return null;
}
async function waitDownload(id, ms) {
  var end = Date.now() + ms, d = null;
  while (Date.now() < end) {
    d = (await chrome.downloads.search({ id: id }).catch(function () { return []; }))[0] || null;
    if (d && d.state !== 'in_progress') return d;
    await sleep(300);
  }
  return d;
}
/** download the file the new tab shows (the browser's cookies apply), close the tab, count it for expect_download. */
async function downloadFromTab(run, tabId, url, i) {
  var prior = await chrome.downloads.search({ startedAfter: new Date(run.startedAt - 1000).toISOString() }).catch(function () { return []; });
  var same = prior.find(function (d) { return sameUrl(d.url, url) || sameUrl(d.finalUrl || '', url); }), name;
  if (same) {
    name = fileName(same) || urlName(url);
    logRun(run, 'step ' + (i + 1) + ': the file in the new tab (' + name + ') is already ' + (same.state === 'complete' ? 'downloaded' : 'downloading') + ' in this run; not downloading it again');
  } else {
    var id = await chrome.downloads.download({ url: url, conflictAction: 'uniquify' });
    var d = await waitDownload(id, 30000);
    name = (d && fileName(d)) || urlName(url);
    logRun(run, 'step ' + (i + 1) + ' opened ' + name + ' in a new tab: downloaded it with the browser (your login applies)'
                + (d && d.state === 'complete' ? '' : ' (' + (d ? d.state : 'not started') + ')') + ' and closed the tab');
  }
  (run.fileTabs = run.fileTabs || []).push({ step: i, url: R.stripUrl(url), name: name });
  await chrome.tabs.remove(tabId).catch(function () {});
}

/** after a click / Enter that navigated when recorded: wait (bounded) for the tab URL to change (a real load or
 *  an SPA pushState), for the load to finish and for the DOM to go quiet. -> {changed, url, ms} */
async function settleNavigation(tabId, before, maxMs) {
  var t0 = Date.now(), end = t0 + maxMs, url = before, changed = false;
  while (Date.now() < end) {
    var t = await chrome.tabs.get(tabId).catch(function () { return null; });
    if (!t) throw new Error('tab closed');
    url = t.pendingUrl || t.url || '';
    if (url && url !== before) { changed = true; break; }
    await sleep(100);
  }
  var ms = Date.now() - t0;
  await sleep(150); await waitForLoad(tabId);
  await waitQuiet(tabId, Math.max(800, end - Date.now()));
  var t2 = await chrome.tabs.get(tabId).catch(function () { return null; });
  return { changed: changed, url: (t2 && t2.url) || url, ms: ms };
}
/** DOM quiet on the tab's page (no nodes added / removed for 300 ms), bounded; never throws. */
async function waitQuiet(tabId, maxMs) {
  try { return await sendRetry(tabId, { type: 'quiet', idleMs: 300, timeoutMs: maxMs }, 2); }
  catch (e) { return { quiet: false, error: String(e && e.message || e) }; }
}

// ---- params / domain lock ------------------------------------------------------------------------------
var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
/** 'YYYY-MM' of the month `offset` months from the date `t` (local time). */
function periodOf(t, offset) {
  var d = new Date(t); d.setDate(1); d.setMonth(d.getMonth() + (offset || 0));
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}
/** built-in values for a period: {{year}} 2026, {{month}} 10, {{month_name}} October (v0.6, collections). */
function builtins(period) {
  var m = /^(\d{4})-(\d{2})$/.exec(String(period || '')) || /^(\d{4})-(\d{2})$/.exec(periodOf(Date.now(), 0));
  return { year: m[1], month: m[2], month_name: MONTHS[+m[2] - 1] };
}
/** the run's param values over the built-ins (a recipe's own {{month}} param wins). */
function runParams(run) { return Object.assign(builtins(run.period), run.params || {}); }
function cleanParams(rec, given) {
  var out = {};
  (rec.params || []).forEach(function (p) {
    if (given && given[p.name] != null && String(given[p.name]) !== '') out[p.name] = String(given[p.name]).slice(0, 500);
  });
  return out;
}
/** params this step needs that have no value yet. */
function missingParams(rec, step, values) {
  var names = step.param ? [step.param] : [];
  return (rec.params || []).filter(function (p) { return names.indexOf(p.name) >= 0 && (values[p.name] == null || values[p.name] === ''); });
}
/** a copy of the step with its {{param}} filled in (fill value, select value + option text). */
function resolveStep(step, values) {
  if (!step.param) return step;
  var out = Object.assign({}, step), v = String(values[step.param]);
  if (step.action === 'fill') out.value = v;
  else if (step.action === 'select') { out.value = v; out.optionText = S.norm(v); }
  return out;
}
function siteAllowed(rec, url) {
  var site = R.siteOf(url);
  return !!site && (site === rec.site || (rec.approvedDomains || []).indexOf(site) >= 0);
}

/** the user approved exactly this step and this target (a name, or a URL for a goto). */
function approved(run, i, target) { return !!(run.approval && run.approval.step === i && run.approval.name === (target || '')); }

async function pause(run, status, pending) {
  run.status = status; run.pending = Object.assign({ nonce: nonce(), step: run.i }, pending);
  logRun(run, 'paused: ' + pending.message);
  await saveRun(run);
  await sendTab(run.tabId, { type: 'banner', text: pending.message + ' (open the AWOS popup)' }).catch(function () {});
  if (run.scheduled || pending.handoff || run.collection) notify('awos-run-' + run.id, 'AWOS Replay needs you: ' + run.recName, pending.message + ' Open the AWOS popup.');
  if (run.collection) await collOnPause(run);
}

async function fail(run, code, reason) {
  run.status = 'failed';
  run.result = { verdict: 'FAILED', code: code, reason: reason, checks: [] };
  logRun(run, 'FAILED ' + code + ': ' + reason);
  await archive(run);
  await sendTab(run.tabId, { type: 'banner', text: 'stopped. ' + reason }).catch(function () {});
  await afterRun(run);
}

function urlMatches(pattern, url) {
  if (!pattern) return true;
  if (pattern.indexOf('*') >= 0) {
    var re = new RegExp('^' + pattern.split('*').map(function (p) { return p.replace(/[.+?^${}()|[\]\\]/g, '\\$&'); }).join('.*') + '$');
    return re.test(url);
  }
  try { var u = new URL(url); return u.origin + u.pathname === pattern; } catch (e) { return false; }
}

/** the recorded URL + text checks on the page as it is now. */
async function pageChecks(run, rec) {
  await waitForLoad(run.tabId);
  var tab = await chrome.tabs.get(run.tabId), checks = [];
  var ex = rec.expect || {}, P = runParams(run);
  var urlPat = R.substitute(ex.urlPattern || '', P);
  if (urlPat) checks.push({ kind: 'url', expected: urlPat, actual: tab.url, ok: urlMatches(urlPat, tab.url) });
  var texts = (ex.texts || []).filter(Boolean).map(function (t) { return R.substitute(t, P); });
  if (texts.length) {
    var c = await sendRetry(run.tabId, { type: 'check', texts: texts });
    texts.forEach(function (t, k) { checks.push({ kind: 'text', expected: t, ok: !!c.present[k] }); });
  }
  return { checks: checks, url: tab.url };
}

/** the check results as evidence lines for the verifier (URL without its query). */
function checkEvidence(url, checks) {
  var bits = checks.map(function (c) {
    var what = c.kind === 'url' ? 'address is ' + c.expected : c.kind === 'download' ? 'download matching ' + c.expected : 'page shows "' + c.expected + '"';
    return what + ': ' + (c.ok ? 'PASS' : 'FAIL');
  });
  return { url: R.stripUrl(url), checks: bits };
}

async function finish(run, rec) {
  await sleep(200); await waitForLoad(run.tabId);
  await waitQuiet(run.tabId, 1500);
  var pc = await pageChecks(run, rec), checks = pc.checks;
  if (checks.some(function (x) { return !x.ok; })) {      // an SPA route may still be rendering: one more look
    logRun(run, 'checks not met yet (' + checks.filter(function (x) { return !x.ok; }).map(function (x) { return x.kind; }).join(', ') + '); retrying once after a short wait');
    await sleep(1500); await waitQuiet(run.tabId, 2000);
    pc = await pageChecks(run, rec); checks = pc.checks;
  }
  if (rec.expectDownload && rec.expectDownload.nameRegex) checks.push(await checkDownload(run, R.substitute(rec.expectDownload.nameRegex, runParams(run))));
  var verdict = !checks.length ? 'UNVERIFIED' : checks.every(function (x) { return x.ok; }) ? 'VERIFIED' : 'FAILED';
  var reason = verdict === 'UNVERIFIED' ? 'no expectations recorded' : '';
  var noActions = !actionCount(rec);
  if (noActions && verdict === 'VERIFIED') {               // only reopened a URL: the checks passing is not a chore done
    verdict = 'UNVERIFIED';
    reason = 'nothing was recorded on this page (the recording only reopens a page address, no click or typing ran); '
           + checks.length + '/' + checks.length + ' checks passed on that page';
  }
  // second opinion from the local verifier (G7). It may only DOWNGRADE: a VERIFIED it disagrees with
  // becomes CHECK ("look at this one"); it never turns UNVERIFIED / FAILED into a pass. Its opinion is
  // always recorded (shadow mode for the other verdicts), so exported logs show what it would have caught.
  var cfg = await lockedBrain(run, rec);
  if (cfg.enabled && cfg.verify !== false) {
    var obs = await sendRetry(run.tabId, { type: 'observe' }).catch(function () { return null; });
    var P = runParams(run);
    var op = obs ? await M.askVerify(cfg, M.buildVerifyMessages(R.substitute(rec.goal || rec.name, P), (run.actions || []).concat([{ action: 'done("Done.")' }]),
                                                          M.outline(obs, checkEvidence(pc.url, checks)), 'done("Done.")'))
                 : { error: 'could not read the final page' };
    run.verifier = op;
    logRun(run, op.error ? 'verifier: ' + op.error : 'verifier says ' + op.label + ': ' + op.reason + ' (' + op.ms + ' ms)');
    // a passed download check is evidence the verifier cannot see (it reads the page, not the browser's
    // downloads), so for download chores its disagreement is logged but does not downgrade
    var downloadOk = checks.some(function (x) { return x.kind === 'download' && x.ok; });
    if (downloadOk && verdict === 'VERIFIED' && !op.error && op.label !== 'VERIFIED') {
      logRun(run, 'verifier disagreement not applied: the download check passed, which the verifier cannot see');
    } else if (verdict === 'VERIFIED' && !op.error && op.label !== 'VERIFIED') {
      verdict = 'CHECK';
      reason = 'checks passed, but the local verifier says ' + op.label + ': ' + op.reason;
    }
  }
  run.status = verdict === 'FAILED' ? 'failed' : 'done';
  run.result = { verdict: verdict, code: verdict === 'FAILED' ? 'CHECK_FAILED' : noActions ? 'NO_ACTIONS' : '', checks: checks, reason: reason,
                 noActions: noActions };
  logRun(run, verdict + ' (' + checks.filter(function (x) { return x.ok; }).length + '/' + checks.length + ' checks)');
  await archive(run);
  await sendTab(run.tabId, { type: 'banner', text: '' }).catch(function () {});
  await afterRun(run);
}

// ---- download verification ------------------------------------------------------------------------------
function baseName(p) { return String(p || '').split(/[\\/]/).pop(); }
function urlName(u) { try { return decodeURIComponent(new URL(u).pathname.split('/').pop()); } catch (e) { return ''; } }
/** the names a download can be matched by: the saved file name, else the URL's last path segment. */
function downloadNames(d) { return [baseName(d.filename), urlName(d.finalUrl || d.url), urlName(d.url)].filter(Boolean); }
/** the name to show for a download: the first candidate that looks like a file name (a temp name has no extension). */
function fileName(d) { var ns = downloadNames(d); return ns.find(function (n) { return /\.[a-z0-9]{1,5}$/i.test(n); }) || ns[0] || ''; }
/** a download whose file name matches `nameRegex` finished since the run started. Waits `graceMs` for one to
 *  appear (the last click may start it a moment later) and up to `waitMs` for one in progress to complete. */
async function checkDownload(run, nameRegex, waitMs, graceMs) {
  var re; try { re = new RegExp(nameRegex, 'i'); } catch (e) { return { kind: 'download', expected: nameRegex, ok: false, actual: 'bad regex' }; }
  var t0 = Date.now(), end = t0 + (waitMs == null ? 30000 : waitMs), grace = t0 + (graceMs == null ? 6000 : graceMs), seen = '';
  for (;;) {
    var items = await chrome.downloads.search({ startedAfter: new Date(run.startedAt - 1000).toISOString() }).catch(function () { return []; });
    var mine = items.filter(function (d) { return downloadNames(d).some(function (n) { return re.test(n); }); });
    var done = mine.find(function (d) { return d.state === 'complete'; });
    if (done) return { kind: 'download', expected: nameRegex, ok: true, actual: downloadNames(done).find(function (n) { return re.test(n); }) };
    var going = mine.some(function (d) { return d.state === 'in_progress'; });
    seen = items.map(function (d) { return downloadNames(d).join(' / ') || '(unnamed)'; }).slice(0, 5).join(', ');
    if ((!going && Date.now() >= grace) || Date.now() >= end) {
      return { kind: 'download', expected: nameRegex, ok: false, actual: going ? 'still downloading' : (seen ? 'other downloads: ' + seen : 'no download') };
    }
    await sleep(500);
  }
}

// ---- after a run: last verdict on the recipe, scheduled-run notice --------------------------------------
async function afterRun(run) {
  var rec = await getRec(run.recId);
  var v = run.result ? run.result.verdict : 'FAILED';
  if (rec) { rec.lastRun = { verdict: v, at: Date.now(), scheduled: !!run.scheduled, reason: run.result && run.result.reason || '' }; await putRec(rec); }
  if (run.collection) return collAfterRun(run, rec);
  if (!run.scheduled) return;
  notify('awos-run-' + run.id, 'AWOS Replay: ' + run.recName + ' ' + v,
         v === 'VERIFIED' ? 'Done and verified.' : (run.result && run.result.reason) || 'Open the AWOS popup for details.');
  if (v === 'VERIFIED') { await sleep(300); chrome.tabs.remove(run.tabId).catch(function () {}); }
}

// ---- notifications --------------------------------------------------------------------------------------
var NOTICES = [];            // the last few notices (tests and the popup read them; nothing else)
function notify(id, title, message, buttons) {
  NOTICES.unshift({ id: id, title: title, message: message, at: Date.now(), buttons: (buttons || []).map(function (b) { return b.title; }) });
  NOTICES.length = Math.min(NOTICES.length, 20);
  try {
    var opt = { type: 'basic', iconUrl: 'icons/icon128.png', title: title, message: String(message).slice(0, 300),
                priority: 2, requireInteraction: /needs you/.test(title) };
    if (buttons && buttons.length) opt.buttons = buttons.slice(0, 2);
    chrome.notifications.create(id, opt).catch(function () {});
  } catch (e) { /* notifications unavailable */ }
}
/** a login notice (keep-alive / pre-flight): its button, or a click on it, opens just the portals that need a login. */
function loginNoticeColl(id) { var m = /^awos-(?:keep|pre)-(.+)$/.exec(id || ''); return m ? m[1] : ''; }
chrome.notifications.onButtonClicked.addListener(function (id) {
  var cid = loginNoticeColl(id);
  if (cid) openLogins(cid).catch(function (e) { console.warn('open logins', e); });
});
chrome.notifications.onClicked.addListener(async function (id) {
  var cid = loginNoticeColl(id);
  if (cid) return openLogins(cid).catch(function (e) { console.warn('open logins', e); });
  var run = await getSess('run');
  if (run && id === 'awos-run-' + run.id && run.tabId != null) {
    var t = await chrome.tabs.update(run.tabId, { active: true }).catch(function () { return null; });
    if (t) chrome.windows.update(t.windowId, { focused: true }).catch(function () {});
  }
});

// ---- schedules (chrome.alarms) --------------------------------------------------------------------------
var ALARM = 'awos-sched:';
/** next fire time (ms) for {kind:'daily'|'weekly'|'monthly', time:'HH:MM', day:0-6 (weekly, 0 = Sunday),
 *  date:1-28 (monthly)} after `now`. */
function nextFire(sc, now) {
  var m = /^(\d{1,2}):(\d{2})$/.exec(String(sc && sc.time || ''));
  if (!m || +m[1] > 23 || +m[2] > 59) return null;
  var d = new Date(now); d.setHours(+m[1], +m[2], 0, 0);
  if (sc.kind === 'monthly') {
    var dom = +sc.date;
    if (!(dom >= 1 && dom <= 28)) return null;
    d.setDate(dom);
    if (d.getTime() <= now) d.setMonth(d.getMonth() + 1, dom);
  } else if (sc.kind === 'weekly') {
    d.setDate(d.getDate() + ((+sc.day - d.getDay() + 7) % 7));
    if (d.getTime() <= now) d.setDate(d.getDate() + 7);
  } else if (d.getTime() <= now) d.setDate(d.getDate() + 1);
  return d.getTime();
}
async function armSchedule(rec) {
  await chrome.alarms.clear(ALARM + rec.id);
  if (!rec.schedule) return null;
  var when = nextFire(rec.schedule, Date.now());
  if (when) await chrome.alarms.create(ALARM + rec.id, { when: when });
  return when;
}
async function armAll(onlyMissing) {
  var recs = await getLocal('recordings', {});
  for (var id in recs) {
    if (!recs[id].schedule) continue;
    if (onlyMissing && await chrome.alarms.get(ALARM + id)) continue;
    await armSchedule(recs[id]);
  }
  var cols = await getLocal('collections', {});
  for (var cid in cols) {
    if (!(onlyMissing && await chrome.alarms.get(KEEP_ALARM + cid))) await armKeepAlive(cols[cid]);
    if (!cols[cid].schedule) continue;
    var ca = onlyMissing && await chrome.alarms.get(COLL_ALARM + cid);
    if (!ca) { await armColl(cols[cid]); continue; }
    if (!(await chrome.alarms.get(PRE_ALARM + cid))) await armPreflight(cols[cid], ca.scheduledTime);
  }
}
/** a scheduled run: its own background tab, last-used param values, every human step pauses + notifies. */
async function fireSchedule(recId) {
  var rec = await getRec(recId);
  if (!rec || !rec.schedule) return null;
  await armSchedule(rec);                                   // the next one first: a crash below must not stop the schedule
  var busy = await busyReason();
  if (busy) {
    var skipped = { id: uid('run'), recId: recId, recName: rec.name, status: 'failed', scheduled: true, startedAt: Date.now(), endedAt: Date.now(),
                    log: [{ t: Date.now(), step: 0, text: 'scheduled run skipped: ' + busy }], resolved: [],
                    result: { verdict: 'FAILED', code: 'BUSY', reason: 'scheduled run skipped: ' + busy, checks: [] } };
    var runs = await getLocal('runs', []); runs.unshift(skipped); await setLocal('runs', runs.slice(0, 20));
    rec.lastRun = { verdict: 'FAILED', at: Date.now(), scheduled: true, reason: skipped.result.reason }; await putRec(rec);
    notify('awos-skip-' + skipped.id, 'AWOS Replay skipped: ' + rec.name, busy);
    return skipped;
  }
  var tab = await chrome.tabs.create({ url: 'about:blank', active: false });
  return startRun(recId, tab.id, { scheduled: true });
}
chrome.alarms.onAlarm.addListener(function (a) {
  if (a.name.indexOf(ALARM) === 0) fireSchedule(a.name.slice(ALARM.length)).catch(function (e) { console.warn('schedule', e); });
  else if (a.name.indexOf(COLL_ALARM) === 0) fireColl(a.name.slice(COLL_ALARM.length)).catch(function (e) { console.warn('collection schedule', e); });
  else if (a.name.indexOf(COLL_WAIT) === 0) collSkip(a.name.slice(COLL_WAIT.length), 'nobody answered in time').catch(function () {});
  else if (a.name.indexOf(KEEP_ALARM) === 0) keepAliveSweep(a.name.slice(KEEP_ALARM.length)).catch(function (e) { console.warn('keep-alive', e); });
  else if (a.name.indexOf(PRE_ALARM) === 0) firePreflight(a.name.slice(PRE_ALARM.length)).catch(function (e) { console.warn('pre-flight', e); });
  else if (a.name.indexOf(LOGIN_POLL) === 0) collNext().catch(function () {});          // backup wake-up while waiting for logins
});
chrome.runtime.onStartup.addListener(function () { armAll(true); });
chrome.runtime.onInstalled.addListener(function () { armAll(true); });

// ---- collections: a monthly checklist of portals (v0.6) -------------------------------------------------
// A collection groups recipes (one portal each, with its expected download). Running it runs them one after another,
// each in its own background tab, and records per period (YYYY-MM) what happened: got (file name), missing (the
// portal ran but no matching file), needs_you (a login / code / check / approval nobody answered in time, or the
// user pressed Skip) or failed (reason). A re-run in the same period skips the portals already got.
var COLL_ALARM = 'awos-coll-sched:', COLL_WAIT = 'awos-coll-wait:', COLL_STATUS = { got: 1, missing: 1, needs_you: 1, failed: 1 };
async function getColl(id) { return (await getLocal('collections', {}))[id] || null; }
async function periodResults(collId, period) { return ((await getLocal('collResults', {}))[collId] || {})[period] || {}; }
async function setCollResult(collId, period, recId, val) {
  var all = await getLocal('collResults', {}), byP = all[collId] = all[collId] || {};
  (byP[period] = byP[period] || {})[recId] = Object.assign({ at: Date.now() }, val);
  Object.keys(byP).sort().reverse().slice(24).forEach(function (k) { delete byP[k]; });       // keep two years
  await setLocal('collResults', all);
}
function collSummary(col, res) {
  var n = { got: 0, missing: 0, needs_you: 0, failed: 0, notrun: 0 };
  col.recIds.forEach(function (id) { var r = res[id]; n[r && COLL_STATUS[r.status] ? r.status : 'notrun']++; });
  var bits = [n.got + ' of ' + col.recIds.length + ' got'];
  if (n.missing) bits.push(n.missing + ' missing');
  if (n.needs_you) bits.push(n.needs_you + (n.needs_you === 1 ? ' needs you' : ' need you'));
  if (n.failed) bits.push(n.failed + ' failed');
  if (n.notrun) bits.push(n.notrun + ' not run');
  return { counts: n, text: bits.join(', ') };
}

async function collStart(collId, opts) {
  opts = opts || {};
  var col = await getColl(collId);
  if (!col) throw new Error('no such collection');
  if (!col.recIds.length) throw new Error('this collection has no portals yet');
  var busy = await busyReason();
  if (busy) throw new Error(busy);
  var period = opts.period || periodOf(Date.now(), col.monthOffset || 0);
  if (!/^\d{4}-\d{2}$/.test(period)) throw new Error('period must be YYYY-MM');
  var res = await periodResults(collId, period);
  var queue = col.recIds.filter(function (id) { return !(res[id] && res[id].status === 'got'); });
  var c = { id: uid('coll'), collId: collId, name: col.name, period: period, queue: queue, idx: 0, status: 'running',
            scheduled: !!opts.scheduled, startedAt: Date.now(), alreadyGot: col.recIds.length - queue.length, current: null,
            phase: 'preflight', deferred: {}, needLogin: [] };
  await setSess('coll', c);
  collBegin(c.id).catch(function (e) { console.warn('collection', e); });
  return c;
}

/** pre-flight at the start of a collection run (v0.6.1): check which portals are logged out (a background tab per
 *  portal, nothing clicked or typed), send ONE notification naming them with "Open the ones that need me", then run the
 *  ready portals first; the logged-out ones wait at the end of the queue and are picked up as soon as their login is
 *  seen (collNext). */
async function collBegin(cId) {
  var c = await getSess('coll');
  if (!c || c.id !== cId || c.status !== 'running' || c.phase !== 'preflight') return;
  var col = await getColl(c.collId), cfg = await settings();
  if (col && col.preflightAtStart !== false && c.queue.length) {
    var pf = await withProbeLock(function () { return checkPortals(c.queue, { source: 'run start', spacingMs: cfg.preflightSpacingMs }); });
    var need = pf.results.filter(function (r) { return r.status === 'login'; }).map(function (r) { return r.recId; });
    c = await getSess('coll');
    if (!c || c.id !== cId || c.status !== 'running') return;
    if (need.length) {
      c.queue = c.queue.filter(function (id) { return need.indexOf(id) < 0; }).concat(need);
      need.forEach(function (id) { c.deferred[id] = true; });
      c.needLogin = need;
      for (var k = 0; k < need.length; k++) {
        await setCollResult(c.collId, c.period, need[k], { status: 'needs_you', loginNeeded: true, waiting: true, recName: (await getRec(need[k]) || {}).name,
          reason: 'Login needed: AWOS picks this portal up as soon as you are logged in' });
      }
      var names = await recNames(need), ready = c.queue.length - need.length;
      notify('awos-pre-' + c.collId, 'AWOS Replay: ' + c.name + ': log in to ' + need.length + ' portal' + (need.length === 1 ? '' : 's'),
             (ready ? ready + ' portal' + (ready === 1 ? ' is' : 's are') + ' ready and running now. ' : '') + 'Log in to ' + names
             + ' and AWOS picks ' + (need.length === 1 ? 'it' : 'them') + ' up as soon as you are in.', [{ title: 'Open the ones that need me' }]);
    }
  }
  c.phase = 'run'; await setSess('coll', c);
  await collNext();
}

var collChain = Promise.resolve();
/** start the next portal in the queue, or wait for a login, or finish. Serialised: timers, tab events and run ends
 *  may all ask at once. */
function collNext() { var p = collChain.then(collNextInner); collChain = p.catch(function () {}); return p; }
async function collNextInner() {
  var c = await getSess('coll');
  if (!c || c.status !== 'running' || c.phase === 'preflight' || c.current) return;
  while (c.idx < c.queue.length) {
    var recId = c.queue[c.idx], rec = await getRec(recId);
    if (!rec) { await setCollResult(c.collId, c.period, recId, { status: 'failed', reason: 'the recipe was deleted' }); c.idx++; continue; }
    var res = (await periodResults(c.collId, c.period))[recId];
    if (res && res.status === 'got') { c.idx++; continue; }
    if (c.deferred && c.deferred[recId]) {             // only logged-out portals are left: wait for a login
      var pick = await firstLoggedIn(c);
      if (pick < 0) {
        var cfg = await settings(), waitMs = Math.max(0.05, +cfg.collectionWaitMin || 10) * 60000;
        c.loginDeadline = c.loginDeadline || Date.now() + waitMs;
        if (Date.now() < c.loginDeadline) {
          c.waitingLogin = true; await setSess('coll', c);
          var poll = Math.max(200, +cfg.loginPollMs || 5000);
          setTimeout(function () { collNext().catch(function () {}); }, poll);
          chrome.alarms.create(LOGIN_POLL + c.collId, { when: Date.now() + Math.max(30000, poll) }).catch(function () {});
          return;
        }
        for (var k = c.idx; k < c.queue.length; k++) {
          if (!c.deferred[c.queue[k]]) continue;
          await setCollResult(c.collId, c.period, c.queue[k], { status: 'needs_you', loginNeeded: true, recName: ((await getRec(c.queue[k])) || {}).name,
            reason: 'Login needed: still logged out after ' + Math.round(waitMs / 60000) + ' min. Log in, then Run collection again (portals already got are skipped)' });
        }
        c.waitingLogin = false; c.idx = c.queue.length; await setSess('coll', c);
        break;
      }
      var tmp = c.queue[c.idx]; c.queue[c.idx] = c.queue[pick]; c.queue[pick] = tmp;
      recId = c.queue[c.idx]; rec = await getRec(recId);
      delete c.deferred[recId]; c.waitingLogin = false;
      (c.pickedUp = c.pickedUp || []).push(recId);
      chrome.alarms.clear(LOGIN_POLL + c.collId).catch(function () {});
    }
    var tab = await chrome.tabs.create({ url: 'about:blank', active: false });
    c.current = { recId: recId, tabId: tab.id }; await setSess('coll', c);
    try {
      await startRun(recId, tab.id, { scheduled: c.scheduled, collection: { id: c.collId, period: c.period } });
      return;
    } catch (e) {
      await setCollResult(c.collId, c.period, recId, { status: 'failed', reason: String(e && e.message || e) });
      chrome.tabs.remove(tab.id).catch(function () {});
      c = await getSess('coll'); c.idx++; c.current = null; await setSess('coll', c);
    }
  }
  await collFinish(c);
}
/** the queue index (from c.idx) of the first deferred portal whose login is now seen, or -1. A login tab the user
 *  opened from "Open the ones that need me" is asked first (no new tab); otherwise the portal's start page is checked
 *  in a background tab, at most every loginProbeMs (default 60 s) per portal. */
async function firstLoggedIn(c) {
  var cfg = await settings(), tabs = (await getSess('loginTabs')) || {}, every = Math.max(500, +cfg.loginProbeMs || 60000);
  c.lastProbe = c.lastProbe || {};
  for (var k = c.idx; k < c.queue.length; k++) {
    var id = c.queue[k];
    if (!c.deferred[id]) continue;
    var rec = await getRec(id);
    if (!rec) continue;
    if (tabs[id] != null) {
      var pk = await sendTab(tabs[id], { type: 'page_kind' }).catch(function () { return null; });
      if (pk && pk.url && R.siteOf(pk.url) === R.siteOf(rec.startUrl)) {
        if (!pk.kind) { await setPortalStatus(id, { status: 'ok', why: 'logged in (seen in the tab you opened)', url: R.stripUrl(pk.url) }, 'login tab'); return k; }
        continue;                                      // still on its login page: the user is not done yet
      }
    }
    if (Date.now() - (c.lastProbe[id] || 0) < every) continue;
    c.lastProbe[id] = Date.now(); await setSess('coll', c);
    var r = await withProbeLock(function () { return probePortal(rec); });
    await setPortalStatus(id, r, 'waiting for login');
    if (r.status === 'ok') return k;
  }
  return -1;
}

async function collFinish(c) {
  var cols = await getLocal('collections', {}), col = cols[c.collId];
  var sum = col ? collSummary(col, await periodResults(c.collId, c.period)) : { text: 'collection deleted', counts: {} };
  c.status = 'done'; c.current = null; c.endedAt = Date.now(); c.summary = sum.text; c.counts = sum.counts; c.waitingLogin = false;
  chrome.alarms.clear(LOGIN_POLL + c.collId).catch(function () {});
  await setSess('coll', c);
  if (col) { col.last = { period: c.period, summary: sum.text, at: Date.now() }; await setLocal('collections', cols); }
  notify('awos-coll-' + c.collId, 'AWOS Replay: ' + c.name + ' (' + c.period + ')', sum.text + (c.alreadyGot ? ' (' + c.alreadyGot + ' already got earlier)' : ''));
}

/** what one portal's run means for the checklist. */
function collStatusOf(run, rec) {
  var res = run.result || {}, checks = res.checks || [];
  var dl = checks.find(function (x) { return x.kind === 'download'; });
  if (run.leftForUser) return { status: 'needs_you', reason: run.leftForUser };
  if (dl && dl.ok) return { status: 'got', file: dl.actual || '' };
  if (!dl && /^(VERIFIED|CHECK|UNVERIFIED)$/.test(res.verdict || '')) {
    var last = (run.fileTabs || []).slice(-1)[0];
    return { status: 'got', file: last ? last.name : '', note: rec && !rec.expectDownload ? 'no expected download set for this portal' : '' };
  }
  if (dl && !dl.ok && res.code === 'CHECK_FAILED') return { status: 'missing', reason: 'no file matching ' + dl.expected + ' (' + (dl.actual || 'no download') + ')' };
  if (res.code === 'NOT_FOUND' && rec && downloadStep(rec.steps[run.i])) return { status: 'missing', reason: res.reason || 'the download link is not there' };
  return { status: 'failed', reason: (res.code ? res.code + ': ' : '') + (res.reason || 'stopped') };
}

async function collAfterRun(run, rec) {
  var st = collStatusOf(run, rec);
  st.runId = run.id; st.recName = run.recName;
  await setCollResult(run.collection.id, run.collection.period, run.recId, st);
  await chrome.alarms.clear(COLL_WAIT + run.id).catch(function () {});
  if (st.status === 'got') { await sleep(300); chrome.tabs.remove(run.tabId).catch(function () {}); }   // others stay open to look at
  var c = await getSess('coll');
  if (!c || c.status !== 'running' || !c.current || c.current.recId !== run.recId) return;
  c.idx++; c.current = null; await setSess('coll', c);
  collNext().catch(function (e) { console.warn('collection', e); });
}

/** a portal of the collection paused: it shows as Needs you; nobody answering within collectionWaitMin moves on. */
async function collOnPause(run) {
  var res = (await periodResults(run.collection.id, run.collection.period))[run.recId];
  if (!res || res.status !== 'got') {
    await setCollResult(run.collection.id, run.collection.period, run.recId,
                        { status: 'needs_you', reason: run.pending && run.pending.message || '', runId: run.id, waiting: true, recName: run.recName });
  }
  var cfg = await settings(), mins = Math.max(0.05, +cfg.collectionWaitMin || 10);
  await chrome.alarms.create(COLL_WAIT + run.id, { when: Date.now() + mins * 60000 });
}

/** leave the waiting portal for the user (popup "Skip this portal", or the wait alarm) and go on with the next. */
async function collSkip(runId, why) {
  var run = await getSess('run');
  if (!run || !run.collection || (runId && run.id !== runId) || !/^(needs_user|awaiting_approval)$/.test(run.status)) throw new Error('no portal of a collection is waiting for you');
  var msg = (run.pending && run.pending.message) || 'it needed you';
  run.leftForUser = msg; run.pending = null;
  logRun(run, 'collection moved on (' + (why || 'skipped by the user') + '); this portal is left for you');
  await fail(run, 'NEEDS_YOU', 'left for you, the collection moved on: ' + msg);
  return { ok: true };
}

async function armColl(col) {
  await chrome.alarms.clear(COLL_ALARM + col.id);
  var when = col.schedule ? nextFire(col.schedule, Date.now()) : null;
  if (when) await chrome.alarms.create(COLL_ALARM + col.id, { when: when });
  await armPreflight(col, when);
  return when;
}
async function fireColl(collId) {
  var col = await getColl(collId);
  if (!col || !col.schedule) return null;
  await armColl(col);
  var busy = await busyReason();
  if (busy) { notify('awos-coll-skip-' + collId, 'AWOS Replay skipped: ' + col.name, busy); return null; }
  return collStart(collId, { scheduled: true });
}

async function saveColl(msg) {
  var cols = await getLocal('collections', {}), recs = await getLocal('recordings', {});
  var col = msg.id ? cols[msg.id] : { id: uid('col'), createdAt: Date.now(), recIds: [], monthOffset: 0, schedule: null };
  if (!col) throw new Error('no such collection');
  if (typeof msg.name === 'string') col.name = msg.name.trim().slice(0, 100);
  if (!col.name) throw new Error('give the collection a name (e.g. Month-end supplier docs)');
  if (Array.isArray(msg.recIds)) {
    col.recIds = msg.recIds.map(String).filter(function (id, k, a) { return recs[id] && a.indexOf(id) === k; }).slice(0, 50);
  }
  if (msg.monthOffset != null) col.monthOffset = +msg.monthOffset === -1 ? -1 : 0;
  if (msg.schedule !== undefined) col.schedule = cleanSchedule(msg.schedule);
  // logins (v0.6.1): keep-alive every N days (on by default), pre-flight N hours before a scheduled run (24, 0 = off)
  if (msg.keepAlive !== undefined) col.keepAlive = msg.keepAlive !== false;
  if (msg.keepAliveDays != null) { var kd = parseInt(msg.keepAliveDays, 10); if (!(kd >= 1 && kd <= 30)) throw new Error('keep-alive: every 1-30 days'); col.keepAliveDays = kd; }
  if (msg.preflightHours != null) { var ph = parseInt(msg.preflightHours, 10); if (!(ph >= 0 && ph <= 168)) throw new Error('pre-flight: 0-168 hours before the run'); col.preflightHours = ph; }
  if (msg.preflightAtStart !== undefined) col.preflightAtStart = msg.preflightAtStart !== false;
  cols[col.id] = col; await setLocal('collections', cols);
  var next = await armColl(col);
  var keepNext = await armKeepAlive(col);
  return Object.assign({}, col, { next: next, keepNext: keepNext });
}

// ---- logins: keep-alive, pre-flight, "Open the ones that need me" (v0.6.1) ----------------------------------
// Most handoffs in a monthly run are expired logins. A keep-alive visits each portal's recorded start page every few
// days in a background tab, one portal at a time, and only looks: logged in, or a login page (lib/portal.js
// pageKind). Nothing on the page is clicked or typed (stricter than the pop-up rules allow), the tab is closed. A
// portal that logged the user out is reported early, once, and shows as "Login needed" in the checklist. The same
// check runs `preflightHours` before a scheduled collection run and at the start of every collection run.
// portalStatus (chrome.storage.local) {recId: {status: ok|login|captcha|error|self_login, kind, why, url, at, source, notified}}
var KEEP_ALARM = 'awos-keep:', PRE_ALARM = 'awos-pre:', LOGIN_POLL = 'awos-coll-login:';
var probeChain = Promise.resolve();
/** one portal check at a time (keep-alive, pre-flight and a waiting collection share it). */
function withProbeLock(fn) { var p = probeChain.then(fn, fn); probeChain = p.catch(function () {}); return p; }
/** the recording signs in by itself (it has a password step): its start page is a login page by design. */
function selfLogin(rec) { return (rec.steps || []).some(function (s) { return s.action === 'fill' && /password/i.test(String(s.sensitive || '')); }); }
async function recNames(ids) {
  var recs = await getLocal('recordings', {}), n = ids.map(function (id) { return (recs[id] && recs[id].name) || 'a portal'; });
  return n.length <= 1 ? n.join('') : n.slice(0, -1).join(', ') + ' and ' + n[n.length - 1];
}
/** open the portal's start page in a background tab, classify it, close the tab. Looks only: no click, no typing. */
async function probePortal(rec) {
  var start = rec.startUrl || '';
  if (!/^https?:/.test(start)) return { status: 'error', why: 'no web start page' };
  if (selfLogin(rec)) return { status: 'self_login', why: 'this recipe signs in by itself (you type the password during the run)' };
  if (S.urlDanger(start)) return { status: 'error', why: 'its start page matches "' + S.urlDanger(start) + '"; not opened unattended' };
  var tab = null;
  try {
    tab = await chrome.tabs.create({ url: start, active: false });
    await setSess('probeTab', tab.id);
    await sleep(150); await waitForLoad(tab.id, 20000);
    await ensureContent(tab.id, null, 8000);
    await waitQuiet(tab.id, 1500);
    var pk = await sendTab(tab.id, { type: 'page_kind' });
    var landed = ((await chrome.tabs.get(tab.id).catch(function () { return {}; })).url) || pk.url || '';
    var st = !pk.kind ? 'ok' : (pk.kind === 'login' || pk.kind === 'otp') ? 'login' : pk.kind;
    return { status: st, kind: pk.kind || '', why: pk.why || (st === 'ok' ? 'logged in' : ''), url: R.stripUrl(landed) };
  } catch (e) {
    return { status: 'error', why: String(e && e.message || e) };
  } finally {
    if (tab) await chrome.tabs.remove(tab.id).catch(function () {});
    await setSess('probeTab', null);
  }
}
/** record a portal's login status; -> the new entry with `wasNotified` (a logout already reported). */
async function setPortalStatus(recId, r, source) {
  var all = await getLocal('portalStatus', {}), prev = all[recId] || {};
  var cur = { status: r.status, kind: r.kind || '', why: r.why || '', url: r.url || '', at: Date.now(), source: source || '' };
  cur.notified = cur.status === 'login' && prev.status === 'login' && !!prev.notified;    // a new logout is reported again
  all[recId] = cur; await setLocal('portalStatus', all);
  return cur;
}
async function markNotified(ids) {
  var all = await getLocal('portalStatus', {});
  ids.forEach(function (id) { if (all[id]) all[id].notified = true; });
  await setLocal('portalStatus', all);
}
/** check portals one after another (spacingMs apart, default 8 s: polite to the sites). abortIfBusy: stop before the
 *  next portal when a replay / collection / recording / Tell it run started (keep-alive never gets in the way). */
async function checkPortals(recIds, opts) {
  var cfg = await settings(), out = { results: [], aborted: '' };
  var gap = opts.spacingMs != null ? +opts.spacingMs : (cfg.keepAliveSpacingMs != null ? +cfg.keepAliveSpacingMs : 8000);
  var ids = recIds.filter(function (id, k, a) { return a.indexOf(id) === k; });
  for (var k = 0; k < ids.length; k++) {
    if (opts.abortIfBusy) { var busy = await busyReason(); if (busy) { out.aborted = busy; break; } }
    var rec = await getRec(ids[k]);
    if (!rec) continue;
    if (k > 0 && gap > 0) await sleep(gap);
    var r = await probePortal(rec);
    var cur = await setPortalStatus(ids[k], r, opts.source);
    out.results.push(Object.assign({ recId: ids[k], name: rec.name }, cur));
  }
  return out;
}
async function armKeepAlive(col) {
  if (!col) return null;
  await chrome.alarms.clear(KEEP_ALARM + col.id);
  if (col.keepAlive === false || !(col.recIds || []).length) return null;
  var every = (col.keepAliveDays || 3) * 86400000;
  var when = Math.max(Date.now() + 60000, (col.lastKeepAlive || Date.now()) + every);
  await chrome.alarms.create(KEEP_ALARM + col.id, { when: when });
  return when;
}
/** the keep-alive sweep of one collection (the alarm, or the popup's "Check logins now"). Notifies once per logout. */
async function keepAliveSweep(collId, opts) {
  opts = opts || {};
  var col = await getColl(collId);
  if (!col) return null;
  if (!opts.manual && col.keepAlive === false) return null;
  var busy = await busyReason();
  if (busy) {
    if (opts.manual) throw new Error(busy);
    await chrome.alarms.create(KEEP_ALARM + collId, { when: Date.now() + 30 * 60000 });     // the user is mid-run: later
    return { skipped: busy };
  }
  var out = await withProbeLock(function () { return checkPortals(col.recIds, { source: opts.manual ? 'check now' : 'keep-alive', abortIfBusy: true }); });
  var cols = await getLocal('collections', {});
  if (cols[collId]) { cols[collId].lastKeepAlive = Date.now(); await setLocal('collections', cols); }
  if (out.aborted) await chrome.alarms.create(KEEP_ALARM + collId, { when: Date.now() + 30 * 60000 });
  else await armKeepAlive(cols[collId]);
  var fresh = out.results.filter(function (r) { return r.status === 'login' && !r.notified; }).map(function (r) { return r.recId; });
  if (fresh.length) {
    var names = await recNames(fresh);
    notify('awos-keep-' + collId, 'AWOS Replay: login needed for ' + col.name,
           names + ' logged you out; log in any time before the month-end run.', [{ title: 'Open the ones that need me' }]);
    await markNotified(fresh);
  }
  return { results: out.results, aborted: out.aborted, notified: fresh };
}
/** the advance check before a scheduled run: `preflightHours` (default 24, 0 = off) before it. */
async function armPreflight(col, runAt) {
  await chrome.alarms.clear(PRE_ALARM + col.id);
  var h = col.preflightHours == null ? 24 : +col.preflightHours;
  if (!runAt || !col.schedule || !(h > 0)) return null;
  var when = runAt - h * 3600000;
  if (when < Date.now() + 60000) return null;            // too close: the run's own start check does it
  await chrome.alarms.create(PRE_ALARM + col.id, { when: when });
  return when;
}
/** the portals of the collection's next scheduled period that still need a login: ONE notification naming them. */
async function firePreflight(collId) {
  var col = await getColl(collId);
  if (!col) return null;
  var runAlarm = await chrome.alarms.get(COLL_ALARM + collId), runAt = runAlarm ? runAlarm.scheduledTime : Date.now();
  var busy = await busyReason();
  if (busy) {                                           // mid-run: try again in 30 min if the run is still far enough away
    if (runAt - Date.now() > 3600000) await chrome.alarms.create(PRE_ALARM + collId, { when: Date.now() + 30 * 60000 });
    return { skipped: busy };
  }
  var period = periodOf(runAt, col.monthOffset || 0), res = await periodResults(collId, period);
  var ids = col.recIds.filter(function (id) { return !(res[id] && res[id].status === 'got'); });
  var out = await withProbeLock(function () { return checkPortals(ids, { source: 'pre-flight', abortIfBusy: true }); });
  var need = out.results.filter(function (r) { return r.status === 'login'; }).map(function (r) { return r.recId; });
  var cols = await getLocal('collections', {});
  if (cols[collId]) { cols[collId].lastPreflight = { at: Date.now(), need: need, period: period }; await setLocal('collections', cols); }
  if (need.length) {
    notify('awos-pre-' + collId, 'AWOS Replay: ' + col.name + ' runs ' + new Date(runAt).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }),
           'Log in to ' + (await recNames(need)) + ' any time before then, all in one go; the other portals are ready.', [{ title: 'Open the ones that need me' }]);
    await markNotified(need);
  }
  return { results: out.results, need: need, aborted: out.aborted };
}
/** "Open the ones that need me": a tab per portal of the collection whose login is needed (not already got this
 *  period), the first one in front. On each, "remember me" is ticked and Chrome's autofill may sign in (Options);
 *  the user does the rest. A waiting collection run notices the login in these tabs and picks the portal up. */
async function openLogins(collId) {
  var col = await getColl(collId);
  if (!col) throw new Error('no such collection');
  var c = await getSess('coll'), period = c && c.collId === collId ? c.period : periodOf(Date.now(), col.monthOffset || 0);
  var res = await periodResults(collId, period), ps = await getLocal('portalStatus', {}), tabs = (await getSess('loginTabs')) || {};
  var ids = col.recIds.filter(function (id) { return ps[id] && ps[id].status === 'login' && !(res[id] && res[id].status === 'got'); });
  var opened = [];
  for (var k = 0; k < ids.length; k++) {
    var rec = await getRec(ids[k]);
    if (!rec || !/^https?:/.test(rec.startUrl || '') || S.urlDanger(rec.startUrl)) continue;
    var old = tabs[ids[k]] != null ? await chrome.tabs.get(tabs[ids[k]]).catch(function () { return null; }) : null;
    var tab = old || await chrome.tabs.create({ url: rec.startUrl, active: opened.length === 0 });
    if (old && opened.length === 0) await chrome.tabs.update(old.id, { active: true }).catch(function () {});
    tabs[ids[k]] = tab.id; opened.push({ recId: ids[k], tabId: tab.id });
  }
  await setSess('loginTabs', tabs);
  if (opened.length) {
    var t0 = await chrome.tabs.get(opened[0].tabId).catch(function () { return null; });
    if (t0) chrome.windows.update(t0.windowId, { focused: true }).catch(function () {});
  }
  helpLoginTabs(opened).catch(function (e) { console.warn('login tabs', e); });
  return { opened: opened.length, recIds: opened.map(function (o) { return o.recId; }) };
}
/** on each opened login tab: "remember me", and Chrome's autofill sign-in (once per tab); noted on portalStatus. */
async function helpLoginTabs(opened) {
  var cfg = await settings();
  for (var k = 0; k < opened.length; k++) {
    try {
      await sleep(150); await waitForLoad(opened[k].tabId, 20000); await ensureContent(opened[k].tabId, null, 8000);
      var pk = await sendTab(opened[k].tabId, { type: 'page_kind' });
      if (pk.kind !== 'login') continue;
      var r = await assistTab(opened[k].tabId, cfg, {});
      var all = await getLocal('portalStatus', {});
      if (all[opened[k].recId]) {
        all[opened[k].recId].assist = { ticked: r.ticked, clicked: r.clicked, why: r.why, at: Date.now() };
        await setLocal('portalStatus', all);
      }
    } catch (e) { /* the user may already be typing there; nothing else to do */ }
  }
}
// a login tab the user opened finished loading: a collection waiting for logins looks right away
chrome.tabs.onUpdated.addListener(function (tabId, info) {
  if (info.status !== 'complete') return;
  Promise.all([getSess('coll'), getSess('loginTabs')]).then(function (both) {
    var c = both[0], tabs = both[1] || {};
    if (!c || c.status !== 'running' || !c.waitingLogin) return;
    if (Object.keys(tabs).some(function (id) { return tabs[id] === tabId; })) setTimeout(function () { collNext().catch(function () {}); }, 300);
  }).catch(function () {});
});

async function archive(run) {
  run.pending = null; run.endedAt = Date.now();
  await saveRun(run);
  var runs = await getLocal('runs', []); runs.unshift(run); await setLocal('runs', runs.slice(0, 20));
}

/** user decisions; only accepted from extension pages (popup/options), never from a web page. */
async function decide(kind, runId, n, params) {
  var run = await getSess('run');
  if (!run || run.id !== runId || !run.pending || run.pending.nonce !== n) throw new Error('stale or unknown request');
  if (kind === 'approve') {
    if (run.status !== 'awaiting_approval') throw new Error('nothing to approve');
    if (run.pending.fileTab) {                // a file opened in a new tab from another site: download it as the user asked
      var ftp = run.pending.fileTab;
      logRun(run, 'user approved downloading ' + R.stripUrl(ftp.url));
      try { await downloadFromTab(run, ftp.tabId, ftp.url, ftp.step); } catch (e) { logRun(run, 'download failed: ' + String(e && e.message || e)); }
    } else if (run.pending.domain) {          // an imported recipe leaving its site: remember this domain for this recipe
      var rec = await getRec(run.recId);
      if (rec) { rec.approvedDomains = (rec.approvedDomains || []).concat([run.pending.domain]); await putRec(rec); }
      logRun(run, 'user approved the site ' + run.pending.domain + ' for this recipe');
    } else {
      run.approval = { step: run.pending.step, name: run.pending.target || '' };
      logRun(run, 'user approved "' + run.pending.target + '"');
    }
  } else if (kind === 'continue') {
    if (run.status !== 'needs_user') throw new Error('nothing to continue');
    if (run.pending.needParams) {
      var rec2 = await getRec(run.recId), got = cleanParams(rec2 || {}, params);
      var still = run.pending.needParams.filter(function (p) { return got[p.name] == null; });
      if (still.length) throw new Error('enter a value for ' + still.map(function (p) { return p.label || p.name; }).join(', '));
      Object.assign(run.params, got);
      if (rec2) { rec2.lastParams = Object.assign({}, rec2.lastParams || {}, got); await putRec(rec2); }
      logRun(run, 'user entered ' + Object.keys(got).join(', '));
    } else if (run.pending.handoff) {         // login / code / check handled: re-observe and resume at the first step not done
      run.resume = { step: run.pending.step, kind: run.pending.handoff };
      logRun(run, 'user handled the ' + ({ login: 'login', otp: 'one-time code', captcha: 'bot check' })[run.pending.handoff] + '; checking where the run is');
    } else if (run.pending.popup) {           // the user closed the pop-up: one more unblock pass if it is back
      if (run.unblocked) delete run.unblocked[run.pending.step];
      logRun(run, 'user dealt with the pop-up');
    } else run.userDone[run.pending.step] = true;
  } else {                                    // reject / abort
    run.pending = null;
    return fail(run, 'REJECTED', 'user stopped the run at step ' + (run.i + 1));
  }
  run.status = 'running'; run.pending = null;
  await saveRun(run);
  await chrome.alarms.clear(COLL_WAIT + run.id).catch(function () {});
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
      expect: { url: !!(r.expect && r.expect.urlPattern), texts: ((r.expect && r.expect.texts) || []).length,
                download: !!(r.expectDownload && r.expectDownload.nameRegex) },
      params: (r.params || []).length, imported: !!r.imported, schedule: r.schedule ? r.schedule.kind : '',
      steps: r.steps.map(function (s) {
        if (s.action === 'goto') return { action: 'goto', url: redactUrl(s.url) };
        var l = s.loc || {};
        return { action: s.action, role: l.role, name: l.name, context: l.context || '', danger: s.danger || '',
                 sensitive: s.sensitive || '', valueLen: s.value != null && !s.param ? String(s.value).length : null,
                 param: !!s.param, ask: !!s.ask, navigates: !!s.navigates };
      }) };
  });
  var runOut = runs.map(function (r) {
    return { recId: r.recId, recName: r.recName, status: r.status, startedAt: r.startedAt, endedAt: r.endedAt,
      scheduled: !!r.scheduled, imported: !!r.imported,
      verdict: r.result && r.result.verdict, code: r.result && r.result.code, reason: redactText(r.result && r.result.reason),
      checks: ((r.result && r.result.checks) || []).map(function (c) { return { kind: c.kind, ok: c.ok }; }),
      resolved: r.resolved, modelCalls: r.modelCalls || [],
      verifier: r.verifier ? { label: r.verifier.label || '', reason: redactText(r.verifier.reason || ''),
                               error: r.verifier.error || '', ms: r.verifier.ms || 0 } : null,
      log: r.log.map(function (l) { return { t: l.t, step: l.step, text: redactText(l.text) }; }) };
  });
  return { kind: 'awos-replay-runlog/1', exportedAt: Date.now(), version: chrome.runtime.getManifest().version,
           model: { brain: cfg.brain, endpoint: redactUrl(cfg.endpoint), name: cfg.brain === 'claude' ? cfg.claudeModel : cfg.model, enabled: cfg.enabled },
           recordings: recOut, runs: runOut };
}

// ---- recipes: export / share / import ---------------------------------------------------------------------
var BRIDGE_ORIGINS = /^(https:\/\/awos\.tirramind\.com|https:\/\/awos-site\.pages\.dev|http:\/\/localhost:8000)\/r\//;

/** tell the recipe page that offered the pending import what the user decided (page contract: awos-import-result). */
async function answerPage(p, ok, error) {
  if (p && p.tabId != null) await chrome.tabs.sendMessage(p.tabId, { type: 'import_result', ok: ok, error: error || '' }).catch(function () {});
}

async function exportRecipe(id) {
  var rec = await getRec(id);
  if (!rec) throw new Error('no such recording');
  var out = R.fromRecording(rec);
  return { recipe: out.recipe, warnings: out.warnings, link: await R.encodeLink(out.recipe),
           filename: (rec.name || 'recipe').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase().slice(0, 60) + '.awos.json' };
}

/** a recipe offered for import (recipe page, file, pasted link). Validated, then parked until the user confirms. */
async function offerImport(msg, sender) {
  var fromPage = !!sender.tab && !fromExtensionPage(sender), source, v;   // the popup opened as a tab is not a page
  if (fromPage) {                     // only the recipe page's bridge, only an object
    if (!BRIDGE_ORIGINS.test(String(sender.url || ''))) throw new Error('imports are only accepted from the AWOS recipe page');
    source = 'page ' + R.stripUrl(sender.url);
    v = R.validate(msg.recipe);
  } else if (typeof msg.link === 'string') { source = 'link'; v = await R.decodeLink(msg.link); }
  else if (typeof msg.text === 'string') {
    source = 'file';
    if (msg.text.length > R.MAX_BYTES * 4) v = { ok: false, errors: ['file is larger than 64 KB'] };
    else { try { v = R.validate(JSON.parse(msg.text)); } catch (e) { v = { ok: false, errors: ['not a JSON file'] }; } }
  } else { source = 'popup'; v = R.validate(msg.recipe); }
  if (!v.ok) return { ok: false, error: v.errors.slice(0, 5).join('; ') };
  var pending = { nonce: nonce(), recipe: v.recipe, warnings: v.warnings || [], source: source, site: R.siteOf(v.recipe.start_url), at: Date.now(),
                  tabId: fromPage ? sender.tab.id : null };
  await answerPage(await getSess('pendingImport'), false, 'replaced by another recipe');   // one pending import at a time
  await setSess('pendingImport', pending);
  chrome.action.setBadgeText({ text: '+' }).catch(function () {});
  if (fromPage) {
    try { chrome.action.openPopup().catch(function () {}); } catch (e) { /* older Chrome: badge + notice only */ }
    notify('awos-import', 'AWOS Replay: review this recipe', '"' + v.recipe.name + '" is waiting. Open the AWOS Replay popup to review the steps and import it.');
  }
  return { ok: true, pending: true, name: v.recipe.name };
}

async function confirmImport(n) {
  var p = await getSess('pendingImport');
  if (!p || p.nonce !== n) throw new Error('stale or unknown import');
  var v = R.validate(p.recipe);                    // validate again: storage.session is ours, but cheap insurance
  if (!v.ok) throw new Error(v.errors.join('; '));
  var rec = R.toRecording(v.recipe, uid('rec'));
  await putRec(rec);
  await setSess('pendingImport', null);
  chrome.action.setBadgeText({ text: '' }).catch(function () {});
  await answerPage(p, true);
  return rec;
}

function cleanSchedule(sc) {
  if (!sc) return null;
  var out = { kind: sc.kind, time: String(sc.time || '') };
  if (out.kind !== 'daily' && out.kind !== 'weekly' && out.kind !== 'monthly') throw new Error('schedule must be daily or weekly (or monthly)');
  if (out.kind === 'weekly') { out.day = parseInt(sc.day, 10); if (!(out.day >= 0 && out.day <= 6)) throw new Error('pick a weekday'); }
  if (out.kind === 'monthly') { out.date = parseInt(sc.date, 10); if (!(out.date >= 1 && out.date <= 28)) throw new Error('pick a day of the month (1-28)'); }
  if (nextFire(out, Date.now()) == null) throw new Error('time must be HH:MM (24h)');
  return out;
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
             runs: await getLocal('runs', []), settings: await settings(), claudeKeySet: !!(await getLocal('claudeKey', '')),
             pendingImport: await getSess('pendingImport'), version: chrome.runtime.getManifest().version,
             collections: await getLocal('collections', {}), collResults: await getLocal('collResults', {}), coll: await getSess('coll'),
             portalStatus: await getLocal('portalStatus', {}) };
  },
  start_recording: async function (msg) { return startRecording(msg.tabId, msg.name); },
  stop_recording: async function () { return stopRecording(); },
  save_recording: async function (msg) {
    var all = await getLocal('recordings', {}), rec = all[msg.id];
    if (!rec) throw new Error('no such recording');
    if (typeof msg.name === 'string') rec.name = msg.name;
    if (typeof msg.goal === 'string') rec.goal = msg.goal.slice(0, 300);    // what the chore must achieve (the verifier reads it)
    if (msg.expect) rec.expect = { urlPattern: String(msg.expect.urlPattern || ''), texts: (msg.expect.texts || []).map(String) };
    if (typeof msg.description === 'string') rec.description = msg.description.slice(0, 2000);
    if (msg.expectDownload !== undefined) {
      var rx = msg.expectDownload && String(msg.expectDownload.nameRegex || '').trim();
      if (rx) { try { new RegExp(rx); new RegExp(R.substitute(rx, builtins())); } catch (e) { throw new Error('download name pattern is not a valid regex'); } }
      rec.expectDownload = rx ? { nameRegex: rx.slice(0, 200) } : null;
    }
    await setLocal('recordings', all); return rec;
  },
  /** turn a typed field (fill / select step) into a param, or back (param: null). Secret fields never become params. */
  set_param: async function (msg) {
    var rec = await getRec(msg.id);
    if (!rec) throw new Error('no such recording');
    var step = rec.steps[msg.step];
    if (!step || (step.action !== 'fill' && step.action !== 'select')) throw new Error('only typed fields and dropdowns can be params');
    if (msg.param) {
      if (step.sensitive || S.isPlaceholder(step.value)) throw new Error('secret fields are always typed by you at run time');
      var p = msg.param, name = String(p.name || '').trim();
      if (!R.PARAM_RE.test(name)) throw new Error('param name: lowercase letters, digits and _ (start with a letter)');
      var label = String(p.label || name).trim().slice(0, 100) || name;
      var kind = /^(text|number|date)$/.test(p.kind || '') ? p.kind : 'text';
      var def = { name: name, label: label, example: String(p.example == null ? '' : p.example).slice(0, 200), kind: kind };
      rec.params = (rec.params || []).filter(function (x) { return x.name !== name; }).concat([def]);
      step.param = name;
    } else delete step.param;
    var used = {}; rec.steps.forEach(function (s) { if (s.param) used[s.param] = 1; });
    rec.params = (rec.params || []).filter(function (x) { return used[x.name]; });
    if (rec.lastParams) Object.keys(rec.lastParams).forEach(function (k) { if (!used[k]) delete rec.lastParams[k]; });
    await putRec(rec); return rec;
  },
  set_schedule: async function (msg) {
    var rec = await getRec(msg.id);
    if (!rec) throw new Error('no such recording');
    rec.schedule = cleanSchedule(msg.schedule);
    await putRec(rec);
    var when = await armSchedule(rec);
    return { schedule: rec.schedule, next: when };
  },
  delete_recording: async function (msg) {
    var all = await getLocal('recordings', {}); delete all[msg.id]; await setLocal('recordings', all);
    await chrome.alarms.clear(ALARM + msg.id); return { ok: true };
  },
  run: async function (msg) { return startRun(msg.recId, msg.tabId, { params: msg.params }); },
  approve: async function (msg) { await decide('approve', msg.runId, msg.nonce); return { ok: true }; },
  continue: async function (msg) { await decide('continue', msg.runId, msg.nonce, msg.params); return { ok: true }; },
  reject: async function (msg) { await decide('reject', msg.runId, msg.nonce); return { ok: true }; },
  save_settings: async function (msg) {
    var s = Object.assign({}, msg.settings || {}); delete s.claudeKey;
    if (s.brain !== 'claude') s.brain = 'local';
    if (s.agentBrain !== 'claude') s.agentBrain = 'local';                 // Tell it: local unless the user opts in
    s.cloudConsent = (s.brain === 'claude' || s.agentBrain === 'claude') && s.cloudConsent === true;   // consent only means something with a Claude brain
    if (s.agentEndpoint != null && !M.isLoopback(s.agentEndpoint)) throw new Error('the agent endpoint must be loopback (127.0.0.1 / localhost)');
    if (s.agentClaudeModel != null && ['claude-haiku-5-5', 'claude-sonnet-5-5'].indexOf(s.agentClaudeModel) < 0) s.agentClaudeModel = 'claude-haiku-5-5';
    if (s.searchPage != null && !/^https?:\/\/[^\s]+$/i.test(String(s.searchPage))) delete s.searchPage;   // Tell it's start when no site is open
    if (s.noCloudSites != null) s.noCloudSites = (Array.isArray(s.noCloudSites) ? s.noCloudSites : String(s.noCloudSites).split(/[\s,]+/))
      .map(function (x) { x = String(x).trim().toLowerCase(); return /^https?:/.test(x) ? R.siteOf(x) : R.registrableDomain(x.replace(/\/.*$/, '')); })
      .filter(Boolean).filter(function (x, i, a) { return a.indexOf(x) === i; }).slice(0, 200);
    await setLocal('settings', s); return settings();
  },
  save_claude_key: async function (msg) {
    var k = String(msg.key || '').trim();
    if (k && !/^sk-ant-[A-Za-z0-9_-]{10,}$/.test(k)) throw new Error('that does not look like an Anthropic API key (sk-ant-...)');
    await setLocal('claudeKey', k); return { ok: true, set: !!k };
  },
  brain_status: async function () {
    var cfg = await brainSettings();
    if (cfg.brain === 'claude') { var why = M.claudeBlocked(cfg); return { brain: 'claude', ok: !why, error: why }; }
    var r = await M.pingLocal(cfg, 1500);
    return { brain: 'local', ok: r.ok, error: r.error || '' };
  },
  export_log: async function () { return exportLog(); },
  export_recipe: async function (msg) { return exportRecipe(msg.id); },
  import_offer: async function (msg, sender) { return offerImport(msg, sender); },
  import_confirm: async function (msg) { return confirmImport(msg.nonce); },
  import_discard: async function () {
    var p = await getSess('pendingImport');
    await setSess('pendingImport', null); chrome.action.setBadgeText({ text: '' }).catch(function () {});
    await answerPage(p, false, 'cancelled'); return { ok: true };
  },
  run_schedule_now: async function (msg) { return fireSchedule(msg.id); },     // the popup's "test the schedule" button
  // collections (v0.6)
  coll_save: async function (msg) { return saveColl(msg); },
  coll_delete: async function (msg) {
    var cols = await getLocal('collections', {}); delete cols[msg.id]; await setLocal('collections', cols);
    var res = await getLocal('collResults', {}); delete res[msg.id]; await setLocal('collResults', res);
    await chrome.alarms.clear(COLL_ALARM + msg.id); await chrome.alarms.clear(KEEP_ALARM + msg.id); await chrome.alarms.clear(PRE_ALARM + msg.id);
    return { ok: true };
  },
  // logins (v0.6.1)
  coll_check_logins: async function (msg) { return keepAliveSweep(msg.id, { manual: true }); },     // "Check logins now"
  coll_open_logins: async function (msg) { return openLogins(msg.id); },                           // "Open the ones that need me"
  coll_run: async function (msg) { return collStart(msg.id, { period: msg.period }); },
  coll_skip: async function (msg) { return collSkip(msg.runId, 'skipped by the user'); },
  coll_stop: async function () {               // stop the collection after the current portal (it is not interrupted)
    var c = await getSess('coll');
    if (!c || c.status !== 'running') throw new Error('no collection is running');
    c.queue = c.queue.slice(0, c.idx + 1); await setSess('coll', c); return c;
  },
  coll_clear: async function () { var c = await getSess('coll'); if (c && c.status !== 'running') await setSess('coll', null); return { ok: true }; },
};
// the only messages a content script may send; import_offer checks the sender is the recipe page itself
var PAGE_ONLY = { hello: 1, rec_step: 1, import_offer: 1 };

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
// Everything a pause needs (pending, run.done, run.resume, the collection queue) is in storage.session, so a
// Continue after a restart resumes exactly where the pause was.
async function restoreAfterRestart() {
  var run = await getSess('run');
  if (run && run.status === 'running' && driving !== run.id) {
    await fail(run, 'INTERRUPTED', 'the extension restarted during step ' + (run.i + 1) + '; check the page, then run again');
  }
  var pt = await getSess('probeTab');         // a login check the restart cut short: its background tab goes
  if (pt != null) { await chrome.tabs.remove(pt).catch(function () {}); await setSess('probeTab', null); }
  var co = await getSess('coll');             // a collection whose current portal ended while the worker was gone
  if (co && co.status === 'running' && co.current) {
    var cur = await getSess('run');
    if (!cur || cur.recId !== co.current.recId || /^(done|failed)$/.test(cur.status)) { co.idx++; co.current = null; await setSess('coll', co); await collNext(); }
  } else if (co && co.status === 'running' && co.phase === 'preflight') await collBegin(co.id);
  else if (co && co.status === 'running' && co.waitingLogin) await collNext();     // still waiting for a login
  await armAll(true);                 // alarms normally persist; re-arm any that went missing
  return { run: await getSess('run'), coll: await getSess('coll') };
}
restoreAfterRestart();

importScripts('tellit.js');          // Tell it (v0.5): goal -> agent loop -> recipe; uses the helpers above
