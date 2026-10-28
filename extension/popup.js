/* AWOS Replay popup: record/stop, recordings list (run with params, checks, params, schedule, share, delete),
 * live run panel with the approval / needs-user prompt and the verdict, and the import confirmation.
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
    var name = el('input', { class: 'pname', placeholder: 'name (e.g. month)', value: guess });
    var label = el('input', { class: 'plabel', placeholder: 'Question (e.g. Which month)', value: stepLabel(s) });
    var example = el('input', { class: 'pexample', placeholder: 'Example shown to others (e.g. October 2026)' });
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
    var meta = acts + ' actions, from ' + r.startUrl;
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
    var goal = el('input', { class: 'goal', placeholder: 'Goal: what must be true at the end (e.g. October statement downloaded)', value: r.goal || '' });
    var texts = el('input', { class: 'texts', placeholder: 'Expect text on final page (comma separated)', value: (r.expect.texts || []).join(', ') });
    var url = el('input', { class: 'url', placeholder: 'Final URL (origin+path, * allowed; blank = no check)', value: r.expect.urlPattern || '' });
    var dl = el('input', { class: 'dl', placeholder: 'Expect a download named like (regex, e.g. statement.*\\.pdf)', value: (r.expectDownload && r.expectDownload.nameRegex) || '' });
    var save = el('button', { class: 'save' }, 'Save goal + checks');
    save.onclick = function () {
      call({ type: 'save_recording', id: r.id, goal: goal.value.trim(), expectDownload: { nameRegex: dl.value.trim() },
             expect: { urlPattern: url.value.trim(), texts: texts.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean) } }).then(refresh);
    };
    [goal, texts, url, dl, save].forEach(function (x) { chk.appendChild(x); });
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
  renderImport(); renderRecordings(); renderRun();
}

async function refresh() { state = await call({ type: 'get_state' }); render(); }

$('rec').onclick = function () { call({ type: 'start_recording', tabId: targetTab }).then(refresh); };
$('stop').onclick = function () { call({ type: 'stop_recording' }).then(refresh); };
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
  targetTab = await resolveTab();
  await refresh();
  brainCheck();
  setInterval(function () { refresh().catch(function () {}); }, 500);
})();
