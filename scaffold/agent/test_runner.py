"""
TestRunner — Language-agnostic test executor.

Auto-detects the project's test framework, runs tests after every code patch,
and returns a pass_rate that feeds into the LinUCB reward signal.

Safety: AWOS only runs tests when AWOS_SAFE_TO_RUN_TESTS=1 is set.
This prevents accidental destructive side effects on production test suites.

Cost: FREE (local execution).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
_DEFAULT_TIMEOUT_SEC = 60
#: The whole visible suite is slower than a few mapped files; a real project's
#: suite (sqlparse, more-itertools) runs in seconds, so this is headroom.
_FULL_SUITE_TIMEOUT_SEC = 180
_SAFETY_ENV_VAR = "AWOS_SAFE_TO_RUN_TESTS"

#: Directories never searched for tests (vendored code, envs, build output).
_SKIP_DIRS = {
    "node_modules", "venv", "env", "build", "dist", "__pycache__",
    "site-packages", "htmlcov",
}
_MAX_WALK_FILES = 20000

#: map_tests(related=True): at most this many test files in all (name
#: matches first), so a widely imported module never maps to the whole suite.
MAX_RELATED_TEST_FILES = 12
_MAX_REF_SCAN_BYTES = 1_000_000

#: Set by the host worker for its orchestrator child: where a green
#: whole-suite run records the tree it ran on (host verify_tests reuses it).
FULL_SUITE_MARKER_ENV = "AWOS_FULL_SUITE_MARKER"
#: Never part of the tree hash: VCS/state dirs and what a test run writes.
_TREE_SKIP_DIRS = {".git", ".awos", "__pycache__", ".pytest_cache", ".mypy_cache",
                   ".ruff_cache", ".hypothesis"}


def _tree_files(root: str) -> List[str]:
    """Tracked + untracked (not ignored) files, relative; os.walk without git."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", root, "ls-files", "-co",
             "--exclude-standard", "-z"],
            capture_output=True, timeout=60)
        if proc.returncode == 0:
            names = proc.stdout.decode("utf-8", "replace").split("\0")
            return sorted({n for n in names if n})
    except (OSError, subprocess.TimeoutExpired):
        pass
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _TREE_SKIP_DIRS]
        for name in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(out)


def workspace_tree_hash(root: str) -> str:
    """
    sha256 over the workspace's tracked and untracked files (path + bytes; a
    deleted tracked file hashes as missing), excluding .git, .awos and test-run
    byproducts (__pycache__, *.pyc, .pytest_cache). Same tree, same hash.
    """
    import hashlib
    digest = hashlib.sha256()
    for rel in _tree_files(root):
        if (any(p in _TREE_SKIP_DIRS for p in Path(rel).parts)
                or rel.endswith((".pyc", ".pyo"))):
            continue
        path = os.path.join(root, rel)
        digest.update(rel.encode("utf-8", "replace") + b"\0")
        try:
            if os.path.islink(path):
                digest.update(b"L" + os.readlink(path).encode("utf-8", "replace"))
            elif os.path.isfile(path):
                h = hashlib.sha256()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                digest.update(b"F" + h.digest())
            else:
                digest.update(b"-")
        except OSError:
            digest.update(b"?")
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass
class TestConfig:
    """Detected test framework configuration for a project."""
    __test__ = False  # suppress pytest collection warning

    runner: str        # "pytest" | "jest" | "vitest" | "make" | "go" | "cargo" | "generic"
    command: List[str] # shell command tokens
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC


@dataclass
class TestResult:
    """Result of a single test execution."""
    __test__ = False  # suppress pytest collection warning

    passed: int = 0
    failed: int = 0
    errors: int = 0
    pass_rate: float = 0.0       # 0.0–1.0
    raw_output: str = ""         # stdout + stderr
    timed_out: bool = False
    test_command: List[str] = field(default_factory=list)
    no_tests_found: bool = False
    #: "mapped" — only tests matched to the changed files ran; "full" — the
    #: project's whole visible suite ran; "" — nothing was run.
    mode: str = ""
    #: pytest itself crashed before counting anything (internal error, usage
    #: error, a config-discovery traceback): the tests could not run, which is
    #: not "0 passed, 0 failed". no_tests_found is set too, so every consumer
    #: treats it as no evidence rather than a red suite.
    infra_error: bool = False
    infra_reason: str = ""
    #: v0.2.2 baseline: ids failing now that ALSO fail at the job's base
    #: commit. They are not counted in failed/errors (only new failures are).
    preexisting: List[str] = field(default_factory=list)
    #: True when failures were compared against the baseline workspace.
    baseline_checked: bool = False
    #: Where the tests ran: the project env's description (project_env).
    env_desc: str = ""


#: Through the running interpreter, not a bare `pytest` on PATH: without an
#: activated venv the bare command is "not found", and verification silently
#: ran nothing.
_PYTEST_CMD = [sys.executable, "-m", "pytest", "--tb=short", "-q"]

#: For a project with no pytest config: stop pytest's upward search. Without
#: these, rootdir/inifile discovery stats pytest.ini, pyproject.toml, tox.ini
#: and setup.cfg in every parent dir, and conftest.py lookup does the same; in
#: the seatbelt sandbox a parent under /Users that holds one of those files
#: (the AWOS checkout around a job workspace) answers EPERM and pytest dies
#: before collecting. /dev/null as the ini adds nothing to the project tree
#: (no file in the agent's diff) and exists in a container too; the paths are
#: relative because the sandbox's cwd is the workspace in both backends.
_NO_CONFIG_ARGS = ["-c", os.devnull, "--rootdir=.", "--confcutdir=."]

#: pytest exit codes meaning it never got to run tests: interrupted (also
#: collection errors), internal error, usage error.
_PYTEST_CRASH_CODES = {2, 3, 4}



# A project's pytest addopts can name a plugin that is not installed (most
# often `--cov` without pytest-cov): pytest then exits with a usage error and
# runs nothing. ADDOPTS_OVERRIDE clears them for AWOS's own runs.
ADDOPTS_OVERRIDE = ["-o", "addopts="]
_ADDOPTS_ERROR = re.compile(r"error: unrecognized arguments:|unrecognized arguments: --")


def _is_addopts_usage_error(output: str) -> bool:
    return bool(_ADDOPTS_ERROR.search(output or ""))

class TestRunner:
    """Language-agnostic test executor with auto-detection."""
    __test__ = False  # suppress pytest collection warning
    # Set once the project's addopts caused a pytest usage error (_execute).
    _drop_addopts = False

    def __init__(
        self,
        project_root: str = ".",
        timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
        sandbox=None,
        test_env=None,
    ):
        self.project_root = Path(project_root)
        self.timeout_sec = timeout_sec
        # When set, tests run inside it: pytest imports project code (and any
        # conftest.py), which may have been written by the model.
        self.sandbox = sandbox
        # The project's own environment (project_env.TestEnv), detected lazily.
        self._test_env = test_env

    @property
    def test_env(self):
        """How pytest runs for this project: its venv/uv/poetry env, else AWOS's Python."""
        if self._test_env is None:
            try:
                from scaffold.agent import project_env
            except ImportError:  # pragma: no cover - flat import
                import project_env  # type: ignore
            self._test_env = project_env.detect(self.project_root)
        return self._test_env

    def pytest_command(self) -> List[str]:
        return list(self.test_env.argv) + ["--tb=short", "-q"]

    # ── Public API ────────────────────────────────────────────────────────

    def detect(self) -> Optional[TestConfig]:
        """Inspect project_root and return a TestConfig, or None."""
        # Try each detector in order of specificity
        for detector in [
            self._detect_pytest,
            self._detect_jest,
            self._detect_vitest,
            self._detect_go,
            self._detect_cargo,
            self._detect_make,
        ]:
            cfg = detector()
            if cfg is not None:
                return cfg
        return None

    def run(self, changed_files: Optional[List[str]] = None,
            related: bool = False) -> TestResult:
        """
        Run tests using detected config. related=True also maps test files
        that import/reference the changed modules (map_tests): for the agent's
        own intermediate checks. The success gate keeps related=False, so a
        change with no name-matched test still runs the whole suite.
        """
        # ── Safety guard ────────────────────────────────────────────────
        if os.environ.get(_SAFETY_ENV_VAR) != "1":
            logger.info(
                "[test_runner] Safety guard active — set %s=1 to enable test execution",
                _SAFETY_ENV_VAR,
            )
            return TestResult(
                raw_output=f"Tests skipped — set {_SAFETY_ENV_VAR}=1 to enable.",
                no_tests_found=True,
            )

        cfg = self.detect()
        if cfg is None:
            logger.info("[test_runner] No test framework detected")
            return TestResult(raw_output="No test framework detected.", no_tests_found=True)

        if cfg.runner != "pytest":
            result = self._execute(cfg)
            result.mode = "full"
            return result
        return self._run_pytest(cfg, changed_files or [], related=related)

    # ── pytest: mapped tests first, else the whole visible suite ─────────

    def _run_pytest(self, cfg: TestConfig, changed_files: List[str],
                    related: bool = False) -> TestResult:
        """
        Run the tests mapped to changed_files; when the mapping finds none, or
        they collect nothing, run the whole visible suite instead. Source and
        test files are often named differently (sqlparse/sql.py is covered by
        tests/test_tokenize.py), so an empty mapping is not "no tests".
        """
        base = self.pytest_base(cfg)
        mapped = self.map_tests(changed_files, related=related)
        if mapped:
            result = self._execute(TestConfig("pytest", base + mapped, self.timeout_sec))
            if not result.no_tests_found:
                result.mode = "mapped"
                logger.info("[test_runner] mode=mapped (%d test file(s))", len(mapped))
                return result
            logger.info("[test_runner] mapped tests collected nothing; running the full suite")

        targets = self._full_suite_targets()
        timeout = max(self.timeout_sec, _FULL_SUITE_TIMEOUT_SEC)
        result = self._execute(TestConfig("pytest", base + targets, timeout))
        result.mode = "full"
        logger.info("[test_runner] mode=full targets=%s", targets or "(pytest config)")
        self._record_full_suite(base + targets, result)
        return result

    def full_suite_key(self) -> List[str]:
        """The whole-suite pytest command without run-specific extras (marker key)."""
        return self.pytest_base() + self._full_suite_targets()

    def _record_full_suite(self, command: List[str], result: TestResult) -> None:
        """
        AWOS_FULL_SUITE_MARKER (set by the host worker for its child): after a
        whole-suite run, record whether it was green on this exact tree, so
        the host's verify_tests can reuse it instead of running it again. A
        non-green run removes any earlier marker. Never raises.
        """
        path = os.environ.get(FULL_SUITE_MARKER_ENV, "").strip()
        if not path:
            return
        try:
            green = (not result.timed_out and not result.infra_error
                     and not result.no_tests_found and result.failed == 0
                     and result.errors == 0 and result.passed > 0)
            if not green:
                Path(path).unlink(missing_ok=True)
                return
            data = {
                "tree": workspace_tree_hash(str(self.project_root)),
                "command": list(command),
                "passed": result.passed, "failed": result.failed, "errors": result.errors,
                "preexisting": list(result.preexisting),
                "baseline_checked": bool(result.baseline_checked),
                "env": result.env_desc,
            }
            tmp = Path(path + ".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
            logger.info("[test_runner] full suite green; marker written for host reuse")
        except Exception as exc:  # noqa: BLE001 - an optimisation only
            logger.debug("[test_runner] full-suite marker not written: %s", exc)

    def pytest_base(self, cfg: Optional[TestConfig] = None) -> List[str]:
        """The pytest command without targets."""
        # No cache plugin: nothing written to .pytest_cache, inside a sandbox
        # or not. No color: ANSI codes would sit inside the summary line.
        # -rfE: the short summary names each failing id (baseline comparison).
        command = list(cfg.command) if cfg is not None else self.pytest_command()
        base = command + ["-p", "no:cacheprovider", "--color=no", "-rfE"]
        if not self._has_pytest_config():
            base += _NO_CONFIG_ARGS
        if self._drop_addopts:
            base += ADDOPTS_OVERRIDE
        return base

    def run_targets(self, targets: List[str], timeout_sec: Optional[int] = None,
                    extra_args: Optional[List[str]] = None) -> TestResult:
        """Run pytest on `targets` (none: the configured/visible suite); no safety guard."""
        result = self._execute(TestConfig("pytest", self.pytest_base() + list(extra_args or [])
                                          + list(targets),
                                          int(timeout_sec or self.timeout_sec)))
        result.mode = "mapped" if targets else "full"
        return result

    def find_test_files(self) -> List[str]:
        """Visible pytest-style test files, relative to project_root, sorted."""
        found: List[str] = []
        seen = 0
        root = str(self.project_root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in _SKIP_DIRS and not d.endswith(".egg-info")
            ]
            for name in filenames:
                seen += 1
                if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
                    found.append(os.path.relpath(os.path.join(dirpath, name), root))
            if seen > _MAX_WALK_FILES:
                break
        return sorted(found)

    def map_tests(self, changed_files: List[str], related: bool = False) -> List[str]:
        """
        Test files matched to changed_files by name: a changed test file
        itself, or test_<stem>.py / test_<stem>_*.py / <stem>_test.py for a
        changed source file. Empty when nothing matches.

        related=True adds test files that import or reference a changed
        module (astroid/protocols.py has no test_protocols.py, but tests that
        say `from astroid import protocols` or `astroid.protocols` cover it),
        up to MAX_RELATED_TEST_FILES in all; name matches come first.
        """
        if not changed_files:
            return []
        tests = self.find_test_files()
        mapped: List[str] = []
        sources: List[Path] = []
        for changed in changed_files:
            path = Path(changed)
            if path.is_absolute():
                try:
                    path = path.relative_to(self.project_root.resolve())
                except ValueError:
                    continue
            if path.suffix != ".py":
                continue
            name = path.name
            if name.startswith("test_") or name.endswith("_test.py"):
                if (self.project_root / path).is_file():
                    mapped.append(str(path))
                continue
            stem = path.parent.name if path.stem == "__init__" else path.stem
            stem = stem.lstrip("_")
            if not stem:
                continue
            sources.append(path)
            for test in tests:
                tstem = Path(test).stem
                if tstem in (f"test_{stem}", f"{stem}_test") or tstem.startswith(f"test_{stem}_"):
                    mapped.append(test)
        mapped = list(dict.fromkeys(mapped))
        if related and sources and len(mapped) < MAX_RELATED_TEST_FILES:
            extra = self._referencing_tests(sources, [t for t in tests if t not in mapped])
            mapped += extra[:MAX_RELATED_TEST_FILES - len(mapped)]
        return mapped

    def _referencing_tests(self, sources: List[Path], tests: List[str]) -> List[str]:
        """
        Test files whose text imports or names one of `sources`' modules.
        Strong references first (`pkg.mod`, `from pkg import mod`,
        `import mod`, `from mod import`), then `mod.` attribute use; each
        tier sorted by path.
        """
        strong, weak = [], []
        for src in sources:
            parts = list(src.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if parts and parts[0] in ("src", "lib"):
                parts = parts[1:]
            if not parts or not all(p.isidentifier() for p in parts):
                continue
            mod = re.escape(parts[-1])
            pats = [rf"^\s*import\s+{mod}\b", rf"^\s*from\s+{mod}\s+import\b"]
            if len(parts) > 1:
                pkg = re.escape(".".join(parts[:-1]))
                pats.append(rf"\b{pkg}\.{mod}\b")
                pats.append(rf"^\s*from\s+{pkg}\s+import\s+[^\n#]*\b{mod}\b")
            strong.append(re.compile("|".join(pats), re.MULTILINE))
            weak.append(re.compile(rf"(?<![\w.]){mod}\."))
        if not strong:
            return []
        hits_strong, hits_weak = [], []
        for test in tests:
            path = self.project_root / test
            try:
                if path.stat().st_size > _MAX_REF_SCAN_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(p.search(text) for p in strong):
                hits_strong.append(test)
            elif any(p.search(text) for p in weak):
                hits_weak.append(test)
        return sorted(hits_strong) + sorted(hits_weak)

    def _full_suite_targets(self) -> List[str]:
        """
        Paths for a whole-suite run. With `testpaths` in the pytest config,
        none: pytest uses it. Otherwise tests/ (or test/) when it holds tests,
        else none and pytest discovers from the root.
        """
        if self._configured_testpaths():
            return []
        tests = self.find_test_files()
        for d in ("tests", "test"):
            if any(t.startswith(d + os.sep) for t in tests):
                return [d]
        return []

    def _has_pytest_config(self) -> bool:
        """
        True when project_root holds a file pytest takes as its config, which
        ends the upward search there. A pyproject.toml or setup.cfg without a
        pytest section does not: pytest keeps walking up past it.
        """
        root = self.project_root
        for name in ("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"):
            if (root / name).is_file():
                return True
        return (
            "[tool.pytest" in self._read(root / "pyproject.toml")
            or "[pytest]" in self._read(root / "tox.ini")
            or "[tool:pytest]" in self._read(root / "setup.cfg")
        )

    def _configured_testpaths(self) -> bool:
        root = self.project_root
        for name, section in (
            ("pytest.ini", "[pytest]"),
            ("pyproject.toml", "[tool.pytest.ini_options]"),
            ("tox.ini", "[pytest]"),
            ("setup.cfg", "[tool:pytest]"),
        ):
            content = self._read(root / name)
            if section not in content:
                continue
            body = content.split(section, 1)[1]
            body = re.split(r"^\[", body, maxsplit=1, flags=re.MULTILINE)[0]
            if re.search(r"^\s*testpaths\s*=", body, re.MULTILINE):
                return True
        return False

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError):
            return ""

    # ── Detection helpers ───────────────────────────────────────────────

    def _detect_pytest(self) -> Optional[TestConfig]:
        """Detect pytest via pytest.ini, pyproject.toml, or setup.cfg."""
        root = self.project_root
        if (root / "pytest.ini").exists():
            return TestConfig("pytest", self.pytest_command())

        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8")
            if "[tool.pytest" in content:
                return TestConfig("pytest", self.pytest_command())

        setup_cfg = root / "setup.cfg"
        if setup_cfg.exists():
            content = setup_cfg.read_text(encoding="utf-8")
            if "[tool:pytest]" in content:
                return TestConfig("pytest", self.pytest_command())

        if "[pytest]" in self._read(root / "tox.ini"):
            return TestConfig("pytest", self.pytest_command())

        # No pytest config: most real projects (sqlparse, more-itertools) have
        # none, yet pytest runs their tests/ (unittest-style included).
        if (root / "conftest.py").exists() or self.find_test_files():
            return TestConfig("pytest", self.pytest_command())

        return None

    def _detect_jest(self) -> Optional[TestConfig]:
        """Detect Jest via package.json dependency."""
        pkg = self.project_root / "package.json"
        if not pkg.exists():
            return None
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            if "jest" in deps or "jest" in dev_deps:
                return TestConfig("jest", ["npx", "jest", "--passWithNoTests"])
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _detect_vitest(self) -> Optional[TestConfig]:
        """Detect Vitest via package.json dependency."""
        pkg = self.project_root / "package.json"
        if not pkg.exists():
            return None
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            if "vitest" in deps or "vitest" in dev_deps:
                return TestConfig("vitest", ["npx", "vitest", "run"])
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _detect_go(self) -> Optional[TestConfig]:
        """Detect Go module via go.mod."""
        if (self.project_root / "go.mod").exists():
            return TestConfig("go", ["go", "test", "./..."])
        return None

    def _detect_cargo(self) -> Optional[TestConfig]:
        """Detect Rust via Cargo.toml."""
        if (self.project_root / "Cargo.toml").exists():
            return TestConfig("cargo", ["cargo", "test"])
        return None

    def _detect_make(self) -> Optional[TestConfig]:
        """Detect Make via Makefile with test target."""
        makefile = self.project_root / "Makefile"
        if makefile.exists():
            content = makefile.read_text(encoding="utf-8")
            if re.search(r"^[\w\-]*test\s*:", content, re.MULTILINE):
                return TestConfig("make", ["make", "test"])
        return None

    # ── Execution ───────────────────────────────────────────────────────

    def _execute(self, cfg: TestConfig) -> TestResult:
        """
        Run the configured test command and parse output. A pytest usage error
        from the project's own addopts (e.g. `--cov` in pytest.ini without
        pytest-cov installed) is retried once with `-o addopts=`, and later
        runs keep that override; otherwise the run reports nothing passed.
        """
        if cfg.runner == "pytest" and self._drop_addopts and ADDOPTS_OVERRIDE[1] not in cfg.command:
            cfg = TestConfig(cfg.runner, list(cfg.command) + ADDOPTS_OVERRIDE, cfg.timeout_sec)
        result = self._execute_once(cfg)
        if (cfg.runner == "pytest" and not self._drop_addopts
                and _is_addopts_usage_error(result.raw_output or "")):
            self._drop_addopts = True
            logger.warning("[test_runner] pytest rejected the project's addopts (a plugin it "
                           "names is not installed); rerunning with -o addopts=")
            print("[TESTS] the project's pytest addopts need a plugin that is not installed; "
                  "running without them (-o addopts=)")
            result = self._execute_once(TestConfig(cfg.runner, list(cfg.command) + ADDOPTS_OVERRIDE,
                                                   cfg.timeout_sec))
        return result

    def _execute_once(self, cfg: TestConfig) -> TestResult:
        """Run the configured test command and parse output."""
        logger.info("[test_runner] Running: %s (timeout=%ds)", " ".join(cfg.command), cfg.timeout_sec)
        if self.sandbox is not None:
            return self._with_baseline(cfg, self._execute_in_sandbox(cfg))
        # pytest runs in the project's env with the workspace first on
        # PYTHONPATH, so an editable install of the user's checkout loses.
        env = self.test_env.env_for(self.project_root) if cfg.runner == "pytest" else None
        try:
            result = subprocess.run(
                cfg.command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=cfg.timeout_sec,
                env=env,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[test_runner] Timed out after %ds", cfg.timeout_sec)
            return TestResult(
                timed_out=True,
                raw_output=f"Test execution timed out after {cfg.timeout_sec}s.",
                test_command=cfg.command,
            )
        except FileNotFoundError as e:
            logger.error("[test_runner] Command not found: %s", e)
            # Nothing ran: that is no evidence, not a run with zero results.
            return TestResult(
                raw_output=f"Command not found: {e}",
                test_command=cfg.command,
                no_tests_found=True,
            )

        if cfg.runner == "pytest" and "No module named pytest" in (result.stderr or ""):
            return self._no_pytest(result.stderr or "", cfg.command)
        parsed = self._parse(cfg, result.stdout or "", result.stderr or "",
                             getattr(result, "returncode", None))
        return self._with_baseline(cfg, parsed)

    # ── baseline: only new failures count (baseline_tests.py) ───────────

    def _with_baseline(self, cfg: TestConfig, result: TestResult) -> TestResult:
        if cfg.runner != "pytest":
            return result
        result.env_desc = self.test_env.describe()
        if result.infra_error or result.timed_out or not (result.failed or result.errors):
            return result
        try:
            from scaffold.agent import baseline_tests as bt
        except ImportError:  # pragma: no cover - flat import
            import baseline_tests as bt  # type: ignore
        base_dir = bt.baseline_dir()
        if base_dir is None or os.path.realpath(base_dir) == os.path.realpath(self.project_root):
            return result
        ids = bt.parse_failed_ids(result.raw_output)
        if not ids or len(ids) < result.failed + result.errors:
            # Not every failure is named (a custom -r in addopts): cannot compare.
            return result
        argv = list(self.test_env.argv)
        extra = ["-c", os.devnull, "--rootdir=.", "--confcutdir=."] if "-c" in cfg.command else []
        sandbox = self._baseline_sandbox(base_dir)
        env = self.test_env.env_for(base_dir)
        mount = getattr(sandbox, "MOUNT", None)
        if isinstance(mount, str) and mount:
            env["PYTHONPATH"] = os.pathsep.join(
                [mount] + ([f"{mount}/src"] if (base_dir / "src").is_dir() else []))
        try:
            at_base = bt.failing_at_base(
                ids, self._sandbox_argv(argv) if sandbox is not None else argv, extra, base_dir,
                env, timeout=max(cfg.timeout_sec, 60), sandbox=sandbox)
        finally:
            if sandbox is not None:
                try:
                    sandbox.close()
                except Exception:  # noqa: BLE001 - cleanup only
                    pass
        if at_base is None:
            return result
        result.baseline_checked = True
        pre = [i for i in ids if i in at_base]
        if not pre:
            return result
        err_ids = set(re.findall(r"^ERROR (\S.*?)(?: - .*)?$", result.raw_output, re.M))
        pre_err = len([i for i in pre if i in err_ids])
        result.errors = max(0, result.errors - pre_err)
        result.failed = max(0, result.failed - (len(pre) - pre_err))
        result.preexisting = pre
        total = result.passed + result.failed + result.errors
        result.pass_rate = round(result.passed / total, 4) if total else 1.0
        note = (f"[awos] pre-existing failures: {len(pre)} (they fail at the base commit too, "
                f"so the edit did not cause them; not counted): " + ", ".join(pre)[:600])
        result.raw_output = note + "\n\n" + result.raw_output
        logger.info("[test_runner] %d failure(s) pre-existing at the base commit", len(pre))
        return result

    def _baseline_sandbox(self, base_dir: Path):
        """The same kind of sandbox for the baseline run, so both runs see the same limits."""
        if self.sandbox is None:
            return None
        backend = getattr(self.sandbox, "backend", "")
        if backend not in ("seatbelt", "docker"):
            return None
        try:
            from scaffold.agent.sandbox import make_sandbox
        except ImportError:  # pragma: no cover - flat import
            from sandbox import make_sandbox  # type: ignore
        sb = make_sandbox(base_dir, backend=backend)
        if sb is not None and hasattr(sb, "allow_read") and self.test_env.read_roots:
            sb.allow_read(self.test_env.read_roots)
        return sb

    def _sandbox_argv(self, argv: List[str]) -> List[str]:
        """The command as the sandbox sees it: a host path means nothing in a container."""
        docker = getattr(self.sandbox, "backend", "") == "docker"
        py = self.test_env.python
        return ["python" if (part == sys.executable or (docker and py and part == py)) else part
                for part in argv]

    def _execute_in_sandbox(self, cfg: TestConfig) -> TestResult:
        """Run the test command inside the sandbox; same parsing as on the host."""
        # The host interpreter's path means nothing in a container; "python" is
        # first on the sandbox's PATH in both backends. A project venv's python
        # stays (Seatbelt), and the sandbox is told to let it read that env.
        command = self._sandbox_argv(list(cfg.command))
        shell = shlex.join(command)
        if cfg.runner == "pytest":
            mount = getattr(self.sandbox, "MOUNT", None)
            mount = mount if isinstance(mount, str) and mount else None
            ws = str(self.project_root.resolve())
            if hasattr(self.sandbox, "allow_read"):
                roots = list(self.test_env.read_roots)
                if not mount and self.test_env.remap_from(ws):
                    roots.append(self.test_env.env_vars(ws)["PYTHONPATH"].split(os.pathsep)[0])
                if roots:
                    self.sandbox.allow_read(roots)
            shell = f"{self.test_env.shell_prefix(ws, mount)} {shell}"
        result = self.sandbox.run(shell, timeout_sec=cfg.timeout_sec)
        if result.timed_out:
            return TestResult(
                timed_out=True,
                raw_output=f"Test execution timed out after {cfg.timeout_sec}s.",
                test_command=command,
            )
        if "No module named pytest" in result.stderr:
            # Nothing ran (e.g. a container image without pytest): no evidence.
            return self._no_pytest(result.stderr, command)
        return self._parse(cfg, result.stdout, result.stderr, getattr(result, "exit_code", None))

    def _no_pytest(self, stderr: str, command: List[str]) -> TestResult:
        return TestResult(
            raw_output=stderr[-2000:], test_command=command, no_tests_found=True,
            infra_error=True,
            infra_reason=f"pytest is not installed in the test environment ({command[0]}); "
                         "install it there or set AWOS_TEST_COMMAND",
            env_desc=self.test_env.describe() if command else "",
        )

    def _parse(
        self, cfg: TestConfig, stdout: str, stderr: str, exit_code: Optional[int] = None
    ) -> TestResult:
        # Dispatch to parser
        if cfg.runner == "pytest":
            return self._parse_pytest(stdout, stderr, cfg.command, exit_code=exit_code)
        elif cfg.runner == "jest":
            return self._parse_jest(stdout, stderr, cfg.command)
        elif cfg.runner == "vitest":
            return self._parse_vitest(stdout, stderr, cfg.command)
        else:
            return self._parse_generic(stdout, stderr, cfg.command)

    # ── Parsers ─────────────────────────────────────────────────────────

    @staticmethod
    def _crash_reason(text: str) -> str:
        """The line that says why pytest died, for the log and the agent."""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in reversed(lines):
            if re.search(r"(Error|Exception)\b|^ERROR:|INTERNALERROR", ln):
                return ln[:300]
        return lines[-1][:300] if lines else "pytest produced no output"

    def _crash_result(self, combined: str, command: List[str], exit_code: Optional[int]) -> TestResult:
        """
        pytest died before counting anything. When the traceback points into
        the project (its conftest, its package import), the code under test is
        broken — an edit that breaks the import must fail, so it counts as one
        error. Otherwise the environment stopped pytest: no evidence.
        """
        # The sandbox denying a file is environmental even when project code
        # (a conftest reading it) is where the error surfaced.
        denied = re.search(r"PermissionError|Operation not permitted|\bEPERM\b", combined)
        if not denied and self._crash_in_project(combined):
            reason = self._crash_reason(combined)
            logger.info("[test_runner] pytest crashed in project code — %s", reason)
            return TestResult(
                errors=1,
                pass_rate=0.0,
                raw_output=f"pytest crashed loading project code: {reason}\n\n{combined}",
                test_command=command,
            )
        return self._infra_result(combined, command, exit_code)

    def _project_roots(self) -> List[str]:
        roots = {str(self.project_root.resolve()), os.path.realpath(self.project_root)}
        mount = getattr(self.sandbox, "MOUNT", None)  # Docker: the container's path
        if isinstance(mount, str) and mount:
            roots.add(mount)
        return [r.rstrip(os.sep) + os.sep for r in roots]

    def _crash_in_project(self, text: str) -> bool:
        """True when a traceback frame or conftest path names a project file."""
        paths = re.findall(r'File "([^"]+)"', text)
        paths += re.findall(r"conftest '([^']+)'", text)
        paths += re.findall(r"^(?:INTERNALERROR> )?\s*([^\s:\"']+\.py):\d+: in ", text, re.MULTILINE)
        roots = self._project_roots()
        for path in paths:
            if "site-packages" in path or path.startswith("<"):
                continue
            if os.path.isabs(path):
                if any(path.startswith(r) for r in roots):
                    return True
            elif (self.project_root / path).is_file():
                return True
        return False

    def _infra_result(self, combined: str, command: List[str], exit_code: Optional[int]) -> TestResult:
        reason = self._crash_reason(combined)
        if exit_code is not None:
            reason = f"pytest exit code {exit_code}: {reason}"
        logger.warning("[test_runner] tests could not run — %s", reason)
        return TestResult(
            raw_output=combined,
            test_command=command,
            no_tests_found=True,
            infra_error=True,
            infra_reason=reason,
        )

    def _parse_pytest(
        self, stdout: str, stderr: str, command: List[str], exit_code: Optional[int] = None
    ) -> TestResult:
        """Parse pytest --tb=short -q output."""
        combined = f"{stdout}\n{stderr}"
        if "\x1b[" in combined:
            combined = re.sub(r"\x1b\[[0-9;]*m", "", combined)

        # pytest outputs summary lines like:
        #   "3 passed, 1 failed, 2 errors in 0.12s"
        #   "3 passed in 0.12s"
        #   "1 failed, 1 passed in 0.12s"  ← order varies!
        #   "no tests ran in 0.01s"
        # Search each component independently so order doesn't matter.
        summary_match = re.search(r"in\s+[\d.]+s", combined)
        if summary_match or "no tests ran" in combined.lower():
            # Count from pytest's final summary line when there is one: the
            # short summary above it ("assert 1 == 0\nFAILED tests/...") would
            # otherwise read as "0 failed". Same line, lower case, one space.
            final = [ln for ln in combined.splitlines()
                     if re.search(r"\d+ (passed|failed|errors?)\b", ln)
                     and re.search(r"\bin\s+[\d.]+s\b", ln)]
            scope = final[-1] if final else combined
            passed_match = re.search(r"(\d+)[ \t]+passed", scope, re.IGNORECASE)
            failed_match = re.search(r"(\d+)[ \t]+failed", scope, re.IGNORECASE)
            errors_match = re.search(r"(\d+)[ \t]+errors?", scope, re.IGNORECASE)

            passed = int(passed_match.group(1)) if passed_match else 0
            failed = int(failed_match.group(1)) if failed_match else 0
            errors = int(errors_match.group(1)) if errors_match else 0
            total = passed + failed + errors

            if "no tests ran" in combined.lower() and total == 0:
                return TestResult(
                    raw_output=combined,
                    test_command=command,
                    no_tests_found=True,
                )
            if total == 0 and (
                exit_code in _PYTEST_CRASH_CODES or self._looks_crashed(combined)
            ):
                # A crash whose traceback happens to contain "in 1.0s".
                return self._crash_result(combined, command, exit_code)

            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=errors,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        # No summary line: pytest died before reporting (config discovery,
        # plugin load, internal or usage error). Nothing was counted.
        if (
            exit_code in _PYTEST_CRASH_CODES
            or self._looks_crashed(combined)
            or "error" in combined.lower()
        ):
            return self._crash_result(combined, command, exit_code)

        # Fallback: generic regex
        return self._parse_generic(stdout, stderr, command)

    @staticmethod
    def _looks_crashed(text: str) -> bool:
        return (
            "INTERNALERROR" in text
            or "ERROR: usage" in text
            or "Traceback (most recent call last)" in text
        )

    def _parse_jest(self, stdout: str, stderr: str, command: List[str]) -> TestResult:
        """Parse jest output."""
        combined = f"{stdout}\n{stderr}"

        # "Tests: 2 passed, 1 failed, 3 total"
        test_match = re.search(
            r"Tests:\s*(?:(\d+)\s+passed)?(?:,\s*)?(?:(\d+)\s+failed)?(?:,\s*)?(?:(\d+)\s+total)?",
            combined,
        )
        if test_match:
            passed = int(test_match.group(1) or 0)
            failed = int(test_match.group(2) or 0)
            errors = 0
            total = passed + failed + errors
            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=errors,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        # "Test Suites: 2 passed, 1 failed"
        suite_match = re.search(
            r"Test Suites:\s*(?:(\d+)\s+passed)?(?:,\s*)?(?:(\d+)\s+failed)?",
            combined,
        )
        if suite_match:
            passed = int(suite_match.group(1) or 0)
            failed = int(suite_match.group(2) or 0)
            total = passed + failed
            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=0,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        return self._parse_generic(stdout, stderr, command)

    def _parse_vitest(self, stdout: str, stderr: str, command: List[str]) -> TestResult:
        """Parse vitest output."""
        combined = f"{stdout}\n{stderr}"

        # "Tests  3 passed | 1 failed | 4 total"
        match = re.search(
            r"Tests\s+(?:(\d+)\s+passed)?(?:\s*\|\s*)?(?:(\d+)\s+failed)?",
            combined,
        )
        if match:
            passed = int(match.group(1) or 0)
            failed = int(match.group(2) or 0)
            total = passed + failed
            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=0,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        return self._parse_generic(stdout, stderr, command)

    def _parse_generic(self, stdout: str, stderr: str, command: List[str]) -> TestResult:
        """Fallback parser — regex for passed / failed / errors."""
        combined = f"{stdout}\n{stderr}"

        passed_match = re.search(r"(\d+)\s+passed", combined, re.IGNORECASE)
        failed_match = re.search(r"(\d+)\s+failed", combined, re.IGNORECASE)
        errors_match = re.search(r"(\d+)\s+errors?", combined, re.IGNORECASE)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0
        total = passed + failed + errors

        pass_rate = passed / total if total > 0 else 0.0

        return TestResult(
            passed=passed,
            failed=failed,
            errors=errors,
            pass_rate=round(pass_rate, 4),
            raw_output=combined,
            test_command=command,
        )
