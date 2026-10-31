"""Fixtures for the AWOS Replay extension tests.

A local HTTP server (127.0.0.1, random port) serves tests/extension/pages and a
fake OpenAI-compatible model at /v1/chat/completions (no real model, no network).
`server.overrides` swaps the file behind a path, so the same recorded URL can
serve a renamed / deleted variant at replay time; `server.redirects` answers a path with a 302 (a portal whose
session expired sends you to its login page) and `server.hits` lists every GET path (files downloaded once?). Chromium runs headless with
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
        self.server.hits.append(path)
        loc = self.server.redirects.get(path)
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

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
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


@pytest.fixture(scope="session")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.overrides, srv.model_calls, srv.model_answer = {}, [], "NOT_FOUND"
    srv.redirects, srv.hits = {}, []
    srv.verify_calls, srv.verify_answer = [], "VERIFIED: fake"
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
        srv.hits.clear()
        srv.model_calls.clear()
        srv.model_answer = "NOT_FOUND"
        srv.verify_calls.clear()
        srv.verify_answer = "VERIFIED: fake"
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
        s = {"endpoint": self.server.base + "/v1", "model": "fake", "enabled": True, "findTimeoutMs": 1500}
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
                ".then(() => { if (self.__realFetch) self.fetch = self.__realFetch; NOTICES.length = 0; })")
    d = Ext(ctx, sw, ext_id, server)
    d.set_settings()
    yield d
    live_worker(ctx, d.sw).evaluate("() => { if (self.__realFetch) self.fetch = self.__realFetch; }")
    ctx.unroute_all()
    for p in ctx.pages[1:]:
        p.close()
