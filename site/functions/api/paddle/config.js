// Cloudflare Pages Function: GET /api/paddle/config
// The ONLY place the checkout gets its Paddle client token and price ID.
// Returns {enabled:false} until fulfilment (webhook secret, API key, prices,
// KV LICENSES) is configured AND PADDLE_CHECKOUT_ENABLED=1, so no token or
// price ID ever sits in static HTML and checkout cannot open before a
// payment can be fulfilled (lesson: tirramind paddle_checkout_exposure.md).
import { json } from "../../../lib/common.js";
import { fulfilmentMissing, paddleEnv, priceIds } from "../../../lib/paddle.js";

export async function onRequestGet({ env }) {
  if (fulfilmentMissing(env).length) return json({ enabled: false });
  return json({
    enabled: true,
    env: paddleEnv(env),
    client_token: env.PADDLE_CLIENT_TOKEN,
    price_id: priceIds(env)[0],
  });
}
