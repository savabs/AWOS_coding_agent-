"""
v0.2.2: AWOS on real repositories (docs/launch/demos/README.md, lessons 1-7).

1. a failed job hands off EVERYTHING it did as awos/<id>-failed (atomic);
2. the sandbox lets pytest stat .env.example (and read example env files);
3. tests run in the PROJECT's env (venv/uv/poetry/config), workspace first;
4. only NEW failures count: pre-existing ones are checked at the base commit;
5. the host's suite line is honest (timeouts, targeted tests, baseline);
6. commit subject and the default branch in review hints.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scaffold.agent import baseline_tests, project_env
from scaffold.agent.host import handoff, worker
from scaffold.agent.sandbox import _dotenv_files, build_seatbelt_profile, is_secret_dotenv
from scaffold.agent.test_runner import TestRunner

PYVER = f"python{sys.version_info.major}.{sys.version_info.minor}"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, msg: str = "base") -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _fake_venv(root: Path, name: str = ".venv", pytest_installed: bool = True) -> Path:
    """A venv-shaped dir whose python is this interpreter (which has pytest)."""
    (root / name / "bin").mkdir(parents=True)
    py = root / name / "bin" / "python"
    # A wrapper, not a symlink: a bare symlink outside a venv loses this env's site-packages.
    py.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
    py.chmod(0o755)
    sp = root / name / "lib" / PYVER / "site-packages"
    sp.mkdir(parents=True)
    if pytest_installed:
        (sp / "pytest").mkdir()
    return py


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in (project_env.ENV_VAR, project_env.PROJECT_ROOT_VAR, baseline_tests.ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWOS_HOME", str(tmp_path / "awos_home"))
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    project_env.clear_cache()
    baseline_tests.clear_cache()
    yield
    project_env.clear_cache()
    baseline_tests.clear_cache()


# ── 2. sandbox: .env.example is not a secret ─────────────────────────────────


def test_example_env_files_are_not_secret():
    for name in (".env.example", ".env.sample", ".env.template", ".env.dist"):
        assert not is_secret_dotenv(name), name
    for name in (".env", ".env.local", ".env.production", ".env.dev"):
        assert is_secret_dotenv(name), name


def test_profile_denies_content_not_metadata(tmp_path):
    prof = build_seatbelt_profile(tmp_path, tmp_path / "t", [], [], False)
    assert "(deny file-read-data file-write* (regex" in prof
    assert "(deny file-read* file-write* (regex" not in prof   # stat stays allowed
    assert "example|sample|template" in prof


def test_docker_hides_only_secret_env_files(tmp_path):
    for name in (".env", ".env.local", ".env.example", ".env.sample"):
        (tmp_path / name).write_text("X=1\n")
    assert [p.name for p in _dotenv_files(tmp_path)] == [".env", ".env.local"]


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
def test_pytest_collects_in_a_repo_with_env_example(tmp_path):
    from scaffold.agent.sandbox import SeatbeltSandbox

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".env.example").write_text("API_KEY=put-yours-here\n")
    (ws / ".env").write_text("API_KEY=real-secret-value\n")
    (ws / ".env.local").write_text("API_KEY=other-secret\n")
    (ws / "pytest.ini").write_text("[pytest]\n")
    (ws / "test_env_files.py").write_text(
        "import os, pathlib, pytest\n"
        "def test_example_readable():\n"
        "    assert 'put-yours-here' in pathlib.Path('.env.example').read_text()\n"
        "def test_stat_ok_but_secrets_hidden():\n"
        "    assert os.path.exists('.env') and os.path.exists('.env.local')\n"
        "    for name in ('.env', '.env.local'):\n"
        "        with pytest.raises(PermissionError):\n"
        "            pathlib.Path(name).read_text()\n")
    sb = SeatbeltSandbox(ws)
    try:
        res = TestRunner(project_root=str(ws), sandbox=sb).run()
    finally:
        sb.close()
    assert not res.infra_error, res.raw_output[-800:]
    assert (res.passed, res.failed, res.errors) == (2, 0, 0), res.raw_output[-800:]


# ── 3. the project's environment ─────────────────────────────────────────────


def test_fallback_is_awos_python_with_a_note(tmp_path):
    env = project_env.detect(tmp_path)
    assert env.source == "awos" and env.argv == [sys.executable, "-m", "pytest"]
    assert any("AWOS's own Python" in n for n in env.notes)
    assert "no project env found" in env.describe()


def test_explicit_command_wins(tmp_path, monkeypatch):
    _fake_venv(tmp_path)
    monkeypatch.setenv("AWOS_TEST_COMMAND", "uv run pytest -x")
    env = project_env.detect(tmp_path)
    assert env.source == "AWOS_TEST_COMMAND" and env.argv == ["uv", "run", "pytest", "-x"]


def test_per_repo_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = Path(os.environ["AWOS_HOME"])
    home.mkdir(parents=True)
    (home / "projects.json").write_text(json.dumps({str(repo): {"test_command": "tox -e py -- "}}))
    env = project_env.detect(repo)
    assert env.source == "config" and env.argv == ["tox", "-e", "py", "--"]


def test_repo_venv_is_used(tmp_path):
    py = _fake_venv(tmp_path)
    env = project_env.detect(tmp_path)
    assert env.source == ".venv" and env.argv == [str(py), "-m", "pytest"]
    assert any(r.endswith(".venv") for r in env.read_roots)
    assert TestRunner(project_root=str(tmp_path)).pytest_command()[0] == str(py)


def test_venv_without_pytest_is_skipped(tmp_path):
    _fake_venv(tmp_path, pytest_installed=False)
    env = project_env.detect(tmp_path)
    assert env.source == "awos" and any("has no pytest" in n for n in env.notes)


def test_job_workspace_finds_the_checkouts_venv(tmp_path):
    """The host's workspace is a linked worktree with no .venv of its own."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    _commit(repo)
    py = _fake_venv(repo)
    ws = tmp_path / "jobs" / "j1" / "workspace"
    _git(repo, "worktree", "add", "-q", "--detach", str(ws), "HEAD")
    env = project_env.detect(ws)
    assert env.source == ".venv" and env.argv[0] == str(py)
    assert env.short() == ".venv/bin/python -m pytest"


def test_uv_lock_uses_uv_run(tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text("")
    monkeypatch.setattr(project_env.shutil, "which", lambda name: f"/opt/bin/{name}")
    env = project_env.detect(tmp_path)
    assert env.source == "uv"
    assert env.argv[1:] == ["run", "--frozen", "--project", str(tmp_path.resolve()), "pytest"]


def test_editable_install_pointing_elsewhere_is_noted(tmp_path):
    checkout, ws = tmp_path / "checkout", tmp_path / "ws"
    checkout.mkdir()
    ws.mkdir()
    _fake_venv(ws)
    dist = ws / ".venv" / "lib" / PYVER / "site-packages" / "mypkg-1.0.dist-info"
    dist.mkdir()
    (dist / "direct_url.json").write_text(json.dumps(
        {"url": f"file://{checkout}", "dir_info": {"editable": True}}))
    env = project_env.detect(ws)
    assert any("editable install of mypkg points at" in n for n in env.notes)


def test_workspace_code_comes_first(tmp_path, monkeypatch):
    """An editable install of the user's checkout must not shadow the job's code."""
    checkout, ws = tmp_path / "checkout", tmp_path / "ws"
    for root, value in ((checkout, "checkout"), (ws, "workspace")):
        (root / "src" / "mypkg").mkdir(parents=True)
        (root / "src" / "mypkg" / "__init__.py").write_text(f"WHERE = {value!r}\n")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_where.py").write_text(
        "import mypkg\n\ndef test_where():\n    assert mypkg.WHERE == 'workspace'\n")
    _fake_venv(ws)
    # Stand-in for the editable install: the checkout's src is on the path already.
    monkeypatch.setenv("PYTHONPATH", str(checkout / "src"))
    env = project_env.detect(ws)
    assert env.env_for(ws)["PYTHONPATH"].split(os.pathsep)[:2] == [str(ws), str(ws / "src")]
    res = TestRunner(project_root=str(ws)).run()
    assert (res.passed, res.failed) == (1, 0), res.raw_output[-600:]


# ── 4. baseline: only new failures count ─────────────────────────────────────


def _repo_with_preexisting_failure(tmp_path, extra=None):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    for rel, text in (extra or {}).items():
        (repo / rel).write_text(text)
    _git(repo, "init", "-q")
    (repo / "pytest.ini").write_text("[pytest]\n")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_needs_optional_dep():\n    import pyarrow_not_installed  # noqa: F401\n\n"
        "def test_zero():\n    assert add(0, 0) == 0\n")
    base = _commit(repo)
    ws = tmp_path / "job" / "workspace"
    bl = tmp_path / "job" / "baseline"
    _git(repo, "worktree", "add", "-q", "--detach", str(ws), base)
    _git(repo, "worktree", "add", "-q", "--detach", str(bl), base)
    return repo, ws, bl, base


def test_preexisting_failure_does_not_count(tmp_path, monkeypatch):
    repo, ws, bl, _ = _repo_with_preexisting_failure(tmp_path)
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "tests" / "test_calc.py").write_text(
        (ws / "tests" / "test_calc.py").read_text()
        + "\ndef test_add():\n    assert add(2, 2) == 4\n")
    without = TestRunner(project_root=str(ws)).run(changed_files=["calc.py"])
    assert without.failed == 1                        # the old behaviour: counted
    monkeypatch.setenv("AWOS_BASELINE_WORKSPACE", str(bl))
    res = TestRunner(project_root=str(ws)).run(changed_files=["calc.py"])
    assert (res.passed, res.failed, res.errors) == (2, 0, 0), res.raw_output[-800:]
    assert res.preexisting == ["tests/test_calc.py::test_needs_optional_dep"]
    assert res.baseline_checked and "pre-existing failures: 1" in res.raw_output


def test_new_failures_still_count(tmp_path, monkeypatch):
    repo, ws, bl, _ = _repo_with_preexisting_failure(tmp_path)
    (ws / "calc.py").write_text("def add(a, b):\n    return 1\n")       # breaks test_zero
    (ws / "tests" / "test_new.py").write_text("def test_brand_new():\n    assert False\n")
    monkeypatch.setenv("AWOS_BASELINE_WORKSPACE", str(bl))
    res = TestRunner(project_root=str(ws)).run_targets(["tests"])
    assert res.failed == 2 and len(res.preexisting) == 1, res.raw_output[-800:]


def test_added_test_in_existing_file_is_new(tmp_path, monkeypatch):
    repo, ws, bl, _ = _repo_with_preexisting_failure(tmp_path)
    (ws / "tests" / "test_calc.py").write_text(
        (ws / "tests" / "test_calc.py").read_text() + "\ndef test_added():\n    assert 0\n")
    monkeypatch.setenv("AWOS_BASELINE_WORKSPACE", str(bl))
    res = TestRunner(project_root=str(ws)).run_targets(["tests/test_calc.py"])
    assert res.failed == 1 and res.preexisting == ["tests/test_calc.py::test_needs_optional_dep"]


def test_run_tests_tool_tells_the_agent(tmp_path, monkeypatch):
    from scaffold.agent.tools.code_edit import RunTestsTool

    repo, ws, bl, _ = _repo_with_preexisting_failure(tmp_path)
    monkeypatch.setenv("AWOS_BASELINE_WORKSPACE", str(bl))
    out = RunTestsTool(project_root=ws).execute({})
    assert out.text.startswith("1 passed, 0 failed.") and "pre-existing" in out.text


# ── 5. honest host suite line ────────────────────────────────────────────────


def test_verify_tests_reports_baseline_and_env(tmp_path):
    repo, ws, bl, base = _repo_with_preexisting_failure(tmp_path)
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = worker.verify_tests(str(ws), base_commit=base, project_root=str(repo), baseline=str(bl))
    assert (res["status"], res["passed"], res["failed"], res["preexisting"]) == ("passed", 1, 0, 1)
    assert "pre-existing failures: 1 (unchanged)" in res["line"]
    assert res["targeted"]["files"] == ["tests/test_calc.py"]
    assert "AWOS's own Python" in res["line"]                 # no venv in this repo: said so


def test_preexisting_collection_error_does_not_stop_the_suite(tmp_path):
    """Soup: 43 modules need the torch extra; pytest stopped at collection and
    the host reported '0 passed, 43 failed'."""
    extra = {"tests/test_needs_torch.py": "import torch_not_installed\n\ndef test_t():\n    pass\n"}
    repo, ws, bl, base = _repo_with_preexisting_failure(tmp_path, extra)
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    res = worker.verify_tests(str(ws), base_commit=base, project_root=str(repo), baseline=str(bl))
    assert res["full"]["status"] == "passed", res
    assert (res["full"]["passed"], res["full"]["preexisting"]) == (1, 2)
    assert res["line"].startswith("full suite: 1 passed, 0 new failures; pre-existing failures: 2")


@pytest.mark.timeout(60)
def test_verify_tests_full_suite_timeout_is_honest(tmp_path, monkeypatch):
    slow = {"tests/test_slow.py": "import time\n\ndef test_slow():\n    time.sleep(30)\n"}
    repo, ws, bl, base = _repo_with_preexisting_failure(tmp_path, slow)
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _fake_venv(repo)
    monkeypatch.setenv("AWOS_HOST_VERIFY_TIMEOUT_SEC", "4")
    res = worker.verify_tests(str(ws), base_commit=base, project_root=str(repo), baseline=str(bl))
    assert res["full"]["status"] == "timeout"
    assert res["line"].startswith("full suite timed out after 4s; targeted tests "
                                  "(tests/test_calc.py): 1 passed, 0 new failures")
    assert res["status"] == "passed" and res["env_source"] == ".venv"


# ── 1 + 6. hand-off: atomic failed branch, subject, default branch ───────────


def _handoff_repo(tmp_path, default: str = "master"):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", default)
    (repo / "calc.py").write_text("x = 1\n")
    base = _commit(repo)
    _git(repo, "checkout", "-q", "-b", "feature")         # the user is elsewhere
    ws = tmp_path / "ws"
    _git(repo, "worktree", "add", "-q", "--detach", str(ws), base)
    return repo, ws, base


def test_failed_handoff_is_labelled_and_whole(tmp_path):
    repo, ws, base = _handoff_repo(tmp_path)
    (ws / "calc.py").write_text("x = 2\n")
    (ws / "test_calc.py").write_text("def test_x():\n    pass\n")
    (ws / ".env.example").write_text("API_URL=https://example.invalid\n")
    (ws / ".env.local").write_text("SECRET=1\n")
    job = {"id": "abcdef123456", "repo_path": str(repo), "base_commit": base,
           "goal": "Fix https://github.com/o/r/issues/1 : x should be 2. Then more.",
           "budget_usd": 0.1}
    info = handoff.finish(job, str(ws), {"state": "failed", "error": "budget cap"},
                          job_dir=str(tmp_path / "jd"))
    assert info["branch"] == "awos/abcdef12-failed" and info["status"] == "handed_off_failed"
    assert set(info["files"]) == {"calc.py", "test_calc.py", ".env.example"}
    assert info["excluded"] == [".env.local"]
    subject = _git(repo, "log", "-1", "--format=%s", info["branch"])
    assert subject == "[failed] Fix x should be 2"
    report = Path(info["report_path"]).read_text()
    assert "git diff master...awos/abcdef12-failed" in report
    assert "FAILED: the job did not finish" in report


def test_done_handoff_uses_default_branch(tmp_path):
    repo, ws, base = _handoff_repo(tmp_path, default="main")
    (ws / "calc.py").write_text("x = 2\n")
    job = {"id": "fedcba987654", "repo_path": str(repo), "base_commit": base,
           "goal": "Make x two in calc.py so the totals add up again across every report page"}
    info = handoff.finish(job, str(ws), {"state": "done"}, job_dir=str(tmp_path / "jd"))
    assert info["branch"] == "awos/fedcba98" and info["status"] == "handed_off"
    assert info["summary"].endswith("git diff main...awos/fedcba98")
    subject = _git(repo, "log", "-1", "--format=%s", info["branch"])
    assert len(subject) <= 72 and subject.endswith("…") and " page" not in subject


def test_app_finds_a_job_by_its_failed_branch(tmp_path):
    from scaffold.agent.app import jobs as J
    from scaffold.agent.host.queue import JobQueue

    q = JobQueue(tmp_path / "host")
    repo = tmp_path / "r"
    repo.mkdir()
    job = q.submit("g", repo)
    assert J.find_job(q, repo, f"awos/{job.id[:8]}-failed").id == job.id


def test_checkout_paths_are_redirected_to_the_workspace(tmp_path, monkeypatch):
    """A non-src layout (lib/) installed editable from the user's checkout: no
    PYTHONPATH entry covers it, the remap shim does (sys.path, setuptools'
    editable finders, the `editables` redirector)."""
    checkout, ws = tmp_path / "checkout", tmp_path / "ws"
    for root, value in ((checkout, "checkout"), (ws, "workspace")):
        (root / "lib" / "mypkg").mkdir(parents=True)
        (root / "lib" / "mypkg" / "__init__.py").write_text(f"WHERE = {value!r}\n")
    (ws / "test_where.py").write_text(
        "import mypkg\n\ndef test_where():\n    assert mypkg.WHERE == 'workspace'\n")
    _fake_venv(ws)
    monkeypatch.setenv("PYTHONPATH", str(checkout / "lib"))   # what an editable .pth adds
    env = project_env.detect(ws, project_root=checkout)
    assert env.checkout_root == os.path.realpath(checkout)
    res = TestRunner(project_root=str(ws), test_env=env).run()
    assert (res.passed, res.failed) == (1, 0), res.raw_output[-600:]
