# AWOS site

The site for **AWOS Replay** ("show it once, it does it forever": a Chrome extension that records a web chore once, replays it and checks it worked) at `/`, plus the AWOS coding agent at `/coding-agent/` with its docs under `/docs/`.

Plain HTML and CSS, no framework, no external JS.

- `src/partials/` holds the shared head, header and footer.
- `src/pages/` has one file per page. Each starts with a `<!--meta ... -->` block (title, description, layout, section, optional script, optional `module: yes`). `pages/docs/*.html` use the docs layout, which adds the sidebar and previous/next links.
  - `index.html`: AWOS Replay landing (how it works, demo video slot, free/Pro, install, waitlist).
  - `recipes.html` (`/recipes/`): gallery of the 6 example recipes in `src/recipes/examples.json` (fictional `example.com` sites).
  - `r.html` (`/r/`): the shared-recipe page. See "Recipe links" below.
  - `coding-agent.html` (`/coding-agent/`): the previous home page, unchanged apart from its title.
  - `replay/privacy.html` (`/replay/privacy/`): the extension's privacy policy (the Chrome Web Store listing links here). `EFFECTIVE_DATE` and `CONTACT_EMAIL` are placeholders.
- `src/assets/` has `style.css` (the design tokens, light and dark themes), the form scripts (`access.js`, `waitlist.js`, `replay-waitlist.js`, `pro.js`) and the recipe code (`recipe-core.js`, `recipe-page.js`). It is copied to `public/assets/`.
- `src/replay_install.txt`: the AWOS Replay install command shown on `/` and `/r/`. It installs from `main`, so it works only after `scripts/install_replay.sh` is merged there.
- `src/_headers`: Cloudflare Pages headers (a strict CSP and `no-referrer` on `/r/`).
- `build.py` assembles everything into `public/**` and writes `sitemap.xml` and `robots.txt`. It fills `{{replay_install}}` and `<!--RECIPE_CARDS-->` and encodes each example recipe into its `/r/#...` link. Set `SITE_URL` for canonical and sitemap URLs (default `https://awos.tirramind.com`). Recipe share links are `https://awos.tirramind.com/r/#<payload>`; the `/r/` page only uses `location.origin`, so it works the same on `awos-site.pages.dev` and `localhost:8000`.
- `check_site.py` checks the built site: every internal link and `#anchor` resolves, every `/r/#...` link decodes to a valid v1 recipe under 64 KB, `/recipes/` has 6 of them, no off-site `<script src>`, the recipe scripts never use `innerHTML`, tags nest correctly, each page has a title, a meta description and one `<h1>`. Launch items still missing (the `INSTALL_COMMAND` placeholder, the demo video, the privacy policy date and contact) are listed as PENDING; `check_site.py --release` fails on them.
- `functions/api/request-access.js` is a Cloudflare Pages Function. It stores form submissions in a KV namespace and lists them for the owner.
- `tests/`: `npm test` (`node --test`, Node >= 20, no dependencies): the Pages Functions and the recipe decode/validate/describe logic.

Build, check, test and preview locally:

```
python3 site/build.py && python3 site/check_site.py
cd site && npm test
python3 -m http.server 8000 -d site/public   # preview at http://localhost:8000/
```

Preview a recipe: open http://localhost:8000/recipes/ and click a card.

Before launch: fill `EFFECTIVE_DATE` and `CONTACT_EMAIL` in `src/pages/replay/privacy.html`, add the video at `src/assets/replay/demo.mp4` (poster: `demo-poster.svg`), rebuild, and run `python3 site/check_site.py --release`.

## Recipe links (`/r/`)

A recipe link is `/r/#<base64url(deflate-raw(JSON))>`, no padding. The fragment never reaches the server; `recipe-core.js` decodes it in the browser with `DecompressionStream("deflate-raw")`, refuses anything over 64 KB (also while inflating) or with `awos_recipe` other than `1`, validates the v1 contract and turns steps into plain sentences. `recipe-page.js` renders it with `textContent` only. To make a link elsewhere: `encodeRecipe(recipe)` in `recipe-core.js`, or `recipe_fragment()` in `build.py`.

Page and extension talk through `window.postMessage` on the `/r/` page, same window and same origin:

| Direction | Message | When |
|---|---|---|
| page → extension | `{type: "awos-replay-hello"}` | on load |
| extension → page | `{type: "awos-replay-present", version}` | content script loaded (it may also set `<html data-awos-replay="version">`) |
| page → extension | `{type: "awos-import-recipe", recipe}` | "Add to AWOS Replay" clicked; `recipe` is the validated copy |
| extension → page | `{type: "awos-import-result", ok, error?}` | after the user confirms or cancels |

If neither `present` nor a result arrives within 1.2 s of the click, the page shows the install box. The extension must validate the recipe again and ask the user before saving it: the page is not a trust boundary.

## Deploy (Cloudflare Pages, free tier)

1. Build and check the site:

   ```
   python3 site/build.py && python3 site/check_site.py --release
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

AWOS Replay waitlist (same KV, `rp:` keys, chore = segment):

```
curl "https://<your-site>/api/request-access?token=$ADMIN_TOKEN&view=replay"                 # counts by chore/pro/src/country + entries
curl -o replay.csv "https://<your-site>/api/request-access?token=$ADMIN_TOKEN&view=replay&format=csv"
```

Replay records (`rp:e:<email>` or `rp:a:<ts>:<rand>`): `{form:"replay", segment, chore, pro, note, email, src, ts, country}`; `chore` is one of bills_invoices, reports_exports, timesheets_forms, order_status, alerts_watch, data_entry, other; `pro` is yes, maybe, no or "".

Coding-agent waitlist records (`wl:e:<email>` or `wl:a:<ts>:<rand>`): `{form, segment, role, why, note, email, src, ts, country}` (`src` = `?src=` on the landing URL: hn, localllama, devindia, x, interview, ph, reddit, recipe, other).
`role` is one of solo_dev, team_dev, founder, researcher, other; `why` one of privacy, cost, offline, speed,
curiosity, other; `segment` is derived from both (privacy_team, privacy_individual, cost_sensitive, local_builder,
unclassified). Discovery plan: `docs/launch/discovery/`.

`USAGE` and `LICENSES` KV bindings are optional; see `docs/launch/paddle_setup.md` section 3.

Every number on the page must stay traceable to a run in `docs/memory/runs/`. Update the page when results change.
