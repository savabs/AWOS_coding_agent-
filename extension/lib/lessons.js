/* AWOS Replay: per-site lessons learned from runs (v0.10, docs/research/experience_reuse_2026-11-05.md).
 *
 * The user says "use the Settings tab, not Profile" after a run went wrong. Next time the small model is on that
 * site, in that situation, it should be told so, briefly. This file keeps those lessons as a plain ledger array
 * (the caller stores it in chrome.storage.local) and decides which few to inject. PURE: no storage, no DOM, no
 * network; every function returns a new array / new objects and never mutates its input.
 *
 * Rules from the research note: learn only from explicit user input or VERIFIED runs; inject little (k = 2);
 * describe things by meaning (role / accessible name / region), never ids; never keep secrets or typed values.
 *
 *  item = {id, host, state?, goal?, when, do, avoid, src: 'user'|'verified'|'model', confirmed, created, used,
 *          hits, wins, losses, streakLoss, disabled, why?}
 *      when: a short description of the situation ('on Settings dialog for "October invoice"'); do / avoid: what to
 *      do / not do, in words; state: the site-map state key the lesson was learned in; used: last time injected.
 *  fromCorrection(text, ctx{host, stateKey, stateLabel?, goal, now}) -> item | null   rule-based parse:
 *      "use the Settings tab, not Profile"  -> do 'Settings tab', avoid 'Profile'
 *      "click X instead of Y" / "X rather than Y" / "instead of Y, click X" / "X, not Y"
 *      "don't click Y" / "never open Y" / "avoid Y"   -> avoid only
 *      "X is under the profile menu"        -> do 'open the profile menu first, then X'
 *      anything else                        -> do = the trimmed text
 *  make(fields, now) -> item        normalise any other lesson (src 'verified' / 'model'; model ones unconfirmed)
 *  add(ledger, item) -> ledger      near-identical do / avoid on the same host merge into the old item (a user
 *      re-stating a disabled lesson re-enables it); at most CAP (20) items per host: disabled ones are dropped
 *      first, then the lowest utility (wins - losses), unconfirmed model ones and older ones before others
 *  retrieve(ledger, {host, stateKey, goal}, k = 2) -> items   same host, enabled, model-sourced only if confirmed;
 *      ranked by state match > goal token overlap > utility > recency
 *  render(items) -> text            "Rules learned on this site (from earlier runs):\n- When W: do D. Avoid: A." ('' if none)
 *  score(ledger, injectedIds, verdict, now?) -> ledger   hits + 1 for each injected; VERIFIED: wins + 1, streak 0;
 *      FAILED / halted: losses + 1, streakLoss + 1; UNVERIFIED / CHECK: hits only
 *  prune(ledger, now) -> ledger     disable (with a `why`) when streakLoss >= 2, or hits >= 3 and wins / hits < 0.4,
 *      or not used for more than 60 days
 *  list(ledger, host?) / remove(ledger, id) / setDisabled(ledger, id, bool)   for the options UI
 * Plain script: defines globalThis.AWOSLessons (service worker, test pages).
 */
(function (root) {
  'use strict';

  var CAP = 20;                       // lessons kept per host
  var K = 2;                          // lessons injected per prompt
  var DAY = 24 * 3600 * 1000;
  var STALE_DAYS = 60;
  var MAX_TEXT = 160;                 // a lesson is a sentence, not a page
  var SAME = 0.7;                     // token overlap (Jaccard) above which two do / avoid parts are the same lesson
  var STOP = {};
  ('the a an to of on in at for and or then first this that it its is be button link tab menu item please just '
   + 'click press tap open use choose pick select go hit').split(' ').forEach(function (w) { STOP[w] = 1; });

  function str(v) { return v == null ? '' : String(v); }
  function trim(s) { return str(s).replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, ''); }
  function clip(s, n) { s = trim(s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function copy(it) { var o = {}; for (var k in it) if (Object.prototype.hasOwnProperty.call(it, k)) o[k] = it[k]; return o; }
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : 0; }

  // ---- masking: a lesson never carries a URL, an email, an account number or a token ---------------------------
  var URL_RE = /\b(?:https?:\/\/|www\.)\S+/gi;
  var EMAIL_RE = /\b[^\s@]+@[^\s@]+\.[a-z]{2,}\b/gi;
  var LONGNUM_RE = /\d(?:[\s\-]?\d){5,}/g;                // 6+ digits (card / account / order numbers, codes)
  var SECRETISH = /(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{16,}(?![A-Za-z0-9_\-])/g;
  function mask(text) {
    return str(text).replace(URL_RE, '«link»').replace(EMAIL_RE, '«email»').replace(SECRETISH, '«masked»')
      .replace(LONGNUM_RE, '«number»');
  }

  // ---- tokens --------------------------------------------------------------------------------------------------
  function tokens(s) {
    var out = {}, ws = str(s).toLowerCase().match(/[a-z0-9]+/g) || [];
    for (var i = 0; i < ws.length; i++) if (!STOP[ws[i]] && ws[i].length > 1) out[ws[i]] = 1;
    return out;
  }
  function jaccard(a, b) {
    var ka = Object.keys(a), kb = Object.keys(b);
    if (!ka.length && !kb.length) return 1;
    if (!ka.length || !kb.length) return 0;
    var inter = 0; for (var i = 0; i < ka.length; i++) if (b[ka[i]]) inter++;
    return inter / (ka.length + kb.length - inter);
  }
  /** Share of the query's tokens found in `b` (goal overlap: a long lesson should not be punished). */
  function coverage(q, b) {
    var kq = Object.keys(q); if (!kq.length) return 0;
    var n = 0; for (var i = 0; i < kq.length; i++) if (b[kq[i]]) n++;
    return n / kq.length;
  }

  function hashId(s) {
    var h = 5381; s = str(s);
    for (var i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
    return 'l_' + h.toString(36);
  }

  // ---- parsing corrections -------------------------------------------------------------------------------------
  var VERB = '(?:use|click(?:\\s+on)?|press|tap|open|choose|pick|select|go\\s+to|hit|try)';
  // a target as the user wrote it: drop a leading verb / article, surrounding quotes and trailing punctuation
  function target(s) {
    s = trim(s).replace(/[.!;]+$/, '');
    s = s.replace(new RegExp('^' + VERB + '\\s+', 'i'), '').replace(/^(?:the|a|an)\s+/i, '');
    s = s.replace(/^["'“”‘’`]+|["'“”‘’`]+$/g, '');
    return trim(s);
  }
  var CORRECTIONS = [
    // "instead of Y, click X"
    [/^instead\s+of\s+(.+?)\s*,\s*(.+)$/i, function (m) { return { do: target(m[2]), avoid: target(m[1]) }; }],
    // "use X, not Y" / "click X instead of Y" / "X rather than Y" / "X and not Y"
    [/^(.+?)\s*,?\s+(?:and\s+)?(?:not|instead\s+of|rather\s+than)\s+(.+)$/i, function (m) {
      return /^(?:do|does|did|is|are|should|it|you|i|we)$/i.test(trim(m[1])) ? null : { do: target(m[1]), avoid: target(m[2]) };
    }],
    // "don't click Y" / "do not open Y" / "never use Y" / "avoid Y" / "stop clicking Y"
    [/^(?:don'?t|do\s+not|never|stop|avoid)\s+(?:(?:click|press|tap|open|use|choose|pick|select|go\s+to|hit|touch)(?:ing)?\s+(?:on\s+)?)?(.+)$/i,
     function (m) { return { do: '', avoid: target(m[1]) }; }],
    // "X is under the profile menu" / "X is in / inside / behind Y" / "X lives under Y"
    [/^(.+?)\s+(?:is|are|lives?|sits?)\s+(?:located\s+)?(?:under|in|inside|behind|within)\s+(.+)$/i, function (m) {
      var where = trim(m[2]).replace(/[.!;]+$/, '');
      if (!/^(?:the|a|an|your|my)\s/i.test(where)) where = 'the ' + where;
      return { do: 'open ' + where + ' first, then ' + target(m[1]), avoid: '' };
    }],
  ];

  function parse(text) {
    var t = trim(text).replace(/^(?:please|no|nope|wrong)[,!.:]?\s+/i, '').replace(/[.!]+$/, '');
    for (var i = 0; i < CORRECTIONS.length; i++) {
      var m = CORRECTIONS[i][0].exec(t);
      if (!m) continue;
      var r = CORRECTIONS[i][1](m);
      if (r && (r.do || r.avoid)) return r;
    }
    return { do: t, avoid: '' };
  }

  function whenOf(ctx) {
    var label = trim(ctx.stateLabel || ctx.stateKey || ''), goal = clip(mask(ctx.goal), 60);
    // the state label holds a window's title from the page: one that reads like text for AI agents is not repeated
    var inj = root.AWOSAgent && root.AWOSAgent.INJECTION_RE;
    if (inj && inj.test(label)) label = '';
    var w = label ? 'on ' + clip(mask(label), 50) : 'on this site';
    if (goal) w += ' for "' + goal.replace(/"/g, "'") + '"';
    return w;
  }

  /** A fresh item (all counters zero) from `fields`; text fields masked and clipped. */
  function make(fields, now) {
    fields = fields || {};
    now = typeof now === 'number' ? now : (typeof fields.created === 'number' ? fields.created : Date.now());
    var src = fields.src === 'user' || fields.src === 'verified' ? fields.src : 'model';
    var it = {
      host: trim(fields.host).toLowerCase(),
      state: fields.state ? clip(fields.state, 120) : '',
      goal: fields.goal ? clip(mask(fields.goal), 120) : '',
      when: clip(mask(fields.when || whenOf({ stateKey: fields.state, goal: fields.goal })), 120),
      do: clip(mask(fields.do), MAX_TEXT),
      avoid: clip(mask(fields.avoid), MAX_TEXT),
      src: src,
      confirmed: src === 'model' ? !!fields.confirmed : true,
      created: now, used: now, hits: 0, wins: 0, losses: 0, streakLoss: 0, disabled: false,
    };
    if (!it.host || (!it.do && !it.avoid)) return null;
    it.id = fields.id || hashId(it.host + '|' + it.do + '|' + it.avoid + '|' + now);
    return it;
  }

  function fromCorrection(text, ctx) {
    ctx = ctx || {};
    var t = trim(mask(text));
    if (!t || !trim(ctx.host)) return null;
    var p = parse(t);
    return make({ host: ctx.host, state: ctx.stateKey, goal: ctx.goal, when: whenOf(ctx), do: p.do, avoid: p.avoid,
                  src: 'user', confirmed: true }, typeof ctx.now === 'number' ? ctx.now : Date.now());
  }

  // ---- ledger --------------------------------------------------------------------------------------------------
  function utility(it) { return num(it.wins) - num(it.losses); }
  function same(a, b) {
    return a.host === b.host && jaccard(tokens(a.do), tokens(b.do)) >= SAME && jaccard(tokens(a.avoid), tokens(b.avoid)) >= SAME;
  }
  // eviction order: disabled first, then lowest utility, unconfirmed model before others, then least recently used
  function evictRank(a, b) {
    if (!!a.disabled !== !!b.disabled) return a.disabled ? -1 : 1;
    if (utility(a) !== utility(b)) return utility(a) - utility(b);
    var ma = a.src === 'model' && !a.confirmed, mb = b.src === 'model' && !b.confirmed;
    if (ma !== mb) return ma ? -1 : 1;
    return num(a.used || a.created) - num(b.used || b.created);
  }

  function add(ledger, item) {
    var out = (ledger || []).map(copy);
    if (!item || !item.host) return out;
    item = copy(item);
    var keepId = item.id;
    for (var i = 0; i < out.length; i++) {
      if (!same(out[i], item)) continue;
      var o = out[i];                                   // merge: keep the history, take the newer wording
      if (item.when) o.when = item.when;
      if (item.state) o.state = item.state;
      if (item.goal) o.goal = item.goal;
      if (item.src === 'user' || (item.src === 'verified' && o.src === 'model')) o.src = item.src;
      o.confirmed = !!(o.confirmed || item.confirmed || o.src !== 'model');
      o.used = Math.max(num(o.used), num(item.used || item.created));
      if (item.src === 'user' && o.disabled) { o.disabled = false; o.streakLoss = 0; delete o.why; }
      keepId = o.id;
      item = null;
      break;
    }
    if (item) out.push(item);
    var host = (item || out.filter(function (x) { return x.id === keepId; })[0]).host;
    var mine = out.filter(function (x) { return x.host === host && x.id !== keepId; });
    var over = out.filter(function (x) { return x.host === host; }).length - CAP;
    if (over > 0) {
      var drop = {};
      mine.sort(evictRank).slice(0, over).forEach(function (x) { drop[x.id] = 1; });
      out = out.filter(function (x) { return !drop[x.id]; });
    }
    return out;
  }

  function retrieve(ledger, q, k) {
    q = q || {}; k = typeof k === 'number' ? k : K;
    var host = trim(q.host).toLowerCase(), gt = tokens(q.goal);
    var c = (ledger || []).filter(function (it) {
      return it && it.host === host && !it.disabled && (it.src !== 'model' || it.confirmed);
    }).map(function (it) {
      var st = q.stateKey && it.state && it.state === q.stateKey ? 1 : 0;
      var go = coverage(gt, tokens(it.goal + ' ' + it.when + ' ' + it.do));
      return { it: it, st: st, go: go };
    });
    c.sort(function (a, b) {
      return (b.st - a.st) || (b.go - a.go) || (utility(b.it) - utility(a.it))
        || (num(b.it.used || b.it.created) - num(a.it.used || a.it.created));
    });
    return c.slice(0, Math.max(0, k)).map(function (x) { return copy(x.it); });
  }

  // one line per lesson; quotes / newlines in the stored text cannot break the list shape
  function line(it) {
    var one = function (s) { return clip(str(s).replace(/[\r\n]+/g, ' '), MAX_TEXT).replace(/[.]+$/, ''); };
    var s = '- When ' + one(it.when || 'on this site') + ':';
    if (it.do) s += ' do ' + one(it.do) + '.';
    if (it.avoid) s += ' Avoid: ' + one(it.avoid) + '.';
    return s;
  }
  function render(items) {
    items = (items || []).filter(function (it) { return it && (it.do || it.avoid); });
    if (!items.length) return '';
    return 'Rules learned on this site (from earlier runs):\n' + items.map(line).join('\n');
  }

  function score(ledger, injectedIds, verdict, now) {
    var ids = {}; (injectedIds || []).forEach(function (id) { ids[id] = 1; });
    var v = str(verdict);
    return (ledger || []).map(function (it) {
      if (!ids[it.id]) return copy(it);
      var o = copy(it);
      o.hits = num(o.hits) + 1;
      if (typeof now === 'number') o.used = now;
      if (v === 'VERIFIED') { o.wins = num(o.wins) + 1; o.streakLoss = 0; }
      else if (v === 'FAILED' || v === 'halted') { o.losses = num(o.losses) + 1; o.streakLoss = num(o.streakLoss) + 1; }
      return o;
    });
  }

  function prune(ledger, now) {
    now = typeof now === 'number' ? now : Date.now();
    return (ledger || []).map(function (it) {
      var o = copy(it);
      if (o.disabled) return o;
      var why = '';
      if (num(o.streakLoss) >= 2) why = 'failed ' + num(o.streakLoss) + ' runs in a row';
      else if (num(o.hits) >= 3 && num(o.wins) / num(o.hits) < 0.4) why = 'helped in only ' + num(o.wins) + ' of ' + num(o.hits) + ' runs';
      else if (now - num(o.used || o.created) > STALE_DAYS * DAY) why = 'not used for over ' + STALE_DAYS + ' days';
      if (why) { o.disabled = true; o.why = why; }
      return o;
    });
  }

  function list(ledger, host) {
    var h = host ? trim(host).toLowerCase() : '';
    return (ledger || []).filter(function (it) { return !h || it.host === h; }).map(copy).sort(function (a, b) {
      return a.host < b.host ? -1 : a.host > b.host ? 1 : num(b.created) - num(a.created);
    });
  }
  function remove(ledger, id) { return (ledger || []).filter(function (it) { return it.id !== id; }).map(copy); }
  function setDisabled(ledger, id, off) {
    return (ledger || []).map(function (it) {
      var o = copy(it);
      if (o.id !== id) return o;
      o.disabled = !!off;
      if (off) o.why = o.why || 'turned off by you';
      else { delete o.why; o.streakLoss = 0; }
      return o;
    });
  }

  root.AWOSLessons = {
    CAP: CAP, K: K, STALE_DAYS: STALE_DAYS,
    fromCorrection: fromCorrection, parse: parse, make: make, mask: mask, add: add, retrieve: retrieve, render: render,
    score: score, prune: prune, list: list, remove: remove, setDisabled: setDisabled, tokens: tokens, jaccard: jaccard,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
