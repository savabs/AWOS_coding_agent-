"""
server.py — loopback HTTP server hosting live App instances.

Public routes (what the page links to / posts to):
    GET  /t/<iid>/<view>[?query]      render a view of instance <iid>
    POST /t/<iid>/act                 apply one action (urlencoded form) -> 303 to the next view
Private routes (harness / verifier only; never linked from any page):
    GET  /__state?i=<iid>             ground-truth domain state (JSON)
    GET  /__events?i=<iid>            event log: every POST with its safety effects (JSON)
    GET  /__ui?i=<iid>                UI state (JSON; debugging)
    GET  /__health                    {"ok": true, "instances": n}
Everything else is 404. Binds 127.0.0.1 on an ephemeral port.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple

from .app import App, Form

MAX_BODY = 256 * 1024


class Registry:
    def __init__(self):
        self.apps: Dict[str, App] = {}
        self.locks: Dict[str, threading.Lock] = {}
        self.lock = threading.Lock()

    def add(self, app: App) -> None:
        with self.lock:
            self.apps[app.iid] = app
            self.locks[app.iid] = threading.Lock()

    def drop(self, iid: str) -> None:
        with self.lock:
            self.apps.pop(iid, None)
            self.locks.pop(iid, None)

    def get(self, iid: str) -> Tuple[Optional[App], Optional[threading.Lock]]:
        with self.lock:
            return self.apps.get(iid), self.locks.get(iid)


def handle_get(reg: Registry, path: str, query: str) -> Tuple[int, str, str, Optional[str]]:
    """(status, content-type, body, location) for a GET; shared by the HTTP server
    and the in-process transport."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query, keep_blank_values=True).items()}
    if path in ("/__state", "/__events", "/__ui"):
        app, lock = reg.get(q.get("i", ""))
        if app is None:
            return 404, "application/json", json.dumps({"error": "no such instance"}), None
        with lock:
            obj = {"/__state": app.state, "/__events": app.events, "/__ui": {k: v for k, v in app.ui.items()}}[path]
            return 200, "application/json", json.dumps(obj, default=str), None
    if path == "/__health":
        return 200, "application/json", json.dumps({"ok": True, "instances": len(reg.apps)}), None
    parts = path.split("/", 3)
    if len(parts) >= 3 and parts[1] == "t":
        app, lock = reg.get(parts[2])
        if app is not None:
            with lock:
                status, html = app.get(parts[3] if len(parts) > 3 else "", q)
            return status, "text/html; charset=utf-8", html, None
    return 404, "text/html; charset=utf-8", "<!doctype html><title>Not found</title><h1>Not found</h1>", None


def handle_post(reg: Registry, path: str, body: str) -> Tuple[int, str, str, Optional[str]]:
    parts = path.split("/", 3)
    if len(parts) == 4 and parts[1] == "t" and parts[3] == "act":
        app, lock = reg.get(parts[2])
        if app is not None:
            with lock:
                nxt = app.post(Form.parse(body))
            return 303, "text/plain", "", app.href(nxt)
    return 404, "text/plain", "not found", None


class _Handler(BaseHTTPRequestHandler):
    server_version = "AWOSFactory/0"
    reg: Registry = None  # type: ignore

    def log_message(self, *a):  # quiet
        pass

    def _send(self, status: int, ctype: str, body: str, location: Optional[str]) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/favicon.ico":
            return self._send(204, "text/plain", "", None)
        self._send(*handle_get(self.reg, u.path, u.query))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._send(413, "text/plain", "too large", None)
        body = self.rfile.read(n).decode("utf-8", "replace")
        self._send(*handle_post(self.reg, urllib.parse.urlsplit(self.path).path, body))


class FactoryServer:
    def __init__(self, reg: Registry, host: str = "127.0.0.1", port: int = 0):
        handler = type("Handler", (_Handler,), {"reg": reg})
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="factory-http", daemon=True)

    @property
    def base(self) -> str:
        h, p = self.httpd.server_address[:2]
        return f"http://{h}:{p}"

    def start(self) -> str:
        self.thread.start()
        return self.base

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def inproc_transport(reg: Registry, base: str = "http://127.0.0.1:0"):
    """SimBrowser transport that calls the handlers directly (no sockets)."""
    def t(method: str, url: str, body: Optional[str]):
        for _ in range(5):
            u = urllib.parse.urlsplit(url)
            if method == "GET":
                st, _ct, out, loc = handle_get(reg, u.path, u.query)
            else:
                st, _ct, out, loc = handle_post(reg, u.path, body or "")
            if st in (301, 302, 303) and loc:
                url, method, body = urllib.parse.urljoin(url, loc), "GET", None
                continue
            return st, out, url
        raise RuntimeError("redirect loop")
    return t


def http_transport():
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def t(method: str, url: str, body: Optional[str]):
        req = urllib.request.Request(url, data=body.encode() if body is not None else None, method=method,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {})
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8"), r.geturl()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), url
    return t
