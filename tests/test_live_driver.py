"""Live macOS control layer (scaffold/agent/desktop/live) on a FAKE AX runtime.

No real UI, no PyObjC calls, no model calls: FakeRuntime + FakeSafari render the testbeds'
real 127.0.0.1 HTML into an AX-shaped element tree (links, buttons, labelled text fields,
secure fields, radio groups, forms, tables, live regions, dialogs) and implement AXPress /
AXValue / AXConfirm / menu items the way Safari exposes them, so observe -> actions ->
MacBrowserBackend / MacEnv -> micro-agent / skill runner -> testbed verifier runs end to end.

Real-UI tests are at the bottom and only run with AWOS_LIVE_UI=1 inside the guest VM with
Accessibility granted to the running process.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from scaffold.agent.desktop.grounding import Target, ground
from scaffold.agent.desktop.live import actions as A
from scaffold.agent.desktop.live import driver as D
from scaffold.agent.desktop.live import observe as O
from scaffold.agent.desktop.live import probe as P
from scaffold.agent.desktop.secrets import MemoryProvider, SecretBroker

SAFARI_PID, OTHER_PID = 500, 600
VM = lambda: True  # noqa: E731


# =========================================================== fake AX runtime ==

class El:
    def __init__(self, role, pid=SAFARI_PID, **attrs):
        self.attrs = {"AXRole": role, **attrs}
        self.kids = []
        self.parent = None
        self.pid = pid
        self.acts = {}
        self.settable = set()
        self.on_set = None
        self.meta = {}

    def add(self, child):
        child.parent = self
        self.kids.append(child)
        return child

    def __repr__(self):
        return f"El({self.attrs.get('AXRole')} {self.attrs.get('AXTitle') or self.attrs.get('AXDescription') or ''})"


class FakeRuntime:
    def __init__(self, tick=0.0):
        self.t = 0.0
        self.tick = tick
        self.apps = {}          # pid -> (info, app element)
        self.front = None
        self.events = []
        self.opened = []
        self.reads = 0

    def add_app(self, pid, bundle, name, app_el):
        self.apps[pid] = ({"pid": pid, "bundle_id": bundle, "name": name}, app_el)

    # reads
    def read(self, el, names):
        self.reads += 1
        self.t += self.tick
        if el is None:
            return {}
        out = {}
        for n in names:
            if n in el.attrs and el.attrs[n] is not None:
                out[n] = el.attrs[n]
            elif callable(el.meta.get("computed")):
                v = el.meta["computed"](n)
                if v is not None:
                    out[n] = v
        return out

    def children(self, el):
        return list(el.kids) if el is not None else []

    def same(self, a, b):
        return a is b

    def pid(self, el):
        return el.pid if el is not None else None

    def app_element(self, pid):
        return self.apps[pid][1]

    def frontmost(self):
        return self.apps[self.front][0] if self.front in self.apps else None

    def running_apps(self):
        return [v[0] for v in self.apps.values()]

    def enable_web_accessibility(self, pid):
        pass

    # writes
    def set_attr(self, el, name, value):
        if name not in el.settable:
            return False
        el.attrs[name] = value
        if el.on_set:
            el.on_set(name, value)
        return True

    def settable(self, el, name):
        return name in el.settable

    def actions(self, el):
        return list(el.acts)

    def perform(self, el, action):
        f = el.acts.get(action)
        if f is None:
            return False
        f()
        return True

    def activate(self, pid):
        self.front = pid
        return True

    def open_with(self, app, target):
        self.opened.append((app, target))
        return False

    def can_post_events(self):
        return False

    def post_click(self, x, y):
        self.events.append(("click", x, y))
        return False

    def post_text(self, pid, text):
        self.events.append(("text", pid))
        return False

    def post_key(self, pid, keycode, flags=0):
        self.events.append(("key", pid, keycode))
        return False

    def scroll_wheel(self, x, y, lines):
        return False

    def element_at_point(self, x, y):
        return None

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


# ============================================== fake Safari over real HTML ==

class _AXBuilder(HTMLParser):
    """HTML -> AX-like El tree, the way WebKit exposes the testbed pages."""
    CONTAINER = {"p", "div", "li", "ul", "ol", "nav", "header", "main", "section", "article", "span", "strong",
                 "legend", "footer", "em", "b", "small"}

    def __init__(self, safari, window, web, base):
        super().__init__(convert_charrefs=True)
        self.s, self.win, self.base = safari, window, base
        self.stack = [("#root", {}, web)]
        self.title = ""
        self._in_title = False
        self._skip = 0
        self.labels = {}          # for-id -> label El
        self.ids = {}             # id -> El
        self.forms = []
        self.form = None
        self.table = None
        self.select = None

    @property
    def cur(self):
        return self.stack[-1][2]

    def _push(self, tag, a, el):
        self.cur.add(el)
        self.stack.append((tag, a, el))
        if a.get("id"):
            self.ids[a["id"]] = el

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style", "head"):
            self._skip += 1 if tag != "head" else 0
        if tag == "title":
            self._in_title = True
            return
        if self._skip or tag in ("html", "body", "head", "meta", "br", "thead", "tbody", "hr"):
            return
        role = a.get("role", "")
        if tag == "input":
            self._input(a)
            return
        if tag == "form":
            f = {"action": a.get("action", ""), "method": (a.get("method") or "get").lower(), "hidden": {},
                 "controls": []}
            self.forms.append(f)
            self.form = f
            el = El("AXGroup", AXSubrole="AXLandmarkForm", AXDescription=a.get("aria-label") or None)
            self._push(tag, a, el)
            return
        if tag == "a" and "href" in a:
            url = urllib.parse.urljoin(self.base, a["href"])
            el = El("AXButton" if role == "button" else "AXLink", AXURL=url,
                    AXDescription=a.get("aria-label") or None)
            el.acts["AXPress"] = lambda u=url: self.s.load(self.win, u)
            self._push(tag, a, el)
            return
        if tag == "button":
            el = El("AXButton", AXDescription=a.get("aria-label") or None)
            f = self.form
            if f is not None and (a.get("type") or "submit") == "submit":
                el.acts["AXPress"] = lambda f=f, a=a: self.s.submit(self.win, f, (a.get("name"), a.get("value")))
            else:
                el.acts["AXPress"] = lambda: None
            self._push(tag, a, el)
            return
        if tag == "select":
            el = El("AXPopUpButton", AXDescription=a.get("aria-label") or None)
            self.select = el
            self._push(tag, a, el)
            el.add(El("AXMenu"))
            return
        if tag == "option" and self.select is not None:
            mi = El("AXMenuItem")
            self.select.kids[0].add(mi)
            self.stack.append((tag, a, mi))
            return
        if tag == "table":
            el = El("AXTable", AXDescription=a.get("aria-label") or None)
            self.table = el
            self._push(tag, a, el)
            return
        if tag == "caption":
            self.stack.append((tag, a, El("AXGroup")))     # text goes to the table's description
            return
        if tag == "tr":
            self._push(tag, a, El("AXRow"))
            return
        if tag in ("td", "th"):
            self._push(tag, a, El("AXCell"))
            return
        if tag in ("h1", "h2", "h3"):
            self._push(tag, a, El("AXHeading"))
            return
        if tag == "fieldset" and role == "radiogroup":
            self._push(tag, a, El("AXRadioGroup", AXDescription=a.get("aria-label") or None))
            return
        if tag == "label":
            el = El("AXGroup")
            if a.get("for"):
                self.labels[a["for"]] = el
            self._push(tag, a, el)
            return
        if tag == "code":
            el = El("AXGroup", AXDOMIdentifier=a.get("id") or None, AXDescription=a.get("aria-label") or None)
            self._push(tag, a, el)
            return
        sub = {"alert": "AXApplicationAlert", "status": "AXApplicationStatus", "region": "AXLandmarkRegion",
               "alertdialog": "AXApplicationAlertDialog", "dialog": "AXApplicationDialog"}.get(role)
        el = El("AXGroup", AXSubrole=sub, AXDescription=(a.get("aria-label") or None) if sub else None)
        self._push(tag, a, el)

    def _input(self, a):
        t = (a.get("type") or "text").lower()
        f = self.form
        if t == "hidden":
            if f is not None:
                f["hidden"][a.get("name", "")] = a.get("value", "")
            return
        if t in ("submit", "button"):
            el = El("AXButton", AXTitle=a.get("value") or "Submit")
            if f is not None:
                el.acts["AXPress"] = lambda f=f: self.s.submit(self.win, f, (a.get("name"), a.get("value")))
            self.cur.add(el)
            return
        if t == "radio":
            el = El("AXRadioButton", AXValue=0)
            el.meta.update(field=a.get("name", ""), value=a.get("value", ""), id=a.get("id", ""))

            def press(el=el):
                grp = el.parent
                while grp is not None and grp.attrs["AXRole"] != "AXRadioGroup":
                    grp = grp.parent
                for r in self.s.walk(grp or el.parent):
                    if r.attrs["AXRole"] == "AXRadioButton" and r.meta.get("field") == el.meta["field"]:
                        r.attrs["AXValue"] = 0
                el.attrs["AXValue"] = 1
            el.acts["AXPress"] = press
            self.cur.add(el)
            if a.get("id"):
                self.ids[a["id"]] = el
            if f is not None:
                f["controls"].append(el)
            return
        sec = t == "password"
        el = El("AXTextField", AXSubrole="AXSecureTextField" if sec else None,
                AXDescription=a.get("aria-label") or None, AXFocused=False)
        el.meta.update(field=a.get("name", ""), real=a.get("value", ""), secure=sec, id=a.get("id", ""))
        el.settable |= {"AXValue", "AXFocused"}
        if sec:
            el.meta["computed"] = lambda n, el=el: ("•" * len(el.meta["real"]) if n == "AXValue" else
                                                     len(el.meta["real"]) if n == "AXNumberOfCharacters" else None)
        else:
            el.attrs["AXValue"] = a.get("value", "")

        def on_set(name, v, el=el):
            if name == "AXValue":
                el.meta["real"] = str(v)
                if el.meta["secure"]:
                    el.attrs.pop("AXValue", None)
        el.on_set = on_set
        self.cur.add(el)
        if a.get("id"):
            self.ids[a["id"]] = el
        if f is not None:
            f["controls"].append(el)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if self._skip:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                t, a, el = self.stack[i]
                del self.stack[i:]
                if tag == "caption" and self.table is not None:
                    txt = " ".join(x.attrs.get("AXValue", "") for x in el.kids)
                    self.table.attrs["AXDescription"] = txt.strip() or None
                if tag == "form":
                    self.form = None
                if tag == "select":
                    self.select = None
                if tag == "option":
                    pass
                return

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        txt = " ".join(data.split())
        if not txt:
            return
        t, a, el = self.stack[-1]
        if t == "option":
            el.attrs["AXTitle"] = txt
            return
        el.add(El("AXStaticText", AXValue=txt))

    def finish(self):
        for fid, lab in self.labels.items():
            target = self.ids.get(fid)
            if target is None:
                continue
            if target.attrs["AXRole"] in ("AXRadioButton",):
                target.attrs["AXTitle"] = " ".join(x.attrs.get("AXValue", "") for x in lab.kids).strip()
            else:
                target.attrs["AXTitleUIElement"] = lab
        for el in self.s.walk(self.stack[0][2]):
            if el.attrs["AXRole"] == "AXPopUpButton":
                items = el.kids[0].kids
                if items:
                    el.attrs["AXValue"] = items[0].attrs.get("AXTitle", "")


class FakeSafari:
    """Safari as AX sees it: menu bar File > New Window, windows with an address field, a close
    button and a web area. Pages are fetched for real from 127.0.0.1 with a cookie jar."""

    def __init__(self, rt, running=True):
        self.rt = rt
        self.app = El("AXApplication")
        self.windows = []
        self.jar = CookieJar()
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                              urllib.request.HTTPCookieProcessor(self.jar))
        self.loads = []
        self.posts = []
        bar = self.app.add(El("AXMenuBar"))
        file_ = bar.add(El("AXMenuBarItem", AXTitle="File"))
        menu = file_.add(El("AXMenu"))
        nw = menu.add(El("AXMenuItem", AXTitle="New Window"))
        nw.acts["AXPress"] = self.new_window
        self.app.meta["computed"] = self._app_attr
        self.app.attrs["AXMenuBar"] = bar
        if running:
            rt.add_app(SAFARI_PID, A.SAFARI, "Safari", self.app)
            rt.front = SAFARI_PID

    def _app_attr(self, n):
        if n == "AXWindows":
            return list(self.windows)
        if n in ("AXFocusedWindow", "AXMainWindow"):
            return self.windows[-1] if self.windows else None
        return None

    @staticmethod
    def walk(root):
        stack = [root]
        while stack:
            e = stack.pop()
            yield e
            stack.extend(e.kids)

    def new_window(self, url=None):
        w = El("AXWindow", AXTitle="Start Page")
        close = w.add(El("AXButton", AXSubrole="AXCloseButton"))
        close.acts["AXPress"] = lambda w=w: self.windows.remove(w)
        tb = w.add(El("AXToolbar"))
        addr = tb.add(El("AXTextField", AXIdentifier="WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD",
                         AXDescription="Address and Search", AXValue=""))
        addr.settable |= {"AXValue", "AXFocused"}
        addr.acts["AXConfirm"] = lambda w=w, addr=addr: self.load(w, addr.attrs.get("AXValue", ""))
        sa = w.add(El("AXScrollArea"))
        web = sa.add(El("AXWebArea"))
        w.meta.update(web=web, addr=addr)
        w.acts["AXRaise"] = lambda w=w: (self.windows.remove(w), self.windows.append(w))
        w.settable.add("AXMain")
        self.windows.append(w)
        if url:
            self.load(w, url)
        return w

    def _render(self, w, url, raw):
        web = w.meta["web"]
        web.kids = []
        web.attrs["AXURL"] = url
        b = _AXBuilder(self, w, web, url)
        b.feed(raw)
        b.finish()
        w.attrs["AXTitle"] = " ".join(b.title.split()) or url
        w.meta["addr"].attrs["AXValue"] = url

    def load(self, w, url, data=None):
        assert urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost"), url
        self.loads.append(url)
        try:
            r = self.op.open(url, data=data, timeout=10)
            raw, final = r.read(), r.geturl()
        except urllib.error.HTTPError as e:
            raw, final = e.read(), url
        self._render(w, final, raw.decode("utf-8", "replace"))

    def submit(self, w, form, button):
        fields = dict(form["hidden"])
        for c in form["controls"]:
            if c.attrs["AXRole"] == "AXRadioButton":
                if c.attrs.get("AXValue") == 1:
                    fields[c.meta["field"]] = c.meta["value"]
            elif c.meta.get("field"):
                fields[c.meta["field"]] = c.meta["real"] if c.meta.get("secure") else c.attrs.get("AXValue", "")
        if button and button[0]:
            fields[button[0]] = button[1] or ""
        url = urllib.parse.urljoin(w.meta["web"].attrs.get("AXURL", ""), form["action"] or "")
        body = urllib.parse.urlencode(fields)
        self.posts.append((url, sorted(fields)))
        if form["method"] == "post":
            self.load(w, url, body.encode())
        else:
            self.load(w, url + ("?" + body if body else ""))


def owner_window(rt, saf):
    """A pre-existing Safari window that must never be navigated."""
    w = saf.new_window()
    w.meta["web"].attrs["AXURL"] = "https://owner.example/inbox"
    w.attrs["AXTitle"] = "Owner"
    return w


def actor(rt, **kw):
    kw.setdefault("vm_check", VM)
    kw.setdefault("settle", 1.0)
    return A.Actor(rt, **kw)


# ============================================================ a static page ==

def static_page(rt, *, secure_value="", destructive=False):
    """TextEdit-like window built by hand (no HTTP)."""
    app = El("AXApplication", pid=700)
    w = app.add(El("AXWindow", pid=700, AXTitle="Doc", AXPosition=(0, 0), AXSize=(800, 600)))
    name = w.add(El("AXTextField", pid=700, AXTitle="Name", AXValue="", AXPosition=(10, 10), AXSize=(200, 20)))
    name.settable |= {"AXValue", "AXFocused"}
    pw = w.add(El("AXTextField", pid=700, AXSubrole="AXSecureTextField", AXTitle="Password",
                  AXPosition=(10, 40), AXSize=(200, 20)))
    pw.settable |= {"AXValue"}
    pw.meta["real"] = secure_value
    pw.meta["computed"] = lambda n: len(pw.meta["real"]) if n == "AXNumberOfCharacters" else None
    pw.on_set = lambda n, v: (pw.meta.__setitem__("real", str(v)), pw.attrs.pop("AXValue", None))
    status = w.add(El("AXStaticText", pid=700, AXValue="idle", AXPosition=(10, 80), AXSize=(200, 20)))
    ok = w.add(El("AXButton", pid=700, AXTitle="Delete everything" if destructive else "Apply",
                  AXPosition=(10, 120), AXSize=(80, 24)))
    ok.acts["AXPress"] = lambda: status.attrs.__setitem__("AXValue", "pressed")
    noop = w.add(El("AXButton", pid=700, AXTitle="Nothing", AXPosition=(100, 120), AXSize=(80, 24)))
    noop.acts["AXPress"] = lambda: None
    off = w.add(El("AXButton", pid=700, AXTitle="Off", AXEnabled=False, AXPosition=(200, 120), AXSize=(80, 24)))
    off.acts["AXPress"] = lambda: None
    app.meta["computed"] = lambda n: [w] if n == "AXWindows" else (w if n in ("AXFocusedWindow",) else None)
    rt.add_app(700, A.TEXTEDIT, "TextEdit", app)
    rt.front = 700
    return {"win": w, "name": name, "pw": pw, "status": status, "ok": ok}


# =================================================================== observe ==

def test_observe_tree_shape_secure_omitted_and_grounding_compatible():
    rt = FakeRuntime()
    s = static_page(rt, secure_value="hunter2hunter2")
    snap = O.observe(rt)                                   # frontmost app, focused window
    assert snap.ok and not snap.truncated and snap.app["bundle_id"] == A.TEXTEDIT
    assert snap.tree["role"] == "AXWindow" and snap.window_title == "Doc"
    pw = snap.node((0, 1))
    assert pw["secure"] is True and "value" not in pw
    assert "hunter2" not in json.dumps(snap.tree)
    assert snap.node((0, 0))["frame"] == {"x": 10.0, "y": 10.0, "w": 200.0, "h": 20.0}
    assert snap.node((0, 5))["enabled"] is False
    assert snap.element((0, 3)) is s["ok"]
    g = ground(snap.grounding_input(), Target(role="button", name="Apply"))
    assert g.ok and tuple(g.match.path) == (0, 3)
    assert '"Apply"' in snap.render()


def test_observe_budget_and_timeout_truncate():
    rt = FakeRuntime()
    static_page(rt)
    snap = O.observe(rt, "TextEdit", max_nodes=3)
    assert snap.truncated and snap.reason == "budget" and snap.nodes == 3
    assert not ground(snap.grounding_input(), Target(role="button", name="Apply")).ok   # refuses partial
    rt2 = FakeRuntime(tick=1.0)
    static_page(rt2)
    snap2 = O.observe(rt2, "TextEdit", timeout=2.0)
    assert snap2.truncated and snap2.reason == "timeout"


def test_observe_no_app_no_window():
    rt = FakeRuntime()
    assert O.observe(rt, "Nope").reason == "no_app"
    saf = FakeSafari(rt)
    assert O.observe(rt, "Safari").reason == "no_window"


def test_observe_web_area_url_and_link_names_from_content(tmp_path):
    rt = FakeRuntime()
    saf = FakeSafari(rt)
    srv, url = _tiny_server(tmp_path)
    try:
        w = saf.new_window(url + "/")
        snap = O.observe(rt, "Safari")
        assert snap.url == url + "/" and snap.web_paths
        assert snap.find(lambda n: n.get("role") == "AXLink" and n.get("title") == "Next page")
        lab = snap.find(lambda n: n.get("role") == "AXTextField" and n.get("title") == "Email")
        assert lab, "label via AXTitleUIElement"
    finally:
        srv.shutdown()


# =================================================================== actions ==

def test_press_verified_unverified_disabled():
    rt = FakeRuntime()
    s = static_page(rt)
    ac = actor(rt)
    snap = ac.observe("TextEdit")
    r = ac.press(snap, Target(role="button", name="Apply"))
    assert r.outcome is A.Outcome.VERIFIED and r.method == "ax_press"
    assert s["status"].attrs["AXValue"] == "pressed"
    r2 = ac.press(ac.observe("TextEdit"), Target(role="button", name="Nothing"))
    assert r2.outcome is A.Outcome.UNVERIFIED and r2.reason == "nothing changed"
    r3 = ac.press(ac.observe("TextEdit"), (0, 5))
    assert r3.outcome is A.Outcome.REFUSED and "disabled" in r3.reason
    r4 = ac.press(snap, Target(role="button", name="Apply"), A.Expect(appears="pressed"))
    assert r4.outcome is A.Outcome.VERIFIED


def test_destructive_needs_approval_default_deny():
    rt = FakeRuntime()
    s = static_page(rt, destructive=True)
    ac = actor(rt)
    r = ac.press(ac.observe("TextEdit"), (0, 3))
    assert r.outcome is A.Outcome.REFUSED and r.detail.get("destructive")
    assert s["status"].attrs["AXValue"] == "idle"
    asked = []
    ac2 = actor(rt, approve=lambda req: asked.append(req) or True)
    r2 = ac2.press(ac2.observe("TextEdit"), (0, 3))
    assert r2.outcome is A.Outcome.VERIFIED and asked[0]["kind"] == "destructive"
    assert asked[0]["name"] == "Delete everything"


def test_gates_host_guard_app_allowlist_and_policy():
    rt = FakeRuntime()
    static_page(rt)
    host = actor(rt, vm_check=lambda: False)
    r = host.press(host.observe("TextEdit"), (0, 3))
    assert r.outcome is A.Outcome.REFUSED and "guest VM" in r.reason
    only_safari = actor(rt, allow_apps={A.SAFARI})
    r = only_safari.press(only_safari.observe("TextEdit"), (0, 3))
    assert r.outcome is A.Outcome.REFUSED and "not an allowed test target" in r.reason
    # a terminal is forbidden by app_policy even if someone allow-lists it
    term = El("AXApplication", pid=800)
    tw = term.add(El("AXWindow", pid=800, AXTitle="zsh"))
    b = tw.add(El("AXButton", pid=800, AXTitle="Run"))
    b.acts["AXPress"] = lambda: None
    term.meta["computed"] = lambda n: [tw] if n == "AXWindows" else (tw if n == "AXFocusedWindow" else None)
    rt.add_app(800, "com.apple.Terminal", "Terminal", term)
    t = actor(rt, allow_apps={"com.apple.Terminal"})
    r = t.press(t.observe("Terminal"), (0, 0))
    assert r.outcome is A.Outcome.REFUSED and "app policy" in r.reason


def test_set_value_plain_and_secure_rules():
    rt = FakeRuntime()
    s = static_page(rt)
    broker = SecretBroker(MemoryProvider({"pw": "s3cret-value-123"}))
    ac = actor(rt, secrets=broker)
    snap = ac.observe("TextEdit")
    r = ac.set_value(snap, Target(role="textfield", name="Name"), "Ada")
    assert r.outcome is A.Outcome.VERIFIED and s["name"].attrs["AXValue"] == "Ada"
    assert r.detail["value"] == "<3 chars>"
    # plain text into a secure field: refused, nothing typed
    r = ac.set_value(snap, (0, 1), "hunter2")
    assert r.outcome is A.Outcome.REFUSED and "handle" in r.reason and s["pw"].meta["real"] == ""
    # a handle is resolved at the sink; the result shows the handle only
    r = ac.set_value(snap, (0, 1), "{{secret:pw}}")
    assert r.outcome is A.Outcome.VERIFIED and s["pw"].meta["real"] == "s3cret-value-123"
    assert "s3cret" not in json.dumps(r.to_dict()) and r.detail["value"] == "{{secret:pw}}"
    assert broker.uses[-1]["handle"] == "{{secret:pw}}"
    # handle mixed with text into a secure field: refused
    assert ac.set_value(snap, (0, 1), "x{{secret:pw}}").outcome is A.Outcome.REFUSED
    # unknown handle: refused, never typed literally
    assert ac.set_value(snap, (0, 0), "{{secret:nope}}").outcome is A.Outcome.REFUSED
    # a value a trusted sink already resolved
    r = ac.set_value(snap, (0, 1), A.Revealed("other-secret-999"))
    assert r.outcome is A.Outcome.VERIFIED and r.detail["value"] == "<secret>"
    assert "other-secret" not in repr(A.Revealed("other-secret-999"))
    # no broker: a handle cannot be resolved
    assert actor(rt).set_value(snap, (0, 1), "{{secret:pw}}").outcome is A.Outcome.REFUSED
    # not a text field
    assert ac.set_value(snap, (0, 3), "x").outcome is A.Outcome.REFUSED


def test_typing_fallback_needs_events():
    rt = FakeRuntime()
    s = static_page(rt)
    s["name"].settable.discard("AXValue")
    ac = actor(rt)
    r = ac.set_value(ac.observe("TextEdit"), (0, 0), "Ada")
    assert r.outcome is A.Outcome.REFUSED and "keystrokes are unavailable" in r.reason
    assert not rt.events                                   # nothing was posted


def test_url_allowlist():
    ok = A.url_allowed
    assert ok("http://127.0.0.1:8123/login")[0] and ok("http://localhost/x")[0]
    assert ok("file:///tmp/a.txt")[0]
    assert not ok("https://example.com/")[0] and not ok("http://192.168.64.1:8080/")[0]
    assert ok("https://example.com/a", ["https://example.com/"])[0]
    rt = FakeRuntime()
    FakeSafari(rt)
    r = actor(rt).open_url("https://mail.google.com/")
    assert r.outcome is A.Outcome.REFUSED and not rt.opened


def test_open_url_new_window_never_touches_existing(tmp_path):
    rt = FakeRuntime()
    saf = FakeSafari(rt)
    mine = owner_window(rt, saf)
    srv, url = _tiny_server(tmp_path)
    try:
        ac = actor(rt)
        r = ac.open_url(url + "/")
        assert r.outcome is A.Outcome.VERIFIED, r
        assert r.window is not None and r.window is not mine and len(saf.windows) == 2
        assert mine.meta["web"].attrs["AXURL"] == "https://owner.example/inbox"
        r2 = ac.navigate(r.window, url + "/page2")
        assert r2.outcome is A.Outcome.VERIFIED and r.window.meta["web"].attrs["AXURL"].endswith("/page2")
        assert mine.meta["web"].attrs["AXURL"] == "https://owner.example/inbox"
        assert all("owner.example" not in u for u in saf.loads)
        assert ac.navigate(r.window, "https://example.com/").outcome is A.Outcome.REFUSED
    finally:
        srv.shutdown()


def test_open_url_safari_not_running_uses_open(tmp_path):
    rt = FakeRuntime()
    saf = FakeSafari(rt, running=False)
    srv, url = _tiny_server(tmp_path)

    def open_with(app, target):
        rt.opened.append((app, target))
        rt.add_app(SAFARI_PID, A.SAFARI, "Safari", saf.app)
        saf.new_window(target)
        return True
    rt.open_with = open_with
    try:
        r = actor(rt).open_url(url + "/")
        assert r.outcome is A.Outcome.VERIFIED and rt.opened == [("Safari", url + "/")]
    finally:
        srv.shutdown()


def test_menu_pick_and_activate():
    rt = FakeRuntime()
    saf = FakeSafari(rt)
    rt.front = None
    ac = actor(rt)
    r = ac.menu_pick("Safari", ("File", "New Window"), A.Expect(predicate=lambda s: len(saf.windows) == 1))
    assert r.outcome is A.Outcome.VERIFIED and len(saf.windows) == 1
    assert ac.menu_pick("Safari", ("File", "Nope")).outcome is A.Outcome.REFUSED
    assert ac.activate("Safari").outcome is A.Outcome.VERIFIED and rt.front == SAFARI_PID


# ====================================================== driver: tiny server ==

def _tiny_server(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    pages = {
        "/": "<html><head><title>Home</title></head><body><h1>Home</h1>"
             "<form method='post' action='/login' aria-label='Sign in'>"
             "<p><label for='e'>Email</label> <input id='e' name='email'></p>"
             "<p><label for='p'>Password</label> <input id='p' name='password' type='password'></p>"
             "<button type='submit'>Sign in</button></form>"
             "<p><a href='/page2'>Next page</a></p></body></html>",
        "/page2": "<html><head><title>Two</title></head><body><h1>Two</h1>"
                  "<div role='region' aria-label='New API key'><p><label for='k'>key</label>"
                  "<code id='k' aria-label='New API key value'>fk_live_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456</code>"
                  "</p></div><p role='status'>Ready</p></body></html>",
    }
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = pages.get(self.path.split("?")[0], "<title>Not found</title>").encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got["form"] = urllib.parse.parse_qs(self.rfile.read(n).decode())
            self.send_response(303)
            self.send_header("Location", "/page2")
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.got = got
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_backend_reads_page_and_acts(tmp_path):
    rt = FakeRuntime()
    saf = FakeSafari(rt)
    owner_window(rt, saf)
    srv, url = _tiny_server(tmp_path)
    try:
        b = D.MacBrowserBackend(url, actor=actor(rt, allow_apps={A.SAFARI}))
        p = b.open("/")
        assert p.title == "Home" and [c.role for c in p.controls] == ["textbox", "password", "button", "link"]
        assert p.controls[0].name == "Email" and p.controls[1].name == "Password"
        assert p.controls[2].form == 0 and p.forms[0].method == "post" and p.forms[0].label == "Sign in"
        assert p.controls[0].form == 0                       # inputs share the form
        b.fill(p, 1, "ada@example.com")
        b.fill(p, 2, "pw-not-shown-123")
        assert p.controls[1].value == "•" * 8 and p.values[2] == "pw-not-shown-123"
        p2 = b.click(p, 3)
        assert srv.got["form"]["email"] == ["ada@example.com"]
        assert srv.got["form"]["password"] == ["pw-not-shown-123"]
        assert p2.title == "Two" and p2.statuses == ["Ready"]
        sec = [c for c in p2.controls if c.role == "secret_text"]
        assert len(sec) == 1 and sec[0].name == "New API key value"
        assert "fk_live_ABC" not in p2.text and "fk_live_ABC" not in json.dumps(
            [c.line() for c in p2.controls])
        assert b.read_raw(p2, sec[0].n).startswith("fk_live_ABC")
        assert b.page is p2 and any(x.startswith("GET /") for x in b.log)
        with pytest.raises(PermissionError):            # web_tools.NotLoopback, as LoopbackBrowser
            b.open("https://example.com/")
        assert b.close() and len(saf.windows) == 1         # only our window closed
    finally:
        srv.shutdown()


# ====================================== end to end: the two demos, on fakes ==

def _fake_world():
    rt = FakeRuntime()
    saf = FakeSafari(rt)
    owner_window(rt, saf)
    return rt, saf


@pytest.mark.parametrize("variant", ["a", "b"])
def test_demo_dd_end_to_end_on_fake_safari(tmp_path, variant):
    rt, saf = _fake_world()
    lines = []
    res = P.demo_dd(variant, 7, tmp_path / "dd", runtime=rt, vm_check=VM, out=lines.append, settle=1.0)
    assert res["pass"], (res.get("reasons"), res.get("error"), lines[-30:])
    assert res["report_status"] == "done"
    assert [s["outcome"] for s in res["subtasks"]] == ["verified", "verified"]
    assert all("owner.example" not in u for u in saf.loads)
    assert len(saf.windows) == 1                            # our window closed, the owner's kept
    kc = json.loads((tmp_path / "dd/sandbox/Keychain/fakeservice.json").read_text())
    log = "\n".join(lines)
    assert kc["password"] not in log and not re.search(r"fk_live_[A-Za-z0-9]{32}", log)


def test_demo_teach_t3_end_to_end_on_fake_safari(tmp_path):
    rt, saf = _fake_world()
    lines = []
    res = P.demo_teach_t3("i03", tmp_path / "t3", runtime=rt, vm_check=VM, out=lines.append, settle=1.0)
    assert res["pass"], (res, lines[-30:])
    assert res["status"] == "VERIFIED" and res["model_calls"] == 0
    assert len(saf.windows) == 1


def test_demo_refuses_on_host_and_without_vm():
    rt, saf = _fake_world()
    out = []
    assert P.demo("dd", runtime=rt, vm_check=lambda: False, out=out.append) == 2
    assert "host Mac" in out[0] and not saf.loads


# ==================================================== probe / vm-run / env ==

def test_probe_refuses_sensitive_apps_and_host():
    rt = FakeRuntime()
    static_page(rt)
    mail = El("AXApplication", pid=900)
    rt.add_app(900, "com.apple.mail", "Mail", mail)
    out = []
    assert P.probe("Mail", runtime=rt, vm_check=VM, out=out.append) == 0
    assert any("sensitive" in x for x in out)
    out = []
    assert P.probe("TextEdit", runtime=rt, vm_check=VM, out=out.append) == 0
    assert any('"Apply"' in x for x in out)
    out = []
    assert P.probe("TextEdit", runtime=rt, vm_check=lambda: False, out=out.append) == 2
    assert not any('"Apply"' in x for x in out)


def test_vm_commands_shape():
    cmds = P.vm_commands("192.168.64.7", ["scaffold.agent.desktop.live", "demo", "--target", "dd"],
                         pull="out", build_helper=True)
    assert cmds[0][0] == "rsync" and cmds[0][-1] == "admin@192.168.64.7:AWOS_coding_agent/"
    assert "--exclude=.git" in cmds[0] and "--exclude=.awos" in cmds[0]
    assert "AWOS_LOCAL_BASE_URL=http://192.168.64.1:8080/v1" in cmds[1][-1]
    assert "build_helper.sh" in cmds[2][-1]
    assert cmds[3][-1].endswith("run_helper.sh scaffold.agent.desktop.live demo --target dd")
    assert cmds[4][0] == "rsync" and cmds[4][-1] == "out/"
    t = P.vm_commands("10.0.0.5", ["m"], sync=False, tunnel_port=8080)
    assert "AWOS_LOCAL_BASE_URL=http://127.0.0.1:8080/v1" in t[0][-1]
    assert "-R8080:127.0.0.1:8080" in t[1]


def test_vm_run_dry_run_and_tart_ip(monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, "192.168.64.9\n", ""

    def runner(c, **kw):
        calls.append(c)
        return R()
    monkeypatch.setattr(P, "in_guest_vm", lambda: False)
    out = []
    assert P.vm_run(["scaffold.agent.desktop.live", "probe"], vm="g1", dry_run=True, out=out.append,
                    runner=runner) == 0
    assert calls == [["tart", "ip", "g1"]] and any("admin@192.168.64.9" in x for x in out)


def test_load_live_env_only_awos_keys(tmp_path, monkeypatch):
    f = tmp_path / "live.env"
    f.write_text("AWOS_LOCAL_BASE_URL=http://127.0.0.1:8080/v1\nPATH=/evil\nAWOS_X;rm=1\n")
    monkeypatch.delenv("AWOS_LOCAL_BASE_URL", raising=False)
    got = P.load_live_env(f)
    assert got == {"AWOS_LOCAL_BASE_URL": "http://127.0.0.1:8080/v1"}
    assert os.environ["PATH"] != "/evil"


# ============================================== real UI (guest VM only) ==

def _live_ok():
    if os.environ.get("AWOS_LIVE_UI") != "1":
        return False
    from scaffold.agent.desktop.live import in_guest_vm
    if not in_guest_vm():
        return False
    pf = P.preflight()
    return bool(pf.get("ok"))


live = pytest.mark.skipif(not _live_ok(), reason="real UI: needs AWOS_LIVE_UI=1, the guest VM and Accessibility")


@live
def test_live_probe_reads_safari_or_textedit():
    assert P.probe("Finder", out=lambda s: None) in (0, 2)


@live
def test_live_tiny_page_in_new_safari_window(tmp_path):
    srv, url = _tiny_server(tmp_path)
    try:
        b = D.MacBrowserBackend(url)
        p = b.open("/")
        assert p.title == "Home" and any(c.name == "Email" for c in p.controls)
        b.close()
    finally:
        srv.shutdown()
