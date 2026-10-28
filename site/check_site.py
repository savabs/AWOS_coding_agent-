"""Check the built site in site/public: internal links, fragments, HTML structure.

    python3 site/build.py && python3 site/check_site.py

Fails (exit 1) when:
  - an internal href/src does not resolve to a file in site/public
    ("/docs/x/" -> docs/x/index.html; "/a.css" -> a.css; relative links too);
  - a "#fragment" link points at an id that does not exist on the target page;
  - a page's tags do not nest/close properly (html.parser pass);
  - a page lacks <title>, meta description, one <h1>, or has duplicate ids;
  - a <script src> points off-site (no external JS, no CDNs);
  - a /r/#<fragment> recipe link does not decode (base64url + deflate-raw) to a
    valid v1 AWOS Replay recipe under 64 KB, or /recipes/ lacks its 6 examples;
  - the recipe page scripts use innerHTML/outerHTML/insertAdjacentHTML/
    document.write (recipes are untrusted input);
  - a required page or file is missing (/, /r/, /recipes/, /coding-agent/, _headers...).
Pending launch items are reported but pass, so the site can be built and
previewed before launch; `--release` (run before deploying) fails on them:
  - the INSTALL_COMMAND placeholder (fill site/src/replay_install.txt);
  - a file listed in PENDING_ASSETS that does not exist yet (the demo video);
  - the EFFECTIVE_DATE / CONTACT_EMAIL placeholders in the privacy policy.
External links (http/https/mailto) are listed, not fetched.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import zlib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

PUBLIC = Path(__file__).resolve().parent / "public"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}
# Elements whose end tag HTML lets you omit; we still require them closed except these.
OPTIONAL_END = {"p", "li", "option", "tr", "td", "th", "thead", "tbody"}
# Linked from the site but supplied by the owner before launch.
PENDING_ASSETS = {"/assets/replay/demo.mp4": "the demo video (see docs/launch/replay/demo_scripts.md)"}
REQUIRED = ("sitemap.xml", "robots.txt", "404.html", "_headers", "assets/style.css", "index.html",
            "r/index.html", "recipes/index.html", "coding-agent/index.html", "assets/recipe-core.js",
            "assets/recipe-page.js", "assets/replay/demo-poster.svg", "replay/privacy/index.html")
RECIPE_SCRIPTS = ("assets/recipe-core.js", "assets/recipe-page.js")
UNSAFE_DOM = re.compile(r"\b(innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval)\b|new Function")
RECIPE_MAX = 64 * 1024
EXAMPLE_RECIPES = 6


def recipe_problem(fragment: str) -> str | None:
    """None when the /r/ fragment holds a valid v1 recipe, else what is wrong."""
    try:
        packed = base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4))
        d = zlib.decompressobj(-15)
        raw = d.decompress(packed, RECIPE_MAX + 1)
        if len(raw) > RECIPE_MAX or d.unconsumed_tail:
            return "over 64 KB"
        r = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - any decode failure is the finding
        return f"does not decode ({e.__class__.__name__})"
    if not isinstance(r, dict) or r.get("awos_recipe") != 1:
        return "not an awos_recipe v1"
    if not isinstance(r.get("name"), str) or not r["name"]:
        return "no name"
    if not str(r.get("start_url", "")).startswith(("https://", "http://")):
        return "start_url is not http(s)"
    if not isinstance(r.get("steps"), list) or not r["steps"]:
        return "no steps"
    return None


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.links: list[str] = []
        self.ids: list[str] = []
        self.h1 = 0
        self.title = ""
        self.in_title = False
        self.description = ""
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(a["id"])
        # canonical/preconnect point at the deployed origin, not at files here
        skip = tag == "link" and a.get("rel") in ("preconnect", "canonical")
        for key in ("href", "src", "action"):
            if a.get(key) and not skip:
                self.links.append(a[key])
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        if tag == "meta" and a.get("name") == "description":
            self.description = a.get("content", "")
        if tag == "h1":
            self.h1 += 1
        if tag == "title":
            self.in_title = True
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in VOID:
            return
        # pop optional-end elements implicitly closed
        while self.stack and self.stack[-1][0] != tag and self.stack[-1][0] in OPTIONAL_END:
            self.stack.pop()
        if not self.stack or self.stack[-1][0] != tag:
            open_tag = self.stack[-1] if self.stack else None
            self.errors.append(f"line {self.getpos()[0]}: </{tag}> closes {open_tag}")
            return
        self.stack.pop()

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def target_file(url_path: str) -> Path:
    rel = url_path.lstrip("/")
    if rel == "" or rel.endswith("/"):
        return PUBLIC / rel / "index.html"
    return PUBLIC / rel


def page_url(f: Path) -> str:
    rel = f.relative_to(PUBLIC).as_posix()
    if rel.endswith("index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def main() -> int:
    if not PUBLIC.exists():
        print("site/public missing: run python3 site/build.py first")
        return 1
    pages = sorted(PUBLIC.rglob("*.html"))
    parsed: dict[Path, Page] = {}
    problems: list[str] = []
    for f in pages:
        p = Page()
        p.feed(f.read_text(encoding="utf-8"))
        p.close()
        parsed[f] = p
        rel = f.relative_to(PUBLIC)
        for e in p.errors:
            problems.append(f"{rel}: {e}")
        leftover = [t for t, _ in p.stack if t not in OPTIONAL_END]
        if leftover:
            problems.append(f"{rel}: unclosed tags {leftover}")
        if not p.title.strip():
            problems.append(f"{rel}: missing <title>")
        if not p.description.strip():
            problems.append(f"{rel}: missing meta description")
        if p.h1 != 1:
            problems.append(f"{rel}: has {p.h1} <h1> elements (want 1)")
        dupes = {i for i in p.ids if p.ids.count(i) > 1}
        if dupes:
            problems.append(f"{rel}: duplicate ids {sorted(dupes)}")

    checked, external, recipes_ok, pending = 0, set(), 0, []
    for f, p in parsed.items():
        base = page_url(f)
        rel = f.relative_to(PUBLIC)
        for src in p.scripts:
            if urlsplit(src).scheme or src.startswith("//"):
                problems.append(f"{rel}: external script {src} (no external JS)")
        for link in p.links:
            parts = urlsplit(link)
            if parts.scheme in ("http", "https", "mailto", "tel") or link.startswith("//"):
                external.add(link)
                continue
            if parts.scheme:
                problems.append(f"{rel}: odd link {link}")
                continue
            full = urlsplit(urljoin(base, link))
            if full.path.startswith("/api/"):
                continue  # Pages Function, not a static file
            checked += 1
            tgt = target_file(full.path)
            if not tgt.is_file():
                if full.path in PENDING_ASSETS:
                    pending.append(f"{rel}: {full.path} not added yet ({PENDING_ASSETS[full.path]})")
                    continue
                problems.append(f"{rel}: broken link {link} -> {tgt.relative_to(PUBLIC)}")
                continue
            tp = parsed.get(tgt)
            if full.path == "/r/" and full.fragment and not (tp and full.fragment in tp.ids):
                why = recipe_problem(full.fragment)
                if why:
                    problems.append(f"{rel}: recipe link {link[:40]}... {why}")
                else:
                    recipes_ok += 1
                continue
            if full.fragment and tgt.suffix == ".html":
                tp = parsed.get(tgt)
                if tp is not None and full.fragment not in tp.ids:
                    problems.append(f"{rel}: missing anchor {link}")

    gallery = parsed.get(PUBLIC / "recipes" / "index.html")
    if gallery is not None:
        n = len({l for l in gallery.links if l.startswith("/r/#")})
        if n != EXAMPLE_RECIPES:
            problems.append(f"recipes/index.html: {n} recipe links (want {EXAMPLE_RECIPES})")

    for js in RECIPE_SCRIPTS:
        f = PUBLIC / js
        if f.is_file():
            code = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("//"))
            m = UNSAFE_DOM.search(code)
            if m:
                problems.append(f"{js}: uses {m.group(0)} (recipes are untrusted; use textContent)")

    for token, fix in (("EFFECTIVE_DATE", "the effective date"), ("CONTACT_EMAIL", "a contact address")):
        n = sum(f.read_text(encoding="utf-8").count(token) for f in pages)
        if n:
            pending.append(f"{token} placeholder shown {n}x: put {fix} in src/pages/replay/privacy.html")

    hits = sum(f.read_text(encoding="utf-8").count("INSTALL_COMMAND") for f in pages)
    if hits:
        pending.append(f"INSTALL_COMMAND placeholder shown {hits}x: put the real command in site/src/replay_install.txt")

    for extra in REQUIRED:
        if not (PUBLIC / extra).is_file():
            problems.append(f"missing {extra}")

    print(f"{len(pages)} pages, {checked} internal links checked ({recipes_ok} recipe links decoded), "
          f"{len(external)} external links (not fetched)")
    for e in sorted(external):
        print("   external:", e)
    release = "--release" in sys.argv[1:]
    if pending:
        print(f"{'FAIL' if release else 'PENDING'} before launch: {len(pending)} item(s)")
        for x in pending:
            print("  -", x)
        if release:
            problems.extend(pending)
    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for x in problems:
            print("  -", x)
        return 1
    print("OK: all internal links resolve, anchors exist, recipe links decode, HTML is well-formed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
