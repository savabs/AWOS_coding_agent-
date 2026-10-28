"""Build the AWOS static site: site/src/** -> site/public/**.

    python3 site/build.py            # build
    SITE_URL=https://example.org python3 site/build.py   # absolute URLs for sitemap/canonical

Layout of site/src/:
  partials/head.html, header.html, footer.html   shared chrome ({{title}} etc. filled per page)
  pages/**.html                                  one file per page, front matter first
  assets/**                                      copied to public/assets/ as-is

Each page starts with a front-matter comment:

  <!--meta
  title: Quickstart · AWOS docs
  description: One sentence for search results.
  layout: docs          (docs = sidebar + pager; default = plain)
  section: docs         (header link to mark as current: docs, security, access)
  script: /assets/access.js   (optional, a same-origin script)
  module: yes           (optional: load the script as an ES module)
  -->

Pages may also use {{replay_install}} (the AWOS Replay install command from
src/replay_install.txt) and <!--RECIPE_CARDS--> or <!--RECIPE_CARDS:3--> (cards
for the example recipes in src/recipes/examples.json, each linking to
/r/#<base64url(deflate-raw(JSON))>, the fragment the /r/ page decodes).

Output paths: pages/index.html -> /index.html, pages/404.html -> /404.html,
pages/x.html -> /x/index.html, pages/docs/index.html -> /docs/index.html,
pages/docs/y.html -> /docs/y/index.html. Links in pages are root-absolute
("/docs/quickstart/"), which is what Cloudflare Pages serves.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
OUT = HERE / "public"
# Cloudflare Pages default domain for the project named in site/README.md.
SITE_URL = os.environ.get("SITE_URL", "https://awos.tirramind.com").rstrip("/")
# AWOS Replay install command shown on / and /r/. Packaging writes the real
# command into src/replay_install.txt; until then it is the placeholder
# INSTALL_COMMAND (check_site.py reports it; check_site.py --release fails on it).
REPLAY_INSTALL_FILE = SRC / "replay_install.txt"
RECIPES_FILE = SRC / "recipes" / "examples.json"
RECIPE_MAX_BYTES = 64 * 1024

# Docs sidebar order: (url, label).
DOCS = [
    ("/docs/", "Overview"),
    ("/docs/quickstart/", "Quickstart"),
    ("/docs/fix-github/", "Fix a GitHub issue or PR"),
    ("/docs/concepts/", "Concepts"),
    ("/docs/models-and-cost/", "Models and cost"),
    ("/docs/cli/", "CLI reference"),
    ("/docs/security/", "Security and privacy"),
    ("/docs/faq/", "FAQ"),
]

META_RE = re.compile(r"\A\s*<!--meta\s*\n(.*?)\n-->\s*\n", re.S)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def parse(page: Path) -> tuple[dict, str]:
    text = read(page)
    m = META_RE.match(text)
    if not m:
        raise SystemExit(f"{page}: missing <!--meta ... --> front matter")
    meta = {}
    for line in m.group(1).splitlines():
        if line.strip():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    for key in ("title", "description"):
        if not meta.get(key):
            raise SystemExit(f"{page}: front matter needs '{key}'")
    return meta, text[m.end():]


def url_for(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "index":
        parts = parts[:-1]
        return "/" + "".join(p + "/" for p in parts)
    if parts == ["404"]:
        return "/404.html"
    return "/" + "/".join(parts) + "/"


def out_path(url: str) -> Path:
    if url.endswith(".html"):
        return OUT / url.lstrip("/")
    return OUT / url.lstrip("/") / "index.html"


def docs_shell(url: str, body: str) -> str:
    items = []
    for href, label in DOCS:
        cur = ' aria-current="page"' if href == url else ""
        items.append(f'<li><a href="{href}"{cur}>{html.escape(label)}</a></li>')
    side = ('<nav class="docs-side" aria-label="Docs">\n<h2>Docs</h2>\n<ul>\n'
            + "\n".join(items) + "\n</ul>\n</nav>")
    urls = [h for h, _ in DOCS]
    pager = ""
    if url in urls:
        i = urls.index(url)
        links = []
        if i > 0:
            links.append(f'<a class="prev" href="{DOCS[i-1][0]}"><small>Previous</small>{html.escape(DOCS[i-1][1])}</a>')
        if i < len(DOCS) - 1:
            links.append(f'<a class="next" href="{DOCS[i+1][0]}"><small>Next</small>{html.escape(DOCS[i+1][1])}</a>')
        pager = '<nav class="pager" aria-label="Docs pages">' + "".join(links) + "</nav>"
    return (f'<main id="main" class="wrap"><div class="docs-layout">\n{side}\n'
            f'<article class="doc">\n{body.rstrip()}\n{pager}\n</article>\n</div></main>\n')


def recipe_fragment(recipe: dict) -> str:
    """base64url(deflate-raw(JSON)) without padding: what /r/ decodes from location.hash."""
    raw = json.dumps(recipe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > RECIPE_MAX_BYTES:
        raise SystemExit(f"recipe {recipe.get('name')!r} is over {RECIPE_MAX_BYTES} bytes")
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = co.compress(raw) + co.flush()
    return base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def load_recipes() -> list[dict]:
    items = json.loads(read(RECIPES_FILE))
    for it in items:
        r = it["recipe"]
        if r.get("awos_recipe") != 1 or not r.get("name") or not r.get("steps"):
            raise SystemExit(f"{RECIPES_FILE}: {it.get('slug')}: not a v1 recipe")
        it["href"] = "/r/#" + recipe_fragment(r)
    return items


def recipe_cards(items: list[dict], ids: bool) -> str:
    out = ['<div class="cards recipes">']
    for it in items:
        r = it["recipe"]
        host = re.sub(r"^https?://([^/]+).*$", r"\1", r["start_url"])
        n = len(r["steps"])
        meta = f"{html.escape(it.get('tag', ''))} · {html.escape(host)} · {n} step{'s' if n != 1 else ''}"
        idattr = f' id="recipe-{html.escape(it["slug"])}"' if ids else ""
        out.append(f'<a class="card"{idattr} href="{it["href"]}">'
                   f'<small class="eyebrow">{meta}</small><b>{html.escape(r["name"])}</b>'
                   f'<span>{html.escape(r.get("description", ""))}</span></a>')
    out.append("</div>")
    return "\n".join(out)


CARDS_RE = re.compile(r"<!--RECIPE_CARDS(?::(\d+))?-->")


def fill(body: str, recipes: list[dict], install: str) -> str:
    def cards(m: re.Match) -> str:
        n = m.group(1)
        return recipe_cards(recipes[: int(n)] if n else recipes, ids=not n)
    body = CARDS_RE.sub(cards, body)
    return body.replace("{{replay_install}}", html.escape(install))


def mark_section(header: str, section: str) -> str:
    def sub(m: re.Match) -> str:
        cur = ' aria-current="page"' if m.group(1) == section else ""
        return cur
    return re.sub(r' data-section="([^"]+)"', sub, header)


def build() -> list[str]:
    if OUT.exists():
        for child in OUT.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    OUT.mkdir(exist_ok=True)
    shutil.copytree(SRC / "assets", OUT / "assets")
    shutil.copy2(SRC / "install.sh", OUT / "install.sh")  # curl -fsSL <site>/install.sh | sh
    shutil.copy2(SRC / "_headers", OUT / "_headers")      # Cloudflare Pages response headers
    recipes = load_recipes()
    install = (read(REPLAY_INSTALL_FILE).strip() if REPLAY_INSTALL_FILE.exists() else "") or "INSTALL_COMMAND"

    head_t = read(SRC / "partials" / "head.html")
    header_t = read(SRC / "partials" / "header.html")
    footer_t = read(SRC / "partials" / "footer.html")

    built, sitemap = [], []
    for page in sorted((SRC / "pages").rglob("*.html")):
        rel = page.relative_to(SRC / "pages")
        meta, body = parse(page)
        body = fill(body, recipes, install)
        url = url_for(rel)
        is_404 = url == "/404.html"
        head = (head_t.replace("{{title}}", html.escape(meta["title"]))
                .replace("{{description}}", html.escape(meta["description"]))
                .replace("{{canonical}}", SITE_URL + url)
                .replace("{{robots}}", "noindex" if is_404 else "index,follow"))
        header = mark_section(header_t, meta.get("section", ""))
        if meta.get("layout") == "docs":
            main = docs_shell(url, body)
        else:
            main = f'<main id="main" class="wrap">\n{body.rstrip()}\n</main>\n'
        if not meta.get("script"):
            script = ""
        elif meta.get("module") == "yes":
            script = f'<script type="module" src="{meta["script"]}"></script>\n'
        else:
            script = f'<script src="{meta["script"]}" defer></script>\n'
        doc = head + header + main + script + footer_t
        dest = out_path(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(doc, encoding="utf-8")
        built.append(url)
        if not is_404:
            sitemap.append(url)

    entries = "\n".join(f"  <url><loc>{SITE_URL}{u}</loc></url>" for u in sorted(sitemap))
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n", encoding="utf-8")
    (OUT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: {SITE_URL}/sitemap.xml\n",
        encoding="utf-8")
    return built


if __name__ == "__main__":
    pages = build()
    print(f"built {len(pages)} pages into {OUT}")
    for u in pages:
        print("  ", u)
