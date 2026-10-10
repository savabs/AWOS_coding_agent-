"""
TestRunner discovery: changed files map to tests by name, an empty mapping
falls back to the whole visible suite, and no_tests_found means the project
truly has no tests. These run real pytest subprocesses on tiny projects.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
from test_runner import TestRunner, _SAFETY_ENV_VAR  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_tests(monkeypatch):
    monkeypatch.setenv(_SAFETY_ENV_VAR, "1")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _pkg_project(root: Path) -> Path:
    """No pytest config; source and test names do not match (like sqlparse)."""
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/sql.py", "def name(s):\n    return s.split('.')[-1]\n")
    _write(root, "tests/__init__.py", "")
    _write(root, "tests/test_tokenize.py",
           "from pkg.sql import name\n\ndef test_a():\n    assert name('a.b') == 'b'\n\n"
           "def test_b():\n    assert name('x') == 'x'\n")
    _write(root, "tests/test_util.py", "def test_c():\n    assert True\n")
    _write(root, "pyproject.toml", "[project]\nname = 'pkg'\n[tool.ruff]\n")
    return root


def test_unmatched_names_without_config_run_the_full_suite(tmp_path):
    root = _pkg_project(tmp_path)
    result = TestRunner(str(root)).run(changed_files=["pkg/sql.py"])
    assert not result.no_tests_found
    assert result.mode == "full"
    assert (result.passed, result.failed) == (3, 0)
    assert result.test_command[-1] == "tests"


def test_matched_names_run_only_the_mapped_tests(tmp_path):
    root = _pkg_project(tmp_path)
    _write(root, "pkg/util.py", "X = 1\n")
    result = TestRunner(str(root)).run(changed_files=["pkg/util.py"])
    assert result.mode == "mapped"
    assert result.passed == 1
    assert result.test_command[-1] == str(Path("tests") / "test_util.py")


def test_changed_test_file_is_mapped_to_itself(tmp_path):
    root = _pkg_project(tmp_path)
    assert TestRunner(str(root)).map_tests(["tests/test_tokenize.py"]) == ["tests/test_tokenize.py"]


def test_absolute_changed_paths_are_mapped(tmp_path):
    root = _pkg_project(tmp_path)
    _write(root, "pkg/util.py", "X = 1\n")
    runner = TestRunner(str(root))
    assert runner.map_tests([str(root / "pkg" / "util.py")]) == [str(Path("tests") / "test_util.py")]


def test_no_changed_files_runs_the_full_suite(tmp_path):
    root = _pkg_project(tmp_path)
    result = TestRunner(str(root)).run()
    assert result.mode == "full" and result.passed == 3


def test_failures_are_reported_in_full_mode(tmp_path):
    root = _pkg_project(tmp_path)
    _write(root, "pkg/sql.py", "def name(s):\n    return s.split('.')[0]\n")
    result = TestRunner(str(root)).run(changed_files=["pkg/sql.py"])
    assert result.mode == "full"
    assert (result.passed, result.failed) == (2, 1)


def test_pytest_testpaths_config_is_respected(tmp_path):
    root = tmp_path
    _write(root, "lib/core.py", "def f():\n    return 1\n")
    _write(root, "checks/test_behaviour.py",
           "import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))\n"
           "from core import f\n\ndef test_f():\n    assert f() == 1\n")
    # A test outside testpaths that would fail if collected.
    _write(root, "scratch/test_broken.py", "def test_x():\n    assert False\n")
    _write(root, "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = [\"checks\"]\n")
    runner = TestRunner(str(root))
    assert runner._full_suite_targets() == []
    result = runner.run(changed_files=["lib/core.py"])
    assert result.mode == "full"
    assert (result.passed, result.failed) == (1, 0)


def test_setup_cfg_testpaths_is_detected(tmp_path):
    _write(tmp_path, "setup.cfg", "[metadata]\nname = x\n\n[tool:pytest]\ntestpaths = t\n")
    assert TestRunner(str(tmp_path))._configured_testpaths()
    _write(tmp_path, "setup.cfg", "[tool:pytest]\naddopts = -q\n\n[other]\ntestpaths = t\n")
    assert not TestRunner(str(tmp_path))._configured_testpaths()


def test_mapped_tests_that_collect_nothing_fall_back_to_full(tmp_path):
    root = _pkg_project(tmp_path)
    _write(root, "pkg/util.py", "X = 1\n")
    _write(root, "tests/test_util.py", "# no tests here\n")
    result = TestRunner(str(root)).run(changed_files=["pkg/util.py"])
    assert result.mode == "full"
    assert result.passed == 2 and not result.no_tests_found


def test_truly_testless_project_reports_no_tests(tmp_path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/core.py", "X = 1\n")
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'pkg'\n")
    result = TestRunner(str(tmp_path)).run(changed_files=["pkg/core.py"])
    assert result.no_tests_found
    assert result.mode == ""


def test_configured_project_with_no_tests_reports_no_tests(tmp_path):
    _write(tmp_path, "pytest.ini", "[pytest]\n")
    _write(tmp_path, "core.py", "X = 1\n")
    result = TestRunner(str(tmp_path)).run(changed_files=["core.py"])
    assert result.no_tests_found
    assert result.mode == "full"


def test_vendored_dirs_are_not_searched(tmp_path):
    _write(tmp_path, ".venv/lib/test_vendor.py", "def test_v():\n    pass\n")
    _write(tmp_path, "node_modules/x/test_n.py", "def test_n():\n    pass\n")
    assert TestRunner(str(tmp_path)).find_test_files() == []
    assert TestRunner(str(tmp_path)).detect() is None


def test_ansi_colored_summary_is_parsed():
    out = "\x1b[32m\x1b[1m4 passed\x1b[0m, \x1b[31m1 failed\x1b[0m\x1b[32m in 0.12s\x1b[0m"
    result = TestRunner(".")._parse_pytest(out, "", [])
    assert (result.passed, result.failed) == (4, 1)


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
def test_full_suite_runs_inside_seatbelt(tmp_path):
    from sandbox import SeatbeltSandbox, seatbelt_available

    if not seatbelt_available():
        pytest.skip("sandbox-exec unavailable")
    root = _pkg_project(tmp_path)
    sb = SeatbeltSandbox(str(root))
    result = TestRunner(str(root), sandbox=sb).run(changed_files=["pkg/sql.py"])
    assert result.mode == "full"
    assert (result.passed, result.failed, result.no_tests_found) == (3, 0, False)
    assert not (root / ".pytest_cache").exists()
