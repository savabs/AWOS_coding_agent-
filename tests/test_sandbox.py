"""Tests for scaffold/agent/sandbox.py.

The Seatbelt tests are real: they run sandbox-exec and check that the kernel
actually refuses what the profile denies. Docker is not assumed installed, so
its backend is tested with subprocess mocked.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from scaffold.agent import sandbox as sbx
from scaffold.agent.sandbox import (
    DockerSandbox,
    SandboxResult,
    SeatbeltSandbox,
    build_seatbelt_profile,
    make_sandbox,
    sbpl_quote,
    truncate_output,
)

darwin_only = pytest.mark.skipif(
    not (sys.platform == "darwin" and os.path.exists(sbx.SANDBOX_EXEC)),
    reason="Seatbelt needs macOS sandbox-exec",
)


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_sbpl_quote_escapes_quotes_and_backslashes():
    assert sbpl_quote("/a b/c") == '"/a b/c"'
    assert sbpl_quote('/x"y') == '"/x\\"y"'
    assert sbpl_quote("/x\\y") == '"/x\\\\y"'
    assert sbpl_quote('/a")(allow default)') == '"/a\\")(allow default)"'


def test_sbpl_quote_rejects_newlines():
    with pytest.raises(ValueError):
        sbpl_quote("/a\nb")


def test_profile_denies_network_unless_allowed(tmp_path):
    ws, tmp = tmp_path / "ws", tmp_path / "t"
    closed = build_seatbelt_profile(ws, tmp, [], [Path("/Users")], allow_network=False)
    opened = build_seatbelt_profile(ws, tmp, [], [Path("/Users")], allow_network=True)
    assert "(deny network*)" in closed
    assert "(deny network*)" not in opened


def test_truncate_keeps_head_and_tail():
    text = "HEAD" + "x" * 50_000 + "TAIL"
    out = truncate_output(text, limit=1000)
    assert out.startswith("HEAD") and out.endswith("TAIL")
    assert "truncated" in out
    assert len(out) < 1100
    assert truncate_output("short") == "short"


# ── Seatbelt, for real ───────────────────────────────────────────────────────


@pytest.fixture
def seatbelt(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    box = SeatbeltSandbox(ws, allow_network=False)
    yield box
    box.close()


@darwin_only
def test_write_inside_workspace_allowed(seatbelt):
    res = seatbelt.run("echo hello > out.txt && cat out.txt")
    assert res.exit_code == 0, res.stderr
    assert res.stdout.strip() == "hello"
    assert (seatbelt.workspace / "out.txt").read_text().strip() == "hello"


@darwin_only
def test_write_outside_workspace_blocked(seatbelt, tmp_path):
    outside = tmp_path / "outside.txt"  # a sibling of the workspace, same temp root
    res = seatbelt.run(f"echo pwned > '{outside}'")
    assert res.exit_code != 0
    assert "Operation not permitted" in res.stderr
    assert not outside.exists()


@darwin_only
def test_write_to_tmp_and_home_blocked(seatbelt):
    target = Path.home() / f"awos_sandbox_probe_{os.getpid()}"
    res = seatbelt.run(f"echo x > /tmp/awos_probe_{os.getpid()}; echo x > '{target}'")
    assert res.stderr.count("Operation not permitted") == 2
    assert not target.exists()
    assert not Path(f"/tmp/awos_probe_{os.getpid()}").exists()


@darwin_only
def test_private_tmpdir_is_writable_and_is_home(seatbelt):
    res = seatbelt.run('echo t > "$TMPDIR/t" && cat "$TMPDIR/t" && echo "$HOME"')
    assert res.exit_code == 0, res.stderr
    lines = res.stdout.split()
    assert lines[0] == "t"
    assert lines[1] == str(seatbelt.tmpdir)


@darwin_only
def test_secret_in_denied_home_like_dir_unreadable(tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".ssh").mkdir(parents=True)
    (fake_home / ".ssh" / "id_rsa").write_text("FAKE-PRIVATE-KEY")
    ws = tmp_path / "ws"
    ws.mkdir()
    box = SeatbeltSandbox(ws, read_deny=[fake_home], allow_network=False)
    try:
        res = box.run(f"cat '{fake_home}/.ssh/id_rsa'")
        assert res.exit_code != 0
        assert "FAKE-PRIVATE-KEY" not in res.stdout
        # The workspace itself stays readable even though read_deny is set.
        (ws / "data.txt").write_text("ok")
        assert box.run("cat data.txt").stdout == "ok"
    finally:
        box.close()


@darwin_only
def test_real_home_secrets_blocked(seatbelt):
    home = Path.home()
    # Only exit codes are inspected; no secret content is ever printed.
    res = seatbelt.run(
        f"ls '{home}' >/dev/null 2>&1; echo ls=$?; "
        f"cat '{home}/.ssh/id_rsa' >/dev/null 2>&1; echo ssh=$?; "
        f"cat '{home}/.aws/credentials' >/dev/null 2>&1; echo aws=$?"
    )
    assert "ls=0" not in res.stdout
    assert "ssh=0" not in res.stdout
    assert "aws=0" not in res.stdout


@darwin_only
def test_dotenv_inside_workspace_unreadable(seatbelt):
    (seatbelt.workspace / ".env").write_text("OPENROUTER_API_KEY=sk-fake")
    (seatbelt.workspace / "sub").mkdir()
    (seatbelt.workspace / "sub" / ".env.local").write_text("X=1")
    res = seatbelt.run("cat .env; cat sub/.env.local")
    assert "sk-fake" not in res.stdout
    assert "X=1" not in res.stdout
    assert res.stderr.count("Operation not permitted") == 2


@darwin_only
def test_dotenv_cannot_be_renamed_copied_or_linked_out(seatbelt):
    # Reads of .env are denied by name, so any way to give the bytes another
    # name must be denied too: rename, move into a dir, copy, hard link.
    env = seatbelt.workspace / ".env"
    env.write_text("SECRET=hunter2")
    (seatbelt.workspace / "d").mkdir()
    res = seatbelt.run(
        "mv .env moved; cat moved; mv .env d/; cat d/.env; cp .env c; cat c; "
        "ln .env h; cat h; ln -s .env s; cat s; echo NEW > .env.new"
    )
    assert "hunter2" not in res.stdout
    assert env.read_text() == "SECRET=hunter2"
    assert not (seatbelt.workspace / ".env.new").exists()


@darwin_only
def test_git_dir_is_read_only(seatbelt):
    # The host runs git in the workspace unsandboxed (job reports, commits):
    # a writable .git/config or hook would run code outside the sandbox.
    ws = seatbelt.workspace
    (ws / ".git" / "hooks").mkdir(parents=True)
    (ws / ".git" / "config").write_text("[core]\n")
    (ws / "sub" / ".git").mkdir(parents=True)
    res = seatbelt.run(
        "echo '[core] fsmonitor = touch pwned' >> .git/config; "
        "echo 'touch pwned' > .git/hooks/pre-commit; "
        "echo x > sub/.git/config; "
        "rm .git/config; mv .git moved_git; "
        "echo '*.pyc' > .gitignore && cat .gitignore"
    )
    assert (ws / ".git" / "config").read_text() == "[core]\n"
    assert not (ws / ".git" / "hooks" / "pre-commit").exists()
    assert not (ws / "sub" / ".git" / "config").exists()
    assert not (ws / "moved_git").exists()
    # Look-alike names are ordinary files.
    assert res.stdout.strip() == "*.pyc"


@darwin_only
def test_git_reads_still_work(seatbelt):
    ws = seatbelt.workspace
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "a.py").write_text("x = 1\n")
    res = seatbelt.run("git status --short && git diff --stat; echo rc=$?")
    assert "?? a.py" in res.stdout, res.stderr
    assert "rc=0" in res.stdout


@darwin_only
def test_writes_are_deny_by_default(seatbelt):
    # /Applications is admin-writable on a single-admin Mac and was missing
    # from the old deny-list; every path outside the task is refused now.
    probe = f"/Applications/.awos_probe_{os.getpid()}"
    res = seatbelt.run(f"touch '{probe}'; echo x > /dev/null && echo devnull-ok")
    assert not os.path.exists(probe)
    assert "Operation not permitted" in res.stderr
    assert "devnull-ok" in res.stdout


@darwin_only
def test_shared_temp_areas_unreadable(tmp_path):
    # Other jobs' workspaces and other sandboxes' tmpdirs live in the shared
    # temp areas; the default profile must not let one task read another's.
    ws1, ws2 = tmp_path / "one", tmp_path / "two"
    ws1.mkdir()
    ws2.mkdir()
    (ws2 / "other_job.txt").write_text("OTHER-JOB")
    probe = Path(f"/private/tmp/awos_read_probe_{os.getpid()}")
    probe.write_text("TMP-SECRET")
    box1, box2 = SeatbeltSandbox(ws1, allow_network=False), SeatbeltSandbox(ws2, allow_network=False)
    try:
        (box2.tmpdir / "t.txt").write_text("OTHER-TMPDIR")
        res = box1.run(f"cat '{ws2}/other_job.txt' '{probe}' '{box2.tmpdir}/t.txt'; ls '{tmp_path}'")
        for secret in ("OTHER-JOB", "TMP-SECRET", "OTHER-TMPDIR", "two"):
            assert secret not in res.stdout
        # Its own workspace and tmpdir, both in the same temp root, still work.
        ok = box1.run('echo a > f && cat f && echo b > "$TMPDIR/g" && cat "$TMPDIR/g" && '
                      "python -c 'import os; print(os.getcwd())'")
        assert ok.exit_code == 0, ok.stderr
        assert ok.stdout.split()[:2] == ["a", "b"]
    finally:
        probe.unlink()
        box1.close()
        box2.close()


@darwin_only
def test_workspace_inside_a_read_denied_dir(tmp_path):
    # Real jobs live under /Users, which is read-denied: the workspace hole and
    # the stat-only ancestors are what let them read their own files.
    parent = tmp_path / "deny"
    ws = parent / "proj"
    ws.mkdir(parents=True)
    (ws / "a.txt").write_text("mine\n")
    (parent / "secret.txt").write_text("PARENT-SECRET")
    box = SeatbeltSandbox(ws, read_deny=[parent], allow_network=False)
    try:
        ok = box.run("cat a.txt && pwd && python -c 'import os; print(os.getcwd())'")
        assert ok.exit_code == 0, ok.stderr
        assert ok.stdout.split() == ["mine", str(ws.resolve()), str(ws.resolve())]
        bad = box.run("cat ../secret.txt; ls ..")
        assert "PARENT-SECRET" not in bad.stdout
        assert "proj" not in bad.stdout
        assert bad.stderr.count("Operation not permitted") == 2
    finally:
        box.close()


@darwin_only
def test_launchservices_cannot_start_a_program_outside(seatbelt, tmp_path):
    # A program `open` launches is spawned by a system daemon, outside the
    # sandbox. The probe app only touches a file next to the workspace.
    outside = tmp_path / "escaped.txt"
    macos = seatbelt.workspace / "Probe.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "Probe"
    exe.write_text(f"#!/bin/sh\ntouch '{outside}'\n")
    exe.chmod(0o755)
    (seatbelt.workspace / "Probe.app" / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        "<key>CFBundleExecutable</key><string>Probe</string>"
        "<key>CFBundleIdentifier</key><string>com.awos.sandboxprobe</string>"
        "<key>LSUIElement</key><true/></dict></plist>"
    )
    res = seatbelt.run("open -g ./Probe.app; echo rc=$?")
    import time

    time.sleep(2)
    assert not outside.exists()
    assert "rc=0" not in res.stdout


@darwin_only
def test_mach_allow_list_keeps_tools_working_and_blocks_keychain(seatbelt):
    res = seatbelt.run(
        "python -c 'import getpass; print(getpass.getuser())'; "
        "security find-generic-password -s awos-no-such-item >/dev/null 2>&1; echo sec=$?; "
        "pbpaste >/dev/null 2>&1; echo pb=$?"
    )
    assert res.stdout.splitlines()[0].strip()  # user lookup needs opendirectoryd
    assert "sec=0" not in res.stdout and "pb=0" not in res.stdout
    profile = seatbelt.profile
    assert "(deny mach-lookup)" in profile
    assert "com.apple.SecurityServer" not in profile
    assert "com.apple.trustd" not in profile  # network services only with network on


@darwin_only
def test_timeout_kills_a_child_that_left_the_process_group(seatbelt):
    # setsid() takes a child out of the group killpg reaches; it must still
    # die, and a pipe it holds open must not keep run() waiting.
    code = (
        "import os, sys, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid(); print(os.getpid(), flush=True); time.sleep(3600)\n"
        "else:\n"
        "    time.sleep(3600)\n"
    )
    (seatbelt.workspace / "escape.py").write_text(code)
    res = seatbelt.run("python escape.py", timeout_sec=2)
    assert res.timed_out
    assert res.duration_sec < 2 + sbx.DRAIN_GRACE_SEC + 2
    pid = int(res.stdout.split()[0])
    import time

    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@darwin_only
def test_background_process_holding_the_pipe_does_not_hang_run(seatbelt):
    # The command exits at once; its detached child keeps stdout open.
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid(); print(os.getpid(), flush=True); time.sleep(30)\n"
    )
    (seatbelt.workspace / "bg.py").write_text(code)
    res = seatbelt.run("python bg.py", timeout_sec=60)
    try:
        assert not res.timed_out
        assert res.duration_sec < sbx.DRAIN_GRACE_SEC + 3
        assert "kept the output open" in res.stderr
    finally:
        try:
            os.kill(int(res.stdout.split()[0]), 9)
        except (ProcessLookupError, ValueError, IndexError):
            pass


@darwin_only
def test_file_size_is_capped(seatbelt):
    # Python ignores SIGXFSZ, so the oversize write surfaces as an OSError.
    code = (
        "f = open('big', 'wb')\n"
        f"f.seek({sbx.MAX_FILE_BLOCKS * 1024 + 1}); f.write(b'x'); f.close()\n"
        "print('WROTE')\n"
    )
    (seatbelt.workspace / "big.py").write_text(code)
    res = seatbelt.run("python big.py")
    assert "WROTE" not in res.stdout
    assert "File too large" in res.stderr


def test_output_capture_is_bounded_in_memory():
    buf = sbx._HeadTail(limit=1000)
    for i in range(20_000):
        buf.add(b"x" * 1000)
    buf.add(b"THE-END")
    assert len(buf.head) + len(buf.tail) <= 2 * 1000
    text = buf.text()
    assert text.endswith("THE-END") and "truncated" in text
    assert len(text) < 1100
    small = sbx._HeadTail(limit=1000)
    small.add(b"abc")
    small.add(b"def")
    assert small.text() == "abcdef"


def test_run_capped_streams_a_flood_without_buffering_it(tmp_path):
    # ~100 MB of output; the old communicate() kept all of it in memory.
    code = "import sys\nchunk = 'y' * 65536\nfor _ in range(1600):\n    sys.stdout.write(chunk)\nprint('DONE')\n"
    res = sbx._run_capped([sys.executable, "-c", code], timeout_sec=60, cwd=tmp_path)
    assert res.exit_code == 0
    assert len(res.stdout) <= sbx.MAX_OUTPUT_CHARS + 100
    assert res.stdout.rstrip().endswith("DONE")
    assert "truncated" in res.stdout


@darwin_only
def test_network_blocked_by_default(seatbelt):
    code = (
        "import socket\n"
        "s = socket.socket(); s.settimeout(3)\n"
        "try:\n"
        "    s.connect(('1.1.1.1', 443)); print('CONNECTED')\n"
        "except OSError as e:\n"
        "    print('BLOCKED', e)\n"
    )
    (seatbelt.workspace / "net.py").write_text(code)
    res = seatbelt.run("python net.py")
    assert "BLOCKED" in res.stdout, res.stdout + res.stderr
    assert "CONNECTED" not in res.stdout


@darwin_only
def test_network_env_flag_opens_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX_NETWORK", "1")
    box = SeatbeltSandbox(tmp_path)
    try:
        assert box.allow_network
        assert "(deny network*)" not in box.profile
    finally:
        box.close()


@darwin_only
def test_env_is_scrubbed(seatbelt, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-FAKE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "FAKE")
    res = seatbelt.run("env")
    assert res.exit_code == 0
    names = {line.split("=", 1)[0] for line in res.stdout.splitlines() if "=" in line}
    assert "FAKE" not in res.stdout
    assert not any("KEY" in n or "TOKEN" in n or "SECRET" in n for n in names), names
    assert {"PATH", "HOME", "TMPDIR", "PYTHONDONTWRITEBYTECODE"} <= names


@darwin_only
def test_python_runs_with_stdlib_and_pytest(seatbelt):
    (seatbelt.workspace / "job.py").write_text(
        "import csv, json, sqlite3, statistics\n"
        "with open('r.csv', 'w', newline='') as f:\n"
        "    csv.writer(f).writerows([['a', 'b'], [1, 2]])\n"
        "print(json.dumps({'mean': statistics.mean([1, 2, 3])}))\n"
    )
    res = seatbelt.run("python job.py && python3 -c 'import sys; print(sys.prefix)' && python -m pytest --version")
    assert res.exit_code == 0, res.stderr
    assert '{"mean": 2}' in res.stdout
    assert (seatbelt.workspace / "r.csv").read_text().startswith("a,b")
    assert "pytest" in res.stdout + res.stderr
    # The venv's interpreter is first on PATH.
    assert os.path.dirname(sys.executable) + ":" in seatbelt._env()["PATH"]


@darwin_only
def test_timeout_kills_process_group(seatbelt):
    # The grandchild would write the marker after 3s if it survived the kill.
    res = seatbelt.run("(sleep 3; echo late > marker.txt) & sleep 30", timeout_sec=1)
    assert res.timed_out
    assert res.duration_sec < 10
    import time

    time.sleep(3.5)
    assert not (seatbelt.workspace / "marker.txt").exists()


@darwin_only
def test_output_truncated(seatbelt):
    res = seatbelt.run("python -c \"print('A' * 100000)\"")
    assert res.exit_code == 0
    assert len(res.stdout) <= sbx.MAX_OUTPUT_CHARS + 100
    assert "truncated" in res.stdout


@darwin_only
def test_workspace_with_spaces_and_quotes(tmp_path):
    ws = tmp_path / 'my "odd" ws'
    ws.mkdir()
    box = SeatbeltSandbox(ws, allow_network=False)
    try:
        res = box.run("echo ok > f.txt && cat f.txt && pwd")
        assert res.exit_code == 0, res.stderr
        assert res.stdout.splitlines()[0] == "ok"
        assert (ws / "f.txt").exists()
        blocked = box.run(f"echo x > '{tmp_path}/sibling.txt'")
        assert blocked.exit_code != 0
        (ws / ".env").write_text("SECRET=1")
        assert "SECRET" not in box.run("cat .env").stdout
    finally:
        box.close()


@darwin_only
def test_close_removes_private_tmpdir(tmp_path):
    box = SeatbeltSandbox(tmp_path, allow_network=False)
    assert box.tmpdir.exists()
    box.close()
    assert not box.tmpdir.exists()
    box.close()  # idempotent


@darwin_only
def test_unclosed_sandbox_tmpdir_goes_when_collected(tmp_path):
    # build_coding_registry makes a sandbox per call; a caller that never
    # closes the registry must not leak temp dirs.
    import gc
    box = SeatbeltSandbox(tmp_path, allow_network=False)
    tmpdir = box.tmpdir
    del box
    gc.collect()
    assert not tmpdir.exists()


# ── Docker, mocked ───────────────────────────────────────────────────────────


def _completed(argv, rc=0, out=b"", err=b""):
    return subprocess.CompletedProcess(argv, rc, out, err)


@pytest.fixture
def fake_run():
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        if argv[:2] == ["docker", "run"]:
            return _completed(argv, out="cid123\n", err="")
        if argv[:2] == ["docker", "exec"]:
            return _completed(argv, out=b"hi\n")
        return _completed(argv)

    with mock.patch.object(sbx.subprocess, "run", side_effect=run):
        yield calls


def test_docker_start_command_line(tmp_path, fake_run, monkeypatch):
    monkeypatch.delenv("AWOS_SANDBOX_NETWORK", raising=False)
    monkeypatch.delenv("AWOS_SANDBOX_IMAGE", raising=False)
    box = DockerSandbox(tmp_path)
    argv = fake_run[0][0]
    assert argv[:5] == ["docker", "run", "-d", "--rm", "--name"]
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--memory") + 1] == "2g"
    assert argv[argv.index("--cpus") + 1] == "2"
    assert argv[argv.index("--pids-limit") + 1] == "256"
    assert argv[argv.index("--mount") + 1] == f"type=bind,source={tmp_path.resolve()},target=/workspace"
    assert argv[argv.index("-w") + 1] == "/workspace"
    assert argv[-3:] == ["python:3.12-slim", "sleep", "infinity"]
    assert "-e" not in argv and "--env" not in argv
    assert box.container_id == "cid123"


def test_docker_mounts_git_readonly_and_hides_dotenv(tmp_path, fake_run):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".env.local").write_text("SECRET=2")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / ".env").write_text("skipped")
    DockerSandbox(tmp_path)
    argv = fake_run[0][0]
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    ws = tmp_path.resolve()
    assert f"type=bind,source={ws}/.git,target=/workspace/.git,readonly" in mounts
    assert "type=bind,source=/dev/null,target=/workspace/.env,readonly" in mounts
    assert "type=bind,source=/dev/null,target=/workspace/sub/.env.local,readonly" in mounts
    assert not any("node_modules" in m for m in mounts)


@pytest.mark.parametrize("name", ["a,readonly=false", "x,source=/"])
def test_docker_refuses_mount_injection_in_path(tmp_path, fake_run, name):
    ws = tmp_path / name
    ws.mkdir()
    with pytest.raises(RuntimeError, match="mounted safely"):
        DockerSandbox(ws)
    assert fake_run == []  # docker never ran


def test_docker_network_flag_and_image_env(tmp_path, fake_run, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX_NETWORK", "1")
    monkeypatch.setenv("AWOS_SANDBOX_IMAGE", "python:3.13")
    DockerSandbox(tmp_path)
    argv = fake_run[0][0]
    assert "--network" not in argv
    assert "python:3.13" in argv


def test_docker_run_uses_exec_without_host_env(tmp_path, fake_run, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-FAKE")
    box = DockerSandbox(tmp_path)
    seen = {}

    def capped(argv, timeout_sec, kill=None, cwd=None, env=None):
        seen.update(argv=argv, timeout=timeout_sec, kill=kill, env=env)
        return SandboxResult(0, "hi\n", "", False, 0.1)

    monkeypatch.setattr(sbx, "_run_capped", capped)
    res = box.run("python -V", timeout_sec=7)
    assert seen["argv"] == ["docker", "exec", "-w", "/workspace", "cid123", "/bin/sh", "-c", "python -V"]
    assert seen["timeout"] == 7
    assert "OPENROUTER_API_KEY" not in seen["env"]
    assert seen["kill"] == box._kill_exec
    assert res.stdout == "hi\n"


def test_docker_timeout_kill_stops_the_container_tree(tmp_path, fake_run, monkeypatch):
    box = DockerSandbox(tmp_path)
    killed = []
    monkeypatch.setattr(sbx, "_kill_tree", lambda proc: killed.append(proc))
    box._kill_exec("cli-proc")
    assert fake_run[-1][0] == ["docker", "exec", "cid123", "/bin/sh", "-c", "kill -9 -1"]
    assert killed == ["cli-proc"]


def test_docker_close_removes_container(tmp_path, fake_run):
    box = DockerSandbox(tmp_path)
    box.close()
    assert fake_run[-1][0] == ["docker", "rm", "-f", "cid123"]
    n = len(fake_run)
    box.close()  # idempotent
    assert len(fake_run) == n


def test_docker_start_failure_raises(tmp_path):
    with mock.patch.object(sbx.subprocess, "run", return_value=_completed([], rc=125, out="", err="no daemon")):
        with pytest.raises(RuntimeError):
            DockerSandbox(tmp_path)


# ── make_sandbox selection ───────────────────────────────────────────────────


class _Fake:
    def __init__(self, backend):
        self.backend = backend

    def __call__(self, workspace):
        box = mock.Mock()
        box.backend = self.backend
        return box


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.delenv("AWOS_SANDBOX", raising=False)
    fake_docker, fake_seatbelt = _Fake("docker"), _Fake("seatbelt")
    fake_docker.backend = "docker"
    monkeypatch.setattr(sbx, "DockerSandbox", fake_docker)
    monkeypatch.setattr(sbx, "SeatbeltSandbox", fake_seatbelt)
    return monkeypatch


@pytest.mark.parametrize(
    "setting,docker_ok,seatbelt_ok,expected",
    [
        ("auto", True, True, "docker"),
        ("auto", False, True, "seatbelt"),
        ("auto", False, False, None),
        ("seatbelt", True, True, "seatbelt"),
        ("seatbelt", True, False, None),
        ("docker", False, True, None),
        ("docker", True, False, "docker"),
        ("none", True, True, None),
        ("bogus", True, True, None),
    ],
)
def test_make_sandbox_selection(fakes, tmp_path, setting, docker_ok, seatbelt_ok, expected):
    fakes.setattr(sbx, "docker_available", lambda: docker_ok)
    fakes.setattr(sbx, "seatbelt_available", lambda: seatbelt_ok)
    fakes.setenv("AWOS_SANDBOX", setting)
    box = make_sandbox(tmp_path)
    assert (box.backend if box else None) == expected


def test_make_sandbox_argument_overrides_env(fakes, tmp_path):
    fakes.setattr(sbx, "docker_available", lambda: True)
    fakes.setattr(sbx, "seatbelt_available", lambda: True)
    fakes.setenv("AWOS_SANDBOX", "none")
    assert make_sandbox(tmp_path, backend="seatbelt").backend == "seatbelt"


def test_make_sandbox_default_is_auto(fakes, tmp_path):
    fakes.setattr(sbx, "docker_available", lambda: False)
    fakes.setattr(sbx, "seatbelt_available", lambda: True)
    assert make_sandbox(tmp_path).backend == "seatbelt"


def test_auto_falls_back_when_docker_cannot_start(fakes, tmp_path):
    def broken(workspace):
        raise RuntimeError("image pull failed")

    broken.backend = "docker"
    fakes.setattr(sbx, "DockerSandbox", broken)
    fakes.setattr(sbx, "docker_available", lambda: True)
    fakes.setattr(sbx, "seatbelt_available", lambda: True)
    assert make_sandbox(tmp_path).backend == "seatbelt"


def test_docker_probe_absent_cli(monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: None)
    with mock.patch.object(sbx.subprocess, "run") as run:
        assert sbx.docker_available() is False
        run.assert_not_called()


def test_docker_probe_hung_daemon_times_out(monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: "/usr/local/bin/docker")

    def hang(argv, **kw):
        assert kw["timeout"] <= 5
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    with mock.patch.object(sbx.subprocess, "run", side_effect=hang):
        assert sbx.docker_available() is False


def test_docker_probe_daemon_down(monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: "/usr/local/bin/docker")
    with mock.patch.object(sbx.subprocess, "run", return_value=_completed([], rc=1)):
        assert sbx.docker_available() is False
    with mock.patch.object(sbx.subprocess, "run", return_value=_completed([], rc=0, out=b"27.0")):
        assert sbx.docker_available() is True
