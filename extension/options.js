/* AWOS Replay options: brain for repair / verify (local model or Claude with the user's own key), Tell it's agent
 * brain (local agent model by default; Claude only on opt-in), the never-use-cloud site list, training-data keeping,
 * local endpoint / name / on-off, find timeout, login helpers on portal runs (remember me, Chrome autofill sign-in). The API key goes to chrome.storage.local via save_claude_key and is never shown again. */
'use strict';
var $ = function (id) { return document.getElementById(id); };
function say(text, bad) { $('status').className = bad ? 'error' : ''; $('status').textContent = text; }
function brain() { return $('brain-claude').checked ? 'claude' : 'local'; }
function agentBrain() { return $('agent-claude').checked ? 'claude' : 'local'; }
function showBrain() { $('claudebox').hidden = brain() !== 'claude' && agentBrain() !== 'claude'; }

function load() {
  Promise.all([chrome.runtime.sendMessage({ type: 'get_state' }), chrome.runtime.sendMessage({ type: 'tell_state' })]).then(function (both) {
    var st = both[0], s = Object.assign({}, both[1].settings, st.settings);
    $('endpoint').value = s.endpoint; $('model').value = s.model;
    $('enabled').checked = !!s.enabled; $('findTimeoutMs').value = s.findTimeoutMs; $('collectionWaitMin').value = s.collectionWaitMin || 10;
    $(s.brain === 'claude' ? 'brain-claude' : 'brain-local').checked = true;
    $('cloudConsent').checked = !!s.cloudConsent;
    $(s.agentBrain === 'claude' ? 'agent-claude' : 'agent-local').checked = true;
    $('agentEndpoint').value = s.agentEndpoint || 'http://127.0.0.1:8081/v1';
    $('agentModel').value = s.agentModel || 'qwen3.5-4b';
    $('searchPage').value = s.searchPage || 'https://duckduckgo.com/?q={goal}';
    $('agentClaudeModel').value = s.agentClaudeModel || 'claude-haiku-5-5';
    $('noCloudSites').value = (s.noCloudSites || []).join('\n');
    $('keepTraining').checked = s.keepTraining !== false;
    $('rememberMe').checked = s.rememberMe !== false; $('autofillSignin').checked = s.autofillSignin !== false;
    $('keystate').textContent = st.claudeKeySet ? 'A key is saved. Leave the box empty to keep it.' : 'No key saved yet.';
    showBrain();
  });
  chrome.runtime.sendMessage({ type: 'brain_status' }).then(function (b) {
    if (!b || b.brain !== 'local') return;
    $('localstatus').className = b.ok ? 'ok' : 'error';
    $('localstatus').textContent = b.ok ? 'Running.' : 'Not running: ' + b.error;
  });
}
chrome.runtime.sendMessage({ type: 'agent_status' }).then(function (b) {
  if (!b || b.brain !== 'local') return;
  $('agentstatus').className = b.ok ? 'ok' : 'error';
  $('agentstatus').textContent = b.ok ? 'Agent model answering.' : b.error;
});
$('brain-local').onchange = showBrain; $('brain-claude').onchange = showBrain;
$('agent-local').onchange = showBrain; $('agent-claude').onchange = showBrain;

$('save').onclick = async function () {
  var ep = $('endpoint').value.trim() || AWOSModel.DEFAULTS.endpoint;
  if (!AWOSModel.isLoopback(ep)) return say('Endpoint must be loopback (127.0.0.1 / localhost).', true);
  var b = brain(), ab = agentBrain(), key = $('claudeKey').value.trim();
  if ((b === 'claude' || ab === 'claude') && !$('cloudConsent').checked) return say('Tick the consent box to use Claude, or choose the local model.', true);
  var aep = $('agentEndpoint').value.trim() || 'http://127.0.0.1:8081/v1';
  if (!AWOSModel.isLoopback(aep)) return say('The agent endpoint must be loopback (127.0.0.1 / localhost).', true);
  if (key) {
    var r = await chrome.runtime.sendMessage({ type: 'save_claude_key', key: key });
    if (r && r.error) return say(r.error, true);
    $('claudeKey').value = '';
  }
  var settings = { endpoint: ep, model: $('model').value.trim() || 'local', enabled: $('enabled').checked,
                   findTimeoutMs: Math.max(500, parseInt($('findTimeoutMs').value, 10) || 4000),
                   collectionWaitMin: Math.min(240, Math.max(1, parseInt($('collectionWaitMin').value, 10) || 10)),
                   brain: b, cloudConsent: (b === 'claude' || ab === 'claude') && $('cloudConsent').checked,
                   agentBrain: ab, agentEndpoint: aep, agentModel: $('agentModel').value.trim() || 'qwen3.5-4b',
                   searchPage: /^https:\/\//.test($('searchPage').value.trim()) ? $('searchPage').value.trim() : 'https://duckduckgo.com/?q={goal}',
                   agentClaudeModel: $('agentClaudeModel').value, keepTraining: $('keepTraining').checked,
                   rememberMe: $('rememberMe').checked, autofillSignin: $('autofillSignin').checked,
                   noCloudSites: $('noCloudSites').value.split(/[\s,]+/).filter(Boolean) };
  await chrome.runtime.sendMessage({ type: 'save_settings', settings: settings });
  say('Saved.');
  load();
};
load();
