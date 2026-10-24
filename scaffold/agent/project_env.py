"""
project_env.py — run a project's tests in the PROJECT's environment (v0.2.2).

AWOS used to run pytest with its own interpreter (sys.executable). Installed
with pipx/uv, that interpreter has none of the project's dependencies, so every
project import failed (docs/launch/demos/README.md, lesson 3). This module
decides which command runs the project's tests, in this order:

  1. AWOS_TEST_COMMAND (env), e.g. "uv run pytest" or "/path/to/python -m pytest"
  2. ~/.awos/projects.json: {"<repo path>": {"test_command": "..."}}
  3. the repo's virtualenv: .venv/bin/python or venv/bin/python (with pytest)
  4. uv (uv.lock):            uv run --frozen --project <repo> pytest
  5. poetry (poetry.lock):    the env `poetry env info -p` names
  6. hatch ([tool.hatch.envs]): the env `hatch env find default` names
  7. AWOS's own interpreter, with a note saying so

The command prefix runs pytest; callers append flags and test paths.

The repo's venv usually sits in the user's checkout, not in the job's
workspace (a git worktree has no untracked .venv), so the candidates are the
workspace, AWOS_PROJECT_ROOT (set by the host worker) and the main worktree
of the workspace's git repo.

Tests must import the JOB WORKSPACE code: env_for() puts the workspace (and
its src/ dir) first on PYTHONPATH. An editable install in the project's venv
points at the user's checkout; PYTHONPATH entries come before site-packages
and before setuptools' editable finder, so the workspace wins. detect() notes
each editable install that points outside the workspace.
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

try:
    from scaffold.agent import paths as _paths
except ImportError:  # pragma: no cover - flat import when run from scaffold/agent
    import paths as _paths  # type: ignore

ENV_VAR = "AWOS_TEST_COMMAND"
PROJECT_ROOT_VAR = "AWOS_PROJECT_ROOT"
CONFIG_NAME = "projects.json"
VENV_DIRS = (".venv", "venv")
_PROBE_TIMEOUT_SEC = 15


@dataclass
class TestEnv:
    """How to run pytest for one project."""
    __test__ = False  # not a pytest class

    argv: list[str]                  # command prefix that runs pytest
    source: str                      # AWOS_TEST_COMMAND | config | .venv | venv | uv | poetry | hatch | awos
    python: Optional[str] = None     # the interpreter, when known
    project_root: str = ""           # where the environment was found
    notes: list[str] = field(default_factory=list)
    read_roots: list[str] = field(default_factory=list)   # dirs a sandbox must let it read
    #: The user's checkout (the repo's main worktree): editable installs in the
    #: env point here, and test runs redirect them to the job's workspace.
    checkout_root: str = ""

    @property
    def is_fallback(self) -> bool:
        return self.source == "awos"

    def command_str(self) -> str:
        return shlex.join(self.argv)

    def describe(self) -> str:
        """One line for reports and `awos doctor`."""
        where = {
            ".venv": "project .venv", "venv": "project venv", "uv": "uv project env",
            "poetry": "poetry env", "hatch": "hatch env", "config": "~/.awos/projects.json",
            ENV_VAR: ENV_VAR, "awos": "AWOS's own Python: no project env found",
        }.get(self.source, self.source)
        return f"{self.command_str()} ({where})"

    def short(self) -> str:
        """Compact form for headers: paths inside the project shown relative."""
        if self.source == "awos":
            return "python -m pytest (AWOS's Python)"
        root = os.path.realpath(self.project_root) if self.project_root else ""
        parts = []
        for a in self.argv:
            real = os.path.realpath(a) if os.path.isabs(a) else ""
            if root and os.path.isabs(a) and (a.startswith(root + os.sep) or a.startswith(self.project_root + os.sep)):
                a = os.path.relpath(a, self.project_root if a.startswith(self.project_root) else root)
            elif real and os.path.basename(a) == "uv":
                a = "uv"
            parts.append(a)
        if self.source == "uv":
            parts = [p for i, p in enumerate(parts)
                     if not (p == "--project" or (i and parts[i - 1] == "--project"))]
        return shlex.join(parts)

    def pythonpath(self, workspace: str | Path) -> list[str]:
        ws = Path(os.path.abspath(workspace))
        entries = [str(ws)]
        if (ws / "src").is_dir():
            entries.append(str(ws / "src"))
        return entries

    def remap_from(self, workspace: str | Path) -> list[str]:
        """The user's checkout paths that must read as `workspace` (both spellings)."""
        root = self.checkout_root
        if not root or os.path.realpath(root) == os.path.realpath(workspace):
            return []
        return list(dict.fromkeys([str(root), os.path.realpath(root)]))

    def env_vars(self, workspace: str | Path, mount: Optional[str] = None) -> dict:
        """The variables a test run in `workspace` adds: workspace code first on
        PYTHONPATH and, when the env's editable installs point at the user's
        checkout, the remap shim that redirects them to `workspace`.
        `mount` is the workspace's path inside a container (no shim there)."""
        if mount:
            ws = Path(workspace)
            entries = [mount] + ([f"{mount}/src"] if (ws / "src").is_dir() else [])
            return {"PYTHONPATH": os.pathsep.join(entries)}
        workspace = os.path.abspath(workspace)
        entries = self.pythonpath(workspace)
        out = {}
        sources = self.remap_from(workspace)
        if sources:
            entries = [remap_shim_dir()] + entries
            out["AWOS_REMAP_FROM"] = os.pathsep.join(sources)
            out["AWOS_REMAP_TO"] = str(workspace)
        out["PYTHONPATH"] = os.pathsep.join(entries)
        return out

    def env_for(self, workspace: str | Path, base: Optional[dict] = None) -> dict:
        """The environment a test run in `workspace` gets: workspace code first."""
        env = dict(os.environ if base is None else base)
        rest = env.get("PYTHONPATH", "")
        extra = self.env_vars(workspace)
        env.update(extra)
        if rest:
            env["PYTHONPATH"] = extra["PYTHONPATH"] + os.pathsep + rest
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        # uv run must not re-resolve or touch the network for a test run.
        if self.source == "uv":
            env.setdefault("UV_NO_PROGRESS", "1")
        return env

    def shell_prefix(self, workspace: str | Path, mount: Optional[str] = None) -> str:
        """`VAR=value ...` for a shell command run in a sandbox."""
        return " ".join(f"{k}={shlex.quote(v)}" for k, v in self.env_vars(workspace, mount).items())


# ── editable-install remap shim ──────────────────────────────────────────────

#: A sitecustomize.py that Python imports at startup (after site-packages'
#: .pth files ran): it rewrites every path under AWOS_REMAP_FROM to
#: AWOS_REMAP_TO in sys.path (plain .pth editables), in setuptools' editable
#: finders (MAPPING / NAMESPACES) and in the `editables` redirector (hatch).
#: So an editable install of the user's checkout imports the job's workspace.
#: PYTHONPATH alone cannot do it for split layouts (repowise maps
#: repowise.core to packages/core/src, a path no PYTHONPATH entry covers).
_SHIM = '''\
# AWOS test-run shim (scaffold/agent/project_env.py): redirect editable installs
# of the user's checkout to the job's workspace. Inert without AWOS_REMAP_*.
import os as _os
import sys as _sys


def _awos_remap():
    src = [s for s in _os.environ.get("AWOS_REMAP_FROM", "").split(_os.pathsep) if s]
    dst = _os.environ.get("AWOS_REMAP_TO", "")
    if not src or not dst:
        return

    # The env itself (often <checkout>/.venv) is never remapped.
    keep = [k.rstrip(_os.sep) + _os.sep for k in
            {_sys.prefix, _sys.exec_prefix, _os.path.realpath(_sys.prefix)}]

    def fix(p):
        if isinstance(p, str) and not any((p + _os.sep).startswith(k) for k in keep):
            for s in src:
                if p == s or p.startswith(s.rstrip(_os.sep) + _os.sep):
                    new = dst + p[len(s.rstrip(_os.sep)):]
                    # Only to a path the workspace really has (tracked source).
                    return new if _os.path.exists(new) else p
        return p

    _sys.path[:] = [fix(p) for p in _sys.path]
    for name, mod in list(_sys.modules.items()):
        if name.startswith("__editable__") and name.endswith("_finder"):
            m = getattr(mod, "MAPPING", None)
            if isinstance(m, dict):
                new = {k: fix(v) for k, v in m.items()}
                m.clear()
                m.update(new)
            ns = getattr(mod, "NAMESPACES", None)
            if isinstance(ns, dict):
                new = {k: [fix(v) for v in vs] for k, vs in ns.items()}
                ns.clear()
                ns.update(new)
    red = _sys.modules.get("editables.redirector")
    finder = getattr(red, "RedirectingFinder", None) if red else None
    table = getattr(finder, "_redirections", None) if finder else None
    if isinstance(table, dict):
        for k in list(table):
            table[k] = fix(table[k])
    for k in list(_sys.path_importer_cache):
        if fix(k) != k:
            _sys.path_importer_cache.pop(k, None)


try:
    _awos_remap()
except Exception:  # never break the project's test run
    pass
'''
_SHIM_DIR: Optional[str] = None


def remap_shim_dir() -> str:
    """A private dir holding the shim as sitecustomize.py (created once per process)."""
    global _SHIM_DIR
    if _SHIM_DIR and (Path(_SHIM_DIR) / "sitecustomize.py").is_file():
        return _SHIM_DIR
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="awos-remap-"))
    (d / "sitecustomize.py").write_text(_SHIM, encoding="utf-8")
    _SHIM_DIR = os.path.realpath(d)
    return _SHIM_DIR


# ── helpers ──────────────────────────────────────────────────────────────────


def _git_main_root(path: Path) -> Optional[Path]:
    """The main worktree of the repo `path` belongs to (a job workspace is a
    linked worktree). Read from the .git file, no git process."""
    path = Path(os.path.realpath(path))
    for cand in [path, *list(path.parents)[:12]]:
        dot = cand / ".git"
        if dot.is_dir():
            return cand
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text[len("gitdir:"):].strip())
            if not gitdir.is_absolute():
                gitdir = (cand / gitdir).resolve()
            try:
                common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip())
            except OSError:
                common = gitdir
            common = Path(os.path.realpath(common))
            return common.parent if common.name == ".git" else None
    return None


def candidate_roots(workspace: str | Path, project_root: Optional[str | Path] = None) -> list[Path]:
    roots: list[Path] = []
    for r in (workspace, project_root or os.getenv(PROJECT_ROOT_VAR, "").strip() or None):
        if r:
            roots.append(Path(r).expanduser())
    main = _git_main_root(Path(workspace))
    if main is not None:
        roots.append(main)
    out, seen = [], set()
    for r in roots:
        key = os.path.realpath(r)
        if key not in seen and Path(r).is_dir():
            seen.add(key)
            out.append(Path(key))
    return out


def config_path() -> Path:
    return _paths.awos_home() / CONFIG_NAME


def configured_command(roots: list[Path]) -> Optional[tuple[str, Path]]:
    """A per-repo test_command from ~/.awos/projects.json."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    by_path = {os.path.realpath(os.path.expanduser(k)): v for k, v in data.items()}
    for root in roots:
        entry = by_path.get(os.path.realpath(root))
        if isinstance(entry, dict) and str(entry.get("test_command") or "").strip():
            return str(entry["test_command"]).strip(), root
    return None


def _site_packages(prefix: Path) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(str(prefix / "lib" / "python*" / "site-packages")))]


def _has_pytest(prefix: Path) -> bool:
    return any((sp / "pytest").is_dir() or (sp / "_pytest").is_dir() for sp in _site_packages(prefix))


def _venv_home(prefix: Path) -> Optional[Path]:
    try:
        for line in (prefix / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("=")
            if key.strip() == "home" and val.strip():
                return Path(val.strip())
    except OSError:
        pass
    return None


def _read_roots(python: str) -> list[str]:
    """The env's prefix and the base interpreter it links to (a sandbox must read both)."""
    py = Path(python)
    prefix = py.parent.parent
    roots = {str(prefix), os.path.realpath(prefix)}
    real = Path(os.path.realpath(py))
    roots.add(str(real.parent.parent))
    home = _venv_home(prefix)
    if home is not None:
        roots.add(str(home.parent))
        roots.add(os.path.realpath(home.parent))
    return sorted(r for r in roots if r and r != "/")


def editable_notes(python: Optional[str], workspace: str | Path) -> list[str]:
    """Editable installs in the env that point outside the workspace."""
    if not python:
        return []
    ws = os.path.realpath(workspace)
    notes = []
    for sp in _site_packages(Path(python).parent.parent):
        for direct in sp.glob("*.dist-info/direct_url.json"):
            try:
                data = json.loads(direct.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not (data.get("dir_info") or {}).get("editable"):
                continue
            url = urlparse(str(data.get("url") or ""))
            target = os.path.realpath(unquote(url.path)) if url.scheme == "file" else ""
            if target and not (target == ws or target.startswith(ws + os.sep)):
                name = direct.parent.name.split("-")[0]
                notes.append(f"editable install of {name} points at {target}; test runs "
                             "redirect it to the job workspace, so tests import the workspace code")
    return notes


def _probe(cmd: list[str], cwd: Path) -> Optional[str]:
    try:
        out = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                             timeout=_PROBE_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    return lines[-1] if out.returncode == 0 and lines else None


def _env_python(env_dir: Optional[str]) -> Optional[str]:
    if not env_dir:
        return None
    py = Path(env_dir) / "bin" / "python"
    return str(py) if py.exists() else None


def _python_env(python: str, source: str, root: Path, notes: list[str]) -> TestEnv:
    return TestEnv(argv=[python, "-m", "pytest"], source=source, python=python,
                   project_root=str(root), notes=notes, read_roots=_read_roots(python))


# ── detection ────────────────────────────────────────────────────────────────

_CACHE: dict[tuple, TestEnv] = {}


def detect(workspace: str | Path, project_root: Optional[str | Path] = None,
           use_cache: bool = True) -> TestEnv:
    """The command that runs pytest for the project at `workspace`."""
    workspace = Path(workspace).expanduser()
    explicit = os.getenv(ENV_VAR, "").strip()
    key = (os.path.realpath(workspace), str(project_root or os.getenv(PROJECT_ROOT_VAR, "")),
           explicit, str(config_path()))
    if use_cache and key in _CACHE:
        return _CACHE[key]
    env = _detect(workspace, project_root, explicit)
    checkout = project_root or os.getenv(PROJECT_ROOT_VAR, "").strip() or _git_main_root(workspace)
    if checkout and Path(checkout).is_dir():
        env.checkout_root = os.path.realpath(checkout)
    if not env.is_fallback:
        env.notes.extend(n for n in editable_notes(env.python, workspace) if n not in env.notes)
    _CACHE[key] = env
    return env


def clear_cache() -> None:
    _CACHE.clear()


def _from_command(text: str, source: str, root: Path) -> TestEnv:
    argv = shlex.split(text)
    python = None
    if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"] and os.path.isabs(argv[0]):
        python = argv[0]
    return TestEnv(argv=argv, source=source, python=python, project_root=str(root),
                   notes=[f"test command from {source}: {text}"],
                   read_roots=_read_roots(python) if python else [])


def _detect(workspace: Path, project_root, explicit: str) -> TestEnv:
    roots = candidate_roots(workspace, project_root)
    first = roots[0] if roots else workspace
    if explicit:
        return _from_command(explicit, ENV_VAR, first)
    conf = configured_command(roots)
    if conf:
        return _from_command(conf[0], "config", conf[1])
    notes: list[str] = []
    for root in roots:
        for d in VENV_DIRS:
            py = root / d / "bin" / "python"
            if not py.exists():
                continue
            if not _has_pytest(root / d):
                notes.append(f"{root / d} has no pytest installed; not used "
                             f"(install pytest there, or set {ENV_VAR})")
                continue
            return _python_env(str(py), d, root, notes)
    for root in roots:
        if (root / "uv.lock").is_file() and shutil.which("uv"):
            argv = [shutil.which("uv") or "uv", "run", "--frozen", "--project", str(root), "pytest"]
            return TestEnv(argv=argv, source="uv", project_root=str(root),
                           notes=notes + [f"uv.lock found in {root}: tests run with `uv run`"])
    for root in roots:
        if (root / "poetry.lock").is_file() and shutil.which("poetry"):
            py = _env_python(_probe(["poetry", "env", "info", "-p"], root))
            if py:
                return _python_env(py, "poetry", root, notes)
            notes.append(f"poetry.lock found in {root} but `poetry env info -p` named no env")
    for root in roots:
        pyproject = root / "pyproject.toml"
        try:
            hatch = pyproject.is_file() and "[tool.hatch.envs" in pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            hatch = False
        if hatch and shutil.which("hatch"):
            py = _env_python(_probe(["hatch", "env", "find", "default"], root))
            if py and _has_pytest(Path(py).parent.parent):
                return _python_env(py, "hatch", root, notes)
    notes.append(f"no project environment found (.venv, venv, uv, poetry, hatch): tests run with "
                 f"AWOS's own Python ({sys.executable}); if project imports fail, create a .venv "
                 f"or set {ENV_VAR}")
    return TestEnv(argv=[sys.executable, "-m", "pytest"], source="awos", python=sys.executable,
                   project_root=str(first), notes=notes, read_roots=[])
