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
 * v0.10 (settings.pageView, default on): the prompt is AWOSAgent.buildPromptV2 (the page as typed entities, goal-ranked,
 * capped at 40, grammar = only the shown numbers), with "Known paths" from the site map (chrome.storage.local
 * 'siteMap', learned from recordings and from each action's effect), plan-then-act (one plan call at the start when a
 * known route or a multi-hop goal; one replan after two clicks that changed nothing) and clicks that changed nothing
 * set aside for that page state. traj.promptVersion says which prompt an episode used. Lessons: background.js
 * tellRulesText(t, host, stateKey) fills the "Rules learned on this site" block (2 rules at most, ids kept in
 * t.lessonIds and scored when the episode ends); a correction (tell_note, or a note with Continue) is offered to the
 * popup as "Remember for <site>?" (offerLesson).
 * v0.11 steering (a real reddit.com episode forgot two corrections, re-proposed a declined "Post" three times and typed
 * its own scratch text into the body): t.notes keeps every owner note of the episode (5 at most, latest last) and each
 * v2 prompt shows them as "Owner's instructions"; a declined approval (No, or a note typed while it waits) is kept in
 * t.declined: that control is not shown again in that page state and a reply that still picks it is rejected without
 * asking again (a later note that names it, without "don't", lifts it); fill / fill_enter / select values that carry the
 * model's own scratch text, copy the goal into a body field, or are empty are rejected before anything is typed.
 * v0.11 draft-and-approve writing (settings.agentDraft, default on): a fill / fill_enter into a COMPOSE field (a post,
 * comment or message body, or its title: AWOSAgent.composeField) on a goal that composes is never typed from the action
 * call (200 characters at most, written between control numbers): a separate WRITER call drafts it from the goal, every
 * owner note, the field, where it is and the other fields' text (junk-checked, retried once); the episode waits in
 * 'awaiting_draft' and the popup shows the draft, editable, with Use this text / Rewrite (an optional note: another
 * writer call) / Stop. Only the text the owner approved is typed; the Post / Send click still needs its Approve.
 * traj.drafts keeps every writer call, the draft, the final text and whether the owner edited it (run log: tellLogEnd).
 * v0.12 loop upgrades (docs/research/raising_the_agent_2026-11-05.md, "next loop upgrades"):
 *   U1 a fill / fill_enter / select is read back from the page (content agent_act readback; sensitive fields never) and
 *      its history line says what the field holds: '7. fill(17) "Body" -> field now: "Local AI for…" (212 chars)'.
 *      Verdicts: ok / no visible effect / REFUSED BY OWNER / rejected: <why>. The owner's lines (refusals, rejected
 *      declined picks, answers, take-overs: t.pinned) stay in the history block after they leave the 6-line window.
 *   U2 before asking the owner to approve a risky submit (post / send / publish / submit / reply / save ...) on a compose
 *      page: ONE validator call (VALIDATOR_GRAMMAR 'yes' | 'no: ...', thinking off, temperature 0) with the goal, the
 *      owner's instructions and the form's field values read back from the page (agent_fields, masked). 'no' -> no
 *      approval asked: "Not ready: <why>" in the history (counts toward the invalid cap); 'yes' (or no verdict) -> the
 *      Approve card shows the values (pending.fields). traj.checks keeps every check.
 *   U3 note gate: after a new owner note (t.noteGate) the risky submit controls are left out of the prompt and grammar
 *      (and refused if picked) until a non-risky action ran; a note about the text (body / title / ask / rewrite ...)
 *      also needs a newly approved draft (drafts on, compose goal).
 *   U4 a note, Pause, Take over or Stop while the action call is in flight aborts that request (AbortController in
 *      tellChat) and bumps t.cancelSeq: the stale reply is dropped and the step is asked again (with the note).
 *   U5 the v2 grammar has no backslash in strings (AWOSAgent.STR_V2_RULE, decideV2); typographic quotes in a v2 fill
 *      value become plain ones before typing. The legacy grammar is unchanged.
 *   U6 (settings.agentClarify, default on): a writing goal that does not say what the text should say or ask pauses once
 *      at the start with an editable "What should it say or ask?" line pre-filled with the model's guess (one small
 *      call); the answer is the first owner instruction; Skip goes on without one.
 */
var AG = self.AWOSAgent;
var TELL_MAX_TRAJ = 20;
var TELL_DEFAULTS = { agentBrain: 'local', agentEndpoint: 'http://127.0.0.1:8081/v1', agentModel: 'qwen3.5-4b',
                      agentClaudeModel: 'claude-haiku-5-5', noCloudSites: [], keepTraining: true,
                      searchPage: 'https://duckduckgo.com/?q={goal}',
                      pageView: true,          // v0.10: the page as typed entities + site map + plan (false = the v0.5 prompt)
                      agentPlan: true,         // v0.10: one plan call (2-5 subgoals) when a known path or a multi-step goal
                      agentDraft: true,        // v0.11: compose fields are drafted by a writer call and approved by the owner
                      agentClarify: true };    // v0.12: a vague writing goal asks "What should it say or ask?" once at the start
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
  var b = /^(awaiting_approval|awaiting_draft|needs_user|confirm)$/.test(t.status) ? '!' : t.status === 'running' ? '>' : '';
  chrome.action.setBadgeText({ text: b }).catch(function () {});
}
function stopClock(t) { t.activeMs = elapsed(t); t.segStart = 0; }

async function tellPause(t, status, pending) {
  stopClock(t);
  t.status = status; t.pending = Object.assign({ nonce: nonce(), step: t.step }, pending);
  feed(t, status === 'awaiting_approval' || status === 'awaiting_draft' ? 'approve' : 'wait', pending.message);
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: pending.message + ' (open the AWOS popup)' }).catch(function () {});
}

/** the episode ends (any outcome but the user's confirmation): keep the trajectory, clear the banner.
 *  byModel: the model ran out (step / time cap) - only then is it evidence against the site map path and the rules it
 *  was told (v0.10 review). A stop by the owner, a worker restart or a thrown error teaches nothing: not scored. */
async function tellEnd(t, outcome, reason, byModel) {
  stopClock(t);
  t.status = 'ended'; t.outcome = outcome; t.reason = reason; t.pending = null;
  feed(t, 'end', outcome + (reason ? ': ' + reason : ''));
  if (outcome === 'halted' && byModel) {
    await tellMapOutcome(t, false);                                // v0.10: the path it took did not lead to the goal
    await tellLessonsEnd(t, 'halted');                             // v0.10: the rules it was told
  } else if (outcome !== 'halted') await tellLessonsEnd(t, String(outcome || ''));
  await keepTrajectory(t, null); await tellLogEnd(t);
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
}

/** v0.9.7: every ended episode (stopped, failed, confirmed or not) goes into the run log, model replies included, and
 *  the log is saved like a replay's (autoSaveLog). Local only; the page text the model saw is not kept here. */
var TELL_LOG_MAX = 20;
async function tellLogEnd(t) {
  if (t.logged) return;
  t.logged = true;
  var entry = { id: t.id, goal: redactText(t.goal), brain: t.brain, model: t.model, startedAt: t.startedAt, endedAt: Date.now(),
    outcome: t.outcome || (t.status === 'ended' ? 'ended' : t.status), reason: redactText(t.reason || ''), steps: t.step,
    recipeCheck: t.recipeCheck || null, savedRecipe: t.savedRecId || '',
    promptVersion: t.traj.promptVersion || 'v1', plan: t.plan ? t.plan.steps.map(redactText) : null,     // v0.10
    lessons: (t.lessonIds || []).slice(),                                                                   // v0.10: ids told
    // v0.11 steering (review: notes and texts the owner typed are masked like prompts: a pasted key never reaches the log)
    notes: (t.notes || []).map(function (n) { return { step: n.step, text: redactText(AG.maskSecrets(n.text)) }; }),
    declined: (t.declined || []).map(function (d) { return { step: d.step, verb: d.verb, role: d.role, name: redactText(d.name || ''), url: redactUrl(d.url || '') }; }),
    drafts: (t.traj.drafts || []).map(function (d) {                                                          // v0.11 drafts
      return { step: d.step, field: redactText(d.field || ''), kind: d.kind, writes: (d.writes || []).length, rewrites: d.rewrites || 0,
               draft: redactText(AG.maskSecrets(String(d.draft || '').slice(0, 1500))),
               final: d.final == null ? null : redactText(AG.maskSecrets(String(d.final).slice(0, 1500))),
               edited: !!d.edited, outcome: d.outcome || 'not used' };
    }),
    checks: (t.traj.checks || []).map(function (c) {                                                       // v0.12 (U2)
      return { step: c.step, control: redactText(c.control || ''), verdict: c.verdict, why: redactText(c.why || ''), ms: c.ms, error: c.error || '' };
    }),
    clarify: t.traj.clarify ? { guess: redactText(AG.maskSecrets(t.traj.clarify.guess || '')), answer: redactText(t.traj.clarify.answer || ''),
                                skipped: !!t.traj.clarify.skipped } : null,                                // v0.12 (U6)
    model_steps: (t.traj.steps || []).map(function (s) {
      return { i: s.i, url: redactUrl(s.url), ms: s.ms, reply: redactText(String(s.reply || '').slice(0, 800)), action: s.action || '',
               valid: !!s.valid, repaired: !!s.repaired, note: s.note ? redactText(String(s.note).slice(0, 300)) : '' };
    }),
    events: (t.traj.events || []).slice(-40),
    feed: (t.feed || []).map(function (f) { return { t: f.t, kind: f.kind, k: f.k, text: redactText(f.text), reason: f.reason ? redactText(f.reason) : '' }; }) };
  var all = await getLocal('tellLog', []);
  all.unshift(entry); await setLocal('tellLog', all.slice(0, TELL_LOG_MAX));
  await autoSaveLog({ result: { verdict: 'tellit-' + entry.outcome } }).catch(function (e) { console.warn('auto log', e); });
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

// ---- v0.10: the site map (lib/sitemap.js): what clicks did on each site, learned by watching ------------------------
// chrome.storage.local 'siteMap' = {hosts: {host: {edges, states}}}: per site, which control in which state opened which
// menu / window / tab, went where or downloaded. Fed by the user's recordings ('seen': their own demonstration,
// merged once per recording: 'siteMapRecs'), by every Tell-it action and imported recipe ('unproven': stored, but no
// route or prompt line until proven), by a confirmed episode ('ok' for its whole path) and by a failed one ('fail' for
// every edge of it). Only roles, accessible names, contexts and generic item
// words are kept (lib/sitemap.js merge): never a value, a row's text or a file name. Local only.
var SM = self.AWOSSiteMap, PV = self.AWOSPageView;
function recHost(rec) { return SM.hostOf((rec && (rec.startUrl || ((rec.steps || [])[0] || {}).url)) || ''); }
async function siteMapLoad(host) {
  var map = await getLocal('siteMap', { hosts: {} });
  if (!host) return map;
  var recs = await getLocal('recordings', {}), done = await getLocal('siteMapRecs', {}), add = [];
  Object.keys(recs).forEach(function (id) { if (!done[id] && recHost(recs[id]) === host) add.push(id); });
  if (!add.length) return map;
  // v0.10: a recording is the user's own path but not yet a proof ('seen'); one whose last replay was VERIFIED is 'ok'
  // (recordings made since 0.10 were merged when they were saved: background.js siteMapFromRecording)
  add.forEach(function (id) {
    var lr = recs[id].lastRun;
    map = SM.merge(map, SM.fromRecording(recs[id]), lr && lr.verdict === 'VERIFIED' ? 'ok' : recs[id].imported ? 'unproven' : 'seen', Date.now()); done[id] = 1;
  });
  await setLocal('siteMap', map); await setLocal('siteMapRecs', done);
  return map;
}
async function siteMapMerge(edges, outcome) {
  if (!edges || !edges.length) return;
  await setLocal('siteMap', SM.merge(await getLocal('siteMap', { hosts: {} }), edges, outcome, Date.now()));
}
/** what effectOf needs of an observation: no field values (only tab / checkbox state), no page text (a hash of it). */
function slimObs(o, ents) {
  var txt = (o.alerts || []).concat(o.statuses || [], [o.dialog || '', o.text || '']).join('\n'), h = 5381;
  for (var i = 0; i < txt.length; i++) h = ((h << 5) + h + txt.charCodeAt(i)) | 0;
  return { url: o.url, dialog: o.dialog || '', dialogName: o.dialogName || '', sig: h,
           controls: (o.controls || []).map(function (c) {
             var x = { role: c.role, name: c.name };
             if (c.role === 'tab' || c.role === 'checkbox') x.value = c.value;
             if (c.inDialog) x.inDialog = true;
             if (c.row) x.row = c.row;
             return x;
           }),
           entities: (ents || []).map(function (e) { return { type: e.type, label: e.label || '', count: e.count || e.items.length }; }) };
}
/** the state the page is in: its key, with the menu an earlier click opened while that menu still shows. */
function tellStateKey(t, obs) {
  var k = SM.stateKey(obs), mk = t.menuKey || '';
  var menuOpen = (obs.controls || []).some(function (c) { return /^menuitem/.test(c.role || ''); });
  return mk && menuOpen && mk.split('|menu:')[0] === k ? mk : k;
}
var EFFECT_TEXT = {
  opened_menu: function (l) { return 'it opened the menu of "' + l + '"; if nothing in it fits the goal, click "' + l + '" again to close it'; },
  opened_dialog: function (l) { return 'it opened the "' + l + '" window'; },
  closed_dialog: function (l) { return 'it closed the "' + l + '" window'; },
  switched_tab: function (l) { return 'it switched to the "' + l + '" tab'; },
  navigated: function (l) { return 'it went to ' + l; },
  downloaded: function (l) { return 'a ' + (l || 'file') + ' file downloaded'; },
  new_tab: function () { return 'it opened a new tab'; },
  showed_items: function (l) { return 'it showed more ' + (l || 'list') + ' rows'; },
};
var PLAN_GENERIC = { click: 1, tab: 1, window: 1, button: 1, link: 1, page: 1, select: 1, choos: 1, choose: 1, then: 1, it: 1 };
/** the plan moves on when an action's effect names its current subgoal (or a later one: the model went ahead). */
function planAdvance(t, la, eff) {
  var pl = t.plan;
  if (!pl || pl.at >= pl.steps.length) return false;
  var cw = SM.words([la.name, eff.label, la.items || ''].join(' '));
  function fits(sg) {
    var w = SM.words(sg).filter(function (x) { return !PLAN_GENERIC[x]; });
    return w.some(function (x) { return cw.indexOf(x) >= 0; }) || (eff.kind === 'opened_menu' && /\b(menu|profile|account|avatar)\b/i.test(sg))
      || (eff.kind === 'downloaded' && /\bdownload/i.test(sg));
  }
  for (var j = pl.steps.length - 1; j >= pl.at; j--) {
    if (fits(pl.steps[j])) { pl.at = j + 1; return true; }
  }
  return false;
}
/** the effect of the last action, now that the page settled: learned into the site map, told to the model.
 *  -> the phrase for its history line ('' when nothing is known). */
async function tellEffect(t, obs, ents) {
  var la = t.lastAct;
  t.lastAct = null;
  if (!la || !la.before) return '';
  var dl = null, nt = false;
  if (la.verb !== 'fill_enter') {
    dl = ((await chrome.downloads.search({ orderBy: ['-startTime'], limit: 5 }).catch(function () { return []; })) || [])
      .filter(function (d) { return d.id > (la.dlMark || 0); })[0] || null;
    nt = ((await chrome.tabs.query({}).catch(function () { return []; })) || [])
      .some(function (x) { return (la.tabsBefore || []).indexOf(x.id) < 0 && x.openerTabId === t.tabId; });
  }
  var after = slimObs(obs, ents);
  var eff = SM.effectOf(la.before, after, { role: la.role, name: la.name, download: dl ? { name: String(dl.filename || '').split(/[\\/]/).pop() } : null, newTab: nt });
  t.menuKey = eff.kind === 'opened_menu' ? eff.to : '';
  t.traj.events.push({ step: la.step, effect: eff.kind, label: SM.clean(eff.label, 40) });
  if (eff.kind === 'nothing') {
    var noHash = function (u) { return String(u || '').split('#')[0]; };    // a bare #anchor is no change (a #/route changes the text)
    var changed = la.before.sig !== after.sig || noHash(la.before.url) !== noHash(after.url);
    if (la.verb === 'click' && !changed && !/^(checkbox|radiogroup|switch|textbox|combobox|password)$/.test(la.role || '')) {
      var tr = t.tried[la.fromKey] = (t.tried[la.fromKey] || []);
      if (!tr.some(function (x) { return x.role === la.role && x.name === la.name; })) tr.push({ role: la.role, name: la.name });
      if (tr.length > 12) tr.shift();
      t.nothing = (t.nothing || 0) + 1;
      return NOTHING_EFFECT;
    }
    t.nothing = 0;
    return '';
  }
  t.nothing = 0;
  var edge = { host: SM.hostOf(la.url), from: la.fromKey, to: eff.to, effect: { kind: eff.kind, label: eff.label },
               control: { role: la.role, name: la.name, context: la.context || '', row: !!la.row, items: la.items || '' } };
  t.edges.push(edge);
  if (t.edges.length > 40) t.edges.shift();
  await siteMapMerge([edge], 'unproven');          // the model's own click: kept, but no route until the user confirms
  if (planAdvance(t, la, eff)) feed(t, 'info', t.plan.at < t.plan.steps.length ? 'Plan: now "' + t.plan.steps[t.plan.at] + '"' : 'Plan: every subgoal reached');
  var f = EFFECT_TEXT[eff.kind];
  return f ? f(SM.clean(eff.label, 50).replace(/"/g, "'")) : '';
}
/** the episode's path into the site map: the whole path 'ok' once the user confirmed it, every edge of it 'fail' when
 *  it ended without reaching the goal (the user said not done, or the model ran out). Once per episode. */
async function tellMapOutcome(t, ok) {
  if (t.mapDone || !(t.edges || []).length) return;
  t.mapDone = true;
  await siteMapMerge(t.edges, ok ? 'ok' : 'fail').catch(function (e) { console.warn('site map', e); });
}

/** v0.10: a correction typed in Tell it is offered as a lesson for the page's site (background.js offerLesson). */
async function tellOfferLesson(t, text) {
  var url = ((await chrome.tabs.get(t.tabId).catch(function () { return {}; })).url) || '';
  var host = SM.hostOf(url), key = t.lastKey && t.lastKey.split('|')[0] === SM.pagePattern(url) ? t.lastKey : (host ? SM.stateKey({ url: url }) : '');
  return offerLesson('tell', text, { host: host, stateKey: key, goal: t.goal });
}

// ---- v0.11 steering: notes that persist, declines that stick --------------------------------------------------------
var NOTE_NEG = /\b(don'?t|do not|never|not|no|stop|without|wait|before|instead)\b/i;
// v0.11 review: naming the control is not enough ("write a better post body" names "Post"): the note must also say go
// ahead (a verb aimed at it, or ok / now / yes / go ahead / you can), and the name must not be a noun of another field
var NOTE_GO = /\b(click|press|tap|hit|submit|choose|select|open|go ahead|you can|now|ok|okay|yes|fine|approved?)\b/i;
function reEsc(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
/** an owner note joins the episode's notes (5 at most, latest last; "(new)" on the next step). A later note that names
 *  a declined control with a go-ahead and without "don't" / "before" ... ("ok, now click Post") lifts that decline;
 *  the note that came with the decline itself never does. */
function tellAddNote(t, text, withDecline, noGate) {
  text = String(text || '').trim().slice(0, AG.NOTE_CHARS || 300);
  if (!text) return;
  t.notes = (t.notes || []).concat([{ id: (t.noteSeq = (t.noteSeq || 0) + 1), text: text, step: t.step, fresh: true }]).slice(-(AG.NOTES_MAX || 5));
  // v0.12 (U3): the risky submit controls wait until something was done about this note (the clarify answer at the
  // start of a goal gates nothing: there is nothing stale to submit yet)
  // v0.12 review: a plain go-ahead ("yes", "ok, now click Post") asks for no change: it holds nothing back (else the
  // owner's own "click Post" would hide Post until some other action ran)
  var low = text.toLowerCase(), goAhead = !withDecline && (NOTE_GO.test(low) || AG.isSubmitName(low)) && !NOTE_NEG.test(low) && !NOTE_CHANGE.test(low);
  if (!noGate && !goAhead) t.noteGate = { seq: t.noteSeq, step: t.step, text: AG.noteAboutText(text), acted: false, drafted: false };
  if (withDecline || !(t.declined || []).length) return;
  t.declined = t.declined.filter(function (d) {
    var nm = String(d.verb === 'goto' ? d.url : d.name || '').toLowerCase().trim();
    var named = nm.length >= 2 && new RegExp('(^|[^a-z0-9])' + reEsc(nm) + '($|[^a-z0-9])(?!\\s*(body|title|text|content|draft|field|box)\\b)').test(low);
    if (!named || !NOTE_GO.test(low) || NOTE_NEG.test(low)) return true;
    feed(t, 'info', 'Your note names "' + (d.name || d.url) + '": it may be proposed again (it still needs your Approve).');
    return false;
  });
}
// v0.12 review: a note that asks for a change (it gates the submit controls even with an "ok" / "now" in it)
var NOTE_CHANGE = /\b(rewrite|reword|change|edit|fix|make|add|remove|delete|replace|mention|include|shorten|shorter|longer|ask|say|use|put|write|first|instead|but|also|then)\b/i;
// ---- v0.12 (U3) the note gate, (U1) history verdicts and the owner's pinned lines -----------------------------------
var STRICT_DM = /\b(delete|remove|revoke|rotate|regenerate|roll|reset|wipe|destroy|erase|purge|deactivate|disable|empty|permanently|discard|clear all|terminate|suspend|upgrade|downgrade|purchase|buy|pay|transfer|close account|cancel (plan|subscription)|sign out|log ?out|leave)\b/i;
/** the danger word of clicking control c (the safety gate's own test) or null. */
function tellDangerOf(c) {
  var txt = (c.name || '') + ' | ' + (c.context || ''), dm = STRICT_DM.exec(txt);
  return S.dangerMatchStrict(txt, 'tell') || (dm ? dm[1].toLowerCase() : null);
}
/** a control that sends what was written and needs the owner's Approve (Post, Send, Publish, Submit ...). */
function tellRiskySubmit(c) {
  return !!(c && !/^(textbox|password|combobox)$/.test(c.role || '') && AG.isSubmitName(c.name) && tellDangerOf(c));
}
/** is the note gate open? (no new note, or a non-risky action ran since, and a new draft was approved for a note about
 *  the text when drafts are on). Closes nothing; clears the gate once it is open. */
function tellGateOpen(t, cfg) {
  var g = t.noteGate;
  if (!g) return true;
  var needDraft = g.text && cfg.agentDraft !== false && AG.composeGoal(t.goal);
  if (!g.acted || (needDraft && !g.drafted)) return false;
  t.noteGate = null;
  return true;
}
function tellGateWhy(t, cfg) {
  var g = t.noteGate || {}, needDraft = g.text && cfg.agentDraft !== false && AG.composeGoal(t.goal);
  return 'the owner gave a new instruction at step ' + (g.step || t.step) + '; act on it first'
    + (needDraft ? ' (write the text again: fill the field, the owner approves the new draft)' : ' (another action)') + ', then submit';
}
/** a history line; pin = an owner line kept in the history block after it leaves the 6-line window. */
function tellHist(t, line, pin) {
  t.history.push(line);
  if (pin) t.pinned = (t.pinned || []).concat([line]).slice(-8);
}
/** the owner said no to a pending approval: kept for the rest of the episode (verb, control, page state). */
function tellRecordDecline(t, p) {
  var act = p.act || {}, ctx = p.ctx || {}, c = act.n ? ((ctx.obs && ctx.obs.controls) || [])[act.n - 1] : null;
  var d = { verb: act.verb === 'select' ? 'click' : act.verb, role: c ? c.role || '' : '', name: c ? c.name || '' : p.target || '',
            url: act.verb === 'goto' ? String(act.value || '') : '', key: tellCtxKey(ctx), step: ctx.k || t.step };
  t.declined = (t.declined || []).filter(function (x) { return !sameDecline(x, d); }).concat([d]).slice(-8);
  t.traj.events.push({ step: d.step, action: ctx.show || '', declined: true, control: d.name || d.url });
  feed(t, 'info', 'You said no to ' + (d.verb === 'goto' ? 'opening ' + d.url : '"' + d.name + '"') + '; it will not be proposed again in this run.');
}
function tellCtxKey(ctx) { return ctx.fromKey || (ctx.obs ? SM.stateKey(ctx.obs) : ''); }
function sameDecline(a, b) {
  if ((a.verb === 'goto') !== (b.verb === 'goto')) return false;
  if (a.verb === 'goto') return String(a.url).split('#')[0] === String(b.url).split('#')[0];
  return a.verb === b.verb && a.role === b.role && a.name === b.name && a.key === b.key;
}
/** the action the model picked is one the owner declined in this page state -> the rejection, or ''. */
function tellDeclinedHit(t, act, ctx) {
  if (!(t.declined || []).length || !/^(click|select|fill_enter|goto)$/.test(act.verb)) return '';
  var c = act.n ? ((ctx.obs && ctx.obs.controls) || [])[act.n - 1] : null;
  if (act.verb !== 'goto' && !c) return '';
  var me = { verb: act.verb === 'select' ? 'click' : act.verb, role: c ? c.role || '' : '', name: c ? c.name || '' : '',
             url: act.verb === 'goto' ? String(act.value || '') : '', key: tellCtxKey(ctx) };
  var d = t.declined.filter(function (x) { return sameDecline(x, me); })[0];
  return d ? 'the owner declined ' + (d.verb === 'goto' ? 'opening ' + d.url : d.verb + ' "' + d.name + '"') + ' at step ' + d.step
    + '; do not try it again: follow the owner\'s instructions' : '';
}
/** the declined controls of this page state, as control numbers to leave out of the shown list (like 'tried'). */
function tellDeclinedSkip(t, key, obs) {
  var out = [];
  (t.declined || []).forEach(function (d) {
    if (d.verb === 'goto' || d.key !== key) return;
    (obs.controls || []).forEach(function (c, i) { if (c.role === d.role && c.name === d.name && out.indexOf(i + 1) < 0) out.push(i + 1); });
  });
  return out;
}

// ---- v0.10 plan-then-act --------------------------------------------------------------------------------------------
var PLAN_VERBS = /\b(open|go|click|download|find|change|turn|switch|select|search|fill|save|send|export|set|update|look|check|copy|add|remove|cancel|upload)\b/g;
/** a goal that needs more than one hop: "... then ...", or two different action verbs. */
function multiHop(goal) {
  var g = String(goal || '').toLowerCase(), seen = {};
  if (/\b(then|after that|afterwards)\b/.test(g)) return true;
  (g.match(PLAN_VERBS) || []).forEach(function (v) { seen[v] = 1; });
  return Object.keys(seen).length >= 2;
}
/** ONE model call -> t.plan = {steps: [2-5 subgoals], at: 0} (kept in the trajectory; a bad reply = no plan). */
async function tellPlan(t, cfg, obs, paths, again) {
  var user = AG.buildPlanPrompt({ goal: t.goal, obs: obs, paths: paths, history: again ? t.history : null, replan: again && !!t.plan, now: nowMs(),
                                  notes: t.notes, declined: t.declined });                       // v0.11
  var t0 = Date.now(), r = await tellChat(t, cfg)([{ role: 'system', content: AG.PLAN_SYSTEM }, { role: 'user', content: user }], AG.PLAN_GRAMMAR);
  var steps = r.error ? [] : AG.parsePlan(r.text);
  t.traj.plans = (t.traj.plans || []).concat([{ step: t.step, again: !!again, system: AG.PLAN_SYSTEM, user: user, grammar: AG.PLAN_GRAMMAR,
    reply: r.error ? '' : String(r.text || '').slice(0, 600), steps: steps, ms: Date.now() - t0, error: r.error || '' }]);
  var bad = steps.length ? AG.planCheck(steps, t.goal) : '';
  if (bad) {                                       // v0.10 review: the plan is page-derived; a suspicious one is dropped
    t.traj.plans[t.traj.plans.length - 1].dropped = bad;
    feed(t, 'warn', 'The plan was not used: ' + bad + '.');
    return;
  }
  if (steps.length) {
    t.plan = { steps: steps, at: 0 };
    feed(t, 'info', (again ? 'New plan: ' : 'Plan: ') + steps.join(' → '));
  }
}

// ---- the brain ---------------------------------------------------------------------------------------------
// v0.12 (U4): the action calls in flight; a note / Pause / Take over / Stop aborts them (the reply would be stale)
var tellInflight = [];
function tellAbortInflight(why) {
  var all = tellInflight; tellInflight = [];
  all.forEach(function (c) { c.byOwner = why || 'cancelled'; try { c.abort(); } catch (e) {} });
  return all.length;
}
/** the owner acted while the model may be thinking: the reply in flight (if any) is dropped and the step asked again.
 *  Marks t (cancelSeq); the caller SAVES t first and then calls tellAbortInflight, so the loop that wakes up from the
 *  aborted call already reads the owner's change (and its own step undo is not overwritten by the handler's save). */
function tellCancelInflight(t) {
  t.cancelSeq = (t.cancelSeq || 0) + 1;
}
/** kind 'action' = the step's action call (abortable by the owner, U4); the plan / writer / validator calls are not. */
function tellChat(t, cfg, kind) {
  if (t.brain === 'claude') {
    var s = Object.assign({}, cfg, { claudeModel: cfg.agentClaudeModel });
    return function (messages, grammar, opts) { return M.claudeChat(s, messages, (opts && opts.maxTokens) || 300, 60000); };
  }
  // opts.maxTokens: the writer (v0.11) needs more than an action's 200 tokens; no grammar = plain text
  return async function (messages, grammar, opts) {
    if (!M.isLoopback(cfg.agentEndpoint)) return { error: 'agent endpoint is not loopback; refusing to send page data: ' + cfg.agentEndpoint };
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 120000);
    if (kind === 'action') tellInflight.push(ctl);
    try {
      var resp = await fetch(cfg.agentEndpoint.replace(/\/+$/, '') + '/chat/completions', {
        method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: cfg.agentModel, messages: messages, temperature: 0, max_tokens: (opts && opts.maxTokens) || 200, grammar: grammar,
                               chat_template_kwargs: { enable_thinking: false } }),
      });
      if (!resp.ok) return { error: 'local agent model HTTP ' + resp.status };
      var j = await resp.json();
      var c = j && j.choices && j.choices[0];
      return { text: c ? (c.message ? c.message.content : c.text) || '' : '' };
    } catch (e) {
      if (ctl.byOwner) return { error: 'cancelled: ' + ctl.byOwner, cancelled: true };
      return { error: localAgentHint(cfg.agentEndpoint), localDown: true };
    } finally { clearTimeout(timer); tellInflight = tellInflight.filter(function (c) { return c !== ctl; }); }
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
async function tellBusy() { var t = await getSess('tell'); return t && /^(running|paused|awaiting_approval|awaiting_draft|needs_user|confirm)$/.test(t.status) ? t : null; }

// ---- v0.9.4: a request that fits a saved recipe runs the recipe ------------------------------------------------------
// "get my September Claude invoice" -> the recipe recorded on claude.ai that downloads a PDF, for September 2026. Plain
// matching on this computer (site name, the recipe's name / goal, document words), no model call: a known chore is
// done the way it was shown, for the month the words name; only a request no recipe fits is explored from scratch.
var DOC_WORDS = /\b(invoices?|statements?|receipts?|bills?|billing|payouts?|remittances?|reports?|pdfs?|csvs?|exports?|documents?|docs?)\b/;
var MATCH_STOP = { the: 1, and: 1, for: 1, from: 1, get: 1, got: 1, my: 1, our: 1, me: 1, please: 1, download: 1, fetch: 1,
  grab: 1, pull: 1, save: 1, open: 1, find: 1, last: 1, this: 1, month: 1, months: 1, previous: 1, current: 1, recording: 1,
  with: 1, into: 1, can: 1, you: 1, all: 1, new: 1, www: 1, com: 1, https: 1, http: 1 };
function matchWords(text) {
  var out = {}, months = PD.MONTHS.concat(PD.SHORT).map(function (m) { return m.toLowerCase(); }).concat(['sept']);
  String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').split(' ').forEach(function (w) {
    if (w.length < 3 || MATCH_STOP[w] || /^\d+$/.test(w) || months.indexOf(w) >= 0) return;
    out[w.replace(/s$/, '')] = 1;
  });
  return out;
}
function recipeSite(rec) {
  try { var h = new URL(rec.startUrl || ((rec.steps || [])[0] || {}).url || '').hostname.replace(/^www\./, ''); return { host: h, name: h.split('.')[0] }; }
  catch (e) { return { host: '', name: '' }; }
}
var lastMatch = null;
async function matchRecipe(goal, tabUrl) {
  var recs = await getLocal('recordings', {}), runs = await getLocal('runs', []);
  var g = String(goal).toLowerCase(), gw = matchWords(goal), gDoc = DOC_WORDS.test(g), best = null;
  var here = ''; try { here = new URL(tabUrl || '').hostname.replace(/^www\./, ''); } catch (e) {}
  lastMatch = { goal: redactText(goal), tab: here, considered: [] };       // v0.9.7: why each recipe did / did not fit (run log)
  Object.keys(recs).forEach(function (id) {
    var rec = recs[id], seen = { name: rec.goal || rec.name, site: recipeSite(rec).host };
    lastMatch.considered.push(seen);
    if (!actionCount(rec)) { seen.skipped = 'no clicks or typing recorded'; return; }
    var site = recipeSite(rec), score = 0, why = [];
    // the site: named in the words ("Claude"), or the tab you are on (a recipe called "Recording 05/11/2026" still fits)
    var named = site.name.length >= 3 && (gw[site.name] || g.indexOf(site.host) >= 0), onIt = !!site.host && here === site.host;
    if (named || onIt) { score += 3; why.push(named ? 'the site ' + site.host : 'you are on ' + site.host); }
    var rw = matchWords([rec.name, rec.goal, rec.goalProposed].join(' '));
    var common = Object.keys(gw).filter(function (w) { return rw[w] && w !== site.name; });
    if (common.length) { score += common.length; why.push('"' + common.join('", "') + '"'); }
    var doc = gDoc && downloadGoal(rec);
    if (doc) { score += 2; why.push('it downloads a document'); }
    // the site alone is not enough ("change my Claude theme" is not the invoice chore): it needs the document or a
    // word of the recipe's name / goal; without the site, two such words
    seen.score = score; seen.why = why.join(', ');
    if (!((named || onIt) && (doc || common.length)) && common.length < 2) {
      seen.skipped = named || onIt ? 'the site fits, but nothing else in the words does' : 'neither the site nor 2 words of its name / goal are in the request';
      return;
    }
    var last = runs.filter(function (r) { return r.recId === id; })[0];
    var ok = !!(last && last.result && last.result.verdict === 'VERIFIED');
    var key = [score, ok ? 1 : 0, rec.createdAt || 0];
    if (!best || key[0] > best.key[0] || (key[0] === best.key[0] && (key[1] > best.key[1] || (key[1] === best.key[1] && key[2] > best.key[2]))))
      best = { rec: rec, key: key, why: why, site: site.host, ok: ok };
  });
  if (!best || best.key[0] < 3) { lastMatch.fit = null; return null; }
  lastMatch.fit = best.rec.goal || best.rec.name;
  var named = PD.fromText(goal, nowMs()), period = named || defaultPeriod(best.rec);
  return { recId: best.rec.id, name: best.rec.goal || best.rec.name, site: best.site, period: period, periodLabel: PD.label(period),
           periodFrom: named ? 'your words' : 'the recipe\'s default (' + (best.rec.periodMode === 'this' ? 'this' : 'last') + ' month)',
           why: 'fits ' + best.why.join(', '), lastOk: best.ok };
}

async function tellStart(msg) {
  var busy = await busyReason();
  if (busy) throw new Error(busy);
  if (await tellBusy()) throw new Error('a Tell-it run is already in progress: stop it first');
  var goal = String(msg.goal || '').trim().slice(0, 500);
  if (!goal) throw new Error('type what it should do');
  var tab = await chrome.tabs.get(msg.tabId), recipeNote = '';
  if (!msg.explore) {                                // a chore you showed it already: offer the recipe, for the month named
    var fit = await matchRecipe(goal, tab.url);
    if (fit) return { suggest: fit };
    var nRecs = Object.keys(await getLocal('recordings', {})).length;
    recipeNote = nRecs ? 'None of your ' + nRecs + ' saved recipes fits this request, so I am working it out from scratch. '
      + 'Tip: name a recipe after its chore (e.g. "Claude invoice") or start from its site, and Tell it reuses it.' : '';
  }
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
            edges: [], tried: {}, nothing: 0, plan: null, menuKey: '', lastAct: null, expand: false,   // v0.10 (site map, plan)
            notes: [], noteSeq: 0, declined: [], drafts: [],                                          // v0.11 (steering, drafts)
            traj: { task_id: '', model: '', steps: [], outcome: '', reason: '', events: [], flags: [], seconds: 0, error: '', drafts: [] } };
  t.traj.task_id = t.id; t.traj.model = t.model;
  t.recipeCheck = msg.explore ? { skipped: 'you chose to work it out from scratch' } : lastMatch;
  feed(t, 'info', 'Goal: ' + goal + ' | brain: ' + (t.brain === 'claude' ? 'Claude (' + cfg.agentClaudeModel + '; page text of each step goes to Anthropic)'
                                                                       : 'local model (nothing leaves this computer)'));
  if (recipeNote) feed(t, 'info', recipeNote);
  await saveTell(t);
  await chrome.tabs.update(t.tabId, { url: start });
  await sleep(150);
  tellDrive(t.id);
  return t;
}

var tellDriving = null;
var tellWarmAt = 0;
async function tellDrive(id) {
  if (tellDriving === id) return;
  tellDriving = id;
  try { await tellLoop(id); }
  catch (e) {
    var t = await getSess('tell');
    if (t && t.id === id && t.status === 'running') await tellEnd(t, 'halted', 'error: ' + String(e && e.message || e));
  } finally { if (tellDriving === id) tellDriving = null; }
}

var NOTHING_EFFECT = 'nothing visible changed, so it is set aside on this page';
function histOk(t, obs, effect) {
  if (!t.pendingHist) return;
  var ph = t.pendingHist, lt = AG.liveText(obs), now = 'now on "' + obs.title + '"' + (lt ? ' (' + lt.slice(0, 160) + ')' : '');
  if (ph.readback && ph.verb === 'select') {
    // v0.12 ladder fix: a select keeps the 0.11 line ('select(1, "Canada") -> ok; now on ...'); the read-back is added only
    // when the page shows another option. ('select(1) "" -> chosen: "Canada"' made the 4B re-select until the loop guard:
    // MiniWoB choose-list 3/3 -> 0/3.)
    var sq = function (s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim().toLowerCase(); };
    var same = sq(ph.chosen) === sq(ph.want);
    t.history.push(ph.step + '. ' + ph.show + ' -> ok; ' + (same ? '' : ph.readback + '; ') + now + (effect ? '; ' + effect : ''));
  } else if (ph.readback) {
    // v0.12 (U1): what the field holds now, read back from the page; a fill_enter also says where it went
    t.history.push(ph.step + '. ' + ph.short + ' -> ' + ph.readback + (ph.verb === 'fill_enter' ? '; ' + now : '') + (effect ? '; ' + effect : ''));
  } else if (effect === NOTHING_EFFECT) {
    t.history.push(ph.step + '. ' + ph.show + ' -> no visible effect; ' + now + '; it is set aside on this page');
  } else {
    t.history.push(ph.step + '. ' + ph.show + ' -> ok; ' + now + (effect ? '; ' + effect : ''));   // v0.10: what the action did
  }
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
    // an approved action runs as approved; text the owner approved (v0.11 draft) runs through the gates again (Enter
    // in a message box may send: it still needs its Approve)
    if (t.resumeAct) { var ra = t.resumeAct; t.resumeAct = null; await tellExecute(t, cfg, ra.act, ra.ctx, !ra.drafted); continue; }
    if (t.draftReq) { var dr = t.draftReq; t.draftReq = null; await saveTell(t); await tellWriteDraft(t, cfg, dr); continue; }   // v0.11 Rewrite
    if (t.step >= t.maxSteps) return tellEnd(t, 'halted', 'step cap ' + t.maxSteps + ' reached', true);
    if (elapsed(t) > t.maxSeconds * 1000) return tellEnd(t, 'halted', 'time cap ' + t.maxSeconds + 's reached', true);
    // v0.12 (U6): a vague writing goal asks once, before anything else, what the text should say or ask
    if (!t.clarifyDone && t.step === 0) {
      t.clarifyDone = true;
      if (cfg.agentClarify !== false && AG.vagueWritingGoal(t.goal) && !(t.notes || []).length) return tellClarify(t, cfg);
    }
    await waitForLoad(t.tabId); await ensureContent(t.tabId);
    var raw = await sendRetry(t.tabId, { type: 'agent_observe' });
    if (t.brain === 'claude') {
      var lock = cloudLockedFor(cfg, raw.url);
      if (lock) return tellEnd(t, 'needs_user', 'this page is on ' + lock + ', where the cloud is locked off; stopped before sending anything');
    }
    var obs = AG.maskObs(raw), v2 = cfg.pageView !== false;
    var ents = v2 ? PV.entities(obs) : null, effect = v2 ? await tellEffect(t, obs, ents) : '';
    histOk(t, obs, effect);
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
    var v2c = null;
    if (v2) {
      // v0.10: the page as entities, the site's known paths, the plan (one extra call at the start, one replan after two
      // clicks in a row that changed nothing), controls tried without effect set aside for this state
      var host = SM.hostOf(raw.url), key = tellStateKey(t, obs), map = await siteMapLoad(host);
      var pathsFor = function () {
        var sg = t.plan && t.plan.at < t.plan.steps.length ? ' ' + t.plan.steps[t.plan.at] : '';
        return SM.describe(map, host, { targetWords: t.goal + sg, from: key, max: 4 });
      };
      // the plan is made at the start only (a known route to the goal, or a goal of several hops); the replan is once
      var wantPlan = cfg.agentPlan !== false && ((!t.planTried && t.step === 0 && (multiHop(t.goal) || SM.route(map, host, key, t.goal).length > 0))
                                                 || (t.nothing >= 2 && !t.replanned));
      // v0.10 review: a plan is written from the page; on a page with text addressed to AI agents it would carry that
      // text into every later prompt as the agent's own plan: no plan (or replan) there
      if (wantPlan && hits.length) {
        t.planTried = true; if (t.step > 0) { t.replanned = true; t.nothing = 0; }
        if (!t.planSkipNoted) { t.planSkipNoted = true; feed(t, 'warn', 'No plan on this page: it has text addressed to AI agents.'); }
        wantPlan = false;
      }
      if (wantPlan) {
        var again = t.step > 0, f0 = t.feed.length;
        t.planTried = true; if (again) { t.replanned = true; t.nothing = 0; }
        await saveTell(t);
        await tellPlan(t, cfg, obs, pathsFor(), again);
        var tp = await getSess('tell');
        if (!tp || tp.id !== id || tp.status !== 'running') return;     // stopped while it planned
        // only what the plan call added: a note / pause typed meanwhile (tell_note) keeps its feed line
        tp.plan = t.plan; tp.traj.plans = t.traj.plans; tp.feed = tp.feed.concat(t.feed.slice(f0));
        if (tp.feed.length > 80) tp.feed.splice(0, tp.feed.length - 80);
        t = tp;
      }
      var tried = t.tried[key] || [], skip = [];
      (obs.controls || []).forEach(function (c, i) { if (tried.some(function (x) { return x.role === c.role && x.name === c.name; })) skip.push(i + 1); });
      tellDeclinedSkip(t, key, obs).forEach(function (n) { if (skip.indexOf(n) < 0) skip.push(n); });   // v0.11: the owner said no
      // v0.12 (U3): after a new owner note the submit controls wait (left out of the list and the grammar)
      var held = [];
      if (!tellGateOpen(t, cfg)) {
        (obs.controls || []).forEach(function (c, i) {
          if (!tellRiskySubmit(c) || skip.indexOf(i + 1) >= 0) return;            // (declined / tried: not shown anyway)
          skip.push(i + 1); if (held.indexOf(c.name) < 0) held.push(c.name);
        });
      }
      var rules = typeof tellRulesText === 'function' ? await tellRulesText(t, host, key) : '';   // lessons (lib/lessons.js), when wired
      v2c = { key: key, skip: skip, tried: tried, expand: !!t.expand, paths: pathsFor(), rules: rules || '', slim: slimObs(obs, ents),
              hint: held.length ? 'Held back until you act on the owner\'s newest instruction: "' + held.slice(0, 3).join('", "').replace(/\s+/g, ' ').slice(0, 120)
                                  + '" (' + (t.noteGate && t.noteGate.text && cfg.agentDraft !== false && AG.composeGoal(t.goal) ? 'write the text again first' : 'do another step first') + ').' : '' };
      t.expand = false;
    }
    t.step += 1;
    var note = t.note; t.note = '';
    var system = AG.SYSTEM + AG.SYSTEM_WEB, prompt, grammar, pv2 = null, freshIds = [];
    if (v2c) {
      // v0.11: every owner note of the episode (the newest says "(new)" once) and what the owner declined
      (t.notes || []).forEach(function (n) { if (n.fresh) freshIds.push(n.id); });
      pv2 = AG.buildPromptV2({ goal: t.goal, step: t.step, maxSteps: t.maxSteps, visited: t.visited, history: t.history, obs: obs,
                               injected: hits.length > 0, note: note, rules: v2c.rules, paths: v2c.paths, plan: t.plan,
                               expand: v2c.expand, skip: v2c.skip, tried: v2c.tried, now: nowMs(),
                               notes: t.notes, declined: t.declined,
                               pinned: t.pinned, held: v2c.hint });                             // v0.12 (U1, U3)
      prompt = pv2.text;
      grammar = AG.buildGrammarFor(pv2.shown, true, { scroll: v2c.expand ? 'up' : pv2.hidden > 0 ? 'down' : false });
      t.traj.promptVersion = AG.PROMPT_V2;
    } else {
      prompt = AG.buildPrompt({ goal: t.goal, step: t.step, maxSteps: t.maxSteps, visited: t.visited, history: t.history,
                                obs: obs, window: t.window, injected: hits.length > 0, note: note });
      grammar = AG.buildGrammar((obs.controls || []).length);
      t.traj.promptVersion = 'v1';
    }
    await saveTell(t);
    // v0.12 (U4): the owner's note / Pause during this call bumps cancelSeq (and aborts the request): the reply is dropped
    var cseq = t.cancelSeq || 0, decideFn = v2c ? AG.decideV2 : AG.decide;    // (U5: no backslash in a v2 string)
    var t0 = Date.now(), r = await decideFn(tellChat(t, cfg, 'action'), system, prompt, grammar, t.brain === 'claude');
    t = await getSess('tell');
    if (!t || t.id !== id) return;
    if (t.status !== 'running') return;                     // stopped while the model was thinking: do nothing
    if ((t.cancelSeq || 0) !== cseq || r.cancelled || /^cancelled:/.test(r.error || '')) {
      t.step -= 1;
      if (note && !t.note) t.note = note;                   // the dropped step's own note is not lost
      t.traj.events.push({ step: t.step + 1, cancelled: true, ms: Date.now() - t0 });
      feed(t, 'info', 'You stepped in while the model was thinking: that answer is dropped' + (t.pauseRequested ? '.' : ' and the step is asked again with your note.'));
      await saveTell(t); continue;
    }
    if (r.error) {
      t.step -= 1; t.note = note;
      return tellPause(t, 'needs_user', { kind: 'brain', message: 'The model could not answer: ' + r.error });
    }
    (t.notes || []).forEach(function (n) { if (freshIds.indexOf(n.id) >= 0) n.fresh = false; });   // v0.11: "(new)" was shown
    var ts = { i: t.step, system: system, user: prompt, grammar: grammar, reply: r.reply, action: '', valid: false, url: raw.url, ms: Date.now() - t0 };
    if (note) ts.note = AG.maskSecrets(note);               // v0.11 review: the export keeps notes; a pasted key is masked
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
    var ctx = { k: k, show: show, obs: obs, url: raw.url, expectName: ctrl ? ctrl.name : null, nControls: (obs.controls || []).length };
    if (pv2) Object.assign(ctx, { v2: true, shown: pv2.shown, hidden: pv2.hidden, expanded: v2c.expand, fromKey: v2c.key, slim: v2c.slim });
    if (pv2 && AG.hasBackslash(r.reply)) ctx.backslash = true;   // v0.12 (U5): a reply the v2 grammar would not allow
    await tellExecute(t, cfg, act, ctx, false);
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
function tellReject(t, ctx, err, cancelled, pin) {
  tellHist(t, ctx.k + '. ' + ctx.show + ' -> rejected: ' + err, pin);    // v0.12 (U1): pin = an owner line
  t.traj.events.push({ step: ctx.k, action: ctx.show, rejected: err });
  var st = t.traj.steps[t.traj.steps.length - 1]; if (st && st.i === ctx.k) st.valid = false;
  feed(t, 'bad', 'rejected: ' + err);
  return cancelled;
}
/** the owner said no to the pending action: its history line (pinned: it never leaves the prompt). */
function tellRefused(t, ctx) {
  tellHist(t, ctx.k + '. ' + ctx.show + ' -> REFUSED BY OWNER', true);
  t.traj.events.push({ step: ctx.k, action: ctx.show, rejected: 'the owner did not approve this' });
  var st = t.traj.steps[t.traj.steps.length - 1]; if (st && st.i === ctx.k) st.valid = false;
  feed(t, 'bad', 'refused by you');
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
  // v0.11: the owner already said no to this here: rejected without asking again (before validateV2, so the model is
  // told why even though the control is no longer shown)
  var dh = approved ? '' : tellDeclinedHit(t, act, ctx);
  if (dh) { tellReject(t, ctx, dh, false, true); if (!(await tellInvalid(t))) await saveTell(t); return; }
  // v0.12 (U3): a submit picked before anything was done about the owner's newest note (the control is not shown then;
  // a model that remembers its number, or the legacy prompt, still gets here)
  var gc = !approved && act.n && /^(click|select)$/.test(act.verb) ? ((ctx.obs && ctx.obs.controls) || [])[act.n - 1] : null;
  if (gc && tellRiskySubmit(gc) && !tellGateOpen(t, cfg)) { tellReject(t, ctx, tellGateWhy(t, cfg)); if (!(await tellInvalid(t))) await saveTell(t); return; }
  // (v0.11: the text the owner approved may be longer than an action's 200 characters: checked on its start)
  var vact = ctx.drafted ? Object.assign({}, act, { value: String(act.value || '').slice(0, 200) }) : act;
  var v = ctx.v2 ? AG.validateV2(vact, ctx.obs, ctx.shown, ctx.hidden, ctx.expanded) : AG.validate(vact, ctx.obs);
  if (v.error) { tellReject(t, ctx, v.error); if (!(await tellInvalid(t))) await saveTell(t); return; }
  if (v.secret) return tellSecretPause(t, ctx, v.secret.sensitive, v.secret.name);
  // v0.11: a compose field (post / comment / message body, its title) on a goal that composes: not typed from the action
  // call; the writer drafts it and the owner approves the text (tellWriteDraft). The text the owner approved comes back
  // here with ctx.drafted (not drafted again, not junk-checked: the owner wrote or approved every word).
  var dk = approved || ctx.drafted || cfg.agentDraft === false || !/^(fill|fill_enter)$/.test(act.verb) ? '' : AG.composeField(v.control, t.goal);
  if (dk) {
    var again = tellDraftedHit(t, v.control);
    if (again) { tellReject(t, ctx, again); if (!(await tellInvalid(t))) await saveTell(t); return; }
    markValid(t, ctx, { draft_for: v.control.name || '' });
    t.invalid = 0;
    feed(t, 'info', 'Writing a draft for "' + (v.control.name || 'the text') + '"; you see it before anything is typed.');
    await saveTell(t);
    return tellWriteDraft(t, cfg, { act: act, ctx: ctx, kind: dk });
  }
  // v0.11: junk values (the model's scratch text, the goal pasted into a body, nothing at all) are never typed
  var junk = approved || ctx.drafted ? '' : AG.junkValue(act, v.control, t.goal);
  if (junk) { tellReject(t, ctx, junk); if (!(await tellInvalid(t))) await saveTell(t); return; }
  // v0.12 (U5): the v2 grammar allows no backslash in a string (a model whose server ignores the grammar is refused
  // here); typographic quotes stand in for plain ones and are typed as plain ones
  if (ctx.v2 && !approved && !ctx.drafted && /^(fill|fill_enter|select)$/.test(act.verb)) {
    if (ctx.backslash) {
      tellReject(t, ctx, 'a backslash is not allowed in the text; write plain words (use \u201c \u201d for quotes)');
      if (!(await tellInvalid(t))) await saveTell(t); return;
    }
    if (act.verb !== 'select') act = Object.assign({}, act, { value: AG.mapQuotes(act.value) });
  }
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
  if (danger && !approved) {
    // v0.12 (U2): a submit on a compose page is checked first (one validator call); 'no' = not asked, the model fixes it
    var chk = v.control && (act.verb === 'click' || (act.verb === 'select' && v.control.role !== 'combobox')) && tellRiskySubmit(v.control) && AG.composePage(ctx.obs, t.goal)
      ? await tellPreSubmit(t, cfg, act, ctx, v.control) : null;
    if (chk && chk.stale) return;
    if (chk && chk.notReady) {
      tellHist(t, ctx.k + '. ' + ctx.show + ' "' + String(v.control.name || '').replace(/"/g, "'").slice(0, 40) + '" -> Not ready: ' + chk.notReady);
      var st0 = t.traj.steps[t.traj.steps.length - 1]; if (st0 && st0.i === ctx.k) st0.valid = false;
      feed(t, 'warn', 'Not ready to ' + (v.control.name || 'submit') + ': ' + chk.notReady);
      if (!(await tellInvalid(t))) await saveTell(t);
      return;
    }
    return tellAskApproval(t, act, ctx, danger, gateName, chk && chk.fields);
  }

  if (t.noteGate && act.verb !== 'scroll' && /^(goto|go_back)$/.test(act.verb)) t.noteGate.acted = true;   // (U3)
  if (act.verb === 'scroll' && ctx.v2) {             // v0.10: scroll(down) = every control on the next step, up = the short list
    t.expand = act.direction === 'down';
    await sendTab(t.tabId, { type: 'agent_act', act: act }).catch(function () {});
    return tellOk(t, ctx);
  }
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
  // v0.10: what the page looked like before a click / Enter, to learn its effect once the page settled (tellEffect)
  var la = null;
  if (ctx.v2 && ctx.slim && v.control && (act.verb === 'click' || act.verb === 'fill_enter' || (act.verb === 'select' && isRadio(v.control)))) {
    la = { verb: act.verb, role: v.control.role || '', name: v.control.name || '', context: v.control.context || '', row: !!v.control.row,
           items: v.control.row ? SM.itemWord([v.control.row]) : '', fromKey: ctx.fromKey, url: tabUrl, step: k, before: ctx.slim,
           dlMark: ((await chrome.downloads.search({ orderBy: ['-startTime'], limit: 1 }).catch(function () { return []; }))[0] || {}).id || 0,
           tabsBefore: (await chrome.tabs.query({}).catch(function () { return []; })).map(function (x) { return x.id; }) };
  }
  var r = await sendTab(t.tabId, { type: 'agent_act', act: act, approved: approved, expectName: ctx.expectName })
    .catch(function (e) { return { ok: false, error: 'the page did not answer (' + (e.message || e) + ')' }; });
  if (r && r.ok && la) t.lastAct = la;
  if (!r || !r.ok) {
    if (r && r.needsApproval && !approved) return tellAskApproval(t, act, ctx, r.danger, ctx.expectName || '');
    if (r && r.secret) return tellSecretPause(t, ctx, r.secret, ctx.expectName || '');
    tellReject(t, ctx, (r && r.error) || 'no reply from the page');
    if (!(await tellInvalid(t))) await saveTell(t);
    return;
  }
  if (approved) t.traj.events.push({ step: k, approved: true, control: ctx.expectName || '' });
  // v0.12 (U1): the read-back of a fill / fill_enter / select goes into its history line; (U3) a non-risky action ran
  if (t.noteGate && !(approved && danger) && act.verb !== 'scroll') t.noteGate.acted = true;
  var rbx = r.readback && /^(fill|fill_enter|select)$/.test(act.verb) && !(act.verb === 'select' && r.asClick)
    ? { readback: AG.readbackText(r.readback, act.verb === 'select' ? null : act.value, act.verb), verb: act.verb,
        short: act.verb + '(' + act.n + ') "' + String((v.control && v.control.name) || ctx.expectName || '').replace(/"/g, "'").slice(0, 40) + '"'
               + (ctx.drafted ? ' [the owner approved this text]' : ''),
        want: act.option, chosen: r.readback.value } : null;        // (select: the line is plain when these match)
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
  return tellOk(t, ctx, rbx);
}
/** the agent wanted to type into a password / code field: the user types it, the agent waits. */
function tellSecretPause(t, ctx, reason, name) {
  markValid(t, ctx, { secret: true });
  t.pendingHistRaw = ctx.k + '. ' + ctx.show + ' -> SKIPPED: the owner types passwords and codes themselves; continue with the goal.';
  return tellPause(t, 'needs_user', { kind: 'secret', message: 'Type the ' + (reason || 'password / code') + ' for "' + name
    + '" into the page yourself (AWOS never reads or stores it), then press Continue.' });
}
async function tellOk(t, ctx, rb) {
  markValid(t, ctx);
  t.invalid = 0;
  t.pendingHist = Object.assign({ step: ctx.k, show: ctx.show }, rb || {});
  await saveTell(t);
}
/** what a pause keeps of a step's context to run its action later (approval, v0.11 draft). */
function keepCtx(ctx) {
  var keep = { k: ctx.k, show: ctx.show, obs: ctx.obs, url: ctx.url, expectName: ctx.expectName, nControls: ctx.nControls };
  if (ctx.v2) Object.assign(keep, { v2: true, shown: ctx.shown, hidden: ctx.hidden, expanded: ctx.expanded, fromKey: ctx.fromKey, slim: ctx.slim });
  if (ctx.drafted) keep.drafted = true;
  return keep;
}
async function tellAskApproval(t, act, ctx, danger, target, fields) {
  t.traj.events.push({ step: ctx.k, action: ctx.show, approval_asked: danger });
  var what = act.verb === 'goto' ? 'open ' + act.value : act.verb === 'fill_enter' ? 'press Enter in "' + target + '"' : 'click "' + target + '"';
  var message = 'Step ' + ctx.k + ' wants to ' + what + ' (matches "' + danger + '"). Approve, or tell it no.';
  if (fields && fields.length) {
    // v0.12 (U2): what would be sent, so the owner approves the text and not only the button
    var short = function (v) { v = String(v || '').replace(/\s+/g, ' ').trim(); return v ? '"' + (v.length > 60 ? v.slice(0, 60) + '…' : v) + '"' : '(empty)'; };
    message = 'You are about to ' + what + ' on "' + String((ctx.obs && ctx.obs.title) || '').slice(0, 80) + '": '
      + fields.map(function (f) { return f.name + ' ' + (f.sensitive ? '(protected)' : short(f.value)); }).join(' / ') + '. Approve, or tell it no.';
  }
  return tellPause(t, 'awaiting_approval', { kind: 'approval', act: act, ctx: keepCtx(ctx),
    danger: danger, target: target, fields: fields && fields.length ? fields : null, message: message });
}
/** v0.12 (U2): the pre-submit check -> {notReady: why} | {fields} (ask the owner, showing them) | {stale} (the run
 *  stopped or the owner stepped in meanwhile). One small call: the goal, the owner's notes, the field values read back
 *  from the page (masked; protected fields by name only). No verdict (an error, a reply that is neither) = ask. */
async function tellPreSubmit(t, cfg, act, ctx, c) {
  var fr = await sendTab(t.tabId, { type: 'agent_fields', n: act.n }).catch(function () { return null; });
  var fields = ((fr && fr.fields) || []).filter(function (f) { return f && (f.name || f.value || f.sensitive); }).map(function (f) {
    return f.sensitive ? { name: String(f.name || 'field').slice(0, 60), sensitive: true }
                       : { name: String(f.name || 'field').slice(0, 60), value: AG.maskSecrets(String(f.value || '')).slice(0, 2000) };
  });
  if (!fields.length) {                                            // the page did not answer: the observed values (cut)
    fields = ((ctx.obs && ctx.obs.controls) || []).filter(function (x) { return x.role === 'textbox' && (c.form == null || x.form === c.form); })
      .slice(0, 8).map(function (x) { return x.sensitive ? { name: x.name || 'field', sensitive: true } : { name: x.name || x.context || 'field', value: x.value || '' }; });
  }
  if (t.brain === 'claude') {
    var lock = cloudLockedFor(cfg, ctx.url || '');
    if (lock) return { fields: fields };                          // (never sent: the owner sees the values and decides)
  }
  var user = AG.buildValidatorPrompt({ goal: t.goal, notes: t.notes, action: 'click "' + (c.name || '') + '"', where: (ctx.obs && ctx.obs.title) || '', fields: fields });
  var cseq = t.cancelSeq || 0, t0 = Date.now();
  var r = await tellChat(t, cfg)([{ role: 'system', content: AG.VALIDATOR_SYSTEM }, { role: 'user', content: user }], AG.VALIDATOR_GRAMMAR, { maxTokens: 60 });
  var verdict = r.error ? null : AG.parseVerdict(r.text);
  var tp = await getSess('tell');
  if (!tp || tp.id !== t.id || tp.status !== 'running' || (tp.cancelSeq || 0) !== cseq || tp.pauseRequested) {
    if (tp && tp.id === t.id && tp.status === 'running') { tp.step -= 1; await saveTell(tp); }   // asked again (with the note)
    return { stale: true };
  }
  t.traj.checks = (t.traj.checks || []).concat([{ step: ctx.k, control: c.name || '', system: AG.VALIDATOR_SYSTEM, user: user,
    grammar: AG.VALIDATOR_GRAMMAR, reply: r.error ? '' : String(r.text || '').slice(0, 300), verdict: verdict ? (verdict.ok ? 'yes' : 'no') : 'none',
    why: verdict && !verdict.ok ? verdict.why : '', error: r.error || '', ms: Date.now() - t0 }]);
  if (verdict && !verdict.ok) return { notReady: verdict.why };
  if (!verdict) feed(t, 'info', 'The pre-submit check gave no answer' + (r.error ? ' (' + String(r.error).slice(0, 80) + ')' : '') + '; check the values yourself.');
  return { fields: fields };
}

// ---- v0.12 (U6) clarify a writing goal ----------------------------------------------------------------------------
/** ONE small call guesses what the text should say or ask (from the goal only: no page data), then the episode waits
 *  in needs_user (kind 'clarify') with that guess, editable, in the popup. */
async function tellClarify(t, cfg) {
  var r = { error: 'no call' }, t0 = Date.now();
  if (t.brain !== 'claude' || !cloudLockedFor(cfg, t.startUrl)) {
    r = await tellChat(t, cfg)([{ role: 'system', content: AG.CLARIFY_SYSTEM }, { role: 'user', content: AG.buildClarifyPrompt(t.goal) }], undefined, { maxTokens: 60 });
  }
  var guess = r.error ? '' : AG.cleanGuess(r.text);
  var tp = await getSess('tell');
  if (!tp || tp.id !== t.id || tp.status !== 'running') return;
  tp.clarifyDone = true;
  tp.traj.clarify = { goal: t.goal, reply: r.error ? '' : String(r.text || '').slice(0, 300), guess: guess, error: r.error || '', ms: Date.now() - t0 };
  return tellPause(tp, 'needs_user', { kind: 'clarify', guess: guess,
    message: 'What should it say or ask? Edit the line below (or write your own) and press Use this, or Skip.' });
}

// ---- v0.11 draft-and-approve writing ----------------------------------------------------------------------------
var DRAFT_MAX_CHARS = 5000;          // what the owner may approve (edited) for one field
function squash(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }
/** the model fills a compose field again whose text the owner already approved (and it is still there), with no new
 *  owner note since -> the rejection, or ''. A new note ("rewrite the body ...") lets it draft again. */
function tellDraftedHit(t, c) {
  var d = (t.drafts || []).filter(function (x) { return x.role === (c.role || '') && x.name === (c.name || ''); }).pop();
  var cur = squash(c.value);
  if (!d || d.noteSeq !== (t.noteSeq || 0) || !cur || squash(d.final).slice(0, 60) !== cur.slice(0, 60)) return '';
  return 'the owner approved the text in "' + (c.name || 'this field') + '" at step ' + d.step + ' and it is typed in; do not '
    + 'write it again unless the owner asks: go on with the goal';
}
/** where the field is, for the writer: the page title, the open window, the form's label, the first headings. */
function draftWhere(o, c) {
  var out = [], fm = c && c.form != null ? ((o.forms || [])[c.form] || {}).label : '';
  [o.title, o.dialogName || o.dialog, fm].concat((o.headings || []).slice(0, 3)).forEach(function (w) {
    w = squash(w); if (w && out.indexOf(w) < 0) out.push(w);
  });
  return out.slice(0, 5);
}
/** ONE writer call (one retry when the text is junk: AWOSAgent.junkValue) -> the episode waits in 'awaiting_draft'
 *  with the draft. req = {act, ctx, kind: 'title' | 'body', previous, change, idx (the traj.drafts entry on a rewrite)}.
 *  The writer gets no page text beyond the field's surroundings (masked) and never an action grammar. */
async function tellWriteDraft(t, cfg, req) {
  var act = req.act, ctx = req.ctx, o = ctx.obs || {}, ctls = o.controls || [], c = ctls[act.n - 1] || {};
  var name = c.name || c.context || '';
  if (t.brain === 'claude') {                      // a Rewrite runs after a pause: the page may be another site by now
    var nowUrl = ((await chrome.tabs.get(t.tabId).catch(function () { return {}; })).url) || ctx.url || '';
    var lock = cloudLockedFor(cfg, nowUrl);
    if (lock) return tellEnd(t, 'needs_user', 'this page is on ' + lock + ', where the cloud is locked off; stopped before sending anything');
  }
  var others = ctls.filter(function (x, i) {
    return i !== act.n - 1 && x.role === 'textbox' && !x.sensitive && squash(x.value) && (c.form == null || x.form === c.form);
  }).slice(0, 4).map(function (x) { return { name: x.name || x.context || '', value: x.value }; });
  var where = draftWhere(o, c), allow = AG.draftAllow(t.goal, t.notes), chat = tellChat(t, cfg), writes = [], text = '', why = '', err = '';
  for (var i = 0; i < 2; i++) {
    var user = AG.buildWriterPrompt({ goal: t.goal, notes: t.notes, field: { name: name, kind: req.kind }, where: where, others: others,
                                      current: c.value, previous: req.previous, change: req.change, refused: why });
    var t0 = Date.now();
    var r = await chat([{ role: 'system', content: AG.WRITER_SYSTEM }, { role: 'user', content: user }], undefined,
                       { maxTokens: req.kind === 'title' ? 80 : 600 });
    if (r.error) { err = r.error; writes.push({ user: user, reply: '', text: '', error: r.error, ms: Date.now() - t0 }); break; }
    text = AG.cleanDraft(r.text, req.kind, allow);
    why = AG.junkValue({ verb: 'fill', value: text }, c, t.goal);       // fix D applies to the writer too
    writes.push({ user: user, reply: String(r.text || '').slice(0, 4000), text: text, junk: why, ms: Date.now() - t0 });
    if (!why) break;
  }
  var tp = await getSess('tell');
  if (!tp || tp.id !== t.id || tp.status !== 'running') return;       // stopped / taken over while it wrote
  var ds = tp.traj.drafts = tp.traj.drafts || [], idx = req.idx != null && ds[req.idx] ? req.idx : -1;
  // v0.11 review: Take over pressed while it wrote: the owner has the page; no draft pops up over it (the loop pauses on
  // pauseRequested next). The writes are kept as an interrupted draft. (A plain Pause still shows the draft.)
  if (tp.takeover) {
    if (idx < 0) { ds.push({ step: ctx.k, field: name, kind: req.kind, where: '', writes: [], rewrites: 0, draft: '', final: null, edited: false, outcome: 'interrupted' }); idx = ds.length - 1; }
    writes.forEach(function (w) { ds[idx].writes.push(w); }); ds[idx].draft = text; ds[idx].outcome = 'interrupted';
    tellHist(tp, ctx.k + '. ' + ctx.show + ' -> not typed: the owner took the page; continue from what is there.', true);
    await saveTell(tp); return;
  }
  if (idx < 0) { ds.push({ step: ctx.k, field: name, kind: req.kind, where: where.join(' | '), writes: [], rewrites: 0, draft: '', final: null, edited: false, outcome: '' }); idx = ds.length - 1; }
  var d = ds[idx];
  writes.forEach(function (w) { d.writes.push(Object.assign({ change: req.change || '' }, w)); });
  d.draft = text;
  var what = req.kind === 'title' ? 'the title "' + name + '"' : '"' + name + '"';
  var message = err ? 'The writer model could not answer (' + err.slice(0, 160) + '). Type the text for ' + what + ' yourself, then Use this text; or Rewrite to try again, or Stop.'
    : 'Step ' + ctx.k + ' wants to write ' + what + '. Check the draft: edit it if you like, then Use this text; or Rewrite (say what to change), or Stop.'
      + (why ? ' The writer\'s text looks wrong (' + why.slice(0, 160) + '): fix it or Rewrite.' : '');
  return tellPause(tp, 'awaiting_draft', { kind: 'draft', act: act, ctx: keepCtx(ctx), field: name, fieldKind: req.kind, target: name,
                                          draft: text, draftIdx: idx, fieldName: c.name || '', junk: why || '', error: err || '', message: message });
}
function tellDraftPending(t, msg) {
  if (!t || t.status !== 'awaiting_draft' || !t.pending || t.pending.kind !== 'draft' || t.pending.nonce !== msg.nonce) throw new Error('no draft is waiting');
  return t.pending;
}
/** [Use this text]: the owner's text (the draft, maybe edited) is what gets typed: the step runs again with it. */
async function tellDraftUse(msg) {
  var t = await getSess('tell'), p = tellDraftPending(t, msg);
  var text = String(msg.text == null ? p.draft : msg.text).replace(/\r/g, '').replace(/^\s+|\s+$/g, '');
  if (!text) throw new Error('the text is empty: write it, or Rewrite');
  text = text.slice(0, DRAFT_MAX_CHARS);
  var d = (t.traj.drafts || [])[p.draftIdx] || {}, edited = text !== p.draft, n = p.act.n;
  d.final = AG.maskSecrets(text); d.edited = edited; d.outcome = 'used';   // (the trajectory is exported: masked; the page gets the text)
  t.traj.events.push({ step: p.ctx.k, draft_used: true, field: p.field, edited: edited });
  // (v0.11 review: keyed by the control's own name, as tellDraftedHit compares it; p.field may be its context)
  var fname = p.fieldName != null ? p.fieldName : (p.field || '');
  t.drafts = (t.drafts || []).filter(function (x) { return x.name !== fname; }).concat([{
    role: 'textbox', name: fname, final: text, step: p.ctx.k, noteSeq: t.noteSeq || 0 }]).slice(-6);
  // v0.11 review: the owner approved new text on this page: what they declined here before (the reddit "Post" while the
  // body was wrong) may be proposed again; it still needs its Approve. Without this the run dead-ends: Post hidden forever.
  var dkey = tellCtxKey(p.ctx), lifted = [];
  t.declined = (t.declined || []).filter(function (x) {
    if (x.verb === 'goto' || x.key !== dkey) return true;
    lifted.push(x.name); return false;
  });
  if (lifted.length) feed(t, 'info', 'With the new text, "' + lifted.join('", "') + '" may be proposed again (it still needs your Approve).');
  if (t.noteGate) t.noteGate.drafted = true;                       // v0.12 (U3): a new draft after the owner's note
  var act = Object.assign({}, p.act, { value: text });
  // the history shows the approved text (cut), not the model's own words
  var ctx = Object.assign({}, p.ctx, { drafted: true,
    show: p.act.verb + '(' + n + ', ' + AG.pyJson(AG.maskSecrets(text.replace(/\s+/g, ' ')).slice(0, 80) + (text.length > 80 ? '…' : '')) + ') [the owner approved this text]' });
  t.resumeAct = { act: act, ctx: ctx, drafted: true };
  feed(t, 'info', 'You approved the text for "' + (p.field || 'the field') + '"' + (edited ? ' (edited)' : '') + '; typing it.');
  t.status = 'running'; t.pending = null; t.segStart = Date.now();
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
  tellDrive(t.id);
  return { ok: true, edited: edited };
}
/** [Rewrite] (+ an optional note: kept with the owner's notes too): another writer call, with the previous draft (as
 *  the owner left it in the box) and what to change. */
async function tellDraftRewrite(msg) {
  var t = await getSess('tell'), p = tellDraftPending(t, msg);
  var note = String(msg.note || '').trim().slice(0, AG.NOTE_CHARS || 300);
  if (note) tellAddNote(t, note, false);
  var d = (t.traj.drafts || [])[p.draftIdx]; if (d) d.rewrites = (d.rewrites || 0) + 1;
  t.traj.events.push({ step: p.ctx.k, draft_rewrite: true, field: p.field, note: AG.maskSecrets(note) });   // (exported: masked)
  t.draftReq = { act: p.act, ctx: p.ctx, kind: p.fieldKind, idx: p.draftIdx, change: note,
                 previous: msg.text != null && String(msg.text).trim() ? String(msg.text).slice(0, DRAFT_MAX_CHARS) : p.draft };
  feed(t, 'info', 'Rewriting the draft for "' + (p.field || 'the field') + '"' + (note ? ': ' + note : '') + '.');
  t.status = 'running'; t.pending = null; t.segStart = Date.now();
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
  tellDrive(t.id);
  return { ok: true };
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
    await tellMapOutcome(t, false);
    await tellLessonsEnd(t, 'FAILED');
    t.status = 'ended'; t.pending = null;
    await keepTrajectory(t, false); await tellLogEnd(t); await saveTell(t);
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
  await tellMapOutcome(t, true);                                    // v0.10: the confirmed path is the site map's 'ok'
  await tellLessonsEnd(t, 'VERIFIED');
  var smr = await getLocal('siteMapRecs', {}); smr[rec.id] = 1; await setLocal('siteMapRecs', smr);   // (not merged twice)
  t.status = 'ended'; t.pending = null;
  feed(t, 'end', 'Saved as the recipe "' + rec.name + '". It now replays like any recording.');
  await keepTrajectory(t, true); await tellLogEnd(t); await saveTell(t);
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
    tellRefused(t, p.ctx);                                          // v0.12 (U1): REFUSED BY OWNER, pinned
    tellRecordDecline(t, p);                                       // v0.11: it stays declined for the episode
  } else if (kind === 'continue') {
    if (t.status !== 'needs_user') throw new Error('nothing to continue');
    if (p.kind === 'guard') t.guardSkip[p.guardKey] = 1;
    if (p.kind === 'guard' || p.kind === 'secret') await recordTypedSecrets(t);
    if (t.pendingHistRaw) { tellHist(t, t.pendingHistRaw, true); t.pendingHistRaw = null; }
    if (p.kind === 'invalid') t.invalid = 0;
  } else throw new Error('unknown decision');
  var offer = null;
  if (p.kind === 'clarify') {
    // v0.12 (U6): the answer is the first owner instruction (no one-step note, no lesson offer, no note gate)
    var ans = String(msg.note || '').trim().slice(0, AG.NOTE_CHARS || 300);
    if (ans) { tellAddNote(t, ans, false, true); feed(t, 'info', 'It will write: ' + ans); }
    else feed(t, 'info', 'No answer: it works the text out from the goal.');
    t.traj.clarify = Object.assign(t.traj.clarify || {}, { answer: ans ? AG.maskSecrets(ans) : '', edited: !!ans && ans !== p.guess, skipped: !ans });
  } else if (msg.note) {
    t.note = String(msg.note).trim().slice(0, 300);
    tellAddNote(t, t.note, kind === 'decline');                    // v0.11: kept for every later step
    if (msg.viaNote) feed(t, 'info', 'Your correction goes into the next step: ' + t.note);
    offer = await tellOfferLesson(t, t.note);
  }
  t.status = 'running'; t.pending = null; t.segStart = Date.now();
  await saveTell(t);
  await sendTab(t.tabId, { type: 'banner', text: '' }).catch(function () {});
  tellDrive(t.id);
  return { ok: true, offer: offer, declined: kind === 'decline' };
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
  if (!t || !/^(running|paused|needs_user|awaiting_approval|awaiting_draft)$/.test(t.status)) throw new Error('no Tell-it run to take over');
  t.takeover = true; t.lastClickAt = 0;
  var abort = t.status === 'running';
  if (abort) { t.pauseRequested = true; tellCancelInflight(t); }      // v0.12 (U4)
  else {
    // v0.11 review: a draft waiting is dropped (the owner types by hand now)
    if (t.status === 'awaiting_draft' && t.pending) { var dd = (t.traj.drafts || [])[t.pending.draftIdx]; if (dd && !dd.outcome) dd.outcome = 'taken over'; }
    stopClock(t); t.status = 'paused'; t.pending = null;
  }
  feed(t, 'info', 'You have the page: what you click and type is recorded into the recipe. Press Resume to hand it back.');
  await saveTell(t);
  if (abort) tellAbortInflight('take over');
  await sendTab(t.tabId, { type: 'rec_state', on: true }).catch(function () {});
  return t;
}
async function tellResume() {
  var t = await getSess('tell');
  if (!t || t.status !== 'paused') throw new Error('nothing to resume');
  if (t.takeover) {
    t.takeover = false;
    await sendTab(t.tabId, { type: 'rec_state', on: false }).catch(function () {});
    tellHist(t, t.step + '. (the owner acted on the page by hand; continue from this page)', true);
    t.pendingHist = null;
    // v0.11 review: an approved action / approved text / rewrite queued before the owner took the page is stale now (the
    // page changed by hand): it is not run on Resume
    if (t.resumeAct || t.draftReq) { t.resumeAct = null; t.draftReq = null; feed(t, 'info', 'The step queued before you took over was dropped.'); }
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
    // popup opened: wake the local agent model in the background (one 1-token request) so the first Tell-it step
    // does not pay the cold load. Local loopback endpoint only; never the cloud. At most once a minute.
    tell_warm: async function () {
      var cfg = await tellSettings();
      if (cfg.agentBrain !== 'local' || !M.isLoopback(cfg.agentEndpoint)) return { warm: false };
      if (Date.now() - (tellWarmAt || 0) < 60000) return { warm: 'recent' };
      tellWarmAt = Date.now();
      fetch(String(cfg.agentEndpoint).replace(/\/$/, '') + '/chat/completions', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ model: cfg.agentModel, messages: [{ role: 'user', content: 'ok' }], max_tokens: 1 })
      }).catch(function () {});
      return { warm: true };
    },
    tell_pause: async function () {
      var t = await getSess('tell'); if (!t || t.status !== 'running') throw new Error('not running');
      t.pauseRequested = true; tellCancelInflight(t); await saveTell(t); tellAbortInflight('pause');   // v0.12 (U4)
      return { ok: true };
    },
    tell_resume: async function () { return tellResume(); },
    tell_takeover: async function () { return tellTakeover(); },
    tell_stop: async function () {
      var t = await getSess('tell'); if (!t || t.status === 'ended') throw new Error('no Tell-it run');
      if (t.takeover) { t.takeover = false; await sendTab(t.tabId, { type: 'rec_state', on: false }).catch(function () {}); }
      tellCancelInflight(t);
      await tellEnd(t, 'halted', 'stopped by the owner');
      tellAbortInflight('stop');                                     // v0.12 (U4): nothing waits on a stopped run's call
      return { ok: true };
    },
    tell_note: async function (msg) {
      var t = await getSess('tell'); if (!t || t.status === 'ended') throw new Error('no Tell-it run');
      // v0.11: a note typed while an approval waits is a "no" to it, with the note as the reason (the owner is
      // correcting the course, not approving the click)
      if (t.status === 'awaiting_approval' && t.pending && t.pending.kind === 'approval' && String(msg.text || '').trim())
        return tellDecide('decline', { nonce: t.pending.nonce, note: msg.text, viaNote: true });
      // v0.11: a note typed while a draft waits is a Rewrite with that note
      if (t.status === 'awaiting_draft' && t.pending && t.pending.kind === 'draft' && String(msg.text || '').trim())
        return tellDraftRewrite({ nonce: t.pending.nonce, note: msg.text });
      t.note = String(msg.text || '').trim().slice(0, 300);
      tellAddNote(t, t.note, false);                                 // v0.11: kept for every later step
      // v0.12 (U4): the action the model is choosing right now was chosen without this note: abort it, ask again
      var abort = t.status === 'running' && !!t.note;
      if (abort) tellCancelInflight(t);
      feed(t, 'info', 'Your correction goes into the next step: ' + t.note);
      var offer = await tellOfferLesson(t, t.note);                  // v0.10: "Remember for <site>?" in the popup
      await saveTell(t);
      if (abort) tellAbortInflight('note');
      return { ok: true, offer: offer };
    },
    tell_approve: async function (msg) { return tellDecide('approve', msg); },
    tell_decline: async function (msg) { return tellDecide('decline', msg); },
    tell_continue: async function (msg) { return tellDecide('continue', msg); },
    tell_draft_use: async function (msg) { return tellDraftUse(msg); },             // v0.11 draft-and-approve
    tell_draft_rewrite: async function (msg) { return tellDraftRewrite(msg); },
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
