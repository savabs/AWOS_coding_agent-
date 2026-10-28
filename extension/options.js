/* AWOS Replay options: brain (local model or Claude with the user's own key), local endpoint / name / on-off,
 * find timeout. The API key goes to chrome.storage.local via save_claude_key and is never shown again. */
'use strict';
var $ = function (id) { return document.getElementById(id); };
function say(text, bad) { $('status').className = bad ? 'error' : ''; $('status').textContent = text; }
function brain() { return $('brain-claude').checked ? 'claude' : 'local'; }
function showBrain() { $('claudebox').hidden = brain() !== 'claude'; }

function load() {
  chrome.runtime.sendMessage({ type: 'get_state' }).then(function (st) {
    var s = st.settings;
    $('endpoint').value = s.endpoint; $('model').value = s.model;
    $('enabled').checked = !!s.enabled; $('findTimeoutMs').value = s.findTimeoutMs;
    $(s.brain === 'claude' ? 'brain-claude' : 'brain-local').checked = true;
    $('cloudConsent').checked = !!s.cloudConsent;
    $('keystate').textContent = st.claudeKeySet ? 'A key is saved. Leave the box empty to keep it.' : 'No key saved yet.';
    showBrain();
  });
  chrome.runtime.sendMessage({ type: 'brain_status' }).then(function (b) {
    if (!b || b.brain !== 'local') return;
    $('localstatus').className = b.ok ? 'ok' : 'error';
    $('localstatus').textContent = b.ok ? 'Running.' : 'Not running: ' + b.error;
  });
}
$('brain-local').onchange = showBrain; $('brain-claude').onchange = showBrain;

$('save').onclick = async function () {
  var ep = $('endpoint').value.trim() || AWOSModel.DEFAULTS.endpoint;
  if (!AWOSModel.isLoopback(ep)) return say('Endpoint must be loopback (127.0.0.1 / localhost).', true);
  var b = brain(), key = $('claudeKey').value.trim();
  if (b === 'claude' && !$('cloudConsent').checked) return say('Tick the consent box to use Claude, or choose the local model.', true);
  if (key) {
    var r = await chrome.runtime.sendMessage({ type: 'save_claude_key', key: key });
    if (r && r.error) return say(r.error, true);
    $('claudeKey').value = '';
  }
  var settings = { endpoint: ep, model: $('model').value.trim() || 'local', enabled: $('enabled').checked,
                   findTimeoutMs: Math.max(500, parseInt($('findTimeoutMs').value, 10) || 4000),
                   brain: b, cloudConsent: b === 'claude' && $('cloudConsent').checked };
  await chrome.runtime.sendMessage({ type: 'save_settings', settings: settings });
  say('Saved.');
  load();
};
load();
