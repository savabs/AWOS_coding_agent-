/* AWOS-Ladder fixture events (v0.12): every click / change / submit on a fixture page is sent to the ladder's
 * server (POST /__event) so the runner grades from what happened, not from what the model said.
 * An element's id for grading: data-ev, else id, else name. data-risky marks a control the owner must approve.
 * A form with data-stay does not navigate: it shows its data-done text in #out instead.
 * A control inside [data-blocked-by=<popup id>] does nothing while that pop-up is shown (recorded as "<id>:blocked"). */
(function () {
  function send(ev) {
    ev.page = location.pathname;
    try { navigator.sendBeacon('/__event', JSON.stringify(ev)); } catch (e) {}
  }
  function idOf(el) {
    return el.getAttribute('data-ev') || el.id || el.getAttribute('name') || (el.textContent || '').trim().slice(0, 40);
  }
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest && e.target.closest('[data-ev], button, a, [role=button], [role=tab], [role=menuitem], input[type=checkbox], input[type=radio], input[type=submit], summary');
    if (!el) return;
    var blk = el.closest('[data-blocked-by]');
    if (blk) {
      var by = document.getElementById(blk.getAttribute('data-blocked-by'));
      if (by && !by.hidden) { e.preventDefault(); e.stopImmediatePropagation(); send({ type: 'click', id: idOf(el) + ':blocked' }); return; }
    }
    send({ type: 'click', id: idOf(el), risky: el.hasAttribute('data-risky') });
  }, true);
  document.addEventListener('change', function (e) {
    var el = e.target;
    if (!el || !/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return;
    var chk = el.type === 'checkbox' || el.type === 'radio';
    send({ type: 'change', id: idOf(el), value: chk ? el.checked : el.value });
  }, true);
  document.addEventListener('submit', function (e) {
    var f = e.target, vals = {};
    new FormData(f).forEach(function (v, k) { vals[k] = String(v); });
    send({ type: 'submit', id: idOf(f), values: vals });
    if (f.hasAttribute('data-stay')) {
      e.preventDefault();
      var out = document.getElementById('out');
      if (out) out.textContent = f.getAttribute('data-done') || 'Saved';
    }
  }, true);
  window.ladderEvent = send;
})();
