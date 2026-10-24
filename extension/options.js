/* AWOS Replay options: local model endpoint / name / on-off, find timeout. */
'use strict';
var $ = function (id) { return document.getElementById(id); };

chrome.runtime.sendMessage({ type: 'get_state' }).then(function (st) {
  var s = st.settings;
  $('endpoint').value = s.endpoint; $('model').value = s.model;
  $('enabled').checked = !!s.enabled; $('findTimeoutMs').value = s.findTimeoutMs;
});

$('save').onclick = function () {
  var ep = $('endpoint').value.trim() || AWOSModel.DEFAULTS.endpoint;
  if (!AWOSModel.isLoopback(ep)) { $('status').className = 'error'; $('status').textContent = 'Endpoint must be loopback (127.0.0.1 / localhost).'; return; }
  var settings = { endpoint: ep, model: $('model').value.trim() || 'local', enabled: $('enabled').checked,
                   findTimeoutMs: Math.max(500, parseInt($('findTimeoutMs').value, 10) || 4000) };
  chrome.runtime.sendMessage({ type: 'save_settings', settings: settings }).then(function () {
    $('status').className = ''; $('status').textContent = 'Saved.';
  });
};
