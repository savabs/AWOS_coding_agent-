"""
egress_proxy.py — the sandbox's one door to the internet (opt-in).

The sandbox (sandbox.py) allows no network but loopback. Some tasks
legitimately need `pip install`, `npm install` or `git fetch`. With
AWOS_SANDBOX_EGRESS=allowlist the sandbox points HTTP(S)_PROXY at this
proxy, and this proxy is the only thing that talks to the outside:

- HTTPS: CONNECT tunnelling, only to an allowlisted host on an allowed port.
  The tunnel is opaque TLS; the proxy never terminates it and holds no CA.
- Plain HTTP: absolute-form requests (`GET http://host/path`) to an
  allowlisted host are forwarded with Authorization, Proxy-Authorization and
  Cookie stripped, hop-by-hop headers dropped and `Connection: close` forced:
  one request per client connection, so a pipelined second request can never
  ride an already-approved connection.
- Everything else gets a 403 that says which host was refused and why.
- No credential is ever added to anything: the proxy has none.
- Allowlisted names that resolve to loopback / private / link-local
  addresses are refused (allow_private=False), so the proxy cannot be used
  to reach services on the host or its LAN.
- Each request is logged as one JSONL line: decision, method, host, port,
  status, reason. Never a path, query string or header.

Robustness: bounded concurrent connections (extra clients get a 503),
connect / idle / total-tunnel timeouts, a cap on request-head size.

Stdlib only and free of package imports on purpose: DockerSandbox runs this
same file as `python egress_proxy.py --listen ...` in a sidecar container.

Allowlist syntax (AWOS_SANDBOX_EGRESS_ALLOW, same shape as
AWOS_SANDBOX_READONLY_PATHS): comma-separated; "name" adds an exact host,
"*.example.com" adds every subdomain (not the apex), "!name" removes a
default.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import re
import selectors
import signal
import socket
import sys
import threading
import time
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

#: Package registries and GitHub (fetch, tarballs, release assets).
DEFAULT_EGRESS_ALLOW = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
)
EGRESS_ALLOW_ENV = "AWOS_SANDBOX_EGRESS_ALLOW"
#: Plain HTTP on 80, CONNECT on 443. Anything else is refused.
DEFAULT_PORTS = (80, 443)
#: Never forwarded: credentials the sandboxed code (or a tool's config) put
#: on a request. Inside a CONNECT tunnel the proxy cannot see headers at all.
STRIP_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})
#: Hop-by-hop headers (RFC 9110 7.6.1) plus Expect (the proxy reads the whole
#: body before the upstream answers) and Host (rebuilt from the URL).
_DROP_HEADERS = frozenset({
    "connection", "proxy-connection", "keep-alive", "te", "trailer", "upgrade",
    "expect", "host", "proxy-authenticate",
})
MAX_HEADER_BYTES = 64 * 1024
MAX_CONNECTIONS = 64
CONNECT_TIMEOUT_SEC = 10.0
#: No byte in either direction for this long ends the connection.
IDLE_TIMEOUT_SEC = 120.0
#: Hard cap on one connection's life (a large wheel still fits).
MAX_CONNECTION_SEC = 3600.0
#: Request head must arrive within this long (slowloris guard).
HEAD_TIMEOUT_SEC = 30.0
MAX_EVENTS_IN_MEMORY = 10_000

_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST_RE = re.compile(rf"^{_LABEL}(\.{_LABEL})*$")

Resolver = Callable[[str, int], list]


def normalize_host(raw: str) -> Optional[str]:
    """Lower-case hostname without a trailing dot, or None when raw is not a
    plain DNS name / IPv4 literal (IPv6 literals, userinfo, junk)."""
    h = (raw or "").strip().lower().rstrip(".")
    if not h or len(h) > 253 or not _HOST_RE.match(h):
        return None
    return h


def parse_allowlist(value: Optional[str], defaults: Iterable[str] = DEFAULT_EGRESS_ALLOW) -> list[str]:
    """defaults + additions - "!removals" from a comma-separated spec."""
    allow = list(defaults)
    for raw in (value or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        drop = entry.startswith("!")
        if drop:
            entry = entry[1:].strip()
        wildcard = entry.startswith("*.")
        name = normalize_host(entry[2:] if wildcard else entry)
        if name is None:
            logger.warning("%s: ignoring %r (not a host name)", EGRESS_ALLOW_ENV, raw)
            continue
        item = ("*." if wildcard else "") + name
        if drop:
            allow = [a for a in allow if a != item]
        elif item not in allow:
            allow.append(item)
    return allow


def host_allowed(host: str, allow: Iterable[str]) -> bool:
    """Exact match, or "*.suffix" matching a strict subdomain."""
    for entry in allow:
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and host != entry[2:]:
                return True
        elif host == entry:
            return True
    return False


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified)


def _system_resolver(host: str, port: int) -> list:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen, out = set(), []
    for family, _, _, _, sockaddr in infos:
        if sockaddr[0] not in seen:
            seen.add(sockaddr[0])
            out.append(sockaddr[0])
    return out


class _Refused(Exception):
    def __init__(self, status: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status, self.reason, self.message = status, reason, message
        self.host: Optional[str] = None
        self.port: Optional[int] = None


_PHRASES = {400: "Bad Request", 403: "Forbidden", 431: "Request Header Fields Too Large",
            501: "Not Implemented", 502: "Bad Gateway", 503: "Service Unavailable",
            504: "Gateway Timeout"}


def _error_response(status: int, message: str) -> bytes:
    body = (f"AWOS sandbox egress proxy: {message}\n").encode("utf-8", "replace")
    return (f"HTTP/1.1 {status} {_PHRASES.get(status, 'Error')}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "X-AWOS-Egress: refused\r\n"
            "Connection: close\r\n\r\n").encode("ascii") + body


class _Reader:
    """Buffered reads from a socket, starting with bytes already received."""

    def __init__(self, sock: socket.socket, buf: bytes = b"") -> None:
        self.sock, self.buf = sock, bytearray(buf)

    def _fill(self) -> None:
        data = self.sock.recv(65536)
        if not data:
            raise _Refused(400, "client_closed", "client closed the connection mid-request")
        self.buf += data

    def line(self, limit: int = 8192) -> bytes:
        while b"\r\n" not in self.buf:
            if len(self.buf) > limit:
                raise _Refused(400, "bad_chunk", "chunk line too long")
            self._fill()
        i = self.buf.index(b"\r\n")
        out = bytes(self.buf[:i])
        del self.buf[: i + 2]
        return out

    def exact(self, n: int, sink: socket.socket) -> None:
        """Copy n bytes to sink without holding them all in memory."""
        while n > 0:
            if not self.buf:
                self._fill()
            chunk = bytes(self.buf[:n])
            del self.buf[: len(chunk)]
            sink.sendall(chunk)
            n -= len(chunk)


class EgressProxy:
    """A thread-per-connection HTTP/HTTPS forward proxy with a host allowlist."""

    def __init__(
        self,
        allow: Iterable[str] = DEFAULT_EGRESS_ALLOW,
        bind: str = "127.0.0.1",
        port: int = 0,
        log_path: Optional[str] = None,
        resolver: Optional[Resolver] = None,
        allowed_ports: Iterable[int] = DEFAULT_PORTS,
        allow_private: bool = False,
        max_connections: int = MAX_CONNECTIONS,
        connect_timeout: float = CONNECT_TIMEOUT_SEC,
        idle_timeout: float = IDLE_TIMEOUT_SEC,
        max_connection_sec: float = MAX_CONNECTION_SEC,
        head_timeout: float = HEAD_TIMEOUT_SEC,
    ) -> None:
        self.allow = list(allow)
        self.bind, self._port = bind, port
        self.log_path = log_path
        self.resolver = resolver or _system_resolver
        self.allowed_ports = frozenset(int(p) for p in allowed_ports)
        self.allow_private = allow_private
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.max_connection_sec = max_connection_sec
        self.head_timeout = head_timeout
        self._slots = threading.BoundedSemaphore(max_connections)
        self._lock = threading.Lock()
        self._active: set[socket.socket] = set()
        self._stop = threading.Event()
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._log_file = None
        self.events: list[dict] = []

    # ── lifecycle ──

    def start(self) -> "EgressProxy":
        if self._server is not None:
            return self
        family = socket.AF_INET6 if ":" in self.bind else socket.AF_INET
        srv = socket.socket(family, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind, self._port))
        srv.listen(128)
        srv.settimeout(0.25)  # accept() wakes to notice stop()
        self._server = srv
        self._port = srv.getsockname()[1]
        if self.log_path:
            self._log_file = open(self.log_path, "a", encoding="utf-8")
        self._thread = threading.Thread(target=self._accept_loop, name="awos-egress-proxy", daemon=True)
        self._thread.start()
        return self

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._server is not None and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        with self._lock:
            active = list(self._active)
        for s in active:
            _close(s)
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None

    def __enter__(self) -> "EgressProxy":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ── logging ──

    def _log(self, decision: str, method: str, host: Optional[str], port: Optional[int],
             status: int, reason: str = "") -> None:
        event = {"ts": round(time.time(), 3), "decision": decision, "method": method,
                 "host": host, "port": port, "status": status}
        if reason:
            event["reason"] = reason
        with self._lock:
            if len(self.events) < MAX_EVENTS_IN_MEMORY:
                self.events.append(event)
            if self._log_file is not None:
                self._log_file.write(json.dumps(event) + "\n")
                self._log_file.flush()

    # ── connections ──

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                try:
                    client.settimeout(2)
                    client.sendall(_error_response(503, "too many concurrent connections; retry"))
                except OSError:
                    pass
                _close(client)
                continue
            with self._lock:
                self._active.add(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True,
                             name="awos-egress-conn").start()

    def _serve(self, client: socket.socket) -> None:
        upstream: list[socket.socket] = []
        try:
            self._handle(client, upstream)
        except Exception as exc:  # one bad connection must never kill the proxy
            logger.debug("egress connection error: %s", exc)
        finally:
            for s in upstream:
                _close(s)
            with self._lock:
                self._active.discard(client)
                for s in upstream:
                    self._active.discard(s)
            _close(client)
            self._slots.release()

    def _read_head(self, client: socket.socket) -> tuple[bytes, bytes]:
        client.settimeout(self.head_timeout)
        buf = bytearray()
        deadline = time.monotonic() + self.head_timeout
        while b"\r\n\r\n" not in buf:
            if len(buf) > MAX_HEADER_BYTES:
                raise _Refused(431, "header_too_large", "request head too large")
            if time.monotonic() > deadline:
                raise socket.timeout()
            data = client.recv(65536)
            if not data:
                raise ConnectionError("client closed before sending a request")
            buf += data
        i = buf.index(b"\r\n\r\n")
        if i > MAX_HEADER_BYTES:
            raise _Refused(431, "header_too_large", "request head too large")
        return bytes(buf[:i]), bytes(buf[i + 4:])

    def _handle(self, client: socket.socket, upstream: list) -> None:
        method, host, port = "?", None, None
        try:
            head, rest = self._read_head(client)
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3 or not parts[2].startswith("HTTP/"):
                raise _Refused(400, "bad_request", "malformed request line")
            method, target, version = parts[0].upper(), parts[1], parts[2]
            headers = []
            for line in lines[1:]:
                name, sep, value = line.partition(":")
                if not sep or not name or name != name.strip():
                    raise _Refused(400, "bad_request", "malformed header line")
                headers.append((name, value.strip()))
            if method == "CONNECT":
                host, port = self._parse_authority(target)
                self._check(host, port)
                up = self._dial(host, port)
                upstream.append(up)
                with self._lock:
                    self._active.add(up)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self._log("allow", method, host, port, 200)
                if rest:
                    up.sendall(rest)
                self._relay(client, up)
            else:
                host, port = self._forward(client, method, target, version, headers, rest, upstream)
        except _Refused as r:
            decision = "deny" if r.status == 403 else "error"
            self._log(decision, method, r.host or host, r.port or port, r.status, r.reason)
            _send_quiet(client, _error_response(r.status, r.message))
        except socket.timeout:
            if host is not None:
                self._log("error", method, host, port, 504, "timeout")

    def _parse_authority(self, target: str) -> tuple[str, int]:
        hostpart, sep, portpart = target.rpartition(":")
        if not sep or not portpart.isdigit():
            raise _Refused(400, "bad_request", f"CONNECT target must be host:port, got {target[:100]!r}")
        host = normalize_host(hostpart)
        if host is None:
            raise _Refused(403, "bad_host", f"host {hostpart[:100]!r} is not a plain host name")
        return host, int(portpart)

    def _check(self, host: str, port: int) -> None:
        if not host_allowed(host, self.allow):
            raise _Refused(
                403, "not_allowlisted",
                f"host '{host}' is not on the egress allowlist. Allowed: {', '.join(self.allow)}. "
                f"The owner can extend it with {EGRESS_ALLOW_ENV}.")
        if port not in self.allowed_ports:
            raise _Refused(403, "port_not_allowed",
                           f"port {port} is not allowed for '{host}' "
                           f"(allowed ports: {', '.join(map(str, sorted(self.allowed_ports)))})")

    def _dial(self, host: str, port: int) -> socket.socket:
        try:
            ips = self.resolver(host, port)
        except OSError:
            raise _Refused(502, "dns_failure", f"could not resolve '{host}'")
        if not self.allow_private:
            public = [ip for ip in ips if _is_public(ip)]
            if ips and not public:
                raise _Refused(403, "private_address",
                               f"'{host}' resolves to a loopback/private address; refused")
            ips = public
        last = None
        for ip in ips:
            try:
                sock = socket.create_connection((ip, port), timeout=self.connect_timeout)
                sock.settimeout(self.idle_timeout)
                return sock
            except OSError as exc:
                last = exc
        raise _Refused(502, "upstream_unreachable", f"could not connect to '{host}:{port}' ({last})")

    def _forward(self, client, method, target, version, headers, rest, upstream) -> tuple:
        if not target.lower().startswith("http://"):
            if target.lower().startswith("https://"):
                raise _Refused(400, "use_connect", "https:// URLs must go through CONNECT")
            raise _Refused(400, "not_proxy_request",
                           "this is a forward proxy; send absolute-form http:// requests or CONNECT")
        try:
            url = urlsplit(target)
            port = url.port or 80
        except ValueError:
            raise _Refused(400, "bad_request", "malformed URL")
        if url.username is not None or url.password is not None:
            # Credentials in the URL are credentials too; refuse rather than forward.
            raise _Refused(403, "credentials_in_url", "URLs carrying user:password@ are refused")
        host = normalize_host(url.hostname or "")
        if host is None:
            raise _Refused(403, "bad_host", "URL host is not a plain host name")
        try:
            self._check(host, port)
            return self._forward_allowed(client, method, version, headers, rest, upstream,
                                         host, port, url)
        except _Refused as r:
            r.host, r.port = host, port
            raise

    def _forward_allowed(self, client, method, version, headers, rest, upstream, host, port, url):
        connection_named = set()
        for n, v in headers:
            if n.lower() == "connection":
                connection_named |= {t.strip().lower() for t in v.split(",")}
        te = [v.strip().lower() for n, v in headers if n.lower() == "transfer-encoding"]
        cl = [v.strip() for n, v in headers if n.lower() == "content-length"]
        if te and cl:
            raise _Refused(400, "ambiguous_body", "both Transfer-Encoding and Content-Length")
        if te and te != ["chunked"]:
            raise _Refused(501, "unsupported_te", "only chunked transfer-encoding is supported")
        if len(set(cl)) > 1 or (cl and not cl[0].isdigit()):
            raise _Refused(400, "bad_length", "invalid Content-Length")
        kept = [(n, v) for n, v in headers
                if n.lower() not in STRIP_HEADERS and n.lower() not in _DROP_HEADERS
                and n.lower() not in connection_named]
        path = url.path or "/"
        if url.query:
            path += "?" + url.query
        host_header = host if port == 80 else f"{host}:{port}"
        out = [f"{method} {path} {version}", f"Host: {host_header}"]
        out += [f"{n}: {v}" for n, v in kept]
        out.append("Connection: close")
        up = self._dial(host, port)
        upstream.append(up)
        with self._lock:
            self._active.add(up)
        up.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
        client.settimeout(self.idle_timeout)
        reader = _Reader(client, rest)
        if te:
            while True:
                line = reader.line()
                up.sendall(line + b"\r\n")
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError:
                    raise _Refused(400, "bad_chunk", "malformed chunk size")
                if size == 0:
                    while True:  # trailers, then the blank line
                        trailer = reader.line()
                        up.sendall(trailer + b"\r\n")
                        if not trailer:
                            break
                    break
                reader.exact(size + 2, up)
        elif cl:
            reader.exact(int(cl[0]), up)
        # Response: stream back until the upstream closes (Connection: close).
        self._log("allow", method, host, port, 200)
        deadline = time.monotonic() + self.max_connection_sec
        while time.monotonic() < deadline:
            data = up.recv(65536)
            if not data:
                break
            client.sendall(data)
        return host, port

    def _relay(self, a: socket.socket, b: socket.socket) -> None:
        """Copy both ways until both sides close, idle timeout, or the cap."""
        a.settimeout(self.idle_timeout)
        b.settimeout(self.idle_timeout)
        peer = {a: b, b: a}
        sel = selectors.DefaultSelector()
        sel.register(a, selectors.EVENT_READ)
        sel.register(b, selectors.EVENT_READ)
        deadline = time.monotonic() + self.max_connection_sec
        try:
            while sel.get_map() and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready = sel.select(timeout=min(self.idle_timeout, remaining))
                if not ready:
                    break  # idle
                for key, _ in ready:
                    src = key.fileobj
                    try:
                        data = src.recv(65536)
                    except OSError:
                        data = b""
                    if data:
                        peer[src].sendall(data)
                    else:
                        sel.unregister(src)
                        try:
                            peer[src].shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
        finally:
            sel.close()


def _send_quiet(sock: socket.socket, data: bytes) -> None:
    try:
        sock.settimeout(5)
        sock.sendall(data)
    except OSError:
        pass


def _close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    """Standalone entry point (the docker sidecar runs this)."""
    p = argparse.ArgumentParser(description="AWOS sandbox egress proxy")
    p.add_argument("--listen", default="127.0.0.1:3128", help="host:port to bind")
    p.add_argument("--allow", default=",".join(DEFAULT_EGRESS_ALLOW),
                   help="comma-separated final allowlist (exact hosts or *.suffix)")
    p.add_argument("--log", default=None, help="JSONL log path")
    args = p.parse_args(argv)
    bind, _, port = args.listen.rpartition(":")
    allow = parse_allowlist(args.allow, defaults=())
    proxy = EgressProxy(allow=allow, bind=bind or "0.0.0.0", port=int(port), log_path=args.log).start()
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    print(f"awos-egress-proxy listening on {bind}:{proxy.port}", flush=True)
    done.wait()
    proxy.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
