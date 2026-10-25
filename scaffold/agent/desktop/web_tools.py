"""
web_tools.py — loopback-only page reader / actor for local testbed web apps
(WP8, ~/Projects/REA/product2/BLUEPRINT.md §4).

The stand-in for driving Chrome through the macOS AX tree, until that path
exists. It fetches server-rendered HTML from 127.0.0.1 only and turns it into
what an accessibility client would see: numbered controls with a role, an
accessible name (aria-label / <label for> / text), the enclosing form's label
as context, whether the control sits in a modal alertdialog, plus the page's
live regions (alert / status / region / alertdialog) and readable text.
Actions are by control: follow a link, submit a form through one of its
buttons (with the values filled so far), fill a text input, pick a radio /
<select> option, and read an element's raw text for a secret sink.

    br = LoopbackBrowser("http://127.0.0.1:8123")
    page = br.open("/login")
    page.controls[0]         # Control(n=1, role="textbox", name="Email", ...)
    page = br.fill(page, n, "x"); page = br.click(page, n)

Safety properties (all enforced here, not by the caller):
  * every URL, including redirect targets, must be http://127.0.0.1:<port>
    (or localhost); proxies are disabled;
  * no JavaScript, no downloads; responses over MAX_BYTES are refused;
  * text that looks like a credential (long mixed letter/digit tokens, values
    of password inputs, elements labelled as key/token/secret values) is
    masked in everything the observation exposes. The raw value is only
    reachable through ``read_raw(page, n)``, which the micro-agent calls for
    ``copy_to_secret`` and hands straight to the secret store.

Design notes, not code, come from the Apache-2.0 nanobrowser outline format
(``[e3] textbox "Full name"``) and agent-e's pruning rules; see
docs/third_party/wp8.md.
"""
from __future__ import annotations

import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10.0
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

#: a token that looks like a credential: >= 20 chars of [A-Za-z0-9_-] with at
#: least one letter and one digit (API keys, session ids, bearer tokens)
_SECRETISH = re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])"
                        r"[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-])")
_SECRET_LABEL = re.compile(r"\b(api[ _-]?key|key value|secret|token|password|private key)\b", re.I)
_TEXT_INPUT_TYPES = {"", "text", "email", "search", "tel", "url", "number"}
#: control roles click(n) acts on: the classic four, plus the click targets the AX reader adds
#: (live/driver.read_web: ARIA options / menu items / tree items / grid cells, JS-clickable elements)
CLICKABLE_ROLES = ("link", "button", "checkbox", "tab", "option", "menuitem", "treeitem", "gridcell", "clickable")


class NotLoopback(PermissionError):
    pass


class WebError(RuntimeError):
    pass


class UserNeeded(WebError):
    """The browser refused BEFORE acting because a human has to look (e.g. an unrecognised
    system dialog over the window). Nothing was clicked or typed, so this is needs_user,
    never an uncertain write."""


def mask_secrets(text: str) -> str:
    """Replace credential-looking tokens with a fixed placeholder."""
    return _SECRETISH.sub(lambda m: f"«masked {len(m.group(0))} chars»", text)


def check_loopback(url: str) -> str:
    u = urllib.parse.urlparse(url)
    if u.scheme != "http" or (u.hostname or "") not in LOOPBACK_HOSTS:
        raise NotLoopback(f"refusing non-loopback URL: {u.scheme}://{u.hostname}")
    return url


@dataclass
class Control:
    """One numbered control in the observation (1-based ``n``)."""
    n: int
    role: str                      # link | button | textbox | password | radiogroup | combobox | checkbox | secret_text
                                   # (AX reader also: tab | option | menuitem | treeitem | gridcell | clickable)
    name: str
    context: str = ""              # label of the enclosing form / region / dialog
    in_dialog: bool = False
    input_type: str = ""
    value: str = ""                # masked display value
    options: List[str] = field(default_factory=list)
    href: str = ""
    form: Optional[int] = None     # index into Page.forms
    field: str = ""                # form field name
    autocomplete: str = ""
    inputmode: str = ""
    raw_ref: str = ""              # id of a readable element (secret_text)

    def line(self) -> str:
        bits = [f"[{self.n}] {self.role} \"{self.name}\""]
        if self.context and self.context.lower() != self.name.lower():
            bits.append(f"(in \"{self.context}\")")
        if self.in_dialog:
            bits.append("(dialog)")
        if self.options:
            bits.append("options=" + "|".join(self.options))
        if self.role in ("textbox", "password") and self.value:
            bits.append(f"= \"{self.value}\"")
        if self.role == "checkbox":
            bits.append("= checked" if checkbox_on(self.value) else "= unchecked")
        if self.role == "tab" and checkbox_on(self.value):
            bits.append("(selected)")
        if self.role == "secret_text":
            bits.append("= «hidden; use copy_to_secret»")
        return " ".join(bits)


def checkbox_on(value: str) -> bool:
    """A checkbox Control's value is its state: "1" checked, "0"/"" unchecked (HTML path,
    and the AX value of AXCheckBox on live Safari / Chromium)."""
    return str(value).strip().lower() in ("1", "true", "on", "checked")


@dataclass
class Form:
    action: str
    method: str
    label: str
    hidden: Dict[str, str]
    in_dialog: bool = False


@dataclass
class Page:
    url: str
    status: int
    title: str
    headings: List[str]
    controls: List[Control]
    forms: List[Form]
    alerts: List[str]              # role=alert text
    statuses: List[str]            # role=status text
    regions: List[str]             # role=region / alertdialog text (with their label)
    dialog: str                    # alertdialog title + description, if any
    text: str                      # readable body text (masked), tables as rows
    values: Dict[int, str] = field(default_factory=dict)      # n -> filled value (raw; never shown)
    _raw: Dict[str, str] = field(default_factory=dict, repr=False)   # readable element id -> raw text

    def control(self, n: int) -> Optional[Control]:
        if 1 <= n <= len(self.controls):
            return self.controls[n - 1]
        return None

    def live_text(self) -> str:
        return " | ".join(self.alerts + self.statuses + self.regions + ([self.dialog] if self.dialog else []))


# ---------------------------------------------------------------------------
# HTML -> Page
# ---------------------------------------------------------------------------
_VOID = {"input", "br", "img", "meta", "link", "hr", "area", "base", "col", "source", "wbr"}
_SKIP = {"script", "style", "template", "noscript", "head"}
_BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section", "header", "main", "nav",
          "form", "fieldset", "legend", "table", "ul", "ol", "br", "label"}


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[Tuple[str, dict]] = []
        self.labels_for: Dict[str, str] = {}
        self.items: List[dict] = []            # controls in document order (pre-numbering)
        self.forms: List[Form] = []
        self.form_idx: Optional[int] = None
        self.title = ""
        self.headings: List[str] = []
        self.alerts: List[str] = []
        self.statuses: List[str] = []
        self.regions: List[str] = []
        self.dialog_parts: List[str] = []
        self.text: List[str] = []
        self.raw: Dict[str, str] = {}
        self._cap: List[dict] = []             # open text captures: {"tag", "kind", "buf", ...}
        self._skip = 0
        self._dialog_depth = 0
        self._group: Optional[dict] = None     # open radiogroup / select
        self._in_title = False
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    # -- helpers ----------------------------------------------------------
    def _context(self) -> str:
        for tag, a in reversed(self.stack):
            if a.get("aria-label") and (tag in ("form", "section", "fieldset") or a.get("role") in
                                        ("region", "alertdialog", "dialog", "radiogroup", "group")):
                return a["aria-label"]
        return ""

    def _open_cap(self, tag: str, kind: str, **kw) -> None:
        self._cap.append({"tag": tag, "kind": kind, "buf": [], "depth": len(self.stack), **kw})

    # -- parser callbacks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag in _SKIP and tag != "head":
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag not in _VOID:
            self.stack.append((tag, a))
        if tag in _BLOCK:
            self._newline()
            for c in self._cap:
                c["buf"].append(" ")
        elif tag in ("a", "button") and self.text and not self.text[-1].endswith(("\n", " ")):
            self.text.append(" ")
        role = a.get("role", "")
        if role == "alertdialog" or role == "dialog":
            self._dialog_depth += 1
        if tag == "form":
            self.forms.append(Form(action=a.get("action", ""), method=(a.get("method") or "get").lower(),
                                   label=a.get("aria-label", ""), hidden={}, in_dialog=self._dialog_depth > 0))
            self.form_idx = len(self.forms) - 1
        if tag in ("h1", "h2", "h3"):
            self._open_cap(tag, "heading")
        if role in ("alert", "status"):
            self._open_cap(tag, role)
        if role == "region":
            self._open_cap(tag, "region", label=a.get("aria-label", ""))
        if role in ("alertdialog", "dialog"):
            self._open_cap(tag, "dialog")
        if tag == "label" and a.get("for"):
            self._open_cap(tag, "label", target=a["for"])
        if tag == "a" and a.get("href") is not None:
            self._open_cap(tag, "link", attrs=a, ctx=self._context(), dlg=self._dialog_depth > 0)
        if tag == "button":
            self._open_cap(tag, "button", attrs=a, ctx=self._context(), dlg=self._dialog_depth > 0,
                           form=self.form_idx)
        if tag in ("code", "output", "pre") and (a.get("id") or a.get("aria-label")):
            self._open_cap(tag, "readable", attrs=a, ctx=self._context())
        if role == "radiogroup" and tag not in _VOID:
            self._group = {"kind": "radiogroup", "name": a.get("aria-label", ""), "options": [],
                           "values": [], "field": "", "form": self.form_idx, "ids": [],
                           "tag": tag, "depth": len(self.stack)}
        if tag == "select":
            self._group = {"kind": "combobox", "name": a.get("aria-label", ""), "options": [], "values": [],
                           "field": a.get("name", ""), "form": self.form_idx, "id": a.get("id", ""),
                           "ids": [], "tag": tag, "depth": len(self.stack)}
        if tag == "option" and self._group is not None:
            self._open_cap(tag, "option", value=a.get("value"))
        if tag == "td" or tag == "th":
            self._cell = []
        if tag == "tr":
            self._row = []
        if tag == "input":
            self._input(a)

    def _input(self, a: dict) -> None:
        t = (a.get("type") or "").lower()
        if t == "hidden":
            if self.form_idx is not None:
                self.forms[self.form_idx].hidden[a.get("name", "")] = a.get("value", "")
            return
        if t == "radio" and self._group is not None:
            self._group["field"] = a.get("name", "")
            self._group["values"].append(a.get("value", ""))
            self._group["ids"].append(a.get("id", ""))
            return
        if t in ("submit", "button"):
            self.items.append({"role": "button", "name": a.get("value") or a.get("aria-label") or "Submit",
                               "context": self._context(), "dlg": self._dialog_depth > 0,
                               "form": self.form_idx, "submit": t == "submit"})
            return
        role = "password" if t == "password" else ("checkbox" if t == "checkbox" else "textbox")
        if role == "textbox" and t not in _TEXT_INPUT_TYPES:
            role = "input:" + t
        self.items.append({"role": role, "id": a.get("id", ""), "aria": a.get("aria-label", ""),
                           "placeholder": a.get("placeholder", ""), "field": a.get("name", ""),
                           "value": a.get("value", ""), "type": t, "form": self.form_idx,
                           "checked": "checked" in a,
                           "context": self._context(), "dlg": self._dialog_depth > 0,
                           "autocomplete": a.get("autocomplete", ""), "inputmode": a.get("inputmode", "")})

    def handle_endtag(self, tag):
        if tag in _SKIP and tag != "head":
            self._skip = max(0, self._skip - 1)
        if tag == "title":
            self._in_title = False
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        if tag == "tr" and self._row is not None:
            if any(self._row):
                self.text.append("\n" + " | ".join(self._row))
            self._row = None
        # close the captures this element opened (same tag, same depth)
        depth = len(self.stack)
        for c in [c for c in self._cap if c["tag"] == tag and c["depth"] == depth]:
            self._cap.remove(c)
            self._close_cap(c)
        if self._group is not None and self._group["tag"] == tag and self._group["depth"] == depth:
            self._flush_group()
        if tag not in _VOID:
            # pop to the matching tag (tolerate sloppy HTML)
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    t, a = self.stack[i]
                    del self.stack[i:]
                    if a.get("role") in ("alertdialog", "dialog"):
                        self._dialog_depth = max(0, self._dialog_depth - 1)
                    break
        if tag == "form":
            self.form_idx = None
        if tag in _BLOCK:
            self._newline()
            for c in self._cap:
                c["buf"].append(" ")

    def _flush_group(self) -> None:
        g, self._group = self._group, None
        if not g["options"]:
            return
        self.items.append({"role": g["kind"], "name": g["name"] or self.labels_for.get(g.get("id", ""), "")
                           or g["field"], "options": g["options"],
                           "values": g["values"], "field": g["field"], "form": g["form"],
                           "context": self._context(), "dlg": self._dialog_depth > 0})

    def _close_cap(self, c: dict) -> None:
        txt = " ".join("".join(c["buf"]).split())
        k = c["kind"]
        if k == "heading":
            self.headings.append(txt)
        elif k == "alert":
            self.alerts.append(txt)
        elif k == "status":
            self.statuses.append(txt)
        elif k == "region":
            self.regions.append((c.get("label") + ": " if c.get("label") else "") + txt)
        elif k == "dialog":
            self.dialog_parts.append(txt)
        elif k == "label":
            self.labels_for[c["target"]] = txt
            if self._group is not None and c["target"] in self._group["ids"]:
                self._group["options"].append(txt)
        elif k == "option" and self._group is not None:
            self._group["options"].append(txt)
            self._group["values"].append(c.get("value") if c.get("value") is not None else txt)
        elif k == "link":
            a = c["attrs"]
            self.items.append({"role": "button" if a.get("role") == "button" else "link",
                               "name": a.get("aria-label") or txt, "href": a.get("href", ""),
                               "context": c["ctx"], "dlg": c["dlg"], "form": None, "nav": True})
        elif k == "button":
            a = c["attrs"]
            self.items.append({"role": "button", "name": a.get("aria-label") or txt, "context": c["ctx"],
                               "dlg": c["dlg"], "form": c["form"],
                               "submit": (a.get("type") or "submit").lower() == "submit"})
        elif k == "readable":
            a = c["attrs"]
            rid = a.get("id") or a.get("aria-label")
            name = a.get("aria-label") or self.labels_for.get(a.get("id", ""), "") or rid
            secretish = bool(_SECRETISH.search(txt)) or bool(_SECRET_LABEL.search(name))
            self.raw[rid] = txt
            if secretish:
                self.items.append({"role": "secret_text", "name": name, "raw_ref": rid,
                                   "context": c["ctx"], "dlg": False, "form": None})

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        for c in self._cap:
            c["buf"].append(data)
        if self._cell is not None:
            self._cell.append(data)
        elif data.strip():
            self.text.append(data)

    def _newline(self) -> None:
        if self.text and not self.text[-1].endswith("\n"):
            self.text.append("\n")


def parse_page(url: str, status: int, html_text: str) -> Page:
    p = _Parser()
    p.feed(html_text)
    p.close()
    if p._group is not None:
        p._flush_group()
    controls: List[Control] = []
    for it in p.items:
        role = it["role"]
        if role in ("textbox", "password", "checkbox") or role.startswith("input:"):
            name = (it.get("aria") or p.labels_for.get(it.get("id", ""), "") or it.get("placeholder")
                    or it.get("field") or "")
            val = it.get("value", "")
            shown = ("•" * 8 if val else "") if role == "password" else mask_secrets(val)
            if role == "checkbox":                       # value = state; the form value is kept aside
                shown = "1" if it.get("checked") else "0"
            c = Control(0, role, " ".join(name.split()), it.get("context", ""), it.get("dlg", False),
                        it.get("type", ""), shown, form=it.get("form"), field=it.get("field", ""),
                        autocomplete=it.get("autocomplete", ""), inputmode=it.get("inputmode", ""))
            if role == "checkbox":
                c._on_value = val or "on"  # type: ignore[attr-defined]
            controls.append(c)
        elif role in ("radiogroup", "combobox"):
            c = Control(0, role, it["name"], it.get("context", ""), it.get("dlg", False),
                        options=list(it["options"]), form=it.get("form"), field=it.get("field", ""))
            c._values = list(it["values"])  # type: ignore[attr-defined]
            controls.append(c)
        elif role == "secret_text":
            controls.append(Control(0, role, it["name"], it.get("context", ""), raw_ref=it["raw_ref"]))
        else:
            c = Control(0, role, mask_secrets(" ".join(it.get("name", "").split())), it.get("context", ""),
                        it.get("dlg", False), href=it.get("href", ""), form=it.get("form"))
            c._submit = it.get("submit", False)  # type: ignore[attr-defined]
            controls.append(c)
    for i, c in enumerate(controls, 1):
        c.n = i
    body = mask_secrets("".join(p.text))
    body = "\n".join(" ".join(ln.split()) for ln in body.splitlines() if ln.strip())
    return Page(url=url, status=status, title=" ".join(p.title.split()), headings=p.headings,
                controls=controls, forms=p.forms, alerts=[mask_secrets(x) for x in p.alerts],
                statuses=[mask_secrets(x) for x in p.statuses], regions=[mask_secrets(x) for x in p.regions],
                dialog=mask_secrets(" ".join(p.dialog_parts)), text=body, _raw=p.raw)


# ---------------------------------------------------------------------------
# the browser
# ---------------------------------------------------------------------------
class _LoopbackRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_loopback(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class LoopbackBrowser:
    """Cookie-keeping, loopback-only HTML client that acts by control number."""

    def __init__(self, base_url: str, timeout: float = TIMEOUT_S):
        self.base = check_loopback(base_url).rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                              urllib.request.HTTPCookieProcessor(self.jar),
                                              _LoopbackRedirect())
        self.log: List[str] = []          # method + path + status (no values)
        self.page: Optional[Page] = None

    def _url(self, path_or_url: str, page: Optional[Page] = None) -> str:
        base = page.url if page else self.base + "/"
        return check_loopback(urllib.parse.urljoin(base, path_or_url))

    def _request(self, url: str, data: Optional[bytes] = None) -> Page:
        check_loopback(url)
        method = "POST" if data is not None else "GET"
        try:
            with self.op.open(url, data=data, timeout=self.timeout) as r:
                raw = r.read(MAX_BYTES + 1)
                status, final = r.status, r.geturl()
        except urllib.error.HTTPError as e:
            raw, status, final = e.read(MAX_BYTES + 1), e.code, url
        except (urllib.error.URLError, OSError) as e:
            raise WebError(f"{method} failed: {type(e).__name__}") from e
        if len(raw) > MAX_BYTES:
            raise WebError("response too large")
        self.log.append(f"{method} {urllib.parse.urlparse(url).path} -> {status}")
        self.page = parse_page(check_loopback(final), status, raw.decode("utf-8", "replace"))
        return self.page

    def open(self, path_or_url: str = "/") -> Page:
        return self._request(self._url(path_or_url, self.page))

    # -- actions --------------------------------------------------------------
    def click(self, page: Page, n: int) -> Page:
        c = page.control(n)
        if c is None:
            raise WebError(f"no control {n}")
        if c.role == "link" or (c.role == "button" and c.href):
            return self._request(self._url(c.href, page))
        if c.role == "button" and c.form is not None:
            return self._submit(page, c)
        if c.role == "checkbox":                         # toggles locally, sent with the next submit
            c.value = "0" if checkbox_on(c.value) else "1"
            return page
        raise WebError(f"control {n} ({c.role}) is not clickable")

    def _submit(self, page: Page, button: Control) -> Page:
        f = page.forms[button.form]
        fields: Dict[str, str] = dict(f.hidden)
        for c in page.controls:
            if c.form != button.form or not c.field:
                continue
            if c.role == "checkbox":
                if checkbox_on(c.value):
                    fields[c.field] = getattr(c, "_on_value", "on")
            elif c.n in page.values:
                fields[c.field] = page.values[c.n]
            elif c.role in ("textbox", "password"):
                fields.setdefault(c.field, "")
        url = self._url(f.action or page.url, page)
        if f.method == "post":
            return self._request(url, urllib.parse.urlencode(fields).encode())
        q = urllib.parse.urlencode(fields)
        return self._request(url + ("&" if "?" in url else "?") + q if q else url)

    def fill(self, page: Page, n: int, value: str) -> Page:
        c = page.control(n)
        if c is None or c.role not in ("textbox", "password"):
            raise WebError(f"control {n} is not a text input")
        page.values[n] = value
        c.value = "•" * 8 if c.role == "password" else mask_secrets(value)
        return page

    def select(self, page: Page, n: int, option: str) -> Page:
        c = page.control(n)
        if c is None or c.role not in ("radiogroup", "combobox"):
            raise WebError(f"control {n} is not a choice")
        opts = [o.lower() for o in c.options]
        if option.lower() not in opts:
            raise WebError(f"option not offered by control {n}")
        page.values[n] = getattr(c, "_values")[opts.index(option.lower())]
        c.value = c.options[opts.index(option.lower())]
        return page

    def read_raw(self, page: Page, n: int) -> str:
        """Raw text of a secret_text control. Caller must route it to a secret
        store; it must never reach a model, log or transcript."""
        c = page.control(n)
        if c is None or c.role != "secret_text":
            raise WebError(f"control {n} is not readable")
        return page._raw.get(c.raw_ref, "")


def outline(page: Page, start: int = 0, limit: int = 40) -> str:
    """Model-facing outline: masked live regions, text, numbered controls."""
    out = [f"URL path: {urllib.parse.urlparse(page.url).path}  (HTTP {page.status})",
           f"Title: {page.title}"]
    if page.alerts:
        out.append("Alerts: " + " | ".join(page.alerts))
    if page.statuses:
        out.append("Status: " + " | ".join(page.statuses))
    if page.dialog:
        out.append("Dialog: " + page.dialog)
    if page.regions:
        out.append("Regions: " + " | ".join(page.regions))
    out.append("Page text:\n" + page.text[:3000])
    ctl = page.controls[start:start + limit]
    out.append(f"Controls ({len(page.controls)} total, showing {start + 1}-{start + len(ctl)}):")
    out += [c.line() for c in ctl]
    return "\n".join(out)
