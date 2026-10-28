// AWOS Replay waitlist: POST JSON {form:"replay", chore, pro, note, email, website, src}
// to data-endpoint and show an inline status. No third-party scripts, no cookies.
(function () {
  var form = document.getElementById('replay-form');
  if (!form) return;
  var status = document.getElementById('r-status');
  var EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  // ?src=hn etc. on the landing URL says which post sent you; the server keeps known values only.
  var src = '';
  try { src = (new URLSearchParams(window.location.search).get('src') || '').slice(0, 20); } catch (_) { src = ''; }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var el = form.elements;
    var chore = el.chore.value, pro = el.pro.value;
    var note = el.note.value.trim(), email = el.email.value.trim();
    status.className = 'status err';
    if (!chore) { status.textContent = 'Pick the chore you would hand off.'; el.chore.focus(); return; }
    if (note.length > 500) { status.textContent = 'Keep the description under 500 characters.'; el.note.focus(); return; }
    if (email && !EMAIL.test(email)) { status.textContent = 'That email does not look valid.'; el.email.focus(); return; }
    status.className = 'status';
    var button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = 'Sending…';
    fetch(form.getAttribute('data-endpoint'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ form: 'replay', chore: chore, pro: pro, note: note, email: email, website: el.website.value, src: src })
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) throw new Error(body.error || ('Request failed (' + r.status + ').'));
        });
      })
      .then(function () {
        status.className = 'status ok';
        status.textContent = email
          ? 'Thanks. You are on the list, and we will email ' + email + ' when Pro and scheduling open.'
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
