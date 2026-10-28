// /pro/: Paddle overlay checkout. Nothing about Paddle is in the static page:
// the client token and price ID come from /api/paddle/config, which answers
// {enabled:false} until fulfilment (webhook + licence store) is configured.
// Paddle.js is loaded only when checkout is enabled.
(function () {
  var button = document.getElementById('pro-buy');
  var status = document.getElementById('pro-status');
  var priceTag = document.getElementById('pro-price');
  if (!button) return;
  var PADDLE_JS = 'https://cdn.paddle.com/paddle/v2/paddle.js';

  function say(text, cls) {
    status.className = 'status' + (cls ? ' ' + cls : '');
    status.textContent = text;
  }

  function loadPaddle() {
    return new Promise(function (resolve, reject) {
      if (window.Paddle) return resolve(window.Paddle);
      var s = document.createElement('script');
      s.src = PADDLE_JS;
      s.async = true;
      s.onload = function () { window.Paddle ? resolve(window.Paddle) : reject(new Error('Paddle.js')); };
      s.onerror = function () { reject(new Error('Paddle.js')); };
      document.head.appendChild(s);
    });
  }

  fetch('/api/paddle/config', { headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : { enabled: false }; })
    .catch(function () { return { enabled: false }; })
    .then(function (cfg) {
      if (!cfg || !cfg.enabled || !cfg.client_token || !cfg.price_id) {
        say('Checkout is not open yet. Request access and we will email you when it is.');
        return;
      }
      return loadPaddle().then(function (Paddle) {
        if (cfg.env === 'sandbox') Paddle.Environment.set('sandbox');
        Paddle.Initialize({
          token: cfg.client_token,
          eventCallback: function (ev) {
            if (ev && ev.name === 'checkout.completed' && ev.data && ev.data.transaction_id) {
              window.location.href = '/pro/welcome/?txn=' + encodeURIComponent(ev.data.transaction_id);
            }
          }
        });
        var items = [{ priceId: cfg.price_id, quantity: 1 }];
        if (Paddle.PricePreview) {
          Paddle.PricePreview({ items: items }).then(function (res) {
            var line = res && res.data && res.data.details && res.data.details.lineItems && res.data.details.lineItems[0];
            if (line && line.formattedTotals && line.formattedTotals.total) {
              var cyc = line.price && line.price.billingCycle;
              priceTag.textContent = line.formattedTotals.total + (cyc && cyc.interval
                ? ' / ' + (cyc.frequency > 1 ? cyc.frequency + ' ' + cyc.interval + 's' : cyc.interval) : '');
            }
          }).catch(function () {});
        }
        button.disabled = false;
        button.textContent = 'Get AWOS Pro';
        if (cfg.env === 'sandbox') say('Sandbox checkout: use a Paddle test card. No real charge.');
        button.addEventListener('click', function () {
          Paddle.Checkout.open({ items: items, settings: { displayMode: 'overlay' } });
        });
      });
    })
    .catch(function () {
      say('Could not load the checkout. Check your connection or ad blocker and reload.', 'err');
    });
})();
