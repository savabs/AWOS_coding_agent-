/* AWOS Replay content script: the recorder and the step executor for one page.
 *
 * Recorder (only while the service worker says this tab is recording):
 *   click on a clickable control      -> {action:'click', loc, danger}
 *   typing into a text-like field     -> {action:'fill', loc, value}   (merged per field)
 *   changing a <select>               -> {action:'select', loc, value, optionText}
 *   Enter in a field of a form / search box -> {action:'press', key:'Enter', loc, danger}
 *                                        (replayed as Enter on that field, not as the URL it led to)
 * Targets come from e.composedPath()[0], so controls inside OPEN shadow roots (web components) are
 * recorded as themselves, not as their host element.
 * Sensitive fields (AWOSSafety.sensitiveField) never have their value read
 * into a message: the step carries a {{secret:...}} placeholder instead.
 *
 * Executor (driven by the service worker, one message per phase):
 *   locate {step, timeoutMs} -> AWOSLocator.find result, polled until found or timeout; if still no
 *          anchored match, the page is scrolled in steps (<= 8 viewports, <= 6 s) so lazy-loaded
 *          targets (GitHub release assets) get a chance to appear, re-scanning after each step
 *   quiet  {idleMs, timeoutMs} -> resolves once the DOM stopped changing for idleMs (bounded)
 *   act    {step, index, approved} -> performs the step on that scanned candidate.
 *          A click whose target matches the danger vocabulary is refused here
 *          unless the worker passes approved:true (the worker only does that
 *          after a user click in the extension UI) -- a second, local gate.
 *          strict:true (scheduled run / imported recipe) also gates submit / confirm / order / ...
 *   check  {texts} -> which expected texts are present on the page
 *   page_kind -> AWOSPortal.pageKind(): is this a login / one-time-code / bot-check page (the worker decides whether
 *          the recording expected it; nothing on the page is touched)
 *   unblock -> AWOSPortal.unblock(): dismiss a pop-up / consent banner in the way (close / not now / reject only)
 *   locate also reports `covered` when the found target sits under an overlay or dialog
 *   v0.9: act first waits (<= usableMs, 8 s) until the target is usable: visible, enabled, not under an overlay or a
 *          loading spinner, no aria-busy; the reply carries `waited` {ms, why}, or `unusable` when it never became so
 *   peek {url} -> the first 1 KB of a downloaded file, re-read on this page's origin only (file content check)
 *   popup_close {n, name} -> AWOSPortal.closeBlocked: close a pop-up the agent brain read as harmless
 *   agent_observe / agent_act {act, approved, expectName} / agent_secrets -> Tell it (lib/agent.js contract):
 *          act on control n of the last observation, with the same danger / sensitive-field gates
 * v0.7: a recorded click inside a repeated list / table row is marked `variable` (goal-guided replay).
 * v0.9.1: every recorded step carries `dialog` {name, tab} when its control sat in a dialog (the app's Settings window),
 *   and `innerScroll` (the scrollTop of the inner scroll box it sat in); the lazy-target scroll search scrolls inner
 *   scroll boxes (the open dialog's first) step by step before the window, and puts them back when nothing turns up.
 *   unblock / popup_close take `keep` (the step's target) so the app's own windows are never closed;
 *   rec_context -> the modal window open when recording starts.
 * Messages are plain JSON; element handles never leave the page.
 */
(function () {
  'use strict';
  if (window.__awosReplayLoaded) return;
  window.__awosReplayLoaded = true;
  var S = window.AWOSSafety, L = window.AWOSLocator, P = window.AWOSPortal;
  var recording = false, lastFillKey = null, keySeq = 0, sentValue = {};
  var lastEnter = null;         // {form, t}: the Enter that submitted a form (its implicit default-button click is not a step)

  function send(msg) { try { chrome.runtime.sendMessage(msg); } catch (e) { /* worker restarting */ } }
  function keyOf(el) { if (!el.__awosKey) el.__awosKey = 'f' + (++keySeq) + '-' + Date.now(); return el.__awosKey; }

  function fieldValue(el) {
    var reason = S.sensitiveField(el);
    if (reason) return { value: S.placeholderFor(reason), sensitive: reason };
    var v = el.isContentEditable ? (el.innerText || '') : el.value;
    return { value: String(v == null ? '' : v), sensitive: null };
  }

  /** the real target, also inside an OPEN shadow root (e.target is retargeted to the host). */
  function realTarget(e) {
    var p = e.composedPath ? e.composedPath() : null, t = p && p.length ? p[0] : null;
    if (t && t.nodeType !== 1) t = t.parentElement;
    return t || e.target;
  }

  /** the button an Enter in this field would press (HTML implicit submission), or null. */
  function defaultButton(el) {
    var f = el.form;
    return f ? f.querySelector('button:not([type]), button[type=submit], input[type=submit], input[type=image]') : null;
  }
  /** danger text for an Enter in a field: the field, its form, and the form's default button. */
  function pressDanger(el, name, strict) {
    var b = defaultButton(el);
    return S.dangerMatchStrict([S.dangerContext(el, name), b ? S.dangerContext(b, L.accName(b)) : ''].join(' | '), strict === 'tell' ? 'tell' : strict === true);
  }

  function onClick(e) {
    if (!recording || !e.isTrusted) return;
    var t = realTarget(e), el = t && L.closestDeep(t, L.KIND_SELECTOR.click);
    if (!el || el.matches(L.KIND_SELECTOR.fill) || el.tagName === 'SELECT') return;
    if (lastEnter && el.form && el.form === lastEnter.form && Date.now() - lastEnter.t < 600) return;   // Enter's implicit submit click
    var loc = L.describe(el);
    lastFillKey = null;
    var step = { action: 'click', loc: loc, url: location.href, scrollY: Math.round(scrollY),
                 danger: S.dangerMatch(S.dangerContext(el, loc.name)) };
    if (repeatedRow(el)) step.variable = true;          // v0.7: a row of a list / table (which row it is can change)
    recContext(step, el);
    send({ type: 'rec_step', step: step });
  }

  /** v0.9.1: where the control was: the app window (dialog) it sat in and its tab, and the scroll position of the
   *  inner scroll box it sat in (a Settings dialog's list scrolls inside the dialog, not the window). */
  function recContext(step, el) {
    var d = P && P.dialogOf(el);
    if (d) step.dialog = d;
    var box = scrollBoxOf(el);
    if (box) step.innerScroll = Math.round(box.scrollTop);
    return step;
  }
  function scrollable(e) {
    if (!e || e.nodeType !== 1 || e === document.body || e === document.documentElement) return false;
    if (e.scrollHeight <= e.clientHeight + 4) return false;
    var oy = getComputedStyle(e).overflowY;
    return oy === 'auto' || oy === 'scroll' || oy === 'overlay';
  }
  /** the nearest scrollable ancestor of el (through open shadow roots), or null. */
  function scrollBoxOf(el) {
    for (var n = el && (el.parentElement || (el.parentNode && el.parentNode.host)); n; n = n.parentElement || (n.parentNode && n.parentNode.host)) {
      if (scrollable(n)) return n;
    }
    return null;
  }
  /** visible inner scroll boxes now, the ones inside an open dialog first, then the largest. */
  function scrollBoxes() {
    var all = L.deepAll('*').filter(function (e) { return scrollable(e) && (!P || P.vis(e)); });
    all = all.filter(function (e) { return !all.some(function (o) { return o !== e && P && P.containsDeep(e, o) && o.clientHeight >= e.clientHeight * 0.8; }); });
    var inDlg = function (e) { return !!(P && L.closestDeep(e, P.MODAL_SEL)); };
    return all.sort(function (a, b) { return (inDlg(b) - inDlg(a)) || (b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight); }).slice(0, 2);
  }

  /** v0.7: the control sits in a repeated list / table row: a tr / li / row-like ancestor (<= 6 levels up) whose parent
   *  has at least 3 children of the same kind (an invoice list, a statements table). Which row is meant can change
   *  from run to run, so replay decides it by meaning when it does not match confidently. */
  function repeatedRow(el) {
    var n = el;
    for (var k = 0; k < 6 && n && n.parentElement; k++, n = n.parentElement) {
      var tag = n.tagName, role = n.getAttribute && n.getAttribute('role');
      if (!/^(TR|LI|ARTICLE)$/.test(tag) && !/^(row|listitem|article)$/.test(role || '')) continue;
      var same = Array.prototype.filter.call(n.parentElement.children, function (c) { return c.tagName === tag && (c.getAttribute('role') || '') === (role || ''); });
      if (same.length >= 3) return true;
    }
    return false;
  }

  /** Enter in a single-line field inside a form or a search box: the submit IS this keypress. */
  function onKeyDown(e) {
    if (!recording || !e.isTrusted || e.key !== 'Enter' || e.isComposing || e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;
    var el = realTarget(e);
    if (!el || !el.matches || !el.matches(L.KIND_SELECTOR.fill) || el.tagName === 'TEXTAREA' || el.isContentEditable) return;
    var inSearch = el.form || L.closestDeep(el, '[role=search], [role=combobox], [role=searchbox]')
      || /^(search)$/i.test(el.getAttribute('type') || '') || /^(combobox|searchbox)$/.test(el.getAttribute('role') || '');
    if (!inSearch) return;
    var loc = L.describe(el);
    lastFillKey = null; lastEnter = { form: el.form || null, t: Date.now() };
    send({ type: 'rec_step', step: recContext({ action: 'press', key: 'Enter', loc: loc, url: location.href, scrollY: Math.round(scrollY),
           danger: pressDanger(el, loc.name, false) }, el) });
  }

  function onInput(e) {
    if (!recording) return;
    var el = realTarget(e);
    if (!el || !el.matches) return;
    if (el.tagName === 'SELECT') {          // isTrusted not required: some UIs set selects by script
      if (e.type !== 'change') return;
      var opt = el.options[el.selectedIndex];
      lastFillKey = null;
      send({ type: 'rec_step', step: recContext({ action: 'select', loc: L.describe(el), url: location.href,
             value: el.value, optionText: opt ? S.norm(opt.text) : '' }, el) });
      return;
    }
    if (!e.isTrusted || (!el.matches(L.KIND_SELECTOR.fill) && !el.isContentEditable)) return;
    var key = keyOf(el), fv = fieldValue(el);
    if (sentValue[key] === fv.value) return;     // the blur-time 'change' repeats what 'input' already sent
    send({ type: 'rec_step', merge: key === lastFillKey, mergeKey: key,
           step: recContext({ action: 'fill', loc: L.describe(el), url: location.href, value: fv.value, sensitive: fv.sensitive }, el) });
    lastFillKey = key; sentValue[key] = fv.value;
  }

  document.addEventListener('click', onClick, true);
  // v0.9.1: a file the page builds itself (fetch -> Blob -> a temporary <a download>, often clicked by script) is saved
  // under the name the page gives it; the worker learns that name here (the browser may not report it, or late)
  document.addEventListener('click', function (e) {
    var t = realTarget(e), a = t && L.closestDeep(t, 'a[download]');
    if (a && /^(blob|data):/i.test(a.href || '') && a.getAttribute('download')) send({ type: 'dl_hint', href: String(a.href).slice(0, 300), name: String(a.getAttribute('download')).slice(0, 200) });
  }, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onInput, true);
  document.addEventListener('keydown', onKeyDown, true);

  // ---- executor -------------------------------------------------------------

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  var SCROLL_STEPS = 8, SCROLL_MS = 6000, SCROLL_WAIT = 450;
  /** nothing anchored on the page yet: scroll down in viewport steps (first to the recorded spot), re-scanning
   *  after each, so content that loads when scrolled into view (lazy lists, include-fragments) can appear.
   *  Bounded by SCROLL_STEPS viewports and SCROLL_MS. Scroll position is restored when nothing turns up.
   *  v0.9.1: inner scroll boxes first (<= 2, the open dialog's first; the recorded innerScroll, then box-height steps),
   *  each put back when nothing turns up, then the window as before. */
  async function scrollSearch(step, res) {
    var t0 = Date.now(), tried = 0, r;
    // v0.9.1: first the inner scroll boxes (a dialog's list scrolls inside the dialog), then the window
    var boxes = scrollBoxes();
    for (var b = 0; b < boxes.length && Date.now() - t0 < SCROLL_MS; b++) {
      var box = boxes[b], top0 = box.scrollTop, bh = Math.max(120, Math.round(box.clientHeight * 0.9)), tops = [];
      if (step.innerScroll > 0) tops.push(step.innerScroll);
      for (var j = 1; j <= SCROLL_STEPS; j++) tops.push(top0 + j * bh);
      var stuckB = 0;
      for (var m = 0; m < tops.length && Date.now() - t0 < SCROLL_MS; m++) {
        var prev = box.scrollTop;
        box.scrollTop = tops[m]; tried++;
        await sleep(SCROLL_WAIT);
        r = L.find(step.loc, step.action);
        if (r.status === 'found' || (r.status === 'ambiguous' && r.anchor)) { r.scrolled = { steps: tried, y: Math.round(scrollY), box: Math.round(box.scrollTop), ms: Date.now() - t0 }; return r; }
        if (m > 0 && Math.abs(box.scrollTop - prev) < 2) { if (++stuckB >= 2) break; } else stuckB = 0;
      }
      await sleep(300);
      r = L.find(step.loc, step.action);
      if (r.status === 'found' || (r.status === 'ambiguous' && r.anchor)) { r.scrolled = { steps: tried, y: Math.round(scrollY), box: Math.round(box.scrollTop), ms: Date.now() - t0 }; return r; }
      box.scrollTop = top0;                                            // nothing there: put the box back
    }
    var se = document.scrollingElement || document.documentElement;
    if (!se || se.scrollHeight <= innerHeight + 4) {                  // nothing to scroll: lazy content cannot be below
      if (!tried) return res;
      res = L.find(step.loc, step.action);
      res.scrolled = { steps: tried, y: -1, ms: Date.now() - t0 };
      return res;
    }
    var x0 = scrollX, y0 = scrollY, H = Math.max(200, Math.round(innerHeight * 0.9)), t1 = Date.now();
    var ys = [], p = step.loc && step.loc.pos;
    var recY = step.scrollY > 0 ? step.scrollY : (p && p.y > innerHeight / 2 ? p.y - innerHeight / 2 : 0);
    if (recY > 0) ys.push(recY);
    for (var k = 1; k <= SCROLL_STEPS; k++) ys.push(y0 + k * H);
    var stuck = 0;
    for (var i = 0; i < ys.length && Date.now() - t1 < SCROLL_MS; i++) {
      var before = scrollY;
      window.scrollTo(x0, ys[i]); tried++;
      await sleep(SCROLL_WAIT);
      r = L.find(step.loc, step.action);
      if (r.status === 'found') { r.scrolled = { steps: tried, y: Math.round(scrollY), ms: Date.now() - t0 }; return r; }
      if (i > 0 && Math.abs(scrollY - before) < 2) { if (++stuck >= 2) break; } else stuck = 0;   // at the bottom
    }
    var tail = Math.min(t1 + SCROLL_MS, Date.now() + 1500);           // the last fragment may still be loading
    while (Date.now() < tail) {
      await sleep(300);
      r = L.find(step.loc, step.action);
      if (r.status === 'found') { r.scrolled = { steps: tried, y: Math.round(scrollY), ms: Date.now() - t0 }; return r; }
    }
    window.scrollTo(x0, y0);
    res = L.find(step.loc, step.action);
    res.scrolled = { steps: tried, y: -1, ms: Date.now() - t0 };
    return res;
  }

  function dangerOf(action, el, name) {
    if (!el) return null;
    if (action === 'click') return S.dangerMatch(S.dangerContext(el, name));
    if (action === 'press') return pressDanger(el, name, false);
    return null;
  }

  async function locate(step, timeoutMs) {
    var deadline = Date.now() + (timeoutMs || 4000), res;
    for (;;) {
      res = L.find(step.loc, step.action);
      if (res.status === 'found' || Date.now() >= deadline) break;
      await sleep(200);
    }
    if (res.status !== 'found' && !(res.status === 'ambiguous' && res.anchor)) res = await scrollSearch(step, res);
    res.dangers = {};
    res.shortlist.forEach(function (c) {
      var d = dangerOf(step.action, L.element(c.index), c.desc.name);
      if (d) res.dangers[c.index] = d;
    });
    if (res.index >= 0 && !(res.index in res.dangers)) {
      var bel = L.element(res.index), bd = dangerOf(step.action, bel, bel ? L.accName(bel) : '');
      if (bd) res.dangers[res.index] = bd;
    }
    if (res.index >= 0 && P) {                       // found, but drawn under a pop-up (a "what's new" modal after login)
      var cov = P.coveredBy(L.element(res.index));
      if (cov) res.covered = { text: S.norm(cov.innerText || cov.textContent).slice(0, 120) };
    }
    return res;
  }

  /** resolves when no node was added / removed / retexted for idleMs, or at timeoutMs (attribute churn such as
   *  map tiles or animations is ignored). -> {quiet: bool, ms} */
  function quiet(idleMs, timeoutMs) {
    return new Promise(function (resolve) {
      var t0 = Date.now(), last = Date.now(), done = false, mo, iv;
      function end(q) { if (done) return; done = true; try { mo.disconnect(); } catch (e) {} clearInterval(iv); resolve({ quiet: q, ms: Date.now() - t0 }); }
      mo = new MutationObserver(function () { last = Date.now(); });
      try { mo.observe(document, { childList: true, subtree: true, characterData: true }); } catch (e) { return end(true); }
      iv = setInterval(function () {
        if (Date.now() - last >= (idleMs || 300)) end(true);
        else if (Date.now() - t0 >= (timeoutMs || 3000)) end(false);
      }, 50);
    });
  }

  function setValue(el, v) {
    if (el.isContentEditable) { el.focus(); el.innerText = v; }
    else {
      var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
        : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      el.focus();
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    }
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // ---- wait until usable (v0.9): visible, enabled, not covered, no busy spinner over it ------------------------
  var SPINNER_SEL = '[role=progressbar], [aria-busy=true], .spinner, .loading, .loader, [class*=spinner i], [class*=loading i], [class*=loader i]';
  function boxOf(el) { return el.getBoundingClientRect(); }
  function overlaps(a, b) { return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom; }
  function spinnerLike(n) {
    for (var k = 0, e = n; e && e.nodeType === 1 && k < 4; k++, e = e.parentElement) {
      try { if (e.matches(SPINNER_SEL)) return e; } catch (x) { return null; }
    }
    return null;
  }
  /** why el cannot be used right now ('' = usable). Clicks / Enter need it visible; every action needs it enabled,
   *  not under an overlay or a spinner (the top element at its centre is el, inside it, or not overlay-like), and no
   *  busy marker (aria-busy) on it or around it. A cover that is not an overlay / spinner (a stretched link) does not
   *  count: el.click() reaches the target anyway. */
  function unusableWhy(el, action) {
    if (!el || !el.isConnected) return 'gone';
    var r = boxOf(el), cs = getComputedStyle(el), clicky = action === 'click' || action === 'press';
    if (clicky && (r.width <= 0 || r.height <= 0 || cs.visibility === 'hidden' || cs.display === 'none')) return 'not visible';
    if (el.disabled === true || el.getAttribute('aria-disabled') === 'true' || (el.matches && el.matches(':disabled'))) return 'disabled';
    if (action === 'fill' && el.readOnly === true) return 'read-only';
    var busy = L.closestDeep(el, '[aria-busy=true]');
    if (busy) return 'busy (aria-busy)';
    if (r.width > 0 && r.height > 0 && P) {
      var cx = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), cy = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
      var t = P.topAt(cx, cy);
      if (t && t !== el && !P.containsDeep(el, t) && !P.containsDeep(t, el)) {
        if (spinnerLike(t)) return 'a loading spinner is over it';
        var ov = P.overlayOf(t);
        if (ov && !P.containsDeep(ov, el)) return 'covered by an overlay';
      }
      var spins = L.deepAll(SPINNER_SEL).filter(function (sp) {
        if (sp === el || P.containsDeep(sp, el) || P.containsDeep(el, sp) || !P.vis(sp)) return false;
        return overlaps(boxOf(sp), r);
      });
      if (spins.length) return 'a loading spinner is over it';
    }
    return '';
  }
  /** polls (100 ms) until el is usable or maxMs (default 8 s) -> {ok, ms, why: what it waited for, last} */
  async function waitUsable(el, action, maxMs) {
    var t0 = Date.now(), end = t0 + (maxMs == null ? 8000 : maxMs), first = '', why;
    for (;;) {
      why = unusableWhy(el, action);
      if (!why) return { ok: true, ms: Date.now() - t0, why: first };
      if (!first) first = why;
      if (why === 'gone' || Date.now() >= end) return { ok: false, ms: Date.now() - t0, why: first, last: why };
      await sleep(100);
    }
  }

  async function act(step, index, approved, strict, reply, usableMs) {
    var el = L.element(index);
    if (!el || !el.isConnected) return reply({ ok: false, error: 'element gone' });
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    var w = await waitUsable(el, step.action, usableMs);
    if (!w.ok) return reply({ ok: false, unusable: w.last || w.why, waited: w, error: 'the control is ' + (w.last || w.why) + ' after ' + Math.round(w.ms / 100) / 10 + ' s' });
    var waited = w.why ? w : null;
    if (step.action === 'fill') {
      if (S.isPlaceholder(step.value)) return reply({ ok: false, error: 'placeholder value; the user must type it' });
      setValue(el, step.value);
      return reply({ ok: true, waited: waited });
    }
    if (step.action === 'select') {
      var opts = Array.prototype.slice.call(el.options);
      var o = opts.find(function (x) { return x.value === step.value; })
           || opts.find(function (x) { return S.norm(x.text) === step.optionText; });
      if (!o) return reply({ ok: false, error: 'option not found: ' + (step.optionText || step.value) });
      setValue(el, o.value);
      return reply({ ok: true, waited: waited });
    }
    if (step.action === 'press') {
      var pd = pressDanger(el, L.accName(el), strict) || step.danger;
      if (pd && approved !== true) return reply({ ok: false, needsApproval: true, danger: pd });
      reply({ ok: true, url: location.href, waited: waited });
      setTimeout(function () { pressEnter(el); }, 0);
      return;
    }
    var danger = S.dangerMatchStrict(S.dangerContext(el, L.accName(el)), strict === true) || step.danger;
    if (danger && approved !== true) return reply({ ok: false, needsApproval: true, danger: danger });
    reply({ ok: true, url: location.href, waited: waited });   // reply before clicking: the click may unload this page
    setTimeout(function () { el.focus && el.focus(); el.click(); }, 0);
  }

  /** v0.9 file check: the first <= 1024 bytes of a downloaded file, read again on THIS page's origin only (the
   *  portal's cookies apply, as for the download). Another origin is not read: the worker says "content not checked".
   *  -> {ok, status, type, head (latin1), why} */
  async function peek(url) {
    var u; try { u = new URL(url, location.href); } catch (e) { return { ok: false, why: 'not a web address' }; }
    if (!/^(https?|blob):$/.test(u.protocol) || u.origin !== location.origin) return { ok: false, why: 'the file is on another origin than the portal page' };
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 8000);
    try {
      var resp = await fetch(u.href, { credentials: 'include', cache: 'no-store', headers: u.protocol === 'blob:' ? {} : { Range: 'bytes=0-1023' }, signal: ctl.signal });
      var out = { ok: resp.ok, status: resp.status, type: resp.headers.get('content-type') || '', redirected: !!resp.redirected };
      if (!resp.ok) { out.why = 'reading it again answered HTTP ' + resp.status; return out; }
      var bytes = new Uint8Array(0);
      if (resp.body && resp.body.getReader) {
        var rd = resp.body.getReader();
        while (bytes.length < 1024) {
          var c = await rd.read();
          if (c.done) break;
          var nb = new Uint8Array(bytes.length + c.value.length); nb.set(bytes); nb.set(c.value, bytes.length); bytes = nb;
        }
        try { rd.cancel(); } catch (e) {}
      } else bytes = new Uint8Array(await resp.arrayBuffer());
      bytes = bytes.slice(0, 1024);
      var head = ''; for (var k = 0; k < bytes.length; k++) head += String.fromCharCode(bytes[k]);
      out.head = head;
      return out;
    } catch (e) { return { ok: false, why: 'could not read it again (' + String(e && e.message || e).slice(0, 80) + ')' }; }
    finally { clearTimeout(timer); }
  }

  /** Enter on a field as a person would: key events for page handlers (search boxes, comboboxes), then, unless a
   *  handler cancelled it, HTML implicit submission (click the form's default button, else requestSubmit). */
  function pressEnter(el) {
    el.focus && el.focus();
    var o = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
    var go = el.dispatchEvent(new KeyboardEvent('keydown', o));
    go = el.dispatchEvent(new KeyboardEvent('keypress', o)) && go;
    var submit = function () {
      var b = defaultButton(el);
      if (b && !b.disabled) b.click();
      else if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit();
    };
    if (go && el.isConnected && el.form) submit();
    if (el.isConnected) el.dispatchEvent(new KeyboardEvent('keyup', o));
    // A page handler cancelled the synthetic Enter but then did nothing with it (sites that only act on a trusted
    // key press, e.g. DuckDuckGo's search box: a real run typed the same query 22 times). If ~1.2 s later the
    // address is the same and the field is still in its form, submit the form the way the browser would.
    if (!go && el.form) {
      var before = location.href;
      setTimeout(function () {
        if (location.href === before && el.isConnected && el.form) submit();
      }, 1200);
    }
  }

  // ---- Tell it: act on control n of the last observe() (the agent's numbering) -----------------------------
  // The service worker validated the action against the observation already; this is the second, local gate:
  // approval-gated clicks / Enters (AWOSSafety TELL_RE: pay, send, delete, submit, place order, confirm, unsubscribe ...) are refused
  // unless approved:true, sensitive fields are never filled (the user types them), and the control must still
  // carry the name the agent saw (expectName) so an approval binds to that exact control.
  function agentAct(msg, reply) {
    var a = msg.act || {}, el = L.observed(a.n);
    if (a.verb === 'scroll') { window.scrollBy(0, (a.direction === 'up' ? -1 : 1) * Math.round(innerHeight * 0.8)); return reply({ ok: true }); }
    if (!el || !el.isConnected) return reply({ ok: false, error: 'control ' + a.n + ' is gone (the page changed)' });
    var name = L.accName(el);
    if (msg.expectName != null && name !== msg.expectName) return reply({ ok: false, error: 'control ' + a.n + ' changed (the page changed)' });
    var loc = L.describe(el);
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    if (a.verb === 'click' || (a.verb === 'select' && el.tagName !== 'SELECT')) {
      var d = S.dangerMatchStrict(S.dangerContext(el, name), 'tell');
      if (d && msg.approved !== true) return reply({ ok: false, needsApproval: true, danger: d, loc: loc });
      reply({ ok: true, loc: loc, url: location.href, asClick: true, recDanger: S.dangerMatch(S.dangerContext(el, loc.name)) });
      setTimeout(function () { el.focus && el.focus(); el.click(); }, 0);
      return;
    }
    if (a.verb === 'fill' || a.verb === 'fill_enter') {
      var sr = S.sensitiveField(el);
      if (sr) return reply({ ok: false, secret: sr, loc: loc });
      if (a.verb === 'fill_enter') {
        var pd = pressDanger(el, name, 'tell');
        if (pd && msg.approved !== true) return reply({ ok: false, needsApproval: true, danger: pd, loc: loc });
      }
      setValue(el, String(a.value == null ? '' : a.value));
      if (a.verb === 'fill') return reply({ ok: true, loc: loc });
      reply({ ok: true, loc: loc, url: location.href, recDanger: pressDanger(el, name, false) });
      setTimeout(function () { pressEnter(el); }, 0);
      return;
    }
    if (a.verb === 'select') {
      var want = S.norm(a.option).toLowerCase();
      var o = Array.prototype.slice.call(el.options).find(function (x) { return S.norm(x.text).toLowerCase() === want; });
      if (!o) return reply({ ok: false, error: '"' + a.option + '" is not an option here' });
      setValue(el, o.value);
      return reply({ ok: true, loc: loc, value: o.value, optionText: S.norm(o.text) });
    }
    reply({ ok: false, error: 'unknown action' });
  }
  /** sensitive fields the user has typed into (values never leave the page: only the locator and the reason). */
  function agentSecrets() {
    return L.deepAll(L.KIND_SELECTOR.fill).filter(function (el) {
      return S.sensitiveField(el) && String(el.value || '').length > 0;
    }).map(function (el) { return { loc: L.describe(el), reason: S.sensitiveField(el) }; });
  }

  /** v0.8 phone link: the one-time code the user sent from their phone (or an SMS they forwarded) goes into the
   *  page's one-time-code field, then Enter / the form's default button, as a person would. Only on a page that has
   *  such a field; the value is never kept or sent anywhere. Messages come only from the extension's own worker. */
  function phoneCode(code, reply) {
    code = String(code || '');
    if (!/^[A-Za-z0-9]{4,8}$/.test(code)) return reply({ ok: false, error: 'not a code' });
    var el = P && P.otpField();
    if (!el) return reply({ ok: false, error: 'no one-time-code field on this page' });
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    setValue(el, code);
    code = '';
    reply({ ok: true, field: L.accName(el) || 'code field', url: location.href });   // reply first: submitting may unload the page
    setTimeout(function () { pressEnter(el); }, 0);
  }

  function banner(text) {
    var b = document.getElementById('__awos_banner');
    if (!text) { if (b) b.remove(); return; }
    if (!b) {
      b = document.createElement('div'); b.id = '__awos_banner';
      b.style.cssText = 'position:fixed;z-index:2147483647;left:12px;right:12px;bottom:12px;padding:10px 14px;'
        + 'background:#1f2328;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.3)';
      document.documentElement.appendChild(b);
    }
    b.textContent = 'AWOS Replay: ' + text;
  }

  chrome.runtime.onMessage.addListener(function (msg, _sender, reply) {
    switch (msg && msg.type) {
      case 'ping': reply({ ok: true, url: location.href, recording: recording, contentType: document.contentType || '' }); return;
      case 'page_kind': reply(P.pageKind()); return;
      case 'unblock': reply(P.unblock(msg.keep)); return;           // v0.9.1: keep = the step's target / recorded window
      case 'open_dialogs': reply({ titles: P.openDialogs() }); return;     // v0.9.1: windows a replayed step opened
      case 'rec_context': reply({ dialog: P.openDialog(), url: location.href }); return;   // v0.9.1: recording starts here
      case 'remember_me': reply(P.rememberMe()); return;                      // v0.6.1, login handoffs only
      case 'autofill_signin': reply(P.autofillSignin(!!msg.testHook)); return; // v0.6.1, never reads a field value
      case 'rec_state': recording = !!msg.on; lastFillKey = null; reply({ ok: true }); return;
      case 'locate': locate(msg.step, msg.timeoutMs).then(reply); return true;
      case 'act':                                                             // async since 0.9 (wait until usable): always replies
        act(msg.step, msg.index, msg.approved, msg.strict, reply, msg.usableMs)
          .catch(function (e) { try { reply({ ok: false, error: 'page script error: ' + String(e && e.message || e) }); } catch (x) {} });
        return true;
      case 'peek': peek(msg.url).then(reply); return true;                    // v0.9: the first bytes of a downloaded file
      case 'popup_close': reply(P.closeBlocked(msg.n, msg.name, msg.keep)); return;     // v0.9: a pop-up the agent brain read as harmless
      case 'quiet': quiet(msg.idleMs, msg.timeoutMs).then(reply); return true;
      case 'check': {
        var body = S.norm(L.deepText());
        reply({ url: location.href, present: (msg.texts || []).map(function (t) { return body.indexOf(S.norm(t)) >= 0; }) });
        return;
      }
      case 'page_text': reply({ url: location.href, text: S.norm(document.body ? document.body.innerText : '').slice(0, 2000) }); return;
      case 'observe': reply(L.observe()); return;             // the final page for the local verifier
      case 'agent_observe': reply(L.observe()); return;       // Tell it: the page, numbered as the agent sees it
      case 'agent_act': agentAct(msg, reply); return;
      case 'agent_secrets': reply({ fields: agentSecrets() }); return;
      case 'banner': banner(msg.text); reply({ ok: true }); return;
      case 'phone_code': phoneCode(msg.code, reply); return;   // v0.8: the user's own code, sent from their phone
    }
  });

  try {
    chrome.runtime.sendMessage({ type: 'hello' }, function (r) {
      if (chrome.runtime.lastError) return;
      recording = !!(r && r.recording);
    });
  } catch (e) {}
})();
