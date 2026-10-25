/* AWOS Replay content script: the recorder and the step executor for one page.
 *
 * Recorder (only while the service worker says this tab is recording):
 *   click on a clickable control      -> {action:'click', loc, danger}
 *   typing into a text-like field     -> {action:'fill', loc, value}   (merged per field)
 *   changing a <select>               -> {action:'select', loc, value, optionText}
 * Sensitive fields (AWOSSafety.sensitiveField) never have their value read
 * into a message: the step carries a {{secret:...}} placeholder instead.
 *
 * Executor (driven by the service worker, one message per phase):
 *   locate {step, timeoutMs} -> AWOSLocator.find result, polled until found or timeout
 *   act    {step, index, approved} -> performs the step on that scanned candidate.
 *          A click whose target matches the danger vocabulary is refused here
 *          unless the worker passes approved:true (the worker only does that
 *          after a user click in the extension UI) -- a second, local gate.
 *   check  {texts} -> which expected texts are present on the page
 * Messages are plain JSON; element handles never leave the page.
 */
(function () {
  'use strict';
  if (window.__awosReplayLoaded) return;
  window.__awosReplayLoaded = true;
  var S = window.AWOSSafety, L = window.AWOSLocator;
  var recording = false, lastFillKey = null, keySeq = 0, sentValue = {};

  function send(msg) { try { chrome.runtime.sendMessage(msg); } catch (e) { /* worker restarting */ } }
  function keyOf(el) { if (!el.__awosKey) el.__awosKey = 'f' + (++keySeq) + '-' + Date.now(); return el.__awosKey; }

  function fieldValue(el) {
    var reason = S.sensitiveField(el);
    if (reason) return { value: S.placeholderFor(reason), sensitive: reason };
    var v = el.isContentEditable ? (el.innerText || '') : el.value;
    return { value: String(v == null ? '' : v), sensitive: null };
  }

  function onClick(e) {
    if (!recording || !e.isTrusted) return;
    var el = e.target && e.target.closest && e.target.closest(L.KIND_SELECTOR.click);
    if (!el || el.matches(L.KIND_SELECTOR.fill) || el.tagName === 'SELECT') return;
    var loc = L.describe(el);
    lastFillKey = null;
    send({ type: 'rec_step', step: { action: 'click', loc: loc, url: location.href,
           danger: S.dangerMatch(S.dangerContext(el, loc.name)) } });
  }

  function onInput(e) {
    if (!recording) return;
    var el = e.target;
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

  // ---- executor -------------------------------------------------------------

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  async function locate(step, timeoutMs) {
    var deadline = Date.now() + (timeoutMs || 4000), res;
    for (;;) {
      res = L.find(step.loc, step.action);
      if (res.status === 'found' || Date.now() >= deadline) break;
      await sleep(200);
    }
    res.dangers = {};
    res.shortlist.forEach(function (c) {
      var el = L.element(c.index);
      var d = step.action === 'click' && el ? S.dangerMatch(S.dangerContext(el, c.desc.name)) : null;
      if (d) res.dangers[c.index] = d;
    });
    if (res.index >= 0 && step.action === 'click' && !(res.index in res.dangers)) {
      var bel = L.element(res.index), bd = bel ? S.dangerMatch(S.dangerContext(bel, L.accName(bel))) : null;
      if (bd) res.dangers[res.index] = bd;
    }
    return res;
  }

  function setValue(el, v) {
    if (el.isContentEditable) { el.focus(); el.innerText = v; }
    else {
      var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
        : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      el.focus();
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function act(step, index, approved, reply) {
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
    var danger = S.dangerMatch(S.dangerContext(el, L.accName(el))) || step.danger;
    if (danger && approved !== true) return reply({ ok: false, needsApproval: true, danger: danger });
    reply({ ok: true });                 // reply before clicking: the click may unload this page
    setTimeout(function () { el.focus && el.focus(); el.click(); }, 0);
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
      case 'act': act(msg.step, msg.index, msg.approved, reply); return;
      case 'check': {
        var body = S.norm(document.body ? document.body.innerText : '');
        reply({ url: location.href, present: (msg.texts || []).map(function (t) { return body.indexOf(S.norm(t)) >= 0; }) });
        return;
      }
      case 'page_text': reply({ url: location.href, text: S.norm(document.body ? document.body.innerText : '').slice(0, 2000) }); return;
      case 'observe': reply(L.observe()); return;             // the final page for the local verifier
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
