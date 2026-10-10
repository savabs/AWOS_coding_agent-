"""
pytest that cannot run is not "0 passed, 0 failed".

Regression for real_jsonpointer: a project with no pytest config, whose job
workspace sits under a dir that has one (the AWOS checkout's pytest.ini).
pytest's config search walked up into the seatbelt-denied parent, crashed with
PermissionError, the parser returned a clean 0/0, the task was judged failed
and a correct edit was rolled back.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
from test_runner import _NO_CONFIG_ARGS, _SAFETY_ENV_VAR, TestRunner  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_tests(monkeypatch):
    monkeypatch.setenv(_SAFETY_ENV_VAR, "1")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonpointer_like(root: Path) -> Path:
    """Single root module, tests/ dir, setup.cfg with only [flake8]."""
    _write(root, "mod.py", "def f():\n    return 1\n")
    _write(root, "setup.cfg", "[flake8]\nmax-line-length = 120\n")
    _write(root, "tests/test_mod.py",
           "import unittest\nfrom mod import f\n\n"
           "class T(unittest.TestCase):\n"
           "    def test_a(self):\n        self.assertEqual(f(), 1)\n"
           "    def test_b(self):\n        self.assertTrue(f())\n")
    return root


class _Recorder:
    """Sandbox stand-in that records the command and replies with a pass."""

    def __init__(self):
        self.commands = []

    def run(self, command, timeout_sec=0):
        from types import SimpleNamespace
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, stdout="2 passed in 0.01s\n", stderr="", timed_out=False)


# ── Step 1: no-config projects stop pytest's upward search ──────────────

def test_no_config_project_gets_the_search_stopping_args(tmp_path):
    root = _jsonpointer_like(tmp_path)
    sb = _Recorder()
    TestRunner(str(root), sandbox=sb).run(changed_files=["mod.py"])
    assert sb.commands and all(" ".join(_NO_CONFIG_ARGS) in c for c in sb.commands)


@pytest.mark.parametrize("name, text", [
    ("pytest.ini", "[pytest]\n"),
    ("pyproject.toml", "[tool.pytest.ini_options]\naddopts = ''\n"),
    ("tox.ini", "[pytest]\n"),
    ("setup.cfg", "[tool:pytest]\n"),
])
def test_configured_project_args_are_unchanged(tmp_path, name, text):
    root = _jsonpointer_like(tmp_path)
    _write(root, name, text)
    sb = _Recorder()
    TestRunner(str(root), sandbox=sb).run(changed_files=["mod.py"])
    assert sb.commands
    for command in sb.commands:
        assert "--rootdir" not in command and "--confcutdir" not in command
        assert " -c " not in command


def test_pyproject_without_a_pytest_section_is_not_config(tmp_path):
    # pytest walks up past such a file, so it needs the args too.
    root = _jsonpointer_like(tmp_path)
    _write(root, "pyproject.toml", "[project]\nname = 'x'\n")
    assert TestRunner(str(root))._has_pytest_config() is False


def test_no_config_args_still_run_the_suite_on_the_host(tmp_path):
    root = _jsonpointer_like(tmp_path)
    result = TestRunner(str(root)).run(changed_files=["mod.py"])
    assert (result.passed, result.failed, result.infra_error) == (2, 0, False)
    assert list(root.iterdir()) and not (root / "pytest.ini").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
def test_config_in_a_denied_parent_no_longer_crashes_pytest(tmp_path):
    from sandbox import SeatbeltSandbox, seatbelt_available

    if not seatbelt_available():
        pytest.skip("sandbox-exec unavailable")
    # tmp_path is under /private/var/folders, which the sandbox denies: a
    # config file there is what the AWOS checkout's pytest.ini was.
    _write(tmp_path, "pytest.ini", "[pytest]\n")
    root = _jsonpointer_like(tmp_path / "job" / "ws")
    sb = SeatbeltSandbox(str(root))
    try:
        for changed in (["mod.py"], []):
            result = TestRunner(str(root), sandbox=sb).run(changed_files=changed)
            assert (result.passed, result.failed, result.infra_error) == (2, 0, False), result.raw_output
    finally:
        sb.close()


# ── Step 2: a crashed pytest is flagged, not a clean 0/0 ────────────────

CRASH = (
    "Traceback (most recent call last):\n"
    '  File "_pytest/config/findpaths.py", line 120, in locate_config\n'
    "PermissionError: [Errno 1] Operation not permitted: '/Users/x/repo/pytest.ini'\n"
)


@pytest.mark.parametrize("stdout, stderr, code", [
    ("", CRASH, 1),                                          # config discovery crash
    ("", CRASH, None),                                       # exit code unknown
    ("INTERNALERROR> Traceback (most recent call last):\nINTERNALERROR> KeyError: 'x'\n", "", 3),
    ("", "ERROR: usage: pytest [options]\npytest: error: unrecognized arguments: --bogus\n", 4),
    ("", "something odd\n", 2),
])
def test_crashed_runs_are_infra_errors(stdout, stderr, code):
    result = TestRunner(".")._parse_pytest(stdout, stderr, ["pytest"], exit_code=code)
    assert result.infra_error is True
    assert result.no_tests_found is True  # every consumer: no evidence, not red
    assert result.infra_reason
    assert (result.passed, result.failed) == (0, 0)


def test_crash_reason_names_the_error():
    result = TestRunner(".")._parse_pytest("", CRASH, [], exit_code=1)
    assert "PermissionError" in result.infra_reason


def test_counted_results_are_not_infra_errors():
    runner = TestRunner(".")
    red = runner._parse_pytest("1 failed, 3 passed in 0.10s\n", "", [], exit_code=1)
    assert (red.passed, red.failed, red.infra_error, red.no_tests_found) == (3, 1, False, False)
    # A collection error the edit caused is counted, so it stays a red result.
    coll = runner._parse_pytest("ERROR tests/test_a.py\n1 error in 0.05s\n", "", [], exit_code=2)
    assert (coll.errors, coll.infra_error) == (1, False)
    none = runner._parse_pytest("no tests ran in 0.01s\n", "", [], exit_code=5)
    assert none.no_tests_found and not none.infra_error
    skipped = runner._parse_pytest("2 skipped in 0.01s\n", "", [], exit_code=0)
    assert not skipped.infra_error


def test_sandbox_exit_code_reaches_the_parser(tmp_path):
    from types import SimpleNamespace

    root = _jsonpointer_like(tmp_path)

    class _Crashing:
        def run(self, command, timeout_sec=0):
            return SimpleNamespace(exit_code=4, stdout="", stderr="ERROR: usage: bad\n", timed_out=False)

    result = TestRunner(str(root), sandbox=_Crashing()).run(changed_files=["mod.py"])
    assert result.infra_error and "exit code 4" in result.infra_reason


# ── A crash inside project code is a real failure, not "could not run" ──

def test_conftest_import_error_from_a_project_module_is_a_counted_error(tmp_path):
    # An edit that breaks the package import must still fail the task.
    root = _jsonpointer_like(tmp_path)
    _write(root, "tests/conftest.py", "import mod\n")
    _write(root, "mod.py", "def f(:\n    return 1\n")  # SyntaxError
    result = TestRunner(str(root)).run(changed_files=["mod.py"])
    assert result.infra_error is False and result.no_tests_found is False
    assert result.errors >= 1 and result.pass_rate == 0.0
    assert "SyntaxError" in result.raw_output


def test_conftest_import_error_text_is_a_counted_error(tmp_path):
    root = _jsonpointer_like(tmp_path)
    _write(root, "tests/conftest.py", "import mod\n")
    out = (f"ImportError while loading conftest '{root.resolve()}/tests/conftest.py'.\n"
           "tests/conftest.py:1: in <module>\n    import mod\n"
           "mod.py:1: in <module>\n    import nope\n"
           "E   ModuleNotFoundError: No module named 'nope'\n")
    result = TestRunner(str(root))._parse_pytest("", out, [], exit_code=4)
    assert (result.errors, result.infra_error, result.no_tests_found) == (1, False, False)
    assert "ModuleNotFoundError" in result.raw_output


def test_project_traceback_without_exit_code_is_a_counted_error(tmp_path):
    root = _jsonpointer_like(tmp_path)
    out = ("Traceback (most recent call last):\n"
           f'  File "{root.resolve()}/mod.py", line 3, in <module>\n'
           "NameError: name 'x' is not defined\n")
    result = TestRunner(str(root))._parse_pytest("", out, [])
    assert (result.errors, result.infra_error) == (1, False)


def test_permission_error_on_a_parent_pytest_ini_is_infra(tmp_path):
    root = _jsonpointer_like(tmp_path / "ws")
    out = ("Traceback (most recent call last):\n"
           '  File "/venv/lib/python3.12/site-packages/_pytest/config/findpaths.py", line 120, in locate_config\n'
           f"PermissionError: [Errno 1] Operation not permitted: '{tmp_path}/pytest.ini'\n")
    result = TestRunner(str(root))._parse_pytest("", out, [], exit_code=1)
    assert (result.infra_error, result.no_tests_found, result.errors) == (True, True, 0)


def test_internal_error_outside_the_project_is_infra(tmp_path):
    root = _jsonpointer_like(tmp_path)
    out = ("INTERNALERROR> Traceback (most recent call last):\n"
           'INTERNALERROR>   File "/venv/lib/python3.12/site-packages/_pytest/main.py", line 283, in wrap_session\n'
           "INTERNALERROR> KeyError: 'x'\n")
    result = TestRunner(str(root))._parse_pytest(out, "", [], exit_code=3)
    assert (result.infra_error, result.errors) == (True, 0)
