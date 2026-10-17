"""
Sandbox hardening: sensitive loopback ports, git in a linked worktree,
extra control paths, the docker egress sidecar's listen address, and egress
presets. Runs on Linux: Seatbelt is checked through the generated profile,
docker through the generated command lines (subprocess mocked).
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from scaffold.agent import egress_proxy as ep
from scaffold.agent import sandbox as sbx
from scaffold.agent.sandbox import (
    DockerSandbox,
    SeatbeltSandbox,
    build_seatbelt_profile,
    egress_allowlist,
    sensitive_local_ports,
    worktree_git_paths,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("AWOS_SANDBOX_EGRESS", "AWOS_SANDBOX_EGRESS_ALLOW", "AWOS_SANDBOX_EGRESS_PRESETS",
                "AWOS_SANDBOX_NETWORK", "AWOS_SANDBOX_IMAGE", sbx.DENY_LOCAL_PORTS_ENV,
                sbx.READONLY_PATHS_ENV, sbx.READONLY_SWITCH_ENV,
                "AWOS_LOCAL_BASE_URL", "AWOS_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_run():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(argv, 0, "cid123\n", "")
        if argv[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(argv, 0, "awos-egress-proxy listening on 172.30.0.2:3128\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with mock.patch.object(sbx.subprocess, "run", side_effect=run):
        yield calls


def _mounts(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd),
                        "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


# ── 1. sensitive loopback ports ──────────────────────────────────────────────


def test_default_ports_include_llama_and_services():
    ports = sensitive_local_ports({})
    assert ports[0] == 8080  # default local base URL http://127.0.0.1:8080/v1
    for p in (11434, 2375, 2376, 5432, 3306, 6379, 27017, 9200):
        assert p in ports


def test_llama_port_follows_local_base_url_and_8080_only_when_llama():
    ports = sensitive_local_ports({"AWOS_LOCAL_BASE_URL": "http://localhost:9999/v1"})
    assert 9999 in ports and 8080 not in ports
    # A remote model server is nothing on this host to protect.
    ports = sensitive_local_ports({"AWOS_LOCAL_BASE_URL": "http://gpu-box.lan:8080/v1"})
    assert 8080 not in ports and 11434 in ports


def test_llama_port_read_from_providers(monkeypatch):
    monkeypatch.setenv("AWOS_LOCAL_BASE_URL", "http://127.0.0.1:7777/v1")
    assert sensitive_local_ports()[0] == 7777


def test_deny_ports_env_adds_and_removes():
    ports = sensitive_local_ports({sbx.DENY_LOCAL_PORTS_ENV: "1234, !5432,!8080, junk, 70000, !"})
    assert 1234 in ports
    assert 5432 not in ports and 8080 not in ports
    assert 70000 not in ports


def test_profile_denies_ports_after_localhost_allow():
    prof = build_seatbelt_profile(Path("/w"), Path("/t"), [], [], False, [],
                                  deny_local_ports=[11434, 2375, 5432])
    lines = prof.splitlines()
    allow = lines.index('(allow network-outbound (remote ip "localhost:*"))')
    deny = next(i for i, ln in enumerate(lines) if ln.startswith("(deny network-outbound"))
    assert allow < deny  # SBPL: last match wins, so the deny overrides the allow
    for p in (11434, 2375, 5432):
        assert f'(remote ip "localhost:{p}")' in lines[deny]


def test_profile_keeps_egress_proxy_port_open():
    prof = build_seatbelt_profile(Path("/w"), Path("/t"), [], [], False, [],
                                  egress_proxy_port=5432, deny_local_ports=[5432, 11434])
    lines = prof.splitlines()
    deny = next(ln for ln in lines if ln.startswith("(deny network-outbound"))
    assert "localhost:5432" not in deny and "localhost:11434" in deny
    # And the proxy allow is the last network rule.
    assert lines[-1] == '(allow network-outbound (remote ip "localhost:5432"))'


def test_no_port_denies_when_network_allowed():
    prof = build_seatbelt_profile(Path("/w"), Path("/t"), [], [], True, [], deny_local_ports=[11434])
    assert "deny network-outbound" not in prof


def test_seatbelt_sandbox_uses_port_config(tmp_path, monkeypatch):
    monkeypatch.setenv(sbx.DENY_LOCAL_PORTS_ENV, "4444,!6379")
    box = SeatbeltSandbox(tmp_path, read_deny=[], allow_network=False)
    try:
        deny = next(ln for ln in box.profile.splitlines() if ln.startswith("(deny network-outbound"))
        assert "localhost:4444" in deny and "localhost:11434" in deny
        assert "localhost:6379" not in deny
    finally:
        box.close()


def test_seatbelt_egress_proxy_survives_port_denies(tmp_path):
    box = SeatbeltSandbox(tmp_path, read_deny=[], allow_network=False, egress="allowlist",
                          egress_allow=["pypi.org"], deny_local_ports=[11434])
    try:
        box._ensure_egress()
        port = box._proxy.port
        lines = box.profile.splitlines()
        assert lines[-1] == f'(allow network-outbound (remote ip "localhost:{port}"))'
    finally:
        box.close()


def test_docker_has_no_host_loopback(tmp_path, fake_run):
    DockerSandbox(tmp_path)
    argv = fake_run[0]
    assert argv[argv.index("--network") + 1] == "none"
    joined = " ".join(argv)
    assert "--network host" not in joined and "host-gateway" not in joined
    assert "--add-host" not in argv


# ── 2. git in a linked worktree ──────────────────────────────────────────────


@pytest.fixture
def worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    wt = tmp_path / "jobs" / "job1"
    _git(repo, "worktree", "add", "-q", "-b", "job1", str(wt))
    return repo.resolve(), wt.resolve()


def test_worktree_git_paths_resolved(worktree):
    repo, wt = worktree
    assert (wt / ".git").is_file()
    info = worktree_git_paths(wt)
    assert info is not None
    assert info.commondir_host == repo / ".git"
    assert info.gitdir_host.parent == repo / ".git" / "worktrees"
    assert worktree_git_paths(repo) is None  # .git is a dir: nothing to add


def test_seatbelt_worktree_git_readable_not_writable(worktree):
    repo, wt = worktree
    common, gitdir = repo / ".git", worktree_git_paths(wt).gitdir_host
    # Read-deny the whole tmp tree, as the real defaults deny /Users.
    box = SeatbeltSandbox(wt, read_deny=[repo.parent], allow_network=False)
    try:
        lines = box.profile.splitlines()
        read_allow = next(ln for ln in lines if ln.startswith("(allow file-read* (subpath"))
        read_deny = next(i for i, ln in enumerate(lines) if ln.startswith("(deny file-read*"))
        assert lines.index(read_allow) > read_deny
        for p in (common, gitdir):
            assert f'(subpath "{p}")' in read_allow
        write_deny = [ln for ln in lines if ln.startswith("(deny file-write* (subpath")]
        assert len(write_deny) == 1
        for p in (common, gitdir):
            assert f'(subpath "{p}")' in write_deny[0]
        # The deny comes after the workspace write-allow, so it wins.
        ws_allow = next(i for i, ln in enumerate(lines) if ln.startswith("(allow file-write* (subpath"))
        assert lines.index(write_deny[0]) > ws_allow
        # Never a write allow for them.
        assert not any(ln.startswith("(allow file-write*") and str(common) in ln for ln in lines)
    finally:
        box.close()


def test_seatbelt_plain_repo_adds_no_git_holes(tmp_path):
    (tmp_path / ".git").mkdir()
    box = SeatbeltSandbox(tmp_path, read_deny=[], allow_network=False)
    try:
        assert box.git_read == []
        assert not any(ln.startswith("(deny file-write* (subpath") for ln in box.profile.splitlines())
    finally:
        box.close()


def test_docker_worktree_git_mounted_readonly_at_host_paths(worktree, fake_run):
    repo, wt = worktree
    gitdir = worktree_git_paths(wt).gitdir_host
    box = DockerSandbox(wt)
    mounts = _mounts(fake_run[0])
    common_spec = f"type=bind,source={repo / '.git'},target={repo / '.git'},readonly"
    gitdir_spec = f"type=bind,source={gitdir},target={gitdir},readonly"
    assert common_spec in mounts and gitdir_spec in mounts
    assert mounts.index(common_spec) < mounts.index(gitdir_spec)  # gitdir layered on top
    assert box.git_mounts == [common_spec, gitdir_spec]
    # The .git file itself stays read-only in the workspace.
    assert f"type=bind,source={wt}/.git,target=/workspace/.git,readonly" in mounts
    # No git data is mounted writable.
    assert not any(str(repo) in m and not m.endswith(",readonly") for m in mounts)


def test_docker_relative_gitdir_mapped_inside_container(tmp_path, fake_run):
    ws = tmp_path / "ws"
    gd = tmp_path / "store" / "wt"
    common = tmp_path / "store" / "common"
    ws.mkdir()
    gd.mkdir(parents=True)
    common.mkdir()
    (ws / ".git").write_text("gitdir: ../store/wt\n")
    (gd / "commondir").write_text("../common\n")
    DockerSandbox(ws)
    mounts = _mounts(fake_run[0])
    assert f"type=bind,source={common.resolve()},target=/store/common,readonly" in mounts
    assert f"type=bind,source={gd.resolve()},target=/store/wt,readonly" in mounts


# ── 3. control paths ─────────────────────────────────────────────────────────


def test_new_default_control_paths():
    paths = sbx.readonly_control_paths({})
    for p in (".mcp.json", ".aws", ".gemini", ".windsurf", ".continue"):
        assert p in paths
    for p in sbx.OPTIONAL_READONLY_PATHS:  # documented opt-ins, not defaults
        assert p not in paths
    assert set(sbx.OPTIONAL_READONLY_PATHS) == {".vscode", ".devcontainer", ".envrc"}


def test_seatbelt_denies_mcp_json_creation():
    prof = build_seatbelt_profile(Path("/w"), Path("/t"), [], [], False, sbx.readonly_control_paths({}))
    rx = next(re.compile(re.sub(r'\\(.)', r'\1', m.group(1)))
              for m in re.finditer(r'\(deny file-write\* \(regex "((?:[^"\\]|\\.)*)"\)\)', prof)
              if "mcp" in m.group(1))
    for hit in (".mcp.json", ".aws/config", ".gemini/settings.json", ".windsurf", ".continue/x"):
        assert rx.search(f"/w/{hit}"), hit
    assert not rx.search("/w/.mcp.jsonx") and not rx.search("/w/sub/.mcp.json")


def test_docker_missing_file_default_gets_file_placeholder(tmp_path, fake_run):
    ws = tmp_path.resolve()
    box = DockerSandbox(ws)
    mounts = _mounts(fake_run[0])
    # A read-only empty FILE over .mcp.json: the container can neither write
    # it nor replace it with its own file.
    spec = f"type=bind,source={box._empty_file},target=/workspace/.mcp.json,readonly"
    assert spec in mounts
    assert box._empty_file.is_file() and box._empty_file.stat().st_size == 0
    assert not (box._empty_file.stat().st_mode & 0o222)
    for d in (".aws", ".gemini", ".windsurf", ".continue"):
        assert f"type=bind,source={box._empty_dir},target=/workspace/{d},readonly" in mounts
    assert ws / ".mcp.json" in box._placeholders
    assert box.unprotected == []
    # Docker creates an empty mountpoint file; close() removes it.
    (ws / ".mcp.json").touch()
    (ws / ".aws").mkdir()
    box.close()
    assert not (ws / ".mcp.json").exists() and not (ws / ".aws").exists()


def test_docker_file_placeholder_with_content_is_kept(tmp_path, fake_run):
    ws = tmp_path.resolve()
    box = DockerSandbox(ws)
    (ws / ".mcp.json").write_text('{"servers": {}}')
    box.close()
    assert (ws / ".mcp.json").read_text() == '{"servers": {}}'


def test_docker_existing_mcp_json_bound_readonly(tmp_path, fake_run):
    ws = tmp_path.resolve()
    (ws / ".mcp.json").write_text("{}")
    box = DockerSandbox(ws)
    mounts = _mounts(fake_run[0])
    assert f"type=bind,source={ws}/.mcp.json,target=/workspace/.mcp.json,readonly" in mounts
    assert ws / ".mcp.json" not in box._placeholders


def test_docker_opt_in_paths_get_placeholders(tmp_path, fake_run, monkeypatch):
    monkeypatch.setenv(sbx.READONLY_PATHS_ENV, ".vscode,.envrc")
    ws = tmp_path.resolve()
    box = DockerSandbox(ws)
    mounts = _mounts(fake_run[0])
    assert f"type=bind,source={box._empty_dir},target=/workspace/.vscode,readonly" in mounts
    assert f"type=bind,source={box._empty_file},target=/workspace/.envrc,readonly" in mounts
    assert box.unprotected == []


# ── 4. docker egress sidecar listen address ──────────────────────────────────


def test_sidecar_listens_on_internal_alias_only(tmp_path, fake_run):
    box = DockerSandbox(tmp_path, egress="allowlist", egress_allow=["pypi.org"])
    side = box.sidecar_argv(tmp_path)
    assert side[side.index("--network") + 1] == box.egress_network
    assert side[side.index("--network-alias") + 1] == sbx.DOCKER_EGRESS_ALIAS
    assert side[side.index("--listen") + 1] == f"{sbx.DOCKER_EGRESS_ALIAS}:{sbx.DOCKER_EGRESS_PORT}"
    assert "0.0.0.0" not in " ".join(side)
    assert "0.0.0.0" not in box.ENFORCEMENT


def test_resolve_listen_host():
    assert ep.resolve_listen_host("") == ""
    assert ep.resolve_listen_host("10.1.2.3") == "10.1.2.3"
    calls = []

    def resolver(host, port, family, kind):
        calls.append(host)
        if len(calls) < 2:
            raise socket.gaierror("not yet")
        return [(family, kind, 6, "", ("172.30.0.2", 0))]

    assert ep.resolve_listen_host("awos-egress-proxy", timeout_sec=5, resolver=resolver) == "172.30.0.2"
    assert calls == ["awos-egress-proxy", "awos-egress-proxy"]

    def never(*a):
        raise socket.gaierror("no")

    with pytest.raises(OSError, match="did not resolve"):
        ep.resolve_listen_host("nope", timeout_sec=0.3, resolver=never)


def test_proxy_main_binds_resolved_name(tmp_path):
    """The standalone proxy binds the address a name resolves to, not 0.0.0.0."""
    src = Path(ep.__file__)
    proc = subprocess.Popen(
        [sys.executable, "-I", str(src), "--listen", "localhost:0", "--allow", "pypi.org"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        assert "listening on 127.0.0.1:" in line, line + proc.stderr.read()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ── 5. egress presets ────────────────────────────────────────────────────────


def test_presets_not_on_by_default():
    allow = egress_allowlist({})
    for h in ("crates.io", "index.crates.io", "static.crates.io", "registry.yarnpkg.com"):
        assert h not in allow


def test_presets_selected_by_env():
    allow = egress_allowlist({"AWOS_SANDBOX_EGRESS_PRESETS": " crates , yarn, bogus"})
    for h in ("crates.io", "index.crates.io", "static.crates.io", "registry.yarnpkg.com"):
        assert h in allow
    assert "pypi.org" in allow  # defaults kept
    only_yarn = egress_allowlist({"AWOS_SANDBOX_EGRESS_PRESETS": "yarn"})
    assert "registry.yarnpkg.com" in only_yarn and "crates.io" not in only_yarn


def test_allow_env_can_drop_a_preset_host():
    allow = egress_allowlist({"AWOS_SANDBOX_EGRESS_PRESETS": "crates",
                              "AWOS_SANDBOX_EGRESS_ALLOW": "!static.crates.io"})
    assert "crates.io" in allow and "static.crates.io" not in allow


def test_preset_hosts_dedup_and_unknown():
    assert ep.preset_hosts("yarn,yarn,unknown") == ["registry.yarnpkg.com"]
    assert ep.preset_hosts("") == []


def test_sandbox_picks_up_presets(tmp_path, monkeypatch, fake_run):
    monkeypatch.setenv("AWOS_SANDBOX_EGRESS_PRESETS", "crates")
    box = DockerSandbox(tmp_path, egress="allowlist")
    side = box.sidecar_argv(tmp_path)
    assert "index.crates.io" in side[side.index("--allow") + 1].split(",")
