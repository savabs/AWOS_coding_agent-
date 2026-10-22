"""
htmldriver.py — a pure-Python stand-in for the browser, used to gate tasks
without Chromium.

    parse(html) -> PageView      controls (role, name, context, in_dialog, ...) computed
                                 with the accessible-name rules Chromium uses for the DOM
                                 patterns the factory emits, plus status/alert/dialog text
    SimBrowser(transport)        open / fill / select / click on those controls by issuing
                                 exactly the requests the page's HTML + JS would issue
                                 (one form POST per button, GET per link)
    SimBrowser.run_step(step)    execute one semantic oracle step (contract.TaskSource.oracle)

Accessible names (subset of HTML-AAM, in this order):
    aria-labelledby > aria-label > <label for> / wrapping <label> (form fields) >
    text content without aria-hidden parts (buttons, links) > title > placeholder.
Context = name of the innermost dialog / alertdialog / role=form / role=region
ancestor (what live/driver.read_web puts in Control.context).
Roles are the product's (web_tools.Control.role): link | button | textbox | password |
radiogroup | combobox | checkbox.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field as dfield
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional, Tuple

VOID = {"input", "br", "img", "meta", "link", "hr", "area", "base", "col", "source", "wbr"}
CONTEXT_ROLES = {"dialog", "alertdialog", "form", "region"}


class El:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["El"]):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def text(self, skip_hidden: bool = True) -> str:
        out: List[str] = []

        def walk(e):
            for c in e.children:
                if isinstance(c, str):
                    out.append(c)
                elif not (skip_hidden and (c.attrs.get("aria-hidden") == "true" or c.tag in ("script", "style"))):
                    if c.tag in ("input", "select", "textarea"):
                        continue
                    walk(c)
        walk(self)
        return " ".join(" ".join(out).split())

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, El):
                yield from c.iter()


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = El("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        e = El(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(e)
        if tag not in VOID:
            self.cur = e

    def handle_startendtag(self, tag, attrs):
        e = El(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(e)

    def handle_endtag(self, tag):
        e = self.cur
        while e is not None and e.tag != tag:
            e = e.parent
        if e is not None and e.parent is not None:
            self.cur = e.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


@dataclass
class Ctl:
    role: str
    name: str
    context: str = ""
    in_dialog: bool = False
    ref: str = ""                    # ctl id (buttons / data-ctl links) or field name (inputs)
    submit: Optional[Tuple[str, str]] = None     # (form key, value) posted by a click
    href: str = ""
    field: str = ""
    autosubmit: str = ""
    options: List[Tuple[str, str]] = dfield(default_factory=list)   # (value, text)
    el: Optional[El] = dfield(default=None, repr=False)

    def key(self) -> tuple:
        return (self.role, self.name.lower(), self.context.lower(), self.in_dialog)


@dataclass
class PageView:
    title: str
    controls: List[Ctl]
    statuses: List[str]
    alerts: List[str]
    dialog: str
    text: str
    action: str
    defaults: List[Tuple[str, str, str]]          # (kind, name, value) of every form field at load


def _hidden(e: El) -> bool:
    a = e.attrs
    return "hidden" in a or a.get("aria-hidden") == "true" or "display:none" in a.get("style", "").replace(" ", "")


def parse(html: str) -> PageView:
    p = _Parser()
    p.feed(html)
    root = p.root
    by_id = {e.attrs["id"]: e for e in root.iter() if "id" in e.attrs}
    label_for: Dict[str, El] = {e.attrs["for"]: e for e in root.iter() if e.tag == "label" and "for" in e.attrs}

    def role_of(e: El) -> str:
        return e.attrs.get("role", "")

    def name_of(e: El, field_like: bool) -> str:
        a = e.attrs
        if a.get("aria-labelledby"):
            return _norm(" ".join(by_id[i].text() for i in a["aria-labelledby"].split() if i in by_id))
        if a.get("aria-label"):
            return _norm(a["aria-label"])
        if field_like:
            lab = label_for.get(a.get("id", ""))
            if lab is None:
                anc = e.parent
                while anc is not None and anc.tag != "label":
                    anc = anc.parent
                lab = anc
            if lab is not None and lab.text():
                return lab.text()
        else:
            t = e.text()
            if t:
                return t
        if a.get("title"):
            return _norm(a["title"])
        if field_like and a.get("placeholder"):
            return _norm(a["placeholder"])
        return ""

    controls: List[Ctl] = []
    statuses: List[str] = []
    alerts: List[str] = []
    dialogs: List[str] = []
    defaults: List[Tuple[str, str, str]] = []
    title = ""
    action = ""

    def walk(e: El, ctx: str, in_dlg: bool, radiogroup: Optional[Ctl]):
        nonlocal title, action
        for c in e.children:
            if not isinstance(c, El):
                continue
            if c.tag in ("script", "style"):
                continue
            if c.tag == "title":
                title = c.text()
                continue
            if _hidden(c) and c.tag != "input":
                continue
            a, r = c.attrs, role_of(c)
            if c.tag == "form" and c.attrs.get("id") == "appform":
                action = a.get("action", "")
            # form-field defaults (incl. hidden inputs)
            if c.tag == "input":
                t = a.get("type", "text")
                if t in ("checkbox", "radio"):
                    if "checked" in a:
                        defaults.append((t, a.get("name", ""), a.get("value", "on")))
                elif "name" in a:
                    defaults.append(("text", a["name"], a.get("value", "")))
            elif c.tag == "select" and "name" in a:
                sel = [o for o in c.iter() if o.tag == "option"]
                chosen = next((o for o in sel if "selected" in o.attrs), sel[0] if sel else None)
                if chosen is not None:
                    defaults.append(("text", a["name"], chosen.attrs.get("value", chosen.text())))
            elif c.tag == "textarea" and "name" in a:
                defaults.append(("text", a["name"], c.text(skip_hidden=False)))

            nctx, ndlg = ctx, in_dlg
            if r in CONTEXT_ROLES:
                nm = name_of(c, False) if r != "region" and r != "form" else _norm(a.get("aria-label", ""))
                if r in ("dialog", "alertdialog"):
                    ndlg = True
                    dialogs.append(c.text())
                if nm:
                    nctx = nm
            if r == "status":
                statuses.append(c.text())
            elif r == "alert":
                alerts.append(c.text())

            if c.tag == "a" and "href" in a:
                controls.append(Ctl("link", name_of(c, False), nctx, ndlg, ref=a.get("data-ctl", ""),
                                    href=a["href"], el=c))
                continue
            if c.tag == "button" or r == "button":
                sub = None
                if "data-submit" in a:
                    sub = ("__js", a["data-submit"])
                elif c.tag == "button" and a.get("type", "submit") == "submit":
                    sub = (a.get("name", ""), a.get("value", ""))
                ref = sub[1] if sub else ""
                if ref.startswith("__toggle:"):              # toggle button for a boolean field
                    ref = ref[len("__toggle:"):]
                controls.append(Ctl("button", name_of(c, False), nctx, ndlg, ref=ref,
                                    submit=sub, el=c))
                continue
            if r == "radiogroup":
                g = Ctl("radiogroup", name_of(c, False) if not a.get("aria-label") else _norm(a["aria-label"]),
                        nctx, ndlg, el=c)
                if not g.name and a.get("title"):
                    g.name = _norm(a["title"])
                controls.append(g)
                walk(c, nctx, ndlg, g)
                continue
            if c.tag == "input":
                t = a.get("type", "text")
                if t == "hidden":
                    continue
                if t == "radio":
                    if radiogroup is not None:
                        radiogroup.field = a.get("name", "")
                        radiogroup.ref = radiogroup.field
                        radiogroup.options.append((a.get("value", ""), name_of(c, True)))
                        radiogroup.autosubmit = radiogroup.autosubmit or a.get("data-autosubmit", "")
                    continue
                role = {"checkbox": "checkbox", "password": "password"}.get(t, "textbox")
                controls.append(Ctl(role, name_of(c, True), nctx, ndlg, ref=a.get("name", ""), field=a.get("name", ""),
                                    autosubmit=a.get("data-autosubmit", ""), el=c))
                continue
            if c.tag == "select":
                opts = [(o.attrs.get("value", o.text()), o.text()) for o in c.iter() if o.tag == "option"]
                controls.append(Ctl("combobox", name_of(c, True), nctx, ndlg, ref=a.get("name", ""), field=a.get("name", ""),
                                    autosubmit=a.get("data-autosubmit", ""), options=opts, el=c))
                continue
            if c.tag == "textarea":
                controls.append(Ctl("textbox", name_of(c, True), nctx, ndlg, ref=a.get("name", ""), field=a.get("name", ""), el=c))
                continue
            walk(c, nctx, ndlg, radiogroup)

    walk(root, "", False, None)
    body = next((e for e in root.iter() if e.tag == "body"), root)
    return PageView(title, controls, [s for s in statuses if s], [s for s in alerts if s],
                    " ".join(d for d in dialogs if d), body.text(), action, defaults)


# ------------------------------------------------------------------ browser --

class StepError(RuntimeError):
    pass


Transport = Callable[[str, str, Optional[str]], Tuple[int, str, str]]
# transport(method, url, body) -> (status, html, final_url)  (redirects followed)


class SimBrowser:
    def __init__(self, transport: Transport):
        self.t = transport
        self.url = ""
        self.html = ""
        self.status = 0
        self.view: Optional[PageView] = None
        self.values: Dict[str, object] = {}       # field -> str | bool (checkbox) ; radio field -> value

    # -- navigation -----------------------------------------------------------
    def open(self, url: str) -> PageView:
        self.status, self.html, self.url = self.t("GET", url, None)
        return self._load()

    def _load(self) -> PageView:
        self.view = parse(self.html)
        self.values = {}
        for kind, name, value in self.view.defaults:
            if kind == "checkbox":
                self.values[name] = True
            else:
                self.values[name] = value
        # unchecked checkboxes
        for c in self.view.controls:
            if c.role == "checkbox":
                self.values.setdefault(c.field, False)
        return self.view

    def _abs(self, href: str) -> str:
        return urllib.parse.urljoin(self.url, href)

    def _submit(self, key: str, value: str) -> PageView:
        pairs: List[Tuple[str, str]] = []
        cb_names = {c.field for c in self.view.controls if c.role == "checkbox"}
        radio_names = {c.field for c in self.view.controls if c.role == "radiogroup"}
        for kind, name, val in self.view.defaults:            # hidden + text fields keep doc order
            if kind == "text" and name not in cb_names and name not in radio_names:
                pairs.append((name, val if name.startswith("__") else str(self.values.get(name, val))))
        for n in cb_names:
            if self.values.get(n):
                pairs.append((n, "on"))
        for n in radio_names:
            v = self.values.get(n)
            if v:
                pairs.append((n, str(v)))
        pairs = [(k, v) for k, v in pairs if k != "__js"]
        pairs.append(("__js", value if key == "__js" else ""))
        if key and key != "__js":
            pairs.append((key, value))
        body = urllib.parse.urlencode(pairs)
        self.status, self.html, self.url = self.t("POST", self._abs(self.view.action), body)
        return self._load()

    # -- control resolution --------------------------------------------------
    def find(self, role: str, name: str, context: Optional[str] = None) -> Ctl:
        n = _norm(name).lower()
        hits = [c for c in self.view.controls if c.role == role and c.name.lower() == n
                and (context is None or c.context.lower() == _norm(context).lower())]
        if len(hits) != 1:
            raise StepError(f"{len(hits)} controls match {role} {name!r} context={context!r} on {self.view.title!r}")
        c = hits[0]
        twins = [x for x in self.view.controls if x.key() == c.key()]
        if len(twins) > 1:
            raise StepError(f"ambiguous for the product's grounder: {role} {name!r} ({len(twins)} twins)")
        return c

    def by_ref(self, ref: str) -> Ctl:
        hits = [c for c in self.view.controls if c.ref == ref]
        if len(hits) != 1:
            raise StepError(f"{len(hits)} controls with ref {ref!r} on {self.view.title!r}")
        return hits[0]

    # -- actions ---------------------------------------------------------------
    def fill(self, c: Ctl, value: str) -> PageView:
        if c.role not in ("textbox", "password"):
            raise StepError(f"fill on {c.role}")
        self.values[c.field] = value
        return self._submit("__js", c.autosubmit) if c.autosubmit else self.view

    def select(self, c: Ctl, option: str) -> PageView:
        if c.role not in ("combobox", "radiogroup"):
            raise StepError(f"select on {c.role}")
        m = [v for v, t in c.options if _norm(t).lower() == _norm(option).lower()]
        if len(m) != 1:
            raise StepError(f"option {option!r} not in {[t for _, t in c.options]}")
        self.values[c.field] = m[0]
        return self._submit("__js", c.autosubmit) if c.autosubmit else self.view

    def click(self, c: Ctl) -> PageView:
        if c.role == "checkbox":
            self.values[c.field] = not self.values.get(c.field)
            return self._submit("__js", c.autosubmit) if c.autosubmit else self.view
        if c.role == "link":
            return self.open(self._abs(c.href))
        if c.role == "button":
            if not c.submit:
                return self.view
            return self._submit(*c.submit)
        raise StepError(f"click on {c.role}")

    def run_step(self, step: dict) -> PageView:
        op = step["op"]
        if op in ("needs_user", "done", "scroll"):
            return self.view
        c = self.find(step["role"], step["name"], step.get("context"))
        if op == "click":
            return self.click(c)
        if op == "fill":
            return self.fill(c, step["value"])
        if op == "select":
            return self.select(c, step["option"])
        raise StepError(f"unknown op {op}")


def semantic_step(view: PageView, c: Ctl, op: str, **extra) -> dict:
    """The contract's semantic step for control ``c``; adds ``context`` only when the
    (role, name) pair is not unique on the page. Raises StepError if the product's
    grounder would refuse it (no name, or an exact twin)."""
    if not c.name:
        raise StepError(f"{c.role} ref={c.ref!r} has no accessible name")
    twins = [x for x in view.controls if x.key() == c.key()]
    if len(twins) > 1:
        raise StepError(f"{c.role} {c.name!r} has {len(twins)} exact twins (role, name, context, dialog)")
    step = {"op": op, "role": c.role, "name": c.name}
    same = [x for x in view.controls if x.role == c.role and x.name.lower() == c.name.lower()]
    if len(same) > 1:
        step["context"] = c.context
        if len([x for x in same if x.context.lower() == c.context.lower()]) > 1:
            raise StepError(f"{c.role} {c.name!r}: context does not disambiguate")
    step.update(extra)
    return step
