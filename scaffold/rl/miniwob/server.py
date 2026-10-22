"""
server.py — loopback HTTP server for MiniWoB++ static tasks plus the AWOS shim.

Routes (all on 127.0.0.1):
    GET  /miniwob/<task>.html?awos_seed=N&awos_ep=E&awos_aria=1
                               the upstream task page with <script src="/__awos/shim.js">
                               injected just before </body>
    GET  /core/..., /common/...  upstream static files, served verbatim (read-only)
    GET  /__awos/shim.js       our shim (scaffold/rl/miniwob/shim.js)
    POST /__awos/event         {"ep", "type": "start"|"end", ...} from the shim
    GET  /__awos/episode/<ep>  the server-side record (debugging / tests)
    GET  /__awos/health        "ok"

Episode records live in memory (EpisodeStore); the first "start" and the first "end"
per episode are kept, later ones are ignored (a finished episode cannot be re-scored).
Upstream files are only ever read and served; nothing in the cache is executed here.
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import unquote, urlsplit

SHIM_PATH = Path(__file__).with_name("shim.js")
SHIM_TAG = '<script src="/__awos/shim.js"></script>'
MAX_BODY = 64 * 1024
_BODY_CLOSE = re.compile(r"</body\s*>", re.IGNORECASE)


def inject_shim(html: str) -> str:
    """Insert the shim tag before the last </body> (or append if there is none)."""
    matches = list(_BODY_CLOSE.finditer(html))
    if not matches:
        return html + "\n" + SHIM_TAG + "\n"
    m = matches[-1]
    return html[:m.start()] + SHIM_TAG + "\n" + html[m.start():]


class EpisodeStore:
    """Thread-safe in-memory episode records, keyed by an opaque episode id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._eps: Dict[str, dict] = {}
        self._cond = threading.Condition(self._lock)

    def new(self, task: str, seed: int, task_id: str = "") -> str:
        ep = uuid.uuid4().hex[:16]
        with self._lock:
            self._eps[ep] = {"ep": ep, "task": task, "seed": seed, "task_id": task_id,
                             "created": time.time(), "start": None, "end": None, "n_events": 0}
        return ep

    def record(self, ep: str, event: dict) -> bool:
        kind = event.get("type")
        if kind not in ("start", "end"):
            return False
        with self._cond:
            rec = self._eps.get(ep)
            if rec is None:
                return False
            rec["n_events"] += 1
            if rec[kind] is None:
                ev = {k: event.get(k) for k in ("reward", "raw_reward", "reason", "done", "utterance", "seed", "title")
                      if k in event}
                ev["t"] = time.time()
                rec[kind] = ev
                self._cond.notify_all()
            return True

    def get(self, ep: str) -> Optional[dict]:
        with self._lock:
            rec = self._eps.get(ep)
            return json.loads(json.dumps(rec)) if rec is not None else None

    def wait(self, ep: str, key: str, timeout: float) -> Optional[dict]:
        """Wait up to ``timeout`` s for rec[key] to be set; returns the record (or None)."""
        deadline = time.time() + timeout
        with self._cond:
            while True:
                rec = self._eps.get(ep)
                if rec is None:
                    return None
                if rec.get(key) is not None or time.time() >= deadline:
                    return json.loads(json.dumps(rec))
                self._cond.wait(timeout=max(0.0, deadline - time.time()))

    def drop(self, ep: str) -> None:
        with self._lock:
            self._eps.pop(ep, None)


def _make_handler(root: Path, store: EpisodeStore):
    root = root.resolve()
    shim_bytes = SHIM_PATH.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "awos-miniwob/1"

        def log_message(self, fmt, *args):   # quiet
            return

        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path == "/__awos/health":
                return self._send(200, b"ok")
            if path == "/__awos/shim.js":
                return self._send(200, shim_bytes, "application/javascript; charset=utf-8")
            if path.startswith("/__awos/episode/"):
                rec = store.get(path.rsplit("/", 1)[-1])
                if rec is None:
                    return self._send(404, b"no such episode")
                return self._send(200, json.dumps(rec).encode(), "application/json")
            if path in ("", "/"):
                return self._send(404, b"not found")
            target = (root / path.lstrip("/")).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return self._send(403, b"forbidden")
            if not target.is_file():
                return self._send(404, b"not found")
            data = target.read_bytes()
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix == ".html" and target.parent.name == "miniwob" and target.parent.parent == root:
                data = inject_shim(data.decode("utf-8", errors="replace")).encode("utf-8")
                ctype = "text/html; charset=utf-8"
            elif ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, data, ctype)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != "/__awos/event":
                return self._send(404, b"not found")
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY:
                return self._send(413 if n > MAX_BODY else 400, b"bad length")
            try:
                event = json.loads(self.rfile.read(n).decode("utf-8"))
                assert isinstance(event, dict)
            except Exception:
                return self._send(400, b"bad json")
            ok = store.record(str(event.get("ep", "")), event)
            return self._send(200 if ok else 404, b"ok" if ok else b"unknown episode")

    return Handler


class MiniWobServer:
    """Owns the ThreadingHTTPServer on 127.0.0.1:<port> (0 = any free port)."""

    def __init__(self, html_root: Path, port: int = 0, store: Optional[EpisodeStore] = None):
        self.html_root = Path(html_root)
        self.port = port
        self.store = store or EpisodeStore()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server not started")
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def start(self) -> str:
        if self._httpd is not None:
            return self.base_url
        httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _make_handler(self.html_root, self.store))
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="miniwob-http", daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
