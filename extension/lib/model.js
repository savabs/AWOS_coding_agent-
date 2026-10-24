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

  // The repair model's contract, byte-for-byte the same as scaffold/agent/desktop/repair/model.py
  // (SYSTEM, prompt(), GRAMMAR, REPLY_RE): the trained 2B (G6) learned exactly this text, so any drift
  // here is a distribution shift. tests/extension/test_model_contract.py pins JS == Python.
  var SYSTEM = 'You repair one step of a recorded web chore. The page changed and the control the step used '
    + 'is no longer found exactly. Pick the candidate that IS the same control (same purpose, same '
    + 'item), or answer NOT_FOUND when none of them is. Controls get deleted: a similar-looking '
    + 'neighbour (another row, another item, a different action) is NOT the same control. '
    + "Reply with exactly one line: 'PICK <n>: <reason>' or 'NOT_FOUND: <reason>'.";
  var GRAMMAR = 'root ::= ("PICK " [1-5] | "NOT_FOUND") ": " [^\\n]{0,160}';
  var REPLY_RE = /^\s*(?:PICK\s+([1-5])|(NOT_FOUND))\s*(?::\s*([\s\S]*))?$/;

  /** Python repr() of a str (the training prompts quote values with repr). */
  function pyRepr(s) {
    s = String(s == null ? '' : s);
    var q = (s.indexOf("'") >= 0 && s.indexOf('"') < 0) ? '"' : "'";
    var out = s.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t');
    if (q === "'") out = out.replace(/'/g, "\\'");
    return q + out + q;
  }

  /** repair.model.prompt's desc(): role named 'name'; in region '...'; next to text '...'; between ... */
  function desc(d) {
    var bits = [(d.role || d.tag || '') + ' named ' + pyRepr(d.name || '')];
    if (d.context) bits.push('in region ' + pyRepr(d.context));
    if (d.block) bits.push('next to text ' + pyRepr(String(d.block).slice(0, 120)));
    if (d.siblings && d.siblings.length) bits.push('between ' + d.siblings.slice(0, 4).map(pyRepr).join(', '));
    return bits.join('; ');
  }

  function buildMessages(step, shortlist) {
    var lines = ['The step used: ' + desc(step.loc || {}), '', 'Candidates on the page now:'];
    shortlist.forEach(function (c, i) { lines.push((i + 1) + '. ' + desc(c.desc || {})); });
    lines.push('', "Which candidate is the same control? 'PICK <n>: reason' or 'NOT_FOUND: reason'.");
    return [{ role: 'system', content: SYSTEM }, { role: 'user', content: lines.join('\n') }];
  }

  /** -> {choice: 0-based index, reason} | {notFound: true, reason} | {error}; an unparsable reply or an
   * out-of-range pick is an error (stop and show the user), never a guess. */
  function parseAnswer(text, n) {
    var t = String(text || '').trim().split('\n')[0];
    var m = REPLY_RE.exec(t);
    if (!m) return { error: 'unparsable model answer: ' + t.slice(0, 60) };
    if (m[2]) return { notFound: true, reason: (m[3] || '').trim() };
    var k = parseInt(m[1], 10);
    if (k < 1 || k > n) return { error: 'model picked ' + k + ' of ' + n };
    return { choice: k - 1, reason: (m[3] || '').trim() };
  }

  async function ask(settings, step, shortlist) {
    var s = Object.assign({}, DEFAULTS, settings || {});
    if (!s.enabled) return { error: 'local model disabled in options' };
    if (!isLoopback(s.endpoint)) return { error: 'endpoint is not loopback; refusing to send page data: ' + s.endpoint };
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, s.timeoutMs);
    try {
      var resp = await fetch(s.endpoint.replace(/\/+$/, '') + '/chat/completions', {
        method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: s.model, messages: buildMessages(step, shortlist.slice(0, 5)), temperature: 0,
                               max_tokens: 64, grammar: GRAMMAR }),
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

  root.AWOSModel = { DEFAULTS: DEFAULTS, isLoopback: isLoopback, buildMessages: buildMessages, parseAnswer: parseAnswer, ask: ask,
                     fmt: fmt, desc: desc, pyRepr: pyRepr, SYSTEM: SYSTEM, GRAMMAR: GRAMMAR };
})(typeof globalThis !== 'undefined' ? globalThis : self);
