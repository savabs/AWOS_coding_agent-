#!/usr/bin/env python3
"""AWOS Replay run-log sink: the extension POSTs each run log here; it is written into a folder (the project's runlogs/).

Why: Chrome extensions can only save into ~/Downloads, which macOS keeps away from terminal tools and from symlinks
into it. This listens on 127.0.0.1 only (never the network), accepts one JSON log per request from an extension page
(Origin chrome-extension://...), and writes runlog-<UTC stamp>-<verdict>.json. Standard library only.

    python3 scripts/awos_logsink.py --dir /path/to/runlogs [--port 8099]
"""
import argparse
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 8 * 1024 * 1024


def make_handler(out_dir):
    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            origin = self.headers.get("Origin", "")
            if origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, "dir": out_dir})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/runlog":
                return self._send(404, {"error": "not found"})
            if not self.headers.get("Origin", "").startswith("chrome-extension://"):
                return self._send(403, {"error": "only the extension may write logs"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BYTES:
                return self._send(413, {"error": "empty or too large"})
            try:
                log = json.loads(self.rfile.read(n))
            except ValueError:
                return self._send(400, {"error": "not JSON"})
            if not isinstance(log, dict) or not str(log.get("kind", "")).startswith("awos-replay-runlog"):
                return self._send(400, {"error": "not an AWOS run log"})
            tag = re.sub(r"[^a-z0-9_-]+", "-", str(log.get("tag") or "log").lower())[:40] or "log"
            name = "runlog-%s-%s.json" % (time.strftime("%Y-%m-%d-%H-%M-%S", time.gmtime()), tag)
            path = os.path.join(out_dir, name)
            k = 1
            while os.path.exists(path):
                path = os.path.join(out_dir, name[:-5] + "-%d.json" % k)
                k += 1
            with open(path, "w") as fh:
                json.dump(log, fh, indent=1)
            self._send(200, {"ok": True, "file": os.path.basename(path)})

    return Sink


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(os.path.abspath(a.dir))).serve_forever()


if __name__ == "__main__":
    main()
