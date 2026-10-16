"""D17 transactional merge-back: plan (applied / skipped / conflicted), rollback per step."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scaffold.agent.app import gitops


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    for name in ("a.py", "b.py", "c.py"):
        (r / name).write_text(f"{name} = 0\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


def _branch(repo: Path, name: str, files: dict) -> None:
    _git(repo, "checkout", "-q", "-b", name)
    for path, text in files.items():
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{name} work")
    _git(repo, "checkout", "-q", "main")


def _commit_on_main(repo: Path, files: dict) -> None:
    for path, text in files.items():
        (repo / path).write_text(text)
    _git(repo, "commit", "-q", "-am", "user work")


def test_plan_reports_applied_skipped_and_conflicted_without_touching_anything(repo):
    _branch(repo, "awos/1", {"a.py": "a = 1\n", "b.py": "b = 1\n", "c.py": "c = 1\n", "d.py": "d\n"})
    _commit_on_main(repo, {"b.py": "b = 1\n", "c.py": "c = 2\n"})   # b same as branch, c differs
    head = _git(repo, "rev-parse", "HEAD")
    plan = gitops.plan_merge(repo, "awos/1")
    assert plan is not None
    assert plan.applied == ["a.py", "d.py"]
    assert plan.skipped == ["b.py"]
    assert plan.conflicts == ["c.py"]
    assert _git(repo, "rev-parse", "HEAD") == head and _git(repo, "status", "--porcelain") == ""


def test_conflicting_merge_is_refused_up_front_with_paths(repo):
    _branch(repo, "awos/2", {"a.py": "a = 1\n", "c.py": "c = 1\n"})
    _commit_on_main(repo, {"c.py": "c = 2\n"})
    head = _git(repo, "rev-parse", "HEAD")
    res = gitops.merge_branch(repo, "awos/2")
    assert not res.ok and res.conflicts == ["c.py"] and res.applied == ["a.py"]
    assert "c.py" in res.message and "nothing was changed" in res.message
    assert _git(repo, "rev-parse", "HEAD") == head and _git(repo, "status", "--porcelain") == ""
    assert not res.rolled_back      # refused before anything changed


def test_clean_merge_reports_applied_and_skipped(repo):
    _branch(repo, "awos/3", {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    _commit_on_main(repo, {"b.py": "b = 1\n"})
    res = gitops.merge_branch(repo, "awos/3", "do things")
    assert res.ok, res.message
    assert res.applied == ["a.py"] and res.skipped == ["b.py"] and res.conflicts == []
    assert (repo / "a.py").read_text() == "a = 1\n"


def test_dirty_file_the_merge_skips_does_not_block(repo):
    _branch(repo, "awos/4", {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    _commit_on_main(repo, {"b.py": "b = 1\n"})
    (repo / "b.py").write_text("b = 1  # my wip\n")     # dirty, but the merge leaves b.py alone
    res = gitops.merge_branch(repo, "awos/4")
    assert res.ok, res.message
    assert (repo / "b.py").read_text() == "b = 1  # my wip\n"


def test_failing_follow_up_step_rolls_the_merge_back(repo):
    _branch(repo, "awos/5", {"a.py": "a = 1\n"})
    (repo / "c.py").write_text("c = 0  # unrelated wip\n")
    head = _git(repo, "rev-parse", "HEAD")
    seen = []

    def record(new_head):
        seen.append(new_head)
        raise RuntimeError("queue locked")

    res = gitops.merge_branch(repo, "awos/5", on_merged=record)
    assert not res.ok and res.rolled_back and "queue locked" in res.message
    assert seen and seen[0] != head
    assert _git(repo, "rev-parse", "HEAD") == head
    assert (repo / "a.py").read_text() == "a.py = 0\n"
    assert (repo / "c.py").read_text() == "c = 0  # unrelated wip\n"   # user's edit survives
    assert gitops.merge_branch(repo, "awos/5", on_merged=lambda h: None).ok   # retry works


def test_merge_step_failure_restores_head(repo, monkeypatch):
    """git merge itself fails after the plan said clean (e.g. a hook): HEAD is put back."""
    _branch(repo, "awos/6", {"a.py": "a = 1\n"})
    head = _git(repo, "rev-parse", "HEAD")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-merge-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    res = gitops.merge_branch(repo, "awos/6")
    assert not res.ok and res.rolled_back
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""


def test_older_git_without_merge_tree_falls_back(repo, monkeypatch):
    _branch(repo, "awos/7", {"a.py": "a = 1\n"})
    monkeypatch.setattr(gitops, "plan_merge", lambda r, b: None)
    res = gitops.merge_branch(repo, "awos/7")
    assert res.ok and res.applied == ["a.py"]
