/* AWOS Replay: phone link helpers (v0.8, docs/specs/phone_link.md). Pure functions, no chrome.* calls, so they load
 * in the service worker (importScripts) and in a plain test page.
 *
 *  b64u(bytes) / unb64u(str)       base64url without padding
 *  newPairing()                    {channel, key, smsToken, ntfyTopic}: 16 random bytes, an AES-GCM 256 raw key,
 *                                  16 random bytes (all base64url), "awos-" + 16 random hex
 *  pairLink(base, p)               <base>/phone/#c=<channel>&k=<key>&t=<smsToken>&n=<ntfyTopic>
 *  sha256Hex(text)                 the register call's tokenHash (sha256 of the smsToken string, hex)
 *  seal(key, dir, obj)             -> {id, iv, ct}: AES-GCM, 12-byte random iv, AAD = dir ("to-phone" | "to-mac"),
 *                                  plaintext = JSON of obj, all base64url; id = 16 random bytes base64url
 *  open(key, dir, env)             -> obj (throws if the key, the direction or the bytes do not match)
 *  extractCode(sms)                -> the one-time code in a forwarded SMS, or '': a 4-8 digit number, or a 6-character
 *                                  code with at least one digit, near "code / OTP / verification / passcode / PIN /
 *                                  2FA / security / login / sign-in"; the closest to such a word wins
 *  validCode(code)                 4-8 letters / digits, nothing else (what is typed into the field)
 *  smsCode(messages, sinceMs)      -> {code, id, at} from the newest forwarded SMS at or after sinceMs that has one
 *  mask(text, code)                text with every occurrence of code replaced by ******
 * Plain script: defines globalThis.AWOSPhone.
 */
(function (root) {
  'use strict';
  var C = root.crypto, TE = new TextEncoder(), TD = new TextDecoder();

  function b64u(bytes) {
    var s = '', a = new Uint8Array(bytes);
    for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function unb64u(str) {
    var s = String(str || '').replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function rand(n) { var a = new Uint8Array(n); C.getRandomValues(a); return a; }
  function hex(bytes) { return Array.prototype.map.call(new Uint8Array(bytes), function (b) { return ('0' + b.toString(16)).slice(-2); }).join(''); }

  function newPairing() {
    return { channel: b64u(rand(16)), key: b64u(rand(32)), smsToken: b64u(rand(16)), ntfyTopic: 'awos-' + hex(rand(8)) };
  }
  function pairLink(base, p) {
    return String(base || 'https://awos.tirramind.com').replace(/\/+$/, '') + '/phone/#c=' + p.channel + '&k=' + p.key
      + '&t=' + p.smsToken + '&n=' + p.ntfyTopic;
  }
  async function sha256Hex(text) { return hex(await C.subtle.digest('SHA-256', TE.encode(String(text)))); }

  var keys = {};
  async function importKey(k) {
    if (!keys[k]) keys[k] = C.subtle.importKey('raw', unb64u(k), { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
    return keys[k];
  }
  async function seal(key, dir, obj) {
    var iv = rand(12);
    var ct = await C.subtle.encrypt({ name: 'AES-GCM', iv: iv, additionalData: TE.encode(dir) }, await importKey(key), TE.encode(JSON.stringify(obj)));
    return { id: b64u(rand(16)), iv: b64u(iv), ct: b64u(ct) };
  }
  async function open(key, dir, env) {
    var pt = await C.subtle.decrypt({ name: 'AES-GCM', iv: unb64u(env.iv), additionalData: TE.encode(dir) }, await importKey(key), unb64u(env.ct));
    return JSON.parse(TD.decode(pt));
  }

  var CUE = /\b(code|codes|otp|one[- ]?time|verification|verify|passcode|pin|2fa|mfa|security|login|log[- ]?in|sign[- ]?in|authenticat\w*)\b/ig;
  function validCode(c) { return /^[A-Za-z0-9]{4,8}$/.test(String(c || '')); }
  function extractCode(sms) {
    var text = String(sms || '').slice(0, 1000), cues = [], m;
    CUE.lastIndex = 0;
    while ((m = CUE.exec(text))) cues.push(m.index);
    if (!cues.length) return '';
    var best = '', bestD = Infinity, re = /(?:^|[^A-Za-z0-9])([0-9]{4,8}|[A-Za-z0-9]{6})(?![A-Za-z0-9])/g;
    while ((m = re.exec(text))) {
      var c = m[1], at = m.index + m[0].length - c.length;
      if (!/[0-9]/.test(c)) continue;                       // a 6-letter word ("Please") is not a code
      var d = Math.min.apply(null, cues.map(function (k) { return Math.abs(k - at); }));
      if (d < bestD) { bestD = d; best = c; }
    }
    return bestD <= 80 ? best : '';
  }
  function smsCode(messages, sinceMs) {
    var list = (messages || []).filter(function (s) { return s && +s.at >= +sinceMs; })
      .sort(function (a, b) { return +b.at - +a.at; });
    for (var i = 0; i < list.length; i++) {
      var c = extractCode(list[i].sms);
      if (c) return { code: c, id: list[i].id, at: +list[i].at };
    }
    return null;
  }
  function mask(text, code) {
    if (!code) return String(text);
    return String(text).split(String(code)).join('******');
  }

  root.AWOSPhone = { b64u: b64u, unb64u: unb64u, newPairing: newPairing, pairLink: pairLink, sha256Hex: sha256Hex,
                     seal: seal, open: open, extractCode: extractCode, validCode: validCode, smsCode: smsCode, mask: mask };
})(typeof self !== 'undefined' ? self : this);
