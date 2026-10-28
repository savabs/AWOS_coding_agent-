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
 *   agent_observe / agent_act {act, approved, expectName} / agent_secrets -> Tell it (lib/agent.js contract):
 *          act on control n of the last observation, with the same danger / sensitive-field gates
 * Messages are plain JSON; element handles never leave the page.
 */
(function () {
  'use strict';
  if (window.__awosReplayLoaded) return;
  window.__awosReplayLoaded = true;
  var S = window.AWOSSafety, L = window.AWOSLocator;
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
    send({ type: 'rec_step', step: { action: 'click', loc: loc, url: location.href, scrollY: Math.round(scrollY),
           danger: S.dangerMatch(S.dangerContext(el, loc.name)) } });
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
    send({ type: 'rec_step', step: { action: 'press', key: 'Enter', loc: loc, url: location.href, scrollY: Math.round(scrollY),
           danger: pressDanger(el, loc.name, false) } });
  }

  function onInput(e) {
    if (!recording) return;
    var el = realTarget(e);
    if (!el || !el.matches) return;
    if (el.tagName === 'SELECT') {          // isTrusted not required: some UIs set selects by script
      if (e.type !== 'change') return;
      var opt = el.options[el.selectedIndex];
      lastFillKey = null;
      send({ type: 'rec_step', step: { action: 'select', loc: L.describe(el), url: location.href,
             value: el.value, optionText: opt ? S.norm(opt.text) : '' } });
      return;
    }
    if (!e.isTrusted || (!el.matches(L.KIND_SELECTOR.fill) && !el.isContentEditable)) return;
    var key = keyOf(el), fv = fieldValue(el);
    if (sentValue[key] === fv.value) return;     // the blur-time 'change' repeats what 'input' already sent
    send({ type: 'rec_step', merge: key === lastFillKey, mergeKey: key,
           step: { action: 'fill', loc: L.describe(el), url: location.href, value: fv.value, sensitive: fv.sensitive } });
    lastFillKey = key; sentValue[key] = fv.value;
  }

  document.addEventListener('click', onClick, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onInput, true);
  document.addEventListener('keydown', onKeyDown, true);

  // ---- executor -------------------------------------------------------------

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  var SCROLL_STEPS = 8, SCROLL_MS = 6000, SCROLL_WAIT = 450;
  /** nothing anchored on the page yet: scroll down in viewport steps (first to the recorded spot), re-scanning
   *  after each, so content that loads when scrolled into view (lazy lists, include-fragments) can appear.
   *  Bounded by SCROLL_STEPS viewports and SCROLL_MS. Scroll position is restored when nothing turns up. */
  async function scrollSearch(step, res) {
    var se = document.scrollingElement || document.documentElement;
    if (!se || se.scrollHeight <= innerHeight + 4) return res;          // nothing to scroll: lazy content cannot be below
    var x0 = scrollX, y0 = scrollY, t0 = Date.now(), H = Math.max(200, Math.round(innerHeight * 0.9));
    var ys = [], p = step.loc && step.loc.pos;
    var recY = step.scrollY > 0 ? step.scrollY : (p && p.y > innerHeight / 2 ? p.y - innerHeight / 2 : 0);
    if (recY > 0) ys.push(recY);
    for (var k = 1; k <= SCROLL_STEPS; k++) ys.push(y0 + k * H);
    var stuck = 0, tried = 0, r;
    for (var i = 0; i < ys.length && Date.now() - t0 < SCROLL_MS; i++) {
      var before = scrollY;
      window.scrollTo(x0, ys[i]); tried++;
      await sleep(SCROLL_WAIT);
      r = L.find(step.loc, step.action);
      if (r.status === 'found') { r.scrolled = { steps: tried, y: Math.round(scrollY), ms: Date.now() - t0 }; return r; }
      if (i > 0 && Math.abs(scrollY - before) < 2) { if (++stuck >= 2) break; } else stuck = 0;   // at the bottom
    }
    var tail = Math.min(t0 + SCROLL_MS, Date.now() + 1500);           // the last fragment may still be loading
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

  function act(step, index, approved, strict, reply) {
    var el = L.element(index);
    if (!el || !el.isConnected) return reply({ ok: false, error: 'element gone' });
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    if (step.action === 'fill') {
      if (S.isPlaceholder(step.value)) return reply({ ok: false, error: 'placeholder value; the user must type it' });
      setValue(el, step.value);
      return reply({ ok: true });
    }
    if (step.action === 'select') {
      var opts = Array.prototype.slice.call(el.options);
      var o = opts.find(function (x) { return x.value === step.value; })
           || opts.find(function (x) { return S.norm(x.text) === step.optionText; });
      if (!o) return reply({ ok: false, error: 'option not found: ' + (step.optionText || step.value) });
      setValue(el, o.value);
      return reply({ ok: true });
    }
    if (step.action === 'press') {
      var pd = pressDanger(el, L.accName(el), strict) || step.danger;
      if (pd && approved !== true) return reply({ ok: false, needsApproval: true, danger: pd });
      reply({ ok: true, url: location.href });
      setTimeout(function () { pressEnter(el); }, 0);
      return;
    }
    var danger = S.dangerMatchStrict(S.dangerContext(el, L.accName(el)), strict === true) || step.danger;
    if (danger && approved !== true) return reply({ ok: false, needsApproval: true, danger: danger });
    reply({ ok: true, url: location.href });   // reply before clicking: the click may unload this page
    setTimeout(function () { el.focus && el.focus(); el.click(); }, 0);
  }

  /** Enter on a field as a person would: key events for page handlers (search boxes, comboboxes), then, unless a
   *  handler cancelled it, HTML implicit submission (click the form's default button, else requestSubmit). */
  function pressEnter(el) {
    el.focus && el.focus();
    var o = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
    var go = el.dispatchEvent(new KeyboardEvent('keydown', o));
    go = el.dispatchEvent(new KeyboardEvent('keypress', o)) && go;
    if (go && el.isConnected && el.form) {
      var b = defaultButton(el);
      if (b && !b.disabled) b.click();
      else if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit();
    }
    if (el.isConnected) el.dispatchEvent(new KeyboardEvent('keyup', o));
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
      case 'ping': reply({ ok: true, url: location.href, recording: recording }); return;
      case 'rec_state': recording = !!msg.on; lastFillKey = null; reply({ ok: true }); return;
      case 'locate': locate(msg.step, msg.timeoutMs).then(reply); return true;
      case 'act': act(msg.step, msg.index, msg.approved, msg.strict, reply); return;
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
    }
  });

  try {
    chrome.runtime.sendMessage({ type: 'hello' }, function (r) {
      if (chrome.runtime.lastError) return;
      recording = !!(r && r.recording);
    });
  } catch (e) {}
})();
