# AWOS site

The landing page, docs and early-access form for the AWOS background coding agent.

- `index.html` is the page body source.
- `build.py` wraps it into `public/index.html` for static hosting.
- `functions/api/request-access.js` is a Cloudflare Pages Function. It stores form submissions in a KV namespace and lists them for the owner.

## Deploy (Cloudflare Pages, free tier)

1. Build the page:

   ```
   python3 site/build.py
   ```

2. Create the KV namespace:

   ```
   npx wrangler kv namespace create ACCESS_REQUESTS
   ```

3. Create the Pages project:

   ```
   npx wrangler pages project create awos-site
   ```

4. Bind KV and set the admin token. In the Cloudflare dashboard, open Pages → awos-site → Settings → Functions:
   - add a KV binding `ACCESS_REQUESTS`;
   - add a secret `ADMIN_TOKEN` (any long random string).

5. Deploy:

   ```
   npx wrangler pages deploy site/public --project-name awos-site
   ```

   The functions directory is picked up from `site/functions` when you deploy from `site/`. You can also deploy with `cd site && npx wrangler pages deploy public`.

6. Optional: attach a custom domain in Pages → Custom domains.

Read the requests:

```
curl "https://<your-site>/api/request-access?token=$ADMIN_TOKEN"
```

Every number on the page must stay traceable to a run in `docs/memory/runs/`. Update the page when results change.
