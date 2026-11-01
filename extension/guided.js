/* AWOS Replay goal-guided runs (v0.7, docs/specs/goal_guided.md). Loaded into the service worker after tellit.js
 * (importScripts), so it shares background.js's replay helpers and tellit.js's brain (tellSettings, tellChat,
 * cloudLockedFor) and lib/agent.js's micro-agent contract (prompt, grammar, parser, validator).
 *
 *  applyPeriod(rec, keep)  at save / import: the recording's period (inferred, or kept), its templates (rec.tpl, from
 *                          lib/period.js) and a proposed goal when it has none.
 *  guidedEligible(rec, i)  the step's target depends on the period (templated) or sits in a repeated row (variable).
 *  guidedDecide(...)       ONE action for step i from the agent brain: goal (period substituted), "last time at this
 *                          step you clicked ...", the steps done, the observation. needs_user / done -> MISSING (the
 *                          recorded, old target is never used instead). At most GUIDED_CAP model calls per run.
 *  guidedExecute(...)      validate against a fresh observation, the Tell-it safety gate (+ the content script's own),
 *                          execute with agent_act, record which control it chose.
 *  downloadExpectation()   the file name a run must produce for its period, and the same pattern for any period
 *                          (a match there but not for this period = WRONG_PERIOD).
 *  offerUpdate / guidedUpdate  skills memory: after a VERIFIED run with model-decided steps, "the portal changed: use
 *                          the new step from now on?"; accepted -> the model's control becomes the step (templated
 *                          again), the old one goes to rec.history.
 * Privacy: the same brains and rules as Tell it. Local (loopback) by default; Claude only with the user's consent and
 * key, and never for a recipe / site the user locked off the cloud (then the local model decides).
 */
var GUIDED_CAP = 5;

// ---- save / import --------------------------------------------------------------------------------------------
function applyPeriod(rec, keep) {
  if (!keep || !PD.valid(rec.period)) {
    var inf = PD.infer(rec, nowMs());
    rec.period = inf.period; rec.periodInferred = inf.inferred;
    if (rec.periodMode !== 'this' && rec.periodMode !== 'previous') rec.periodMode = inf.mode;
  }
  rec.tpl = PD.scan(rec, rec.period, rec.tpl || {});
  rec.goalProposed = rec.goal ? '' : proposeGoal(rec);
  return rec;
}
/** "Download the {{month_name}} {{year}} invoice from portal.example" for a recording that downloads something. */
function proposeGoal(rec) {
  var tv = PD.templatedView(rec), dl = null;
  (tv.steps || []).forEach(function (s) { if (s.action === 'click' && (s.download || downloadStep(s))) dl = s; });
  if (!dl) return '';
  var raw = (dl.loc && (dl.loc.name || dl.loc.text)) || (dl.download && dl.download.name) || '';
  var what = raw.replace(/\{\{[a-z_]+\}\}/g, ' ').replace(/\.[a-z0-9]{2,5}$/i, '').replace(/\b(download|get|open|view|latest|the)\b/gi, ' ')
    .replace(/[\s\-_.,/()]+/g, ' ').trim().toLowerCase();
  if (!what || /^(pdf|csv|xlsx?|file|here|document)$/.test(what)) {
    what = ((dl.download && dl.download.name) || '').replace(/\{\{[a-z_]+\}\}/g, ' ').replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[\s\-_.,/()]+/g, ' ').trim().toLowerCase() || 'document';
  }
  var templ = Object.keys(rec.tpl || {}).some(function (k) { return rec.tpl[k].on !== false; });
  return 'Download the ' + (templ ? '{{month_name}} {{year}} ' : '') + what + ' from ' + (R.siteOf(rec.startUrl || '') || 'the site');
}

// ---- deciding a step by meaning ----------------------------------------------------------------------------------
function guidedEligible(rec, i) {
  var s = rec.steps[i] || {}, templated = PD.stepTemplated(rec.tpl, i), variable = !!s.variable;
  return { templated: templated, variable: variable, any: s.action !== 'goto' && (templated || variable) };
}
function describeLoc(s) {
  var l = (s && s.loc) || {}, role = l.role || l.tag || 'control';
  var word = { link: 'link', button: 'button', textbox: 'field', combobox: 'dropdown', checkbox: 'checkbox', password: 'field' }[role] || role;
  var name = l.name || l.text || l.label || l.placeholder || l.title || '';
  return 'the ' + word + ' "' + String(name).slice(0, 80) + '"' + (l.context ? ' in "' + String(l.context).slice(0, 60) + '"' : '');
}
function stepVerbPast(s) { return s.action === 'fill' ? 'typed into' : s.action === 'select' ? 'chose in' : s.action === 'press' ? 'pressed Enter in' : 'clicked'; }
function sameKind(action, verb) { return action === verb || (action === 'press' && verb === 'fill_enter'); }

/** the expected download for the run's period, for the failure reasons a collection shows. */
function expectNote(base, run) {
  var dx = downloadExpectation(base, run, run.period);
  return dx.exact ? ' Expected a file matching ' + dx.exact + '.' : '';
}
/** the recipe ends MISSING: what the goal needs for this period is not there (the model said so). Nothing else runs. */
async function missing(run, reason) {
  run.status = 'failed';
  run.result = { verdict: 'MISSING', code: 'MISSING', reason: reason, checks: [] };
  logRun(run, 'MISSING: ' + reason);
  await archive(run);
  await sendTab(run.tabId, { type: 'banner', text: 'missing: ' + reason }).catch(function () {});
  await afterRun(run);
}

/** -> {act, expectName, role, show, reason, why} or null when the run already ended (missing / failed). */
async function guidedDecide(run, base, rec, step, i, why) {
  var n1 = i + 1;
  run.guidedCalls = run.guidedCalls || 0;
  if (run.guidedCalls >= GUIDED_CAP) {
    await fail(run, 'NOT_FOUND', 'step ' + n1 + ': ' + why + '; the model already decided ' + GUIDED_CAP + ' steps in this run (the cap), so the run stops here.' + expectNote(base, run));
    return null;
  }
  var cfg = await tellSettings(), brain = cfg.agentBrain;
  var tabUrl = ((await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url) || '';
  if (brain === 'claude') {
    cfg.claudeKey = await getLocal('claudeKey', '');
    var blocked = M.claudeBlocked(cfg), lock = cloudLockedFor(cfg, tabUrl, base) || cloudLockedFor(cfg, base.startUrl || '', base);
    if (blocked || lock) {
      brain = 'local'; delete cfg.claudeKey;
      logRun(run, 'step ' + n1 + ': the step decision uses the local agent model (' + (lock ? 'cloud is locked off for ' + lock : blocked) + ')');
    }
  }
  if (brain === 'local' && !M.isLoopback(cfg.agentEndpoint)) {
    await fail(run, 'NOT_FOUND', 'step ' + n1 + ': ' + why + '; the agent endpoint is not on this computer, so nothing was sent.' + expectNote(base, run));
    return null;
  }
  var raw = await sendRetry(run.tabId, { type: 'agent_observe' });
  if (brain === 'claude' && cloudLockedFor(cfg, raw.url, base)) { brain = 'local'; delete cfg.claudeKey; logRun(run, 'step ' + n1 + ': this page is cloud-locked; the local agent model decides'); }
  var obs = AG.maskObs(raw), hits = AG.detectInjection(obs);
  var goal = rec.goal || PD.substitute(base.goalProposed || ('Repeat the saved chore "' + base.name + '"'), run.period);
  goal = R.substitute(goal, runParams(run));
  var hint = 'This is a saved recipe replayed for ' + PD.label(run.period) + (base.period ? ' (it was recorded for ' + PD.label(base.period) + ')' : '')
    + ': give ONE action for this step only, the recipe does the rest. If the page does not have what the goal needs for '
    + PD.label(run.period) + ' (for example it is not posted yet), reply needs_user("why"). Never pick something for another period.\n'
    + 'Last time at this step you ' + stepVerbPast(base.steps[i]) + ' ' + describeLoc(base.steps[i]) + '.';
  var history = (run.history || []).slice(-6).map(function (h, k, a) { return (k + 1 + Math.max(0, (run.history || []).length - a.length)) + '. ' + h + ' -> ok'; });
  var prompt = AG.buildPrompt({ goal: goal, step: run.guidedCalls + 1, maxSteps: GUIDED_CAP, visited: [], history: history, obs: obs, window: 0,
                                injected: hits.length > 0, hint: hint });
  var grammar = AG.buildGrammar((obs.controls || []).length), system = AG.SYSTEM + AG.SYSTEM_WEB;
  logRun(run, 'step ' + n1 + ': ' + why + '; asking the ' + (brain === 'claude' ? 'Claude' : 'local') + ' agent model to decide by meaning');
  run.guidedCalls++;
  await saveRun(run);
  var t0 = Date.now(), r = await AG.decide(tellChat({ brain: brain }, cfg), system, prompt, grammar, brain === 'claude');
  var p = r.error ? null : AG.tryParse(r.reply);
  var act = p && !p.error ? p.action : null;
  var reason = (String(r.reply || '').split('\n').filter(function (l) { return l.indexOf('# ') === 0; })[0] || '').slice(2);
  (run.modelCalls = run.modelCalls || []).push({ step: i, kind: 'guided', brain: brain, ms: Date.now() - t0, why: why,
    answer: r.error ? '' : String(r.reply || '').slice(0, 300), outcome: r.error ? 'error' : act ? AG.show(act) : 'invalid' });
  if (r.error) { await fail(run, 'NOT_FOUND', 'step ' + n1 + ': ' + why + '; the agent model could not answer: ' + r.error + expectNote(base, run)); return null; }
  if (!act || r.invalid) { await fail(run, 'NOT_FOUND', 'step ' + n1 + ': ' + why + '; the agent model gave no usable action (' + ((p && p.error) || r.firstError || 'invalid') + ').' + expectNote(base, run)); return null; }
  if (act.verb === 'needs_user' || act.verb === 'done') {
    run.guided = run.guided || [];
    run.guided.push({ step: i, why: why, reason: act.text || reason, action: AG.show(act), control: null, verb: act.verb, period: run.period });
    await missing(run, 'step ' + n1 + ': ' + (act.text || reason || 'the model found nothing for ' + PD.label(run.period) + ' on this page')
                       + ' (the model looked for ' + PD.label(run.period) + '; the recorded target was not used)');
    return null;
  }
  if (!/^(click|fill|fill_enter|select)$/.test(act.verb)) {
    await fail(run, 'NOT_FOUND', 'step ' + n1 + ': the agent model answered ' + AG.show(act) + ', not an action on this page.' + expectNote(base, run));
    return null;
  }
  var v = AG.validate(act, obs);
  if (v.error) { await fail(run, 'NOT_FOUND', 'step ' + n1 + ': the agent model chose ' + AG.show(act) + ', which does not fit the page: ' + v.error + expectNote(base, run)); return null; }
  if (v.secret) { await fail(run, 'NEEDS_YOU', 'step ' + n1 + ': the agent model wanted to type into a password / code field; AWOS never does that.'); return null; }
  return { step: i, act: act, expectName: v.control.name || '', role: v.control.role || '', show: AG.show(act), reason: reason, why: why };
}

/** the Tell-it gate words for an observed control (strict vocabulary + the micro-agent's destructive list). */
function guidedDanger(act, c) {
  if (act.verb === 'fill' || (act.verb === 'select' && !(c.role === 'radiogroup' && !(c.options && c.options.length)))) return null;
  var txt = (c.name || '') + ' | ' + (c.context || '');
  if (act.verb === 'fill_enter') return S.dangerMatchStrict('', 'tell');
  return S.dangerMatchStrict(txt, 'tell') || (AG.isDestructive(c) ? 'destructive' : null);
}

/** -> {cand, how, index, urlBefore, tabsBefore} after the action ran, or null when paused / failed. */
async function guidedExecute(run, step, i, gd) {
  var n1 = i + 1, ok = approved(run, i, gd.expectName);
  var raw = await sendRetry(run.tabId, { type: 'agent_observe' });          // fresh numbering (an approval may come later)
  var obs = AG.maskObs(raw), ctl = obs.controls || [], act = JSON.parse(JSON.stringify(gd.act));
  if (act.n) {
    var c0 = ctl[act.n - 1];
    if (!c0 || c0.name !== gd.expectName || c0.role !== gd.role) {
      var k = ctl.findIndex(function (c) { return c.name === gd.expectName && c.role === gd.role; });
      if (k < 0) { run.guidedPending = null; await fail(run, 'NOT_FOUND', 'step ' + n1 + ': the control the model chose ("' + gd.expectName + '") is gone'); return null; }
      act.n = k + 1;
    }
  }
  var v = AG.validate(act, obs);
  if (v.error || !v.control) { run.guidedPending = null; await fail(run, 'NOT_FOUND', 'step ' + n1 + ': ' + (v.error || 'the model\'s choice does not fit the page')); return null; }
  if (v.secret) { run.guidedPending = null; await fail(run, 'NEEDS_YOU', 'step ' + n1 + ': the model chose a password / code field; AWOS never types those'); return null; }
  var c = v.control, danger = guidedDanger(act, c);
  if (danger && !ok) {
    run.guidedPending = gd;
    await pause(run, 'awaiting_approval', { danger: danger, target: gd.expectName, guided: true,
      message: 'Step ' + n1 + ': the page changed and the model chose to ' + (act.verb === 'fill_enter' ? 'press Enter in' : act.verb) + ' "' + gd.expectName
             + '" (matches "' + danger + '"). Approve to continue.' });
    return null;
  }
  var urlBefore = ((await chrome.tabs.get(run.tabId).catch(function () { return {}; })).url) || '';
  var tabsBefore = (await chrome.tabs.query({}).catch(function () { return []; })).map(function (t) { return t.id; });
  var r = await sendTab(run.tabId, { type: 'agent_act', act: act, approved: ok, expectName: gd.expectName })
    .catch(function (e) { return { ok: false, error: 'the page did not answer (' + (e.message || e) + ')' }; });
  if (!r || !r.ok) {
    if (r && r.needsApproval && !ok) {
      run.guidedPending = gd;
      await pause(run, 'awaiting_approval', { danger: r.danger, target: gd.expectName, guided: true,
        message: 'Step ' + n1 + ': the model chose "' + gd.expectName + '" (matches "' + r.danger + '"). Approve to continue.' });
      return null;
    }
    run.guidedPending = null;
    await fail(run, 'ERROR', 'step ' + n1 + ': ' + ((r && (r.error || (r.secret && 'a password / code field'))) || 'no reply'));
    return null;
  }
  run.guidedPending = null;
  run.guided = run.guided || [];
  run.guided.push({ step: i, why: gd.why, reason: gd.reason, action: gd.show, control: { role: c.role || '', name: c.name || '' }, loc: r.loc || null,
                    verb: act.verb, period: run.period, recDanger: r.recDanger || null, approved: ok });
  logRun(run, 'step ' + n1 + ': decided by the model: ' + gd.show + ' = ' + (c.role || 'control') + ' "' + (c.name || '') + '"'
              + (gd.reason ? ' (' + gd.reason + ')' : '') + (ok ? ' [approved by you]' : ''));
  return { cand: { name: c.name || '', role: c.role || '' }, how: 'model decision (goal-guided)', index: act.n ? act.n - 1 : -1,
           urlBefore: urlBefore, tabsBefore: step.action === 'click' || act.verb === 'click' ? tabsBefore : null };
}

// ---- the result against the goal: the downloaded file vs the run's period -----------------------------------------
function escRx(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
/** a recorded download name with placeholders -> a regex for it (Chrome's " (1)" for a repeat allowed). */
function nameRegex(t) {
  var out = String(t).split(/(\{\{[a-z_]+\}\})/).map(function (x, k) { return k % 2 ? x : escRx(x); }).join('');
  return '^' + out.replace(/(\\\.[A-Za-z0-9]{1,5})$/, '(?: \\(\\d+\\))?$1') + '$';
}
var ANY_PERIOD = {
  year: '(?:19|20)\\d{2}', month: '(?:0[1-9]|1[0-2])', month_n: '(?:1[0-2]|[1-9])', quarter: '[1-4]',
  month_name: '(?:' + PD.MONTHS.join('|') + ')', month_short: '(?:Sept|' + PD.SHORT.join('|') + ')',
};
function genericize(t, params) {
  return String(t).replace(/\{\{([a-z_]+)\}\}/g, function (all, n) {
    if (!PD.BUILTIN_SET[n] || (params && params[n] != null)) return all;      // a param of the recipe stays its value
    return ANY_PERIOD[n] || ANY_PERIOD[n.replace(/_(lower|upper)$/, '')];
  });
}
/** -> {exact, generic}: the download name pattern for `period`, and for any period ('' when it names none). */
function downloadExpectation(base, run, period) {
  var tv = PD.templatedView(base || {}), tmpl = '', tpl = (base && base.tpl) || {};
  if (tv.expectDownload && tv.expectDownload.nameRegex) tmpl = tv.expectDownload.nameRegex;
  else (tv.steps || []).forEach(function (s, k) {
    var e = tpl['steps.' + k + '.download.name'];
    if (e && e.on !== false && s.download && s.download.name) tmpl = nameRegex(s.download.name);
  });
  if (!tmpl) return {};
  var params = run.params || {}, P = Object.assign(PD.builtins(period), params);
  var hasP = PD.hasTokens(tmpl.replace(/\{\{([a-z_]+)\}\}/g, function (all, n) { return params[n] != null ? '' : all; }));
  return { exact: R.substitute(tmpl, P), generic: hasP ? R.substitute(genericize(tmpl, params), params) : '' };
}
/** a download completed in this run whose name matches `rx` -> its name, else ''. */
async function findDownload(run, rx) {
  var re; try { re = new RegExp(rx, 'i'); } catch (e) { return ''; }
  var items = await chrome.downloads.search({ startedAfter: new Date(run.startedAt - 1000).toISOString() }).catch(function () { return []; });
  for (var k = 0; k < items.length; k++) {
    if (items[k].state !== 'complete' || items[k].id <= (run.dlMark || 0)) continue;     // only files this run downloaded
    var n = downloadNames(items[k]).find(function (x) { return re.test(x); });
    if (n) return n;
  }
  return '';
}

// ---- skills memory: keep what the model found when it worked ---------------------------------------------------------
function offerUpdate(run, base, verdict) {
  if (verdict !== 'VERIFIED' || !base || !(run.guided || []).length) return;
  var steps = run.guided.filter(function (g) { return g.loc && base.steps[g.step] && sameKind(base.steps[g.step].action, g.verb); })
    .map(function (g) { return { step: g.step, from: describeLoc(base.steps[g.step]), to: g.control, reason: g.reason || '' }; });
  if (!steps.length) return;
  run.updateOffer = { nonce: nonce(), steps: steps, period: run.period };
  logRun(run, 'the portal changed: ' + steps.length + ' step' + (steps.length === 1 ? ' was' : 's were') + ' decided by the model and the run is VERIFIED; '
              + 'the popup offers to use the new step from now on');
}
async function guidedUpdate(msg) {
  var cur = await getSess('run'), runs = await getLocal('runs', []);
  var run = cur && cur.id === msg.runId ? cur : runs.find(function (x) { return x.id === msg.runId; });
  if (!run || !run.updateOffer || run.updateOffer.nonce !== msg.nonce || run.updateOffer.done) throw new Error('stale or unknown update offer');
  var rec = await getRec(run.recId);
  if (!rec) throw new Error('the recipe was deleted');
  var changed = [];
  run.updateOffer.steps.forEach(function (o) {
    var g = (run.guided || []).find(function (x) { return x.step === o.step && x.loc; }), s = rec.steps[o.step];
    if (!g || !s) return;
    var entry = { at: Date.now(), runId: run.id, period: run.period, step: o.step, from: describeLoc(s), reason: g.reason || '', accepted: !!msg.accept };
    if (msg.accept) {
      s.loc = g.loc;
      s.hint = describeLoc(s);
      s.learnedAt = Date.now();
      if (s.action === 'click') s.danger = g.recDanger || null;
      rec.tpl = PD.scan(rec, run.period, rec.tpl || {}, 'steps.' + o.step + '.loc.');   // the new target, templated for its period
      entry.to = describeLoc(s);
    } else entry.to = 'kept ' + entry.from + ' (offered: the ' + (g.control.role || 'control') + ' "' + g.control.name + '")';
    changed.push(entry);
  });
  rec.history = (rec.history || []).concat(changed).slice(-20);
  await putRec(rec);
  var done = msg.accept ? 'accepted' : 'declined';
  if (cur && cur.id === run.id) { cur.updateOffer.done = done; await setSess('run', cur); }
  var k = runs.findIndex(function (x) { return x.id === run.id; });
  if (k >= 0 && runs[k].updateOffer) { runs[k].updateOffer.done = done; await setLocal('runs', runs); }
  return { ok: true, changed: changed, recipe: rec };
}
