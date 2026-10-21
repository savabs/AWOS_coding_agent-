"""
driver.py — the existing agents on live Safari.

    MacBrowserBackend(base_url)     micro_agent's browser interface (open / click / fill / select /
                                    read_raw / .page / .log) over a NEW Safari window, read through
                                    the AX tree. Pages are web_tools.Page objects, so MicroAgent,
                                    WebAXGrounder, PageReadback and micro_wiring.read_key_rows run
                                    unchanged.
    MacEnv(base_env, urls)          teach-once skill_runner's env interface (page / open / switch /
                                    click / type / is_web / secret) over one Safari window per web
                                    app; file operations stay with the wrapped TestbedEnv.
    read_web(snapshot)              AX web area -> WebView (controls, forms, live regions, tables,
                                    text), the common reading both adapters are built from.

Reading rules (AX -> controls), document order:
    AXLink -> link (href = AXURL)        AXButton -> button        AXTextField/AXTextArea -> textbox
    AXSecureTextField -> password        AXRadioGroup -> radiogroup (options = its radio buttons)
    AXPopUpButton -> combobox            AXCheckBox -> checkbox
    a labelled leaf (DOM id / aria-label) whose text looks like a credential, or whose label says
    key / token / secret -> secret_text (raw text only via read_raw; masked everywhere else)
Forms: an explicit form (AXLandmarkForm / role description "form"); otherwise a button belongs
to the innermost ancestor that contains an input (the web area counts), or to its dialog.
Every form is treated as a POST form, so micro_agent counts its submit as a write and asks
for approval first (conservative). The HTTP status is not in the AX tree: a page titled
"Not found" / "404" is reported as 404, anything else as 200.
"""
from __future__ import annotations

import contextlib
import itertools
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from ..ax_compress import norm_role
from ..web_tools import _SECRET_LABEL, _SECRETISH, Control, Form, Page, WebError, check_loopback, mask_secrets
from .actions import SAFARI, Actor, Expect, Outcome, Revealed, norm_url
from .observe import Snapshot, snapshot_element

INPUT_ROLES = {"textfield", "textarea", "searchfield", "combobox", "securetextfield", "radiogroup",
               "popupbutton", "checkbox"}
DIALOG_SUBROLES = {"AXApplicationAlertDialog", "AXApplicationDialog"}
LEAF_TEXT_ROLES = {"statictext", "text"}
_snap_ids = itertools.count(1)


# ------------------------------------------------------------------ reading --

@dataclass
class WebControl:
    kind: str
    name: str
    path: Tuple[int, ...]
    context: str = ""
    in_dialog: bool = False
    form: Optional[int] = None
    value: str = ""
    has_value: bool = False
    href: str = ""
    options: List[str] = field(default_factory=list)
    option_paths: Dict[str, Tuple[int, ...]] = field(default_factory=dict)
    raw: str = field(default="", repr=False)          # secret_text only; never shown


@dataclass
class WebView:
    url: str
    title: str
    status: int
    controls: List[WebControl]
    forms: List[dict]
    alerts: List[str]
    statuses: List[str]
    regions: List[str]
    dialog: str
    headings: List[str]
    lines: List[Tuple[str, str]]                      # (text, kind: text|heading|row|caption)
    tables: List[dict]


def _role(n: dict) -> str:
    return norm_role(n.get("role"))


def _name(n: dict) -> str:
    return " ".join(str(n.get("title") or n.get("description") or "").split())


def _is_form(n: dict) -> bool:
    return (n.get("subrole") == "AXLandmarkForm" or _role(n) == "form"
            or str(n.get("roledescription") or "").lower() == "form")


def _is_dialog(n: dict) -> bool:
    return n.get("subrole") in DIALOG_SUBROLES or _role(n) in ("sheet", "dialog", "alertdialog")


def _is_region(n: dict) -> bool:
    return n.get("subrole") == "AXLandmarkRegion" or str(n.get("roledescription") or "").lower() == "region"


def _leaf_text(n: dict) -> str:
    v = n.get("value")
    return " ".join(str(v if isinstance(v, str) and v.strip() else (n.get("title") or "")).split())


def text_of(n: dict, limit: int = 4000) -> str:
    parts: List[str] = []

    def walk(x):
        if _role(x) in LEAF_TEXT_ROLES:
            t = _leaf_text(x)
            if t:
                parts.append(t)
        for c in x.get("children") or []:
            walk(c)
    walk(n)
    return " ".join(parts)[:limit]


def _only_text_below(n: dict) -> bool:
    return all(_role(c) in LEAF_TEXT_ROLES and _only_text_below(c) for c in n.get("children") or [])


def _secret_leaf(n: dict) -> bool:
    if _role(n) not in ("group", "statictext", "text", "unknown", "code"):
        return False
    if not (n.get("identifier") or n.get("description")) or not _only_text_below(n):
        return False
    txt = text_of(n) if n.get("children") else _leaf_text(n)
    label = str(n.get("description") or n.get("title") or n.get("identifier") or "")
    return bool(txt) and (bool(_SECRETISH.search(txt)) or bool(_SECRET_LABEL.search(label)))


def _status_guess(title: str) -> int:
    t = title.strip().lower()
    if re.match(r"^(not found|404)\b", t):
        return 404
    if re.match(r"^(forbidden|403)\b", t):
        return 403
    return 200


def read_web(snap: Snapshot, web_path: Optional[Tuple[int, ...]] = None) -> WebView:
    wp = tuple(web_path) if web_path else (snap.web_paths[0] if snap.web_paths else None)
    root = snap.node(wp) if wp else None
    if root is None:
        return WebView(snap.url or "", snap.window_title, 0, [], [], [], [], [], "", [], [], [])
    has_input: Dict[Tuple[int, ...], bool] = {}

    def mark(n, p) -> bool:
        r = _role(n) in INPUT_ROLES
        for i, c in enumerate(n.get("children") or []):
            r = mark(c, p + (i,)) or r
        has_input[p] = r
        return r
    mark(root, wp)

    controls: List[WebControl] = []
    forms: List[dict] = []
    form_of_path: Dict[Tuple[int, ...], int] = {}
    alerts, statuses, regions, dialog_parts, headings = [], [], [], [], []
    lines: List[Tuple[str, str]] = []
    tables: List[dict] = []

    def form_for(path, ancestors) -> Optional[int]:
        """Innermost ancestor with an input (or the dialog); stop at table rows."""
        dialog_anc = None
        for ap, an in reversed(ancestors):
            r = _role(an)
            if r in ("row", "cell", "table"):
                break
            if _is_dialog(an) and dialog_anc is None:
                dialog_anc = (ap, an)
            if has_input.get(ap):
                return new_form(ap, an, ancestors)
        if dialog_anc is not None:
            return new_form(dialog_anc[0], dialog_anc[1], ancestors)
        return None

    def new_form(ap, an, ancestors) -> int:
        if ap not in form_of_path:
            in_dlg = _is_dialog(an) or any(_is_dialog(a) for p2, a in ancestors if p2 == ap[:len(p2)])
            form_of_path[ap] = len(forms)
            forms.append({"label": _name(an), "in_dialog": bool(in_dlg), "path": ap})
        return form_of_path[ap]

    def walk(n: dict, p: Tuple[int, ...], ctx: dict, ancestors: list) -> None:
        r = _role(n)
        ctx = dict(ctx)
        if _is_dialog(n):
            ctx["dialog"] = True
            ctx["context"] = _name(n) or ctx.get("context", "")
            t = text_of(n, 600)
            if t:
                dialog_parts.append(t)
        if _is_form(n):
            if p not in form_of_path:
                form_of_path[p] = len(forms)
                forms.append({"label": _name(n), "in_dialog": bool(ctx.get("dialog")), "path": p})
            ctx["form"] = form_of_path[p]
            if _name(n):
                ctx["context"] = _name(n)
        sub = n.get("subrole")
        if sub == "AXApplicationAlert":
            t = text_of(n, 600)
            if t:
                alerts.append(t)
        elif sub == "AXApplicationStatus":
            t = text_of(n, 600)
            if t:
                statuses.append(t)
        if _is_region(n):
            t = text_of(n, 600)
            regions.append((_name(n) + ": " if _name(n) else "") + t)
            if _name(n):
                ctx["context"] = _name(n)

        def add(kind, **kw):
            c = WebControl(kind, _name(n), p, ctx.get("context", ""), bool(ctx.get("dialog")),
                           ctx.get("form"), **kw)
            controls.append(c)
            return c

        if r == "heading":
            t = text_of(n, 300) or _name(n)
            if t:
                headings.append(t)
                lines.append((t, "heading"))
            return
        if r == "table":
            rows = []
            for row in n.get("children") or []:
                if _role(row) != "row":
                    continue
                rows.append([text_of(c, 300) for c in row.get("children") or [] if _role(c) in ("cell", "group")])
            rows = [x for x in rows if any(x)]
            cap = _name(n)
            if cap:
                lines.append((cap, "caption"))
            for x in rows:
                lines.append((" | ".join(x), "row"))
            tables.append({"caption": cap, "header": rows[0] if rows else [], "rows": rows[1:]})
            ctx["in_table"] = True                   # controls inside still count; text already taken
        elif r == "link":
            add("link", href=str(n.get("url") or ""))
            if not ctx.get("in_table") and _name(n):
                lines.append((_name(n), "text"))
            return
        elif r == "button":
            c = add("button")
            if c.form is None:
                c.form = form_for(p, ancestors)
            return
        elif r in ("textfield", "textarea", "searchfield", "combobox", "securetextfield"):
            secure = bool(n.get("secure")) or n.get("subrole") == "AXSecureTextField"
            v = "" if secure or not isinstance(n.get("value"), str) else n["value"]
            add("password" if secure else "textbox", value=v, has_value=bool(v))
            return
        elif r == "radiogroup":
            opts, paths, cur = [], {}, ""
            def radios(x, xp):
                nonlocal cur
                if _role(x) == "radiobutton" and _name(x):
                    opts.append(_name(x))
                    paths[_name(x).lower()] = xp
                    if x.get("value") in (1, True, "1") or x.get("selected"):
                        cur = _name(x)
                for i, c in enumerate(x.get("children") or []):
                    radios(c, xp + (i,))
            radios(n, p)
            add("radiogroup", options=opts, option_paths=paths, value=cur, has_value=bool(cur))
            return
        elif r == "popupbutton":
            opts = []
            def items(x):
                if _role(x) == "menuitem" and _name(x):
                    opts.append(_name(x))
                for c in x.get("children") or []:
                    items(c)
            items(n)
            v = n.get("value") if isinstance(n.get("value"), str) else ""
            add("combobox", options=opts, value=v, has_value=bool(v))
            return
        elif r == "checkbox":
            add("checkbox", value=str(n.get("value", "")))
            return
        elif _secret_leaf(n):
            raw = text_of(n) if n.get("children") else _leaf_text(n)
            c = add("secret_text", raw=raw)
            c.name = " ".join(str(n.get("description") or n.get("title") or n.get("identifier")).split())
            lines.append((mask_secrets(raw), "text"))
            return
        elif r in LEAF_TEXT_ROLES:
            if not ctx.get("in_table"):
                t = _leaf_text(n)
                if t:
                    lines.append((t, "text"))
            return
        kids = n.get("children") or []
        for i, c in enumerate(kids):
            walk(c, p + (i,), ctx, ancestors + [(p, n)])

    walk(root, wp, {}, [])
    # inputs with no explicit form join the synthetic form whose container holds them
    for c in controls:
        if c.form is None and c.kind in ("textbox", "password", "radiogroup", "combobox", "checkbox"):
            best = None
            for ap, fi in form_of_path.items():
                if c.path[:len(ap)] == ap and (best is None or len(ap) > len(best[0])):
                    best = (ap, fi)
            if best:
                c.form = best[1]
    title = snap.window_title or (root.get("title") or "")
    return WebView(snap.url or root.get("url") or "", title, _status_guess(title), controls, forms, alerts,
                   statuses, regions, " ".join(dialog_parts), headings, lines, tables)


# ------------------------------------------------- micro_agent (web_tools) --

def to_micro_page(snap: Snapshot, view: Optional[WebView] = None) -> Page:
    v = view or read_web(snap)
    controls: List[Control] = []
    raw: Dict[str, str] = {}
    paths: Dict[int, Tuple[int, ...]] = {}
    options: Dict[int, Dict[str, Tuple[int, ...]]] = {}
    for i, wc in enumerate(v.controls, 1):
        shown = ""
        if wc.kind == "password":
            shown = ""
        elif wc.kind in ("textbox", "radiogroup", "combobox"):
            shown = mask_secrets(wc.value)
        c = Control(i, wc.kind, mask_secrets(wc.name) if wc.kind != "secret_text" else wc.name,
                    mask_secrets(wc.context), wc.in_dialog, value=shown, options=list(wc.options),
                    href=wc.href, form=wc.form)
        if wc.kind == "secret_text":
            c.raw_ref = f"ax{i}"
            raw[c.raw_ref] = wc.raw
        if wc.kind in ("radiogroup", "combobox"):
            c._values = list(wc.options)  # type: ignore[attr-defined]
        controls.append(c)
        paths[i] = wc.path
        options[i] = dict(wc.option_paths)
    text = "\n".join(mask_secrets(t) for t, _k in v.lines if t.strip())
    page = Page(url=v.url, status=v.status, title=v.title, headings=[mask_secrets(h) for h in v.headings],
                controls=controls,
                forms=[Form(action="", method="post", label=f["label"], hidden={}, in_dialog=f["in_dialog"])
                       for f in v.forms],
                alerts=[mask_secrets(x) for x in v.alerts], statuses=[mask_secrets(x) for x in v.statuses],
                regions=[mask_secrets(x) for x in v.regions], dialog=mask_secrets(v.dialog), text=text, _raw=raw)
    page._live = {"snap": snap, "paths": paths, "options": options,  # type: ignore[attr-defined]
                  "truncated": bool(snap.truncated), "settled": True}
    return page


def _same_path(url: Optional[str], path: str) -> bool:
    got = urllib.parse.urlsplit(url or "").path.rstrip("/") or "/"
    return got == (urllib.parse.urlsplit(path).path.rstrip("/") or "/")


class MacBrowserBackend:
    """micro_agent browser interface over one new Safari window (loopback URLs only)."""

    def __init__(self, base_url: str, *, actor: Optional[Actor] = None, runtime: Any = None,
                 approve: Optional[Callable[[dict], bool]] = None, load_timeout: float = 15.0,
                 log: Optional[Callable[[str], None]] = None):
        self.base = check_loopback(base_url).rstrip("/")
        if actor is None:
            if runtime is None:
                from .observe import MacRuntime
                runtime = MacRuntime()
            actor = Actor(runtime, approve=approve, allow_apps={SAFARI}, log=log)
        self.actor = actor
        self.rt = actor.rt
        self.load_timeout = load_timeout
        self.window: Any = None
        self.app: Dict[str, Any] = {}
        self.page: Optional[Page] = None
        self.log: List[str] = []                     # method + path + outcome (no values)
        self.unverified: List[dict] = []

    # -- plumbing -----------------------------------------------------------
    def _url(self, path_or_url: str) -> str:
        base = self.page.url if self.page and self.page.url.startswith("http") else self.base + "/"
        return check_loopback(urllib.parse.urljoin(base, path_or_url))

    def _snap(self, timeout: Optional[float] = None) -> Snapshot:
        return snapshot_element(self.rt, self.window, app=self.app, max_nodes=self.actor.max_nodes,
                                timeout=timeout or self.actor.timeout)

    def _web_ready(self, snap: Snapshot) -> bool:
        """The web area exists, is fully read (not truncated), has content, and WebKit does
        not say it is still loading (AXLoaded False). Real Safari updates AXURL at commit,
        before the new document's AX tree is built, and under load an AX walk can hit its
        time budget; neither state may be read as 'the page'."""
        if not snap.ok or not snap.web_paths or snap.truncated:
            return False
        wp = snap.web_paths[0]
        if not (snap.node(wp) or {}).get("children"):
            return False
        try:
            loaded = self.rt.read(snap.element(wp), ("AXLoaded",)).get("AXLoaded")
        except Exception:                            # attribute not supported: no signal
            loaded = None
        return loaded is None or bool(loaded)

    def _load(self, expect_path: Optional[str] = None, timeout: Optional[float] = None) -> Page:
        """Re-observe until the page is ready and two consecutive reads agree (settled), then
        build the Page. ``expect_path``: also wait until the web area shows that path (a
        redirect elsewhere is accepted once the wait runs out). A snapshot truncated by the
        AX time budget is retried with a larger budget. If the wait runs out the last read is
        returned marked ``_live["settled"] = False`` (and ``truncated`` when partial) so
        readers that certify effects can refuse it."""
        t_end = self.rt.now() + (self.load_timeout if timeout is None else timeout)
        prev = None
        budget = self.actor.timeout
        snap = self._snap(budget)
        settled = False
        while True:
            sig = (snap.url, snap.render(400)) if snap.ok else None
            at = expect_path is None or _same_path(snap.url, expect_path)
            if sig is not None and sig == prev and self._web_ready(snap) and at:
                settled = True
                break
            if self.rt.now() >= t_end:
                break
            if snap.truncated and snap.reason == "timeout":
                budget = min(budget * 2, 30.0)
            prev = sig
            self.rt.sleep(self.actor.poll)
            snap = self._snap(budget)
        if not snap.ok or not snap.web_paths:
            raise WebError("the Safari window has no web page (closed or not loaded)")
        self.page = to_micro_page(snap)
        self.page._live["settled"] = settled  # type: ignore[attr-defined]
        return self.page

    def wait_for(self, pred: Callable[[Page], bool], timeout: Optional[float] = None) -> Optional[Page]:
        """Re-read the CURRENT page (no navigation, no action) until ``pred(page)`` holds;
        None when it never does within ``timeout``. For readers that know what a complete
        page must contain (e.g. the API-key table) and must not certify a half-built one."""
        t_end = self.rt.now() + (self.load_timeout if timeout is None else timeout)
        while True:
            try:
                p = self._load(timeout=max(0.0, min(self.load_timeout, t_end - self.rt.now())))
            except WebError:
                p = None
            if p is not None and pred(p):
                return p
            if self.rt.now() >= t_end:
                return None
            self.rt.sleep(self.actor.poll)

    def _live(self, page: Page, n: int) -> Tuple[Control, Snapshot, Tuple[int, ...]]:
        c = page.control(n)
        live = getattr(page, "_live", None)
        if c is None or live is None or n not in live["paths"]:
            raise WebError(f"no control {n}")
        return c, live["snap"], live["paths"][n]

    # -- the interface --------------------------------------------------------
    def open(self, path_or_url: str = "/") -> Page:
        url = self._url(path_or_url)
        if self.window is None:
            r = self.actor.open_url(url)
            if r.window is not None:
                self.window = r.window
                self.app = (r.after.app if r.after is not None else {}) or self.actor._app(SAFARI) or {}
        else:
            r = self.actor.navigate(self.window, url, app=self.app)
        path = urllib.parse.urlsplit(url).path
        self.log.append(f"GET {path} -> {r.outcome.value}")
        if r.outcome is Outcome.REFUSED or self.window is None:
            raise WebError(f"open refused: {r.reason}")
        if r.outcome is Outcome.UNVERIFIED:
            self.unverified.append(r.to_dict())
        redirected = bool((getattr(r, "detail", None) or {}).get("redirected_to"))
        return self._load(expect_path=None if redirected else path)

    def click(self, page: Page, n: int) -> Page:
        c, snap, path = self._live(page, n)
        if c.role not in ("link", "button"):
            raise WebError(f"control {n} ({c.role}) is not clickable")
        before = (snap.url, snap.render(400)) if snap.ok else None
        r = self.actor.press(snap, path, Expect(any_change=True))
        self.log.append(f"CLICK {c.role} -> {r.outcome.value}")
        if r.outcome is Outcome.REFUSED:
            raise WebError(f"click refused: {r.reason}")
        if r.outcome is Outcome.UNVERIFIED:
            self.unverified.append(r.to_dict())
        self._wait_change(before)
        return self._load()

    def _wait_change(self, before, timeout: float = 5.0) -> None:
        """After a press, wait until the page differs from the pre-click page (navigation
        or in-page update) before settling, so the old page is not read as 'settled'.
        Clicks that change nothing just time out and fall through to _load()."""
        if before is None:
            return
        t_end = self.rt.now() + min(timeout, self.load_timeout)
        while self.rt.now() < t_end:
            s = self._snap()
            if s.ok and (s.url, s.render(400)) != before:
                return
            self.rt.sleep(self.actor.poll)

    def fill(self, page: Page, n: int, value: str) -> Page:
        c, snap, path = self._live(page, n)
        if c.role not in ("textbox", "password"):
            raise WebError(f"control {n} is not a text input")
        v = Revealed(value) if c.role == "password" or (self.actor.secrets is None and _SECRETISH.search(value)) \
            else value
        r = self.actor.set_value(snap, path, v)
        self.log.append(f"FILL {c.role} -> {r.outcome.value}")
        if r.outcome is Outcome.REFUSED:
            raise WebError(f"fill refused: {r.reason}")
        if r.outcome is Outcome.UNVERIFIED:
            self.unverified.append(r.to_dict())
        page.values[n] = value
        c.value = "•" * 8 if c.role == "password" else mask_secrets(value)
        return page

    def select(self, page: Page, n: int, option: str) -> Page:
        c, snap, path = self._live(page, n)
        if c.role not in ("radiogroup", "combobox"):
            raise WebError(f"control {n} is not a choice")
        if option.lower() not in [o.lower() for o in c.options]:
            raise WebError(f"option not offered by control {n}")
        opt_paths = page._live["options"].get(n) or {}  # type: ignore[attr-defined]
        if c.role == "radiogroup" and option.lower() in opt_paths:
            r = self.actor.press(snap, opt_paths[option.lower()], Expect(selected=True))
        else:
            r = self.actor.select_option(snap, path, option)
        self.log.append(f"SELECT {c.role} -> {r.outcome.value}")
        if r.outcome is Outcome.REFUSED:
            raise WebError(f"select refused: {r.reason}")
        if r.outcome is Outcome.UNVERIFIED:
            self.unverified.append(r.to_dict())
        canon = c.options[[o.lower() for o in c.options].index(option.lower())]
        page.values[n] = canon
        c.value = canon
        return page

    def read_raw(self, page: Page, n: int) -> str:
        """Raw text of a secret_text control, for the secret store only."""
        c = page.control(n)
        if c is None or c.role != "secret_text":
            raise WebError(f"control {n} is not readable")
        return page._raw.get(c.raw_ref, "")

    def close(self) -> bool:
        """Close OUR window (its close button); never another window."""
        if self.window is None:
            return False
        snap = self._snap()
        hits = snap.find(lambda x: x.get("subrole") == "AXCloseButton")
        ok = bool(hits) and self.rt.perform(snap.element(hits[0]), "AXPress")
        self.window = None
        return ok


@contextlib.contextmanager
def micro_browser(factory: Callable[[str], Any]) -> Iterator[None]:
    """Make micro_agent.run_dev_dashboard build ``factory(dashboard_url)`` instead of its
    loopback HTML client, for the duration of the block."""
    from .. import micro_agent
    orig = micro_agent.LoopbackBrowser
    micro_agent.LoopbackBrowser = factory  # type: ignore[assignment]
    try:
        yield
    finally:
        micro_agent.LoopbackBrowser = orig  # type: ignore[assignment]


# ---------------------------------------------------- teach-once skill env --

def to_teach_page(snap: Snapshot, view: Optional[WebView] = None):
    """A testbeds.teach_once.browser.Page built from the AX tree (same attributes)."""
    from testbeds.teach_once.browser import Page as TeachPage
    v = view or read_web(snap)
    sid = next(_snap_ids)
    elements: List[dict] = []
    counts: Dict[str, int] = {}
    for wc in v.controls:
        role = {"link": "link", "button": "button", "textbox": "textbox", "password": "textbox"}.get(wc.kind)
        if role is None:
            continue
        el = {"role": role, "name": wc.name, "form": wc.form, "ax_path": list(wc.path), "snap": sid}
        if role == "link":
            el["href"] = wc.href
        if role == "textbox":
            el.update({"value": "" if wc.kind == "password" else wc.value, "secure": wc.kind == "password",
                       "invalid": False, "field": ""})
        el["ordinal"] = counts.get(role, 0)
        counts[role] = el["ordinal"] + 1
        elements.append(el)
    p = TeachPage.__new__(TeachPage)
    p.url, p.status, p.title = v.url, v.status, v.title
    p.forms = [{"action": "", "method": "post", "label": f["label"], "hidden": {}} for f in v.forms]
    p.tables = [{"caption": t["caption"], "header": t["header"], "rows": t["rows"]} for t in v.tables]
    p.alerts, p.status_msgs, p.headings = list(v.alerts), list(v.statuses), list(v.headings)
    p.text_lines = [t for t, k in v.lines if k in ("text", "heading", "caption") and t.strip()]
    p.elements = elements
    p.snap_id = sid
    p._snap = snap
    return p


class MacEnv:
    """teach-once env over live Safari: one window per web app, file ops delegated to ``base``.

    ``broker`` (a secrets.SecretBroker): ``secret(name)`` returns a handle and the value is
    substituted by the actor at the moment of typing (the runner never holds it)."""

    def __init__(self, base: Any, urls: Dict[str, str], *, actor: Optional[Actor] = None, runtime: Any = None,
                 broker: Any = None, approve: Optional[Callable[[dict], bool]] = None,
                 load_timeout: float = 15.0, log: Optional[Callable[[str], None]] = None):
        self.base = base
        self.urls = {a: check_loopback(u).rstrip("/") for a, u in urls.items()}
        if actor is None:
            if runtime is None:
                from .observe import MacRuntime
                runtime = MacRuntime()
            actor = Actor(runtime, approve=approve, allow_apps={SAFARI}, log=log)
        self._names: Dict[str, str] = {}
        if broker == "env":                          # a broker over the base env's own secret lookup
            from testbeds.teach_once.wiring import _EnvProvider
            from ..secrets import SecretBroker
            broker = SecretBroker(_EnvProvider(base.secret, self._names))
        if broker is not None:
            actor.secrets = broker
        self.actor, self.rt, self.broker = actor, actor.rt, broker
        self.load_timeout = load_timeout
        self.windows: Dict[str, Any] = {}
        self.app_info: Dict[str, Any] = {}
        self.pages: Dict[str, Any] = {}
        self.ui_actions = 0
        self.unverified: List[dict] = []

    def __getattr__(self, k):
        if k == "redact_obj":
            if self.__dict__.get("broker") is None:
                raise AttributeError(k)
            return self.__dict__["broker"].redact_obj
        return getattr(self.__dict__["base"], k)

    # -- web --------------------------------------------------------------------
    def is_web(self, app: str) -> bool:
        return app in self.urls

    def _snap(self, app: str) -> Snapshot:
        return snapshot_element(self.rt, self.windows[app], app=self.app_info, max_nodes=self.actor.max_nodes,
                                timeout=self.actor.timeout)

    def page(self, app: str):
        if app not in self.windows:
            return None
        t_end = self.rt.now() + self.load_timeout
        prev, snap = None, self._snap(app)
        while True:
            sig = (snap.url, snap.render(400)) if snap.ok else None
            if (sig is not None and sig == prev and snap.web_paths) or self.rt.now() >= t_end:
                break
            prev = sig
            self.rt.sleep(self.actor.poll)
            snap = self._snap(app)
        if not snap.ok or not snap.web_paths:
            return None
        p = to_teach_page(snap)
        self.pages[app] = p
        return p

    def _note(self, r) -> None:
        self.ui_actions += 1
        if r.outcome is Outcome.UNVERIFIED:
            self.unverified.append(r.to_dict())
        if r.outcome is Outcome.REFUSED:
            raise RuntimeError(f"{r.action} refused: {r.reason}")

    def open(self, app: str, path: str):
        if app not in self.urls:
            raise ValueError(f"refusing non-task app {app!r}")
        url = check_loopback(urllib.parse.urljoin(self.urls[app] + "/", path))
        if app not in self.windows:
            r = self.actor.open_url(url)
            if r.window is not None:
                self.windows[app] = r.window
                self.app_info = (r.after.app if r.after is not None else {}) or self.actor._app(SAFARI) or {}
        else:
            self.switch_window(app)
            r = self.actor.navigate(self.windows[app], url, app=self.app_info)
        self._note(r)
        return self.page(app)

    def switch_window(self, app: str) -> None:
        snap = self._snap(app)
        self.actor.raise_window(snap)

    def switch(self, app: str):
        if app not in self.windows:
            return self.open(app, "/")
        self.switch_window(app)
        return self.page(app)

    def _path_for(self, app: str, el: dict) -> Tuple[Snapshot, Tuple[int, ...]]:
        cur = self.pages.get(app)
        if cur is not None and el.get("snap") == getattr(cur, "snap_id", None) and el.get("ax_path"):
            return cur._snap, tuple(el["ax_path"])
        fresh = self.page(app)
        if fresh is None:
            raise RuntimeError(f"{app}: no page")
        for e in fresh.elements:
            if e["role"] == el.get("role") and e.get("name") == el.get("name") and \
                    (el.get("ordinal") is None or e["ordinal"] == el.get("ordinal")):
                return fresh._snap, tuple(e["ax_path"])
        from testbeds.teach_once.browser import ElementNotFound
        raise ElementNotFound(f"{el.get('role')} {el.get('name')!r} not on the live page")

    def click(self, app: str, el: dict):
        self.switch_window(app)
        snap, path = self._path_for(app, el)
        r = self.actor.press(snap, path, Expect(any_change=True))
        self._note(r)
        return self.page(app)

    def type(self, app: str, el: dict, value: str):
        self.switch_window(app)
        snap, path = self._path_for(app, el)
        node = snap.node(path) or {}
        from ..secrets import handles_in
        v = value
        if not handles_in(value) and (node.get("secure") or el.get("secure")):
            v = Revealed(value)            # resolved upstream (no broker): the trusted runner sink
        r = self.actor.set_value(snap, path, v)
        self._note(r)
        el["value"] = "" if el.get("secure") else value
        return el

    # -- secrets ----------------------------------------------------------------
    def secret(self, name: str) -> Optional[str]:
        if self.broker is None:
            return self.base.secret(name)
        key = re.sub(r"[^A-Za-z0-9_.\-]+", "_", (name or "").strip())[:64] or "secret"
        self._names[key] = name
        if not self.broker.has(key):
            return None
        self.broker.reveal_for_check(key)            # remembered for redaction
        return self.broker.handle(key)

    def actions(self) -> int:
        return self.base.actions() + self.ui_actions
