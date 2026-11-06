/* AWOS-Ladder level 2 (v0.12): start a MiniWoB++ episode on load with the fixed seed from ?seed=, without the cover
 * click and without the 10 s episode timer (the local model takes 5-19 s per step). window.LADDER_READY = started. */
(function () {
  var m = /[?&]seed=([^&]+)/.exec(location.search), seed = m ? decodeURIComponent(m[1]) : '1';
  function boot(tries) {
    if (typeof core === 'undefined' || typeof genProblem === 'undefined' || !document.getElementById('sync-task-cover')) {
      if (tries < 200) setTimeout(function () { boot(tries + 1); }, 50);
      return;
    }
    core.EPISODE_MAX_TIME = 3600 * 1000;
    Math.seedrandom(seed);
    core.startEpisodeReal();
    if (core.CD_TIMER) clearInterval(core.CD_TIMER);
    var cd = document.getElementById('timer-countdown'); if (cd) cd.textContent = '-';
    window.LADDER_READY = true;
  }
  window.addEventListener('load', function () { setTimeout(function () { boot(0); }, 30); });
})();
