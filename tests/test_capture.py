"""Tests for the native macOS demonstration recorder (scaffold/agent/desktop/capture, WP4).

No model calls, 127.0.0.1 only, and NO real event taps, AX observers or TCC
calls: macOS input arrives as recorded/synthetic native records, PyObjC is
replaced by fakes, and permission APIs are fakes that record whether a prompt
would have been requested.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scaffold.agent.desktop.capture import ax_macos as axm  # noqa: E402
from scaffold.agent.desktop.capture.adapter import ConsentRequired, NativeAdapter, sanitize_record  # noqa: E402
from scaffold.agent.desktop.capture.fsevents import decode_flags, encode_flags  # noqa: E402
from scaffold.agent.desktop.capture.input_tap import ListenOnlyTap, TapUnavailable  # noqa: E402
from scaffold.agent.desktop.capture.permissions import (CaptureUnavailable, PermissionMissing,  # noqa: E402
                                                        PyObjCMissing, check_permissions, load_frameworks,
                                                        require_permissions)
from scaffold.agent.desktop.capture.schema import omit_tree_value, sanitize_element  # noqa: E402
from testbeds.teach_once import DEMO_INSTANCE, demonstrate  # noqa: E402
from testbeds.teach_once.compiler import compile_trace  # noqa: E402
from testbeds.teach_once.seed import PORTAL_PASSWORD, seed_instance  # noqa: E402
from testbeds.teach_once.trace import Recorder, Trace, validate_trace  # noqa: E402

PYOBJC = ("objc", "Quartz", "AppKit", "ApplicationServices", "FSEvents", "CoreFoundation", "Foundation")
CONSENT = {"src": "session", "event": "start", "consent": {"granted": True, "notice_sha256": "test"}}
BUNDLE = {"Finder": "com.apple.finder", "Preview": "com.apple.Preview", "Numbers": "com.apple.iWork.Numbers",
          "TextEdit": "com.apple.TextEdit"}
SAFARI = "com.apple.Safari"


# ===================================================== synthetic native source ==

class Lowerer:
    """Turns the scripted demo's trace-level events into what the native macOS
    sources (app poll, listen-only tap + AX at point, AXObserver, FSEvents)
    would have produced for the same human actions — raw FSEvents bitmasks,
    bundle ids, page URLs, element descriptions — so they can be pushed
    through the real NativeAdapter path."""

    def __init__(self, urls: dict, leaky_secret: str = ""):
        self.urls, self.leaky_secret = dict(urls), leaky_secret
        self.front = None
        self.page_url: dict = {}
        self.t = 1_000.0

    def __call__(self, ev: dict) -> list:
        self.t += 0.25
        out = []

        def front(bid):
            if self.front != bid:
                self.front = bid
                out.append({"src": "app", "bundle_id": bid, "name": "", "pid": 400 + len(bid), "t": self.t})

        typ = ev["type"]
        if typ == "app":
            if ev.get("kind") == "web":
                front(SAFARI)
                if ev["app"] in self.page_url:          # a tab switch: Safari retitles the window
                    out.append({"src": "ax", "notification": "AXTitleChanged", "bundle_id": SAFARI,
                                "url": self.page_url[ev["app"]], "t": self.t})
            else:
                front(BUNDLE[ev["app"]])
        elif typ == "pointer":
            out.append({"src": "mouse", "button": "left", "pressed": True, "x": 400.0, "y": 300.0,
                        "injected": False, "clicks": 1, "bundle_id": SAFARI, "url": self.page_url[ev["app"]],
                        "element": dict(ev["element"]), "t": self.t})
        elif typ == "ax":
            n = ev["notification"]
            if n == "AXLoadComplete":
                w = ev["window"]
                url = self.urls[ev["app"]] + w["url_path"]
                self.page_url[ev["app"]] = url
                out.append({"src": "ax", "notification": n, "bundle_id": SAFARI, "url": url, "t": self.t,
                            "window": {k: w[k] for k in ("title", "alerts", "status", "page_text", "tables")}})
            elif n in ("AXFocusedUIElementChanged", "AXValueChanged"):
                el = dict(ev["element"])
                if el.get("subrole") == "AXSecureTextField":
                    el.pop("value_length", None)
                    if self.leaky_secret:             # a buggy source that DID read the secure value
                        el["value"] = self.leaky_secret
                out.append({"src": "ax", "notification": n, "bundle_id": SAFARI, "url": self.page_url[ev["app"]],
                            "element": el, "t": self.t})
            elif n == "AXFocusedWindowChanged":
                doc = ev["window"]["document"]
                out.append({"src": "ax", "notification": n, "bundle_id": BUNDLE[ev["app"]], "t": self.t,
                            "window": {"document": "file://" + quote(doc), "title": ev["window"].get("title", "")}})
        elif typ == "fs":
            mask = encode_flags(ev["flags"])
            if "ItemCreated" in ev["flags"] and "ItemIsFile" in ev["flags"]:
                mask |= encode_flags(["ItemXattrMod", "ItemInodeMetaMod"])   # FSEvents noise on new files
            ino = ev.get("inode")
            if ino is None and os.path.lexists(ev["path"]):
                ino = os.lstat(ev["path"]).st_ino
            out.append({"src": "fs", "path": ev["path"], "flags": mask, "inode": ino, "t": self.t})
        return out


def run_parity(monkeypatch, tmp_path, task, leaky_secret=""):
    """Run the scripted demo; tee every event through the native path into a second Recorder."""
    from testbeds.teach_once.webapps import Servers
    I = seed_instance(tmp_path / "demo_scratch", task, DEMO_INSTANCE)
    box = {}
    log = []
    orig = demonstrate._recorder

    def factory(inst, context, apps):
        rec_a = orig(inst, context, apps)
        rec_b = Recorder(inst.home, task=inst.task, instance=inst.inst, context=context, title=rec_a.trace.title,
                         note=rec_a.trace.note, kind="macos_ax_fsevents", apps=apps)
        adapter = NativeAdapter(rec_b.feed, home=inst.home, apps=apps, log=log.append)
        adapter.feed(dict(CONSENT, task=inst.task, instance=inst.inst, context=context, apps=apps,
                          home=str(inst.home)))
        lower = Lowerer(box["urls"], leaky_secret)
        feed_a = rec_a.feed

        def tee(ev):
            feed_a(ev)
            for native in lower(ev):
                adapter.feed(native)

        rec_a.feed = tee
        box.update(rec_b=rec_b, adapter=adapter)
        return rec_a

    monkeypatch.setattr(demonstrate, "_recorder", factory)
    if task == "t1_statements":
        box["urls"] = {}
        trace_a = demonstrate.demo_t1(I)
    else:
        with Servers(I) as urls:
            box["urls"] = urls
            trace_a = (demonstrate.demo_t2 if task == "t2_orders" else demonstrate.demo_t3)(I, urls)
    trace_b = box["adapter"].finish(box["rec_b"], web_with_text=task != "t2_orders")
    return trace_a, trace_b, log, box["adapter"]


def _steps(trace):
    return [s for s in trace.to_dict()["steps"]]


# ================================================================ parity ==

@pytest.mark.parametrize("task", ["t1_statements", "t3_invoices", "t2_orders"])
def test_native_path_matches_scripted_demo(monkeypatch, tmp_path, task):
    a, b, log, adapter = run_parity(monkeypatch, tmp_path, task)
    assert len(a.steps) > 5
    assert _steps(b) == _steps(a)
    assert b.start == a.start and b.end["files"] == a.end["files"] and b.end["text"] == a.end["text"]
    assert b.end["web"] == a.end["web"]
    assert b.recorder["kind"] == "macos_ax_fsevents" and b.recorder["session"]["consent"]["granted"]
    assert validate_trace(b, (PORTAL_PASSWORD,)) == []
    assert validate_trace(a, (PORTAL_PASSWORD,)) == []
    ca, cb = compile_trace(a), compile_trace(b)
    assert cb == ca                                   # the same skill compiles from either recording
    assert log and all(r["src"] in ("session", "app", "mouse", "key", "ax", "fs") for r in log)


def test_replay_from_events_cli_reproduces_web_task(monkeypatch, tmp_path, capsys):
    """events.jsonl written by the native path -> `record --from-events` on a fresh seed -> same steps."""
    from scaffold.agent.desktop.capture.__main__ import main
    a, b, log, _ = run_parity(monkeypatch, tmp_path / "rec", "t3_invoices")
    ev = tmp_path / "events.jsonl"
    ev.write_text("".join(json.dumps(r) + "\n" for r in log), encoding="utf-8")
    out = tmp_path / "trace.json"
    rc = main(["record", "--from-events", str(ev), "--out", str(out), "--run-dir", str(tmp_path / "replay")])
    printed = capsys.readouterr().out
    assert rc == 0, printed
    assert "valid" in printed
    replayed = Trace.load(out)
    assert _steps(replayed) == _steps(a)
    assert main(["validate", str(out)]) == 0


# ================================================================ privacy ==

def test_secure_value_never_recorded_even_from_a_leaky_source(monkeypatch, tmp_path):
    a, b, log, _ = run_parity(monkeypatch, tmp_path, "t2_orders", leaky_secret=PORTAL_PASSWORD)
    out = tmp_path / "trace.json"
    b.save(out)
    ev = tmp_path / "events.jsonl"
    ev.write_text("".join(json.dumps(r) + "\n" for r in log), encoding="utf-8")
    assert PORTAL_PASSWORD not in out.read_text(encoding="utf-8")          # grep test: trace
    assert PORTAL_PASSWORD not in ev.read_text(encoding="utf-8")           # grep test: native event log
    pw = [s for s in b.steps if s.action == "type" and s.target.get("name") == "Password"]
    assert pw and all(s.value == {"secret": "Password"} for s in pw)
    assert validate_trace(b, (PORTAL_PASSWORD,)) == []
    assert _steps(b) == _steps(a)


def test_sanitizers_drop_secure_values_and_key_characters():
    el = {"role": "AXTextField", "subrole": "AXSecureTextField", "name": "Password", "value": "hunter2",
          "value_length": 7, "selected_text": "hunter2"}
    assert "value" not in sanitize_element(el) and "hunter2" not in json.dumps(sanitize_element(el))
    assert omit_tree_value("AXSecureTextField") and omit_tree_value(None, "password text")
    assert not omit_tree_value("AXTextField")
    rec = sanitize_record({"src": "key", "key": "enter", "char": "x", "chars": "hunter2", "focused": el})
    assert "hunter2" not in json.dumps(rec) and "char" not in rec
    rec = sanitize_record({"src": "ax", "notification": "AXValueChanged", "element": el, "secret": "hunter2"})
    assert "hunter2" not in json.dumps(rec)


class FakeRT:
    """A fake AX runtime: elements are dicts with attributes + children."""

    def attribute(self, el, name):
        if name == "AXChildren":
            return el.get("children", [])
        return el.get(name)

    def children(self, el):
        return list(el.get("children", []))

    def same(self, a, b):
        return a is b


def _fake_page():
    web = {"AXRole": "AXWebArea", "AXURL": "http://127.0.0.1:9/login", "AXTitle": "Sign in", "children": []}
    user = {"AXRole": "AXTextField", "AXDescription": "Username", "AXValue": "sam", "AXParent": web}
    pw = {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField", "AXDescription": "Password",
          "AXValue": PORTAL_PASSWORD, "AXParent": web}
    btn = {"AXRole": "AXButton", "AXTitle": "Sign in", "AXParent": web}
    alert = {"AXRole": "AXGroup", "AXSubrole": "AXApplicationAlert", "AXParent": web,
             "children": [{"AXRole": "AXStaticText", "AXValue": "Wrong password"}]}
    head = {"AXRole": "AXStaticText", "AXValue": "Orders portal", "AXParent": web}
    web["children"] = [head, alert, user, pw, btn]
    return web, user, pw, btn


def test_ax_reads_omit_secure_values_and_compute_ordinals():
    rt = FakeRT()
    web, user, pw, btn = _fake_page()
    assert axm.describe(rt, user) == {"role": "AXTextField", "name": "Username", "value": "sam"}
    d = axm.describe(rt, pw)
    assert d == {"role": "AXTextField", "subrole": "AXSecureTextField", "name": "Password"}
    assert axm.web_context(rt, pw) == (web, "http://127.0.0.1:9/login")
    assert axm.ordinal_in(rt, web, pw, "AXTextField") == 1
    assert axm.ordinal_in(rt, web, btn, "AXButton") == 0
    tree, truncated = axm.window_tree(rt, web)
    assert not truncated and PORTAL_PASSWORD not in json.dumps(tree)
    page = axm.page_summary(rt, web)
    assert page["alerts"] == ["Wrong password"] and "Orders portal" in page["page_text"]
    assert PORTAL_PASSWORD not in json.dumps(page)
    _, truncated = axm.window_tree(rt, web, max_nodes=2)
    assert truncated


# ============================================================ adapter rules ==

def _adapter(home, apps=None, **kw):
    rec = Recorder(home, task="t1_statements", instance="i01", kind="macos_ax_fsevents",
                   apps=apps or [{"name": "Finder", "kind": "files"}])
    log = []
    ad = NativeAdapter(rec.feed, home=home, apps=apps or [{"name": "Finder", "kind": "files"}], log=log.append, **kw)
    return rec, ad, log


def _fs(path, *names, inode=None):
    return {"src": "fs", "path": str(path), "flags": encode_flags(names), "inode": inode}


def test_consent_required_before_anything_is_captured(tmp_path):
    rec, ad, log = _adapter(tmp_path)
    with pytest.raises(ConsentRequired):
        ad.feed({"src": "app", "bundle_id": "com.apple.finder"})
    with pytest.raises(ConsentRequired):
        ad.feed({"src": "session", "event": "start", "consent": {"granted": False}})
    assert all(r["src"] == "session" for r in log)
    ad.feed(CONSENT)
    ad.feed({"src": "app", "bundle_id": "com.apple.finder"})
    assert [s.action for s in rec.trace.steps] == ["switch_app"]


def test_pause_for_auth_keeps_nothing(tmp_path):
    apps = [{"name": "inbox", "kind": "web", "base_url": "http://127.0.0.1:9"}]
    rec, ad, log = _adapter(tmp_path, apps)
    ad.feed(CONSENT)
    ad.feed({"src": "app", "bundle_id": SAFARI})
    ad.feed({"src": "ax", "notification": "AXLoadComplete", "bundle_id": SAFARI, "url": "http://127.0.0.1:9/",
             "window": {"title": "Inbox"}})
    ad.feed({"src": "session", "event": "pause", "reason": "auth"})
    ad.feed({"src": "ax", "notification": "AXValueChanged", "bundle_id": SAFARI, "url": "http://127.0.0.1:9/",
             "element": {"role": "AXTextField", "name": "One-time code", "value": "918273"}})
    ad.feed({"src": "session", "event": "resume"})
    ad.feed({"src": "mouse", "button": "left", "injected": True, "bundle_id": SAFARI, "url": "http://127.0.0.1:9/",
             "element": {"role": "AXButton", "name": "Delete all", "ordinal": 0}})
    trace = ad.finish(rec)
    blob = json.dumps(trace.to_dict()) + json.dumps(log)
    assert "918273" not in blob and "Delete all" not in json.dumps(trace.to_dict())
    assert [s.action for s in trace.steps] == ["switch_app", "open_url"]
    sess = trace.recorder["session"]
    assert sess["pauses"] and sess["pauses"][0]["reason"] == "auth"
    assert sess["stats"]["dropped_paused"] == 1 and sess["stats"]["dropped_injected"] == 1


def test_other_apps_pages_and_folders_are_ignored(tmp_path):
    apps = [{"name": "inbox", "kind": "web", "base_url": "http://127.0.0.1:9"}, {"name": "Finder", "kind": "files"}]
    rec, ad, log = _adapter(tmp_path / "home", apps)
    (tmp_path / "home").mkdir()
    (tmp_path / "elsewhere.txt").write_text("xyzzy-note")
    ad.feed(CONSENT)
    ad.feed({"src": "app", "bundle_id": "com.apple.Terminal"})
    ad.feed({"src": "mouse", "button": "left", "bundle_id": "com.apple.Terminal", "element": {"role": "AXButton",
             "name": "terminal-secret"}})
    ad.feed({"src": "app", "bundle_id": SAFARI})
    ad.feed({"src": "ax", "notification": "AXLoadComplete", "bundle_id": SAFARI, "url": "https://bank.example/acct",
             "window": {"title": "My bank", "page_text": "balance 1234"}})
    ad.feed({"src": "mouse", "button": "left", "bundle_id": SAFARI, "url": None,          # address bar
             "element": {"role": "AXTextField", "name": "Address"}})
    ad.feed({"src": "app", "bundle_id": "com.apple.Preview"})                              # not in the task
    ad.feed({"src": "ax", "notification": "AXFocusedWindowChanged", "bundle_id": "com.apple.Preview",
             "window": {"document": str(tmp_path / "elsewhere.txt")}})
    ad.feed(_fs(tmp_path / "elsewhere.txt", "ItemModified", "ItemIsFile"))
    trace = ad.finish(rec)
    blob = json.dumps(trace.to_dict()) + json.dumps(log)
    for s in ("terminal-secret", "balance 1234", "My bank", "xyzzy-note", "elsewhere"):
        assert s not in blob, s
    assert trace.steps == []


def test_web_rules_focus_clicks_enter_and_duplicate_loads(tmp_path):
    base = "http://127.0.0.1:9"
    apps = [{"name": "orders", "kind": "web", "base_url": base}]
    rec, ad, _ = _adapter(tmp_path, apps)
    ad.feed(CONSENT)
    ad.feed({"src": "app", "bundle_id": SAFARI, "url": base + "/"})
    load = {"src": "ax", "notification": "AXLoadComplete", "bundle_id": SAFARI, "url": base + "/",
            "window": {"title": "Sign in", "alerts": [], "status": []}}
    ad.feed(load)
    ad.feed(load)                                                               # duplicate: dropped
    field = {"role": "AXTextField", "name": "From date", "ordinal": 0}
    ad.feed({"src": "mouse", "button": "left", "bundle_id": SAFARI, "url": base + "/", "element": field})
    ad.feed({"src": "ax", "notification": "AXValueChanged", "bundle_id": SAFARI, "url": base + "/",
             "element": dict(field, value="2026-03-0")})
    ad.feed({"src": "ax", "notification": "AXValueChanged", "bundle_id": SAFARI, "url": base + "/",
             "element": dict(field, value="2026-03-09")})
    ad.feed({"src": "key", "key": "enter", "bundle_id": SAFARI, "url": base + "/", "focused": field,
             "default_button": {"role": "AXButton", "name": "Apply filter", "ordinal": 1}})
    ad.feed(dict(load, url=base + "/?from=2026-03-09", window={"title": "Orders", "alerts": [], "status": []}))
    t = ad.finish(rec)
    acts = [(s.action, s.target.get("name") or s.target.get("url_path")) for s in t.steps]
    assert acts == [("switch_app", None), ("open_url", "/"), ("type", "From date"), ("click", "Apply filter")]
    assert t.steps[2].value == "2026-03-09" and t.steps[3].after["url_path"] == "/?from=2026-03-09"


def test_fs_normalisation_new_folder_atomic_save_and_noise(tmp_path):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Documents").mkdir()
    (home / "Downloads" / "stmt.pdf").write_bytes(b"%PDF fake")
    (home / "Documents" / "ledger.csv").write_text("date,bank\n2026-01-31,acme\n")
    rec, ad, _ = _adapter(home, [{"name": "Finder", "kind": "files"}, {"name": "TextEdit", "kind": "files"}])
    ad.feed(CONSENT)
    ad.feed({"src": "app", "bundle_id": "com.apple.finder"})
    # Finder: New Folder ("untitled folder") then the user names it
    (home / "Documents" / "untitled folder").mkdir()
    ad.feed(_fs(home / "Documents" / "untitled folder", "ItemCreated", "ItemIsDir"))
    os.rename(home / "Documents" / "untitled folder", home / "Documents" / "Statements")
    ad.feed(_fs(home / "Documents" / "untitled folder", "ItemRenamed", "ItemIsDir"))
    ad.feed(_fs(home / "Documents" / "Statements", "ItemRenamed", "ItemIsDir"))
    # drag the statement into it (one inode, coalesced extra bits)
    ino = (home / "Downloads" / "stmt.pdf").stat().st_ino
    os.rename(home / "Downloads" / "stmt.pdf", home / "Documents" / "Statements" / "stmt.pdf")
    ad.feed(_fs(home / "Downloads" / "stmt.pdf", "ItemRenamed", "ItemIsFile", "ItemXattrMod", inode=ino))
    ad.feed(_fs(home / "Documents" / "Statements" / "stmt.pdf", "ItemRenamed", "ItemIsFile", inode=ino))
    # Finder tags / xattrs only: no step
    ad.feed(_fs(home / "Documents" / "Statements" / "stmt.pdf", "ItemXattrMod", "ItemIsFile", "ItemCreated"))
    # TextEdit safe-save: temp file renamed over the ledger
    ad.feed({"src": "app", "bundle_id": "com.apple.TextEdit"})
    tmp = home / "Documents" / "ledger.csv.sb-1234-abcd"
    tmp.write_text("date,bank\n2026-01-31,acme\n2026-02-28,acme\n")
    ad.feed(_fs(tmp, "ItemCreated", "ItemIsFile"))
    os.replace(tmp, home / "Documents" / "ledger.csv")
    ad.feed(_fs(tmp, "ItemRenamed", "ItemIsFile"))
    ad.feed(_fs(home / "Documents" / "ledger.csv", "ItemRenamed", "ItemIsFile", "ItemModified"))
    (home / "Documents" / ".DS_Store").write_bytes(b"x")
    ad.feed(_fs(home / "Documents" / ".DS_Store", "ItemCreated", "ItemIsFile"))
    t = ad.finish(rec)
    acts = [(s.action, s.app) for s in t.steps]
    assert acts == [("switch_app", "Finder"), ("move", "Finder"), ("switch_app", "TextEdit"),
                    ("append_rows", "TextEdit")], acts
    mv = t.steps[1]
    assert mv.target == {"from": "Downloads/stmt.pdf", "to": "Documents/Statements/stmt.pdf"}
    assert {"path": "Documents/Statements", "op": "mkdir"} in mv.files_touched
    assert t.steps[3].value == [["2026-02-28", "acme"]]


def test_unpaired_rename_source_becomes_delete(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    rec, ad, _ = _adapter(tmp_path)
    ad.feed(CONSENT)
    ad.feed({"src": "app", "bundle_id": "com.apple.finder"})
    os.remove(tmp_path / "a.txt")                                   # moved to the Trash (outside the watch)
    ad.feed(_fs(tmp_path / "a.txt", "ItemRenamed", "ItemIsFile"))
    t = ad.finish(rec)
    assert [(s.action, s.target.get("path")) for s in t.steps] == [("switch_app", None), ("delete", "a.txt")]


# ================================================ permissions / lazy import ==

def test_package_imports_without_pyobjc():
    code = (
        "import sys\n"
        f"BLOCK = {PYOBJC!r}\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in BLOCK: raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "import scaffold.agent.desktop.capture as c\n"
        "from scaffold.agent.desktop.capture import adapter, live, ax_macos, input_tap, fsevents, permissions, schema\n"
        "import scaffold.agent.desktop.capture.__main__ as cli\n"
        "import testbeds.teach_once.trace as tr\n"
        "assert not [m for m in sys.modules if m.split('.')[0] in BLOCK], sys.modules.keys()\n"
        "try:\n"
        "    permissions.load_frameworks(platform='darwin')\n"
        "except permissions.PyObjCMissing as e:\n"
        "    assert 'pip install' in str(e); print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=8)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr


def test_not_macos_and_missing_pyobjc_errors_are_clear(monkeypatch):
    with pytest.raises(CaptureUnavailable, match="needs macOS"):
        load_frameworks(platform="linux")
    for m in PYOBJC:
        monkeypatch.setitem(sys.modules, m, None)
    with pytest.raises(PyObjCMissing, match="pyobjc-framework-ApplicationServices"):
        load_frameworks(platform="darwin")


class FakeAX:
    kAXErrorSuccess = 0
    kAXTrustedCheckOptionPrompt = "AXTrustedCheckOptionPrompt"

    def __init__(self, trusted):
        self.trusted, self.calls = trusted, []

    def AXIsProcessTrusted(self):
        self.calls.append("AXIsProcessTrusted")
        return self.trusted

    def AXIsProcessTrustedWithOptions(self, opts):
        self.calls.append("PROMPT")
        return self.trusted


class FakeQuartz:
    kCGSessionEventTap, kCGHeadInsertEventTap = 1, 0
    kCGEventTapOptionDefault, kCGEventTapOptionListenOnly = 0, 1
    kCGEventLeftMouseDown, kCGEventRightMouseDown, kCGEventKeyDown = 1, 3, 10
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = 0xFFFFFFFE, 0xFFFFFFFF
    kCGEventSourceUnixProcessID, kCGKeyboardEventKeycode, kCGMouseEventClickState = 41, 9, 1
    kCFRunLoopDefaultMode = "default"

    def __init__(self, listen=True, tap_ok=True):
        self.listen, self.tap_ok, self.calls = listen, tap_ok, []

    def CGPreflightListenEventAccess(self):
        self.calls.append("preflight")
        return self.listen

    def CGRequestListenEventAccess(self):
        self.calls.append("PROMPT")
        return self.listen

    def CGEventMaskBit(self, t):
        return 1 << t

    def CGEventTapCreate(self, tap, place, options, mask, cb, refcon):
        self.calls.append(("tap", options))
        return object() if self.tap_ok else None

    def CFMachPortCreateRunLoopSource(self, a, tap, order):
        return object()

    def CFRunLoopAddSource(self, *a):
        self.calls.append("add_source")

    def CGEventTapEnable(self, tap, on):
        self.calls.append(("enable", on))

    def CGEventGetIntegerValueField(self, ev, field):
        return ev.get(field, 0)

    def CGEventGetLocation(self, ev):
        class P:
            x, y = ev["xy"]
        return P()


def test_permission_missing_is_clear_and_never_prompts():
    ax, q = FakeAX(False), FakeQuartz(listen=False)
    fw = {"ApplicationServices": ax, "Quartz": q}
    st = check_permissions(fw)
    assert st.missing == ["accessibility", "input_monitoring"] and not st.ok
    with pytest.raises(PermissionMissing) as e:
        require_permissions(fw)
    msg = str(e.value)
    assert "Privacy & Security > Accessibility" in msg and "Input Monitoring" in msg and "Terminal" in msg
    assert "PROMPT" not in ax.calls + q.calls
    with pytest.raises(PermissionMissing, match="Accessibility"):
        axm.AXRuntime({"ApplicationServices": ax, "AppKit": None, "Quartz": q})
    from scaffold.agent.desktop.capture.live import MacRecorder
    with pytest.raises(PermissionMissing):
        MacRecorder(lambda r: None, watch=["/tmp"], frameworks={"ApplicationServices": ax, "Quartz": q,
                                                               "AppKit": None, "FSEvents": None}).open()
    assert not [c for c in q.calls if isinstance(c, tuple) and c[0] == "tap"]      # no tap was ever created
    ok = check_permissions({"ApplicationServices": FakeAX(True), "Quartz": FakeQuartz(listen=False)})
    assert ok.missing == ["input_monitoring"]


def test_cli_reports_missing_pyobjc_without_seeding(monkeypatch, tmp_path, capsys):
    from scaffold.agent.desktop.capture.__main__ import main
    for m in PYOBJC:
        monkeypatch.setitem(sys.modules, m, None)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert main(["check"]) == 2
    rc = main(["record", "--task", "t3_invoices", "--out", str(tmp_path / "t.json"), "--run-dir",
               str(tmp_path / "run"), "--yes"])
    err = capsys.readouterr().err
    assert rc == 2 and "PyObjC" in err and "pip install" in err
    assert not (tmp_path / "run").exists() and not (tmp_path / "t.json").exists()


def test_listen_only_tap_drops_characters_and_flags_injected():
    q = FakeQuartz()
    got = []
    tap = ListenOnlyTap(got.append, frameworks={"Quartz": q}, clock=lambda: 5.0)
    tap.schedule(object())
    assert ("tap", q.kCGEventTapOptionListenOnly) in q.calls
    tap._on_event(None, q.kCGEventLeftMouseDown, {"xy": (10.0, 20.0), 41: 0, 1: 1}, None)
    tap._on_event(None, q.kCGEventLeftMouseDown, {"xy": (10.0, 20.0), 41: 777, 1: 1}, None)   # posted by pid 777
    tap._on_event(None, q.kCGEventKeyDown, {9: 0, 41: 0}, None)                               # "a": dropped
    tap._on_event(None, q.kCGEventKeyDown, {9: 36, 41: 0}, None)                              # enter
    assert [(r["src"], r.get("injected")) for r in got] == [("mouse", False), ("mouse", True), ("key", False)]
    assert got[2] == {"src": "key", "key": "enter", "pressed": True, "injected": False, "t": 5.0}
    with pytest.raises(TapUnavailable, match="Input Monitoring"):
        ListenOnlyTap(got.append, frameworks={"Quartz": FakeQuartz(tap_ok=False)}).schedule(object())


def test_fsevents_flag_decoding():
    assert decode_flags(0x100 | 0x10000) == ["ItemCreated", "ItemIsFile"]
    assert decode_flags(encode_flags(["ItemRenamed", "ItemIsDir"])) == ["ItemRenamed", "ItemIsDir"]


class FakeLiveRT(FakeRT):
    """FakeRT + the AXRuntime methods live.MacRecorder calls (no PyObjC, no observers)."""

    def __init__(self, window, at_point, pid=501, bundle=SAFARI):
        self.window, self.at_point, self._pid, self.bundle = window, at_point, pid, bundle
        self.observed = []

    def element_at_point(self, x, y):
        return self.at_point

    def focused_element(self):
        return self.at_point

    def pid(self, el):
        return self._pid

    def focused_application(self):
        return self._pid

    def focused_window(self, pid):
        return self.window

    def app_info(self, pid):
        return {"pid": pid, "bundle_id": self.bundle, "name": "Safari"}

    def observe(self, pid, notifications, cb, loop):
        self.observed.append((pid, tuple(notifications)))
        return True

    def enable_web_accessibility(self, pid):
        pass


def test_live_recorder_builds_native_records_from_ax_reads():
    """live.MacRecorder's callbacks (app poll, click, AX notification) with a fake AX runtime."""
    from scaffold.agent.desktop.capture.live import MacRecorder
    web, user, pw, btn = _fake_page()
    window = {"AXRole": "AXWindow", "AXTitle": "Sign in", "children": [web]}
    web["AXParent"] = window
    got = []
    m = MacRecorder(got.append, watch=["/tmp"])
    m.rt = FakeLiveRT(window, btn)
    m._poll_front()
    m._on_input({"src": "mouse", "button": "left", "pressed": True, "x": 1.0, "y": 2.0, "injected": False})
    m._on_ax(pw, "AXValueChanged")
    m._on_ax(web, "AXLoadComplete")
    assert got[0]["src"] == "app" and got[0]["url"] == "http://127.0.0.1:9/login"
    assert got[1]["element"] == {"role": "AXButton", "name": "Sign in", "ordinal": 0}
    assert got[1]["url"] == "http://127.0.0.1:9/login" and got[1]["bundle_id"] == SAFARI
    assert got[2]["element"] == {"role": "AXTextField", "subrole": "AXSecureTextField", "name": "Password",
                                 "ordinal": 1}
    assert got[3]["window"]["alerts"] == ["Wrong password"]
    assert PORTAL_PASSWORD not in json.dumps(got)
    assert m.rt.observed and "AXLoadComplete" in m.rt.observed[0][1]
