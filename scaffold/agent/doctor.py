"""
doctor.py — `awos doctor`: a friendly checklist of everything AWOS needs.

    awos doctor [--repo PATH] [--json]

Checks (nothing here spends money; key checks use free read-only endpoints):

  required  Python >= 3.11, git on PATH, a working model provider (key_health),
            host dir writable (AWOS_HOST_DIR, default ~/.awos/host)
  info      install location / `awos` on PATH, local model server reachable,
            current repo + its test command, host worker running

Prints one line per check with a check mark or a cross, and one "fix:" line
under each cross. Exit 0 when every required check passes, else 1. Keys are
never printed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    from scaffold.agent import key_health as _kh
    from scaffold.agent import onboarding as _ob
    from scaffold.agent import paths as _paths
except ImportError:  # pragma: no cover
    import key_health as _kh  # type: ignore
    import onboarding as _ob  # type: ignore
    import paths as _paths  # type: ignore

OK_MARK, BAD_MARK = "✓", "✗"   # ✓ ✗


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def host_dir() -> Path:
    raw = os.environ.get("AWOS_HOST_DIR", "").strip()
    return Path(raw).expanduser() if raw else _paths.awos_home() / "host"


# ── individual checks (each small and injectable for tests) ─────────────────


def check_python(version=None) -> Check:
    v = version or sys.version_info
    ok = tuple(v[:2]) >= (3, 11)
    return Check("Python", ok, f"{v[0]}.{v[1]}.{v[2]} ({sys.executable})",
                 "" if ok else "install Python 3.11+ (or re-run the installer: uv provides one)")


def check_install(which=shutil.which) -> Check:
    where = _paths.CODE_ROOT
    mode = "installed package" if _paths.is_installed() else "source checkout"
    exe = which("awos")
    detail = f"{mode} at {where}; `awos` " + (f"on PATH ({exe})" if exe else "not on PATH")
    return Check("Install", bool(exe), detail,
                 "" if exe else "add the install dir to PATH (the installer printed the line), "
                                f"or reinstall: curl -fsSL {_paths.install_url()} | sh",
                 required=False)


def check_git(which=shutil.which) -> Check:
    exe = which("git")
    return Check("git", bool(exe), exe or "not found",
                 "" if exe else "install git (macOS: xcode-select --install; Linux: your package manager)")


def check_provider(env: Optional[dict] = None, get=None) -> list[Check]:
    """Model credentials via key_health (free endpoints). One required verdict + detail rows."""
    get = get or _kh.http_get
    checks = _kh.check_all(env=_ob.known_env(env), get=get)
    by = {c.provider: c for c in checks}
    problem = _kh.credentials_problem(checks)
    usable = [c for c in checks if c.status == _kh.OK]
    out: list[Check] = []
    if problem is None and usable:
        names = ", ".join(c.provider for c in usable)
        out.append(Check("Model provider", True, f"{names}: key accepted (no spend)"))
    elif problem is None:
        # configured but nothing verified (e.g. offline): not a credentials block
        bad = [c for c in checks if c.status == _kh.UNREACHABLE]
        detail = "; ".join(f"{c.provider}: {c.detail}" for c in bad) or "not verified"
        out.append(Check("Model provider", True, f"configured, not verified ({detail})"))
    elif problem.get("status") == _kh.NOT_CONFIGURED:
        out.append(Check("Model provider", False, "no provider configured",
                         "run `awos` to set one up, or put ANTHROPIC_API_KEY=... in "
                         f"{_ob.user_env_path()} (or AWOS_PROVIDER=local)"))
    else:
        out.append(Check("Model provider", False, problem["summary"], problem.get("action", "")))

    local = by.get("local")
    if local is not None:
        e = _ob.known_env(env)
        wants_local = (e.get("AWOS_PROVIDER") or "").strip().lower() == "local"
        base, _ = _kh.local_base(e)
        ok = local.status == _kh.OK
        out.append(Check("Local model server", ok,
                         f"{base}: " + ("reachable" if ok else local.detail),
                         "" if ok else "start llama-server (or Ollama) and set AWOS_LOCAL_BASE_URL",
                         required=wants_local))
    return out


#: AWOS_EXECUTOR values a coding job can run with (orchestrator.executor_choice).
EXECUTORS = {"pipeline": "planner, one-shot edit, gates, capped repair",
             "agent_loop": "planner, one-shot edit, gates, capped repair",
             "loop": "open bash loop until the model submits, then the success gate",
             "react": "ReAct worker", "worker": "single-file worker"}


def check_executor(env: Optional[dict] = None) -> Check:
    """Which executor runs a coding job (AWOS_EXECUTOR); informational."""
    e = _ob.known_env(env)
    raw = (e.get("AWOS_EXECUTOR") or "").strip().lower()
    source = "AWOS_EXECUTOR"
    if not raw:
        try:
            from scaffold.agent.orchestrator import _sandbox_possible
        except ImportError:  # pragma: no cover - flat import
            from orchestrator import _sandbox_possible  # type: ignore
        raw = "loop" if _sandbox_possible() else "pipeline"
        source = "default" if raw == "loop" else "default: no sandbox available for loop"
    name = "pipeline" if raw == "agent_loop" else raw
    if raw not in EXECUTORS:
        return Check("Executor", False, f"AWOS_EXECUTOR={raw!r} is unknown",
                     "set AWOS_EXECUTOR to pipeline or loop (or unset it)", required=False)
    return Check("Executor", True, f"{name}: {EXECUTORS[raw]} ({source})", required=False)


def check_repo(repo: Path) -> list[Check]:
    repo = Path(repo).expanduser().resolve()
    if not _ob.is_git_repo(repo):
        return [Check("Current repo", False, f"{repo} is not a git repo",
                      "cd into the git repo you want AWOS to work on", required=False)]
    branch = _ob.default_branch(repo) or "?"
    out = [Check("Current repo", True, f"{repo} (default branch {branch})", required=False)]
    cmd, notes = _ob.detect_test_command(repo)
    unsupported = [n for n in notes if "not supported" in n]
    if cmd:
        out.append(Check("Test command", True, cmd, required=False))
        env = _ob.detect_test_env(repo)
        if env is not None:
            # v0.2.2: tests run in the project's env, not AWOS's Python.
            out.append(Check("Test environment", not env.is_fallback, env.describe(),
                             "" if not env.is_fallback else
                             "create the project's .venv (with pytest), or set AWOS_TEST_COMMAND",
                             required=False))
    else:
        out.append(Check("Test command", False,
                         "no pytest tests found" + (f" ({'; '.join(unsupported)})" if unsupported else ""),
                         "AWOS v0.1 verifies fixes with pytest: add a tests/ dir or pytest config",
                         required=False))
    return out


def check_host_dir(root: Optional[Path] = None) -> Check:
    root = root or host_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".doctor-", delete=True) as fh:
            fh.write(b"ok")
        return Check("Host dir", True, f"{root} is writable")
    except OSError as exc:
        return Check("Host dir", False, f"{root}: {exc.strerror or exc}",
                     f"make {root} writable, or set AWOS_HOST_DIR to a writable directory")


def worker_pid(root: Optional[Path] = None) -> Optional[int]:
    """The live worker's pid (it holds an flock on <root>/worker.lock), else None."""
    lock = (root or host_dir()) / "worker.lock"
    if not lock.exists():
        return None
    try:
        with open(lock, "r") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.seek(0)
                raw = fh.read().strip()
                return int(raw) if raw.isdigit() else -1
            fcntl.flock(fh, fcntl.LOCK_UN)
            return None
    except OSError:
        return None


def check_worker(root: Optional[Path] = None) -> Check:
    pid = worker_pid(root)
    if pid:
        return Check("Worker", True, "running" + (f" (pid {pid})" if pid > 0 else ""),
                     required=False)
    return Check("Worker", False, "not running (queued jobs wait until it starts)",
                 "start it: awos serve --worker", required=False)


# ── report ───────────────────────────────────────────────────────────────────


def run_checks(repo: Optional[Path] = None, env: Optional[dict] = None, get=None) -> list[Check]:
    repo = Path(repo) if repo else Path.cwd()
    checks = [check_python(), check_install(), check_git()]
    checks += check_provider(env=env, get=get)
    checks.append(check_executor(env=env))
    checks += check_repo(repo)
    checks.append(check_host_dir())
    checks.append(check_worker())
    return checks


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = ["AWOS doctor", ""]
    for c in checks:
        mark = OK_MARK if c.ok else BAD_MARK
        opt = "" if c.required or c.ok else " (optional)"
        lines.append(f"  {mark} {c.name:<{width}}  {c.detail}{opt}")
        if not c.ok and c.fix:
            lines.append(f"    {'':<{width}}  fix: {c.fix}")
    bad = [c for c in checks if c.required and not c.ok]
    lines.append("")
    lines.append("Ready: everything required is OK. Run: awos" if not bad
                 else f"Not ready: {len(bad)} required check(s) failed (see fix lines above).")
    if bad:
        lines.append(f"Help: {_paths.docs_url()}")
    return "\n".join(lines)


def main(argv: Optional[list] = None, out: Callable[[str], None] = print,
         get=None) -> int:
    ap = argparse.ArgumentParser(prog="awos doctor",
                                 description="Check that AWOS is ready to run (spends nothing).")
    ap.add_argument("--repo", default=None, help="repo to check (default: current directory)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    checks = run_checks(Path(args.repo) if args.repo else None, get=get)
    ok = all(c.ok for c in checks if c.required)
    if args.json:
        out(json.dumps({"ok": ok, "checks": [c.to_dict() for c in checks]}, indent=2))
    else:
        out(format_checks(checks))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
