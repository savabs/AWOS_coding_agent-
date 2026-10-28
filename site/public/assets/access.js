// Early-access form: POST JSON to the endpoint in data-endpoint, show inline status.
(function () {
  var form = document.getElementById('access-form');
  if (!form) return;
  var status = document.getElementById('f-status');
  var EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var name = form.elements.name.value.trim(), email = form.elements.email.value.trim();
    status.className = 'status';
    if (!name || !EMAIL.test(email)) {
      status.className = 'status err';
      status.textContent = 'Add your name and a valid email so we can reach you.';
      (name ? form.elements.email : form.elements.name).focus();
      return;
    }
    var data = {
      name: name, email: email,
      role: form.elements.role.value, team: form.elements.team.value,
      tickets: form.elements.tickets.value.trim(), tools: form.elements.tools.value.trim(),
      ts: new Date().toISOString()
    };
    var button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    status.textContent = 'Sending…';
    fetch(form.getAttribute('data-endpoint'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) throw new Error(body.error || ('Request failed (' + r.status + ').'));
        });
      })
      .then(function () {
        status.className = 'status ok';
        status.textContent = 'Thanks, ' + name + '. We will email ' + email + ' about early access.';
        form.reset();
      })
      .catch(function (err) {
        status.className = 'status err';
        status.textContent = (err && err.message && !/fetch|network/i.test(err.message))
          ? err.message
          : 'Could not send your request. Check your connection and try again.';
      })
      .then(function () { button.disabled = false; });
  });
})();
