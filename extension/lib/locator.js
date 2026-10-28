/* AWOS Replay: multi-attribute locators and Similo-style re-finding.
 *
 * describe(el) captures a target as many weak attributes (tag, type, role,
 * accessible name, text, id, name attr, placeholder, nearby label, href,
 * css path, classes, neighbour text, form id, page position). find(loc, kind)
 * scores every visible candidate of the same kind against the recorded
 * locator -- a weighted sum of per-attribute similarities normalised by the
 * weight of the attributes the recording actually had (Similo, Nass et al.
 * 2021) -- and decides:
 *   found      best >= ACCEPT, clear of the runner-up by MARGIN, and anchored:
 *              at least one identity attribute (id, name attr, accessible name,
 *              text, label, href, placeholder) still matches >= ANCHOR_SIM. So a
 *              different control that merely sits where the old one was (same
 *              css path, position, neighbour) is never "found" by scoring alone.
 *   ambiguous  a plausible best but unanchored / too close to another / below ACCEPT
 *   not_found  nothing plausible at all
 * ambiguous / not_found carry a shortlist (top SHORTLIST >= FLOOR) that the
 * service worker hands to the local model. Element handles never leave the
 * page; candidates are addressed by their index in the last scan.
 * Duplicates: candidates that are the same control (same tag / role / name / label / placeholder / href /
 * region / block text, e.g. a docs page's top and bottom "Quick search" boxes) collapse into one; the margin
 * is measured against the best DIFFERENT control, and the recorded position / css path picks among copies.
 * Shadow DOM: candidates are collected through OPEN shadow roots (web components), in document order.
 * Plain script: defines globalThis.AWOSLocator (needs AWOSSafety for labelOf).
 */
(function (root) {
  'use strict';

  var ACCEPT = 0.55, MARGIN = 0.12, FLOOR = 0.25, SHORTLIST = 5, ANCHOR_SIM = 0.8;
  var ANCHORS = { id: 1, nameAttr: 1, name: 1, text: 1, label: 1, href: 1, placeholder: 1 };
  var WEIGHTS = {
    tag: 1.5, type: 0.5, role: 1.0, name: 3.0, text: 2.0, id: 2.0, nameAttr: 2.0,
    placeholder: 1.0, label: 2.0, href: 1.0, css: 1.0, classes: 0.5, neighbor: 1.0,
    title: 0.5, formId: 0.5, pos: 1.0,
  };
  var KIND_SELECTOR = {
    fill: 'input:not([type]), input[type=text], input[type=email], input[type=password], input[type=search],'
        + ' input[type=tel], input[type=url], input[type=number], input[type=date], textarea, [contenteditable=""], [contenteditable=true]',
    select: 'select',
    click: 'a[href], button, input[type=submit], input[type=button], input[type=reset], input[type=checkbox],'
         + ' input[type=radio], input[type=image], [role=button], [role=link], [role=tab], [role=menuitem],'
         + ' [role=checkbox], [role=radio], [role=option], [role=switch], summary, [onclick]',
  };

  /** every element matching `sel` in document order, descending into OPEN shadow roots. */
  function deepAll(sel, r) {
    var out = [], all = (r || document).querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.matches(sel)) out.push(e);
      if (e.shadowRoot) { var inner = deepAll(sel, e.shadowRoot); for (var j = 0; j < inner.length; j++) out.push(inner[j]); }
    }
    return out;
  }
  /** parent across shadow boundaries (an element at the top of a shadow root -> its host). */
  function parentDeep(n) { return n.parentElement || (n.parentNode && n.parentNode.host) || null; }
  /** Element.closest that also climbs out of shadow roots. */
  function closestDeep(el, sel) {
    for (var n = el; n; n = parentDeep(n)) if (n.nodeType === 1 && n.matches(sel)) return n;
    return null;
  }
  /** getElementById in the element's own tree (document or shadow root). */
  function byId(el, id) {
    var r = el.getRootNode ? el.getRootNode() : document;
    return (r && r.getElementById ? r.getElementById(id) : null) || document.getElementById(id);
  }

  function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }
  function low(s) { return norm(s).toLowerCase(); }

  var IMPLICIT_ROLE = { a: 'link', button: 'button', select: 'combobox', textarea: 'textbox', summary: 'button' };
  function roleOf(el) {
    var r = el.getAttribute('role'); if (r) return r;
    var tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      var t = (el.getAttribute('type') || 'text').toLowerCase();
      return { checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button', reset: 'button',
               image: 'button', range: 'slider', password: 'password' }[t] || 'textbox';
    }
    return IMPLICIT_ROLE[tag] || '';
  }

  // ---- the repair model's view of a control (scaffold/agent/desktop/repair: Fingerprint) -----------
  // context = name of the innermost dialog / alertdialog / form / region ancestor (htmldriver's Context);
  // block   = text of the nearest of up to 3 enclosing blocks, minus the control's own name (mutate._label);
  // siblings = names of the 2 controls before and 2 after it, in document order (ctl_fingerprint).
  // The repair model (G6) was trained on exactly these fields; scoring below does not use them.
  var CONTEXT_SEL = 'dialog, [role=dialog], [role=alertdialog], [role=form], [role=region], form[aria-label],'
                  + ' form[aria-labelledby], section[aria-label], section[aria-labelledby]';
  var ALL_CONTROLS = null;
  function contextOf(el) {
    var p = parentDeep(el), c = p ? closestDeep(p, CONTEXT_SEL) : null;
    if (!c) return '';
    var lb = c.getAttribute('aria-labelledby');
    if (lb) {
      var t = lb.split(/\s+/).map(function (id) { var n = byId(c, id); return n ? n.textContent : ''; }).join(' ');
      if (norm(t)) return norm(t);
    }
    return norm(c.getAttribute('aria-label') || '');
  }
  function blockText(el, own) {
    var u = el;
    for (var k = 0; k < 3; k++) {
      if (!u.parentElement || /^(body|html)$/i.test(u.parentElement.tagName)) break;
      u = u.parentElement;
      var t = String(u.textContent || '');
      if (own) t = t.split(own).join(' ');
      t = norm(t);
      if (t) return t.slice(0, 200);
    }
    return '';
  }
  function allControls() {
    if (!ALL_CONTROLS) ALL_CONTROLS = [KIND_SELECTOR.fill, KIND_SELECTOR.select, KIND_SELECTOR.click].join(', ');
    return deepAll(ALL_CONTROLS).filter(visible);
  }
  /** names of every visible control in document order, computed once per scan. */
  function pageNames() {
    var els = allControls();
    return { els: els, names: els.map(function (e) { return accName(e); }) };
  }
  function siblingsOf(el, page) {
    var i = page.els.indexOf(el);
    if (i < 0) return [];
    return page.names.slice(Math.max(0, i - 2), i).concat(page.names.slice(i + 1, i + 3));
  }

  function accName(el) {
    var a = el.getAttribute('aria-label'); if (a && norm(a)) return norm(a);
    var lb = el.getAttribute('aria-labelledby');
    if (lb) {
      var t = lb.split(/\s+/).map(function (id) { var n = byId(el, id); return n ? n.textContent : ''; }).join(' ');
      if (norm(t)) return norm(t);
    }
    var tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (/^(input|select|textarea)$/.test(tag) && !/^(submit|button|reset|image)$/.test(type)) {
      var l = root.AWOSSafety ? root.AWOSSafety.labelOf(el) : '';
      if (l) return l;
    }
    if (tag === 'input' && /^(submit|button|reset)$/.test(type) && el.value) return norm(el.value);
    if (!/^(input|select|textarea)$/.test(tag)) { var tx = norm(el.innerText || el.textContent); if (tx) return tx.slice(0, 80); }
    return norm(el.getAttribute('title') || el.getAttribute('placeholder') || el.getAttribute('alt') || '');
  }

  /** nearest preceding visible text in the same container: a cheap "nearby label". */
  function nearbyText(el) {
    var n = el, hops = 0;
    while (n && hops < 4) {
      var p = n.previousElementSibling;
      while (p) {
        var t = norm(p.innerText || p.textContent);
        if (t && !/^(input|select|textarea|button)$/i.test(p.tagName)) return t.slice(-60);
        p = p.previousElementSibling;
      }
      n = n.parentElement; hops++;
    }
    return '';
  }

  function cssPath(el) {
    var parts = [];
    for (var n = el; n && n.nodeType === 1 && n !== document.documentElement; ) {
      var tag = n.tagName.toLowerCase(), i = 1, s = n;
      while ((s = s.previousElementSibling)) if (s.tagName === n.tagName) i++;
      parts.unshift(tag + ':nth-of-type(' + i + ')');
      if (!n.parentElement && n.parentNode && n.parentNode.host) { parts.unshift('#shadow-root'); n = n.parentNode.host; }
      else n = n.parentElement;
    }
    return parts.join(' > ');
  }

  function describe(el, page) {
    var r = el.getBoundingClientRect();
    var form = el.form || closestDeep(el, 'form');
    var href = el.getAttribute('href') || '';
    try { if (href) href = new URL(href, location.href).pathname; } catch (e) {}
    var name = accName(el);
    return {
      tag: el.tagName.toLowerCase(),
      type: low(el.getAttribute('type')),
      role: roleOf(el),
      name: name,
      context: contextOf(el),
      block: blockText(el, name),
      siblings: siblingsOf(el, page || pageNames()),
      text: norm(el.innerText || el.textContent).slice(0, 80),
      id: el.id || '',
      nameAttr: el.getAttribute('name') || '',
      placeholder: norm(el.getAttribute('placeholder')),
      label: root.AWOSSafety ? root.AWOSSafety.labelOf(el) : '',
      href: href,
      css: cssPath(el),
      classes: norm(typeof el.className === 'string' ? el.className : ''),
      neighbor: nearbyText(el),
      title: norm(el.getAttribute('title')),
      formId: form ? (form.id || form.getAttribute('name') || '') : '',
      pos: { x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height) },
    };
  }

  function bigrams(s) { var out = {}; for (var i = 0; i < s.length - 1; i++) { var b = s.substr(i, 2); out[b] = (out[b] || 0) + 1; } return out; }
  function dice(a, b) {
    if (a.length < 2 || b.length < 2) return a === b ? 1 : 0;
    var A = bigrams(a), B = bigrams(b), inter = 0, na = a.length - 1, nb = b.length - 1;
    for (var k in A) if (B[k]) inter += Math.min(A[k], B[k]);
    return 2 * inter / (na + nb);
  }
  function tokens(s) { return s.split(/[^a-z0-9]+/).filter(Boolean); }
  function jaccard(a, b) {
    var A = tokens(a), B = tokens(b); if (!A.length || !B.length) return 0;
    var setA = {}, setB = {}, uni = {}, inter = 0;
    A.forEach(function (t) { setA[t] = 1; uni[t] = 1; });
    B.forEach(function (t) { setB[t] = 1; uni[t] = 1; });
    for (var t in setA) if (setB[t]) inter++;
    return inter / Object.keys(uni).length;
  }
  function strSim(a, b) {
    a = low(a); b = low(b);
    if (a === b) return 1;
    if (!a || !b) return 0;
    return Math.max(dice(a, b), jaccard(a, b));
  }
  function cssSim(a, b) {
    if (a === b) return 1; if (!a || !b) return 0;
    var A = a.split(' > '), B = b.split(' > '), same = 0;
    for (var i = 1; i <= Math.min(A.length, B.length); i++) { if (A[A.length - i] === B[B.length - i]) same++; else break; }
    return same / Math.max(A.length, B.length);
  }
  function posSim(p, q) {
    if (!p || !q) return 0;
    var dx = (p.x + p.w / 2) - (q.x + q.w / 2), dy = (p.y + p.h / 2) - (q.y + q.h / 2);
    return Math.max(0, 1 - Math.sqrt(dx * dx + dy * dy) / 300);
  }

  /** score in [0,1]: weighted similarity over the attributes the recorded locator has. */
  function score(rec, cand) { return detail(rec, cand).score; }

  /** {score, anchor}: anchor = some identity attribute (id, name attr, accessible name,
   * text, label, href, placeholder) still matches strongly. Structure + position alone
   * (the control that now sits where the deleted one was) never counts as found. */
  function detail(rec, cand) {
    var total = 0, got = 0, anchor = false;
    for (var k in WEIGHTS) {
      var rv = rec[k];
      if (k === 'pos') { if (!rv || !rv.w) continue; }
      else if (!rv) continue;
      var w = WEIGHTS[k], s;
      if (k === 'pos') s = posSim(rv, cand.pos);
      else if (k === 'css') s = cssSim(rv, cand.css);
      else if (k === 'tag' || k === 'type' || k === 'role') s = rv === cand[k] ? 1 : 0;
      else if (k === 'id' || k === 'nameAttr' || k === 'formId') s = rv === cand[k] ? 1 : 0.6 * strSim(rv, cand[k]);
      else s = strSim(rv, cand[k]);
      if (s >= ANCHOR_SIM && ANCHORS[k]) anchor = true;
      total += w; got += w * s;
    }
    return { score: total ? got / total : 0, anchor: anchor };
  }

  function visible(el) {
    if (!el.isConnected) return false;
    var r = el.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  }

  var lastScan = [];
  /** a 'press' step (Enter in a field) targets the field: same candidates as 'fill'. */
  function selectorFor(kind) { return KIND_SELECTOR[kind === 'press' ? 'fill' : kind] || KIND_SELECTOR.click; }
  function candidates(kind) { return deepAll(selectorFor(kind)).filter(visible); }

  var SAME_KEYS = ['tag', 'type', 'role', 'name', 'nameAttr', 'placeholder', 'label', 'href', 'context', 'block', 'text', 'formId'];
  /** two scanned controls are copies of one control (identical role + name + label + region + block text ...). */
  function sameControl(a, b) {
    for (var i = 0; i < SAME_KEYS.length; i++) { var k = SAME_KEYS[i]; if (String(a[k] || '') !== String(b[k] || '')) return false; }
    return true;
  }

  /** -> {status, index, score, margin, shortlist:[{index, score, desc}]} ; index into lastScan. */
  function find(rec, kind) {
    var els = candidates(kind), scored = [], page = pageNames();
    lastScan = els;
    for (var i = 0; i < els.length; i++) {
      var d = describe(els[i], page), r = detail(rec, d);
      scored.push({ index: i, score: r.score, anchor: r.anchor, desc: d });
    }
    scored.sort(function (a, b) { return b.score - a.score; });
    // collapse copies of the same control: keep the best-scoring copy of each (recorded position / css path
    // already rank the copies), and measure the margin against the best DIFFERENT control.
    var distinct = [], dupes = 0;
    scored.forEach(function (c) {
      for (var j = 0; j < distinct.length; j++) if (sameControl(distinct[j].desc, c.desc)) { if (j === 0) dupes++; return; }
      distinct.push(c);
    });
    var best = distinct[0], second = distinct[1];
    var margin = best ? best.score - (second ? second.score : 0) : 0;
    var r3 = function (v) { return Math.round(v * 1000) / 1000; };
    var shortlist = distinct.filter(function (c) { return c.score >= FLOOR; }).slice(0, SHORTLIST).map(function (c) {
      return { index: c.index, score: r3(c.score), anchor: c.anchor, desc: brief(c.desc) };
    });
    var status = !best || best.score < FLOOR ? 'not_found'
      : (best.score >= ACCEPT && margin >= MARGIN && best.anchor) ? 'found' : 'ambiguous';
    return { status: status, index: best ? best.index : -1, score: best ? r3(best.score) : 0,
             anchor: !!(best && best.anchor), margin: r3(margin), dupes: dupes, shortlist: shortlist };
  }

  function brief(d) {
    return { tag: d.tag, type: d.type, role: d.role, name: d.name, text: d.text.slice(0, 60), id: d.id,
             nameAttr: d.nameAttr, label: d.label, placeholder: d.placeholder, neighbor: d.neighbor, href: d.href,
             context: d.context || '', block: d.block || '', siblings: d.siblings || [] };
  }

  function element(index) { return lastScan[index] || null; }

  // ---- the verifier's view of the final page (web_tools.outline's fields, from the DOM) -------------
  // alerts = role=alert; statuses = role=status / aria-live=polite; regions = named role=region;
  // dialog = an open dialog's name; text = visible page text; controls = every visible control in
  // document order. Values of sensitive fields (passwords, card numbers, codes) are always masked.
  function textOf(el, n) { return norm(el.innerText || el.textContent).slice(0, n || 600); }
  function observe() {
    var q = function (sel) { return deepAll(sel).filter(visible); };
    var alerts = q('[role=alert]').map(function (e) { return textOf(e); }).filter(Boolean);
    var statuses = q('[role=status], [aria-live=polite]:not([role=alert]), output').map(function (e) { return textOf(e); }).filter(Boolean);
    var regions = q('[role=region][aria-label], [role=region][aria-labelledby]').map(function (e) {
      var nm = contextOf(e.firstElementChild || e) || norm(e.getAttribute('aria-label'));
      var t = textOf(e); return t ? (nm ? nm + ': ' : '') + t : '';
    }).filter(Boolean);
    var dlg = q('dialog[open], [role=dialog], [role=alertdialog]')[0];
    var lines = String(document.body ? document.body.innerText : '').split('\n').map(norm).filter(Boolean);
    var controls = allControls().map(function (el) {
      var role = roleOf(el), d = { role: role === 'radio' ? 'radiogroup' : role, name: accName(el), context: contextOf(el),
                                   inDialog: !!closestDeep(el, 'dialog, [role=dialog], [role=alertdialog]') };
      if (el.tagName === 'SELECT') {
        d.role = 'combobox';
        d.options = Array.prototype.slice.call(el.options).map(function (o) { return norm(o.text); }).slice(0, 12);
        var so = el.options[el.selectedIndex]; d.value = so ? norm(so.text) : '';
      } else if (role === 'checkbox' || el.type === 'radio') {
        d.value = el.checked ? '1' : '0';
      } else if (role === 'textbox' || role === 'password') {
        var sens = root.AWOSSafety && root.AWOSSafety.sensitiveField(el);
        var v = el.isContentEditable ? norm(el.innerText) : String(el.value == null ? '' : el.value);
        d.value = (role === 'password' || sens) && v ? '••••••••' : v.slice(0, 120);
      } else if (role === 'tab') {
        d.value = el.getAttribute('aria-selected') === 'true' ? '1' : '0';
      }
      return d;
    });
    var nav = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
    return { url: location.href, status: (nav && nav.responseStatus) || 0, title: norm(document.title), alerts: alerts, statuses: statuses, regions: regions,
             dialog: dlg ? (contextOf(dlg.firstElementChild || dlg) || norm(dlg.getAttribute('aria-label')) || textOf(dlg, 120)) : '',
             text: lines.join('\n'), controls: controls };
  }

  root.AWOSLocator = {
    ACCEPT: ACCEPT, MARGIN: MARGIN, FLOOR: FLOOR, WEIGHTS: WEIGHTS, KIND_SELECTOR: KIND_SELECTOR,
    describe: describe, score: score, find: find, element: element, brief: brief, observe: observe,
    accName: accName, roleOf: roleOf, strSim: strSim, kindOf: kindOf,
    deepAll: deepAll, closestDeep: closestDeep, deepText: deepText, sameControl: sameControl,
  };

  /** visible text of the page including OPEN shadow roots (innerText stops at a shadow host). */
  function deepText() {
    var parts = [document.body ? document.body.innerText : ''];
    deepAll('*').forEach(function (h) {
      if (!h.shadowRoot) return;
      for (var c = h.shadowRoot.firstElementChild; c; c = c.nextElementSibling) {
        if (!/^(style|script|template)$/i.test(c.tagName)) parts.push(c.innerText || '');
      }
    });
    return parts.join('\n');
  }

  function kindOf(el) {
    var tag = el.tagName.toLowerCase();
    if (tag === 'select') return 'select';
    if (el.matches(KIND_SELECTOR.fill)) return 'fill';
    return 'click';
  }
})(typeof globalThis !== 'undefined' ? globalThis : self);
