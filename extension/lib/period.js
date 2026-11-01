/* AWOS Replay: period templating for goal-guided runs (v0.7, docs/specs/goal_guided.md).
 *
 * A monthly portal recipe recorded in October clicked "Invoice September 2026". Replaying that literally next month
 * downloads September again. At save / import time this file finds period expressions in a recording and stores
 * them as placeholders relative to the recording's period, so each run substitutes ITS period before locating.
 *
 *  detect(text, period, kind) -> {template, changed, hits[]}
 *      Period expressions found in `text`. Those that name `period` (the recording's month, 'YYYY-MM') become
 *      placeholders in `template`; the others stay literal (an older statement clicked on purpose is not moved).
 *      Recognised: full / short month names + year ("September 2026", "Sep 2026", "Sept. 2026", "Oct-2026",
 *      "2026 September"), YYYY-MM / YYYY/MM / YYYY_MM / YYYY.MM, MM/YYYY / MM-YYYY / MM.YYYY, quarters ("Q3 2026",
 *      "2026-Q3"), and a bare capitalised month name ("Download October payouts"; never May / March, which are
 *      ordinary words). Not periods: order ids (2026-0912, INV-2026-09-0042), dates with a day (2026-09-30,
 *      15/09/2026), prices, phone numbers, plain years.
 *      kind 'url': only the path + query of an http(s) URL is looked at (never the host).
 *  TOKENS: {{year}} {{month}} (09) {{month_n}} (9) {{month_name}} {{month_short}} {{quarter}} (3, after a literal Q),
 *      plus _lower / _upper variants of month_name / month_short so "SEP" and "inv-sep-2026" keep their case.
 *  builtins(period) / substitute(template, period) / label(period) ("October 2026") / periodOf(t, offset) / shift
 *  fields(rec) -> [{path, value, kind}]          the recording fields that are templated
 *  scan(rec, period, prevTpl, onlyPrefix) -> tpl  {path: {template, example, period, on}}; an entry whose field still
 *      holds its example keeps its `on` flag (the user's per-field undo survives a re-scan)
 *  view(rec, period)   -> a copy with every `on` template substituted for `period` (what a run replays)
 *  templatedView(rec)  -> a copy with the templates themselves in the fields (what a shared recipe carries)
 *  fromTokens(rec, period) -> builds rec.tpl from placeholders already in the fields (an imported recipe)
 *  infer(rec, now)     -> {period, inferred}: the recording's period, from the expressions it contains (one period
 *      named most often among the last 3 months), else the previous month of `now`
 *  consistent(tpl, stepIndex, desc, period) -> {ok, checked, good, bad}: does a found control name the run's period
 *      in the fields that were templated (no other period)? A link "Invoice October 2026" whose file is
 *      /files/invoice-2026-09.pdf is not consistent with October.
 * Plain script: defines globalThis.AWOSPeriod (service worker, test pages).
 */
(function (root) {
  'use strict';

  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var BUILTIN = ['year', 'month', 'month_n', 'month_name', 'month_name_lower', 'month_name_upper',
                 'month_short', 'month_short_lower', 'month_short_upper', 'quarter'];
  var BUILTIN_SET = {}; BUILTIN.forEach(function (b) { BUILTIN_SET[b] = 1; });
  var TOKEN_G = /\{\{([a-z][a-z0-9_]{0,31})\}\}/g;
  var PERIOD_RE = /^(\d{4})-(0[1-9]|1[0-2])$/;

  function str(v) { return v == null ? '' : String(v); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }

  // ---- periods -----------------------------------------------------------------------------------------------
  function periodOf(t, offset) {
    var d = new Date(t); d.setDate(1); d.setMonth(d.getMonth() + (offset || 0));
    return d.getFullYear() + '-' + pad(d.getMonth() + 1);
  }
  function parse(p) { var m = PERIOD_RE.exec(str(p)); return m ? { y: +m[1], m: +m[2] } : null; }
  function shift(p, n) { var x = parse(p); if (!x) return ''; var k = x.y * 12 + (x.m - 1) + (n || 0); return Math.floor(k / 12) + '-' + pad(k % 12 + 1); }
  function valid(p) { return !!parse(p); }
  function label(p) { var x = parse(p); return x ? MONTHS[x.m - 1] + ' ' + x.y : str(p); }
  function builtins(p) {
    var x = parse(p) || parse(periodOf(Date.now(), 0));
    var name = MONTHS[x.m - 1], sh = SHORT[x.m - 1];
    return { year: String(x.y), month: pad(x.m), month_n: String(x.m), month_name: name, month_name_lower: name.toLowerCase(),
             month_name_upper: name.toUpperCase(), month_short: sh, month_short_lower: sh.toLowerCase(), month_short_upper: sh.toUpperCase(),
             quarter: String(Math.floor((x.m - 1) / 3) + 1) };
  }
  /** skip: {name: 1} of the recipe's own params ({{month}} declared as a param is the user's value, not the period's). */
  function substitute(text, p, skip) {
    var b = builtins(p);
    return str(text).replace(TOKEN_G, function (all, n) { return BUILTIN_SET[n] && !(skip && skip[n]) ? b[n] : all; });
  }
  function hasTokens(text, skip) {
    TOKEN_G.lastIndex = 0; var m, s = str(text);
    while ((m = TOKEN_G.exec(s))) if (BUILTIN_SET[m[1]] && !(skip && skip[m[1]])) return true;
    return false;
  }
  function ownParams(rec) { var o = {}; ((rec && rec.params) || []).forEach(function (q) { if (q && q.name) o[q.name] = 1; }); return o; }

  // ---- the detector ------------------------------------------------------------------------------------------
  var FULL_ALT = MONTHS.join('|'), SHORT_ALT = 'Sept|' + SHORT.join('|');
  var MON = '(' + FULL_ALT + '|' + SHORT_ALT + ')';
  var YEAR = '((?:19|20)\\d{2})';
  var PATTERNS = [
    // month name (+ optional dot) + separator + year: "September 2026", "Sep. 2026", "Oct-2026", "sep_2026"
    { kind: 'my', re: new RegExp('(?<![A-Za-z])' + MON + '(?![A-Za-z])(\\.?)([ \\-_/,]{0,3})' + YEAR + '(?!\\d)', 'gi') },
    // year + separator + month name: "2026 September", "2026-Sep"
    { kind: 'ym', re: new RegExp('(?<![\\d])' + YEAR + '([ \\-_/,]{1,3})' + MON + '(?![A-Za-z])', 'gi') },
    // quarters: "Q3 2026", "q3-2026", "2026 Q3", "2026-Q3"
    { kind: 'qy', re: new RegExp('(?<![A-Za-z0-9])([Qq])([1-4])([ \\-_/]{0,2})' + YEAR + '(?!\\d)', 'g') },
    { kind: 'yq', re: new RegExp('(?<!\\d)' + YEAR + '([ \\-_/]{0,2})([Qq])([1-4])(?![0-9])', 'g') },
    // YYYY-MM / YYYY/MM / YYYY_MM / YYYY.MM, not part of a longer number or a date with a day
    { kind: 'yn', re: new RegExp('(?<![\\d.,])(?<!\\d[-/])' + YEAR + '([-/_.])(0[1-9]|1[0-2])(?!\\d)(?![-/.]\\d)', 'g') },
    // MM/YYYY / MM-YYYY / MM.YYYY: not after a day ("15/09/2026"), a digit, a letter (v1.2026) or a currency sign ($12.2026)
    { kind: 'ny', re: new RegExp('(?<![A-Za-z0-9.,:$\u20ac\u00a3#])(?<!\\d[-/.])(0?[1-9]|1[0-2])([-/.])' + YEAR + '(?!\\d)(?![-/.]\\d)', 'g') },
    // a bare capitalised month name (no year): "Download October payouts". May / March are ordinary words.
    { kind: 'm', re: /(?<![A-Za-z])(January|February|April|June|July|August|September|October|November|December)(?![A-Za-z])/g },
  ];
  function monthIndex(word) {
    var w = word.toLowerCase().replace(/\.$/, '');
    if (w === 'sept') return 9;
    for (var i = 0; i < 12; i++) if (MONTHS[i].toLowerCase() === w || SHORT[i].toLowerCase() === w) return i + 1;
    return 0;
  }
  function nameToken(word) {
    var full = word.length > 4 || MONTHS.some(function (m) { return m.toLowerCase() === word.toLowerCase(); });
    var base = full ? 'month_name' : 'month_short';
    if (word.length > 1 && word === word.toUpperCase()) return '{{' + base + '_upper}}';
    if (word === word.toLowerCase()) return '{{' + base + '_lower}}';
    return '{{' + base + '}}';
  }
  /** one match -> {start, end, text, year, month, quarter, tpl} */
  function hitOf(kind, m) {
    var h = { start: m.index, end: m.index + m[0].length, text: m[0], year: 0, month: 0, quarter: 0, tpl: '' };
    if (kind === 'my') { h.month = monthIndex(m[1]); h.year = +m[4]; h.tpl = nameToken(m[1]) + m[2] + m[3] + '{{year}}'; }
    else if (kind === 'ym') { h.year = +m[1]; h.month = monthIndex(m[3]); h.tpl = '{{year}}' + m[2] + nameToken(m[3]); }
    else if (kind === 'qy') { h.quarter = +m[2]; h.year = +m[4]; h.tpl = m[1] + '{{quarter}}' + m[3] + '{{year}}'; }
    else if (kind === 'yq') { h.year = +m[1]; h.quarter = +m[4]; h.tpl = '{{year}}' + m[2] + m[3] + '{{quarter}}'; }
    else if (kind === 'yn') { h.year = +m[1]; h.month = +m[3]; h.tpl = '{{year}}' + m[2] + '{{month}}'; }
    else if (kind === 'ny') { h.month = +m[1]; h.year = +m[3]; h.tpl = (m[1].length === 2 ? '{{month}}' : '{{month_n}}') + m[2] + '{{year}}'; }
    else { h.month = monthIndex(m[1]); h.tpl = nameToken(m[1]); }
    if (h.year && h.month) h.period = h.year + '-' + pad(h.month);
    return h;
  }
  /** does hit h name period p? (a bare month: same month; a quarter: same quarter and year) */
  function names(h, p) {
    var x = parse(p); if (!x) return false;
    if (h.quarter) return h.year === x.y && h.quarter === Math.floor((x.m - 1) / 3) + 1;
    if (!h.year) return h.month === x.m;
    return h.year === x.y && h.month === x.m;
  }
  /** spans of {{tokens}} already in the text: never matched again. */
  function tokenSpans(s) { var out = [], m; TOKEN_G.lastIndex = 0; while ((m = TOKEN_G.exec(s))) out.push([m.index, m.index + m[0].length]); return out; }
  function hitsIn(s) {
    var all = [], toks = tokenSpans(s);
    PATTERNS.forEach(function (p) {
      p.re.lastIndex = 0; var m;
      while ((m = p.re.exec(s))) {
        var h = hitOf(p.kind, m);
        if (!toks.some(function (t) { return h.start < t[1] && t[0] < h.end; })) all.push(h);
        if (m[0].length === 0) p.re.lastIndex++;
      }
    });
    all.sort(function (a, b) { return a.start - b.start || (b.end - b.start) - (a.end - a.start); });
    var out = [], end = -1;
    all.forEach(function (h) { if (h.start >= end) { out.push(h); end = h.end; } });
    return out;
  }
  /** the part of a URL that may name a period: path + query (never scheme / host). */
  function urlSplit(u) { var m = /^(https?:\/\/[^/?#]*)(.*)$/i.exec(str(u)); return m ? [m[1], m[2]] : ['', str(u)]; }

  function detect(text, p, kind) {
    var s = str(text), head = '', body = s;
    if (kind === 'url') { var sp = urlSplit(s); head = sp[0]; body = sp[1]; }
    var hits = hitsIn(body), out = '', at = 0, changed = false;
    hits.forEach(function (h) {
      h.templated = !!p && names(h, p);
      if (!h.templated) return;
      out += body.slice(at, h.start) + h.tpl; at = h.end; changed = true;
    });
    out += body.slice(at);
    return { template: head + out, changed: changed, hits: hits };
  }
  function periodsIn(text, kind) { return detect(text, null, kind).hits; }

  // ---- recordings ----------------------------------------------------------------------------------------------
  var LOC_KEYS = ['name', 'text', 'label', 'title', 'href', 'placeholder', 'neighbor', 'block', 'context', 'id'];
  var IDENT_KEYS = { name: 1, text: 1, label: 1, title: 1, href: 1, id: 1 };
  function isSecretValue(v) { return /^\{\{secret:/.test(str(v)); }
  function fields(rec) {
    var out = [];
    function add(path, v, kind) { if (typeof v === 'string' && v) out.push({ path: path, value: v, kind: kind || 'text' }); }
    add('goal', rec.goal);
    (rec.steps || []).forEach(function (s, k) {
      var pre = 'steps.' + k + '.';
      add(pre + 'url', s.url, 'url');
      if (s.loc) LOC_KEYS.forEach(function (key) { add(pre + 'loc.' + key, s.loc[key], key === 'href' ? 'url' : 'text'); });
      if (s.action === 'fill' && !s.param && !s.ask && !s.sensitive && !isSecretValue(s.value)) add(pre + 'value', s.value);
      if (s.action === 'select' && !s.param) { add(pre + 'value', s.value); add(pre + 'optionText', s.optionText); }
      if (s.download) add(pre + 'download.name', s.download.name);
    });
    var ex = rec.expect || {};
    (ex.texts || []).forEach(function (t, k) { add('expect.texts.' + k, t); });
    add('expect.urlPattern', ex.urlPattern, 'url');
    if (rec.expectDownload) add('expectDownload.nameRegex', rec.expectDownload.nameRegex);
    return out;
  }
  function getPath(o, path) {
    var ks = path.split('.');
    for (var i = 0; i < ks.length; i++) { if (o == null) return undefined; o = o[ks[i]]; }
    return o;
  }
  function setPath(o, path, v) {
    var ks = path.split('.');
    for (var i = 0; i < ks.length - 1; i++) { if (o[ks[i]] == null) return false; o = o[ks[i]]; }
    o[ks[ks.length - 1]] = v; return true;
  }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function scan(rec, p, prev, onlyPrefix) {
    var tpl = {};
    prev = prev || rec.tpl || {};
    if (onlyPrefix) Object.keys(prev).forEach(function (k) { if (k.indexOf(onlyPrefix) !== 0) tpl[k] = prev[k]; });
    fields(rec).forEach(function (f) {
      if (onlyPrefix && f.path.indexOf(onlyPrefix) !== 0) return;
      var old = prev[f.path];
      if (old && old.example === f.value && old.period === p) { tpl[f.path] = old; return; }
      var d = detect(f.value, p, f.kind);
      if (d.changed) tpl[f.path] = { template: d.template, example: f.value, period: p, on: old && old.example === f.value ? old.on !== false : true };
    });
    return tpl;
  }
  function view(rec, p) {
    var out = clone(rec), tpl = rec.tpl || {}, own = ownParams(rec);
    Object.keys(tpl).forEach(function (path) {
      var e = tpl[path];
      if (e && e.on !== false && getPath(out, path) === e.example) setPath(out, path, substitute(e.template, p, own));
    });
    return out;
  }
  function templatedView(rec) {
    var out = clone(rec), tpl = rec.tpl || {};
    Object.keys(tpl).forEach(function (path) {
      var e = tpl[path];
      if (e && e.on !== false && getPath(out, path) === e.example) setPath(out, path, e.template);
    });
    delete out.tpl;
    return out;
  }
  function fromTokens(rec, p) {
    var tpl = rec.tpl || {}, own = ownParams(rec);
    fields(rec).forEach(function (f) {
      if (!hasTokens(f.value, own)) return;
      var ex = substitute(f.value, p, own);
      tpl[f.path] = { template: f.value, example: ex, period: p, on: true };
      setPath(rec, f.path, ex);
    });
    rec.tpl = tpl;
    return rec;
  }
  function infer(rec, now) {
    var cur = periodOf(now, 0), lo = shift(cur, -3), counts = {};
    fields(rec).forEach(function (f) {
      periodsIn(f.value, f.kind).forEach(function (h) { if (h.period && h.period >= lo && h.period <= cur) counts[h.period] = (counts[h.period] || 0) + 1; });
    });
    var best = '', n = 0, tie = false;
    Object.keys(counts).forEach(function (k) { if (counts[k] > n) { best = k; n = counts[k]; tie = false; } else if (counts[k] === n) tie = true; });
    if (best && !tie) return { period: best, inferred: true, mode: best === cur ? 'this' : 'previous' };
    return { period: shift(cur, -1), inferred: false, mode: 'previous' };
  }
  /** a found control vs the step's templated identity fields: names the run's period, and no other one. */
  function consistent(tpl, k, desc, p) {
    var pre = 'steps.' + k + '.loc.', out = { checked: 0, good: 0, bad: 0, why: [] };
    Object.keys(tpl || {}).forEach(function (path) {
      if (path.indexOf(pre) !== 0) return;
      var key = path.slice(pre.length), e = tpl[path];
      if (!IDENT_KEYS[key] || !e || e.on === false) return;
      var v = str(desc && desc[key]);
      if (!v) return;
      out.checked++;
      var hs = periodsIn(v, key === 'href' ? 'url' : 'text');
      var good = hs.some(function (h) { return names(h, p); });
      var other = hs.filter(function (h) { return !names(h, p) && (h.year || h.month); });
      if (good && !other.length) out.good++;
      else if (other.length) { out.bad++; out.why.push(key + ' "' + v.slice(0, 60) + '" names ' + other.map(function (h) { return h.text; }).join(', ')); }
      else out.why.push(key + ' "' + v.slice(0, 60) + '" names no period');
    });
    out.ok = out.checked === 0 || (out.bad === 0 && out.good > 0);
    return out;
  }
  /** the step has templated fields (its target depends on the period). */
  function stepTemplated(tpl, k) {
    var pre = 'steps.' + k + '.';
    return Object.keys(tpl || {}).some(function (path) { return path.indexOf(pre) === 0 && tpl[path].on !== false; });
  }

  root.AWOSPeriod = {
    MONTHS: MONTHS, SHORT: SHORT, BUILTIN: BUILTIN, BUILTIN_SET: BUILTIN_SET,
    periodOf: periodOf, shift: shift, valid: valid, label: label, builtins: builtins, substitute: substitute, hasTokens: hasTokens,
    detect: detect, periodsIn: periodsIn, names: names, fields: fields, scan: scan, view: view, templatedView: templatedView,
    fromTokens: fromTokens, infer: infer, consistent: consistent, stepTemplated: stepTemplated, getPath: getPath, setPath: setPath,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
