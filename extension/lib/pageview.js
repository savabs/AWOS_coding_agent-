/* AWOS Replay: the page as typed entities, not 200 buttons (v0.10).
 *
 * The flat list of the first 40 controls (AWOSAgent.outline) lets a 4B model click distractors: on claude.ai dozens
 * of chat-history links and "Customize" outrank the profile menu -> Settings -> Billing path. This file gives the
 * model a structured, goal-ranked, capped view instead (AgentOccam / Prune4Web: prune what the model sees;
 * docs/research/experience_reuse_2026-11-05.md finding 2). Three steps:
 *
 *  structure(els) -> [group]                      IN THE PAGE (content script, needs the DOM)
 *      els = the control elements in exactly the order of AWOSLocator.observe().controls (n = index + 1). Each n
 *      lands in exactly one group: its innermost meaningful container. Containers, innermost wins:
 *        menu (role=menu / menubar), dialog (dialog, role=dialog / alertdialog), tablist, list / table (ul, ol,
 *        role=list / grid / table, table, or >= 4 structurally similar siblings), form (form, role=form / search),
 *        nav, header, footer, sidebar (aside, role=complementary), main, a named region ('other');
 *        a page without landmarks falls back to class / id words (header, navbar, sidebar, modal ...) and then
 *        to the nearest block with a heading. A short list inside a nav / header (< 8 items) stays in the nav.
 *      group = {kind, label, items:[n], within?:{kind, label}, list?:{count, sample:[names]}, rows?:[{n, text}],
 *               hints?:{n: {popup, expanded, icon, current}}}
 *      Only roles, accessible names, labels and row text are read; never a field's value.
 *  entities(obs) -> [entity]                      PURE (no DOM; also runs in the service worker)
 *      obs = observe() result + obs.structure. entity = {type, label, items:[n], pinned?, pin?:[n], collapsed?,
 *      count?, rows?:[{n, text, period?, amount?, status?, date?}], selected?, within?, open?, hint?}
 *      types: account_menu, primary_nav, settings_tabs, tabs, document_list, item_list, search, filters,
 *      pagination, dialog, popup, form, actions, other. Every n is in exactly one entity.
 *  render(obs, opts) -> {text, shown:[n], ranked:[n], hidden}   PURE
 *      opts = {goal, subgoal, words:[...], max: 40, expand: false, now}. Controls are ranked by BM25-lite overlap
 *      with goal + subgoal + words (light stemming, month names folded, a few synonyms), plus priors: pinned global
 *      navigation always shown, open menu / dialog content first, document rows naming the goal's month first.
 *      Lines are grouped under their entity's header; each control line is AWOSModel.controlLine(c, n).
 *      Item lists (chat history) collapse to one line "<label>: 45 links hidden" unless a link in them matches the
 *      goal. Page strings in headers are quoted and clipped: they are data, never instructions.
 * Plain script: defines globalThis.AWOSPageView (uses AWOSLocator / AWOSPeriod / AWOSModel when present).
 */
(function (root) {
  'use strict';

  var MAX = 40, NAV_LIST_MIN = 8, SIM_SIBLINGS = 4, LIST_ITEMS = 3;

  function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }
  function low(s) { return norm(s).toLowerCase(); }
  /** a page string as a quoted, clipped label (no newlines, no quotes to break out of). */
  function q(s, n) { return '"' + norm(s).replace(/"/g, "'").slice(0, n || 50) + '"'; }

  // ==== structure (in the page) =================================================================================
  var MENU_SEL = '[role=menu], [role=menubar]';
  var DIALOG_SEL = 'dialog, [role=dialog], [role=alertdialog]';
  var LIST_SEL = 'ul, ol, [role=list], [role=listbox], [role=grid], [role=treegrid], [role=table], [role=tree], table';
  var FORM_SEL = 'form, [role=form], [role=search]';
  var HINT_KIND = [                                        // a page without landmarks: class / id words
    [/(^|[\s_-])(modal|dialog|popup|popover|overlay)([\s_-]|$)/i, 'dialog'],
    [/(^|[\s_-])(dropdown-menu|context-menu|menu-list)([\s_-]|$)/i, 'menu'],
    [/(^|[\s_-])(header|topbar|top-bar|masthead|navbar|appbar|app-bar)([\s_-]|$)/i, 'header'],
    [/(^|[\s_-])(sidebar|side-bar|sidenav|side-nav|drawer|leftnav|left-nav|rail)([\s_-]|$)/i, 'sidebar'],
    [/(^|[\s_-])(nav|navigation|menu|tabs?)([\s_-]|$)/i, 'nav'],
    [/(^|[\s_-])(footer|bottom-bar)([\s_-]|$)/i, 'footer'],
    [/(^|[\s_-])(main|content|page-content)([\s_-]|$)/i, 'main'],
  ];

  function L() { return root.AWOSLocator || {}; }
  function parentDeep(n) { return n.parentElement || (n.parentNode && n.parentNode.host) || null; }
  function attr(el, a) { return el.getAttribute ? el.getAttribute(a) : null; }
  function roleAttr(el) { return low(attr(el, 'role')); }
  function tag(el) { return el.tagName ? el.tagName.toLowerCase() : ''; }
  function accName(el) { return L().accName ? L().accName(el) : norm(attr(el, 'aria-label') || el.textContent).slice(0, 80); }
  function byId(el, id) {
    var r = el.getRootNode ? el.getRootNode() : document;
    return (r && r.getElementById ? r.getElementById(id) : null) || document.getElementById(id);
  }
  function labelledBy(el) {
    var lb = attr(el, 'aria-labelledby');
    if (lb) {
      var t = norm(lb.split(/\s+/).map(function (id) { var n = byId(el, id); return n ? n.textContent : ''; }).join(' '));
      if (t) return t;
    }
    return norm(attr(el, 'aria-label'));
  }
  function textOf(el, n) { return norm(el.innerText || el.textContent).slice(0, n || 160); }
  function firstHeading(el) {
    var h = el.querySelector && el.querySelector('h1, h2, h3, h4, h5, h6, [role=heading]');
    return h ? textOf(h, 60) : '';
  }
  /** the heading just before `el` (its own previous siblings, then its parent's, 2 levels): a list's title. */
  function headingBefore(el) {
    var n = el;
    for (var up = 0; up < 3 && n; up++, n = n.parentElement) {
      for (var p = n.previousElementSibling, k = 0; p && k < 4; p = p.previousElementSibling, k++) {
        if (/^h[1-6]$/.test(tag(p)) || roleAttr(p) === 'heading') return textOf(p, 60);
        var hs = p.querySelectorAll ? p.querySelectorAll('h1, h2, h3, h4, h5, h6, [role=heading]') : [];
        if (hs.length) return textOf(hs[hs.length - 1], 60);
        if (textOf(p, 1)) break;                       // only the heading right before it (text between: another section's)
      }
    }
    return '';
  }
  function isHidden(el) {
    if (attr(el, 'hidden') != null || attr(el, 'aria-hidden') === 'true') return true;
    try { var cs = getComputedStyle(el); return cs.display === 'none' || cs.visibility === 'hidden'; } catch (e) { return false; }
  }
  /** structural signature of a sibling: tag + role + class list + child tags (two rows of a list share it). */
  function sig(el) {
    var kids = [];
    for (var c = el.firstElementChild, k = 0; c && k < 4; c = c.nextElementSibling, k++) kids.push(c.tagName);
    var cls = typeof el.className === 'string' ? el.className.replace(/\b(active|selected|current|open|is-[\w-]+)\b/g, '') : '';
    return el.tagName + '|' + (attr(el, 'role') || '') + '|' + norm(cls) + '|' + kids.join(',');
  }
  /** -> {same, total}: how many element children of `parent` share el's signature (cached per parent). */
  function similarCount(el, cache) {
    var p = el.parentElement; if (!p) return 0;
    var m = cache.get(p);
    if (!m) {
      m = {};
      for (var c = p.firstElementChild; c; c = c.nextElementSibling) { var s = sig(c); m[s] = (m[s] || 0) + 1; }
      cache.set(p, m);
    }
    return m[sig(el)] || 0;
  }
  function explicitItems(list) {
    var t = tag(list), sel = t === 'table' ? 'tr' : '';
    if (sel) return list.querySelectorAll('tr').length;
    var n = 0;
    for (var c = list.firstElementChild; c; c = c.nextElementSibling) if (!/^(script|style|template)$/i.test(c.tagName)) n++;
    return n;
  }
  /** the landmark kind of one node, or '' (ARIA first, then HTML5, then class / id words when `loose`). */
  function landmarkKind(n, loose) {
    var r = roleAttr(n), t = tag(n);
    if (n.matches(MENU_SEL)) return 'menu';
    if (n.matches(DIALOG_SEL)) return 'dialog';
    if (r === 'tablist') return 'tablist';
    if (n.matches(FORM_SEL)) return 'form';
    if (r === 'navigation' || t === 'nav') return 'nav';
    if (r === 'banner' || (t === 'header' && !(L().closestDeep && parentDeep(n) && L().closestDeep(parentDeep(n), 'article, section, main, aside, [role=main]')))) return 'header';
    if (r === 'contentinfo' || (t === 'footer' && !(L().closestDeep && parentDeep(n) && L().closestDeep(parentDeep(n), 'article, section, main, aside, [role=main]')))) return 'footer';
    if (r === 'complementary' || t === 'aside') return 'sidebar';
    if (r === 'main' || t === 'main') return 'main';
    if ((r === 'region' || t === 'section') && labelledBy(n)) return 'other';
    if (loose) {
      var words = (typeof n.className === 'string' ? n.className : '') + ' ' + (n.id || '');
      for (var i = 0; i < HINT_KIND.length; i++) if (HINT_KIND[i][0].test(words)) return HINT_KIND[i][1];
    }
    return '';
  }

  /** the innermost meaningful container of control `el` -> {node, kind}. */
  function containerOf(el, cache, loose) {
    var list = null, listKind = '';
    for (var n = el, depth = 0; n && n.nodeType === 1 && !/^(body|html)$/i.test(n.tagName); n = parentDeep(n), depth++) {
      if (n !== el) {
        var k = landmarkKind(n, false);
        if (!k && n.matches(LIST_SEL) && explicitItems(n) >= LIST_ITEMS && !list) { list = n; listKind = tag(n) === 'table' || /grid|table/.test(roleAttr(n)) ? 'table' : 'list'; continue; }
        if (k) {
          if (list === n) return { node: n, kind: k };     // the landmark itself holds the alike children (4 tabs)
          // a short list of links inside a nav / header / menu / tablist belongs to it; a long one (chat history) is a list
          if (list && /^(nav|header|menu|tablist)$/.test(k) && countItems(list, cache) < NAV_LIST_MIN) return { node: n, kind: k };
          if (list) return { node: list, kind: listKind };
          return { node: n, kind: k };
        }
      }
      // generic repeated siblings (>= 4 alike): the parent is a list. Only near the control (a row, not the page).
      if (!list && depth <= 5 && n.parentElement && !/^(body|html)$/i.test(n.parentElement.tagName)
          && similarCount(n, cache) >= SIM_SIBLINGS) {
        list = n.parentElement; listKind = /^(tr|tbody)$/i.test(n.tagName) ? 'table' : 'list';
        if (tag(list) === 'tbody' && list.parentElement) list = list.parentElement;
      }
    }
    if (list) return { node: list, kind: listKind };
    if (loose) {                                        // no landmarks at all: class / id words, then a titled block
      for (n = parentDeep(el); n && n.nodeType === 1 && !/^(body|html)$/i.test(n.tagName); n = parentDeep(n)) {
        var h = landmarkKind(n, true);
        if (h) return { node: n, kind: h };
        if (n.querySelector && n.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4')) return { node: n, kind: 'other' };
      }
    }
    return { node: null, kind: 'other' };
  }
  function countItems(list, cache) {
    var n = explicitItems(list), best = 0;
    for (var c = list.firstElementChild; c; c = c.nextElementSibling) best = Math.max(best, similarCount(c, cache));
    return Math.max(tag(list) === 'table' ? n : 0, best, /^(ul|ol)$/.test(tag(list)) || /list|grid|table|tree/.test(roleAttr(list)) ? n : 0);
  }
  /** the direct child of `list` that holds `el` (its row), skipping a table's tbody. */
  function rowOf(el, list) {
    for (var n = el; n && parentDeep(n); n = parentDeep(n)) {
      var p = parentDeep(n);
      if (p === list || (tag(p) === 'tbody' && parentDeep(p) === list)) return n;
      if (tag(n) === 'tr' && list.contains(n)) return n;
    }
    return null;
  }
  function groupLabel(node, kind) {
    if (!node) return '';
    var lb = labelledBy(node);
    if (lb) return lb.slice(0, 60);
    if (kind === 'menu' && node.id) {                   // the button that opens it
      var trig = document.querySelector('[aria-controls="' + node.id.replace(/"/g, '') + '"]');
      if (trig) return accName(trig).slice(0, 60);
    }
    if (kind === 'dialog' || kind === 'form' || kind === 'other' || kind === 'sidebar' || kind === 'main') {
      var h = firstHeading(node); if (h) return h;
    }
    if (kind === 'list' || kind === 'table') { var c = tag(node) === 'table' && node.querySelector('caption'); return c ? textOf(c, 60) : headingBefore(node); }
    return '';
  }
  function hintsOf(el) {
    var h = {}, hp = low(attr(el, 'aria-haspopup'));
    if (hp && hp !== 'false') h.popup = 1;
    var ex = attr(el, 'aria-expanded'); if (ex === 'true') h.expanded = 1; else if (ex === 'false') h.expanded = 0;
    var cur = attr(el, 'aria-current'); if (cur && cur !== 'false') h.current = 1;
    if (!/^(input|select|textarea)$/i.test(el.tagName)) {
      var own = norm(el.innerText || el.textContent);
      if ((!own || own.length <= 2) && el.querySelector && el.querySelector('svg, img, i, [class*=icon]')) h.icon = 1;
    }
    return h;
  }

  /** els (observe() order) -> groups. Every n (index + 1) is in exactly one group. */
  function structure(els) {
    els = els || [];
    var cache = typeof Map !== 'undefined' ? new Map() : null, groups = [], byNode = typeof Map !== 'undefined' ? new Map() : null;
    if (!cache) return [{ kind: 'other', label: '', items: els.map(function (e, i) { return i + 1; }) }];
    var loose = !(document.querySelector('header, nav, main, aside, footer, [role=banner], [role=navigation], [role=main], [role=complementary]'));
    var pageKey = {};
    els.forEach(function (el, i) {
      var n = i + 1, c = containerOf(el, cache, loose), key = c.node || pageKey;
      var g = byNode.get(key);
      if (!g) {
        g = { kind: c.kind, label: groupLabel(c.node, c.kind), items: [] };
        if (c.node) {
          var up = parentDeep(c.node) ? containerOf(c.node, cache, loose) : { node: null };
          if (up.node) g.within = { kind: up.kind, label: groupLabel(up.node, up.kind) };
          if (c.kind === 'menu' || c.kind === 'dialog') g.open = !isHidden(c.node);
        }
        g._node = c.node;
        byNode.set(key, g); groups.push(g);
      }
      g.items.push(n);
      var h = hintsOf(el); for (var k in h) { g.hints = g.hints || {}; g.hints[n] = h; break; }
      if ((c.kind === 'list' || c.kind === 'table') && c.node) {
        var row = rowOf(el, c.node);
        if (row && row !== el) { g.rows = g.rows || []; g.rows.push({ n: n, text: textOf(row, 160) }); }
      }
    });
    groups.forEach(function (g) {
      if ((g.kind === 'list' || g.kind === 'table') && g._node) {
        g.list = { count: Math.max(countItems(g._node, cache), g.items.length),
                   sample: g.items.slice(0, 3).map(function (n) { return accName(els[n - 1]).slice(0, 40); }) };
      }
      delete g._node;
    });
    return groups;
  }

  // ==== entities (pure) =========================================================================================
  var ACCOUNT_RE = /\b(account|profile|avatar|my account|user menu|sign ?out|log ?out|settings|preferences)\b/i;
  var PLAN_RE = /[·•|]\s*(free|pro|max|plus|team|premium|enterprise|business)\b/i;
  var POPUP_RE = /\b(what'?s new|new feature|introducing|announcement|tour|tips?|promo|special offer|upgrade|cookies?|consent|newsletter|got it)\b/i;
  var SETTINGS_RE = /\b(settings|preferences|account|billing|profile|options)\b/i;
  var DOC_ACTION_RE = /\b(download|view|open|pdf|invoice|receipt|statement|export|csv|print|details)\b/i;
  var AMOUNT_RE = /(?:[$€£¥₹]|\b(?:USD|EUR|GBP|INR|CAD|AUD|JPY|CHF|Rs\.?)\s?)\s?-?\d[\d,]*(?:\.\d{1,2})?/;
  var AMOUNT_AFTER_RE = /-?\d[\d,]*[.,]\d{2}\s?(?:USD|EUR|GBP|INR|CAD|AUD|CHF|€|zł|kr)(?![A-Za-z])/;   // "99,00 €" (never "2026 EUR")
  var STATUS_RE = /\b(paid|unpaid|due|overdue|pending|refunded|failed|open|void|draft|issued|processing|cancel(?:l)?ed)\b/i;
  var DATE_RE = /\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b|\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}\/\d{1,2}\/\d{2,4}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}\b/i;
  var PAGER_RE = /^(next|previous|prev|first|last|\d{1,3}|«|»|‹|›|<|>|older|newer|next page|previous page|go to page \d+|page \d+)$/i;
  var FILTER_RE = /\b(filter|sort|status|date range|year|month|from|to|type|category|show)\b/i;
  var MONTH_NUM = { jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3, apr: 4, april: 4, may: 5, jun: 6, june: 6,
                    jul: 7, july: 7, aug: 8, august: 8, sep: 9, sept: 9, september: 9, oct: 10, october: 10,
                    nov: 11, november: 11, dec: 12, december: 12 };

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  /** periods a row names: AWOSPeriod.rowPeriods when loaded, else a small date regex. */
  function rowPeriods(text) {
    if (root.AWOSPeriod && root.AWOSPeriod.rowPeriods) return root.AWOSPeriod.rowPeriods(text);
    var out = [], t = String(text || ''), m;
    var re = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(20\d\d)\b|\b(20\d\d)-(0[1-9]|1[0-2])\b/gi;
    while ((m = re.exec(t))) {
      var p = m[1] ? m[2] + '-' + pad(MONTH_NUM[m[1].toLowerCase()]) : m[3] + '-' + m[4];
      if (out.indexOf(p) < 0) out.push(p);
    }
    return out;
  }
  function rowFacts(text) {
    var r = {}, t = String(text || ''), ps = rowPeriods(t), m;
    if (ps.length) r.period = ps[0];
    if ((m = AMOUNT_RE.exec(t) || AMOUNT_AFTER_RE.exec(t))) r.amount = norm(m[0]);
    if ((m = STATUS_RE.exec(t))) r.status = m[1].charAt(0).toUpperCase() + m[1].slice(1).toLowerCase();
    if ((m = DATE_RE.exec(t))) r.date = norm(m[0]);
    return r;
  }
  function ctl(obs, n) { return (obs.controls || [])[n - 1] || {}; }
  function isAccountish(c, h) {
    return !!((h && h.popup) || ACCOUNT_RE.test(c.name || '') || PLAN_RE.test(c.name || ''));
  }
  /** a control that is global navigation, wherever it sits: menus, avatar / account / settings, labelled icon buttons. */
  function pinnable(c, h, g) {
    if (isAccountish(c, h)) return true;
    var inList = g.kind === 'list' || g.kind === 'table';
    return !!(h && h.icon && c.name && !inList && /^(button|link)$/.test(c.role || '') && /^(header|nav|sidebar)$/.test(g.kind));
  }

  /** obs (observe() + obs.structure) -> typed entities, in page order. */
  function entities(obs) {
    obs = obs || {};
    var n0 = (obs.controls || []).length;
    var groups = obs.structure && obs.structure.length ? obs.structure
      : [{ kind: 'other', label: '', items: Array.apply(null, Array(n0)).map(function (x, i) { return i + 1; }) }];
    var out = [], seen = {}, acctEnt = null;
    function add(e) {
      e.items = e.items.filter(function (n) { if (seen[n] || n < 1 || n > n0) return false; seen[n] = 1; return true; });
      if (e.items.length) out.push(e);
      return e;
    }
    groups.forEach(function (g) {
      var hints = g.hints || {}, items = (g.items || []).slice(), within = g.within || null, gl = norm(g.label);
      var hOf = function (n) { return hints[n] || hints[String(n)] || null; };
      var base = function (type, label, its) { var e = { type: type, label: label, items: its }; if (within) e.within = within; return e; };
      if (g.kind === 'menu') {
        var acct = isAccountish({ name: gl }, null) || items.some(function (n) { return ACCOUNT_RE.test(ctl(obs, n).name || ''); });
        var em = base(acct ? 'account_menu' : 'actions', gl || 'Menu', items);
        em.open = g.open !== false; em.pinned = true; em.pin = items.slice();
        add(em); return;
      }
      if (g.kind === 'dialog') {
        var dtext = gl + ' ' + items.map(function (n) { return ctl(obs, n).name || ''; }).join(' ');
        var popup = POPUP_RE.test(dtext) && !SETTINGS_RE.test(gl);
        var ed = base(popup ? 'popup' : 'dialog', gl || (popup ? 'Pop-up' : 'Window'), items);
        ed.open = true; if (popup) ed.hint = 'close it first';
        add(ed); return;
      }
      if (g.kind === 'tablist') {
        var settings = SETTINGS_RE.test((within && within.label) || '') || SETTINGS_RE.test(gl)
          || items.filter(function (n) { return SETTINGS_RE.test(ctl(obs, n).name || ''); }).length >= 2;
        var et = base(settings ? 'settings_tabs' : 'tabs', (within && within.label) || gl || '', items);
        var sel = items.filter(function (n) { return ctl(obs, n).value === '1'; });
        if (sel.length) et.selected = sel[0];
        add(et); return;
      }
      if (g.kind === 'list' || g.kind === 'table') {
        var rowsBy = {};
        (g.rows || []).forEach(function (r) { rowsBy[r.n] = r.text; });
        var rows = [], periods = 0, money = 0, acts = 0, links = 0, pager = [];
        items.forEach(function (n) {
          var c = ctl(obs, n), text = rowsBy[n] || c.row || '';
          if (PAGER_RE.test(norm(c.name))) pager.push(n);
          if (c.role === 'link') links++;
          if (!text) return;
          var f = rowFacts(text); f.n = n; f.text = text.slice(0, 160);
          if (f.period) periods++;
          if (f.amount || f.status) money++;
          if (DOC_ACTION_RE.test(c.name || '')) acts++;
          rows.push(f);
        });
        var rest = items;
        if (pager.length && pager.length < items.length) {
          add(base('pagination', 'Pages', pager));
          rest = items.filter(function (n) { return pager.indexOf(n) < 0; });
        }
        var count = (g.list && g.list.count) || rest.length;
        if (periods >= 2 && money >= 1 && acts >= 1) {
          var docWords = rows.map(function (r) { return r.text; }).join(' ') + ' ' + gl;
          var kindWord = /invoice|\binv[-#\s]?\d/i.test(docWords) ? 'Invoice' : /statement/i.test(docWords) ? 'Statement'
            : /receipt/i.test(docWords) ? 'Receipt' : /payout/i.test(docWords) ? 'Payout' : 'Document';
          var ed2 = base('document_list', gl || kindWord + ' list', rest);
          ed2.kindWord = kindWord; ed2.count = count;
          ed2.rows = rows.filter(function (r) { return rest.indexOf(r.n) >= 0; });
          add(ed2); return;
        }
        if (rest.length >= 4 && links >= rest.length * 0.6 || count >= NAV_LIST_MIN) {
          var ei = base('item_list', gl || (within && within.label) || 'List', rest);
          ei.collapsed = true; ei.count = count;
          add(ei); return;
        }
        add(base(within && /^(nav|header|sidebar)$/.test(within.kind) ? 'primary_nav' : 'actions', gl, rest)); return;
      }
      // landmarks / form / other: split out account controls, search, filters and pagination
      var acctN = [], searchN = [], filterN = [], pageN = [], restN = [], pinN = [];
      items.forEach(function (n) {
        var c = ctl(obs, n), h = hOf(n), nm = c.name || '';
        if (isAccountish(c, h) && /^(header|nav|sidebar|other|main)$/.test(g.kind)) acctN.push(n);
        else if (c.role === 'searchbox' || (/^(textbox|combobox)$/.test(c.role) && /\bsearch\b/i.test(nm + ' ' + (c.context || '')))) searchN.push(n);
        else if (PAGER_RE.test(norm(nm)) && /^(button|link)$/.test(c.role)) pageN.push(n);
        else if (/^(combobox|checkbox|radiogroup|switch)$/.test(c.role) && FILTER_RE.test(nm) && g.kind !== 'form') filterN.push(n);
        else { restN.push(n); if (pinnable(c, h, g)) pinN.push(n); }
      });
      if (acctN.length) {                               // one entity for the account / settings controls of all regions
        if (acctEnt) {
          acctN.forEach(function (n) { if (!seen[n]) { seen[n] = 1; acctEnt.items.push(n); acctEnt.pin.push(n); } });
        } else { acctEnt = { type: 'account_menu', label: 'Account and settings', items: acctN, pinned: true, pin: acctN.slice() }; add(acctEnt); }
      }
      if (searchN.length) add(base('search', 'Search', searchN));
      if (filterN.length) add(base('filters', 'Filters', filterN));
      if (pageN.length) add(base('pagination', 'Pages', pageN));
      if (!restN.length) return;
      var type = g.kind === 'form' ? (/search/i.test(gl) ? 'search' : 'form')
        : /^(nav|header|sidebar)$/.test(g.kind) ? 'primary_nav' : g.kind === 'other' && !gl ? 'other' : 'actions';
      var label = gl || { nav: 'Navigation', header: 'Header', sidebar: 'Sidebar', footer: 'Footer', main: 'Main', form: 'Form' }[g.kind] || '';
      var er = base(type, label, restN);
      er.region = g.kind;
      if (pinN.length) { er.pin = pinN; er.pinned = type === 'primary_nav'; }
      add(er);
    });
    // anything the structure missed (a stale structure): one 'other' entity
    var missed = [];
    for (var i = 1; i <= n0; i++) if (!seen[i]) missed.push(i);
    if (missed.length) add({ type: 'other', label: '', items: missed });
    return out;
  }

  // ==== render (pure) ===========================================================================================
  var STOP = {}; ('a an the to of for in on at by my me i and or is it this that be with from please can you your our its '
    + 'get go do want need then into as up out all one').split(' ').forEach(function (w) { STOP[w] = 1; });
  var SYN = { invoice: ['billing', 'bill', 'receipt', 'payment', 'pdf'], bill: ['billing', 'invoice'], receipt: ['invoice', 'billing'],
              statement: ['pdf', 'document'], download: ['pdf', 'export', 'save'], setting: ['preference', 'account'],
              billing: ['invoice', 'payment', 'plan'], account: ['profile', 'setting'], profile: ['account'],
              payment: ['billing', 'invoice'], plan: ['billing', 'subscription'], subscription: ['billing', 'plan'] };
  function stem(w) {
    if (MONTH_NUM[w]) return 'm' + pad(MONTH_NUM[w]);
    if (w.length > 5 && /ings?$/.test(w)) return w.replace(/ings?$/, '');
    if (w.length > 4 && /ies$/.test(w)) return w.slice(0, -3) + 'y';
    if (w.length > 4 && /(ches|shes|xes|sses)$/.test(w)) return w.slice(0, -2);
    if (w.length > 3 && /[^s]s$/.test(w)) return w.slice(0, -1);
    if (w.length > 4 && /ed$/.test(w)) return w.slice(0, -2);
    return w;
  }
  function toks(s) {
    return low(s).split(/[^a-z0-9]+/).filter(function (w) { return w && !STOP[w] && (w.length > 1 || /\d/.test(w)); }).map(stem);
  }
  /** query terms with weights: goal / subgoal 1, extra words 0.8, synonyms 0.4. */
  function queryTerms(opts) {
    var w = {};
    function put(t, x) { if (!w[t] || w[t] < x) w[t] = x; }
    toks(opts.goal).concat(toks(opts.subgoal)).forEach(function (t) { put(t, 1); });
    toks((opts.words || []).join(' ')).forEach(function (t) { put(t, 0.8); });
    Object.keys(w).forEach(function (t) { (SYN[t] || []).forEach(function (s) { put(stem(s), 0.4); }); });
    return w;
  }
  function goalPeriod(opts) {
    var t = [opts.goal, opts.subgoal].join(' ');
    if (root.AWOSPeriod && root.AWOSPeriod.fromText) {
      var p = root.AWOSPeriod.fromText(t, opts.now) || '';
      // "the September invoice" names no year: match September of any year (a list can be older than this year)
      if (p && !/\b20\d\d\b|\b(last|previous|past|this|current)\s+month\b|'\d\d/i.test(t)) p = '*' + p.slice(4);
      return p;
    }
    var m = low(t).match(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b(?:\s+(20\d\d))?/);
    return m ? (m[2] || '*') + '-' + pad(MONTH_NUM[m[1]]) : '';
  }
  function samePeriod(want, have) {
    if (!want || !have) return false;
    return want.charAt(0) === '*' ? have.slice(4) === want.slice(1) : want === have;
  }

  function line(c, n) {
    if (root.AWOSModel && root.AWOSModel.controlLine) return root.AWOSModel.controlLine(c, n);
    var bits = ['[' + n + '] ' + c.role + ' "' + (c.name || '') + '"'];
    if (c.context && c.context.toLowerCase() !== String(c.name || '').toLowerCase()) bits.push('(in "' + c.context + '")');
    if (c.inDialog) bits.push('(dialog)');
    if (c.options && c.options.length) bits.push('options=' + c.options.join('|'));
    if ((c.role === 'textbox' || c.role === 'password') && c.value) bits.push('= "' + c.value + '"');
    if (c.role === 'checkbox') bits.push(c.value === '1' ? '= checked' : '= unchecked');
    if (c.role === 'tab' && c.value === '1') bits.push('(selected)');
    return bits.join(' ');
  }

  function rowSummary(r) {
    return [r.date || (r.period || ''), r.amount || '', r.status || ''].filter(Boolean).join(' ');
  }
  function header(e, obs, shownSet) {
    var where = e.within && e.within.label && low(e.within.label) !== low(e.label) ? ' in ' + q(e.within.label, 40) : '';
    switch (e.type) {
      case 'account_menu': return e.open ? 'Account menu (open)' + (e.label ? ' ' + q(e.label, 40) : '') + ':' : 'Account and settings:';
      case 'settings_tabs': case 'tabs':
        var tabs = e.items.map(function (n) { var c = ctl(obs, n); return q(c.name, 30) + (c.value === '1' ? ' (selected)' : ''); }).join(' · ');
        return (e.label ? 'Window ' + q(e.label, 40) + ' — tabs: ' : (e.type === 'settings_tabs' ? 'Settings tabs: ' : 'Tabs: ')) + tabs;
      case 'document_list':
        var rs = (e.rows || []).slice(0, 4).map(rowSummary).filter(Boolean);
        var named = e.label && low(e.label).indexOf(low(e.kindWord || 'document')) !== 0;   // "Invoices" adds nothing
        return (named ? q(e.label, 40) + ' ' : '') + (e.kindWord || 'Document') + ' list' + where
          + ' (' + (e.count || e.items.length) + ' rows' + (rs.length ? ': ' + rs.join(' | ') + ((e.rows || []).length > 4 ? ' | …' : '') : '') + '):';
      case 'item_list': return 'List ' + q(e.label, 40) + where + ':';
      case 'dialog': return 'Window ' + q(e.label, 40) + ' (open):';
      case 'popup': return 'Pop-up ' + q(e.label, 40) + ' (covers the page; close it first):';
      case 'actions':
        var reg = { main: 'Main', footer: 'Footer', form: 'Form' }[e.region] || '';
        var nm = e.label && e.label !== reg ? q(e.label, 40) : '';
        return norm((e.open ? 'Open menu' : reg) + ' ' + nm) + (e.open || reg || nm ? '' : 'Actions') + where + ':';
      case 'primary_nav': return (e.region === 'sidebar' ? 'Sidebar' : e.region === 'header' ? 'Header' : 'Navigation') + (e.label && !/^(navigation|header|sidebar)$/i.test(e.label) ? ' ' + q(e.label, 40) : '') + ':';
      case 'search': return 'Search:';
      case 'filters': return 'Filters:';
      case 'pagination': return 'Pages:';
      case 'form': return 'Form' + (e.label && e.label !== 'Form' ? ' ' + q(e.label, 40) : '') + where + ':';
      default: return (e.label ? q(e.label, 40) : 'Page') + where + ':';
    }
  }

  /** obs, opts -> {text, shown:[n] (display order), ranked:[n] (best first), hidden, scores:{n: s}}. */
  function render(obs, opts) {
    obs = obs || {}; opts = opts || {};
    var max = opts.max > 0 ? opts.max : MAX, ctls = obs.controls || [], N = ctls.length;
    var ents = obs.entities || entities(obs), entOf = {};
    ents.forEach(function (e, k) { e.items.forEach(function (n) { entOf[n] = k; }); });
    // ---- BM25-lite over each control's document: name (x2), context, row text, entity label -----------------
    var docs = [], df = {}, avg = 0;
    for (var n = 1; n <= N; n++) {
      var c = ctls[n - 1], e = ents[entOf[n]] || {};
      var d = toks(c.name).concat(toks(c.name), toks(c.context), toks(c.row), toks(e.label), toks((e.within || {}).label));
      var tf = {}; d.forEach(function (t) { tf[t] = (tf[t] || 0) + 1; });
      Object.keys(tf).forEach(function (t) { df[t] = (df[t] || 0) + 1; });
      docs.push({ tf: tf, len: d.length }); avg += d.length;
    }
    avg = N ? avg / N || 1 : 1;
    var Q = queryTerms(opts), want = goalPeriod(opts), K1 = 1.2, B = 0.75;
    var score = {}, pinned = {}, rel = {};
    for (n = 1; n <= N; n++) {
      var doc = docs[n - 1], s = 0, ent = ents[entOf[n]] || {};
      for (var t in Q) {
        var f = doc.tf[t]; if (!f) continue;
        var idf = Math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5));
        s += Q[t] * idf * f * (K1 + 1) / (f + K1 * (1 - B + B * doc.len / avg));
      }
      rel[n] = s;
      // ---- type priors ----
      if (ent.pinned || (ent.pin && ent.pin.indexOf(n) >= 0)) pinned[n] = 1;
      if (ent.open || /^(popup|dialog)$/.test(ent.type) || (ent.within && ent.within.kind === 'dialog') || ctls[n - 1].inDialog) s += 1.5;
      if (ent.type === 'settings_tabs' || ent.type === 'tabs') s += 0.5;
      if (ent.type === 'document_list') {
        var row = (ent.rows || []).filter(function (r) { return r.n === n; })[0];
        if (row && row.period) {
          s += 0.8;
          if (want) s += samePeriod(want, row.period) ? 3 : -0.6;
        }
      }
      if (ent.type === 'item_list') s -= 0.5;
      score[n] = Math.round(s * 1000) / 1000;
    }
    var all = []; for (n = 1; n <= N; n++) all.push(n);
    var ranked = all.slice().sort(function (a, b) {
      return (pinned[b] || 0) - (pinned[a] || 0) || score[b] - score[a] || a - b;
    });
    // ---- choose: everything with expand; else pinned + best, item lists stay collapsed unless a link matches ----
    var show = {}, shownN = 0;
    ranked.forEach(function (k) {
      var e = ents[entOf[k]] || {};
      if (!opts.expand) {
        if (shownN >= max && !pinned[k]) return;
        if (e.collapsed && !(rel[k] >= 1.5)) return;
      }
      show[k] = 1; shownN++;
    });
    // ---- text: entities in priority order, lines in page order within each -----------------------------------
    var order = ents.map(function (e, k) { return k; }).sort(function (a, b) {
      return prio(ents[a]) - prio(ents[b]) || ents[a].items[0] - ents[b].items[0];
    });
    var out = [], shown = [], hiddenLists = 0;
    order.forEach(function (k) {
      var e = ents[k], vis = e.items.filter(function (x) { return show[x]; });
      if (e.collapsed && !opts.expand) {
        var rest = (e.count || e.items.length) - vis.length;
        if (!vis.length) { out.push(header(e, obs).replace(/:$/, '') + ': ' + rest + ' ' + (linkish(e, obs) ? 'links' : 'items') + ' hidden'); hiddenLists += e.items.length; return; }
        out.push(header(e, obs).replace(/:$/, ' (' + rest + ' more hidden):'));
      } else {
        if (!vis.length) return;
        out.push(header(e, obs));
      }
      vis.forEach(function (x) { out.push('  ' + line(ctls[x - 1], x)); shown.push(x); });
    });
    var hidden = N - shown.length;
    if (hidden > 0 && !opts.expand) out.push('(' + hidden + ' more controls not shown; ask to expand to see all)');
    return { text: out.join('\n'), shown: shown, ranked: ranked, hidden: hidden, scores: score };
  }
  function linkish(e, obs) { return e.items.filter(function (n) { return ctl(obs, n).role === 'link'; }).length >= e.items.length / 2; }
  /** popups first (they cover the page), then open menus, open windows and their tabs / lists, then global navigation. */
  function prio(e) {
    if (e.type === 'popup') return 0;
    if (e.open && e.type !== 'dialog') return 1;
    if (e.type === 'dialog') return 2;
    if (e.within && e.within.kind === 'dialog') return 3;
    if (e.type === 'account_menu') return 4;
    if (e.type === 'primary_nav') return 5;
    if (e.type === 'item_list') return 9;
    return 6;
  }

  root.AWOSPageView = {
    MAX: MAX, structure: structure, entities: entities, render: render,
    rowFacts: rowFacts, tokens: toks, queryTerms: queryTerms,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
