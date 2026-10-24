/* AWOS Replay popup: record/stop, recordings list (run, expectations, delete),
 * live run panel with the approval / needs-user prompt and the verdict.
 * The target tab is the active tab, or ?tab=<id> when the popup is opened as a
 * page (handy for testing and for pinning it in its own window).
 * Approve is only ever sent from a click handler on the Approve button.
 */
'use strict';
var $ = function (id) { return document.getElementById(id); };
var targetTab = null, state = null, lastSig = '';

function call(msg) {
  return chrome.runtime.sendMessage(msg).then(function (r) {
    if (r && r.error) throw new Error(r.error);
    $('error').textContent = '';
    return r;
  }).catch(function (e) { $('error').textContent = String(e.message || e); throw e; });
}

async function resolveTab() {
  var q = new URLSearchParams(location.search).get('tab');
  if (q) return parseInt(q, 10);
  var tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tabs[0] ? tabs[0].id : null;
}

function el(tag, attrs, text) {
  var e = document.createElement(tag);
  for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}

function renderRecordings() {
  var list = $('recordings'), recs = Object.values(state.recordings || {}).sort(function (a, b) { return b.createdAt - a.createdAt; });
  $('empty').hidden = recs.length > 0;
  if (list.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;   // don't clobber typing
  var busy = state.run && /^(running|awaiting_approval|needs_user)$/.test(state.run.status);
  var sig = JSON.stringify([recs, !!busy, !!state.recState]);
  if (sig === lastSig) return;                 // re-render only on change, so clicks never hit a stale node
  lastSig = sig;
  list.textContent = '';
  recs.forEach(function (r) {
    var li = el('li', { 'data-rec': r.id });
    li.appendChild(el('div', null, r.name));
    var acts = r.steps.filter(function (s) { return s.action !== 'goto'; }).length;
    li.appendChild(el('div', { class: 'meta' }, acts + ' actions, from ' + r.startUrl));
    var texts = el('input', { class: 'texts', placeholder: 'Expect text on final page (comma separated)', value: (r.expect.texts || []).join(', ') });
    var url = el('input', { class: 'url', placeholder: 'Final URL (origin+path, * allowed; blank = no check)', value: r.expect.urlPattern || '' });
    li.appendChild(texts); li.appendChild(url);
    var row = el('div', { class: 'acts' });
    var run = el('button', { class: 'run primary' }, 'Run'); run.disabled = !!(busy || state.recState);
    run.onclick = function () { call({ type: 'run', recId: r.id, tabId: targetTab }).then(refresh); };
    var save = el('button', { class: 'save' }, 'Save checks');
    save.onclick = function () {
      call({ type: 'save_recording', id: r.id, expect: { urlPattern: url.value.trim(),
             texts: texts.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean) } }).then(refresh);
    };
    var del = el('button', { class: 'del' }, 'Delete');
    del.onclick = function () { if (confirm('Delete "' + r.name + '"?')) call({ type: 'delete_recording', id: r.id }).then(refresh); };
    row.appendChild(run); row.appendChild(save); row.appendChild(del);
    li.appendChild(row);
    list.appendChild(li);
  });
}

function renderRun() {
  var run = state.run;
  $('runpanel').hidden = !run;
  if (!run) return;
  $('runname').textContent = run.recName + ' - ' + run.status.replace('_', ' ');
  var v = run.result ? run.result.verdict : (run.status === 'running' ? 'RUNNING' : 'PAUSED');
  $('verdict').textContent = v; $('verdict').className = 'badge ' + v;
  var p = run.pending;
  $('pending').hidden = !p;
  if (p) {
    $('pendingmsg').textContent = p.message;
    $('approve').hidden = run.status !== 'awaiting_approval';
    $('continue').hidden = run.status !== 'needs_user';
  }
  $('reason').textContent = run.result && run.result.reason ? (run.result.code ? run.result.code + ': ' : '') + run.result.reason : '';
  var ch = $('checks'); ch.textContent = '';
  ((run.result && run.result.checks) || []).forEach(function (c) {
    ch.appendChild(el('li', { class: c.ok ? 'ok' : 'bad' }, c.kind + ': ' + c.expected));
  });
  var log = $('log'); log.textContent = '';
  run.log.slice(-40).forEach(function (l) { log.appendChild(el('li', null, l.text)); });
}

function render() {
  var rs = state.recState, mine = rs && rs.tabId === targetTab;
  $('rec').hidden = !!rs; $('stop').hidden = !rs;
  $('recinfo').textContent = rs ? (mine ? 'Recording: ' : 'Recording another tab: ') + (rs.rec.steps.length - 1) + ' actions' : '';
  renderRecordings(); renderRun();
}

async function refresh() { state = await call({ type: 'get_state' }); render(); }

$('rec').onclick = function () { call({ type: 'start_recording', tabId: targetTab }).then(refresh); };
$('stop').onclick = function () { call({ type: 'stop_recording' }).then(refresh); };
function decide(kind) {
  var run = state.run; if (!run || !run.pending) return;
  call({ type: kind, runId: run.id, nonce: run.pending.nonce }).then(refresh);
}
$('approve').onclick = function () { decide('approve'); };
$('continue').onclick = function () { decide('continue'); };
$('reject').onclick = function () { decide('reject'); };

(async function () {
  targetTab = await resolveTab();
  await refresh();
  setInterval(function () { refresh().catch(function () {}); }, 500);
})();
