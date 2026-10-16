// Local-first waitlist: POST JSON {form:"waitlist", role, why, note, email, website}
// to data-endpoint and show an inline status. No third-party scripts, no cookies.
(function () {
  var form = document.getElementById('waitlist-form');
  if (!form) return;
  var status = document.getElementById('w-status');
  var EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  // ?src=hn etc. on the landing URL says which post sent you; the server keeps known values only.
  var src = '';
  try { src = (new URLSearchParams(window.location.search).get('src') || '').slice(0, 20); } catch (_) { src = ''; }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var el = form.elements;
    var role = el.role.value, why = el.why.value;
    var note = el.note.value.trim(), email = el.email.value.trim();
    status.className = 'status err';
    if (!role) { status.textContent = 'Pick your role.'; el.role.focus(); return; }
    if (!why) { status.textContent = 'Pick why you want a local agent.'; el.why.focus(); return; }
    if (note.length > 500) { status.textContent = 'Keep the note under 500 characters.'; el.note.focus(); return; }
    if (email && !EMAIL.test(email)) { status.textContent = 'That email does not look valid.'; el.email.focus(); return; }
    status.className = 'status';
    var button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = 'Sending…';
    fetch(form.getAttribute('data-endpoint'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ form: 'waitlist', role: role, why: why, note: note, email: email, website: el.website.value, src: src })
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) throw new Error(body.error || ('Request failed (' + r.status + ').'));
        });
      })
      .then(function () {
        status.className = 'status ok';
        status.textContent = email
          ? 'Thanks. You are on the list, and we will email ' + email + ' when local results are out.'
          : 'Thanks. You are on the list.';
        form.reset();
      })
      .catch(function (err) {
        status.className = 'status err';
        status.textContent = (err && err.message && !/fetch|network/i.test(err.message))
          ? err.message
          : 'Could not send. Check your connection and try again.';
      })
      .then(function () { button.disabled = false; });
  });
})();
