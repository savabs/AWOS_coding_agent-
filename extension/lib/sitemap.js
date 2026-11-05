/* AWOS Replay: per-site map of what clicks do (v0.10, docs/research/experience_reuse_2026-11-05.md).
 *
 * "Tell it" used to make the local 4B model explore a site from scratch, and on claude.ai it clicked distractors
 * ("Customize", chat history links) instead of the profile menu -> Settings -> Billing. This file learns WHAT CLICKS
 * DO by watching (recordings and every run; never by exploring a logged-in account on its own) and keeps a small
 * per-site graph of states and transitions the planner can route on. The model only reasons where the map runs out.
 *
 * A STATE is "host/path-pattern|dialog:Name|tab:Name|menu:Name" (only the parts that apply). An EDGE is one control
 * clicked in one state and what it did: {host, from, to, control: {role, name, context, row?, items?}, effect: {kind,
 * label}}. kinds: opened_menu | opened_dialog | closed_dialog | switched_tab | navigated | downloaded | new_tab |
 * showed_items | nothing. Only meaning is stored (role, accessible name, context, a generic item word such as
 * "invoice"): never a field value, a row's text, a file name or an id.
 *
 *  stateKey(x, opts)        x = an observation (AWOSLocator.observe()) or {url, dialog, tab, menu}; dialog may be a
 *      string or a recorded step's {name, tab}. Path segments that look like ids (digits, uuids, long hex / mixed
 *      tokens) become ':id'; the selected tab of an observation is its control with role tab and value '1' (a tab
 *      inside the open dialog first). An observation never knows which menu is open: pass opts.menu (or the previous
 *      effect's `to`) for that.
 *  fromRecording(rec)       -> edges inferred from consecutive recorded steps (clicks only: a typed value or a
 *      chosen option is never a route): a menu item after a button = opened_menu; a recorded step's dialog appearing
 *      = opened_dialog / disappearing = closed_dialog; role tab = switched_tab; navigates / a following goto =
 *      navigated; download = downloaded; opensTab = new_tab; the next control sitting in a list row = showed_items.
 *  effectOf(before, after, act) -> {kind, label, to} from two observations and the acted control {role, name,
 *      download?, newTab?}: a new dialog, a new menu (menu items appeared, or a menu entity in after.entities), a
 *      changed URL path, a changed selected tab, new list rows, a closed dialog, else nothing.
 *  merge(map, edges, outcome, now) -> a NEW map {hosts: {host: {edges: {key: edge+{seen, ok, fail, last, unproven?}},
 *      states: {key: {label, seen}}}}}; outcome 'ok' | 'fail' | 'seen' | 'unproven'. 'seen' = the user showed it (their
 *      own recording); 'unproven' = the model's own click in Tell it, or someone else's imported recipe: stored with
 *      unproven:true and left out of route() / describe() until an 'ok' (a confirmed episode, a VERIFIED replay) or the
 *      user's own 'seen' proves it (v0.10 review: learn only from verified runs or explicit user input). One edge per
 *      from + control role/name (an ok observation refreshes its to/effect); at most CAP_EDGES per host (the least
 *      useful go first). An edge whose control, effect or state names read like text addressed to an AI agent
 *      (hostile()) is never stored, and never rendered if an older map holds one.
 *  route(map, host, fromKey, targetWords, opts) -> [edges]: the cheapest reliable path (edge cost grows with its
 *      failures, shrinks with its successes) from fromKey (or the host's start states when fromKey is unknown) that
 *      covers the most target words (stemmed: "invoices" ~ "invoice", "billing" ~ "bill"), ending on the edge that
 *      completes them. [] when nothing matches.
 *  describe(map, host, opts{max=8, targetWords, from}) -> "Known paths on claude.ai:\n- button \"S Saurabh · Max\"
 *      -> opens menu -> menuitem \"Settings\" -> opens the \"Settings\" window -> tab \"Billing\" -> invoice rows ->
 *      link \"Download PDF\" (downloads)" ('' when the host has no edges). Routes matching targetWords come first.
 * Every page string is data: quoted, cut to 60 characters, quotes and line breaks removed, never an instruction; the
 * caller puts describe() inside a delimited untrusted block of the prompt (lib/agent.js memoryBlock).
 * PURE: no DOM, no chrome.*; the caller keeps the map in chrome.storage.local. Defines globalThis.AWOSSiteMap.
 */
(function (root) {
  'use strict';

  var KINDS = ['opened_menu', 'opened_dialog', 'closed_dialog', 'switched_tab', 'navigated', 'downloaded', 'new_tab', 'showed_items', 'nothing'];
  var CAP_EDGES = 200, CAP_STATES = 400, MAX_STR = 60;
  var MENU_ROLES = /^(menuitem|menuitemradio|menuitemcheckbox)$/;
  var OPENERS = /^(button|combobox|link|img|generic|)$/;           // a control that can open a menu
  // generic nouns that say what a list's rows are (row text itself is never stored: it holds dates, amounts, names)
  var ITEM_WORDS = ['invoice', 'receipt', 'statement', 'bill', 'payout', 'payment', 'transaction', 'order', 'report',
                    'document', 'file', 'chat', 'conversation', 'message', 'project', 'ticket', 'subscription'];
  var STOP = { the: 1, a: 1, an: 1, my: 1, me: 1, of: 1, to: 1, for: 1, and: 1, or: 1, in: 1, on: 1, at: 1, get: 1, go: 1,
               please: 1, from: 1, with: 1, this: 1, that: 1, it: 1, all: 1, our: 1, your: 1, open: 1, find: 1, show: 1 };
  // a few words people use for the same place (both sides are stemmed first)
  var SYN = { invoic: ['bill', 'receipt'], bill: ['invoic', 'payment'], receipt: ['invoic'], usag: ['limit'],
              subscription: ['plan', 'bill'], plan: ['subscription'], account: ['profil'], profil: ['account'] };

  function str(v) { return v == null ? '' : String(v); }
  function norm(s) { return str(s).replace(/[\u0000-\u001f\u007f\u2028\u2029]+/g, ' ').replace(/\s+/g, ' ').trim(); }
  /** a page string as data: one line, no quotes, at most 60 characters. */
  function clean(s, n) {
    var t = norm(s).replace(/["“”`]/g, "'");
    n = n || MAX_STR;
    return t.length > n ? t.slice(0, n - 1) + '…' : t;
  }
  function q(s) { return '"' + clean(s) + '"'; }
  // text addressed to AI agents (lib/agent.js INJECTION_RE when it is loaded; the same pattern otherwise): an edge
  // naming it would carry page instructions into every later prompt for the site
  var INJ = /(ignore (all |any )?(the )?(previous|prior|above) (instructions|rules)|(note|notice|instructions?|message|attention) (for|to) (all )?(ai|llm|automated)? ?(agents?|assistants?|models?)|\bai (agents?|assistants?)\b|\bas an ai\b|\bsystem prompt\b|do not (tell|mention|inform)[^.]{0,40}\b(user|owner)\b|you must also\b)/i;
  function hostile(e) {
    var re = (root.AWOSAgent && root.AWOSAgent.INJECTION_RE) || INJ;
    if (!e) return false;
    return [e.control && e.control.name, e.control && e.control.context, e.effect && e.effect.label, e.from, e.to]
      .some(function (x) { return re.test(norm(x)); });
  }
  function low(s) { return norm(s).toLowerCase(); }

  // ---- words ---------------------------------------------------------------------------------------------------
  function stem(w) {
    w = low(w);
    if (w.length > 4 && /ies$/.test(w)) w = w.slice(0, -3) + 'y';
    else if (w.length > 4 && /(sses|xes|ches|shes)$/.test(w)) w = w.slice(0, -2);
    else if (w.length > 3 && /s$/.test(w) && !/ss$/.test(w)) w = w.slice(0, -1);
    if (w.length > 5 && /ing$/.test(w)) w = w.slice(0, -3);
    else if (w.length > 4 && /ed$/.test(w)) w = w.slice(0, -2);
    if (w.length > 4 && /e$/.test(w)) w = w.slice(0, -1);
    if (/(.)\1$/.test(w) && w.length > 4) w = w.slice(0, -1);     // "settings" -> "sett" -> "set"
    return w;
  }
  /** distinct stems of the words in s (stop words dropped). */
  function words(s) {
    var out = [], seen = {};
    (Array.isArray(s) ? s.join(' ') : str(s)).toLowerCase().split(/[^a-z0-9À-￿]+/).forEach(function (w) {
      if (!w || STOP[w] || w.length < 2) return;
      var st = stem(w);
      if (st && !seen[st]) { seen[st] = 1; out.push(st); }
    });
    return out;
  }
  /** each target stem with its synonyms: [[stem, syn...], ...]. */
  function targets(tw) {
    return words(tw).map(function (w) { return [w].concat(SYN[w] || []); });
  }
  /** the generic item word a row / file name suggests ('invoice'), else ''. */
  function itemWord(texts) {
    var ws = words(texts);
    for (var i = 0; i < ITEM_WORDS.length; i++) if (ws.indexOf(stem(ITEM_WORDS[i])) >= 0) return ITEM_WORDS[i];
    return '';
  }

  // ---- states --------------------------------------------------------------------------------------------------
  function idSeg(s) {
    if (/^\d+$/.test(s)) return true;
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return true;
    if (/^[0-9a-f]{12,}$/i.test(s)) return true;
    if (s.length >= 8 && /\d/.test(s) && /[a-z]/i.test(s) && !/^[a-z]+-?\d{1,4}$/i.test(s)) return true;  // acct_1N2x.. (not "v2", "page-12")
    if (s.length >= 20) return true;
    return false;
  }
  /** "host/path-pattern" of a URL (query and fragment dropped, ids -> ':id'). */
  function pagePattern(url) {
    var m = /^[a-z][a-z0-9+.-]*:\/\/([^\/?#]+)([^?#]*)/i.exec(str(url));
    if (!m) return '';
    var host = m[1].toLowerCase().replace(/^www\./, '').replace(/:\d+$/, ''), segs = [];
    m[2].split('/').forEach(function (s) {
      if (!s) return;
      try { s = decodeURIComponent(s); } catch (e) {}
      segs.push(idSeg(s) ? ':id' : clean(s.toLowerCase(), 40));
    });
    return host + (segs.length ? '/' + segs.join('/') : '');
  }
  function hostOf(url) { var p = pagePattern(url); return p ? p.split('/')[0] : ''; }
  /** the selected tab of an observation (inside its dialog first). */
  function obsTab(obs) {
    var cs = (obs && obs.controls) || [], best = '';
    for (var i = 0; i < cs.length; i++) {
      var c = cs[i];
      if (!c || c.role !== 'tab' || str(c.value) !== '1') continue;
      if (obs.dialog && c.inDialog) return norm(c.name);
      if (!best) best = norm(c.name);
    }
    return best;
  }
  function stateParts(x) {
    x = x || {};
    var dlg = x.dialogName || x.dialog, name = '', tab = x.tab;     // an observation's recorder-style title first
    if (dlg && typeof dlg === 'object') { name = dlg.name; if (tab == null) tab = dlg.tab; } else name = dlg;
    if (tab == null && x.controls) tab = obsTab(x);
    return { page: pagePattern(x.url), dialog: clean(name), tab: clean(tab), menu: clean(x.menu) };
  }
  function keyOf(p) {
    return p.page + (p.dialog ? '|dialog:' + p.dialog : '') + (p.tab ? '|tab:' + p.tab : '') + (p.menu ? '|menu:' + p.menu : '');
  }
  function stateKey(x, opts) {
    var p = stateParts(x);
    if (opts && opts.menu) p.menu = clean(opts.menu);
    return keyOf(p);
  }
  function parseKey(k) {
    var parts = str(k).split('|'), p = { page: parts[0] || '', dialog: '', tab: '', menu: '' };
    parts.slice(1).forEach(function (s) {
      var i = s.indexOf(':'); if (i > 0 && p.hasOwnProperty(s.slice(0, i))) p[s.slice(0, i)] = s.slice(i + 1);
    });
    return p;
  }
  /** a short human label of a state: its menu / tab / window, else its path. */
  function stateLabel(k) {
    var p = parseKey(k);
    if (p.menu) return 'menu ' + p.menu;
    if (p.dialog) return p.dialog + (p.tab ? ' > ' + p.tab : '') + ' window';
    if (p.tab) return p.tab + ' tab';
    var i = p.page.indexOf('/');
    return i < 0 ? p.page + ' home' : p.page.slice(i);
  }

  // ---- edges ---------------------------------------------------------------------------------------------------
  function control(loc, extra) {
    loc = loc || {};
    var c = { role: clean(loc.role, 30), name: clean(loc.name || loc.text || loc.label), context: clean(loc.context) };
    if (loc.row) { c.row = true; var w = itemWord([extra || '', loc.row]); if (w) c.items = w; }
    return c;
  }
  function edge(host, from, to, ctl, kind, label) {
    return { host: host, from: from, to: to, control: ctl, effect: { kind: KINDS.indexOf(kind) >= 0 ? kind : 'nothing', label: clean(label) } };
  }
  function fileExt(name) { var m = /\.([a-z0-9]{1,5})$/i.exec(str(name)); return m ? m[1].toUpperCase() : ''; }

  /** edges a recording shows (clicks only), in order. */
  function fromRecording(rec) {
    var steps = (rec && rec.steps) || [], out = [], cur = null;
    var s0 = steps[0] && steps[0].action === 'goto' ? steps[0] : null;
    cur = { url: (s0 && s0.url) || (rec && rec.startUrl) || '', dialog: '', tab: '', menu: '' };
    if (s0 && s0.dialog) { cur.dialog = norm(s0.dialog.name); cur.tab = norm(s0.dialog.tab); }
    // what the page looked like when each step ran (its own recorded url / dialog), menu states filled in below
    var ctx = steps.map(function (s) {
      if (s.action === 'goto') return { url: s.url, dialog: '', tab: '', menu: '' };
      return { url: s.url || '', dialog: s.dialog ? norm(s.dialog.name) : '', tab: s.dialog ? norm(s.dialog.tab) : '', menu: '' };
    });
    // a tab click is often recorded with the tab it selects already selected: the state it left is the tab selected
    // at the click before it (same window), else unknown
    var prev = null;
    steps.forEach(function (s, i) {
      if (s.action === 'goto') { prev = null; return; }
      var c = ctx[i];
      if (s.loc && s.loc.role === 'tab' && c.dialog && c.tab === norm(s.loc.name)) c.tab = prev && prev.dialog === c.dialog && prev.tab !== c.tab ? prev.tab : '';
      prev = c;
    });
    var dlName = '';
    for (var i = 0; i < steps.length; i++) {
      var s = steps[i];
      if (s.action === 'goto') { cur = { url: s.url, dialog: i === 0 ? cur.dialog : '', tab: i === 0 ? cur.tab : '', menu: '' }; continue; }
      var before = { url: ctx[i].url || cur.url, dialog: ctx[i].dialog, tab: ctx[i].tab, menu: cur.menu };
      var loc = s.loc || {};
      if (!MENU_ROLES.test(loc.role || '')) before.menu = '';
      if (s.action !== 'click') { cur = { url: before.url, dialog: before.dialog, tab: before.tab, menu: '' }; continue; }
      var j = i + 1; while (j < steps.length && steps[j].action !== 'goto' && steps[j].action !== 'click') j++;
      var nx = steps[j] || null, nc = nx ? ctx[j] : null;
      var nextLoc = (nx && nx.loc) || {};
      var after = { url: before.url, dialog: before.dialog, tab: before.tab, menu: '' }, kind = 'nothing', label = '';
      var host = hostOf(before.url);
      if (s.download) dlName = s.download.name || '';
      if (s.download && !s.navigates) {
        kind = 'downloaded'; label = fileExt(s.download.name) || 'file';
      } else if (s.opensTab) {
        kind = 'new_tab'; label = loc.name;
      } else if (s.navigates || (nx && nx.action === 'goto')) {
        kind = 'navigated';
        after = { url: (nx && (nx.action === 'goto' ? nx.url : nc.url)) || before.url, dialog: nc && nx.action !== 'goto' ? nc.dialog : '', tab: nc && nx.action !== 'goto' ? nc.tab : '', menu: '' };
        label = stateLabel(keyOf(stateParts(after)));
        if (after.dialog && after.dialog !== before.dialog) { kind = 'opened_dialog'; label = after.dialog; }
      } else if (loc.role === 'tab') {
        kind = 'switched_tab'; label = loc.name; after.tab = norm(loc.name);
      } else if (nx && MENU_ROLES.test(nextLoc.role || '') && !MENU_ROLES.test(loc.role || '') && OPENERS.test(loc.role || '')) {
        kind = 'opened_menu'; label = loc.name; after.menu = norm(loc.name);
      } else if (nc && nc.dialog && nc.dialog !== before.dialog) {
        kind = 'opened_dialog'; label = nc.dialog; after.dialog = nc.dialog; after.tab = nc.tab;
      } else if (nc && before.dialog && !nc.dialog) {
        kind = 'closed_dialog'; label = before.dialog; after.dialog = ''; after.tab = '';
      } else if (nx && nextLoc.row && !loc.row) {
        kind = 'showed_items'; label = itemWord([nextLoc.row, (nx.download && nx.download.name) || '']) || 'list';
      }
      var ctl = control(loc, (s.download && s.download.name) || '');
      out.push(edge(host, keyOf(stateParts(before)), keyOf(stateParts(after)), ctl, kind, label));
      cur = after;
    }
    // a row the download came from says what the rows are ("Invoice-…pdf" -> invoice rows)
    out.forEach(function (e) { if (e.control.row && !e.control.items && dlName) { var w = itemWord(dlName); if (w) e.control.items = w; } });
    return out;
  }

  // ---- effects of one action, from two observations ------------------------------------------------------------
  function menuCount(o) { return ((o && o.controls) || []).filter(function (c) { return c && MENU_ROLES.test(c.role || ''); }).length; }
  function rowSet(o) {
    var s = {}; ((o && o.controls) || []).forEach(function (c) { if (c && c.row) s[norm(c.row)] = 1; }); return s;
  }
  function entityList(o) {
    var e = o && (o.entities || (o.structure && (o.structure.entities || o.structure.groups)));
    return Array.isArray(e) ? e : [];
  }
  function entType(e) { return low(e && (e.type || e.kind)); }
  function entRows(e) { var r = e && (e.rows || e.items); return Array.isArray(r) ? r.length : +(e && (e.count || e.total)) || 0; }
  function effectOf(before, after, act) {
    before = before || {}; after = after || {}; act = act || {};
    var pb = stateParts(before), pa = stateParts(after), to = keyOf(pa);
    if (act.download) return { kind: 'downloaded', label: fileExt(act.download && act.download.name) || 'file', to: keyOf(pb) };
    if (act.newTab || act.opensTab) return { kind: 'new_tab', label: clean(act.name), to: keyOf(pb) };
    if (pa.dialog && pa.dialog !== pb.dialog) return { kind: 'opened_dialog', label: pa.dialog, to: to };
    if (pa.page && pb.page && pa.page !== pb.page) return { kind: 'navigated', label: stateLabel(to), to: to };
    if (pa.tab && pa.tab !== pb.tab && pa.dialog === pb.dialog) return { kind: 'switched_tab', label: pa.tab, to: to };
    var eb = entityList(before), ea = entityList(after);
    var menuEnt = ea.filter(function (e) {
      return /menu|popup/.test(entType(e)) && !eb.some(function (b) { return entType(b) === entType(e) && low(b.name || b.label) === low(e.name || e.label); });
    })[0];
    if (menuEnt || menuCount(after) > menuCount(before)) {
      pa.menu = clean(act.name || (menuEnt && (menuEnt.name || menuEnt.label)));
      return { kind: 'opened_menu', label: pa.menu, to: keyOf(pa) };
    }
    if (pb.dialog && !pa.dialog) return { kind: 'closed_dialog', label: pb.dialog, to: to };
    var rb = rowSet(before), ra = rowSet(after), fresh = Object.keys(ra).filter(function (r) { return !rb[r]; });
    var listGrew = ea.some(function (e) {
      if (!/list|table|rows/.test(entType(e))) return false;
      var b = eb.filter(function (x) { return entType(x) === entType(e) && low(x.name || x.label) === low(e.name || e.label); })[0];
      return entRows(e) > (b ? entRows(b) : 0);
    });
    if (fresh.length >= 2 || (fresh.length && !Object.keys(rb).length) || listGrew) {
      return { kind: 'showed_items', label: itemWord(fresh.concat(ea.map(function (e) { return str(e.name || e.label); }))) || 'list', to: to };
    }
    return { kind: 'nothing', label: '', to: keyOf(pb) };
  }

  // ---- the map ---------------------------------------------------------------------------------------------------
  function edgeKey(e) { return e.from + '\u0001' + low(e.control && e.control.role) + '\u0001' + low(e.control && e.control.name); }
  function usefulness(e) { return (e.ok || 0) * 3 + (e.seen || 0) - (e.fail || 0) * 2 + (e.last || 0) / 1e13; }
  function merge(map, edges, outcome, now) {
    var out = JSON.parse(JSON.stringify(map && map.hosts ? map : { hosts: {} }));
    var t = typeof now === 'number' ? now : Date.now();
    outcome = /^(ok|fail|unproven)$/.test(outcome || '') ? outcome : 'seen';
    (edges || []).forEach(function (e) {
      if (!e || !e.from || !e.control || hostile(e)) return;
      var host = clean(e.host || e.from.split('/')[0].split('|')[0], 80);
      if (!host) return;
      var h = out.hosts[host] = out.hosts[host] || { edges: {}, states: {} };
      h.edges = h.edges || {}; h.states = h.states || {};
      var ctl = { role: clean(e.control.role, 30), name: clean(e.control.name), context: clean(e.control.context) };
      if (e.control.row) ctl.row = true;
      if (e.control.items) ctl.items = clean(e.control.items, 30);
      var eff = { kind: KINDS.indexOf(e.effect && e.effect.kind) >= 0 ? e.effect.kind : 'nothing', label: clean(e.effect && e.effect.label) };
      var from = clean(e.from, 300), to = clean(e.to || e.from, 300);
      var k = edgeKey({ from: from, control: ctl }), x = h.edges[k];
      if (!x) {
        x = h.edges[k] = { from: from, to: to, control: ctl, effect: eff, seen: 0, ok: 0, fail: 0, last: 0 };
        if (outcome === 'unproven' || outcome === 'fail') x.unproven = true;    // a failure proves no effect either
      } else if (outcome === 'ok' || (!x.ok && eff.kind !== 'nothing' && (outcome !== 'unproven' || x.unproven))) {
        x.to = to; x.effect = eff; x.control = ctl;           // the model's own click never rewrites what the user showed
      }
      if (outcome === 'ok' || outcome === 'seen') delete x.unproven;
      x.seen++; if (outcome === 'ok') x.ok++; if (outcome === 'fail') x.fail++;
      x.last = t;
      [from, to].forEach(function (s) {
        var st = h.states[s] = h.states[s] || { label: clean(stateLabel(s)), seen: 0 };
        st.seen++;
      });
    });
    Object.keys(out.hosts).forEach(function (host) {
      var h = out.hosts[host], ks = Object.keys(h.edges || {});
      if (ks.length > CAP_EDGES) {
        ks.sort(function (a, b) { return usefulness(h.edges[b]) - usefulness(h.edges[a]); });
        ks.slice(CAP_EDGES).forEach(function (k) { delete h.edges[k]; });
      }
      var sk = Object.keys(h.states || {});
      if (sk.length > CAP_STATES) {                         // keep the states an edge still uses
        var used = {}; Object.keys(h.edges).forEach(function (k) { used[h.edges[k].from] = used[h.edges[k].to] = 1; });
        sk.filter(function (s) { return !used[s]; }).forEach(function (s) { delete h.states[s]; });
      }
    });
    return out;
  }

  // ---- routes ----------------------------------------------------------------------------------------------------
  /** the host's edges a route may use: proven ones (opts.unproven: all), never a hostile one. */
  function hostEdges(map, host, opts) {
    var h = map && map.hosts && map.hosts[str(host).toLowerCase().replace(/^www\./, '')];
    if (!h || !h.edges) return [];
    return Object.keys(h.edges).sort().map(function (k) { return h.edges[k]; })
      .filter(function (e) { return e && (!e.unproven || (opts && opts.unproven)) && !hostile(e); });
  }
  function cost(e) {
    var ok = e.ok || 0, fail = e.fail || 0;
    return 1 + 3 * fail / (ok + fail + 1) - Math.min(ok, 5) * 0.05 + (ok ? 0 : 0.2);
  }
  function usable(e, opts) { return !(opts && opts.keepFailing) ? !((e.fail || 0) >= 3 && !(e.ok || 0)) : true; }
  /** states to start from: fromKey when edges leave it; else the same page / window without the menu / tab part;
   *  else the host's start states (no edge leads into them); else every state an edge leaves. */
  function starts(es, fromKey) {
    var froms = {}, tos = {};
    es.forEach(function (e) { froms[e.from] = 1; if (e.to !== e.from) tos[e.to] = 1; });
    if (fromKey && froms[fromKey]) return [fromKey];
    if (fromKey) {
      var p = parseKey(fromKey), loose = [keyOf({ page: p.page, dialog: p.dialog, tab: p.tab, menu: '' }), keyOf({ page: p.page, dialog: p.dialog, tab: '', menu: '' }),
        keyOf({ page: p.page, dialog: '', tab: '', menu: '' })].filter(function (k) { return froms[k]; });
      if (loose.length) return [loose[0]];
    }
    var s = Object.keys(froms).filter(function (k) { return !tos[k]; });
    return s.length ? s : Object.keys(froms);
  }
  function edgeText(e) {
    var kindWord = { downloaded: 'download file', new_tab: 'tab', navigated: 'page', opened_dialog: 'window', switched_tab: 'tab', showed_items: 'list' }[e.effect.kind] || '';
    return [e.control.name, e.control.context, e.control.items || '', e.effect.label, kindWord, stateLabel(e.to)].join(' ');
  }
  /** Dijkstra from the start states: per state the cheapest path (list of edges). */
  function paths(es, startKeys, opts) {
    var best = {}, done = {}, out = {};
    startKeys.forEach(function (k) { best[k] = 0; out[k] = []; });
    for (;;) {
      var u = null;
      Object.keys(best).forEach(function (k) { if (!done[k] && (u === null || best[k] < best[u])) u = k; });
      if (u === null) break;
      done[u] = 1;
      if (out[u].length >= ((opts && opts.maxLen) || 10)) continue;
      es.forEach(function (e) {
        if (e.from !== u || e.to === u || !usable(e, opts)) return;
        var c = best[u] + cost(e);
        if (best[e.to] == null || c < best[e.to] - 1e-9) { best[e.to] = c; out[e.to] = out[u].concat([e]); }
      });
    }
    return { cost: best, path: out };
  }
  /** how many target words the path names: 1 per word named as such, 0.5 per word named only by a synonym. */
  function covered(path, tg) {
    var ws = words(path.map(edgeText)), n = 0;
    tg.forEach(function (alts) {
      if (ws.indexOf(alts[0]) >= 0) n += 1;
      else if (alts.some(function (a) { return ws.indexOf(a) >= 0; })) n += 0.5;
    });
    return n;
  }
  /** candidate routes: every usable edge reached by a cheapest path, as [path, cost]. */
  function candidates(es, startKeys, opts) {
    var P = paths(es, startKeys, opts), out = [];
    es.forEach(function (e) {
      if (!usable(e, opts) || P.path[e.from] == null) return;
      var p = P.path[e.from].concat([e]);
      if (p.indexOf(e) < p.length - 1) return;              // e already on the way (a loop)
      out.push({ path: p, cost: P.cost[e.from] + cost(e) });
    });
    return out;
  }
  function route(map, host, fromKey, targetWords, opts) {
    var es = hostEdges(map, host, opts), tg = targets(targetWords);
    if (!es.length || !tg.length) return [];
    var best = null;
    candidates(es, starts(es, fromKey), opts).forEach(function (c) {
      var last = c.path[c.path.length - 1];
      if (!covered([last], tg)) return;                     // the route must END on an edge the goal names
      c.n = covered(c.path, tg) + covered([last], tg) / 4;   // the goal named by the last step itself counts more
      if (!best || c.n > best.n || (c.n === best.n && c.cost < best.cost - 1e-9)) best = c;
    });
    return best ? best.path : [];
  }

  // ---- text for the prompt ---------------------------------------------------------------------------------------
  function hop(e, i, path) {
    var c = e.control, parts = [];
    if (c.row && !(i > 0 && path[i - 1].effect.kind === 'showed_items')) parts.push((c.items || 'list') + ' rows');
    var s = (c.role || 'control') + ' ' + q(c.name), k = e.effect.kind, lb = e.effect.label;
    if (k === 'downloaded') s += ' (downloads)';
    else if (k === 'new_tab') s += ' (opens a new tab)';
    parts.push(s);
    if (k === 'opened_menu') parts.push('opens menu');
    else if (k === 'opened_dialog') parts.push('opens the ' + q(lb) + ' window');
    else if (k === 'closed_dialog') parts.push('closes the window');
    else if (k === 'navigated') parts.push('goes to ' + q(lb));
    else if (k === 'showed_items') parts.push('shows ' + clean(lb || 'list', 30) + ' rows');
    return parts.join(' -> ');
  }
  function render(path) { return path.map(hop).join(' -> '); }
  function describe(map, host, opts) {
    opts = opts || {};
    var es = hostEdges(map, host, opts), max = opts.max || 8;
    if (!es.length) return '';
    var lines = [];
    function prefix(a, b) { return a.length <= b.length && a.every(function (e, i) { return b[i] === e; }); }
    function add(p) {
      if (!p.length || lines.some(function (l) { return prefix(p, l.p); })) return;
      var i = -1;                                            // a listed route this path continues: show the longer one
      lines.forEach(function (l, k) { if (i < 0 && prefix(l.p, p)) i = k; });
      if (i >= 0) { lines[i] = { t: render(p), p: p }; return; }
      if (lines.length < max) lines.push({ t: render(p), p: p });
    }
    var tg = targets(opts.targetWords);
    if (tg.length) add(route(map, host, opts.from, opts.targetWords, opts));
    // other known paths: the cheapest way to each dead end (an edge whose result leads nowhere further, or a file)
    var froms = {}; es.forEach(function (e) { froms[e.from] = 1; });
    var cs = candidates(es, starts(es, opts.from), opts).filter(function (c) {
      var e = c.path[c.path.length - 1];
      return e.effect.kind === 'downloaded' || e.effect.kind === 'new_tab' || !froms[e.to] || e.to === e.from;
    });
    // drop a path that is the start of a longer listed one
    cs = cs.filter(function (c) {
      return !cs.some(function (d) { return d !== c && d.path.length > c.path.length && c.path.every(function (e, i) { return d.path[i] === e; }); });
    });
    cs.forEach(function (c) {
      var ok = c.path.reduce(function (n, e) { return n + (e.ok || 0); }, 0);
      c.rank = (tg.length ? covered(c.path, tg) * 100 : 0) + Math.min(ok, 50) - c.cost;
    });
    cs.sort(function (a, b) { return b.rank - a.rank || a.path.length - b.path.length; });
    cs.forEach(function (c) { add(c.path); });
    if (!lines.length) return '';
    return 'Known paths on ' + clean(host) + ':\n' + lines.map(function (l) { return '- ' + l.t; }).join('\n');
  }

  root.AWOSSiteMap = {
    KINDS: KINDS, CAP_EDGES: CAP_EDGES,
    stateKey: stateKey, parseKey: parseKey, stateLabel: stateLabel, pagePattern: pagePattern, hostOf: hostOf,
    fromRecording: fromRecording, effectOf: effectOf, merge: merge, route: route, describe: describe,
    render: render, stem: stem, words: words, itemWord: itemWord, clean: clean, hostile: hostile,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
