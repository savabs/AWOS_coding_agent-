"""Follow-up jobs start from the previous job's branch; merge/reject safety."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scaffold.agent.app import gitops
from scaffold.agent.app import jobs as J
from scaffold.agent.host import worker
from scaffold.agent.host.models import JobSpec
from scaffold.agent.host.queue import JobQueue


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (r / "notes.txt").write_text("hello\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


def _branch(repo: Path, name: str, path: str, text: str) -> str:
    """Make branch `name` off main with one changed file, without touching the checkout."""
    _git(repo, "checkout", "-q", "-b", name)
    (repo / path).write_text(text)
    _git(repo, "commit", "-q", "-am", f"change {path}")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return sha


# ── follow-up base ───────────────────────────────────────────────────────────

def test_follow_up_workspace_starts_from_previous_branch(repo, tmp_path):
    sha = _branch(repo, "awos/aaaaaaaa", "calc.py", "def add(a, b):\n    return a + b\n")
    q = JobQueue(tmp_path / "host")
    prev = JobSpec.new("fix add", repo)
    new = J.submit_follow_up(q, repo, prev, "also add sub()", "awos/aaaaaaaa")
    jdir = q.job_dir(new.id)
    assert (jdir / "base_ref").read_text().strip() == "awos/aaaaaaaa"
    assert (jdir / "follow_up_of").read_text().strip() == prev.id
    assert new.goal.startswith("also add sub()") and "awos/aaaaaaaa" in new.goal
    ws, base = worker.prepare_workspace(q.get(new.id), jdir)
    try:
        assert base == sha
        assert "a + b" in (Path(ws) / "calc.py").read_text()
        assert "a - b" in (repo / "calc.py").read_text()   # user's checkout untouched
    finally:
        worker.remove_workspace(q.get(new.id), jdir)


def test_without_base_ref_workspace_starts_from_head(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job = q.submit("g", repo)
    ws, base = worker.prepare_workspace(job, q.job_dir(job.id))
    try:
        assert base == _git(repo, "rev-parse", "HEAD")
    finally:
        worker.remove_workspace(job, q.job_dir(job.id))


def test_missing_base_branch_fails_clearly(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    prev = JobSpec.new("fix add", repo)
    new = J.submit_follow_up(q, repo, prev, "more", "awos/deadbeef")
    with pytest.raises(RuntimeError, match="no longer exists"):
        worker.prepare_workspace(q.get(new.id), q.job_dir(new.id))


@pytest.mark.parametrize("ref", ["--upload-pack=evil", "a b", "x;rm"])
def test_unsafe_base_ref_is_refused(repo, tmp_path, ref):
    q = JobQueue(tmp_path / "host")
    job = q.submit("g", repo)
    (q.job_dir(job.id) / "base_ref").write_text(ref)
    with pytest.raises(RuntimeError, match="invalid base_ref"):
        worker.prepare_workspace(job, q.job_dir(job.id))


def test_follow_up_without_branch_has_no_base_ref(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    prev = JobSpec.new("fix add", repo)
    new = J.submit_follow_up(q, repo, prev, "retry", None)
    assert not (q.job_dir(new.id) / "base_ref").exists()


# ── merge / reject ───────────────────────────────────────────────────────────

def test_merge_into_current_branch(repo):
    _branch(repo, "awos/bbbbbbbb", "calc.py", "def add(a, b):\n    return a + b\n")
    res = gitops.merge_branch(repo, "awos/bbbbbbbb", "fix add")
    assert res.ok, res.message
    assert "a + b" in (repo / "calc.py").read_text()
    assert _git(repo, "log", "-1", "--format=%s") == "Merge awos/bbbbbbbb: fix add"
    assert len(_git(repo, "log", "-1", "--format=%P").split()) == 2   # --no-ff
    again = gitops.merge_branch(repo, "awos/bbbbbbbb")
    assert again.ok and "already" in again.message


def test_merge_refused_when_uncommitted_changes_overlap(repo):
    _branch(repo, "awos/cccccccc", "calc.py", "def add(a, b):\n    return a + b\n")
    (repo / "calc.py").write_text("def add(a, b):\n    return b - a  # my wip\n")
    head = _git(repo, "rev-parse", "HEAD")
    res = gitops.merge_branch(repo, "awos/cccccccc")
    assert not res.ok and res.conflicts == ["calc.py"]
    assert "uncommitted changes" in res.message and "calc.py" in res.message
    assert _git(repo, "rev-parse", "HEAD") == head
    assert "my wip" in (repo / "calc.py").read_text()


def test_merge_allowed_with_unrelated_uncommitted_changes(repo):
    _branch(repo, "awos/dddddddd", "calc.py", "def add(a, b):\n    return a + b\n")
    (repo / "notes.txt").write_text("my wip\n")
    res = gitops.merge_branch(repo, "awos/dddddddd")
    assert res.ok, res.message
    assert (repo / "notes.txt").read_text() == "my wip\n"


def test_merge_conflict_is_aborted_cleanly(repo):
    _branch(repo, "awos/eeeeeeee", "calc.py", "def add(a, b):\n    return a + b\n")
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    _git(repo, "commit", "-q", "-am", "user change")
    head = _git(repo, "rev-parse", "HEAD")
    res = gitops.merge_branch(repo, "awos/eeeeeeee")
    assert not res.ok and "calc.py" in res.message
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""


def test_delete_branch(repo):
    _branch(repo, "awos/ffffffff", "calc.py", "x\n")
    ok, _ = gitops.delete_branch(repo, "awos/ffffffff")
    assert ok and not gitops.branch_exists(repo, "awos/ffffffff")
    ok, msg = gitops.delete_branch(repo, "awos/ffffffff")
    assert not ok and "does not exist" in msg


def test_find_job_by_prefix_and_branch_name(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job = q.submit("g", repo)
    assert J.find_job(q, repo, job.id[:6]).id == job.id
    assert J.find_job(q, repo, f"awos/{job.id[:8]}").id == job.id
    assert J.find_job(q, repo, "zzzz") is None
