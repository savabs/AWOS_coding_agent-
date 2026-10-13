"""site/src/install.sh: dry-run in a temp HOME with a fake PATH (no network, no installs)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "site" / "src" / "install.sh"
SYSTEM_TOOLS = ("uname", "id", "basename", "env", "cat")


def _stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def _fake_env(tmp_path: Path, tools: dict[str, str], extra: dict | None = None) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for t in SYSTEM_TOOLS:
        real = shutil.which(t)
        assert real, t
        (bindir / t).symlink_to(real)
    for name, body in tools.items():
        _stub(bindir, name, body)
    env = {"HOME": str(home), "PATH": str(bindir), "SHELL": "/bin/zsh",
           "AWOS_INSTALL_DRY_RUN": "1"}
    env.update(extra or {})
    return env


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/sh", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=30)


GIT = "exit 0"
UV = 'if [ "$1 $2" = "tool dir" ]; then echo "$HOME/.local/bin"; else echo "REAL UV CALLED $*"; exit 9; fi'
PY311 = "exit 0"   # stub: `python3 -c 'version check'` succeeds
PY39 = "exit 1"    # stub: version check fails
PIPX = ('if [ "$1" = environment ]; then echo "$HOME/.local/bin"; '
        'else echo "REAL PIPX CALLED $*"; exit 9; fi')


def test_script_is_posix_sh_and_wrapped_in_main():
    text = SCRIPT.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert text.rstrip().endswith('main "$@"')
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if re.search(r"(^|[;&|(]|\brun)\s*sudo\b", ln)]
    assert "v0.1.0" in text and "AWOS_REF" in text


def test_syntax_ok():
    assert subprocess.run(["/bin/sh", "-n", str(SCRIPT)]).returncode == 0


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_dry_run_with_uv(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT, "uv": UV})
    r = _run(env)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "+ uv tool install --force --python >=3.11 awos @ git+https://github.com/savabs/AWOS_coding_agent-@v0.1.0" in out
    assert "REAL UV CALLED" not in out
    assert out.rstrip().endswith("Run: awos")
    # bin dir not on PATH: print the line, never edit rc files
    assert 'export PATH="' in out and ".local/bin" in out
    assert not list((tmp_path / "home").iterdir()), "dry run must not touch HOME"


def test_ref_override(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT, "uv": UV}, {"AWOS_REF": "main"})
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "@main" in r.stdout and "@v0.1.0" not in r.stdout


def test_dry_run_with_pipx(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT, "pipx": PIPX, "python3": PY311})
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "+ pipx install --force --python " in r.stdout
    assert "awos @ git+https://github.com/savabs/AWOS_coding_agent-@v0.1.0" in r.stdout
    assert "REAL PIPX CALLED" not in r.stdout
    assert r.stdout.rstrip().endswith("Run: awos")


def test_pipx_with_old_python_falls_back_to_uv_bootstrap(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT, "pipx": PIPX, "python3": PY39, "curl": "exit 9"})
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "no Python >= 3.11" in r.stdout
    assert "About to install uv" in r.stderr
    assert "UV_NO_MODIFY_PATH=1" in r.stderr
    assert "tool install --force --python >=3.11" in r.stdout


def test_no_uv_no_pipx_explains_before_installing_uv(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT, "curl": "exit 9"})
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "Neither uv nor pipx found." in r.stdout
    assert "+ curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh" in r.stderr
    assert r.stdout.rstrip().endswith("Run: awos")


def test_no_uv_install_flag_fails_clearly(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT}, {"AWOS_NO_UV_INSTALL": "1"})
    r = _run(env)
    assert r.returncode != 0
    assert "AWOS_NO_UV_INSTALL=1" in r.stderr


def test_missing_git_fails_clearly(tmp_path):
    env = _fake_env(tmp_path, {"uv": UV})
    r = _run(env)
    assert r.returncode == 1
    assert "git is required" in r.stderr


def test_path_already_set_prints_no_hint(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT, "uv": UV})
    env["PATH"] = env["PATH"] + ":" + env["HOME"] + "/.local/bin"
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "not on your PATH" not in r.stdout


def test_site_build_copies_install_script():
    text = (ROOT / "site" / "build.py").read_text()
    assert 'SRC / "install.sh", OUT / "install.sh"' in text


@pytest.mark.skipif(not Path("/bin/dash").exists(), reason="dash not available")
def test_runs_under_dash(tmp_path):
    env = _fake_env(tmp_path, {"git": GIT, "uv": UV})
    r = subprocess.run(["/bin/dash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.rstrip().endswith("Run: awos")
