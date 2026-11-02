/* AWOS Replay: is the downloaded file really a document? (v0.9 "never trust a green run", docs/specs/portal_run.md 7)
 *
 *  expectedKind(name)  -> 'pdf' | 'csv' | 'zip' (xlsx, docx, ods, ... are zip containers) | 'html' | 'other'
 *  classify({name, size, mime, head, peekType, peekWhy}) -> {state, kind, why}
 *      state 'ok'        the content was read and matches the name (PDF starts with %PDF-, CSV is text with a
 *                        delimiter in its first line, XLSX / ZIP start with PK), or (other kinds) it is not a web page
 *            'bad'       never Got: empty (0 bytes), or a web page (a saved login / error page) under a document's
 *                        name, or content that does not match the name (a .pdf that is not a PDF)
 *            'unchecked' the size is fine but the first bytes could not be read (another origin, the portal refused a
 *                        second read, or it answered differently the second time): the verdict says
 *                        "content not checked" instead of pretending
 *      size: bytes (chrome.downloads fileSize / bytesReceived; < 0 = unknown). mime: chrome.downloads' mime (the
 *      original response's type). head: the first <= 1024 bytes as a latin1 string (re-read by the content script on
 *      the portal's own origin), or null. peekType: the content type of that re-read.
 * Pure functions: defines globalThis.AWOSFile (the service worker loads it; tests load it in a page).
 */
(function (root) {
  'use strict';
  var HTML_MIME = /^(text\/html|application\/xhtml\+xml)\b/i;
  var HTML_HEAD = /^\s*<(!doctype\s+html|html[\s>]|head[\s>]|body[\s>]|!--|meta[\s>]|title[\s>]|script[\s>])/i;

  function ext(name) { var m = /\.([a-z0-9]{1,5})$/i.exec(String(name || '').replace(/ \(\d+\)(?=\.[^.]*$)/, '')); return m ? m[1].toLowerCase() : ''; }
  function expectedKind(name) {
    var e = ext(name);
    if (e === 'pdf') return 'pdf';
    if (/^(csv|tsv)$/.test(e)) return 'csv';
    if (/^(xlsx|xlsm|docx|pptx|ods|odt|zip)$/.test(e)) return 'zip';
    if (/^(html?|xhtml)$/.test(e)) return 'html';
    return 'other';
  }
  var KIND_WORD = { pdf: 'a PDF', csv: 'a CSV table', zip: 'a spreadsheet / ZIP file', other: 'a document', html: 'a web page' };
  function stripBom(s) { return String(s || '').replace(/^﻿|^ï»¿/, ''); }
  function looksHtml(head) { return HTML_HEAD.test(stripBom(head)); }
  function isText(head) {
    var s = stripBom(head), bad = 0;
    if (!s.length) return false;
    for (var k = 0; k < s.length; k++) {
      var c = s.charCodeAt(k);
      if (c === 0) return false;
      if (c < 9 || (c > 13 && c < 32)) bad++;
    }
    return bad / s.length < 0.02;
  }
  function firstLine(head) { return stripBom(head).split(/\r?\n/)[0] || ''; }

  /** the content of the first bytes against the expected kind -> {ok, why}. */
  function checkHead(kind, head, name) {
    var s = stripBom(head);
    if (kind !== 'html' && looksHtml(s)) {
      return { ok: false, why: name + ' is a web page (HTML), not ' + KIND_WORD[kind] + ': probably a login or error page saved under the file\'s name' };
    }
    if (kind === 'pdf') {
      return s.indexOf('%PDF-') >= 0 && s.indexOf('%PDF-') < 1024 ? { ok: true, why: 'starts like a PDF (%PDF-)' }
        : { ok: false, why: name + ' does not start like a PDF (no %PDF- header)' };
    }
    if (kind === 'csv') {
      if (!isText(s)) return { ok: false, why: name + ' is not text, so not a CSV table' };
      return /[,;\t|]/.test(firstLine(s)) ? { ok: true, why: 'text with a delimiter in the first line' }
        : { ok: false, why: name + ' has no delimiter (, ; tab |) in its first line, so not a CSV table' };
    }
    if (kind === 'zip') {
      return s.slice(0, 2) === 'PK' ? { ok: true, why: 'starts like a spreadsheet / ZIP file (PK)' }
        : { ok: false, why: name + ' does not start like a spreadsheet / ZIP file (no PK header)' };
    }
    return { ok: true, why: 'not a web page' };
  }

  function classify(o) {
    o = o || {};
    var name = o.name || 'the file', kind = expectedKind(name), size = o.size == null ? -1 : +o.size;
    var out = { kind: kind, size: size, mime: o.mime || '' };
    if (size === 0) return Object.assign(out, { state: 'bad', why: name + ' is empty (0 bytes)' });
    if (kind !== 'html' && kind !== 'other' && HTML_MIME.test(o.mime || '')) {
      return Object.assign(out, { state: 'bad', why: name + ' was served as a web page (' + String(o.mime).split(';')[0] + '), not ' + KIND_WORD[kind]
                                                    + ': probably a login or error page saved under the file\'s name' });
    }
    if (o.head == null || o.head === '') {
      return Object.assign(out, { state: 'unchecked', why: 'content not checked' + (o.peekWhy ? ' (' + o.peekWhy + ')' : '') });
    }
    // the re-read must be the same kind of answer as the download: a re-read that came back as a web page while the
    // download was not (an expired one-time link, a redirect to a login) says nothing about the saved file
    var peekHtml = HTML_MIME.test(o.peekType || '') || looksHtml(o.head), dlHtml = HTML_MIME.test(o.mime || '');
    if (peekHtml && !dlHtml && o.mime && !/octet-stream|binary|download|force/i.test(o.mime) && HTML_MIME.test(o.peekType || '')) {
      return Object.assign(out, { state: 'unchecked', why: 'content not checked (the portal answered with a web page when the file was read again, '
                                                         + 'but the download itself was ' + String(o.mime).split(';')[0] + ')' });
    }
    var h = checkHead(kind, o.head, name);
    return Object.assign(out, { state: h.ok ? 'ok' : 'bad', why: h.ok ? 'content checked: ' + h.why : h.why });
  }

  root.AWOSFile = { expectedKind: expectedKind, classify: classify, looksHtml: looksHtml, checkHead: checkHead, isText: isText };
})(typeof globalThis !== 'undefined' ? globalThis : self);
