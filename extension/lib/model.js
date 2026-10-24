/* AWOS Replay: local-model fallback for re-finding a step's target.
 *
 * Called only when attribute scoring is not confident (ambiguous / weak best).
 * Sends one short chat completion to an OpenAI-compatible endpoint (default
 * llama.cpp server at http://127.0.0.1:8080/v1) with the recorded target and a
 * numbered shortlist, and expects back one number or NOT_FOUND.
 * Loopback only: ask() refuses any endpoint that is not 127.0.0.1 / localhost /
 * [::1], so the extension never sends page content off the machine.
 * Errors (unreachable, timeout, unparsable) come back as {error}; the caller
 * treats them as "stop and show the user", never as a guess.
 */
(function (root) {
  'use strict';

  var DEFAULTS = { endpoint: 'http://127.0.0.1:8080/v1', model: 'local', enabled: true, timeoutMs: 20000, findTimeoutMs: 4000 };

  function isLoopback(url) {
    try {
      var u = new URL(url);
      return /^https?:$/.test(u.protocol) && /^(127\.0\.0\.1|localhost|\[::1\])$/.test(u.hostname);
    } catch (e) { return false; }
  }

  function fmt(d) {
    var bits = [d.role || d.tag];
    if (d.name) bits.push('"' + d.name + '"');
    if (d.label && d.label !== d.name) bits.push('label="' + d.label + '"');
    if (d.text && d.text !== d.name) bits.push('text="' + d.text.slice(0, 60) + '"');
    if (d.id) bits.push('id=' + d.id);
    if (d.nameAttr) bits.push('name=' + d.nameAttr);
    if (d.placeholder) bits.push('placeholder="' + d.placeholder + '"');
    if (d.neighbor) bits.push('near="' + d.neighbor.slice(0, 40) + '"');
    if (d.href) bits.push('href=' + d.href);
    return bits.join(' ');
  }

  var VERB = { click: 'click', fill: 'type into', select: 'choose an option in' };

  function buildMessages(step, shortlist) {
    var lines = shortlist.map(function (c, i) { return (i + 1) + '. ' + fmt(c.desc); });
    return [
      { role: 'system', content: 'You re-find a UI control for a recorded browser automation step. '
        + 'The page changed since recording. Reply with only the number of the candidate that is the same control, '
        + 'or NOT_FOUND if none of them is. Do not guess: a different control is worse than NOT_FOUND.' },
      { role: 'user', content: 'Recorded step: ' + (VERB[step.action] || step.action) + ' ' + fmt(step.loc)
        + '\nCandidates on the page now:\n' + lines.join('\n') + '\nAnswer:' },
    ];
  }

  /** -> {choice: 0-based index} | {notFound: true} | {error} */
  function parseAnswer(text, n) {
    var t = String(text || '').trim();
    if (/NOT[_ ]?FOUND/i.test(t)) return { notFound: true };
    var m = /\b(\d{1,2})\b/.exec(t);
    if (!m) return { error: 'unparsable model answer: ' + t.slice(0, 60) };
    var k = parseInt(m[1], 10);
    if (k < 1 || k > n) return { notFound: true };
    return { choice: k - 1 };
  }

  async function ask(settings, step, shortlist) {
    var s = Object.assign({}, DEFAULTS, settings || {});
    if (!s.enabled) return { error: 'local model disabled in options' };
    if (!isLoopback(s.endpoint)) return { error: 'endpoint is not loopback; refusing to send page data: ' + s.endpoint };
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, s.timeoutMs);
    try {
      var resp = await fetch(s.endpoint.replace(/\/+$/, '') + '/chat/completions', {
        method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: s.model, messages: buildMessages(step, shortlist), temperature: 0, max_tokens: 8 }),
      });
      if (!resp.ok) return { error: 'model HTTP ' + resp.status };
      var j = await resp.json();
      var text = j && j.choices && j.choices[0] && (j.choices[0].message ? j.choices[0].message.content : j.choices[0].text);
      var out = parseAnswer(text, shortlist.length); out.raw = String(text || '').slice(0, 80);
      return out;
    } catch (e) {
      return { error: 'local model unreachable at ' + s.endpoint + ' (' + (e && e.name === 'AbortError' ? 'timeout' : String(e && e.message || e)) + ')' };
    } finally { clearTimeout(timer); }
  }

  root.AWOSModel = { DEFAULTS: DEFAULTS, isLoopback: isLoopback, buildMessages: buildMessages, parseAnswer: parseAnswer, ask: ask, fmt: fmt };
})(typeof globalThis !== 'undefined' ? globalThis : self);
