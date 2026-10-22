/* AWOS MiniWoB++ shim (our own code, injected by scaffold/rl/miniwob/server.py).
 *
 * MiniWoB tasks normally wait for a click on the "START" cover and are started from
 * Python via JS (core.startEpisodeReal()). AWOS's harness only acts through the
 * accessibility tree, so this shim:
 *   1. after the task's own window.onload, seeds Math.random (Math.seedrandom(seed), the
 *      same call MiniWoB's own Python env makes) and starts the episode itself;
 *   2. makes the episode timer effectively infinite (rewards stay ~= raw reward);
 *   3. hides the reward HUD and the click canvas (not part of the task);
 *   4. reports {start, utterance} and {end, reward, raw_reward, reason} to the server
 *      (POST /__awos/event); the first end of the episode is final, and the START cover
 *      is turned into an inert "Episode finished." panel so no new episode can begin;
 *   5. (awos_aria != 0) applies a small ARIA patch: elements with a JS click handler and
 *      no native role get role=button (name from their text), icon-only controls get a
 *      label from a fixed class map, and MiniWoB's fake links (span.alink) get role=link.
 *      Without it those controls are invisible to any accessibility-tree agent.
 * Query params (set by the server): awos_seed, awos_ep, awos_aria.
 */
(function () {
  'use strict';
  var P = new URLSearchParams(window.location.search);
  var EP = P.get('awos_ep') || '';
  var SEED_RAW = P.get('awos_seed');
  var ARIA = P.get('awos_aria') !== '0';
  var started = false, ended = false;

  function post(obj) {
    obj.ep = EP;
    try {
      fetch('/__awos/event', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(obj), keepalive: true
      });
    } catch (e) { /* the server may be gone at teardown */ }
  }

  function utterance() {
    // Some tasks override core.getUtterance() to return {utterance, fields}.
    var u;
    try { u = core.getUtterance(); } catch (e) { u = null; }
    if (u && typeof u === 'object') u = u.utterance;
    if (typeof u !== 'string' || !u) {
      var q = document.getElementById('query');
      u = q ? q.textContent : '';
    }
    return u.replace(/\s+/g, ' ').trim();
  }

  // ------------------------------------------------------------ ARIA patch --
  var ICONS = [
    // email-inbox family (icons are CSS content:url images on empty spans)
    ['.email-actions span.trash', 'button', 'Delete'],
    ['.email-actions span.star', 'button', 'Star'],
    ['.email-reply', 'button', 'Reply'],
    ['.email-forward', 'button', 'Forward'],
    ['#close-email', 'button', 'Back to inbox'],
    ['#close-reply', 'button', 'Discard reply'],
    ['#close-forward', 'button', 'Discard forward'],
    ['#send-reply', 'button', 'Send'],
    ['#send-forward', 'button', 'Send'],
    ['#open-search', 'button', 'Search'],
    ['#search-cancel', 'button', 'Cancel search'],
    // social-media family
    ['.controls .reply', 'button', 'Reply'],
    ['.controls .retweet', 'button', 'Retweet'],
    ['.controls .like', 'button', 'Like'],
    ['.controls .more', 'button', 'More'],
    // click-link: words wrapped in span.alink act as links
    ['.alink', 'link', null]
  ];
  var NATIVE = /^(A|BUTTON|INPUT|SELECT|TEXTAREA|LABEL|OPTION|SUMMARY|BODY|HTML)$/;
  var SKIP_IDS = { 'wrap': 1, 'area': 1, 'query': 1, 'sync-task-cover': 1, 'reward-display': 1, 'click-canvas': 1 };
  var INTERACTIVE_DESC = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=tab],[role=menuitem],[role=option]';

  function hasClick(el) {
    if (typeof el.onclick === 'function') return true;
    if (el.__onclick) return true;                                   // d3 v3
    if (el.__on) {                                                   // d3 v4
      for (var i = 0; i < el.__on.length; i++) if (el.__on[i].type === 'click') return true;
    }
    if (window.jQuery && jQuery._data) {
      var ev = jQuery._data(el, 'events');
      if (ev && (ev.click || ev.mousedown || ev.mouseup)) {
        var lst = (ev.click || []).concat(ev.mousedown || [], ev.mouseup || []);
        for (var j = 0; j < lst.length; j++) if (!lst[j].selector) return true;
      }
    }
    return false;
  }

  function delegatedTargets() {
    // jQuery delegated handlers: $(x).on('click', '.selector', fn)
    var out = [];
    if (!(window.jQuery && jQuery._data)) return out;
    var holders = [document].concat(Array.prototype.slice.call(document.querySelectorAll('*')));
    holders.forEach(function (h) {
      var ev = jQuery._data(h, 'events');
      if (!ev || !ev.click) return;
      ev.click.forEach(function (hd) {
        if (!hd.selector) return;
        try {
          var root = h === document ? document : h;
          Array.prototype.forEach.call(root.querySelectorAll(hd.selector), function (n) { out.push(n); });
        } catch (e) { /* jQuery-only selector syntax */ }
      });
    });
    return out;
  }

  function mark(el, role, label) {
    if (el.getAttribute('data-awos-aria')) return;
    if (el.closest && el.closest('svg')) return;
    el.setAttribute('role', role);
    if (label) el.setAttribute('aria-label', label);
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    el.setAttribute('data-awos-aria', '1');
  }

  function patch() {
    if (!ARIA) return;
    var area = document.getElementById('wrap') || document.body;
    ICONS.forEach(function (row) {
      Array.prototype.forEach.call(area.querySelectorAll(row[0]), function (el) { mark(el, row[1], row[2]); });
    });
    var cands = Array.prototype.slice.call(area.querySelectorAll('*')).filter(hasClick).concat(delegatedTargets());
    // Deepest first, so an inner clickable is named before its clickable container is
    // considered (the container then sees an interactive descendant and is left alone).
    cands.sort(function (a, b) {
      if (a === b) return 0;
      return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? 1 : -1;
    });
    cands.forEach(function (el) {
      if (!area.contains(el) || SKIP_IDS[el.id]) return;
      if (el.tagName === 'A' && !el.hasAttribute('href') && !el.hasAttribute('role')) {
        // <a> without href has no link role (e.g. phone-book's address/phone/email links)
        if ((el.textContent || '').trim()) mark(el, 'link', null);
        return;
      }
      if (NATIVE.test(el.tagName)) return;
      if (el.hasAttribute('role') || el.getAttribute('data-awos-aria')) return;
      if (el.closest && el.closest('svg')) return;
      if (el.querySelector(INTERACTIVE_DESC)) {
        // A role=button container would hide the real controls inside it. Expose its
        // first text-bearing child instead (a click there bubbles to el's handler),
        // e.g. the sender/subject block of an email row that also holds star/delete icons.
        for (var k = 0; k < el.children.length; k++) {
          var ch = el.children[k];
          if (NATIVE.test(ch.tagName) || ch.hasAttribute('role')) continue;
          if (ch.querySelector(INTERACTIVE_DESC)) continue;
          if (!(ch.textContent || '').trim()) continue;
          mark(ch, 'button', null);
          break;
        }
        return;
      }
      if (!(el.textContent || '').trim() && !el.getAttribute('aria-label')) return;   // nothing to name it by
      mark(el, 'button', null);
    });
  }

  var pending = false;
  function schedulePatch() {
    if (pending) return;
    pending = true;
    setTimeout(function () { pending = false; patch(); }, 0);
  }

  // ------------------------------------------------------------- episode --
  function finishCover() {
    var c = core.cover_div;
    if (!c) return;
    c.onclick = null;
    c.innerHTML = 'Episode finished.';
    c.style.display = 'block';
  }

  function begin() {
    if (started) return;
    started = true;
    core.EPISODE_MAX_TIME = 1000000000;   // ~11.6 days: the timer never fires in practice
    var origEnd = core.endEpisode;
    core.endEpisode = function (reward, timeProportional, reason) {
      var running = core.EP_TIMER !== null;
      var r = origEnd.apply(this, arguments);
      if (running && !ended) {
        ended = true;
        post({
          type: 'end', reward: WOB_REWARD_GLOBAL, raw_reward: WOB_RAW_REWARD_GLOBAL,
          reason: WOB_REWARD_REASON == null ? null : String(WOB_REWARD_REASON),
          done: WOB_DONE_GLOBAL, utterance: utterance()
        });
        finishCover();
      }
      return r;
    };
    if (SEED_RAW !== null && SEED_RAW !== '') {
      var n = Number(SEED_RAW);
      Math.seedrandom(String(n) === SEED_RAW ? n : SEED_RAW);   // numbers like MiniWoB's Python env
    }
    core.startEpisodeReal();
    try { core.hideDisplay(); } catch (e) { }
    patch();
    if (ARIA && window.MutationObserver) {
      new MutationObserver(schedulePatch).observe(document.body, { childList: true, subtree: true });
    }
    post({ type: 'start', seed: SEED_RAW, utterance: utterance(), title: document.title });
    window.AWOS_MINIWOB = { ep: EP, started: true, utterance: utterance() };
  }

  var style = document.createElement('style');
  // Icon spans swap their content:url image on :hover; until the hover image loads the
  // box collapses to 0x0, so a real mouse press lands on the parent and the click is lost.
  // A minimum box keeps the hit target stable (no visual change otherwise).
  style.textContent = '#reward-display,#click-canvas{display:none !important}' +
    '.controls .reply,.controls .retweet,.controls .like,.controls .more,' +
    '.email-actions span,.email-reply .icon,.email-forward .icon' +
    '{display:inline-block;min-width:12px;min-height:12px}';
  (document.head || document.documentElement).appendChild(style);

  // Run after the task's own window.onload (which calls core.startEpisode()).
  window.addEventListener('load', function () { setTimeout(begin, 0); });
})();
