"""
Opt-in sandbox egress (AWOS_SANDBOX_EGRESS=allowlist) wired into
scaffold/agent/sandbox.py: switch parsing, Seatbelt profile and env, the
host proxy's lifecycle, and the docker network / sidecar command lines
(docker mocked). Runs on any OS: nothing here calls sandbox-exec.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from scaffold.agent import sandbox as sbx
from scaffold.agent.sandbox import (
    DockerSandbox,
    SandboxResult,
    SeatbeltSandbox,
    build_seatbelt_profile,
    egress_allowlist,
    egress_mode,
    egress_proxy_env,
)

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "PIP_PROXY",
              "npm_config_proxy", "npm_config_https_proxy")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("AWOS_SANDBOX_EGRESS", "AWOS_SANDBOX_EGRESS_ALLOW", "AWOS_SANDBOX_EGRESS_LOG",
                "AWOS_SANDBOX_EGRESS_IMAGE", "AWOS_SANDBOX_NETWORK", "AWOS_SANDBOX_IMAGE",
                "AWOS_SANDBOX_EGRESS_PRESETS"):
        monkeypatch.delenv(key, raising=False)


# ── switch and allowlist ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    (None, "off"), ("", "off"), ("off", "off"), ("0", "off"),
    ("allowlist", "allowlist"), (" AllowList ", "allowlist"),
    ("on", "off"), ("1", "off"), ("everything", "off"),   # unknown fails closed
])
def test_egress_mode(value, expected):
    env = {} if value is None else {"AWOS_SANDBOX_EGRESS": value}
    assert egress_mode(env) == expected


def test_egress_allowlist_env_syntax():
    allow = egress_allowlist({"AWOS_SANDBOX_EGRESS_ALLOW": "proxy.golang.org,!registry.npmjs.org,*.example.org"})
    assert "proxy.golang.org" in allow and "*.example.org" in allow
    assert "registry.npmjs.org" not in allow and "pypi.org" in allow


def test_proxy_env_points_everything_at_the_proxy():
    env = egress_proxy_env("http://127.0.0.1:5555")
    for key in PROXY_VARS:
        assert env[key] == "http://127.0.0.1:5555"
    assert "localhost" in env["NO_PROXY"] and env["no_proxy"] == env["NO_PROXY"]


# ── Seatbelt profile ─────────────────────────────────────────────────────────


def test_profile_egress_rules(tmp_path):
    ws, tmp = tmp_path / "ws", tmp_path / "t"
    off = build_seatbelt_profile(ws, tmp, [], [], allow_network=False)
    on = build_seatbelt_profile(ws, tmp, [], [], allow_network=False, egress_proxy_port=41234)
    for profile in (off, on):
        assert "(deny network*)" in profile                 # still no direct internet
        assert '(allow network-outbound (remote ip "localhost:*"))' in profile
        assert "(allow network-outbound (remote ip \"*" not in profile
        assert "com.apple.dnssd.service" not in profile     # no DNS for the sandbox
    assert '(allow network-outbound (remote ip "localhost:41234"))' in on
    assert "localhost:41234" not in off
    assert "com.apple.trustd" in on and "com.apple.trustd" not in off
    # Outbound rule comes after the blanket deny (SBPL: last match wins).
    assert on.index("(deny network*)") < on.index("localhost:41234")


def test_profile_egress_port_ignored_with_full_network(tmp_path):
    opened = build_seatbelt_profile(tmp_path, tmp_path, [], [], allow_network=True, egress_proxy_port=1)
    assert "(deny network*)" not in opened and "localhost:1" not in opened


# ── Seatbelt sandbox: real host proxy ────────────────────────────────────────


class _Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({k.lower(): v for k, v in self.headers.items()}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _resolver(host, port):
    if host.endswith(".test"):
        return ["127.0.0.1"]
    raise socket.gaierror(host)


def _capture_runs(monkeypatch):
    seen = []

    def capped(argv, timeout_sec, kill=None, cwd=None, env=None):
        seen.append({"argv": argv, "env": env})
        return SandboxResult(0, "", "", False, 0.0)

    monkeypatch.setattr(sbx, "_run_capped", capped)
    return seen


def test_seatbelt_egress_off_by_default(tmp_path, monkeypatch):
    seen = _capture_runs(monkeypatch)
    box = SeatbeltSandbox(tmp_path)
    try:
        assert box.egress == "off"
        box.run("true")
        assert not any(k in seen[0]["env"] for k in PROXY_VARS)
        assert box.egress_proxy_url is None
        assert "com.apple.trustd" not in box.profile
        assert box.egress_report()["mode"] == "off"
    finally:
        box.close()


def test_seatbelt_full_network_supersedes_egress(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS", "allowlist")
    monkeypatch.setenv("AWOS_SANDBOX_NETWORK", "1")
    box = SeatbeltSandbox(tmp_path)
    try:
        assert box.egress == "off"
    finally:
        box.close()


def test_seatbelt_egress_lifecycle_env_profile_and_report(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS", "allowlist")
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS_ALLOW", "pkg.test")
    seen = _capture_runs(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    box = SeatbeltSandbox(ws, egress_resolver=_resolver)
    assert box.egress == "allowlist" and "pkg.test" in box.egress_allow
    assert box.egress_proxy_url is None            # lazy: nothing listens yet
    box.run("pip install x")
    url = box.egress_proxy_url
    assert url and url.startswith("http://127.0.0.1:")
    port = int(url.rsplit(":", 1)[1])
    env = seen[0]["env"]
    for key in PROXY_VARS:
        assert env[key] == url
    assert f'(remote ip "localhost:{port}")' in box.profile
    assert seen[0]["argv"][2] == box.profile        # the profile that ran has the rule
    # The proxy log sits outside the workspace and the sandbox's own tmpdir.
    log = box.egress_log_path
    assert ws not in log.parents and box.tmpdir not in log.parents
    # Use the proxy the way a sandboxed tool would (test upstreams on loopback
    # need the private-address guard relaxed for this one instance).
    box._proxy.allow_private = True
    box._proxy.allowed_ports = frozenset({80, 443, upstream})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": url}))
    with opener.open(f"http://pkg.test:{upstream}/simple/", timeout=5) as r:
        assert r.status == 200
    with pytest.raises(urllib.error.HTTPError) as denied:
        opener.open(f"http://evil.test:{upstream}/", timeout=5)
    assert denied.value.code == 403
    box.run("true")
    assert seen[1]["env"]["HTTPS_PROXY"] == url      # same proxy, not a second one
    box.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    report = box.egress_report()
    assert report["mode"] == "allowlist"
    assert report["allowed"] == {"pkg.test": 1}
    assert report["denied"] == {"evil.test": 1}
    assert "seatbelt" in report["enforcement"]
    assert not log.exists()                          # temp log removed, events kept
    assert "simple" not in json.dumps(report)


def test_seatbelt_egress_custom_log_kept(tmp_path, monkeypatch):
    log = tmp_path / "keep.jsonl"
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS_LOG", str(log))
    _capture_runs(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    box = SeatbeltSandbox(ws, egress="allowlist", egress_resolver=_resolver)
    box.run("true")
    s = socket.create_connection(("127.0.0.1", box._proxy.port), timeout=5)
    s.sendall(b"CONNECT evil.test:443 HTTP/1.1\r\n\r\n")
    assert s.recv(1024).startswith(b"HTTP/1.1 403")
    s.close()
    box.close()
    assert json.loads(log.read_text().splitlines()[0])["host"] == "evil.test"
    assert box.egress_report()["denied"] == {"evil.test": 1}


# ── Docker, mocked ───────────────────────────────────────────────────────────


@pytest.fixture
def fake_docker():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(argv, 0, "cid123\n", "")
        if argv[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(argv, 0, "awos-egress-proxy listening on 0.0.0.0:3128\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with mock.patch.object(sbx.subprocess, "run", side_effect=run):
        yield calls


def test_docker_egress_off_by_default(tmp_path, fake_docker, monkeypatch):
    _capture_runs(monkeypatch)
    box = DockerSandbox(tmp_path)
    argv = fake_docker[0]
    assert argv[:2] == ["docker", "run"]
    assert argv[argv.index("--network") + 1] == "none"
    assert not any(a[:3] == ["docker", "network", "create"] for a in fake_docker)
    box.run("true")
    assert not any(any(v.startswith("HTTP_PROXY=") for v in a) for a in [box.exec_argv("x")])
    assert len([a for a in fake_docker if a[:2] == ["docker", "run"]]) == 1  # no sidecar


def test_docker_egress_network_sidecar_and_env(tmp_path, fake_docker, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS", "allowlist")
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS_ALLOW", "!github.com")
    seen = _capture_runs(monkeypatch)
    box = DockerSandbox(tmp_path)
    net = box.egress_network
    assert net and net.startswith("awos-sbx-")
    # 1. an --internal network first, 2. the sandbox container on it only.
    assert fake_docker[0] == ["docker", "network", "create", "--internal",
                              "--label", "awos.sandbox=egress", net]
    run_argv = fake_docker[1]
    assert run_argv[argv_i(run_argv, "--network")] == net
    assert "none" not in run_argv and "host-gateway" not in " ".join(run_argv)
    assert "-e" not in run_argv
    # Lazy: no sidecar until the first run().
    assert len([a for a in fake_docker if a[:2] == ["docker", "run"]]) == 1
    box.run("pip install requests")
    side = [a for a in fake_docker if a[:2] == ["docker", "run"]][1]
    assert side[side.index("--name") + 1] == box.egress_sidecar
    # Started on the internal network only, under the alias it listens on.
    assert side[argv_i(side, "--network")] == net
    assert side[argv_i(side, "--network-alias")] == "awos-egress-proxy"
    assert "--read-only" in side and side[argv_i(side, "--cap-drop")] == "ALL"
    assert side[argv_i(side, "--security-opt")] == "no-new-privileges"
    assert "-e" not in side and "--env" not in side            # no host env in the proxy
    mounts = [side[i + 1] for i, a in enumerate(side) if a == "--mount"]
    src = Path(sbx.__file__).with_name("egress_proxy.py").resolve()
    assert f"type=bind,source={src},target=/awos/egress_proxy.py,readonly" in mounts
    assert not any("target=/workspace" in m for m in mounts)   # proxy never sees the workspace
    allow = side[argv_i(side, "--allow")].split(",")
    assert "github.com" not in allow and "pypi.org" in allow
    # Never 0.0.0.0: the alias resolves to its internal-network address only.
    assert side[argv_i(side, "--listen")] == "awos-egress-proxy:3128"
    assert "0.0.0.0" not in " ".join(side)
    # The default bridge (upstream) is attached after start.
    connect = next(a for a in fake_docker if a[:3] == ["docker", "network", "connect"])
    assert connect == ["docker", "network", "connect", "bridge", box.egress_sidecar]
    assert fake_docker.index(connect) > fake_docker.index(side)
    # The exec carries the proxy variables and nothing from the host.
    exec_argv = seen[0]["argv"]
    flags = [exec_argv[i + 1] for i, a in enumerate(exec_argv) if a == "-e"]
    for key in PROXY_VARS:
        assert f"{key}=http://awos-egress-proxy:3128" in flags
    box.run("true")
    assert len([a for a in fake_docker if a[:2] == ["docker", "run"]]) == 2  # sidecar started once
    box.close()
    tail = fake_docker[-3:]
    assert tail == [["docker", "rm", "-f", box.egress_sidecar], ["docker", "rm", "-f", "cid123"],
                    ["docker", "network", "rm", net]]
    assert box.egress_report()["mode"] == "allowlist"
    assert "internal" in box.egress_report()["enforcement"]


def argv_i(argv, flag):
    return argv.index(flag) + 1


def test_docker_sidecar_failure_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS", "allowlist")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "run"] and "--read-only" in argv:
            return subprocess.CompletedProcess(argv, 125, "", "pull access denied")
        if argv[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(argv, 0, "cid123\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with mock.patch.object(sbx.subprocess, "run", side_effect=run):
        box = DockerSandbox(tmp_path)
        res = box.run("pip install x")
        assert res.exit_code == 1 and "egress proxy failed to start" in res.stderr
        assert "pull access denied" in res.stderr
        assert box.egress_proxy_url is None
        box.close()
    assert ["docker", "network", "rm", box.egress_network] in calls


def test_docker_network_create_failure_aborts_start(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS", "allowlist")

    def run(argv, **kw):
        if argv[:3] == ["docker", "network", "create"]:
            return subprocess.CompletedProcess(argv, 1, "", "pool exhausted")
        raise AssertionError(f"unexpected {argv}")

    with mock.patch.object(sbx.subprocess, "run", side_effect=run), \
            pytest.raises(RuntimeError, match="network create failed"):
        DockerSandbox(tmp_path)
