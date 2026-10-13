"""onboarding.ensure_ready and `awos doctor`: mocked input/getpass/key checks, temp repos."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from scaffold.agent import doctor, key_health, onboarding, paths

FAKE_KEY = "sk-ant-test-0000-not-a-real-key"
ENV_VARS = ("ANTHROPIC_API_KEY", "AWOS_PROVIDER", "AWOS_LOCAL_BASE_URL", "AWOS_BASE_URL",
            "AWOS_LOCAL_MODEL", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
            "OPENCODE_GO_API_KEY", "AWOS_LOCAL_API_KEY", "LOCAL_API_KEY", "ANTHROPIC_BASE_URL",
            *paths.MODEL_ENV_VARS)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated AWOS_HOME / host dir, no provider env, no repo .env."""
    awos_home = tmp_path / "awoshome"
    monkeypatch.setenv("AWOS_HOME", str(awos_home))
    monkeypatch.setenv("AWOS_HOST_DIR", str(awos_home / "host"))
    for v in ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(paths, "dotenv_path", lambda: awos_home / ".env")
    return awos_home


def no_server(url, headers, timeout):
    return None, "URLError"


def make_repo(root: Path, files: dict[str, str] | None = None, branch: str = "main") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", branch, str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return root


class Asker:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return self.answers.pop(0)


def kc(status, detail="HTTP 200"):
    return lambda env: key_health.KeyCheck("anthropic", status, "ANTHROPIC_API_KEY", detail)


# ── test-command / branch detection ─────────────────────────────────────────


@pytest.mark.parametrize("files,why", [
    ({"pytest.ini": "[pytest]\n"}, "pytest.ini"),
    ({"pyproject.toml": "[tool.pytest.ini_options]\naddopts='-q'\n"}, "pyproject"),
    ({"setup.cfg": "[tool:pytest]\n"}, "setup.cfg"),
    ({"tox.ini": "[pytest]\n"}, "tox.ini"),
    ({"tests/test_a.py": "def test_a(): pass\n"}, "tests/"),
    ({"pkg/sub/test_thing.py": "def test_a(): pass\n"}, "test_*.py"),
    ({"conftest.py": ""}, "conftest.py"),
])
def test_detect_pytest(tmp_path, files, why):
    repo = make_repo(tmp_path / "r", files)
    cmd, notes = onboarding.detect_test_command(repo)
    assert cmd == "pytest"
    assert any(why in n for n in notes), notes


def test_detect_none_and_unsupported(tmp_path):
    repo = make_repo(tmp_path / "r", {
        "package.json": json.dumps({"scripts": {"test": "jest"}}),
        "go.mod": "module x\n", "Cargo.toml": "[package]\n",
        "pyproject.toml": "[project]\nname='x'\n",
        "node_modules/dep/test_x.py": "",  # skipped dir
    })
    cmd, notes = onboarding.detect_test_command(repo)
    assert cmd is None
    joined = " ".join(notes)
    for tool in ("npm test", "go test", "cargo test"):
        assert f"{tool} found: not supported yet in v0.1" in joined


def test_default_branch(tmp_path):
    assert onboarding.default_branch(make_repo(tmp_path / "a", branch="master")) == "master"
    repo = make_repo(tmp_path / "b", branch="trunk")
    assert onboarding.default_branch(repo) == "trunk"
    clone = tmp_path / "c"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    assert onboarding.default_branch(clone) == "trunk"


# ── non-interactive ─────────────────────────────────────────────────────────


def test_non_interactive_reports_missing_without_prompting(home, tmp_path):
    def boom(*a, **k):
        raise AssertionError("must not prompt")
    info = onboarding.ensure_ready(tmp_path, interactive=False, ask=boom, secret=boom)
    assert set(info) >= {"provider", "model", "test_command", "missing", "notes"}
    assert info["provider"] is None
    assert info["missing"] == ["provider", "git_repo", "test_command"]


def test_non_interactive_ready(home, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    repo = make_repo(tmp_path / "r", {"tests/test_a.py": "def test_a(): pass\n"})
    info = onboarding.ensure_ready(repo, interactive=False)
    assert info["provider"] == "anthropic"
    assert info["model"] == "claude-haiku-5-5"
    assert info["test_command"] == "pytest"
    assert info["default_branch"] == "main"
    assert info["missing"] == []
    assert FAKE_KEY not in json.dumps(info)


def test_key_in_user_env_file_counts(home, tmp_path):
    home.mkdir(parents=True)
    (home / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    info = onboarding.ensure_ready(tmp_path, interactive=False)
    assert info["provider"] == "anthropic"


def test_local_provider_counts(home, tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    assert onboarding.ensure_ready(tmp_path, interactive=False)["provider"] == "local"


# ── interactive ─────────────────────────────────────────────────────────────


def test_interactive_anthropic_key_saved_0600_never_echoed(home, tmp_path, capsys):
    out = []
    ask, secret = Asker(["1"]), Asker([FAKE_KEY])
    seen = []

    def check(env):
        seen.append(env["ANTHROPIC_API_KEY"])
        return kc(key_health.OK)(env)

    info = onboarding.ensure_ready(tmp_path, interactive=True, ask=ask, secret=secret,
                                   say=out.append, key_check=check)
    assert seen == [FAKE_KEY]
    assert info["provider"] == "anthropic" and "provider" not in info["missing"]
    env_file = home / ".env"
    assert f"ANTHROPIC_API_KEY={FAKE_KEY}" in env_file.read_text()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    printed = "\n".join(out) + capsys.readouterr().out
    assert FAKE_KEY not in printed
    assert "Claude Haiku 5.5" in printed and "local model" in printed.lower()


def test_interactive_rejected_then_accepted(home, tmp_path):
    results = iter([key_health.UNAUTHORIZED, key_health.OK])
    out = []
    info = onboarding.ensure_ready(
        tmp_path, interactive=True, ask=Asker(["1"]), secret=Asker(["sk-ant-bad", FAKE_KEY]),
        say=out.append, key_check=lambda env: kc(next(results), "HTTP 401")(env))
    assert info["provider"] == "anthropic"
    assert "rejected" in "\n".join(out)
    text = (home / ".env").read_text()
    assert "sk-ant-bad" not in text and FAKE_KEY in text


def test_interactive_all_rejected_saves_nothing(home, tmp_path):
    info = onboarding.ensure_ready(
        tmp_path, interactive=True, ask=Asker(["1"]), secret=Asker(["a", "b", "c"]),
        say=lambda s: None, key_check=kc(key_health.UNAUTHORIZED, "HTTP 401"))
    assert info["provider"] is None and "provider" in info["missing"]
    assert not (home / ".env").exists()


def test_interactive_unreachable_asks_before_saving(home, tmp_path):
    info = onboarding.ensure_ready(
        tmp_path, interactive=True, ask=Asker(["1", "n"]), secret=Asker([FAKE_KEY]),
        say=lambda s: None, key_check=kc(key_health.UNREACHABLE, "no answer"))
    assert info["provider"] is None
    assert not (home / ".env").exists()


def test_interactive_skip(home, tmp_path):
    info = onboarding.ensure_ready(tmp_path, interactive=True, ask=Asker(["s"]),
                                   secret=Asker([]), say=lambda s: None)
    assert "provider" in info["missing"]


def test_interactive_local_found(home, tmp_path):
    def get(url, headers, timeout):
        return (200, "{}") if ":11434" in url else (None, "URLError")
    info = onboarding.ensure_ready(tmp_path, interactive=True, ask=Asker(["2"]),
                                   secret=Asker([]), say=lambda s: None, get=get)
    assert info["provider"] == "local"
    text = (home / ".env").read_text()
    assert "AWOS_PROVIDER=local" in text
    assert "AWOS_LOCAL_BASE_URL=http://127.0.0.1:11434/v1" in text


def test_interactive_local_not_running_declined(home, tmp_path):
    info = onboarding.ensure_ready(tmp_path, interactive=True, ask=Asker(["2", ""]),
                                   secret=Asker([]), say=lambda s: None, get=no_server)
    assert info["provider"] is None


def test_write_env_var_replaces_existing(home):
    p = home / ".env"
    onboarding.write_env_var("FOO", "1", p)
    onboarding.write_env_var("BAR", "2", p)
    onboarding.write_env_var("FOO", "3", p)
    assert p.read_text() == "BAR=2\nFOO=3\n"
    with pytest.raises(ValueError):
        onboarding.write_env_var("FOO", "a\nb", p)


# ── doctor ──────────────────────────────────────────────────────────────────


def test_doctor_ready(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    repo = make_repo(tmp_path / "r", {"tests/test_a.py": "def test_a(): pass\n"})

    def get(url, headers, timeout):
        return (200, "{}") if "anthropic" in url else (None, "URLError")

    lines = []
    code = doctor.main(["--repo", str(repo)], out=lines.append, get=get)
    text = "\n".join(lines)
    assert code == 0, text
    assert "✓ Python" in text and "✓ git" in text
    assert "✓ Model provider" in text and "anthropic: key accepted" in text
    assert "✓ Test command" in text and "pytest" in text
    assert "✓ Host dir" in text
    assert "✗ Worker" in text and "fix: start it: awos serve --worker" in text
    assert "Ready:" in text
    assert FAKE_KEY not in text + capsys.readouterr().out


def test_doctor_no_provider_fails_with_fix(home, tmp_path):
    lines = []
    code = doctor.main(["--repo", str(tmp_path)], out=lines.append, get=no_server)
    text = "\n".join(lines)
    assert code == 1
    assert "✗ Model provider" in text and "fix: run `awos` to set one up" in text
    assert "✗ Current repo" in text and "(optional)" in text
    assert "Not ready: 1 required" in text


def test_doctor_bad_key(home, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    def get(url, headers, timeout):
        return (401, "invalid x-api-key") if "anthropic" in url else (None, "URLError")

    lines = []
    assert doctor.main(["--json", "--repo", str(tmp_path)], out=lines.append, get=get) == 1
    data = json.loads(lines[0])
    prov = next(c for c in data["checks"] if c["name"] == "Model provider")
    assert not prov["ok"] and "rejected" in prov["detail"]
    assert FAKE_KEY not in lines[0]


def test_doctor_local_provider_requires_server(home, tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_LOCAL_BASE_URL", "http://127.0.0.1:9/v1")
    lines = []
    assert doctor.main(["--repo", str(tmp_path)], out=lines.append, get=no_server) == 1
    assert "✗ Local model server" in "\n".join(lines)


def test_doctor_detects_running_worker(home):
    import fcntl
    root = home / "host"
    root.mkdir(parents=True)
    with open(root / "worker.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write("4242")
        fh.flush()
        # flock is per open file description: a second open in this process conflicts
        assert doctor.worker_pid(root) == 4242
        assert doctor.check_worker(root).ok
    assert doctor.worker_pid(root) is None


def test_doctor_host_dir_unwritable(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        c = doctor.check_host_dir(ro / "host")
        assert not c.ok and c.fix
    finally:
        ro.chmod(0o700)
