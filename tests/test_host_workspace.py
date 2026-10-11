"""Regressions found by the M1 host live proof (2026-10-10).

1. RunContext.step wrote records without the journal's required 'kind', so every
   worker step was lost ("journal step needs a 'kind'").
2. The child ran in the user's checkout: it branched, switched HEAD and left
   edits there. Jobs now run in a throwaway worktree at the base commit.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from scaffold.agent.host import worker
from scaffold.agent.host.journal import Journal
from scaffold.agent.host.models import JobSpec


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return repo


class _Queue:
    def __init__(self, root: Path):
        self.root = root

    def job_dir(self, jid: str) -> Path:
        d = self.root / "jobs" / jid
        d.mkdir(parents=True, exist_ok=True)
        return d


def test_step_reaches_a_real_journal(tmp_path):
    job = JobSpec(id="j1", goal="g", repo_path=str(tmp_path), attempts=1)
    q = _Queue(tmp_path / "host")
    logs: list[str] = []
    journal = Journal(q.job_dir("j1"))
    ctx = worker.RunContext(job, q, journal, lambda: False, logs.append)
    ctx.step("attempt_1_start")
    ctx.step("child_started", pid=123)
    assert not [m for m in logs if "journal append failed" in m]
    assert {"attempt_1_start", "child_started"} <= ctx.done_steps()
    rec = journal.get("child_started")
    assert rec["kind"] == worker.WORKER_STEP and rec["payload"] == {"pid": 123}


def test_workspace_isolates_the_users_checkout(tmp_path):
    repo = _repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    job = JobSpec(id="j2", goal="g", repo_path=str(repo), attempts=1)
    job_dir = tmp_path / "host" / "jobs" / "j2"
    job_dir.mkdir(parents=True)

    ws, base = worker.prepare_workspace(job, job_dir)
    assert base == head and Path(ws) == job_dir / worker.WORKSPACE_DIR
    (Path(ws) / "a.py").write_text("x = 2\n")          # the job edits its workspace
    assert (repo / "a.py").read_text() == "x = 1\n"     # the user's checkout is untouched
    assert _git(repo, "status", "--porcelain") == ""
    assert worker.prepare_workspace(job, job_dir) == (ws, base)   # a retry reuses it

    worker.remove_workspace(job, job_dir)
    assert not Path(ws).exists()
    assert str(Path(ws).resolve()) not in _git(repo, "worktree", "list")


def test_non_git_root_runs_in_place(tmp_path):
    job = JobSpec(id="j3", goal="g", repo_path=str(tmp_path), attempts=1)
    job_dir = tmp_path / "host" / "jobs" / "j3"
    job_dir.mkdir(parents=True)
    assert worker.prepare_workspace(job, job_dir) == (str(tmp_path), None)


def test_child_env_turns_off_orchestrator_branching(tmp_path):
    job = JobSpec(id="j4", goal="g", repo_path=str(tmp_path))
    assert worker.child_env(job)["AWOS_GIT_BRANCH"] == "0"


def test_goal_check_skipped_only_when_tests_exist(tmp_path, monkeypatch):
    monkeypatch.delenv("AWOS_GOAL_CHECK", raising=False)
    job = JobSpec(id="j5", goal="g", repo_path=str(tmp_path))
    assert not worker.has_tests(str(tmp_path))
    assert "AWOS_GOAL_CHECK" not in worker.child_env(job, skip_goal_check=False)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "test_a.py").write_text("def test_a():\n    pass\n")
    assert worker.has_tests(str(tmp_path))
    assert worker.child_env(job, skip_goal_check=True)["AWOS_GOAL_CHECK"] == "0"


def test_verify_tests_reports_counts(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_ok():\n    pass\n\ndef test_bad():\n    assert 0\n")
    res = worker.verify_tests(str(tmp_path))
    assert res == {"status": "failed", "passed": 1, "failed": 1, "command": "python -m pytest -q"}
    (tmp_path / "test_x.py").write_text("def test_ok():\n    pass\n")
    assert worker.verify_tests(str(tmp_path))["status"] == "passed"


def test_handoff_never_ships_caches():
    from scaffold.agent.host.handoff import _is_excluded
    for p in ("__pycache__/a.cpython-312.pyc", "pkg/__pycache__/b.pyc", "x.pyc",
              ".pytest_cache/v/cache/nodeids", "sub/.pytest_cache/README.md"):
        assert _is_excluded(p), p
    assert not _is_excluded("textutil.py")


def test_verify_tests_writes_no_bytecode(tmp_path):
    (tmp_path / "mod.py").write_text("X = 1\n")
    (tmp_path / "test_mod.py").write_text("from mod import X\n\ndef test_x():\n    assert X == 1\n")
    assert worker.verify_tests(str(tmp_path))["status"] == "passed"
    assert not list(tmp_path.rglob("*.pyc"))


def test_verify_tests_none_without_tests(tmp_path, monkeypatch):
    assert worker.verify_tests(str(tmp_path)) is None
    (tmp_path / "test_x.py").write_text("def test_ok():\n    pass\n")
    monkeypatch.setenv("AWOS_HOST_VERIFY_TESTS", "0")
    assert worker.verify_tests(str(tmp_path)) is None
