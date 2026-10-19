"""
browser.py — a tiny stdlib "accessibility browser" for the fake web apps.

It parses a page into what a screen reader / the macOS AX tree would expose
(role + accessible name + value + ordinal among same-role elements), and acts
by role/name: open, click, type, read. It talks only to 127.0.0.1 (proxies
disabled) and saves attachments into a Downloads folder like a real browser.

Every action can be mirrored to an event sink (``sink(event)``) in the same
low-level shape a real macOS recorder would produce (see trace.py):
pointer clicks, AXFocusedUIElementChanged / AXValueChanged, AXLoadComplete
with the page's visible text, and FSEvents-style file creation for downloads.
That is how the scripted demonstrations feed trace.Recorder.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode, urljoin, urlparse


class ElementNotFound(LookupError):
    pass


class _Parser(HTMLParser):
    """HTML -> elements (links, buttons, textboxes), tables, alerts, text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements: List[dict] = []
        self.forms: List[dict] = []
        self.labels: Dict[str, str] = {}
        self.tables: List[dict] = []
        self.alerts: List[str] = []
        self.status: List[str] = []
        self.title = ""
        self.headings: List[str] = []
        self.text: List[str] = []
        self._stack: List[tuple] = []       # (tag, attrs, text-buffer)
        self._form: Optional[int] = None
        self._table: Optional[dict] = None
        self._row: Optional[list] = None
        self._cell: Optional[list] = None
        self._in_head = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("input",):
            self._add_input(a)
            return
        if tag == "head":
            self._in_head = True
        if tag == "form":
            self.forms.append({"action": a.get("action", ""), "method": a.get("method", "get").lower(),
                               "label": a.get("aria-label", ""), "hidden": {}})
            self._form = len(self.forms) - 1
        if tag == "table":
            self._table = {"caption": "", "header": [], "rows": []}
        if tag == "tr" and self._table is not None:
            self._row = []
        if tag in ("td", "th") and self._row is not None:
            self._cell = []
        self._stack.append((tag, a, []))

    def _add_input(self, a):
        kind = a.get("type", "text")
        if kind == "hidden":
            if self._form is not None:
                self.forms[self._form]["hidden"][a.get("name", "")] = a.get("value", "")
            return
        self.elements.append({"role": "textbox", "id": a.get("id", ""), "field": a.get("name", ""),
                              "value": a.get("value", ""), "secure": kind == "password",
                              "invalid": a.get("aria-invalid") == "true", "form": self._form,
                              "aria_label": a.get("aria-label", "")})

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        if not self._stack:
            return
        # pop to the matching tag
        idx = max((i for i, s in enumerate(self._stack) if s[0] == tag), default=None)
        if idx is None:
            return
        t, a, buf = self._stack[idx]
        del self._stack[idx:]
        text = re.sub(r"\s+", " ", "".join(buf)).strip()
        if self._stack:                       # bubble text up to the parent
            self._stack[-1][2].append(" " + text + " ")
        if t == "title":
            self.title = text
        elif t == "label":
            self.labels[a.get("for", "")] = text
        elif t == "a" and a.get("href") is not None:
            self.elements.append({"role": "link", "name": a.get("aria-label") or text, "href": a["href"],
                                  "form": None})
        elif t == "button":
            self.elements.append({"role": "button", "name": a.get("aria-label") or text, "form": self._form,
                                  "field": a.get("name", ""), "value": a.get("value", "")})
        elif t in ("h1", "h2", "h3"):
            self.headings.append(text)
        if a.get("role") == "alert":
            self.alerts.append(text)
        if a.get("role") == "status":
            self.status.append(text)
        if t == "form":
            self._form = None
        if t in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(text)
            self._cell = None
        if t == "tr" and self._row is not None and self._table is not None:
            if self._row:
                if not self._table["header"] and self._stack and any(s[0] == "thead" for s in self._stack):
                    self._table["header"] = self._row
                else:
                    self._table["rows"].append(self._row)
            self._row = None
        if t == "caption" and self._table is not None:
            self._table["caption"] = text
        if t == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        if t in ("p", "h1", "h2", "h3", "li", "caption") and not self._in_head and text:
            self.text.append(text)

    def handle_data(self, data):
        if self._stack:
            self._stack[-1][2].append(data)


class Page:
    def __init__(self, url: str, status: int, html: str):
        self.url, self.status = url, status
        p = _Parser()
        p.feed(html)
        self.title, self.forms, self.tables = p.title, p.forms, p.tables
        self.alerts, self.status_msgs, self.headings = p.alerts, p.status, p.headings
        self.text_lines = p.text
        for el in p.elements:
            if el["role"] == "textbox":
                el["name"] = el.pop("aria_label") or p.labels.get(el["id"], "") or el["field"]
        counts: Dict[str, int] = {}
        for el in p.elements:
            el["ordinal"] = counts.get(el["role"], 0)
            counts[el["role"]] = el["ordinal"] + 1
        self.elements = p.elements

    @property
    def path(self) -> str:
        u = urlparse(self.url)
        return u.path + (f"?{u.query}" if u.query else "")

    def text(self) -> str:
        return "\n".join(self.text_lines)

    def summary(self, with_text: bool = False) -> dict:
        out = {"url_path": self.path, "title": self.title, "alerts": list(self.alerts),
               "status": list(self.status_msgs)}
        if with_text:
            out["page_text"] = self.text()[:6000]
            out["tables"] = [{"caption": t["caption"], "header": t["header"], "rows": t["rows"][:60]}
                             for t in self.tables]
        return out

    def find(self, role: str, name: Optional[str] = None, *, name_re: Optional[str] = None,
             ordinal: Optional[int] = None) -> dict:
        for el in self.elements:
            if el["role"] != role:
                continue
            if name is not None and el.get("name") != name:
                continue
            if name_re is not None and not re.search(name_re, el.get("name", ""), re.I):
                continue
            if ordinal is not None and el["ordinal"] != ordinal:
                continue
            return el
        raise ElementNotFound(f"{role} name={name!r} re={name_re!r} ordinal={ordinal} not on {self.path}")

    def table(self, caption_re: str) -> dict:
        for t in self.tables:
            if re.search(caption_re, t["caption"], re.I):
                return t
        raise ElementNotFound(f"table /{caption_re}/ not on {self.path}")


def ax_element(el: dict) -> dict:
    """The element as an AX observer would describe it (no internal fields)."""
    out = {"role": {"textbox": "AXTextField", "button": "AXButton", "link": "AXLink"}[el["role"]],
           "name": el.get("name", ""), "ordinal": el["ordinal"]}
    if el["role"] == "textbox":
        out["value"] = "" if el.get("secure") else el.get("value", "")
        if el.get("secure"):
            out["subrole"] = "AXSecureTextField"
    return out


class Browser:
    def __init__(self, base_urls: Dict[str, str], downloads: Path, sink: Optional[Callable[[dict], None]] = None):
        self.base_urls = dict(base_urls)
        self.downloads = Path(downloads)
        self.sink = sink
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPCookieProcessor(self.jar))
        self.page: Optional[Page] = None
        self.app: Optional[str] = None
        self.pages: Dict[str, Page] = {}      # one tab per app
        self.actions = 0

    def _emit(self, ev: dict) -> None:
        if self.sink:
            self.sink(ev)

    def _app_of(self, url: str) -> str:
        for name, base in self.base_urls.items():
            if url.startswith(base):
                return name
        raise ValueError(f"refusing non-task URL {url!r}")

    def _request(self, url: str, data: Optional[dict] = None):
        host = urlparse(url).hostname
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError(f"refusing non-local URL {url!r}")
        body = urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body)
        try:
            resp = self.opener.open(req, timeout=10)
            return resp.status, resp.geturl(), dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, url, dict(e.headers), e.read()

    def _load(self, url: str, data: Optional[dict] = None):
        self.actions += 1
        status, final, headers, raw = self._request(url, data)
        app = self._app_of(final)
        disp = headers.get("Content-Disposition", "")
        if "attachment" in disp:
            name = re.search(r'filename="([^"]+)"', disp).group(1)
            target = self.downloads / name
            k = 1
            while target.exists():
                stem, dot, ext = name.rpartition(".")
                target = self.downloads / f"{stem} ({k}).{ext}"
                k += 1
            self.downloads.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            self._emit({"type": "fs", "path": str(target), "flags": ["ItemCreated", "ItemIsFile"],
                        "by_app": "Browser"})
            return target
        self.page, self.app = Page(final, status, raw.decode("utf-8", "replace")), app
        self.pages[app] = self.page
        self._emit({"type": "ax", "app": app, "notification": "AXLoadComplete",
                    "window": self.page.summary(with_text=True)})
        return self.page

    # ------------------------------------------------------------ actions --
    def open(self, app: str, path: str = "/"):
        self._emit({"type": "app", "event": "activated", "app": app, "kind": "web"})
        return self._load(urljoin(self.base_urls[app], path))

    def switch(self, app: str):
        """Bring the app's tab to the front (opens '/' the first time)."""
        if app not in self.pages:
            return self.open(app, "/")
        self._emit({"type": "app", "event": "activated", "app": app, "kind": "web"})
        self.page, self.app = self.pages[app], app
        return self.page

    def type(self, name: Optional[str], value: str, *, name_re: Optional[str] = None,
             ordinal: Optional[int] = None) -> dict:
        el = self.page.find("textbox", name, name_re=name_re, ordinal=ordinal)
        self.actions += 1
        self._emit({"type": "ax", "app": self.app, "notification": "AXFocusedUIElementChanged",
                    "element": ax_element(el), "window": self.page.summary()})
        el["value"] = value
        # a real AX observer sees one AXValueChanged per keystroke; send a few partials
        for cut in sorted({max(1, len(value) // 2), len(value)}):
            if el.get("secure"):
                partial = {**ax_element(el), "value_length": cut}
            else:
                partial = {**ax_element(el), "value": value[:cut]}
            self._emit({"type": "ax", "app": self.app, "notification": "AXValueChanged", "element": partial,
                        "window": self.page.summary()})
        return el

    def click(self, role: str, name: Optional[str] = None, *, name_re: Optional[str] = None,
              ordinal: Optional[int] = None):
        el = self.page.find(role, name, name_re=name_re, ordinal=ordinal)
        self._emit({"type": "pointer", "kind": "click", "app": self.app, "element": ax_element(el),
                    "window": self.page.summary()})
        if role == "link":
            return self._load(urljoin(self.page.url, el["href"]))
        form = self.page.forms[el["form"]] if el.get("form") is not None else None
        if form is None:
            raise ElementNotFound("button outside a form")
        data = dict(form["hidden"])
        for f in self.page.elements:
            if f["role"] == "textbox" and f.get("form") == el["form"]:
                data[f["field"]] = f["value"]
        if el.get("field"):
            data[el["field"]] = el.get("value", "")
        url = urljoin(self.page.url, form["action"])
        if form["method"] == "get":
            return self._load(url + "?" + urlencode(data))
        return self._load(url, data)

    def read(self) -> dict:
        """The user looks at the page (no request): a recorder already has it from AXLoadComplete."""
        return self.page.summary(with_text=True)
