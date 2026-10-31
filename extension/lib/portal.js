/* AWOS Replay: portal-run helpers for the content script (v0.6, docs/specs/portal_run.md).
 *
 *  pageKind() -> {kind: 'captcha' | 'otp' | 'login' | '', why, url}
 *      what the page in front of a replay step is asking a human for. The service worker decides whether that is
 *      unexpected (the recording was not on such a page here) and hands off; this file never touches the page.
 *      captcha: a visible challenge widget (reCAPTCHA / hCaptcha / Turnstile / ... frame or container) or a
 *      "verify you are human" style text. Never solved, clicked or bypassed: the user does it.
 *      otp:     a visible one-time-code field (autocomplete=one-time-code, "verification code", "2FA", ...)
 *      login:   a visible password field, or a "your session expired / log in again" text
 *  coveredBy(el) -> the overlay / dialog element drawn over el's centre, or null
 *  blockers()   -> visible modals, overlays and cookie / consent banners on the page now
 *  unblock()    -> {dismissed: [{name, kind, text}], blocked: {kind, text, controls[]} | null}
 *      clicks ONE allowed control per blocker: a dismiss control inside a pop-up (close, x, not now, maybe later,
 *      skip, got it, no thanks, remind me later, continue to site), or for a cookie / consent banner only
 *      reject / decline / necessary-only / close. Never "accept", "agree", "allow all", never anything in the
 *      danger vocabulary (pay, send, delete, submit, confirm, ...). A blocker with no allowed control is reported
 *      as `blocked` so the run pauses for the user instead.
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
    var otp = null, otpWhy = '';
    fields.some(function (el) { var r = S.sensitiveField(el); if (r && OTP_REASON.test(r)) { otp = el; otpWhy = r; return true; } return false; });
    if (otp) { out.kind = 'otp'; out.why = 'a one-time code field ("' + (L.accName(otp) || otpWhy) + '")'; return out; }
    if (fields.some(function (el) { return (el.getAttribute('type') || '').toLowerCase() === 'password'; })) {
      out.kind = 'login'; out.why = 'a password field'; return out;
    }
    var ex = EXPIRED_TEXT.exec(text);
    if (ex) { out.kind = 'login'; out.why = '"' + ex[0] + '"'; }
    return out;
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

  function isConsent(el) { return CONSENT_HINT.test(hay(el) + ' ' + textOf(el, 400)); }
  /** visible pop-ups in the way: open dialogs / modals, overlays over the viewport, cookie / consent banners. */
  function blockers() {
    var out = [];
    function add(e) {
      if (!e || !vis(e)) return;
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

  function unblock() {
    var res = { dismissed: [], blocked: null }, bs = blockers();
    bs.forEach(function (b) {
      if (res.blocked) return;
      var kind = isConsent(b) ? 'consent' : 'popup', ctl = controlsIn(b);
      var pick = ctl.filter(function (c) { return allowed(c, kind); });
      if (kind === 'consent') {             // prefer reject / decline over a plain close
        pick.sort(function (a, b2) { return (/^(close|x|×|✕)$/i.test(nameOf(a)) ? 1 : 0) - (/^(close|x|×|✕)$/i.test(nameOf(b2)) ? 1 : 0); });
      }
      var text = textOf(b, 160);
      if (!pick.length) { res.blocked = { kind: kind, text: text, controls: ctl.map(nameOf).filter(Boolean).slice(0, 8) }; return; }
      var c = pick[0], name = nameOf(c);
      try { c.focus && c.focus(); c.click(); } catch (e) { res.blocked = { kind: kind, text: text, controls: [name] }; return; }
      res.dismissed.push({ name: name, kind: kind, text: text });
    });
    return res;
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
    DISMISS_RE: DISMISS_RE, CONSENT_RE: CONSENT_RE, NEVER_RE: NEVER_RE, REMEMBER_RE: REMEMBER_RE, SIGNIN_RE: SIGNIN_RE,
    pageKind: pageKind, coveredBy: coveredBy, blockers: blockers, unblock: unblock, allowed: allowed,
    rememberMe: rememberMe, autofillSignin: autofillSignin, autofilled: autofilled, rememberLabel: checkboxName,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
