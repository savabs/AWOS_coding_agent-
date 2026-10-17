"""
Tests for scaffold/agent/egress_proxy.py: a real proxy on localhost in front
of local upstream servers. Test host names are mapped to 127.0.0.1 through
the resolver hook, so no real DNS and no external network are used.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scaffold.agent import egress_proxy as ep
from scaffold.agent.egress_proxy import EgressProxy, host_allowed, parse_allowlist

# ── local upstreams ──────────────────────────────────────────────────────────


class _Echo(BaseHTTPRequestHandler):
    """Answers with what it received: path, headers, body."""

    hits: list = []  # noqa: RUF012 - reset per test by the fixture

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = []
            while True:
                size = int(self.rfile.readline().split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            body = b"".join(chunks)
        _Echo.hits.append(self.path)
        payload = json.dumps({
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body.decode(),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


@pytest.fixture
def upstream():
    _Echo.hits = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def echo_tcp():
    """Raw TCP echo server: stands in for a TLS server behind CONNECT."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    seen = []

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            seen.append(1)

            def echo(c=c):
                try:
                    while True:
                        d = c.recv(4096)
                        if not d:
                            break
                        c.sendall(d)
                finally:
                    c.close()
            threading.Thread(target=echo, daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    yield srv.getsockname()[1], seen
    srv.close()


TEST_HOSTS = {"pkg.test", "git.test", "evil.test"}


def resolver(host, port):
    if host in TEST_HOSTS:
        return ["127.0.0.1"]
    raise socket.gaierror(f"test resolver: unknown host {host}")


@pytest.fixture
def make_proxy(tmp_path):
    proxies = []

    def make(port, allow=("pkg.test", "git.test"), **kw):
        kw.setdefault("allow_private", True)  # test upstreams live on loopback
        p = EgressProxy(allow=list(allow), log_path=str(tmp_path / "egress.jsonl"),
                        resolver=resolver, allowed_ports=(80, 443, port), **kw).start()
        proxies.append(p)
        return p

    yield make
    for p in proxies:
        p.stop()


def _raw(proxy, data: bytes, timeout=5) -> bytes:
    s = socket.create_connection(("127.0.0.1", proxy.port), timeout=timeout)
    s.sendall(data)
    out = b""
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            out += d
    except TimeoutError:
        pass
    s.close()
    return out


def _body(resp: bytes) -> dict:
    return json.loads(resp.split(b"\r\n\r\n", 1)[1])


def _log(tmp_path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "egress.jsonl").read_text().splitlines()]


# ── allowlist syntax ─────────────────────────────────────────────────────────


def test_default_allowlist():
    assert parse_allowlist("") == list(ep.DEFAULT_EGRESS_ALLOW)
    assert set(ep.DEFAULT_EGRESS_ALLOW) == {
        "pypi.org", "files.pythonhosted.org", "registry.npmjs.org",
        "github.com", "codeload.github.com", "objects.githubusercontent.com"}


def test_allowlist_extend_remove_and_wildcards():
    allow = parse_allowlist(" Example.COM. , !github.com, *.corp.test, !nothere, bad host, /x, ")
    assert "example.com" in allow
    assert "github.com" not in allow
    assert "*.corp.test" in allow
    assert "pypi.org" in allow
    assert not any(" " in a or "/" in a for a in allow)
    assert host_allowed("a.corp.test", allow)
    assert host_allowed("a.b.corp.test", allow)
    assert not host_allowed("corp.test", allow)            # wildcard is subdomains only
    assert not host_allowed("evilcorp.test", allow)
    assert not host_allowed("pypi.org.evil.test", allow)   # exact match, not prefix
    assert not host_allowed("sub.pypi.org", allow)


# ── plain HTTP ───────────────────────────────────────────────────────────────


def test_allowed_host_passes_through_urllib(make_proxy, upstream, tmp_path):
    proxy = make_proxy(upstream)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy.port}"}))
    with opener.open(f"http://pkg.test:{upstream}/simple/requests/?token=s3cret", timeout=5) as r:
        data = json.loads(r.read())
    assert r.status == 200
    assert data["path"] == "/simple/requests/?token=s3cret"   # origin-form upstream
    assert data["headers"]["host"] == f"pkg.test:{upstream}"
    log = _log(tmp_path)
    assert log == [{**log[0], "decision": "allow", "method": "GET", "host": "pkg.test",
                    "port": upstream, "status": 200}]
    text = (tmp_path / "egress.jsonl").read_text()
    assert "s3cret" not in text and "/simple" not in text


def test_credentials_are_stripped_and_nothing_injected(make_proxy, upstream, tmp_path):
    proxy = make_proxy(upstream)
    req = (f"GET http://pkg.test:{upstream}/x HTTP/1.1\r\n"
           f"Host: other.test\r\n"
           "Authorization: Bearer ghp_SECRET\r\n"
           "Proxy-Authorization: Basic dXNlcjpwYXNz\r\n"
           "Cookie: session=SECRET\r\n"
           "Connection: keep-alive, X-Hop\r\n"
           "X-Hop: drop-me\r\n"
           "Proxy-Connection: keep-alive\r\n"
           "X-Keep: yes\r\n\r\n").encode()
    data = _body(_raw(proxy, req))
    h = data["headers"]
    assert "authorization" not in h and "proxy-authorization" not in h and "cookie" not in h
    assert "x-hop" not in h and "proxy-connection" not in h
    assert h["x-keep"] == "yes"
    assert h["host"] == f"pkg.test:{upstream}"          # rebuilt from the URL
    assert h["connection"] == "close"
    assert "SECRET" not in json.dumps(data)
    log_text = (tmp_path / "egress.jsonl").read_text()
    assert "SECRET" not in log_text and "Bearer" not in log_text and "X-Keep" not in log_text


def test_denied_host_gets_403_and_upstream_untouched(make_proxy, upstream, tmp_path):
    proxy = make_proxy(upstream)
    resp = _raw(proxy, f"GET http://evil.test:{upstream}/steal?d=1 HTTP/1.1\r\n\r\n".encode())
    head, body = resp.split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.1 403")
    assert b"evil.test" in body and b"not on the egress allowlist" in body
    assert b"AWOS_SANDBOX_EGRESS_ALLOW" in body
    assert _Echo.hits == []
    (ev,) = _log(tmp_path)
    assert ev["decision"] == "deny" and ev["host"] == "evil.test" and ev["status"] == 403
    assert ev["reason"] == "not_allowlisted"
    assert "steal" not in (tmp_path / "egress.jsonl").read_text()


def test_denied_host_via_urllib_raises_403(make_proxy, upstream):
    proxy = make_proxy(upstream)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy.port}"}))
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(f"http://evil.test:{upstream}/", timeout=5)
    assert exc.value.code == 403


def test_disallowed_port_refused(make_proxy, upstream):
    proxy = make_proxy(upstream)
    resp = _raw(proxy, b"GET http://pkg.test:2222/ HTTP/1.1\r\n\r\n")
    assert resp.startswith(b"HTTP/1.1 403") and b"port 2222" in resp


def test_private_address_refused_by_default(tmp_path, upstream):
    with EgressProxy(allow=["pkg.test"], resolver=resolver, allowed_ports=(upstream,),
                     log_path=str(tmp_path / "egress.jsonl")) as proxy:
        resp = _raw(proxy, f"GET http://pkg.test:{upstream}/ HTTP/1.1\r\n\r\n".encode())
    assert resp.startswith(b"HTTP/1.1 403") and b"private" in resp
    assert _Echo.hits == []
    assert _log(tmp_path)[0]["reason"] == "private_address"


def test_credentials_in_url_refused(make_proxy, upstream):
    proxy = make_proxy(upstream)
    resp = _raw(proxy, f"GET http://user:pw@pkg.test:{upstream}/ HTTP/1.1\r\n\r\n".encode())
    assert resp.startswith(b"HTTP/1.1 403")
    assert _Echo.hits == []


def test_non_proxy_requests_rejected(make_proxy, upstream):
    proxy = make_proxy(upstream)
    assert _raw(proxy, b"GET / HTTP/1.1\r\nHost: pkg.test\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert _raw(proxy, b"GET https://pkg.test/ HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert _raw(proxy, b"garbage\r\n\r\n").startswith(b"HTTP/1.1 400")


def test_post_bodies_forwarded(make_proxy, upstream):
    proxy = make_proxy(upstream)
    fixed = (f"POST http://pkg.test:{upstream}/up HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello").encode()
    assert _body(_raw(proxy, fixed))["body"] == "hello"
    chunked = (f"POST http://pkg.test:{upstream}/up HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
               "3\r\nabc\r\n4;ext=1\r\ndefg\r\n0\r\n\r\n").encode()
    assert _body(_raw(proxy, chunked))["body"] == "abcdefg"
    both = (f"POST http://pkg.test:{upstream}/up HTTP/1.1\r\nTransfer-Encoding: chunked\r\n"
            "Content-Length: 3\r\n\r\n0\r\n\r\n").encode()
    assert _raw(proxy, both).startswith(b"HTTP/1.1 400")


def test_pipelined_second_request_is_not_forwarded(make_proxy, upstream):
    """One request per client connection: a second request smuggled behind an
    approved one must not reach any upstream, allowed or not."""
    proxy = make_proxy(upstream)
    two = (f"GET http://pkg.test:{upstream}/first HTTP/1.1\r\n\r\n"
           f"GET http://pkg.test:{upstream}/second HTTP/1.1\r\nCookie: x=1\r\n\r\n").encode()
    resp = _raw(proxy, two)
    assert resp.startswith((b"HTTP/1.0 200", b"HTTP/1.1 200"))
    time.sleep(0.2)
    assert _Echo.hits == ["/first"]


# ── CONNECT ──────────────────────────────────────────────────────────────────


def test_connect_to_allowed_host_tunnels(make_proxy, echo_tcp, tmp_path):
    port, _ = echo_tcp
    proxy = make_proxy(port)
    s = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    s.sendall(f"CONNECT git.test:{port} HTTP/1.1\r\nHost: git.test:{port}\r\n"
              "Proxy-Authorization: Basic c2VjcmV0\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += s.recv(1024)
    assert head.startswith(b"HTTP/1.1 200")
    s.sendall(b"\x16\x03\x01tls-bytes")
    got = b""
    while len(got) < 12:
        got += s.recv(1024)
    assert got == b"\x16\x03\x01tls-bytes"
    s.close()
    (ev,) = _log(tmp_path)
    assert ev == {**ev, "decision": "allow", "method": "CONNECT", "host": "git.test", "port": port}
    assert "c2VjcmV0" not in (tmp_path / "egress.jsonl").read_text()


def test_connect_to_denied_host_refused(make_proxy, echo_tcp, tmp_path):
    port, seen = echo_tcp
    proxy = make_proxy(port)
    resp = _raw(proxy, f"CONNECT evil.test:{port} HTTP/1.1\r\n\r\n".encode())
    assert resp.startswith(b"HTTP/1.1 403") and b"evil.test" in resp
    assert seen == []
    for target in (f"127.0.0.1:{port}", "[::1]:443", f"localhost:{port}"):
        assert _raw(proxy, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode()).startswith(b"HTTP/1.1 403")
    assert seen == []
    log = _log(tmp_path)
    assert log[0]["decision"] == "deny" and log[0]["host"] == "evil.test"
    assert all(e["decision"] == "deny" for e in log)


def test_connect_dns_failure_is_502(make_proxy, echo_tcp):
    port, _ = echo_tcp
    proxy = make_proxy(port, allow=("nowhere.test",))
    assert _raw(proxy, f"CONNECT nowhere.test:{port} HTTP/1.1\r\n\r\n".encode()).startswith(b"HTTP/1.1 502")


# ── robustness ───────────────────────────────────────────────────────────────


def test_connection_cap_returns_503(make_proxy, upstream):
    proxy = make_proxy(upstream, max_connections=1, head_timeout=3)
    hog = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)  # never sends a request
    time.sleep(0.3)
    resp = _raw(proxy, f"GET http://pkg.test:{upstream}/ HTTP/1.1\r\n\r\n".encode())
    assert resp.startswith(b"HTTP/1.1 503")
    hog.close()
    time.sleep(0.5)
    assert _raw(proxy, f"GET http://pkg.test:{upstream}/ HTTP/1.1\r\n\r\n".encode())[9:12] == b"200"


def test_slow_client_head_times_out(make_proxy, upstream):
    proxy = make_proxy(upstream, head_timeout=0.5)
    s = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    s.sendall(b"GET http://pkg.test/ HTTP/1.1\r\n")  # never finishes the head
    t0 = time.monotonic()
    assert s.recv(1024) == b""  # proxy hung up
    assert time.monotonic() - t0 < 3
    s.close()


def test_oversized_head_rejected(make_proxy, upstream):
    proxy = make_proxy(upstream)
    big = b"GET http://pkg.test/ HTTP/1.1\r\nX: " + b"a" * (ep.MAX_HEADER_BYTES + 10) + b"\r\n\r\n"
    assert _raw(proxy, big).startswith(b"HTTP/1.1 431")


def test_idle_tunnel_is_closed(make_proxy, echo_tcp):
    port, _ = echo_tcp
    proxy = make_proxy(port, idle_timeout=0.5)
    s = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    s.sendall(f"CONNECT git.test:{port} HTTP/1.1\r\n\r\n".encode())
    assert s.recv(1024).startswith(b"HTTP/1.1 200")
    t0 = time.monotonic()
    assert s.recv(1024) == b""
    assert time.monotonic() - t0 < 4
    s.close()


def test_stop_closes_listener_and_live_tunnels(tmp_path, echo_tcp):
    port, _ = echo_tcp
    proxy = EgressProxy(allow=["git.test"], resolver=resolver, allowed_ports=(port,),
                        allow_private=True).start()
    s = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    s.sendall(f"CONNECT git.test:{port} HTTP/1.1\r\n\r\n".encode())
    assert s.recv(1024).startswith(b"HTTP/1.1 200")
    proxy.stop()
    assert s.recv(1024) == b""
    s.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", proxy.port), timeout=1)


def test_standalone_entry_point_runs_with_stdlib_only(tmp_path):
    """The docker sidecar runs the file directly with `python -I`."""
    src = Path(ep.__file__)
    log = tmp_path / "side.jsonl"
    proc = subprocess.Popen(
        [sys.executable, "-I", str(src), "--listen", "127.0.0.1:0", "--allow", "pypi.org",
         "--log", str(log)], stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        assert "listening" in line
        port = int(line.rsplit(":", 1)[1])
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
        assert s.recv(1024).startswith(b"HTTP/1.1 403")  # removed from the final list
        s.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert proc.returncode == 0
    assert json.loads(log.read_text().splitlines()[0])["host"] == "github.com"
