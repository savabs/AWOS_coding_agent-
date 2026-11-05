/* AWOS Replay: self-healing steps (v0.10, docs/research/experience_reuse_2026-11-05.md).
 *
 * When a replay step's recorded control is not found and the local model (or Claude, or the user) rescues it, the
 * control that worked is written back to the recording as an ALTERNATE locator of that step. The recording itself
 * (steps[i].loc) is never overwritten: a rescue that was luck must not erase what the user showed us.
 * Rules (from the research note): learn only from VERIFIED runs or explicit user input; keep few (4 per step);
 * describe a control by meaning (role, accessible name, context, labels), never by a field's value.
 *
 *  record(rec, stepIdx, loc, meta)  -> rec'   meta {by: '4b'|'claude'|'user', runId, at}. Appends
 *      {key, loc, by, ok:0, fail:0, streak:0, verified:false, at, runId} to steps[stepIdx].alternates. `loc` is an
 *      AWOSLocator.describe() result; only its descriptive fields are kept (never value / defaultValue / checked).
 *      Dedupe by role + name + context (case-insensitive): a repeat refreshes the loc and, while still unverified, the
 *      runId (a verified alternate keeps its history). At most 4 per step: the worst one goes (unverified before
 *      verified, then fewest ok - fail, then oldest), which can be the newcomer itself.
 *  confirm(rec, runId, verdict)     -> rec'   'VERIFIED': the alternates recorded in run `runId` become verified.
 *      'FAILED' / 'WRONG_PERIOD': the still-unverified ones recorded in that run are removed. Other verdicts
 *      (UNVERIFIED, CHECK) leave them pending: no proof either way.
 *  candidates(step)                 -> [{loc, source: 'primary'|'alternate', key}] in the order to try:
 *      promoted alternates (verified, ok >= 2, fewer than 2 failures in a row), then the recorded loc unless it is
 *      demoted (step.primaryFails >= 2 in a row AND some verified alternate exists), then the other verified
 *      alternates, then a demoted primary (still tried, never dropped). An UNVERIFIED alternate is never a candidate
 *      (v0.10 review): nothing unproven is clicked without the model or the user deciding. It stays pending; the same
 *      control rescued again in a VERIFIED run (record refreshes its runId) is what verifies it.
 *  outcome(rec, stepIdx, key, ok)   -> rec'   counts one use of a candidate. key 'primary': step.primaryFails counts
 *      failures in a row (0 again on success). An alternate counts ok / fail / streak; one with fail >= 2 and ok == 0
 *      is dropped. Call it with ok=true only for steps of a run that ended VERIFIED, so promotion means verified uses;
 *      such a use also marks the alternate verified (it was recorded in a run whose verdict proved nothing).
 *  describeHeal(step, stepIdx)      -> one run-log line about the best alternate, '' when there is none:
 *      'step 3 now also matches tab "Plans & billing" (fixed by the local model on 2026-11-05, verified twice)'
 *  revert(rec, stepIdx)             -> rec'   removes the step's alternates and primaryFails.
 * Every function is pure: the input recording is not modified, a changed deep copy is returned (JSON data only).
 * Plain script: defines globalThis.AWOSHeal (service worker, test pages).
 */
(function (root) {
  'use strict';

  var CAP = 4;            // alternates per step
  var PROMOTE_OK = 2;     // verified uses before an alternate is tried ahead of the recording
  var DEMOTE_FAILS = 2;   // failures in a row before the recording (or a promoted alternate) steps back
  var DROP_FAILS = 2;     // an alternate that failed this often and never worked is forgotten
  // AWOSLocator.describe() fields that say what a control IS; anything else (a field's value) is never stored
  var KEEP = ['tag', 'type', 'role', 'name', 'context', 'block', 'siblings', 'text', 'id', 'nameAttr', 'placeholder',
              'label', 'href', 'css', 'classes', 'neighbor', 'title', 'formId', 'pos', 'row', 'inDialog', 'dialog'];
  var BY = { '4b': 'the local model', claude: 'Claude', user: 'you' };

  function clone(o) { return o == null ? o : JSON.parse(JSON.stringify(o)); }
  function low(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim().toLowerCase(); }

  function cleanLoc(loc) {
    var out = {};
    loc = loc || {};
    KEEP.forEach(function (k) { if (loc[k] !== undefined) out[k] = clone(loc[k]); });
    if (typeof out.href === 'string') out.href = out.href.replace(/[?#].*$/, '');   // no query tokens / fragments
    return out;
  }
  function keyOf(loc) { loc = loc || {}; return 'alt:' + low(loc.role) + '|' + low(loc.name) + '|' + low(loc.context); }

  function stepOf(rec, stepIdx) {
    var out = clone(rec) || {};
    var step = (out.steps || [])[stepIdx];
    return { rec: out, step: step || null };
  }

  function promoted(a) { return a.verified && (a.ok || 0) >= PROMOTE_OK && (a.streak || 0) < DEMOTE_FAILS; }
  function rank(a) { return (a.verified ? 1000 : 0) + 10 * ((a.ok || 0) - (a.fail || 0)); }
  function atMs(a) { var t = typeof a.at === 'number' ? a.at : Date.parse(a.at || ''); return isNaN(t) ? 0 : t; }
  // better first: higher rank, then newer
  function better(a, b) { return rank(b) - rank(a) || atMs(b) - atMs(a); }

  function record(rec, stepIdx, loc, meta) {
    var s = stepOf(rec, stepIdx);
    if (!s.step || !loc) return s.rec;
    meta = meta || {};
    var clean = cleanLoc(loc), key = keyOf(clean), at = meta.at != null ? meta.at : Date.now();
    var by = BY[meta.by] ? meta.by : '4b';
    var alts = s.step.alternates = (s.step.alternates || []).slice();
    var have = alts.filter(function (a) { return a.key === key; })[0];
    if (have) {
      have.loc = clean; have.at = at; have.by = by;
      if (!have.verified) have.runId = meta.runId || have.runId || '';
      return s.rec;
    }
    alts.push({ key: key, loc: clean, by: by, ok: 0, fail: 0, streak: 0, verified: false, at: at, runId: meta.runId || '' });
    if (alts.length > CAP) {
      var worst = alts.slice().sort(better)[alts.length - 1];
      s.step.alternates = alts.filter(function (a) { return a !== worst; });
    }
    return s.rec;
  }

  function confirm(rec, runId, verdict) {
    var out = clone(rec) || {};
    if (!runId) return out;
    var good = verdict === 'VERIFIED', bad = verdict === 'FAILED' || verdict === 'WRONG_PERIOD';
    (out.steps || []).forEach(function (step) {
      if (!step || !step.alternates) return;
      if (good) step.alternates.forEach(function (a) { if (a.runId === runId) a.verified = true; });
      if (bad) step.alternates = step.alternates.filter(function (a) { return a.verified || a.runId !== runId; });
    });
    return out;
  }

  function candidates(step) {
    if (!step) return [];
    var alts = (step.alternates || []).slice().sort(better);
    var anyVerified = alts.some(function (a) { return a.verified; });
    var demoted = (step.primaryFails || 0) >= DEMOTE_FAILS && anyVerified;
    var alt = function (a) { return { loc: a.loc, source: 'alternate', key: a.key }; };
    var out = alts.filter(promoted).map(alt);
    var primary = step.loc ? [{ loc: step.loc, source: 'primary', key: 'primary' }] : [];
    if (!demoted) out = out.concat(primary);
    out = out.concat(alts.filter(function (a) { return a.verified && !promoted(a); }).map(alt));
    if (demoted) out = out.concat(primary);
    return out;                 // unverified alternates: pending, never tried on their own
  }

  function outcome(rec, stepIdx, key, ok) {
    var s = stepOf(rec, stepIdx);
    if (!s.step) return s.rec;
    if (key === 'primary') {
      if (ok) delete s.step.primaryFails; else s.step.primaryFails = (s.step.primaryFails || 0) + 1;
      return s.rec;
    }
    var alts = s.step.alternates || [];
    var a = alts.filter(function (x) { return x.key === key; })[0];
    if (!a) return s.rec;
    if (ok) { a.ok = (a.ok || 0) + 1; a.streak = 0; a.verified = true; }     // ok only comes from a VERIFIED run
    else { a.fail = (a.fail || 0) + 1; a.streak = (a.streak || 0) + 1; }
    if ((a.fail || 0) >= DROP_FAILS && !(a.ok > 0)) s.step.alternates = alts.filter(function (x) { return x !== a; });
    return s.rec;
  }

  // page strings are data: one line, no quotes of their own, short
  function quote(s) { return '"' + String(s == null ? '' : s).replace(/["“”]/g, "'").replace(/\s+/g, ' ').trim().slice(0, 60) + '"'; }
  function day(at) {
    var t = typeof at === 'number' ? at : Date.parse(at || '');
    if (isNaN(t) || !t) return '';
    var d = new Date(t), p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }
  function times(n) { return n === 1 ? 'once' : n === 2 ? 'twice' : n + ' times'; }

  function describeHeal(step, stepIdx) {
    if (!step || !(step.alternates || []).length) return '';
    var a = (step.alternates || []).slice().sort(better)[0], loc = a.loc || {};
    var role = low(loc.role).replace(/^menuitem$/, 'menu item') || 'control';
    var what = loc.name || loc.text || loc.label || loc.placeholder || '';
    var when = day(a.at);
    var proof = !a.verified ? 'not verified yet' : (a.ok || 0) > 0 ? 'verified ' + times(a.ok) : 'verified';
    if (a.fail) proof += ', failed ' + times(a.fail);
    return (stepIdx != null ? 'step ' + (stepIdx + 1) : 'this step') + ' now also matches ' + role + (what ? ' ' + quote(what) : '')
      + ' (fixed by ' + (BY[a.by] || BY['4b']) + (when ? ' on ' + when : '') + ', ' + proof + ')';
  }

  function revert(rec, stepIdx) {
    var s = stepOf(rec, stepIdx);
    if (s.step) { delete s.step.alternates; delete s.step.primaryFails; }
    return s.rec;
  }

  root.AWOSHeal = {
    record: record, confirm: confirm, candidates: candidates, outcome: outcome, describeHeal: describeHeal, revert: revert,
    keyOf: keyOf, cleanLoc: cleanLoc, CAP: CAP,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
