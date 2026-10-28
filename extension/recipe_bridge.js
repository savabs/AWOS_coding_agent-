/* AWOS Replay recipe bridge: runs only on the AWOS recipe pages
 *   https://awos.tirramind.com/r/*   (the site)
 *   https://awos-site.pages.dev/r/*  (Pages preview)
 *   http://localhost:8000/r/*        (development)
 * Contract: docs/specs/recipe_v1.md section 5.
 *
 *   page -> {type:'awos-replay-hello'}                => {type:'awos-replay-present', version}
 *            (also document.documentElement.dataset.awosReplay = version, as a fallback signal)
 *   page -> {type:'awos-import-recipe', recipe: {}}    => {type:'awos-import-result', ok:true}
 *                                                      | {type:'awos-import-result', ok:false, error}
 *            The answer comes only after the user confirmed (Import) or cancelled (error 'cancelled') in the popup.
 *
 * Only messages from this window and one of the three origins are read. The recipe is forwarded as plain data
 * to the service worker, which validates it and parks it until the user decides; nothing is imported here.
 */
(function () {
  'use strict';
  if (window.__awosBridgeLoaded) return;
  window.__awosBridgeLoaded = true;
  var ORIGINS = ['https://awos.tirramind.com', 'https://awos-site.pages.dev', 'http://localhost:8000'];
  var version = chrome.runtime.getManifest().version;

  function answer(msg) { window.postMessage(msg, location.origin); }
  function result(ok, error) { answer(ok ? { type: 'awos-import-result', ok: true } : { type: 'awos-import-result', ok: false, error: String(error || 'failed') }); }
  try { document.documentElement.dataset.awosReplay = version; } catch (e) {}

  // the user's decision in the popup comes back from the service worker
  chrome.runtime.onMessage.addListener(function (msg, _sender, reply) {
    if (!msg || msg.type !== 'import_result') return;
    result(!!msg.ok, msg.error); reply({ ok: true });
  });

  window.addEventListener('message', function (e) {
    if (e.source !== window || ORIGINS.indexOf(e.origin) < 0 || e.origin !== location.origin) return;
    var d = e.data;
    if (!d || typeof d !== 'object') return;
    if (d.type === 'awos-replay-hello') { answer({ type: 'awos-replay-present', version: version }); return; }
    if (d.type !== 'awos-import-recipe') return;
    var recipe;
    try { recipe = JSON.parse(JSON.stringify(d.recipe)); }          // plain data only
    catch (err) { result(false, 'recipe is not plain JSON'); return; }
    try {
      chrome.runtime.sendMessage({ type: 'import_offer', recipe: recipe }, function (r) {
        if (chrome.runtime.lastError || !r) { result(false, 'AWOS Replay did not answer; reload the page'); return; }
        if (r.error || !r.ok) result(false, r.error || 'invalid recipe');
        // ok + pending: wait for import_result (the user's Import / Discard click)
      });
    } catch (err) { result(false, 'AWOS Replay was updated; reload the page'); }
  });
})();
