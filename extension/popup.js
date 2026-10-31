/* AWOS Replay popup: record/stop, recordings list (run with params, checks, params, schedule, share, delete),
 * live run panel with the approval / needs-user prompt and the verdict, the import confirmation, and (v0.6)
 * collections: the monthly checklist (Got / Missing / Needs you / Failed per portal, run, skip, schedule).
 * The target tab is the active tab, or ?tab=<id> when the popup is opened as a
 * page (handy for testing and for pinning it in its own window).
 * Approve and Import are only ever sent from click handlers on those buttons.
 */
'use strict';
var $ = function (id) { return document.getElementById(id); };
var targetTab = null, state = null, lastSig = '', lastImpSig = '', lastNeedSig = '';
var openDetails = {};          // rec id + section -> open, survives re-renders
var DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function call(msg) {
  return chrome.runtime.sendMessage(msg).then(function (r) {
    if (r && r.error) throw new Error(r.error);
    if (msg.type !== 'get_state') $('error').textContent = '';      // the 500 ms poll must not wipe an error
    return r;
  }).catch(function (e) { $('error').textContent = String(e.message || e); throw e; });
}
function notice(t) { $('notice').textContent = t || ''; }

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
function details(key, summary) {
  var d = el('details', { 'data-key': key }); d.appendChild(el('summary', null, summary));
  d.open = !!openDetails[key];
  d.addEventListener('toggle', function () { openDetails[key] = d.open; });
  return d;
}
function download(name, text) {
  var a = el('a', { href: URL.createObjectURL(new Blob([text], { type: 'application/json' })), download: name });
  document.body.appendChild(a); a.click(); a.remove();
}

function stepLabel(s) {
  var l = s.loc || {};
  return l.name || l.label || l.placeholder || l.nameAttr || l.id || l.css || 'field';
}

/** one field row in "Fields": make it a param, or show why it can't be. */
function fieldRow(r, s, k) {
  var row = el('div', { class: 'field', 'data-step': String(k) });
  var secret = s.sensitive || /^\{\{secret:/.test(String(s.value || ''));
  row.appendChild(el('span', { class: 'fname' }, (s.action === 'select' ? 'dropdown ' : '') + '"' + stepLabel(s) + '"'));
  if (secret) { row.appendChild(el('span', { class: 'muted' }, ' secret: you type it each run')); return row; }
  if (s.ask) { row.appendChild(el('span', { class: 'muted' }, ' you type it each run')); return row; }
  var p = s.param && (r.params || []).find(function (x) { return x.name === s.param; });
  if (p && !row.editing) {
    row.appendChild(el('span', { class: 'param' }, ' {{' + p.name + '}} "' + p.label + '"'));
    var rm = el('button', { class: 'unparam small' }, 'Not a param');
    rm.onclick = function () { call({ type: 'set_param', id: r.id, step: k, param: null }).then(refresh); };
    row.appendChild(rm);
    return row;
  }
  var make = el('button', { class: 'mkparam small' }, 'Ask each run');
  make.onclick = function () {
    make.remove();
    var guess = stepLabel(s).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^(\d)/, 'p$1').slice(0, 32) || 'value';
    var name = el('input', { class: 'pname', placeholder: 'name (e.g. customer)', value: guess });
    var label = el('input', { class: 'plabel', placeholder: 'Question (e.g. Which customer)', value: stepLabel(s) });
    var example = el('input', { class: 'pexample', placeholder: 'Example shown to others (e.g. Grace Hopper)' });
    var save = el('button', { class: 'psave small primary' }, 'Save param');
    save.onclick = function () {
      call({ type: 'set_param', id: r.id, step: k, param: { name: name.value.trim(), label: label.value.trim(), example: example.value.trim(), kind: 'text' } })
        .then(function () { lastSig = ''; refresh(); });
    };
    [name, label, example, save].forEach(function (x) { row.appendChild(x); });
    name.focus();
  };
  row.appendChild(make);
  return row;
}

function scheduleBox(r) {
  var box = el('div', { class: 'sched' });
  var kind = el('select', { class: 'skind' });
  [['', 'Off'], ['daily', 'Daily'], ['weekly', 'Weekly on']].forEach(function (o) { var x = el('option', { value: o[0] }, o[1]); kind.appendChild(x); });
  var day = el('select', { class: 'sday' });
  DAYS.forEach(function (d, k) { day.appendChild(el('option', { value: String(k) }, d)); });
  var time = el('input', { class: 'stime', type: 'time', value: (r.schedule && r.schedule.time) || '09:00' });
  kind.value = r.schedule ? r.schedule.kind : '';
  day.value = r.schedule && r.schedule.day != null ? String(r.schedule.day) : '1';
  function vis() { day.hidden = kind.value !== 'weekly'; time.hidden = !kind.value; }
  kind.onchange = vis; vis();
  var save = el('button', { class: 'ssave small' }, 'Save schedule');
  save.onclick = function () {
    var sc = kind.value ? { kind: kind.value, time: time.value, day: parseInt(day.value, 10) } : null;
    call({ type: 'set_schedule', id: r.id, schedule: sc }).then(function (res) {
      notice(res.next ? 'Next run: ' + new Date(res.next).toLocaleString() : 'Schedule off.'); refresh();
    });
  };
  [kind, day, el('span', null, ' at '), time, save].forEach(function (x) { box.appendChild(x); });
  box.appendChild(el('div', { class: 'muted' }, 'A scheduled run opens its own tab. If it needs you (password, approval, a missing value) it pauses and notifies you.'));
  return box;
}

function runForm(r, li) {
  var old = li.querySelector('.runform'); if (old) { old.remove(); return; }
  var f = el('div', { class: 'runform' }), inputs = {};
  (r.params || []).forEach(function (p) {
    f.appendChild(el('label', null, p.label));
    var i = el('input', { class: 'pval', 'data-param': p.name, placeholder: p.example || '' });
    i.value = (r.lastParams && r.lastParams[p.name]) || '';
    inputs[p.name] = i; f.appendChild(i);
  });
  var go = el('button', { class: 'rungo primary' }, 'Run now');
  go.onclick = function () {
    var vals = {}; for (var k in inputs) vals[k] = inputs[k].value.trim();
    var miss = (r.params || []).filter(function (p) { return !vals[p.name]; });
    if (miss.length) { $('error').textContent = 'Enter: ' + miss.map(function (p) { return p.label; }).join(', '); return; }
    call({ type: 'run', recId: r.id, tabId: targetTab, params: vals }).then(function () { lastSig = ''; refresh(); });
  };
  f.appendChild(go);
  li.appendChild(f);
  var first = f.querySelector('input'); if (first) first.focus();
}

function renderRecordings() {
  var list = $('recordings'), recs = Object.values(state.recordings || {}).sort(function (a, b) { return b.createdAt - a.createdAt; });
  $('empty').hidden = recs.length > 0;
  var ae = document.activeElement;
  if (list.contains(ae) && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName)) return;     // don't clobber typing
  if (list.querySelector('.runform, .field input')) return;                         // a form is open
  var busy = state.run && /^(running|awaiting_approval|needs_user)$/.test(state.run.status);
  var sig = JSON.stringify([recs, !!busy, !!state.recState]);
  if (sig === lastSig) return;                 // re-render only on change, so clicks never hit a stale node
  lastSig = sig;
  list.textContent = '';
  recs.forEach(function (r) {
    var li = el('li', { 'data-rec': r.id });
    var head = el('div', { class: 'row' }); head.appendChild(el('span', null, r.name));
    if (r.imported) head.appendChild(el('span', { class: 'badge' }, 'imported'));
    if (r.lastRun) head.appendChild(el('span', { class: 'badge ' + r.lastRun.verdict, title: r.lastRun.reason || '' }, r.lastRun.verdict));
    li.appendChild(head);
    var acts = r.steps.filter(function (s) { return s.action !== 'goto'; }).length;
    var meta = (acts ? acts + ' actions' : 'no actions recorded (only page addresses)') + ', from ' + r.startUrl;
    if (r.params && r.params.length) meta += ' | asks: ' + r.params.map(function (p) { return p.label; }).join(', ');
    if (r.schedule) meta += ' | ' + (r.schedule.kind === 'weekly' ? 'weekly ' + DAYS[r.schedule.day] : 'daily') + ' ' + r.schedule.time;
    li.appendChild(el('div', { class: 'meta' }, meta));

    var row = el('div', { class: 'acts' });
    var run = el('button', { class: 'run primary' }, 'Run'); run.disabled = !!(busy || state.recState);
    run.onclick = function () {
      if (r.params && r.params.length) return runForm(r, li);
      call({ type: 'run', recId: r.id, tabId: targetTab }).then(refresh);
    };
    var share = el('button', { class: 'share' }, 'Share');
    share.onclick = function () {
      call({ type: 'export_recipe', id: r.id }).then(function (x) {
        var warn = x.warnings && x.warnings.length ? ' Note: ' + x.warnings.join(' ') : '';
        return navigator.clipboard.writeText(x.link).then(function () { notice('Recipe link copied (no typed values, no passwords).' + warn); },
                                                          function () { notice('Recipe link: ' + x.link + warn); });
      });
    };
    var exp = el('button', { class: 'exportjson' }, 'Export .awos.json');
    exp.onclick = function () { call({ type: 'export_recipe', id: r.id }).then(function (x) { download(x.filename, JSON.stringify(x.recipe, null, 1)); }); };
    var del = el('button', { class: 'del' }, 'Delete');
    del.onclick = function () { if (confirm('Delete "' + r.name + '"?')) call({ type: 'delete_recording', id: r.id }).then(refresh); };
    [run, share, exp, del].forEach(function (b) { row.appendChild(b); });
    li.appendChild(row);

    var chk = details(r.id + ':checks', 'Goal + checks');
    var goal = el('input', { class: 'goal', placeholder: 'Goal: what must be true at the end (e.g. marketing e-mails are off)', value: r.goal || '' });
    var texts = el('input', { class: 'texts', placeholder: 'Expect text on final page (comma separated)', value: (r.expect.texts || []).join(', ') });
    var url = el('input', { class: 'url', placeholder: 'Final URL (origin+path, * allowed; blank = no check)', value: r.expect.urlPattern || '' });
    var dl = el('input', { class: 'dl', placeholder: 'Expect a download named like (regex, e.g. report.*\\.csv)', value: (r.expectDownload && r.expectDownload.nameRegex) || '' });
    var save = el('button', { class: 'save' }, 'Save goal + checks');
    save.onclick = function () {
      call({ type: 'save_recording', id: r.id, goal: goal.value.trim(), expectDownload: { nameRegex: dl.value.trim() },
             expect: { urlPattern: url.value.trim(), texts: texts.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean) } }).then(refresh);
    };
    var lockl = el('label', { class: 'cloudlock' }), lock = el('input', { type: 'checkbox', class: 'nocloud' });
    lock.checked = !!r.noCloud;
    lock.onchange = function () { call({ type: 'set_recipe_cloud_lock', id: r.id, on: lock.checked }).then(refresh); };
    lockl.appendChild(lock); lockl.appendChild(document.createTextNode(' Never use cloud for this recipe (repair / verify stay on this computer)'));
    [goal, texts, url, dl, save, lockl].forEach(function (x) { chk.appendChild(x); });
    li.appendChild(chk);

    var typed = r.steps.map(function (s, k) { return [s, k]; }).filter(function (p) { return p[0].action === 'fill' || p[0].action === 'select'; });
    if (typed.length) {
      var fields = details(r.id + ':fields', 'Fields (' + typed.length + '): what to ask each run');
      typed.forEach(function (p) { fields.appendChild(fieldRow(r, p[0], p[1])); });
      li.appendChild(fields);
    }
    var sch = details(r.id + ':sched', 'Schedule' + (r.schedule ? ' (on)' : ''));
    sch.appendChild(scheduleBox(r));
    li.appendChild(sch);
    list.appendChild(li);
  });
}

function renderRun() {
  var run = state.run;
  $('runpanel').hidden = !run;
  if (!run) return;
  $('runname').textContent = run.recName + ' - ' + run.status.replace('_', ' ') + (run.scheduled ? ' (scheduled)' : '');
  var v = run.result ? run.result.verdict : (run.status === 'running' ? 'RUNNING' : 'PAUSED');
  $('verdict').textContent = v; $('verdict').className = 'badge ' + v;
  var p = run.pending;
  $('pending').hidden = !p;
  if (p) {
    $('pendingmsg').textContent = p.message;
    $('approve').hidden = run.status !== 'awaiting_approval';
    $('approve').textContent = p.domain ? 'Approve site ' + p.domain : 'Approve this click';
    $('continue').hidden = run.status !== 'needs_user';
    var needSig = p.nonce + JSON.stringify(p.needParams || []);
    if (needSig !== lastNeedSig) {
      lastNeedSig = needSig;
      var box = $('needparams'); box.textContent = '';
      (p.needParams || []).forEach(function (np) {
        box.appendChild(el('label', null, np.label || np.name));
        box.appendChild(el('input', { class: 'needval', 'data-param': np.name, placeholder: np.example || '' }));
      });
    }
  } else { $('needparams').textContent = ''; lastNeedSig = ''; }
  $('reason').textContent = run.result && run.result.reason ? (run.result.code ? run.result.code + ': ' : '') + run.result.reason : '';
  var ch = $('checks'); ch.textContent = '';
  ((run.result && run.result.checks) || []).forEach(function (c) {
    ch.appendChild(el('li', { class: c.ok ? 'ok' : 'bad' }, c.kind + ': ' + c.expected + (c.kind === 'download' && c.actual ? ' (' + c.actual + ')' : '')));
  });
  var log = $('log'); log.textContent = '';
  run.log.slice(-40).forEach(function (l) { log.appendChild(el('li', null, l.text)); });
}

function describeStep(s) {
  if (s.action === 'goto') return 'open ' + s.url;
  var what = '"' + stepLabel(s) + '"';
  if (s.action === 'click') return 'click ' + what;
  if (s.action === 'press') return 'press Enter in ' + what;
  if (s.action === 'fill') return 'type into ' + what + ': ' + (s.ask ? (s.sensitive ? 'you type it (' + s.sensitive + ')' : 'you type it') : s.value);
  return 'choose ' + (s.optionText || s.value) + ' in ' + what;
}

function renderImport() {
  var p = state.pendingImport;
  $('importpanel').hidden = !p;
  if (!p) { lastImpSig = ''; return; }
  if (p.nonce === lastImpSig) return;
  lastImpSig = p.nonce;
  var rc = p.recipe;
  $('imp-name').textContent = rc.name; $('imp-desc').textContent = rc.description || '';
  $('imp-start').textContent = rc.start_url; $('imp-site').textContent = p.site; $('imp-source').textContent = p.source;
  $('imp-params').textContent = rc.params.length ? 'Asks you each run: ' + rc.params.map(function (x) { return x.label + (x.example ? ' (e.g. ' + x.example + ')' : ''); }).join(', ') : '';
  var ol = $('imp-steps'); ol.textContent = '';
  rc.steps.forEach(function (s) { ol.appendChild(el('li', null, describeStep(s))); });
  var c = rc.checks, bits = [];
  if (c.goal) bits.push('goal: ' + c.goal);
  if (c.expect_text.length) bits.push('expects text: ' + c.expect_text.join(' | '));
  if (c.expect_url) bits.push('ends at: ' + c.expect_url);
  if (c.expect_download) bits.push('expects a download matching /' + c.expect_download.name_regex + '/');
  $('imp-checks').textContent = bits.join('; ');
  var wl = $('imp-warn'); wl.textContent = '';
  (p.warnings || []).forEach(function (w) { wl.appendChild(el('li', null, w)); });
}

function render() {
  var rs = state.recState, mine = rs && rs.tabId === targetTab;
  $('rec').hidden = !!rs; $('stop').hidden = !rs;
  $('recinfo').textContent = rs ? (mine ? 'Recording: ' : 'Recording another tab: ') + (rs.rec.steps.length - 1) + ' actions' : '';
  renderImport(); renderRecordings(); renderRun(); renderCollections();
}

// ---- Collections: the monthly checklist (v0.6) -------------------------------------------------------------
var lastCollSig = '', lastPickSig = '';
function periodOf(t, offset) {
  var d = new Date(t); d.setDate(1); d.setMonth(d.getMonth() + (offset || 0));
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}
/** v0.6.1: a portal the last login check found logged out shows "Login needed" until it is got this period. */
function loginNeeded(r, p) { return !(r && r.status === 'got') && ((p && p.status === 'login') || !!(r && r.loginNeeded)); }
function collStatusText(r, p) {
  if (loginNeeded(r, p)) {
    var when = p && p.at ? ' (checked ' + new Date(p.at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) + ')' : '';
    return 'Login needed' + (r && r.waiting ? ' (waiting for you)' : '') + (r && r.loginNeeded && !r.waiting && r.reason ? ': ' + r.reason.replace(/^Login needed: /, '') : when);
  }
  if (!r) return 'Not run yet';
  if (r.status === 'got') return r.file ? 'Got (' + r.file + ')' : 'Got';
  if (r.status === 'missing') return 'Missing' + (r.reason ? ': ' + r.reason : '');
  if (r.status === 'needs_you') return 'Needs you' + (r.waiting ? ' (waiting)' : '') + (r.reason ? ': ' + r.reason : '');
  return 'Failed' + (r.reason ? ' (' + r.reason + ')' : '');
}
function collSchedBox(col) {
  var box = el('div', { class: 'sched' });
  var on = el('select', { class: 'cskind' });
  [['', 'No schedule'], ['monthly', 'Monthly on day']].forEach(function (o) { on.appendChild(el('option', { value: o[0] }, o[1])); });
  var date = el('input', { class: 'csdate', type: 'number', min: '1', max: '28', value: String((col.schedule && col.schedule.date) || 1) });
  var time = el('input', { class: 'cstime', type: 'time', value: (col.schedule && col.schedule.time) || '09:00' });
  on.value = col.schedule ? 'monthly' : '';
  function vis() { date.hidden = time.hidden = !on.value; }
  on.onchange = vis; vis();
  var save = el('button', { class: 'cssave small' }, 'Save schedule');
  save.onclick = function () {
    var sc = on.value ? { kind: 'monthly', date: parseInt(date.value, 10), time: time.value } : null;
    call({ type: 'coll_save', id: col.id, schedule: sc }).then(function (res) {
      notice(res.next ? 'Next collection run: ' + new Date(res.next).toLocaleString() : 'Schedule off.'); lastCollSig = ''; refresh();
    });
  };
  [on, date, time, save].forEach(function (x) { box.appendChild(x); });
  box.appendChild(el('div', { class: 'muted' }, 'A scheduled collection opens one background tab per portal; a login, code or check notifies you.'));
  return box;
}
function collKeepBox(col) {
  var box = el('div', { class: 'sched keep' });
  var lab = el('label', { class: 'meta' }), on = el('input', { type: 'checkbox', class: 'kaon' }); on.checked = col.keepAlive !== false;
  lab.appendChild(on); lab.appendChild(document.createTextNode(' Keep logins alive: visit each portal every '));
  var days = el('input', { class: 'kadays', type: 'number', min: '1', max: '30', value: String(col.keepAliveDays || 3) });
  lab.appendChild(days); lab.appendChild(document.createTextNode(' days (a background tab; nothing is clicked or typed)'));
  var lab2 = el('label', { class: 'meta' }, 'Check logins ');
  var hours = el('input', { class: 'kahours', type: 'number', min: '0', max: '168', value: String(col.preflightHours == null ? 24 : col.preflightHours) });
  lab2.appendChild(hours); lab2.appendChild(document.createTextNode(' hours before a scheduled run (0 = off; every run also checks at its start)'));
  var save = el('button', { class: 'kasave small' }, 'Save');
  save.onclick = function () {
    call({ type: 'coll_save', id: col.id, keepAlive: on.checked, keepAliveDays: parseInt(days.value, 10) || 3,
           preflightHours: Math.max(0, parseInt(hours.value, 10) || 0) }).then(function (r) {
      notice(r.keepNext ? 'Next login check: ' + new Date(r.keepNext).toLocaleString() : 'Keep-alive off.'); lastCollSig = ''; refresh();
    });
  };
  [lab, lab2, save].forEach(function (x) { box.appendChild(x); });
  return box;
}
function renderCollections() {
  var cols = Object.values(state.collections || {}).sort(function (a, b) { return a.createdAt - b.createdAt; });
  var recs = state.recordings || {}, results = state.collResults || {}, c = state.coll, run = state.run;
  $('collempty').hidden = cols.length > 0;
  // the "New collection" picker: one checkbox per recording
  var pickSig = JSON.stringify(Object.keys(recs).map(function (k) { return [k, recs[k].name]; }));
  if (pickSig !== lastPickSig) {
    lastPickSig = pickSig;
    var pick = $('collpick'), was = {};
    pick.querySelectorAll('input').forEach(function (i) { was[i.value] = i.checked; });
    pick.textContent = '';
    Object.values(recs).sort(function (a, b) { return a.createdAt - b.createdAt; }).forEach(function (r) {
      var l = el('label'), i = el('input', { type: 'checkbox', value: r.id, class: 'collrec' });
      i.checked = !!was[r.id];
      l.appendChild(i); l.appendChild(document.createTextNode(' ' + r.name + (r.expectDownload && r.expectDownload.nameRegex ? '' : ' (no expected download yet)')));
      pick.appendChild(l);
    });
    if (!Object.keys(recs).length) pick.textContent = 'Record each portal first (one recipe per portal).';
  }
  $('collrun').textContent = c ? (c.status === 'running' ? 'Running "' + c.name + '" for ' + c.period + ' (' + Math.min(c.idx + 1, c.queue.length) + ' of ' + c.queue.length + ')'
                                                        : 'Last run of "' + c.name + '" (' + c.period + '): ' + (c.summary || '')) : '';
  var list = $('collections'), ae = document.activeElement;
  if (list.contains(ae) && /^(INPUT|SELECT)$/.test(ae.tagName)) return;
  var busy = (run && /^(running|awaiting_approval|needs_user)$/.test(run.status)) || (c && c.status === 'running') || !!state.recState;
  var waiting = run && run.collection && /^(needs_user|awaiting_approval)$/.test(run.status) ? run : null;
  var sig = JSON.stringify([cols, results, c, !!busy, waiting && waiting.id, Object.keys(recs), state.portalStatus]);
  if (sig === lastCollSig) return;
  lastCollSig = sig;
  list.textContent = '';
  cols.forEach(function (col) {
    var period = c && c.collId === col.id ? c.period : (col.last ? col.last.period : periodOf(Date.now(), col.monthOffset || 0));
    var res = (results[col.id] || {})[period] || {};
    var li = el('li', { class: 'coll', 'data-coll': col.id });
    var head = el('div', { class: 'row' }); head.appendChild(el('strong', null, col.name)); head.appendChild(el('span', { class: 'badge' }, period));
    li.appendChild(head);
    col.recIds.forEach(function (id) {
      var r = res[id], row = el('div', { class: 'portal', 'data-rec': id });
      row.appendChild(el('span', null, recs[id] ? recs[id].name : (r && r.recName) || '(deleted recipe)'));
      var p = (state.portalStatus || {})[id];
      row.appendChild(el('span', { class: 'st ' + (loginNeeded(r, p) ? 'needs_you login' : r ? r.status : 'notrun') }, collStatusText(r, p)));
      li.appendChild(row);
    });
    if (col.last && !(c && c.collId === col.id && c.status === 'running')) li.appendChild(el('div', { class: 'meta' }, 'Last: ' + col.last.summary));
    var acts = el('div', { class: 'acts' });
    var go = el('button', { class: 'collgo primary' }, 'Run collection'); go.disabled = !!busy;
    go.onclick = function () { call({ type: 'coll_run', id: col.id }).then(function () { lastCollSig = ''; refresh(); }); };
    acts.appendChild(go);
    if (waiting && waiting.collection.id === col.id) {
      var skip = el('button', { class: 'collskip' }, 'Skip this portal (leave it for me)');
      skip.onclick = function () { call({ type: 'coll_skip', runId: waiting.id }).then(function () { lastCollSig = ''; refresh(); }); };
      acts.appendChild(skip);
    }
    var ps = state.portalStatus || {};
    var nNeed = col.recIds.filter(function (id) { return loginNeeded(res[id], ps[id]) && ps[id] && ps[id].status === 'login'; }).length;
    if (nNeed) {
      var open = el('button', { class: 'collopen primary' }, 'Open the ones that need me (' + nNeed + ')');
      open.onclick = function () { call({ type: 'coll_open_logins', id: col.id }).then(function (r) { notice('Opened ' + r.opened + ' login tab(s). Log in there; AWOS notices.'); }); };
      acts.appendChild(open);
    }
    var chk = el('button', { class: 'collcheck' }, 'Check logins now'); chk.disabled = !!busy;
    chk.onclick = function () {
      notice('Checking each portal in a background tab (nothing is clicked or typed)...');
      call({ type: 'coll_check_logins', id: col.id }).then(function (r) {
        var bad = (r.results || []).filter(function (x) { return x.status === 'login'; }).length;
        notice(bad ? bad + ' portal(s) need a login.' : 'All portals are logged in.'); lastCollSig = ''; refresh();
      });
    };
    acts.appendChild(chk);
    var del = el('button', { class: 'colldel' }, 'Delete');
    del.onclick = function () { if (confirm('Delete the collection "' + col.name + '"? The recipes stay.')) call({ type: 'coll_delete', id: col.id }).then(refresh); };
    acts.appendChild(del);
    li.appendChild(acts);
    var sch = details(col.id + ':csched', 'Schedule' + (col.schedule ? ' (monthly, day ' + col.schedule.date + ' ' + col.schedule.time + ')' : ''));
    sch.appendChild(collSchedBox(col));
    li.appendChild(sch);
    var ka = details(col.id + ':ckeep', 'Logins' + (col.keepAlive === false ? ' (keep-alive off)' : ' (keep-alive every ' + (col.keepAliveDays || 3) + ' days)'));
    ka.appendChild(collKeepBox(col));
    li.appendChild(ka);
    list.appendChild(li);
  });
}
$('collcreate').onclick = function () {
  var ids = []; document.querySelectorAll('#collpick .collrec').forEach(function (i) { if (i.checked) ids.push(i.value); });
  call({ type: 'coll_save', name: $('collname').value.trim(), recIds: ids, monthOffset: $('collprev').checked ? -1 : 0 }).then(function (col) {
    notice('Created "' + col.name + '" with ' + col.recIds.length + ' portal(s).'); $('collname').value = ''; $('collnew').open = false;
    lastCollSig = ''; lastPickSig = ''; refresh();
  });
};

// ---- Tell it ---------------------------------------------------------------------------------------------
var EXAMPLES = [
  'Compare the price of the same product in two shops and tell me the cheaper one',
  'Fill this job application up to, not including, Submit',
  'Book the first free appointment slot next week, stop before confirming',
  'Turn off marketing e-mails in my profile settings',
  'Add Grace Hopper (grace@example.com) as a new lead in the CRM',
  'Look up order 4417 in the admin table and tell me its status',
  'Check whether train IC 512 is on time today',
  'Collect the opening hours, phone number and address from this site',
  'Draft a listing for a used bike, do not post it',
  'Unsubscribe me from this newsletter',
];
var tell = null, tellCfg = null, lastTellSig = '', tellConfirmFor = '';
$('tellexample').textContent = 'e.g. ' + EXAMPLES[Math.floor(Math.random() * EXAMPLES.length)];

function renderTell() {
  var t = tell, busy = t && t.status !== 'ended';
  $('tellbar').hidden = !!busy;
  $('tellpanel').hidden = !t;
  if (tellCfg) {
    $('tellbrain').textContent = tellCfg.agentBrain === 'claude'
      ? 'Brain: Claude (' + tellCfg.agentClaudeModel + ', your key). Each step sends the page text and button names to Anthropic. Change in Options.'
      : 'Brain: local model on this computer (' + tellCfg.agentModel + '). Page data never leaves this Mac.';
  }
  if (!t) return;
  var sig = JSON.stringify([t.status, t.feed.length, t.pending && t.pending.nonce, t.takeover]);
  if (sig === lastTellSig) return;
  lastTellSig = sig;
  $('tellgoalshow').textContent = t.goal;
  var label = t.status === 'ended' ? (t.outcome || 'ended').toUpperCase() : t.status.replace('_', ' ').toUpperCase();
  $('tellstatus').textContent = label; $('tellstatus').className = 'badge ' + (t.outcome === 'verified' ? 'VERIFIED' : t.status === 'ended' ? 'FAILED' : '');
  var ol = $('tellfeed'); ol.textContent = '';
  t.feed.slice(-30).forEach(function (f) {
    var li = el('li', { class: f.kind }, f.kind === 'step' ? 'step ' + f.k + ': ' + f.text : f.text);
    if (f.reason) li.appendChild(el('span', { class: 'why' }, f.reason));
    ol.appendChild(li);
  });
  ol.scrollTop = ol.scrollHeight;
  var p = t.pending, isConfirm = t.status === 'confirm';
  $('tellpending').hidden = !p || isConfirm;
  if (p && !isConfirm) {
    $('tellpendingmsg').textContent = p.message;
    $('tellapprove').hidden = $('telldecline').hidden = t.status !== 'awaiting_approval';
    $('tellapprove').textContent = 'Approve "' + (p.target || '').slice(0, 40) + '"';
    $('tellcontinue').hidden = t.status !== 'needs_user';
  }
  $('tellconfirm').hidden = !isConfirm;
  if (isConfirm && tellConfirmFor !== p.nonce) {            // fill the editable proposal once per confirmation
    tellConfirmFor = p.nonce;
    var r = t.result || {}, pr = r.proposed || {};
    $('tellanswer').textContent = 'It says: ' + (r.answer || 'done');
    $('tellverifier').textContent = r.verifier ? (r.verifier.label ? 'Verifier: ' + r.verifier.label + ' ' + (r.verifier.reason || '') : 'Verifier: ' + (r.verifier.error || '')) : '';
    $('tellname').value = t.goal.slice(0, 80);
    $('tellcheckurl').value = pr.urlPattern || '';
    $('tellchecktext').value = (pr.texts || []).join(', ');
  }
  var live = /^(running|paused|awaiting_approval|needs_user)$/.test(t.status);
  $('tellpause').hidden = t.status !== 'running';
  $('tellresume').hidden = t.status !== 'paused';
  $('telltakeover').hidden = !live || !!t.takeover;
  $('tellstop').hidden = !live && !isConfirm;
  $('tellclose').hidden = t.status !== 'ended';
  $('tellnotebar').hidden = !live;
  $('tellreason').textContent = t.status === 'ended' ? t.reason || '' : '';
}
function tellDecideMsg(kind) {
  var p = tell && tell.pending; if (!p) return;
  var m = { type: kind, nonce: p.nonce };
  var note = $('tellnote').value.trim(); if (note && kind !== 'tell_approve') { m.note = note; $('tellnote').value = ''; }
  call(m).then(refresh);
}
$('tellgo').onclick = function () {
  call({ type: 'tell_start', tabId: targetTab, goal: $('tellgoal').value.trim(), startUrl: $('tellurl').value.trim() })
    .then(function () { $('tellgoal').value = ''; lastTellSig = ''; refresh(); });
};
$('tellpause').onclick = function () { call({ type: 'tell_pause' }).then(refresh); };
$('tellresume').onclick = function () { call({ type: 'tell_resume' }).then(refresh); };
$('telltakeover').onclick = function () { call({ type: 'tell_takeover' }).then(refresh); };
$('tellstop').onclick = function () { call({ type: 'tell_stop' }).then(refresh); };
$('tellclose').onclick = function () { call({ type: 'tell_clear' }).then(function () { lastTellSig = ''; refresh(); }); };
$('tellapprove').onclick = function () { tellDecideMsg('tell_approve'); };
$('telldecline').onclick = function () { tellDecideMsg('tell_decline'); };
$('tellcontinue').onclick = function () { tellDecideMsg('tell_continue'); };
$('tellnotego').onclick = function () {
  var v = $('tellnote').value.trim(); if (!v) return;
  if (tell && tell.status === 'needs_user') return tellDecideMsg('tell_continue');
  call({ type: 'tell_note', text: v }).then(function () { $('tellnote').value = ''; refresh(); });
};
$('tellyes').onclick = function () {
  var p = tell && tell.pending; if (!p) return;
  call({ type: 'tell_confirm', nonce: p.nonce, yes: true, name: $('tellname').value.trim(),
         expect: { urlPattern: $('tellcheckurl').value.trim(), texts: $('tellchecktext').value.split(',').map(function (x) { return x.trim(); }).filter(Boolean) } })
    .then(function (rec) { notice('Saved "' + rec.name + '". Run it from Recordings any time.'); lastSig = ''; refresh(); });
};
$('tellno').onclick = function () { var p = tell && tell.pending; if (p) call({ type: 'tell_confirm', nonce: p.nonce, yes: false }).then(refresh); };
$('exporttraining').onclick = function () {
  if (!confirm('Save your Tell-it runs as a file on this computer? It contains the page text the agent saw on each step. Nothing is uploaded.')) return;
  call({ type: 'tell_export_training' }).then(function (d) {
    download('awos-tellit-training-' + new Date().toISOString().slice(0, 10) + '.json', JSON.stringify(d, null, 1));
  });
};

async function refresh() {
  state = await call({ type: 'get_state' });
  var ts = await call({ type: 'tell_state' });
  tell = ts.tell; tellCfg = ts.settings;
  render(); renderTell();
}

$('rec').onclick = function () { call({ type: 'start_recording', tabId: targetTab }).then(refresh); };
$('stop').onclick = function () {
  call({ type: 'stop_recording' }).then(function (rec) { if (rec && rec.warning) notice(rec.warning); return refresh(); });
};
function decide(kind) {
  var run = state.run; if (!run || !run.pending) return;
  var msg = { type: kind, runId: run.id, nonce: run.pending.nonce };
  if (kind === 'continue' && run.pending.needParams) {
    msg.params = {};
    document.querySelectorAll('#needparams .needval').forEach(function (i) { msg.params[i.getAttribute('data-param')] = i.value.trim(); });
  }
  call(msg).then(refresh);
}
$('approve').onclick = function () { decide('approve'); };
$('continue').onclick = function () { decide('continue'); };
$('reject').onclick = function () { decide('reject'); };
$('export').onclick = function () {
  call({ type: 'export_log' }).then(function (log) {
    download('awos-runlog-' + new Date().toISOString().slice(0, 10) + '.json', JSON.stringify(log, null, 1));
  });
};
$('imp-ok').onclick = function () {
  var p = state.pendingImport; if (!p) return;
  call({ type: 'import_confirm', nonce: p.nonce }).then(function (rec) { notice('Imported "' + rec.name + '".'); lastSig = ''; refresh(); });
};
$('imp-no').onclick = function () { call({ type: 'import_discard' }).then(refresh); };
function offer(msg) {          // not call(): an invalid recipe is a normal reply with .error, shown as such
  return chrome.runtime.sendMessage(msg).then(function (r) {
    if (!r || !r.ok) $('error').textContent = 'Not a valid recipe: ' + ((r && r.error) || 'no reply');
    else { $('error').textContent = ''; refresh(); }
  });
}
$('implinkgo').onclick = function () { offer({ type: 'import_offer', link: $('implink').value.trim() }); };
$('impfile').onchange = function () {
  var f = $('impfile').files[0]; if (!f) return;
  if (f.size > 262144) { $('error').textContent = 'That file is too big for a recipe (64 KB max).'; return; }
  f.text().then(function (t) { return offer({ type: 'import_offer', text: t }); });
  $('impfile').value = '';
};

async function brainCheck() {
  var b = await chrome.runtime.sendMessage({ type: 'brain_status' }).catch(function () { return null; });   // its reply carries .error on purpose
  var w = $('brainwarn');
  if (!b || b.ok) { w.hidden = true; return; }
  w.hidden = false; w.textContent = '';
  w.appendChild(document.createTextNode(b.brain === 'local'
    ? 'Local model is not running, so AWOS cannot re-find moved buttons or double-check results. Start it with bash scripts/awos_model.sh, or '
    : b.error + '. '));
  var a = el('a', { href: 'options.html', target: '_blank' }, b.brain === 'local' ? 'use Claude with your own API key' : 'Open Options');
  w.appendChild(a);
}

(async function () {
  // start loading the local agent model now: it unloads when idle, and a cold load takes seconds to minutes
  call({ type: 'tell_warm' }).catch(function () {});
  targetTab = await resolveTab();
  await refresh();
  brainCheck();
  setInterval(function () { refresh().catch(function () {}); }, 500);
})();
