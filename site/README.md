# AWOS site

The landing page, docs and early-access form for the AWOS background coding agent.

Plain HTML and CSS, no framework, no external JS.

- `src/partials/` holds the shared head, header and footer.
- `src/pages/` has one file per page. Each starts with a `<!--meta ... -->` block (title, description, layout, section, optional script). `pages/docs/*.html` use the docs layout, which adds the sidebar and previous/next links.
- `src/assets/` has `style.css` (the design tokens, light and dark themes) and `access.js` (the request-access form). It is copied to `public/assets/`.
- `build.py` assembles everything into `public/**` (`/`, `/docs/...`, `/access/`, `404.html`) and writes `sitemap.xml` and `robots.txt`. Set `SITE_URL` for canonical and sitemap URLs (default `https://awos-site.pages.dev`).
- `check_site.py` checks the built site: every internal link and `#anchor` resolves, tags nest correctly, each page has a title, a meta description and one `<h1>`, and the `INSTALL_COMMAND` placeholder in the quickstart appears exactly once.
- `functions/api/request-access.js` is a Cloudflare Pages Function. It stores form submissions in a KV namespace and lists them for the owner.

Build and check locally:

```
python3 site/build.py && python3 site/check_site.py
python3 -m http.server 8000 -d site/public   # preview at http://localhost:8000/
```

The quickstart has one placeholder, `INSTALL_COMMAND`, in `src/pages/docs/quickstart.html`. Replace it with the real install command once packaging is ready.

## Deploy (Cloudflare Pages, free tier)

1. Build and check the site:

   ```
   python3 site/build.py && python3 site/check_site.py
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
   cd site && npx wrangler pages deploy public
   ```

   The functions directory is picked up from `site/functions` when you deploy from `site/`. You can also deploy with `cd site && npx wrangler pages deploy public`.

6. Optional: attach a custom domain in Pages → Custom domains.

Read the requests (early-access form and the home-page waitlist share KV `ACCESS_REQUESTS` and `ADMIN_TOKEN`):

```
curl "https://<your-site>/api/request-access?token=$ADMIN_TOKEN"                        # early-access requests + waitlist_count
curl "https://<your-site>/api/request-access?token=$ADMIN_TOKEN&view=waitlist"          # counts by segment/role/why/country + entries
curl -o waitlist.csv "https://<your-site>/api/request-access?token=$ADMIN_TOKEN&view=waitlist&format=csv"
```

Waitlist records (`wl:e:<email>` or `wl:a:<ts>:<rand>`): `{form, segment, role, why, note, email, src, ts, country}` (`src` = `?src=` on the landing URL: hn, localllama, devindia, x, interview, other).
`role` is one of solo_dev, team_dev, founder, researcher, other; `why` one of privacy, cost, offline, speed,
curiosity, other; `segment` is derived from both (privacy_team, privacy_individual, cost_sensitive, local_builder,
unclassified). Discovery plan: `docs/launch/discovery/`.

`USAGE` and `LICENSES` KV bindings are optional; see `docs/launch/paddle_setup.md` section 3.

Every number on the page must stay traceable to a run in `docs/memory/runs/`. Update the page when results change.
