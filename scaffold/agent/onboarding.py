"""
onboarding.py — first-run setup: get a fresh user from `awos` to a runnable job.

    from scaffold.agent.onboarding import ensure_ready
    info = ensure_ready(Path.cwd(), interactive=True)
    # {"provider", "model", "test_command", "default_branch", "missing", "notes"}

What it checks:
  provider       ANTHROPIC_API_KEY (exported, or in ~/.awos/.env / the .env AWOS
                 loads), AWOS_PROVIDER=local, or another configured cloud key.
                 Interactive and missing: explains the two options, reads a key
                 with getpass, validates it with the free key check
                 (key_health: GET /v1/models, spends nothing) and stores it in
                 ~/.awos/.env (dir 0700, file 0600). The key is never echoed,
                 printed, logged or returned.
  test command   pytest, detected from pytest.ini / conftest.py / pyproject
                 [tool.pytest...] / setup.cfg / tox.ini / a tests dir / test_*.py.
                 npm, go and cargo tests are reported as "not supported yet" (v0.1).
  default branch origin/HEAD, else main/master, else the current branch.

Non-interactive mode never prompts: it returns what it found plus `missing`.
"""

from __future__ import annotations

import configparser
import getpass as _getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

try:
    from scaffold.agent import key_health as _kh
    from scaffold.agent import paths as _paths
except ImportError:  # pragma: no cover - flat import when run from scaffold/agent
    import key_health as _kh  # type: ignore
    import paths as _paths  # type: ignore

try:
    import tomllib as _toml
except ImportError:  # pragma: no cover - Python < 3.11
    _toml = None  # type: ignore

#: Cloud keys that also count as "a provider is configured".
OTHER_CLOUD_KEYS = (("openrouter", "OPENROUTER_API_KEY"), ("deepseek", "DEEPSEEK_API_KEY"),
                    ("openai", "OPENAI_API_KEY"), ("opencode", "OPENCODE_GO_API_KEY"))

LOCAL_SERVERS = (("llama-server", "http://127.0.0.1:8080/v1"),
                 ("Ollama", "http://127.0.0.1:11434/v1"))

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", ".tox",
             "__pycache__", "build", "dist", ".mypy_cache", ".pytest_cache", "site-packages"}

MAX_KEY_ATTEMPTS = 3


# ── where keys live ──────────────────────────────────────────────────────────


def user_env_path() -> Path:
    """~/.awos/.env (AWOS_HOME moves it): where onboarding stores what it learns."""
    return _paths.awos_home() / ".env"


def _env_files() -> list[Path]:
    files = [user_env_path()]
    other = _paths.dotenv_path()
    if other != files[0]:
        files.append(other)
    return files


def known_env(env: Optional[dict] = None) -> dict:
    """Exported vars win; ~/.awos/.env and the loaded .env fill absent names."""
    out = dict(os.environ if env is None else env)
    for path in _env_files():
        for k, v in _kh._dotenv_values(path).items():
            out.setdefault(k, v)
    return out


def write_env_var(name: str, value: str, path: Optional[Path] = None) -> Path:
    """Set NAME=value in the env file (replacing an old line), mode 0600. Never prints."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"bad variable name: {name!r}")
    if any(c in value for c in "\r\n"):
        raise ValueError("value must be a single line")
    path = path or user_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    pat = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")
    kept = [ln for ln in lines if not pat.match(ln)]
    kept.append(f"{name}={value}")
    data = ("\n".join(kept) + "\n").encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


# ── provider ─────────────────────────────────────────────────────────────────


def detect_provider(env: Optional[dict] = None) -> tuple[Optional[str], Optional[str]]:
    """(provider, model) from the configuration alone; (None, None) when nothing is set."""
    e = known_env(env)
    if (e.get("AWOS_PROVIDER") or "").strip().lower() == "local" \
            or (e.get("AWOS_AGENT_MODEL") or "").strip().lower().startswith("local/"):
        return "local", (e.get("AWOS_LOCAL_MODEL") or e.get("AWOS_AGENT_MODEL") or "local").strip()
    if (e.get("ANTHROPIC_API_KEY") or "").strip():
        return "anthropic", (e.get("AWOS_AGENT_MODEL") or _paths.DEFAULT_ANTHROPIC_MODEL).strip()
    for name, var in OTHER_CLOUD_KEYS:
        if (e.get(var) or "").strip():
            return name, (e.get("AWOS_AGENT_MODEL") or "").strip() or None
    return None, None


def probe_local(base_url: str, get=None) -> bool:
    """True when anything answers HTTP at the server (llama-server /health, Ollama /v1/models)."""
    get = get or _kh.http_get
    root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
    for path in ("/health", "/v1/models"):
        status, _ = get(root + path, {}, 2.0)
        if status is not None:
            return True
    return False


def _setup_anthropic(ask, secret, say, key_check, env_path) -> Optional[str]:
    say("\nGet a key at https://console.anthropic.com/settings/keys (it is stored only "
        f"in {env_path}, readable by you alone).")
    for attempt in range(1, MAX_KEY_ATTEMPTS + 1):
        try:
            key = (secret("Paste your ANTHROPIC_API_KEY (input hidden): ") or "").strip()
        except (EOFError, KeyboardInterrupt):
            say("")
            return None
        if not key:
            say("No key entered.")
            return None
        if not key.startswith("sk-ant-"):
            say("Note: Anthropic keys usually start with 'sk-ant-'. Checking it anyway.")
        say("Checking the key (free request, spends nothing)...")
        check = key_check({"ANTHROPIC_API_KEY": key})
        if check.status == _kh.OK:
            write_env_var("ANTHROPIC_API_KEY", key, env_path)
            os.environ["ANTHROPIC_API_KEY"] = key
            _paths.apply_model_defaults()
            say(f"Key accepted and saved to {env_path} (mode 600).")
            return "anthropic"
        if check.status == _kh.UNAUTHORIZED:
            left = MAX_KEY_ATTEMPTS - attempt
            say(f"Anthropic rejected that key ({check.detail})."
                + (f" Try again ({left} left)." if left else ""))
            continue
        # unreachable: cannot tell; let the user decide
        say(f"Could not reach Anthropic to check the key ({check.detail}).")
        if _yes(ask("Save it anyway and check later with `awos doctor`? [y/N] "), False):
            write_env_var("ANTHROPIC_API_KEY", key, env_path)
            os.environ["ANTHROPIC_API_KEY"] = key
            _paths.apply_model_defaults()
            say(f"Saved to {env_path} (mode 600), not yet verified.")
            return "anthropic"
        return None
    return None


def _setup_local(ask, say, get, env_path) -> Optional[str]:
    found = None
    for label, url in LOCAL_SERVERS:
        if probe_local(url, get=get):
            found = (label, url)
            break
    if found:
        label, url = found
        say(f"\nFound a local model server ({label}) at {url}.")
    else:
        say("\nNo local model server is answering on 127.0.0.1:8080 (llama-server) "
            "or :11434 (Ollama).")
        say("Start one, for example:")
        say("  llama-server -m <model.gguf> --port 8080 --jinja")
        say("  ollama serve   (then: ollama pull qwen2.5-coder:7b)")
        if not _yes(ask("Use a local model anyway (you will start the server later)? [y/N] "),
                    False):
            return None
        label, url = LOCAL_SERVERS[0]
    write_env_var("AWOS_PROVIDER", "local", env_path)
    os.environ["AWOS_PROVIDER"] = "local"
    if url != LOCAL_SERVERS[0][1]:
        write_env_var("AWOS_LOCAL_BASE_URL", url, env_path)
        os.environ["AWOS_LOCAL_BASE_URL"] = url
    say(f"Saved AWOS_PROVIDER=local to {env_path}.")
    return "local"


def _yes(answer: Optional[str], default: bool) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return default
    return a in ("y", "yes")


def setup_provider(ask: Callable[[str], str] = input,
                   secret: Callable[[str], str] = _getpass.getpass,
                   say: Callable[[str], None] = print,
                   key_check: Optional[Callable[[dict], "_kh.KeyCheck"]] = None,
                   get=None, env_path: Optional[Path] = None) -> Optional[str]:
    """Interactive provider choice. Returns 'anthropic' / 'local', or None if skipped."""
    key_check = key_check or (lambda env: _kh.check_anthropic(env, get=get or _kh.http_get))
    env_path = env_path or user_env_path()
    say("AWOS needs a model to work with. Pick one:")
    say("  1) Claude Haiku 5.5 (Anthropic API) - about a cent or two per task; needs an API key")
    say("  2) A local model - free and private; needs llama-server or Ollama running")
    say("  s) Skip for now")
    say(f"  (setup guide: {_paths.docs_url()})")
    try:
        choice = (ask("Choose [1/2/s] (default 1): ") or "1").strip().lower()
    except (EOFError, KeyboardInterrupt):
        say("")
        return None
    if choice in ("1", "a", "anthropic", "claude"):
        return _setup_anthropic(ask, secret, say, key_check, env_path)
    if choice in ("2", "l", "local"):
        return _setup_local(ask, say, get, env_path)
    return None


# ── repo ─────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def is_git_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree") == "true"


def default_branch(repo: Path) -> Optional[str]:
    ref = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ref:
        return ref.split("/", 1)[1] if "/" in ref else ref
    for name in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}") is not None:
            return name
    cur = _git(repo, "symbolic-ref", "--short", "HEAD")
    return cur or None


def _walk_py_tests(repo: Path, max_depth: int = 3) -> bool:
    def walk(d: Path, depth: int) -> bool:
        try:
            entries = list(d.iterdir())
        except OSError:
            return False
        for p in entries:
            if p.is_file() and p.suffix == ".py" and (
                    p.name.startswith("test_") or p.stem.endswith("_test")):
                return True
        if depth >= max_depth:
            return False
        return any(walk(p, depth + 1) for p in entries
                   if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith("."))
    return walk(repo, 0)


def _has_pytest_config(repo: Path) -> Optional[str]:
    if (repo / "pytest.ini").is_file():
        return "pytest.ini"
    if (repo / "conftest.py").is_file():
        return "conftest.py"
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8")
            if _toml is not None:
                tool = _toml.loads(text).get("tool", {})
                if "pytest" in tool:
                    return "pyproject.toml [tool.pytest]"
            elif re.search(r"^\[tool\.pytest", text, re.M):
                return "pyproject.toml [tool.pytest]"
        except (OSError, ValueError):
            pass
    for name, section in (("setup.cfg", "tool:pytest"), ("tox.ini", "pytest")):
        f = repo / name
        if f.is_file():
            cp = configparser.ConfigParser()
            try:
                cp.read(f, encoding="utf-8")
            except (configparser.Error, UnicodeDecodeError):
                continue
            if cp.has_section(section):
                return f"{name} [{section}]"
    return None


def detect_test_command(repo: Path) -> tuple[Optional[str], list[str]]:
    """(test command or None, notes). v0.1 runs pytest only."""
    repo = Path(repo)
    notes: list[str] = []
    cmd = None
    why = _has_pytest_config(repo)
    if not why:
        for d in ("tests", "test"):
            if (repo / d).is_dir() and any((repo / d).rglob("*.py")):
                why = f"{d}/ directory"
                break
    if not why and _walk_py_tests(repo):
        why = "test_*.py files"
    if why:
        cmd = "pytest"
        notes.append(f"test command: pytest (found {why})")

    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}) or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        if "test" in scripts:
            notes.append("npm test found: not supported yet in v0.1 (pytest only)")
    if (repo / "go.mod").is_file():
        notes.append("go test found: not supported yet in v0.1 (pytest only)")
    if (repo / "Cargo.toml").is_file():
        notes.append("cargo test found: not supported yet in v0.1 (pytest only)")
    return cmd, notes


# ── entry point ──────────────────────────────────────────────────────────────


def ensure_ready(repo: Path, interactive: bool = True, *,
                 ask: Optional[Callable[[str], str]] = None,
                 secret: Optional[Callable[[str], str]] = None,
                 say: Optional[Callable[[str], None]] = None,
                 key_check: Optional[Callable[[dict], "_kh.KeyCheck"]] = None,
                 get=None) -> dict:
    """
    Make sure AWOS can run a job in `repo`. Returns
    {"provider", "model", "test_command", "default_branch", "missing", "notes"}.
    `missing` lists what still blocks a job: "provider", "git_repo", "test_command".
    Interactive mode may prompt for a provider; non-interactive never prompts.
    """
    repo = Path(repo).expanduser().resolve()
    say = say or print
    notes: list[str] = []
    missing: list[str] = []

    if interactive and ask is None and not sys.stdin.isatty():
        interactive = False
        notes.append("stdin is not a terminal; skipped interactive setup")

    provider, model = detect_provider()
    if provider is None and interactive:
        chosen = setup_provider(ask=ask or input, secret=secret or _getpass.getpass, say=say,
                                key_check=key_check, get=get)
        if chosen:
            provider, model = detect_provider()
    if provider is None:
        missing.append("provider")
        notes.append("no model provider: run `awos` in a terminal to set one up, "
                     "or put ANTHROPIC_API_KEY=... in " + str(user_env_path())
                     + " (or AWOS_PROVIDER=local); guide: " + _paths.docs_url())

    branch = None
    if is_git_repo(repo):
        branch = default_branch(repo)
        if branch:
            notes.append(f"default branch: {branch}")
    else:
        missing.append("git_repo")
        notes.append(f"{repo} is not a git repository (run `git init`, or cd into a repo)")

    test_cmd, test_notes = detect_test_command(repo)
    notes.extend(test_notes)
    if not test_cmd:
        missing.append("test_command")
        notes.append("no pytest tests found (AWOS v0.1 needs a pytest suite to verify fixes)")

    return {"provider": provider, "model": model, "test_command": test_cmd,
            "default_branch": branch, "missing": missing, "notes": notes}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(ensure_ready(Path.cwd(), interactive=sys.stdin.isatty()), indent=2))
