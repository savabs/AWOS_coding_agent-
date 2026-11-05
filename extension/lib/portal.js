/* AWOS Replay: portal-run helpers for the content script (v0.6, docs/specs/portal_run.md).
 *
 *  pageKind() -> {kind: 'captcha' | 'otp' | 'login' | '', why, url}
 *      what the page in front of a replay step is asking a human for. The service worker decides whether that is
 *      unexpected (the recording was not on such a page here) and hands off; this file never touches the page.
 *      captcha: a visible challenge widget (reCAPTCHA / hCaptcha / Turnstile / ... frame or container) or a
 *      "verify you are human" style text. Never solved, clicked or bypassed: the user does it.
 *      otp:     a visible one-time-code field (autocomplete=one-time-code, "verification code", "2FA", ...)
 *      login:   a visible password field, or a "your session expired / log in again" text
 *  otpField()  -> the visible one-time-code field (the same rule as pageKind's otp), or null (v0.8 phone link)
 *  coveredBy(el) -> the overlay / dialog element drawn over el's centre, or null
 *  blockers()   -> visible modals, overlays and cookie / consent banners on the page now
 *  unblock()    -> {dismissed: [{name, kind, text}], blocked: {kind, text, controls[]} | null}
 *      clicks ONE allowed control per blocker: a dismiss control inside a pop-up (close, x, not now, maybe later,
 *      skip, got it, no thanks, remind me later, continue to site), or for a cookie / consent banner only
 *      reject / decline / necessary-only / close. Never "accept", "agree", "allow all", never anything in the
 *      danger vocabulary (pay, send, delete, submit, confirm, ...). A blocker with no allowed control is reported
 *      as `blocked` so the run pauses for the user instead.
 *      `blocked` also carries title, body (<= 800 chars) and ctl [{n, name, hint}] for the agent brain (v0.9)
 *  v0.9.1: unblock(keep) / closeBlocked(n, name, keep) never touch the app's own windows (appDialogWhy): a dialog the
 *      step was recorded in, one holding the step's control, or one with tabs / menus / a Settings-like title; such
 *      dialogs are reported in `kept` and treated as part of the page. dialogOf(el) / openDialog() describe them.
 *  closeBlocked(n, name) -> {ok, name, why}   (v0.9) closes a pop-up the agent brain read as HARMLESS with the
 *      control it named, only when that control's name is a close / dismiss word and no deny rule matches
 *  decisionWord(info) -> the word that makes a pop-up a decision for the user (terms, consent, payment, ...) or ''
 *  rememberMe() -> {ticked: label | '', seen: n}   (v0.6.1)
 *      on a login handoff: ticks ONE unchecked, visible checkbox whose whole label is "remember me" / "keep me signed
 *      in" / "stay signed in" / "trust this device" / "don't ask again on this device" (REMEMBER_RE). Nothing else.
 *  autofillSignin(testHook) -> {clicked: button name | '', why}   (v0.6.1, experimental)
 *      when Chrome has autofilled BOTH the username and the password field of a visible sign-in form (the :autofill /
 *      :-webkit-autofill pseudo-class, and not :placeholder-shown), clicks that form's sign-in button once. It never
 *      reads a field's value (only those pseudo-classes and the field's type), and returns nothing about the fields
 *      except why it did or did not click. `testHook` (passed only by the worker, from chrome.storage.session, which
 *      pages cannot reach) also counts a data-awos-test-autofill attribute as autofilled: Playwright cannot make
 *      Chrome autofill a saved password, so the tests mark the fields that way.
 * Plain script: defines globalThis.AWOSPortal (needs AWOSSafety and AWOSLocator loaded first).
 */
(function (root) {
  'use strict';
  var S = root.AWOSSafety, L = root.AWOSLocator;

  var CAPTCHA_FRAME = /recaptcha|hcaptcha|turnstile|challenges\.cloudflare|captcha|arkoselabs|funcaptcha|geetest|friendlycaptcha/i;
  var CAPTCHA_SEL = '.g-recaptcha, .h-captcha, .cf-turnstile, .frc-captcha, [data-sitekey], [id*=captcha i], [class*=captcha i]';
  var CAPTCHA_TEXT = /(verify (that )?you('| a)re (a )?human|i'?m not a robot|are you a robot|prove you('| a)re (a )?human|human verification|bot check|checking (if the site connection is secure|your browser)|press and hold|complete the (security )?(check|challenge) (below|to continue))/i;
  var EXPIRED_TEXT = /(session (has )?(expired|timed out)|you('ve| have) been (signed|logged) out|(signed|logged) out (due to|because of|after) inactivity|please (sign|log) ?in again|(sign|log) ?in again to continue|your session (has )?ended)/i;
  var OTP_REASON = /^(one-time-code|otp|one-time|one time|onetime|verification code|auth code|authentication code|2fa|totp|mfa)$/;

  // pop-up dismiss controls (the whole accessible name, punctuation-insensitive)
  var DISMISS_RE = /^(close|close (this|dialog|modal|popup|pop up|banner|window|message|notice)|x|×|✕|✖|dismiss|dismiss (this|message|notice|banner)|not now|not right now|maybe later|later|ask me later|remind me later|skip|skip for now|skip (the )?tour|got it|ok,? got it|no thanks|no,? thanks|no thank you|continue to (the )?(site|website|portal|dashboard)|go to (the )?(site|dashboard)|hide|hide (this|message))$/i;
  // cookie / consent banners: only these
  var CONSENT_RE = /^(reject|reject all|reject (all |optional |non-essential |additional )?cookies|decline|decline all|decline (all |optional )?cookies|deny|deny all|refuse|refuse all|necessary only|only necessary|strictly necessary only|(use |allow )?(only )?(strictly )?necessary cookies( only)?|essential only|only essential|essential cookies only|(use |allow )?(only )?essential cookies( only)?|continue without accepting|close|x|×|✕)$/i;
  // never clicked on a blocker, whatever else matches
  var NEVER_RE = /\b(accept|agree|allow all|allow cookies|i consent|consent|subscribe|sign up|upgrade|start (your |a )?(free )?trial|buy|pay|install|enable|turn on|yes)\b/i;
  var CONSENT_HINT = /cookie|consent|gdpr|ccpa|tracking|privacy (preferences|settings|choices)|we value your privacy/i;
  var MODAL_SEL = 'dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true]';
  var BANNER_SEL = '[id*=cookie i], [class*=cookie i], [id*=consent i], [class*=consent i], [aria-label*=cookie i], [aria-label*=consent i]';

  function norm(s) { return S.norm(s); }
  function vis(el) {
    if (!el || !el.isConnected) return false;
    var r = el.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity !== 0;
  }
  function parentDeep(n) { return n.parentElement || (n.parentNode && n.parentNode.host) || null; }
  function containsDeep(a, b) { for (var n = b; n; n = parentDeep(n)) if (n === a) return true; return false; }
  function textOf(el, n) { return norm(el.innerText || el.textContent).slice(0, n || 200); }
  function hay(el) { return [el.id || '', String(el.className && el.className.baseVal != null ? el.className.baseVal : el.className || ''), el.getAttribute('aria-label') || ''].join(' '); }

  function pageKind() {
    var out = { kind: '', why: '', url: location.href };
    var frames = L.deepAll('iframe').filter(function (f) {
      if (!(CAPTCHA_FRAME.test(f.src || '') || CAPTCHA_FRAME.test(f.title || '')) || !vis(f)) return false;
      if (L.closestDeep(f, '.grecaptcha-badge')) return false;               // the invisible-reCAPTCHA corner badge asks nothing
      var r = f.getBoundingClientRect(); return r.width >= 60 && r.height >= 40;
    });
    var widgets = frames.length ? [] : L.deepAll(CAPTCHA_SEL).filter(function (e) {
      if (!vis(e) || L.closestDeep(e, '.grecaptcha-badge')) return false;
      var r = e.getBoundingClientRect(); return r.width >= 60 && r.height >= 30;
    });
    var text = norm(L.deepText()).slice(0, 6000), title = norm(document.title);
    var ct = CAPTCHA_TEXT.exec(title + ' ' + text);
    if (frames.length || widgets.length || ct) {
      out.kind = 'captcha';
      out.why = frames.length ? 'a challenge frame (' + (frames[0].title || 'captcha') + ')' : widgets.length ? 'a captcha widget' : '"' + ct[0] + '"';
      return out;
    }
    var fields = L.deepAll('input').filter(vis);
    var otp = otpField(fields), otpWhy = otp ? S.sensitiveField(otp) : '';
    if (otp) { out.kind = 'otp'; out.why = 'a one-time code field ("' + (L.accName(otp) || otpWhy) + '")'; return out; }
    if (fields.some(function (el) { return (el.getAttribute('type') || '').toLowerCase() === 'password'; })) {
      out.kind = 'login'; out.why = 'a password field'; return out;
    }
    var ex = EXPIRED_TEXT.exec(text);
    if (ex) { out.kind = 'login'; out.why = '"' + ex[0] + '"'; }
    return out;
  }

  /** the visible one-time-code field on the page, or null (v0.8: a code from the user's phone is typed into it). */
  function otpField(fields) {
    return (fields || L.deepAll('input').filter(vis)).find(function (el) { var r = S.sensitiveField(el); return !!r && OTP_REASON.test(r); }) || null;
  }

  /** the topmost element at viewport point (x, y), through OPEN shadow roots. */
  function topAt(x, y) {
    var t = document.elementFromPoint(x, y), guard = 0;
    while (t && t.shadowRoot && guard++ < 10) { var u = t.shadowRoot.elementFromPoint(x, y); if (!u || u === t) break; t = u; }
    return t;
  }
  /** the outermost overlay-like ancestor of n: a dialog / modal, or a fixed / sticky box. */
  function overlayOf(n) {
    var found = null;
    for (var e = n; e && e.nodeType === 1 && e !== document.body && e !== document.documentElement; e = parentDeep(e)) {
      if (e.matches(MODAL_SEL)) { found = e; continue; }
      var pos = getComputedStyle(e).position;
      if (pos === 'fixed' || pos === 'sticky') found = e;
    }
    return found;
  }
  /** the overlay drawn over el's centre (el scrolled into view first), or null when el is reachable. */
  function coveredBy(el) {
    if (!el || !el.isConnected) return null;
    var r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) { el.scrollIntoView({ block: 'center', inline: 'nearest' }); r = el.getBoundingClientRect(); }
    if (!r.width || !r.height) return null;
    var t = topAt(Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2)));
    if (!t || t === el || containsDeep(el, t) || containsDeep(t, el)) return null;
    var ov = overlayOf(t);
    return ov && !containsDeep(ov, el) ? ov : null;
  }

  /** a loading overlay (spinner / progress bar / aria-busy, no controls) is not a pop-up: replay waits it out (v0.9). */
  var BUSY_SEL = '[role=progressbar], [aria-busy=true], [class*=spinner i], [class*=loading i], [class*=loader i]';
  function busyOverlay(e) {
    var busy = false;
    try { busy = e.matches(BUSY_SEL) || !!e.querySelector(BUSY_SEL); } catch (x) { return false; }
    return busy && !L.deepAll(L.KIND_SELECTOR.click, e).length && textOf(e, 200).length < 80;
  }
  function isConsent(el) { return CONSENT_HINT.test(hay(el) + ' ' + textOf(el, 400)); }
  /** visible pop-ups in the way: open dialogs / modals, overlays over the viewport, cookie / consent banners. */
  function blockers() {
    var out = [];
    function add(e) {
      if (!e || !vis(e) || busyOverlay(e)) return;
      for (var k = 0; k < out.length; k++) { if (containsDeep(out[k], e)) return; if (containsDeep(e, out[k])) { out[k] = e; return; } }
      out.push(e);
    }
    L.deepAll(MODAL_SEL).forEach(add);
    [[0.5, 0.5], [0.5, 0.2], [0.5, 0.85], [0.2, 0.5], [0.8, 0.5]].forEach(function (p) {
      var t = topAt(innerWidth * p[0], innerHeight * p[1]); var ov = t && overlayOf(t);
      if (ov) {
        var r = ov.getBoundingClientRect();                       // a sticky header / small toast is not "in the way"
        if (ov.matches(MODAL_SEL) || isConsent(ov) || (r.width * r.height) >= innerWidth * innerHeight * 0.25) add(ov);
      }
    });
    L.deepAll(BANNER_SEL).filter(function (e) {
      if (!vis(e) || !CONSENT_HINT.test(hay(e) + ' ' + textOf(e, 400))) return false;
      var pos = getComputedStyle(e).position; return pos === 'fixed' || pos === 'sticky' || !!overlayOf(e);
    }).forEach(add);
    return out;
  }

  function controlsIn(box) {
    var all = L.deepAll(L.KIND_SELECTOR.click, box);
    if (box.shadowRoot) all = all.concat(L.deepAll(L.KIND_SELECTOR.click, box.shadowRoot));
    return all.filter(function (c) {
      return vis(c) && !c.matches('input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]');
    });
  }
  function nameOf(c) {
    var n = L.accName(c);
    if (!n) n = c.getAttribute('title') || '';
    return norm(n).replace(/[.!]+$/, '');
  }
  /** -> {ok: control name allowed?, why} for one control in a blocker of `kind`. */
  function allowed(c, kind) {
    var name = nameOf(c), low = name.toLowerCase();
    if (!name) return false;
    if (NEVER_RE.test(name) || S.dangerMatchStrict(name, true)) return false;            // own name: the strict vocabulary
    if (S.dangerMatch(S.dangerContext(c, name).replace(/\b(cookies?|consent)\b/gi, ''))) return false;   // context: pay / delete / ...
    return kind === 'consent' ? CONSENT_RE.test(low) : DISMISS_RE.test(low);
  }

  // ---- the app's own windows (v0.9.1, docs/specs/portal_run.md 9) ---------------------------------------------
  // A Settings / Account / Billing window of the app itself (a modal dialog with tabs or menus, or one the step's
  // target lives in) is part of the page, not a pop-up: it is never dismissed or offered to the pop-up reader.
  var APPNAV_SEL = '[role=tablist], [role=tab], [role=menu], [role=menubar], [role=navigation], nav, [role=tree]';
  var APP_TITLE = /^(settings|preferences|account|account settings|your account|my account|profile|billing|billing settings|options|workspace settings|organi[sz]ation settings|team settings|admin settings)\b/i;
  function selectedTab(b) {
    var t = L.deepAll('[role=tab][aria-selected=true]', b).filter(vis)[0];
    return t ? nameOf(t) : '';
  }
  function looseSame(a, b) {
    a = norm(a).toLowerCase(); b = norm(b).toLowerCase();
    return !!a && !!b && (a === b || (a.length >= 4 && b.indexOf(a) === 0) || (b.length >= 4 && a.indexOf(b) === 0));
  }
  /** why blocker b is the app's own window the step needs ('' = an ordinary pop-up).
   *  keep: {name, role, dialog: {name, tab}} of the step being replayed (all optional). */
  function appDialogWhy(b, keep) {
    keep = keep || {};
    var title = titleOf(b);
    if (keep.dialog && keep.dialog.name && looseSame(title, keep.dialog.name)) return 'the step was recorded in this window';
    if ((keep.own || []).some(function (t) { return looseSame(title, t); })) return 'an earlier step of this run opened it';
    var want = norm(keep.name || '').toLowerCase();
    if (want && L.deepAll(L.KIND_SELECTOR.click + ', ' + L.KIND_SELECTOR.fill, b).some(function (c) { return nameOf(c).toLowerCase() === want; })) {
      return 'it has the step\'s control "' + norm(keep.name) + '"';
    }
    // the step's control was found UNDER this window (the app reopened its Settings over the page the step needs):
    // then only the rules above keep it; a Settings-like window in the way is closed like any pop-up
    if (isConsent(b) || keep.covered) return '';
    if (L.deepAll(APPNAV_SEL, b).some(vis)) return 'it has the app\'s own tabs / menus';
    if (APP_TITLE.test(title)) return 'it is the app\'s "' + title.slice(0, 40) + '" window';
    return '';
  }
  /** the dialog / modal el sits in -> {name, tab} (recorded with a step), or null. */
  function dialogOf(el) {
    var d = el && L.closestDeep(el, MODAL_SEL);
    return d ? { name: titleOf(d).slice(0, 120), tab: selectedTab(d).slice(0, 80) } : null;
  }
  /** a visible modal window open now (not a cookie banner) -> {name, tab, nav}, or null (recording start, v0.9.1). */
  function openDialog() {
    var ds = L.deepAll(MODAL_SEL).filter(function (d) { return vis(d) && !busyOverlay(d) && !isConsent(d); });
    var d = ds[ds.length - 1];
    if (!d) return null;
    return { name: titleOf(d).slice(0, 120), tab: selectedTab(d).slice(0, 80), nav: !!appDialogWhy(d, {}) };
  }

  /** titles of the visible modal windows now (the worker remembers the ones its own steps opened). */
  function openDialogs() {
    return L.deepAll(MODAL_SEL).filter(function (d) { return vis(d) && !busyOverlay(d) && !isConsent(d); })
      .map(function (d) { return titleOf(d).slice(0, 120); }).filter(Boolean);
  }

  function unblock(keep) {
    var res = { dismissed: [], blocked: null, kept: [] }, bs = blockers().filter(function (b) {
      var why = appDialogWhy(b, keep);
      if (why) res.kept.push({ title: titleOf(b).slice(0, 80), why: why });
      return !why;
    });
    bs.forEach(function (b) {
      if (res.blocked) return;
      var kind = isConsent(b) ? 'consent' : 'popup', ctl = controlsIn(b);
      var pick = ctl.filter(function (c) { return allowed(c, kind); });
      if (kind === 'consent') {             // prefer reject / decline over a plain close
        pick.sort(function (a, b2) { return (/^(close|x|×|✕)$/i.test(nameOf(a)) ? 1 : 0) - (/^(close|x|×|✕)$/i.test(nameOf(b2)) ? 1 : 0); });
      }
      var text = textOf(b, 160);
      if (!pick.length) {
        res.blocked = { kind: kind, text: text, controls: ctl.map(nameOf).filter(Boolean).slice(0, 8),
                        title: titleOf(b), body: textOf(b, 800), ctl: ctl.slice(0, 12).map(function (c, k) { return { n: k + 1, name: nameOf(c), hint: nameOf(c) ? '' : hintOf(c) }; }) };
        return;
      }
      var c = pick[0], name = nameOf(c);
      try { c.focus && c.focus(); c.click(); } catch (e) { res.blocked = { kind: kind, text: text, controls: [name] }; return; }
      res.dismissed.push({ name: name, kind: kind, text: text });
    });
    return res;
  }

  // ---- unknown pop-ups read by the agent brain (v0.9, docs/specs/portal_run.md 8) ------------------------------
  // The worker asks the brain only when unblock() found a blocker with no allowed control. The brain may name ONE
  // control to close a HARMLESS pop-up; it is clicked only if its name is a close / dismiss word (HARMLESS_RE) and
  // nothing in the deny rules (NEVER_RE, the strict danger vocabulary, the danger context) matches: those win.
  var HARMLESS_RE = /\b(close|dismiss|not now|not right now|later|skip|got it|no,? thanks|no thank you|continue to (the )?(site|website|portal|dashboard|app|page))\b|^(x|×|✕|✖|ok|okay)$/i;
  // a pop-up about any of these needs the user's decision, whatever the model says ("accept new terms" always)
  var DECISION_RE = /\b(terms|conditions|t&cs?|agreement|agree|accept|consent|privacy (policy|notice)|data (sharing|processing|protection)|share (your )?data|gdpr|pay|payment|payments|billing|(credit|debit|payment) card|card (details|number)|subscribe|subscription|(change|upgrade|choose|select) (your |a )?plan|upgrade|free trial|renew|renewal|delete|deletion|close (your )?account|deactivate|cancel (your )?(account|subscription|plan)|account (change|changes|settings|update)|verify (your )?(identity|account|e-?mail|phone)|password|permissions?|authori[sz]e|legal|e-?sign|signature)\b/i;
  function titleOf(b) {
    var lab = b.getAttribute('aria-label') || '';
    var by = b.getAttribute('aria-labelledby');
    if (!lab && by) lab = by.split(/\s+/).map(function (id) { var n = document.getElementById(id); return n ? n.textContent : ''; }).join(' ');
    if (!lab) { var h = L.deepAll('h1, h2, h3, h4, [role=heading]', b)[0]; if (h) lab = h.textContent; }
    if (!lab) { var d = L.deepAll(MODAL_SEL, b)[0]; if (d) lab = d.getAttribute('aria-label') || ''; }
    return norm(lab).slice(0, 120) || textOf(b, 60);
  }
  function hintOf(c) { return norm(hay(c)).slice(0, 60); }
  /** a control the brain named may close the pop-up: a close / dismiss word, and no deny rule matches. */
  function harmlessAllowed(c, kind) {
    var name = nameOf(c);
    if (name && (NEVER_RE.test(name) || S.dangerMatchStrict(name, true))) return false;
    if (S.dangerMatch(S.dangerContext(c, name).replace(/\b(cookies?|consent)\b/gi, ''))) return false;
    if (kind === 'consent' && name && !CONSENT_RE.test(name.toLowerCase()) && !HARMLESS_RE.test(name)) return false;
    if (!name) return /\b(close|dismiss)\b/i.test(hay(c)) && !NEVER_RE.test(hay(c));        // an icon-only close button
    return HARMLESS_RE.test(name);
  }
  /** the decision rule over what the user would read: title, text and the controls' names. -> matched word or ''. */
  function decisionWord(info) {
    var t = [info.title || '', info.body || info.text || '', (info.controls || []).join(' | ')].join(' | ');
    var m = DECISION_RE.exec(t); return m ? m[0].toLowerCase() : '';
  }
  /** click control n (as numbered in unblock()'s `blocked.ctl`) of the blocker that offers no allowed control, only
   *  when it still carries the name the brain saw and harmlessAllowed() agrees. -> {ok, name, why} */
  function closeBlocked(n, expectName, keep) {
    var bs = blockers().filter(function (b) { return !appDialogWhy(b, keep); });
    for (var k = 0; k < bs.length; k++) {
      var b = bs[k], kind = isConsent(b) ? 'consent' : 'popup', ctl = controlsIn(b);
      if (ctl.some(function (c) { return allowed(c, kind); })) continue;      // unblock() handles that one itself
      var c = ctl[(n | 0) - 1];
      if (!c) return { ok: false, why: 'the pop-up has no control ' + n };
      var name = nameOf(c);
      if (name !== (expectName || '')) return { ok: false, why: 'control ' + n + ' changed (now "' + name + '")' };
      if (!harmlessAllowed(c, kind)) return { ok: false, why: '"' + (name || hintOf(c) || 'that control') + '" is not a close / dismiss control AWOS may click' };
      try { c.focus && c.focus(); c.click(); } catch (e) { return { ok: false, why: 'the click failed' }; }
      return { ok: true, name: name || hintOf(c) || 'close', text: textOf(b, 160) };
    }
    return { ok: false, why: 'the pop-up is gone' };
  }

  // ---- login helpers (v0.6.1): "remember me", Chrome-autofilled sign-in -------------------------------------
  var DEV = '( (on|for) (this|my) (device|computer|browser))?';
  var REMEMBER_RE = new RegExp('^(remember me' + DEV + '|remember (this|my) (device|computer|browser)'
    + '|keep me (signed|logged) in' + DEV + '|stay (signed|logged) in' + DEV
    + '|trust this (device|computer|browser)|(do not|don\'t|dont) ask (me )?again' + DEV
    + '|(do not|don\'t|dont) ask (me )?for (a )?code again' + DEV + ')( for \\d+ days)?$', 'i');
  var SIGNIN_RE = /^(sign ?in|log ?in|login|log on|logon|sign on|sign in securely|log in securely|secure sign ?in|continue|next|submit)$/i;
  function cleanLabel(s) { return norm(s).toLowerCase().replace(/’/g, "'").replace(/[.!?:]+$/, '').trim(); }
  function checkboxName(el) {
    var n = L.accName(el) || '';
    if (!n) { var lab = L.closestDeep(el, 'label'); if (lab) n = lab.innerText || lab.textContent || ''; }
    return cleanLabel(n);
  }
  function isChecked(el) { return el.matches('input') ? !!el.checked : el.getAttribute('aria-checked') === 'true'; }
  /** a checkbox the user can see: itself, or (custom-styled, the input hidden) its label. */
  function boxVisible(el) {
    if (vis(el)) return true;
    return Array.prototype.slice.call(el.labels || []).some(vis);
  }
  function rememberMe() {
    var boxes = L.deepAll('input[type=checkbox], [role=checkbox]').filter(function (el) { return !el.disabled && boxVisible(el); });
    var hit = boxes.find(function (el) { return !isChecked(el) && REMEMBER_RE.test(checkboxName(el)); });
    if (!hit) return { ticked: '', seen: boxes.length };
    try { hit.click(); } catch (e) { return { ticked: '', seen: boxes.length }; }
    return { ticked: checkboxName(hit), seen: boxes.length, checked: isChecked(hit) };
  }
  function pseudo(el, sel) { try { return el.matches(sel); } catch (e) { return false; } }
  /** Chrome autofilled this field, judged by pseudo-classes only (its value is never read). */
  function autofilled(el, testHook) {
    var af = pseudo(el, ':autofill') || pseudo(el, ':-webkit-autofill') || (!!testHook && el.hasAttribute('data-awos-test-autofill'));
    return af && !pseudo(el, ':placeholder-shown');
  }
  var USER_SEL = 'input[type=email], input[type=text], input[type=tel], input:not([type])';
  var BTN_SEL = 'button, input[type=submit], [role=button]';
  function autofillSignin(testHook) {
    var pws = L.deepAll('input[type=password]').filter(vis);
    if (pws.length !== 1) return { clicked: '', why: pws.length ? 'more than one password field (not a plain sign-in form)' : 'no visible password field' };
    var pw = pws[0], form = pw.form || L.closestDeep(pw, 'form');
    var users = L.deepAll(USER_SEL, form || undefined).filter(function (el) {
      return vis(el) && !!(el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING);       // before the password field
    });
    var user = users[users.length - 1];
    if (!user) return { clicked: '', why: 'no username field before the password field (a two-step sign-in is not handled)' };
    var uA = autofilled(user, testHook), pA = autofilled(pw, testHook);
    if (!uA || !pA) return { clicked: '', why: 'Chrome has not autofilled ' + (!uA && !pA ? 'the username and the password' : !uA ? 'the username' : 'the password') };
    var btn = L.deepAll(BTN_SEL, form || undefined).filter(function (b) { return vis(b) && !b.disabled; })
      .find(function (b) { return SIGNIN_RE.test(cleanLabel(L.accName(b) || b.value || '')); });
    if (!btn) return { clicked: '', why: 'no sign-in button in the form' };
    var name = norm(L.accName(btn) || btn.value || 'Sign in');
    try { if (btn.focus) btn.focus(); btn.click(); } catch (e) { return { clicked: '', why: 'the click failed' }; }
    return { clicked: name, why: 'Chrome had autofilled the username and the password' };
  }

  root.AWOSPortal = {
    otpField: otpField,
    DISMISS_RE: DISMISS_RE, CONSENT_RE: CONSENT_RE, NEVER_RE: NEVER_RE, REMEMBER_RE: REMEMBER_RE, SIGNIN_RE: SIGNIN_RE,
    pageKind: pageKind, coveredBy: coveredBy, blockers: blockers, unblock: unblock, allowed: allowed,
    HARMLESS_RE: HARMLESS_RE, DECISION_RE: DECISION_RE, harmlessAllowed: harmlessAllowed, decisionWord: decisionWord, closeBlocked: closeBlocked,
    topAt: topAt, overlayOf: overlayOf, containsDeep: containsDeep, vis: vis,
    appDialogWhy: appDialogWhy, dialogOf: dialogOf, openDialog: openDialog, openDialogs: openDialogs, MODAL_SEL: MODAL_SEL,
    rememberMe: rememberMe, autofillSignin: autofillSignin, autofilled: autofilled, rememberLabel: checkboxName,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
