// /pro/welcome/: claim the licence key for the transaction in ?txn= (or
// Paddle's ?_ptxn=). The server confirms the payment with Paddle itself;
// while the webhook has not landed yet it answers 202 and we poll.
(function () {
  var status = document.getElementById('w-status');
  var box = document.getElementById('w-key');
  var cmd = document.getElementById('w-command');
  var copy = document.getElementById('w-copy');
  if (!status) return;
  var params = new URLSearchParams(window.location.search);
  var txn = params.get('txn') || params.get('_ptxn') || '';
  var tries = 0, MAX_TRIES = 25, DELAY_MS = 3000;

  function say(text, cls) {
    status.className = 'status' + (cls ? ' ' + cls : '');
    status.textContent = text;
  }

  var MESSAGES = {
    unknown_transaction: 'We could not find that transaction. Use the link from the checkout, or contact us with your receipt.',
    not_awos: 'That transaction is not an AWOS Pro purchase.',
    already_claimed: 'This key was already shown. Run `awos licence status` on a machine where you activated it, or contact us with your receipt.',
    rate_limited: 'Too many tries for this transaction. Wait an hour and reload.',
    not_configured: 'Licences are not switched on yet. Contact us with your receipt.'
  };

  function show(key, command) {
    cmd.textContent = command || ('awos activate ' + key);
    box.hidden = false;
    say('Payment confirmed. AWOS Pro is ready.', 'ok');
  }

  function poll() {
    tries += 1;
    fetch('/api/license/claim?txn=' + encodeURIComponent(txn), { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (b) { return [r.status, b]; }); })
      .then(function (res) {
        var code = res[0], body = res[1] || {};
        if (code === 200 && body.key) return show(body.key, body.command);
        if ((code === 202 || code === 502) && tries < MAX_TRIES) {
          say('Waiting for Paddle to confirm the payment… (' + tries + ')');
          return setTimeout(poll, DELAY_MS);
        }
        if (code === 202 || code === 502) {
          return say('Paddle has not confirmed the payment yet. Reload this page in a few minutes; the key will appear here.', 'err');
        }
        say(MESSAGES[body.status] || 'Something went wrong. Reload the page, or contact us with your receipt.', 'err');
      })
      .catch(function () {
        if (tries < MAX_TRIES) return setTimeout(poll, DELAY_MS);
        say('Could not reach the server. Check your connection and reload.', 'err');
      });
  }

  if (copy) {
    copy.addEventListener('click', function () {
      if (navigator.clipboard) navigator.clipboard.writeText(cmd.textContent).then(function () {
        copy.textContent = 'Copied';
      });
    });
  }

  if (!/^txn_[A-Za-z0-9]{10,64}$/.test(txn)) {
    say('This page needs the transaction id from checkout. Start from AWOS Pro, or contact us with your receipt.', 'err');
    return;
  }
  poll();
})();
