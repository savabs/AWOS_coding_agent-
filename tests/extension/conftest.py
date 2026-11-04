"""Fixtures for the AWOS Replay extension tests.

A local HTTP server (127.0.0.1, random port) serves tests/extension/pages and a
fake OpenAI-compatible model at /v1/chat/completions (no real model, no network).
A fake phone relay (v0.8, docs/specs/phone_link.md) answers /api/relay/<channel>/... in memory with the same JSON as the
real one (`server.relay`: registered token hashes, to-phone / to-mac envelopes, forwarded SMS; `server.relay_status`
= 503 makes every relay call answer "not configured"), and /ntfy/<topic> records pushes in `server.ntfy`.
`server.overrides` swaps the file behind a path, so the same recorded URL can
serve a renamed / deleted variant at replay time; `server.redirects` answers a path with a 302 (a portal whose
session expired sends you to its login page), `server.auth` does the same only until a login page set the
`portal_session=ok` cookie (v0.6.1 keep-alive / pre-flight), and `server.hits` lists every GET path (files downloaded once?); `server.state` is served as /__state.json (what a fixture
page shows) and /files/gen/<name> is a generated document. Chromium runs headless with
the unpacked extension loaded (Playwright's "chromium" channel = new headless,
which supports extensions). Skips when Playwright or a Chromium build is missing.
Run with the 3.12 venv:  ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import json
import pathlib
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXT = ROOT / "extension"
PAGES = pathlib.Path(__file__).resolve().parent / "pages"
# the extension's period clock (default run period = previous month, a recording's month) is pinned to mid-November
# 2026 so "previous month" is 2026-10 whatever day the suite runs; the fixture files are October 2026 documents
TEST_NOW_MS = int(time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1)) * 1000)


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(PAGES), **kw)

    def log_message(self, *a):  # quiet
        pass

    def end_headers(self):  # variants swap behind one URL: never let Chromium cache a page
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/relay/"):
            return self._relay("GET")
        if self.headers.get("Range"):              # v0.9 file check re-reading the first 1 KB: not a download
            self.server.peeks.append(path)
        else:
            self.server.hits.append(path)
        if path == "/__state.json":               # page scripts read what the test set (v0.7 invoices portal)
            return self._send(json.dumps(self.server.state).encode(), "application/json")
        if path.startswith("/files/gen/"):        # a generated document (any name: invoice-2026-10.pdf, payout-2026-11.csv)
            name = path[len("/files/gen/"):]
            if name.endswith(".csv"):
                return self._send(b"date,description,amount\n2026-10-31," + name.encode() + b",100.00\n", "text/csv")
            return self._send(b"%PDF-1.4 fake document " + name.encode() + b"\n", "application/pdf")
        if path.startswith("/files/doc/"):        # v0.9 file checks: what the document link serves is server.state["doc"]
            name, mode = path[len("/files/doc/"):], self.server.state.get("doc", "good")
            html = b"<!doctype html><html><head><title>Sign in</title></head><body>Your session expired. Please sign in.</body></html>"
            good_ctype = "text/csv" if name.endswith(".csv") else "application/pdf"
            if mode == "html":                    # the login / error page, served as what it is (text/html)
                return self._send(html, "text/html; charset=utf-8")
            if mode == "html_as_doc":             # the same page mislabelled as the document type: only its bytes tell
                return self._send(html, good_ctype)
            if mode == "empty":
                return self._send(b"", good_ctype)
            if mode == "notcsv":                  # one word per line: no delimiter
                return self._send(b"report\nnothing here\n", "text/csv")
            if name.endswith(".csv"):
                return self._send(b"date;description;amount\n2026-10-31;fees;12.00\n", "text/csv")
            return self._send(b"%PDF-1.4\n%fake but real header " + name.encode() + b"\n%%EOF\n", "application/pdf")
        if path.startswith("/files/attach/"):     # v0.9.2: an attachment response with no file name in the URL (a
            key = path[len("/files/attach/"):]     # "Download PDF" that opens a tab, which downloads and closes itself)
            body = b"%PDF-1.4\n%attachment " + key.encode() + b"\n%%EOF\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="Invoice-' + key + '.pdf"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        loc = self.server.redirects.get(path)
        if not loc and path in self.server.auth and "portal_session=ok" not in (self.headers.get("Cookie") or ""):
            loc = self.server.auth[path]          # a portal that needs a login: 302 until the login page set the cookie
        if loc:
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        alt = self.server.overrides.get(path)
        if alt:
            self.path = "/" + alt + self.path[len(path):]
        return super().do_GET()

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- fake phone relay (in memory, same JSON as the real one) ----
    def _json(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _relay(self, method, body=None):
        srv, rl = self.server, self.server.relay
        parts = self.path.split("?")[0].split("/")[3:]          # <channel>/<what>[/<id>]
        q = dict(kv.split("=", 1) for kv in (self.path.split("?", 1)[1].split("&") if "?" in self.path else []) if "=" in kv)
        rl["calls"].append((method, "/".join(parts[1:]), body))
        if srv.relay_status != 200:
            return self._json(srv.relay_status, {"error": "relay not configured"})
        if len(parts) < 2:
            return self._json(404, {"error": "no"})
        ch, what = parts[0], parts[1]
        boxes = rl["boxes"].setdefault(ch, {"to-phone": [], "to-mac": [], "sms": []})
        if what == "register" and method == "POST":           # first registration wins; another hash -> 409
            if rl["registered"].get(ch, body.get("tokenHash")) != body.get("tokenHash"):
                return self._json(409, {"error": "channel registered to another token"})
            rl["registered"][ch] = body.get("tokenHash")
            return self._json(200, {"ok": True})
        if what not in boxes:
            return self._json(404, {"error": "no"})
        if method == "POST" and len(parts) == 2:
            if any(m.get("id") == body.get("id") for m in boxes[what]):
                return self._json(200, {"duplicate": True})
            msg = dict(body, at=max([int(time.time() * 1000)] + [m["at"] + 1 for m in boxes[what]]))   # monotonic per list
            boxes[what].append(msg)
            return self._json(201, {"ok": True, "at": msg["at"]})
        if method == "GET" and len(parts) == 2:
            since = 0 if rl.get("ignore_since") else int(q.get("since") or 0)   # ignore_since: the client must filter
            return self._json(200, {"messages": [m for m in boxes[what] if m["at"] > since]})
        if method == "DELETE" and len(parts) == 3:
            before = len(boxes[what])
            boxes[what][:] = [m for m in boxes[what] if m.get("id") != parts[2]]
            rl["deleted"].append((what, parts[2]))
            return self._json(200, {"ok": True, "deleted": before - len(boxes[what])})
        return self._json(405, {"error": "method"})

    def do_DELETE(self):
        if self.path.startswith("/api/relay/"):
            return self._relay("DELETE")
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) or b""
        if self.path.startswith("/ntfy/"):
            self.server.ntfy.append({"topic": self.path[len("/ntfy/"):], "body": raw.decode("utf-8", "replace"),
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
            return self._json(200, {"id": "x"})
        if self.path.startswith("/api/relay/"):
            return self._relay("POST", json.loads(raw or b"{}"))
        body = json.loads(raw or b"{}")
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404)
            return
        if str(body.get("grammar", "")).startswith('root ::= ("VERIFIED"'):    # the verifier's call, not repair's
            self.server.verify_calls.append(body)
            ans = self.server.verify_answer
        else:
            self.server.model_calls.append(body)
            ans = self.server.model_answer
        ans = ans(body) if callable(ans) else ans
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": ans}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _fresh_relay():
    return {"registered": {}, "boxes": {}, "calls": [], "deleted": []}


def relay_box(srv, channel, what):
    """the fake relay's list for one channel and direction ("to-phone", "to-mac", "sms"); tests append / read it."""
    return srv.relay["boxes"].setdefault(channel, {"to-phone": [], "to-mac": [], "sms": []})[what]


@pytest.fixture(scope="session")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.overrides, srv.model_calls, srv.model_answer = {}, [], "NOT_FOUND"
    srv.redirects, srv.hits, srv.auth, srv.peeks = {}, [], {}, []
    srv.state = {}
    srv.verify_calls, srv.verify_answer = [], "VERIFIED: fake"
    srv.relay, srv.relay_status, srv.ntfy = _fresh_relay(), 200, []
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset_server(request):
    if "server" in request.fixturenames:
        srv = request.getfixturevalue("server")
        srv.overrides.clear()
        srv.redirects.clear()
        srv.auth.clear()
        srv.hits.clear()
        srv.peeks.clear()
        srv.state.clear()
        srv.model_calls.clear()
        srv.model_answer = "NOT_FOUND"
        srv.verify_calls.clear()
        srv.verify_answer = "VERIFIED: fake"
        srv.relay, srv.relay_status = _fresh_relay(), 200
        srv.ntfy.clear()
    yield


@pytest.fixture(scope="session")
def pw():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def ext(pw):
    """(context, service_worker, extension_id) with the unpacked extension loaded."""
    try:
        ctx = pw.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="awos-ext-"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
    except Exception as e:  # no Chromium build in this environment
        pytest.skip(f"cannot launch Chromium with extensions: {e}")
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    ext_id = sw.url.split("/")[2]
    yield ctx, sw, ext_id
    ctx.close()


class Ext:
    """Thin driver: calls the worker's handlers directly (as the popup would) and polls state."""

    def __init__(self, ctx, sw, ext_id, server):
        self.ctx, self.sw, self.id, self.server = ctx, sw, ext_id, server

    def call(self, typ, **kw):
        r = self.sw.evaluate("([t, m]) => HANDLERS[t](m, {})", [typ, kw])
        assert not (isinstance(r, dict) and r.get("error")), r
        return r

    def tab_id(self, page):
        url = page.url
        return self.sw.evaluate("(u) => chrome.tabs.query({}).then(ts => ts.find(t => t.url === u).id)", url)

    def state(self):
        return self.call("get_state")

    def set_settings(self, **kw):
        # the agent brain (Tell it, goal-guided steps) is the fake server too: never a real model on this machine
        s = {"endpoint": self.server.base + "/v1", "model": "fake", "enabled": True, "findTimeoutMs": 1500,
             "agentEndpoint": self.server.base + "/v1", "agentModel": "fake-agent"}
        s.update(kw)
        return self.call("save_settings", settings=s)

    def wait_run(self, statuses=("done", "failed"), timeout=45):
        end = time.time() + timeout
        while time.time() < end:
            run = self.state()["run"]
            if run and run["status"] in statuses:
                return run
            time.sleep(0.2)
        raise AssertionError(f"run did not reach {statuses}: {self.state()['run']}")

    def popup(self, tab_id):
        p = self.ctx.new_page()
        p.goto(f"chrome-extension://{self.id}/popup.html?tab={tab_id}")
        return p


def live_worker(ctx, fallback):
    """the extension's running service worker (a test may have restarted it: test_portal_run)."""
    for w in list(reversed(ctx.service_workers)) or [fallback]:
        try:
            w.evaluate("1")
            return w
        except Exception:
            continue
    return fallback


@pytest.fixture()
def x(ext, server):
    ctx, sw, ext_id = ext
    sw = live_worker(ctx, sw)
    sw.evaluate("chrome.storage.local.clear().then(() => chrome.storage.session.clear()).then(() => chrome.alarms.clearAll())"
                ".then(() => chrome.storage.session.set({testNow: %d}))"
                ".then(() => { if (self.__realFetch) self.fetch = self.__realFetch; NOTICES.length = 0; })" % TEST_NOW_MS)
    d = Ext(ctx, sw, ext_id, server)
    d.set_settings()
    yield d
    live_worker(ctx, d.sw).evaluate("() => { if (self.__realFetch) self.fetch = self.__realFetch; }")
    ctx.unroute_all()
    for p in ctx.pages[1:]:
        p.close()
