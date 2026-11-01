/* AWOS Replay: shows one failure screenshot (v0.8) from chrome.storage.local, by id (shot.html?id=...). */
'use strict';
var id = new URLSearchParams(location.search).get('id') || '';
chrome.runtime.sendMessage({ type: 'get_shot', id: id }).then(function (s) {
  if (!s || s.error || !/^data:image\/(jpeg|png);base64,/.test(s.dataUrl || '')) {
    document.getElementById('err').textContent = (s && s.error) || 'No such screenshot.';
    document.getElementById('shot').hidden = true;
    return;
  }
  document.getElementById('title').textContent = (s.recName || 'Run') + ', step ' + (s.step + 1) + ' (' + s.why + '), ' + new Date(s.at).toLocaleString();
  document.getElementById('shot').src = s.dataUrl;
});
