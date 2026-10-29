/* AWOS Replay "Tell it" (v0.5): the user types a goal, the agent drives the tab one action at a time while the
 * user watches; a confirmed run is saved as a normal recording (recipe) and kept locally as a training trajectory.
 * Loaded into the service worker by background.js (importScripts), so it shares its helpers (getSess, sendTab,
 * waitForLoad, ensureContent, sendRetry, waitQuiet, putRec, uid, nonce, HANDLERS ...).
 *
 * Loop (one model call per step, docs/specs/tell_it.md):
 *   observe (content agent_observe = AWOSLocator.observe, numbered as the agent sees it) -> page guards
 *   (password / code / CAPTCHA / HTTP error = the user handles it, the agent waits) -> prompt (lib/agent.js =
 *   micro_agent._prompt) -> brain (local llama-server by default, Claude only on explicit opt-in) -> parse +
 *   grammar check, one repair call -> validate against the observation -> safety gate (strict vocabulary +
 *   micro_agent's destructive list -> Approve click in the popup) -> execute with the replay actuators
 *   (content agent_act) -> record the step in the recording format -> repeat.
 *   Caps: 25 model steps, 10 min of agent time (pauses for the user do not count), 3 invalid replies in a
 *   row -> pause and ask the user.
 * Privacy: the local brain is the default; the Claude brain needs the user's opt-in (consent + own key, the same
 * rule as repair / verify), and a "never use cloud" lock on the page's site or the recipe always wins: the run
 * stops before any page data is sent. Secrets are never read: sensitive fields are typed by the user, the
 * recording keeps a {{secret:...}} placeholder (replay asks for it).
 * State: chrome.storage.session 'tell' (the live episode); chrome.storage.local 'tellTraj' (last 20 episodes in
 * the micro-agent Trajectory format, scaffold/rl/contract.py; exported only on the user's click).
 */
var AG = self.AWOSAgent;
var TELL_MAX_TRAJ = 20;
var TELL_DEFAULTS = { agentBrain: 'local', agentEndpoint: 'http://127.0.0.1:8081/v1', agentModel: 'qwen3.5-4b',
                      agentClaudeModel: 'claude-haiku-5-5', noCloudSites: [], keepTraining: true,
                      searchPage: 'https://duckduckgo.com/?q={goal}' };
/** a site the goal names ("... on wikipedia.org", "https://..."), as an https URL; not e-mail addresses. */
function siteInGoal(goal) {
  var m = /(?:^|[\s("'])((?:https?:\/\/)?(?:[a-z0-9-]+\.)+[a-z]{2,24})(\/[^\s"')]*)?/i.exec(String(goal || ''));
  if (!m) return '';
  var host = m[1].replace(/^https?:\/\//i, '');
  if (/^\d+(\.\d+)+$/.test(host) || !/[a-z]/i.test(host.split('.').pop())) return '';
  return 'https://' + host + (m[2] || '/');
}
var CLAUDE_AGENT_MODELS = ['claude-haiku-5-5', 'claude-sonnet-5-5'];
function localAgentHint(ep) {
  return 'No local agent model is answering at ' + ep + '. Set one up on this Mac (private: page data never leaves it) '
    + 'with the AWOS installer: install_replay.sh --agent 4b (Qwen3.5-4B on llama-server, 127.0.0.1:8081), or --agent 9b '
    + 'on a Mac with 16 GB or more, then try again. The small 2B model on 8080 only repairs / verifies; it cannot drive a page.';
}

async function tellSettings() {
  var s = Object.assign({}, TELL_DEFAULTS, await settings());
  if (CLAUDE_AGENT_MODELS.indexOf(s.agentClaudeModel) < 0) s.agentClaudeModel = TELL_DEFAULTS.agentClaudeModel;
  if (s.agentBrain !== 'claude') s.agentBrain = 'local';
  // 0.5.0 Options saved the bare search home page; it now starts on the results for the goal
  if (/^https:\/\/duckduckgo\.com\/?$/i.test(String(s.searchPage || ''))) s.searchPage = TELL_DEFAULTS.searchPage;
  return s;
}
/** the cloud may not see this page: its site is locked by the user, or the recipe is. */
function cloudLockedFor(cfg, url, rec) {
  if (rec && rec.noCloud) return 'this recipe';
  var site = R.siteOf(url);
  return site && (cfg.noCloudSites || []).indexOf(site) >= 0 ? site : '';
}

function feed(t, kind, text, extra) {
  t.feed.push(Object.assign({ t: Date.now(), kind: kind, text: String(text).slice(0, 400) }, extra || {}));
  if (t.feed.length > 80) t.feed.splice(0, t.feed.length - 80);
}
function elapsed(t) { return (t.activeMs || 0) + (t.segStart ? Date.now() - t.segStart : 0); }
async function saveTell(t) {
  await setSess('tell', t);
  var b = /^(awaiting_approval|needs_user|confirm)$/.test(t.status) ? '!' : t.status === 'running' ? '>' : '';
  chrome.action.setBadgeText({ text: b }).catch(function () {});
}
function stopClock(t) { t.activeMs = elapsed(t); t.segStart = 0; }

async function tellPause(t, status, pending) {
  stopClock(t);
  t.status = status; t.pending = Object.assign({ nonce: nonce(), step: t.step }, pending);
  feed(t, status === 'awaiting_approval' ? 'approve' : 'wait', pending.message);
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: pending.message + ' (open the AWOS popup)' }).catch(function () {});
}

/** the episode ends (any outcome but the user's confirmation): keep the trajectory, clear the banner. */
async function tellEnd(t, outcome, reason) {
  stopClock(t);
  t.status = 'ended'; t.outcome = outcome; t.reason = reason; t.pending = null;
  feed(t, 'end', outcome + (reason ? ': ' + reason : ''));
  await keepTrajectory(t, null);
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
}

async function keepTrajectory(t, confirmed) {
  var cfg = await tellSettings();
  if (cfg.keepTraining === false || t.kept) return;
  t.kept = true;
  var tr = Object.assign({}, t.traj, { outcome: t.outcome || '', reason: t.reason || '', seconds: Math.round(elapsed(t) / 10) / 100,
    kind: 'awos-tellit-trajectory/1', goal: t.goal, brain: t.brain, user_confirmed: confirmed, verifier: t.result && t.result.verifier || null,
    answer: t.result && t.result.answer || '', recording: t.savedRecId || '', createdAt: t.startedAt });
  var all = await getLocal('tellTraj', []);
  all.unshift(tr); await setLocal('tellTraj', all.slice(0, TELL_MAX_TRAJ));
}

// ---- the brain ---------------------------------------------------------------------------------------------
function tellChat(t, cfg) {
  if (t.brain === 'claude') {
    var s = Object.assign({}, cfg, { claudeModel: cfg.agentClaudeModel });
    return function (messages) { return M.claudeChat(s, messages, 300, 60000); };
  }
  return async function (messages, grammar) {
    if (!M.isLoopback(cfg.agentEndpoint)) return { error: 'agent endpoint is not loopback; refusing to send page data: ' + cfg.agentEndpoint };
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 120000);
    try {
      var resp = await fetch(cfg.agentEndpoint.replace(/\/+$/, '') + '/chat/completions', {
        method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: cfg.agentModel, messages: messages, temperature: 0, max_tokens: 200, grammar: grammar,
                               chat_template_kwargs: { enable_thinking: false } }),
      });
      if (!resp.ok) return { error: 'local agent model HTTP ' + resp.status };
      var j = await resp.json();
      var c = j && j.choices && j.choices[0];
      return { text: c ? (c.message ? c.message.content : c.text) || '' : '' };
    } catch (e) {
      return { error: localAgentHint(cfg.agentEndpoint), localDown: true };
    } finally { clearTimeout(timer); }
  };
}

/** is a usable local agent model there? -> '' or why not (never suggests the cloud). */
async function checkLocalAgent(cfg) {
  if (!M.isLoopback(cfg.agentEndpoint)) return 'The agent endpoint must be on this computer (127.0.0.1 / localhost).';
  var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 2500);
  try {
    var r = await fetch(cfg.agentEndpoint.replace(/\/+$/, '') + '/models', { signal: ctl.signal });
    if (!r.ok) return '';                                   // answering, just no model list: try it
    var j = await r.json().catch(function () { return null; });
    var ids = ((j && j.data) || []).map(function (m) { return String(m.id || ''); });
    if (ids.length && ids.every(function (id) { return /(^|[^0-9.])2b\b|g6|g7|verif|repair/i.test(id); }))
      return 'The local server only runs the small repair / verify model (' + ids.join(', ') + '), which cannot drive a page. '
        + 'Set up the agent model with the AWOS installer: install_replay.sh --agent 4b (or --agent 9b on 16 GB+ Macs); '
        + 'it serves on http://127.0.0.1:8081/v1, the default agent endpoint in Options.';
    return '';
  } catch (e) { return localAgentHint(cfg.agentEndpoint); }
  finally { clearTimeout(timer); }
}

// ---- start / drive -----------------------------------------------------------------------------------------
async function tellBusy() { var t = await getSess('tell'); return t && /^(running|paused|awaiting_approval|needs_user|confirm)$/.test(t.status) ? t : null; }

async function tellStart(msg) {
  var busy = await busyReason();
  if (busy) throw new Error(busy);
  if (await tellBusy()) throw new Error('a Tell-it run is already in progress: stop it first');
  var goal = String(msg.goal || '').trim().slice(0, 500);
  if (!goal) throw new Error('type what it should do');
  var tab = await chrome.tabs.get(msg.tabId);
  var cfg = await tellSettings();
  // start: the typed start page, else this tab if it is a web page, else a site named in the goal, else the search
  // page (the agent searches or goto()s from there, like a person opening a new tab). No start page is required.
  var start = String(msg.startUrl || '').trim(), startNote = '';
  if (start && !/^https?:\/\//i.test(start)) start = 'https://' + start;
  if (!start && /^https?:\/\//i.test(tab.url || '')) start = tab.url;
  if (!start) {
    var named = siteInGoal(goal);
    if (named) { start = named; startNote = 'I opened ' + named + ' because the goal names it.'; }
    else {
      // the search RESULTS for the goal ({goal} in the setting): typing into a search engine's box is fragile (DuckDuckGo
      // ignores synthetic Enter; a real run repeated it 22 times) and costs a step
      var sp = cfg.searchPage || TELL_DEFAULTS.searchPage;
      start = sp.indexOf('{goal}') >= 0 ? sp.replace('{goal}', encodeURIComponent(goal.slice(0, 200))) : sp;
      startNote = sp.indexOf('{goal}') >= 0
        ? 'No site was open: you are on web search results for the goal. Open the result that fits, or use goto("https://...") if you know the address.'
        : 'No site was open: you are on a search page. Search for the site you need, or use goto("https://...") if you know its address.';
    }
  }
  if (cfg.agentBrain === 'claude') {
    cfg.claudeKey = await getLocal('claudeKey', '');
    var why = M.claudeBlocked(cfg);
    if (why) throw new Error(why);
    var lock = cloudLockedFor(cfg, start);
    if (lock) throw new Error('Cloud is locked off for ' + lock + ': nothing from it is sent to Anthropic. Switch Tell it to the local model in Options to run here.');
  } else {
    var bad = await checkLocalAgent(cfg);
    if (bad) throw new Error(bad);
  }
  var caps = msg.caps || {};
  var t = { id: uid('tell'), tabId: msg.tabId, goal: goal, startUrl: start, status: 'running', startedAt: Date.now(),
            brain: cfg.agentBrain, model: cfg.agentBrain === 'claude' ? 'anthropic:' + cfg.agentClaudeModel : 'local:' + cfg.agentModel,
            maxSteps: Math.max(1, Math.min(AG.MAX_STEPS, caps.steps || AG.MAX_STEPS)),
            maxSeconds: Math.max(1, Math.min(AG.MAX_SECONDS, caps.seconds || AG.MAX_SECONDS)),
            step: 0, activeMs: 0, segStart: Date.now(), window: 0, visited: [], history: [], invalid: 0, note: startNote,
            steps: [{ action: 'goto', url: start }], feed: [], pending: null, pendingHist: null, guardSkip: {}, flagSeen: {},
            traj: { task_id: '', model: '', steps: [], outcome: '', reason: '', events: [], flags: [], seconds: 0, error: '' } };
  t.traj.task_id = t.id; t.traj.model = t.model;
  feed(t, 'info', 'Goal: ' + goal + ' | brain: ' + (t.brain === 'claude' ? 'Claude (' + cfg.agentClaudeModel + '; page text of each step goes to Anthropic)'
                                                                       : 'local model (nothing leaves this computer)'));
  await saveTell(t);
  await chrome.tabs.update(t.tabId, { url: start });
  await sleep(150);
  tellDrive(t.id);
  return t;
}

var tellDriving = null;
async function tellDrive(id) {
  if (tellDriving === id) return;
  tellDriving = id;
  try { await tellLoop(id); }
  catch (e) {
    var t = await getSess('tell');
    if (t && t.id === id && t.status === 'running') await tellEnd(t, 'halted', 'error: ' + String(e && e.message || e));
  } finally { if (tellDriving === id) tellDriving = null; }
}

function histOk(t, obs) {
  if (!t.pendingHist) return;
  var lt = AG.liveText(obs);
  t.history.push(t.pendingHist.step + '. ' + t.pendingHist.show + ' -> ok; now on "' + obs.title + '"' + (lt ? ' (' + lt.slice(0, 160) + ')' : ''));
  t.pendingHist = null;
}

async function tellLoop(id) {
  for (;;) {
    var t = await getSess('tell');
    if (!t || t.id !== id || t.status !== 'running') return;
    if (t.pauseRequested) {
      t.pauseRequested = false; stopClock(t); t.status = 'paused';
      feed(t, 'info', t.takeover ? 'You have the page. Press Resume when you are done.' : 'Paused.');
      await saveTell(t); return;
    }
    var cfg = await tellSettings();
    if (t.brain === 'claude') cfg.claudeKey = await getLocal('claudeKey', '');    // only for the call itself; consent re-checked per call
    if (t.resumeAct) { var ra = t.resumeAct; t.resumeAct = null; await tellExecute(t, cfg, ra.act, ra.ctx, true); continue; }
    if (t.step >= t.maxSteps) return tellEnd(t, 'halted', 'step cap ' + t.maxSteps + ' reached');
    if (elapsed(t) > t.maxSeconds * 1000) return tellEnd(t, 'halted', 'time cap ' + t.maxSeconds + 's reached');
    await waitForLoad(t.tabId); await ensureContent(t.tabId);
    var raw = await sendRetry(t.tabId, { type: 'agent_observe' });
    if (t.brain === 'claude') {
      var lock = cloudLockedFor(cfg, raw.url);
      if (lock) return tellEnd(t, 'needs_user', 'this page is on ' + lock + ', where the cloud is locked off; stopped before sending anything');
    }
    var obs = AG.maskObs(raw);
    histOk(t, obs);
    var hits = AG.detectInjection(obs), path = String(raw.url).replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    hits.forEach(function (h) {
      if (t.flagSeen[path + h]) return;
      t.flagSeen[path + h] = 1;
      t.traj.flags.push({ type: 'prompt_injection', page: path, detail: 'page text addressed to AI agents was treated as untrusted data and ignored: "' + AG.maskSecrets(h).slice(0, 200) + '"' });
      feed(t, 'warn', 'This page has text addressed to AI agents; it is ignored.');
    });
    var g = AG.guardPage(obs);
    if (g && !t.guardSkip[path + ':' + g.kind]) {
      var gm = g.kind === 'signin' || g.kind === 'mfa'
        ? 'This page asks for a password or code. Type it into the page yourself (AWOS never reads it), then press Continue.'
        : g.kind === 'captcha' || g.kind === 'passkey' ? 'This page wants a human check (' + g.reason + '). Do it yourself, then press Continue.'
        : 'The page looks wrong (' + g.reason + '). Fix it in the tab and press Continue, or Stop.';
      return tellPause(t, 'needs_user', { kind: 'guard', guard: g.kind, guardKey: path + ':' + g.kind, message: gm });
    }
    if (!t.visited.length || t.visited[t.visited.length - 1] !== obs.title) t.visited.push(obs.title);
    t.step += 1;
    var note = t.note; t.note = '';
    var system = AG.SYSTEM + AG.SYSTEM_WEB;
    var prompt = AG.buildPrompt({ goal: t.goal, step: t.step, maxSteps: t.maxSteps, visited: t.visited, history: t.history,
                                  obs: obs, window: t.window, injected: hits.length > 0, note: note });
    var grammar = AG.buildGrammar((obs.controls || []).length);
    await saveTell(t);
    var t0 = Date.now(), r = await AG.decide(tellChat(t, cfg), system, prompt, grammar, t.brain === 'claude');
    t = await getSess('tell');
    if (!t || t.id !== id) return;
    if (t.status !== 'running') return;                     // stopped while the model was thinking: do nothing
    if (r.error) {
      t.step -= 1; t.note = note;
      return tellPause(t, 'needs_user', { kind: 'brain', message: 'The model could not answer: ' + r.error });
    }
    var ts = { i: t.step, system: system, user: prompt, grammar: grammar, reply: r.reply, action: '', valid: false, url: raw.url, ms: Date.now() - t0 };
    if (note) ts.note = note;
    if (r.repaired) ts.repaired = true;
    t.traj.steps.push(ts);
    var p = AG.tryParse(r.reply), k = t.step;
    if (p.error) {
      t.history.push(k + '. (unparseable reply) -> rejected: ' + p.error);
      t.traj.events.push({ step: k, rejected: p.error });
      feed(t, 'bad', 'step ' + k + ': unusable reply (' + p.error + ')');
      if (await tellInvalid(t)) return;
      await saveTell(t); continue;
    }
    var act = p.action, show = AG.show(act);
    ts.action = show;
    var reason = (String(r.reply).split('\n').filter(function (l) { return l.indexOf('# ') === 0; })[0] || '').slice(2);
    feed(t, 'step', show, { k: k, reason: reason });
    var ctrl = act.n ? (raw.controls || [])[act.n - 1] : null;
    await tellExecute(t, cfg, act, { k: k, show: show, obs: obs, url: raw.url,
                                     expectName: ctrl ? ctrl.name : null, nControls: (obs.controls || []).length }, false);
  }
}

/** a rejected / unusable step: 3 in a row -> pause and ask the user. -> true when paused. */
async function tellInvalid(t) {
  t.invalid += 1;
  if (t.invalid < AG.MAX_INVALID) return false;
  t.invalid = 0;
  var hint = t.brain === 'local' ? ' A bigger local model (the 9B) follows the format more reliably.' : '';
  await tellPause(t, 'needs_user', { kind: 'invalid', message: 'The model kept producing actions that do not fit this page. Type a correction below and press Continue, or Stop.' + hint });
  return true;
}
function tellReject(t, ctx, err, cancelled) {
  t.history.push(ctx.k + '. ' + ctx.show + ' -> REJECTED: ' + err);
  t.traj.events.push({ step: ctx.k, action: ctx.show, rejected: err });
  var st = t.traj.steps[t.traj.steps.length - 1]; if (st && st.i === ctx.k) st.valid = false;
  feed(t, 'bad', 'rejected: ' + err);
  return cancelled;
}
function markValid(t, ctx, ev) {
  var st = t.traj.steps[t.traj.steps.length - 1]; if (st && st.i === ctx.k) st.valid = true;
  t.traj.events.push(Object.assign({ step: ctx.k, action: ctx.show }, ev || {}));
}
function isRadio(c) { return c && c.role === 'radiogroup' && !(c.options && c.options.length); }

/** wait (bounded) for a click / Enter to navigate, then for the page to settle. -> changed */
async function tellSettle(tabId, before) {
  var end = Date.now() + 1500, changed = false;
  while (Date.now() < end) {
    var tb = await chrome.tabs.get(tabId).catch(function () { return null; });
    if (!tb) throw new Error('tab closed');
    var u = tb.pendingUrl || tb.url || '';
    if (u && u !== before) { changed = true; break; }
    await sleep(100);
  }
  await sleep(150); await waitForLoad(tabId);
  await waitQuiet(tabId, 1500);
  return changed;
}
function addStep(t, step) {
  var last = t.steps[t.steps.length - 1];
  if (step.action === 'fill' && last && last.action === 'fill' && last.loc && step.loc && last.loc.css === step.loc.css && last.url === step.url) {
    t.steps[t.steps.length - 1] = step; return;          // the same field typed again: keep the last value (recorder merge)
  }
  t.steps.push(step);
}

/** run one validated-or-to-validate action. approved = the user clicked Approve for exactly this action. */
async function tellExecute(t, cfg, act, ctx, approved) {
  var k = ctx.k;
  if (act.verb === 'done') {
    markValid(t, ctx);
    return tellDone(t, cfg, act.text);
  }
  if (act.verb === 'needs_user') {
    markValid(t, ctx, { needs_user: act.text });
    t.pendingHistRaw = k + '. ' + ctx.show + ' -> the owner answered; continue with the goal.';
    return tellPause(t, 'needs_user', { kind: 'agent', message: 'The agent needs you: ' + (act.text || 'no reason given')
      + '. Do it in the page or type a correction below, then press Continue.' });
  }
  var v = AG.validate(act, ctx.obs);
  if (v.error) { tellReject(t, ctx, v.error); if (!(await tellInvalid(t))) await saveTell(t); return; }
  if (v.secret) return tellSecretPause(t, ctx, v.secret.sensitive, v.secret.name);
  var tabUrl = ((await chrome.tabs.get(t.tabId).catch(function () { return {}; })).url) || '';
  // loop guard: the same click on the same page a 3rd time in a row (a 4B model toggling one checkbox for all 25
  // steps on a real run) pauses and asks instead of using up the step budget. micro_agent.py never counts clicks as
  // duplicates (toggles are legitimate), so two in a row still pass.
  // any action, not only clicks: a real run repeated the same fill_enter 22 times on a page that ignored it
  var rk = v.control && /^(click|fill|fill_enter|select)$/.test(act.verb)
    ? [tabUrl, act.verb, v.control.name || '', act.value == null ? '' : act.value, act.option || ''].join('|') : '';
  t.repeat = rk && t.repeatKey === rk ? (t.repeat || 1) + 1 : 1;
  t.repeatKey = rk;
  if (rk && t.repeat >= 3) {
    t.repeat = 0; t.repeatKey = '';
    tellReject(t, ctx, 'the same action on the same page a third time in a row; it is not moving the goal forward');
    return tellPause(t, 'needs_user', { kind: 'loop', message: 'The agent keeps repeating ' + ctx.show + ' on "' + (v.control.name || 'the same control')
      + '" without getting closer to the goal. Type a correction below and press Continue, or Stop.' });
  }
  // safety gate, before anything is sent to the page
  var gateName = '', danger = null;
  if (act.verb === 'goto') { danger = S.urlDanger(act.value); gateName = act.value; }
  else if (act.verb === 'click' || act.verb === 'fill_enter' || (act.verb === 'select' && isRadio(v.control))) {
    var c = v.control, txt = (c.name || '') + ' | ' + (c.context || '');
    var dm = /\b(delete|remove|revoke|rotate|regenerate|roll|reset|wipe|destroy|erase|purge|deactivate|disable|empty|permanently|discard|clear all|terminate|suspend|upgrade|downgrade|purchase|buy|pay|transfer|close account|cancel (plan|subscription)|sign out|log ?out|leave)\b/i.exec(txt);
    danger = S.dangerMatchStrict(act.verb === 'fill_enter' ? '' : txt, 'tell') || (dm && act.verb !== 'fill_enter' ? dm[1].toLowerCase() : null);
    gateName = c.name || '';
  }
  if (danger && !approved) return tellAskApproval(t, act, ctx, danger, gateName);

  if (act.verb === 'scroll') {
    var n = ctx.nControls;
    t.window = Math.max(0, t.window + (act.direction === 'down' ? AG.WINDOW : -AG.WINDOW));
    if (t.window >= n) t.window = Math.max(0, n - AG.WINDOW);
    await sendTab(t.tabId, { type: 'agent_act', act: act }).catch(function () {});
    return tellOk(t, ctx);
  }
  if (act.verb === 'goto' || act.verb === 'go_back') {
    if (act.verb === 'goto') await chrome.tabs.update(t.tabId, { url: act.value });
    else { try { await chrome.tabs.goBack(t.tabId); } catch (e) { tellReject(t, ctx, 'go_back failed: ' + (e.message || e)); return saveTell(t); } }
    await tellSettle(t.tabId, tabUrl).catch(function () {});
    var now = ((await chrome.tabs.get(t.tabId).catch(function () { return {}; })).url) || act.value;
    addStep(t, { action: 'goto', url: now });
    t.window = 0;
    return tellOk(t, ctx);
  }
  var r = await sendTab(t.tabId, { type: 'agent_act', act: act, approved: approved, expectName: ctx.expectName })
    .catch(function (e) { return { ok: false, error: 'the page did not answer (' + (e.message || e) + ')' }; });
  if (!r || !r.ok) {
    if (r && r.needsApproval && !approved) return tellAskApproval(t, act, ctx, r.danger, ctx.expectName || '');
    if (r && r.secret) return tellSecretPause(t, ctx, r.secret, ctx.expectName || '');
    tellReject(t, ctx, (r && r.error) || 'no reply from the page');
    if (!(await tellInvalid(t))) await saveTell(t);
    return;
  }
  if (approved) t.traj.events.push({ step: k, approved: true, control: ctx.expectName || '' });
  if (act.verb === 'fill') { addStep(t, { action: 'fill', loc: r.loc, url: tabUrl, value: act.value, sensitive: null }); await sleep(150); }
  else if (act.verb === 'select' && !r.asClick) { addStep(t, { action: 'select', loc: r.loc, url: tabUrl, value: r.value, optionText: r.optionText }); await sleep(150); }
  else if (act.verb === 'fill_enter') {
    addStep(t, { action: 'fill', loc: r.loc, url: tabUrl, value: act.value, sensitive: null });
    var press = { action: 'press', key: 'Enter', loc: r.loc, url: tabUrl, danger: r.recDanger || null };
    if (await tellSettle(t.tabId, tabUrl)) press.navigates = true;
    t.steps.push(press); t.window = 0;
  } else {
    var click = { action: 'click', loc: r.loc, url: tabUrl, scrollY: 0, danger: r.recDanger || null };
    if (await tellSettle(t.tabId, tabUrl)) { click.navigates = true; t.window = 0; }
    t.steps.push(click);
  }
  return tellOk(t, ctx);
}
/** the agent wanted to type into a password / code field: the user types it, the agent waits. */
function tellSecretPause(t, ctx, reason, name) {
  markValid(t, ctx, { secret: true });
  t.pendingHistRaw = ctx.k + '. ' + ctx.show + ' -> SKIPPED: the owner types passwords and codes themselves; continue with the goal.';
  return tellPause(t, 'needs_user', { kind: 'secret', message: 'Type the ' + (reason || 'password / code') + ' for "' + name
    + '" into the page yourself (AWOS never reads or stores it), then press Continue.' });
}
async function tellOk(t, ctx) {
  markValid(t, ctx);
  t.invalid = 0;
  t.pendingHist = { step: ctx.k, show: ctx.show };
  await saveTell(t);
}
async function tellAskApproval(t, act, ctx, danger, target) {
  t.traj.events.push({ step: ctx.k, action: ctx.show, approval_asked: danger });
  return tellPause(t, 'awaiting_approval', { kind: 'approval', act: act, ctx: { k: ctx.k, show: ctx.show, obs: ctx.obs, url: ctx.url, expectName: ctx.expectName, nControls: ctx.nControls },
    danger: danger, target: target,
    message: 'Step ' + ctx.k + ' wants to ' + (act.verb === 'goto' ? 'open ' + act.value : act.verb === 'fill_enter' ? 'press Enter in "' + target + '"' : 'click "' + target + '"')
           + ' (matches "' + danger + '"). Approve, or tell it no.' });
}

// ---- done -> verify -> the user's confirmation ---------------------------------------------------------------
function proposeText(o) {
  var pick = (o.statuses || []).concat(o.alerts || []).find(function (s) { return s && s.length <= 120; })
          || (o.headings || []).find(function (s) { return s && s.length <= 120; }) || '';
  return S.norm(pick);
}
async function tellDone(t, cfg, answer) {
  stopClock(t);
  t.status = 'confirm';
  await waitQuiet(t.tabId, 1500);
  var raw = await sendRetry(t.tabId, { type: 'agent_observe' }).catch(function () { return null; });
  var url = ((await chrome.tabs.get(t.tabId).catch(function () { return {}; })).url) || '';
  var u = {}; try { u = new URL(url); } catch (e) {}
  var proposed = { urlPattern: u.origin ? u.origin + u.pathname : '', texts: raw ? [proposeText(raw)].filter(Boolean) : [] };
  var verifier = null;
  if (raw && cfg.enabled !== false && cfg.verify !== false) {
    var vcfg = await brainSettings();
    if (vcfg.brain === 'claude' && cloudLockedFor(cfg, url)) vcfg.brain = 'local';
    var acts = t.traj.steps.filter(function (s) { return s.valid && s.action; }).map(function (s) { return { action: s.action }; });
    verifier = await M.askVerify(vcfg, M.buildVerifyMessages(t.goal, acts, M.outline(raw, { url: R.stripUrl(url), checks: [] }), 'done(' + AG.pyJson(answer) + ')'));
  }
  t.result = { answer: answer, url: url, proposed: proposed, verifier: verifier };
  t.pending = { nonce: nonce(), kind: 'confirm', message: 'Done? Check the page, then confirm.' };
  feed(t, 'done', 'The agent says it is done' + (answer ? ': ' + answer : '') + (verifier && verifier.label ? ' | verifier: ' + verifier.label + (verifier.reason ? ' (' + verifier.reason + ')' : '') : ''));
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: 'Tell it finished. Confirm in the AWOS popup.' }).catch(function () {});
}

async function tellConfirm(msg) {
  var t = await getSess('tell');
  if (!t || t.status !== 'confirm' || !t.pending || t.pending.nonce !== msg.nonce) throw new Error('nothing to confirm');
  if (!msg.yes) {
    t.outcome = 'completed_unverified'; t.reason = 'the owner said it is not done';
    feed(t, 'end', 'Not saved: you said it is not done.');
    t.status = 'ended'; t.pending = null;
    await keepTrajectory(t, false); await saveTell(t);
    await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
    return { saved: false };
  }
  var ex = msg.expect || t.result.proposed;
  var rec = { id: uid('rec'), name: String(msg.name || t.goal).slice(0, 120), createdAt: Date.now(), startUrl: t.startUrl,
              steps: t.steps, goal: String(msg.goal || t.goal).slice(0, 300),
              expect: { urlPattern: String(ex.urlPattern || ''), texts: (ex.texts || []).map(String).filter(Boolean) },
              createdBy: 'tell-it', tellId: t.id };
  if (!actionCount(rec)) rec.warning = NO_ACTIONS;
  await putRec(rec);
  t.savedRecId = rec.id; t.outcome = 'verified'; t.reason = 'the owner confirmed';
  t.status = 'ended'; t.pending = null;
  feed(t, 'end', 'Saved as the recipe "' + rec.name + '". It now replays like any recording.');
  await keepTrajectory(t, true); await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
  return rec;
}

/** the user's answer to a pause (only from extension pages; nonce-bound like replay approvals). */
async function tellDecide(kind, msg) {
  var t = await getSess('tell');
  if (!t || !t.pending || t.pending.nonce !== msg.nonce) throw new Error('stale or unknown request');
  var p = t.pending;
  if (kind === 'approve') {
    if (t.status !== 'awaiting_approval') throw new Error('nothing to approve');
    t.resumeAct = { act: p.act, ctx: p.ctx };
    feed(t, 'info', 'You approved "' + (p.target || '') + '".');
  } else if (kind === 'decline') {
    if (t.status !== 'awaiting_approval') throw new Error('nothing to decline');
    tellReject(t, p.ctx, 'the owner did not approve this');
  } else if (kind === 'continue') {
    if (t.status !== 'needs_user') throw new Error('nothing to continue');
    if (p.kind === 'guard') t.guardSkip[p.guardKey] = 1;
    if (p.kind === 'guard' || p.kind === 'secret') await recordTypedSecrets(t);
    if (t.pendingHistRaw) { t.history.push(t.pendingHistRaw); t.pendingHistRaw = null; }
    if (p.kind === 'invalid') t.invalid = 0;
  } else throw new Error('unknown decision');
  if (msg.note) t.note = String(msg.note).slice(0, 300);
  t.status = 'running'; t.pending = null; t.segStart = Date.now();
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
  tellDrive(t.id);
  return { ok: true };
}

/** fields the user typed a secret into become {{secret:...}} steps (replay asks the user again; no value stored). */
async function recordTypedSecrets(t) {
  var r = await sendRetry(t.tabId, { type: 'agent_secrets' }).catch(function () { return null; });
  var url = ((await chrome.tabs.get(t.tabId).catch(function () { return {}; })).url) || '';
  ((r && r.fields) || []).forEach(function (f) {
    var dup = t.steps.some(function (s) { return s.action === 'fill' && s.sensitive && s.loc && s.loc.css === f.loc.css && s.url === url; });
    if (dup) return;
    t.steps.push({ action: 'fill', loc: f.loc, url: url, value: S.placeholderFor(f.reason), sensitive: f.reason });
    feed(t, 'info', 'You typed the ' + f.reason + ' for "' + (f.loc.name || f.loc.label || 'a field') + '"; the recipe will ask you each run.');
  });
}

// ---- take over: the user drives by hand, the recorder captures it into the same recording ----------------------
async function tellTakeover() {
  var t = await getSess('tell');
  if (!t || !/^(running|paused|needs_user|awaiting_approval)$/.test(t.status)) throw new Error('no Tell-it run to take over');
  t.takeover = true; t.lastClickAt = 0;
  if (t.status === 'running') t.pauseRequested = true;
  else { stopClock(t); t.status = 'paused'; t.pending = null; }
  feed(t, 'info', 'You have the page: what you click and type is recorded into the recipe. Press Resume to hand it back.');
  await saveTell(t);
  await sendTab(t.tabId, { type: 'rec_state', on: true }).catch(function () {});
  return t;
}
async function tellResume() {
  var t = await getSess('tell');
  if (!t || t.status !== 'paused') throw new Error('nothing to resume');
  if (t.takeover) {
    t.takeover = false;
    await sendTab(t.tabId, { type: 'rec_state', on: false }).catch(function () {});
    t.history.push(t.step + '. (the owner acted on the page by hand; continue from this page)');
    t.pendingHist = null;
  }
  t.status = 'running'; t.segStart = Date.now();
  feed(t, 'info', 'Resumed.');
  await saveTell(t);
  tellDrive(t.id);
  return t;
}
async function tellRecStep(tabId, msg) {
  var t = await getSess('tell');
  if (!t || !t.takeover || t.tabId !== tabId) return false;
  var last = t.steps[t.steps.length - 1];
  if (msg.merge && last && last.action === 'fill' && last.mergeKey === msg.mergeKey) {
    last.value = msg.step.value; last.sensitive = msg.step.sensitive; last.loc = msg.step.loc;
  } else { var s = msg.step; if (s.action === 'fill') s.mergeKey = msg.mergeKey; t.steps.push(s); }
  if (msg.step.action === 'click' || msg.step.action === 'press') t.lastClickAt = Date.now();
  await setSess('tell', t);
  return true;
}

async function exportTraining() {
  return { kind: 'awos-tellit-trajectories/1', exportedAt: Date.now(), version: chrome.runtime.getManifest().version,
           note: 'Tell-it episodes in the micro-agent Trajectory format (scaffold/rl/contract.py). Contains the page text the agent saw.',
           episodes: await getLocal('tellTraj', []) };
}

// ---- wiring ---------------------------------------------------------------------------------------------------
(function () {
  var recStep = HANDLERS.rec_step, hello = HANDLERS.hello;
  var tellQueue = Promise.resolve();
  HANDLERS.rec_step = async function (msg, sender) {
    if (sender.tab) {
      var mine = false;
      tellQueue = tellQueue.then(async function () { mine = await tellRecStep(sender.tab.id, msg); }).catch(function () {});
      await tellQueue;
      if (mine) return { ok: true };
    }
    return recStep(msg, sender);
  };
  HANDLERS.hello = async function (msg, sender) {
    var r = await hello(msg, sender), t = await getSess('tell');
    if (t && t.takeover && sender.tab && t.tabId === sender.tab.id) r.recording = true;
    return r;
  };
  chrome.tabs.onUpdated.addListener(function (tabId, info) {
    if (!info.url) return;
    tellQueue = tellQueue.then(async function () {
      var t = await getSess('tell');
      if (!t || !t.takeover || t.tabId !== tabId) return;
      var last = t.steps[t.steps.length - 1];
      if (last && last.action === 'goto' && sameUrl(last.url, info.url)) return;
      if (Date.now() - (t.lastClickAt || 0) < 3000 && last && (last.action === 'click' || last.action === 'press')) last.navigates = true;
      else t.steps.push({ action: 'goto', url: info.url });
      await setSess('tell', t);
    }).catch(function () {});
  });
  Object.assign(HANDLERS, {
    tell_start: async function (msg) { return tellStart(msg); },
    tell_state: async function () { return { tell: await getSess('tell'), settings: await tellSettings() }; },
    tell_pause: async function () {
      var t = await getSess('tell'); if (!t || t.status !== 'running') throw new Error('not running');
      t.pauseRequested = true; await saveTell(t); return { ok: true };
    },
    tell_resume: async function () { return tellResume(); },
    tell_takeover: async function () { return tellTakeover(); },
    tell_stop: async function () {
      var t = await getSess('tell'); if (!t || t.status === 'ended') throw new Error('no Tell-it run');
      if (t.takeover) { t.takeover = false; await sendTab(t.tabId, { type: 'rec_state', on: false }).catch(function () {}); }
      await tellEnd(t, 'halted', 'stopped by the owner'); return { ok: true };
    },
    tell_note: async function (msg) {
      var t = await getSess('tell'); if (!t || t.status === 'ended') throw new Error('no Tell-it run');
      t.note = String(msg.text || '').trim().slice(0, 300);
      feed(t, 'info', 'Your correction goes into the next step: ' + t.note);
      await saveTell(t); return { ok: true };
    },
    tell_approve: async function (msg) { return tellDecide('approve', msg); },
    tell_decline: async function (msg) { return tellDecide('decline', msg); },
    tell_continue: async function (msg) { return tellDecide('continue', msg); },
    tell_confirm: async function (msg) { return tellConfirm(msg); },
    tell_clear: async function () { var t = await getSess('tell'); if (t && t.status !== 'ended') throw new Error('stop the run first'); await setSess('tell', null); return { ok: true }; },
    tell_export_training: async function () { return exportTraining(); },
    agent_status: async function () {
      var cfg = await tellSettings();
      if (cfg.agentBrain === 'claude') return { brain: 'claude', ok: true, error: '' };
      var why = await checkLocalAgent(cfg); return { brain: 'local', ok: !why, error: why };
    },
    tell_delete_training: async function () { await setLocal('tellTraj', []); return { ok: true }; },
    set_site_lock: async function (msg) {
      var site = R.siteOf(msg.url || '') || String(msg.site || '').toLowerCase().trim();
      if (!site) throw new Error('no site');
      var s = await getLocal('settings', {}), list = (s.noCloudSites || []).filter(function (x) { return x !== site; });
      if (msg.on !== false) list.push(site);
      s.noCloudSites = list; await setLocal('settings', s); return { site: site, locked: msg.on !== false, noCloudSites: list };
    },
    set_recipe_cloud_lock: async function (msg) {
      var rec = await getRec(msg.id); if (!rec) throw new Error('no such recording');
      rec.noCloud = msg.on !== false; await putRec(rec); return rec;
    },
  });
})();

// a worker restart mid-step: the last action may have half-run, so the run ends (paused runs stay paused).
(async function tellOnWorkerStart() {
  var t = await getSess('tell');
  if (t && t.status === 'running' && tellDriving !== t.id) await tellEnd(t, 'halted', 'the extension restarted during step ' + t.step + '; check the page');
})();
