#!/usr/bin/env python3
"""Live, view-only screen stream for a disposable CI Mac (GitHub macOS runner).

Serves the console screen as an MJPEG stream (one `screencapture` frame about every
INTERVAL seconds) behind HTTP Basic auth, on 127.0.0.1 only. A tunnel (cloudflared)
exposes it. Frames flow continuously, so the tunnel never idles out.

    LIVE_VIEW_PASSWORD=... python3 scripts/live_screen_server.py --port 6080

Refuses to start unless running inside a VM on GitHub Actions: it must never stream a
real user's screen.
"""
import argparse
import base64
import hmac
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>AWOS live</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0;background:#111;color:#ddd;font:14px -apple-system,sans-serif}
header{padding:6px 10px}img{display:block;max-width:100%;height:auto;margin:0 auto}</style>
</head><body><header>AWOS live view (GitHub macOS runner, view-only)</header>
<img src="/stream" alt="live screen"></body></html>"""


def in_ci_vm() -> bool:
    try:
        vm = subprocess.run(["sysctl", "-n", "kern.hv_vmm_present"], capture_output=True,
                            text=True, timeout=5).stdout.strip() == "1"
    except Exception:
        vm = False
    return vm and os.environ.get("GITHUB_ACTIONS") == "true"


class Frames:
    """One background grabber shared by all viewers."""

    def __init__(self, interval: float, width: int):
        self.interval, self.width = interval, width
        self.jpeg = b""
        self.lock = threading.Lock()
        self.tmp = tempfile.mkdtemp(prefix="awos_live_")
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        raw, small = os.path.join(self.tmp, "s.jpg"), os.path.join(self.tmp, "t.jpg")
        while True:
            t0 = time.time()
            try:
                subprocess.run(["screencapture", "-x", "-C", "-t", "jpg", raw], timeout=10, check=True)
                subprocess.run(["sips", "-Z", str(self.width), "-s", "formatOptions", "60", raw,
                                "--out", small], capture_output=True, timeout=10, check=True)
                with open(small, "rb") as f:
                    data = f.read()
                with self.lock:
                    self.jpeg = data
            except Exception as e:  # keep streaming the last good frame
                print(f"grab failed: {e}", file=sys.stderr)
            time.sleep(max(0.0, self.interval - (time.time() - t0)))

    def latest(self) -> bytes:
        with self.lock:
            return self.jpeg


def make_handler(frames: Frames, user: str, password: str, interval: float):
    expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _authed(self) -> bool:
            got = self.headers.get("Authorization", "")
            if hmac.compare_digest(got.encode(), expected.encode()):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="AWOS live view"')
            self.end_headers()
            return False

        def do_GET(self):
            if not self._authed():
                return
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(PAGE)
                return
            if self.path.startswith("/stream"):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    while True:
                        jpg = frames.latest()
                        if jpg:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode())
                            self.wfile.write(jpg + b"\r\n")
                            self.wfile.flush()
                        time.sleep(interval)
                except (BrokenPipeError, ConnectionResetError):
                    return
            self.send_response(404)
            self.end_headers()

    return H


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6080)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--user", default="viewer")
    a = ap.parse_args()
    if not in_ci_vm():
        print("refusing: only streams a disposable GitHub Actions VM screen", file=sys.stderr)
        return 2
    pw = os.environ.get("LIVE_VIEW_PASSWORD", "")
    if len(pw) < 8:
        print("LIVE_VIEW_PASSWORD (>= 8 chars) required", file=sys.stderr)
        return 2
    frames = Frames(a.interval, a.width)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(frames, a.user, pw, a.interval))
    print(f"live screen on 127.0.0.1:{a.port} (user {a.user})", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
