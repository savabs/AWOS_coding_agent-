/* AWOS Replay: safety rules shared by the content script and the service worker.
 *
 * Two jobs, both deliberately conservative:
 *  - sensitiveField(el) -> reason | null. The recorder NEVER stores the value of
 *    a field this flags (password, card number/CVC/expiry, OTP/2FA code, SSN,
 *    PIN, bank/routing number). It stores a placeholder instead, and replay
 *    pauses for the user to type the value themselves.
 *  - dangerMatch(text) -> matched phrase | null. A click whose target name or
 *    context matches pay/buy/send/delete/... pauses replay until the user clicks
 *    Approve in the extension UI. Nothing in the code path auto-approves.
 * Plain script: defines globalThis.AWOSSafety so it loads in a page, a content
 * script, or the service worker (importScripts) without a build step.
 */
(function (root) {
  'use strict';

  // Click-gate vocabulary. Word-bounded; "submit" alone is not dangerous, "submit order" is.
  var DANGER_RE = new RegExp(
    '\\b(' + [
      'pay now', 'pay(?:ment)?s?', 'buy(?:ing)?', 'purchase', 'check ?out',
      'send', 'submit order', 'place (?:your )?order', 'confirm (?:order|payment|purchase|transfer)',
      'delete', 'remove', 'empty', 'permanently', 'discard', 'clear all', 'transfer', 'wire',
    ].join('|') + ')\\b', 'i');

  // Field names/labels whose values must never be stored.
  var SENSITIVE_RE = /(passw|passcode|\bpin\b|\bcvv\b|\bcvc\b|\bcsc\b|security code|card ?(number|no)|\bcc[-_ ]?(num|number|no|exp|csc)\b|credit ?card|expir|\botp\b|one[- ]?time|verification code|auth(entication)? code|2fa|totp|\bmfa\b|\bssn\b|social security|tax ?id|routing|account ?number|\biban\b|secret)/i;
  var SENSITIVE_AUTOCOMPLETE = /^(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp|cc-exp-month|cc-exp-year)$/;

  function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }

  function dangerMatch(text) {
    var m = DANGER_RE.exec(norm(text));
    return m ? m[1].toLowerCase() : null;
  }

  /** text pieces to check for one element: own name/text plus enclosing form/dialog context. */
  function dangerContext(el, locatorName) {
    var parts = [locatorName || ''];
    if (el) {
      parts.push(el.getAttribute && (el.getAttribute('aria-label') || ''), el.title || '',
                 el.value && /^(submit|button|reset)$/i.test(el.type || '') ? el.value : '',
                 (el.innerText || el.textContent || '').slice(0, 120), el.id || '',
                 el.getAttribute && (el.getAttribute('name') || ''));
      var form = el.closest && el.closest('form, [role=dialog], dialog');
      if (form) {
        parts.push(form.getAttribute('aria-label') || '', form.id || '', form.getAttribute('name') || '',
                   (form.getAttribute('action') || '').replace(/[\/_.-]+/g, ' '));
      }
    }
    return parts.join(' | ');
  }

  function sensitiveField(el) {
    if (!el || !el.tagName) return null;
    var type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'password') return 'password';
    var ac = (el.getAttribute('autocomplete') || '').toLowerCase().trim();
    if (SENSITIVE_AUTOCOMPLETE.test(ac)) return ac;
    var hay = [el.id, el.getAttribute('name'), el.getAttribute('placeholder'), el.getAttribute('aria-label'),
               ac, labelOf(el)].join(' ');
    var m = SENSITIVE_RE.exec(hay);
    return m ? m[1].toLowerCase() : null;
  }

  function labelOf(el) {
    var out = [];
    if (el.labels) for (var i = 0; i < el.labels.length; i++) out.push(el.labels[i].innerText || el.labels[i].textContent || '');
    var lb = el.getAttribute && el.getAttribute('aria-labelledby');
    if (lb && el.ownerDocument) lb.split(/\s+/).forEach(function (id) {
      var n = el.ownerDocument.getElementById(id); if (n) out.push(n.textContent || '');
    });
    return norm(out.join(' '));
  }

  function placeholderFor(reason) { return '{{secret:' + String(reason).replace(/[^a-z0-9-]+/gi, '-') + '}}'; }
  function isPlaceholder(v) { return /^\{\{secret:[a-z0-9-]+\}\}$/i.test(String(v || '')); }

  root.AWOSSafety = {
    DANGER_RE: DANGER_RE, dangerMatch: dangerMatch, dangerContext: dangerContext,
    sensitiveField: sensitiveField, labelOf: labelOf, norm: norm,
    placeholderFor: placeholderFor, isPlaceholder: isPlaceholder,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
