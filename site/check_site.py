"""Check the built site in site/public: internal links, fragments, HTML structure.

    python3 site/build.py && python3 site/check_site.py

Fails (exit 1) when:
  - an internal href/src does not resolve to a file in site/public
    ("/docs/x/" -> docs/x/index.html; "/a.css" -> a.css; relative links too);
  - a "#fragment" link points at an id that does not exist on the target page;
  - a page's tags do not nest/close properly (html.parser pass);
  - a page lacks <title>, meta description, one <h1>, or has duplicate ids;
  - an unfilled INSTALL_COMMAND placeholder is left anywhere in the site.
External links (http/https/mailto) are listed, not fetched.
"""
from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

PUBLIC = Path(__file__).resolve().parent / "public"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}
# Elements whose end tag HTML lets you omit; we still require them closed except these.
OPTIONAL_END = {"p", "li", "option", "tr", "td", "th", "thead", "tbody"}


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

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(a["id"])
        # canonical/preconnect point at the deployed origin, not at files here
        skip = tag == "link" and a.get("rel") in ("preconnect", "canonical")
        for key in ("href", "src", "action"):
            if a.get(key) and not skip:
                self.links.append(a[key])
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

    checked, external = 0, set()
    for f, p in parsed.items():
        base = page_url(f)
        for link in p.links:
            parts = urlsplit(link)
            if parts.scheme in ("http", "https", "mailto", "tel") or link.startswith("//"):
                external.add(link)
                continue
            if parts.scheme:
                problems.append(f"{f.relative_to(PUBLIC)}: odd link {link}")
                continue
            full = urlsplit(urljoin(base, link))
            if full.path.startswith("/api/"):
                continue  # Pages Function, not a static file
            checked += 1
            tgt = target_file(full.path)
            if not tgt.is_file():
                problems.append(f"{f.relative_to(PUBLIC)}: broken link {link} -> {tgt.relative_to(PUBLIC)}")
                continue
            if full.fragment and tgt.suffix == ".html":
                tp = parsed.get(tgt)
                if tp is not None and full.fragment not in tp.ids:
                    problems.append(f"{f.relative_to(PUBLIC)}: missing anchor {link}")

    hits = sum(f.read_text(encoding="utf-8").count("INSTALL_COMMAND") for f in pages)
    if hits:
        problems.append(f"INSTALL_COMMAND placeholder left in the site ({hits}x); fill in the real command")

    for extra in ("sitemap.xml", "robots.txt", "404.html", "assets/style.css"):
        if not (PUBLIC / extra).is_file():
            problems.append(f"missing {extra}")

    print(f"{len(pages)} pages, {checked} internal links checked, {len(external)} external links (not fetched)")
    for e in sorted(external):
        print("   external:", e)
    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for x in problems:
            print("  -", x)
        return 1
    print("OK: all internal links resolve, anchors exist, HTML is well-formed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
