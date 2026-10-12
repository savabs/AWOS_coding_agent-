"""
Packaging: pyproject.toml metadata, code-vs-state paths (scaffold/agent/paths.py)
and fresh-user defaults, so `pip install` / `pipx install` gives a working `awos`
whose state never lands in site-packages. See docs/INSTALL.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scaffold.agent import paths  # noqa: E402


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


# ── pyproject.toml ────────────────────────────────────────────────────────────


def test_console_script_points_at_awos_main():
    meta = _pyproject()
    assert meta["project"]["scripts"]["awos"] == "awos:main"
    import awos
    assert callable(awos.main)


def test_python_floor_and_package_layout():
    meta = _pyproject()
    assert meta["project"]["requires-python"] == ">=3.11"
    setuptools = meta["tool"]["setuptools"]
    assert setuptools["py-modules"] == ["awos"]
    include = setuptools["packages"]["find"]["include"]
    assert "scaffold.agent" in include and "scaffold.agent.*" in include
    assert not any("tests" in pat for pat in include)


def test_core_dependencies_are_trimmed():
    deps = " ".join(_pyproject()["project"]["dependencies"]).lower()
    for needed in ("anthropic", "openai", "python-dotenv", "numpy"):
        assert needed in deps
    for heavy in ("aider", "chromadb", "sentence-transformers", "fastembed", "tree-sitter",
                  "google-genai", "huggingface"):
        assert heavy not in deps, f"{heavy} belongs in an extra, not the core install"


def test_extras_cover_the_heavy_requirements():
    extras = _pyproject()["project"]["optional-dependencies"]
    flat = " ".join(" ".join(v) for v in extras.values()).lower()
    for heavy in ("chromadb", "tree-sitter", "google-genai", "duckduckgo-search"):
        assert heavy in flat


# ── paths: checkout vs installed ─────────────────────────────────────────────


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("AWOS_HOME", "AWOS_STATE_DIR", "AWOS_PROVIDER", "ANTHROPIC_API_KEY",
              "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENCODE_GO_API_KEY",
              *paths.MODEL_ENV_VARS):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_checkout_keeps_repo_state_layout(clean_env):
    assert not paths.is_installed()
    assert paths.CODE_ROOT == REPO
    assert paths.state_root() == REPO
    assert paths.dotenv_path() == REPO / ".env"


def test_installed_state_goes_to_awos_home(clean_env, tmp_path):
    clean_env.setattr(paths, "CODE_ROOT", tmp_path / "lib" / "site-packages")
    clean_env.setenv("AWOS_HOME", str(tmp_path / "home"))
    assert paths.is_installed()
    root = paths.state_root()
    assert root == tmp_path / "home" / "runtime" and root.is_dir()
    assert "site-packages" not in str(root)
    assert paths.dotenv_path() == tmp_path / "home" / ".env"


def test_state_dir_override(clean_env, tmp_path):
    clean_env.setenv("AWOS_STATE_DIR", str(tmp_path / "state"))
    assert paths.state_root() == tmp_path / "state"


def test_host_modules_use_the_state_root():
    from scaffold.agent import job_host
    from scaffold.agent.host import worker
    assert worker.LEDGER_PATH == worker.REPO / ".awos" / "budget.json"
    assert job_host.CODE_ROOT == paths.CODE_ROOT
    assert (Path(job_host.CODE_ROOT) / "scaffold" / "agent" / "job_host.py").exists()


# ── fresh-user model defaults ────────────────────────────────────────────────


def test_anthropic_key_alone_defaults_to_haiku():
    env = {"ANTHROPIC_API_KEY": "x"}
    assert paths.apply_model_defaults(env) is True
    for k in paths.MODEL_ENV_VARS:
        assert env[k] == "claude-haiku-5-5"


@pytest.mark.parametrize("extra", [
    {"AWOS_AGENT_MODEL": "deepseek-chat"},
    {"AWOS_LADDER": "claude-sonnet-4-6"},
    {"AWOS_PROVIDER": "local"},
])
def test_explicit_choice_is_never_overridden(extra):
    env = {"ANTHROPIC_API_KEY": "x", **extra}
    before = dict(env)
    assert paths.apply_model_defaults(env) is False
    assert env == before


def test_no_key_no_defaults():
    env: dict = {}
    assert paths.apply_model_defaults(env) is False and env == {}


def test_provider_configured_and_message():
    assert not paths.provider_configured({})
    assert not paths.provider_configured({"ANTHROPIC_API_KEY": ""})
    assert paths.provider_configured({"ANTHROPIC_API_KEY": "x"})
    assert paths.provider_configured({"AWOS_PROVIDER": "local"})
    msg = paths.no_provider_message()
    assert "ANTHROPIC_API_KEY" in msg and "AWOS_PROVIDER=local" in msg


# ── CLI without any credentials ──────────────────────────────────────────────


def _bare_env(tmp_path) -> dict:
    keep = {k: v for k, v in os.environ.items()
            if k in ("PATH", "HOME", "SYSTEMROOT", "TMPDIR", "LANG")}
    return {**keep, "AWOS_HOME": str(tmp_path / "home"),
            "AWOS_HOST_DIR": str(tmp_path / "home" / "host"),
            # a checkout loads <repo>/.env; point it at nothing
            "ANTHROPIC_API_KEY": "", "OPENROUTER_API_KEY": "", "DEEPSEEK_API_KEY": "",
            "OPENAI_API_KEY": "", "OPENCODE_GO_API_KEY": ""}


@pytest.mark.timeout(60)
def test_help_works_without_keys(tmp_path):
    r = subprocess.run([sys.executable, str(REPO / "awos.py"), "--help"], cwd=str(tmp_path),
                       env=_bare_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Quickstart" in r.stdout and "ANTHROPIC_API_KEY" in r.stdout


@pytest.mark.timeout(60)
def test_worker_without_keys_explains_what_to_set(tmp_path):
    r = subprocess.run([sys.executable, str(REPO / "awos.py"), "serve", "--worker", "--once"],
                       cwd=str(tmp_path), env=_bare_env(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "ANTHROPIC_API_KEY" in r.stderr and "AWOS_PROVIDER=local" in r.stderr


# ── the built wheel (needs network for build isolation) ──────────────────────


@pytest.mark.integration
@pytest.mark.timeout(300)
def test_wheel_contents(tmp_path):
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q",
                        "-w", str(tmp_path), str(REPO)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (whl,) = tmp_path.glob("awos-*.whl")
    names = zipfile.ZipFile(whl).namelist()
    for must in ("awos.py", "scaffold/agent/paths.py", "scaffold/agent/job_host.py",
                 "scaffold/agent/host/worker.py", "scaffold/agent/key_health.py"):
        assert must in names
    assert not any("/.awos/" in n or n.startswith("tests/") or n.startswith("site/")
                   for n in names)
    entry = next(n for n in names if n.endswith("entry_points.txt"))
    assert "awos = awos:main" in zipfile.ZipFile(whl).read(entry).decode()
