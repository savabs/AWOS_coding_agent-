"""
Host hardening: the post-run test verification never runs model-written code
on the bare host with the worker's secrets; non-git roots are refused; the
loop checkpoint survives an auto-continue; worktree hygiene (nested repos,
snapshot retention, pinning, removal cap, Spotlight marker, hardened git env).
No model calls.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from scaffold.agent import test_runner as tr
from scaffold.agent.host import workspace as W
from scaffold.agent.host import worker
from scaffold.agent.host.models import JobSpec
from scaffold.agent.sandbox import SandboxResult


def _git(cwd, *args) -> str:
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


# ── 1. sandboxed / scrubbed verification ─────────────────────────────────────


def test_scrub_env_drops_secrets_and_keeps_the_rest():
    env = {"OPENROUTER_API_KEY": "k", "ANTHROPIC_BASE_URL": "u", "GH_TOKEN": "t",
           "GITHUB_TOKEN": "t", "AWS_PROFILE": "p", "MY_SERVICE_SECRET": "s",
           "OPENAI_ORG": "o", "GOOGLE_APPLICATION_CREDENTIALS": "/c.json",
           "SSH_AUTH_SOCK": "/s", "NETRC": "/n", "SLACK_TOKEN": "x",
           "PATH": "/usr/bin", "HOME": "/h", "PYTHONPATH": "/w", "AWOS_JOB_PRIVACY": "cloud_ok"}
    out = tr.scrub_env(env)
    assert out == {"PATH": "/usr/bin", "HOME": "/h", "PYTHONPATH": "/w",
                   "AWOS_JOB_PRIVACY": "cloud_ok"}


def _leaky_project(root: Path) -> Path:
    """A test suite whose conftest dumps the env it sees and marks that it ran."""
    dump = root.parent / f"{root.name}_env.json"
    (root / "conftest.py").write_text(
        "import json, os\n"
        f"json.dump(dict(os.environ), open({str(dump)!r}, 'w'))\n")
    (root / "test_a.py").write_text("def test_a():\n    pass\n")
    return dump


def test_verify_fails_closed_without_a_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    monkeypatch.delenv(worker.UNSAFE_HOST_VERIFY_ENV, raising=False)
    dump = _leaky_project(tmp_path)
    res = worker.verify_tests(str(tmp_path))
    assert res["status"] == "error" and res["reason"] == "no_sandbox"
    assert res["sandboxed"] is False and "not run" in res["line"]
    assert not dump.exists()                       # the project's code never ran
    assert worker.verify_isolation_available() is False


def test_unsafe_opt_in_runs_on_host_with_a_scrubbed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    monkeypatch.setenv(worker.UNSAFE_HOST_VERIFY_ENV, "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    dump = _leaky_project(tmp_path)
    res = worker.verify_tests(str(tmp_path))
    assert res["status"] == "passed" and res["sandboxed"] is False
    assert "UNSANDBOXED" in res["line"]
    seen = json.loads(dump.read_text())
    assert not {"OPENROUTER_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY"} & set(seen)
    assert worker.verify_isolation_available() is True


class _RecordingSandbox:
    backend = "fake"

    def __init__(self, workspace):
        self.workspace = workspace
        self.commands = []
        self.closed = False

    def run(self, command, timeout_sec=120):
        self.commands.append(command)
        return SandboxResult(0, "1 passed in 0.01s\n", "", False, 0.0)

    def close(self):
        self.closed = True


def test_verify_runs_inside_the_sandbox_on_the_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv(worker.UNSAFE_HOST_VERIFY_ENV, raising=False)
    made = []

    def fake_make(ws, backend=None):
        sb = _RecordingSandbox(ws)
        made.append(sb)
        return sb

    monkeypatch.setattr("scaffold.agent.sandbox.make_sandbox", fake_make)
    dump = _leaky_project(tmp_path)

    def no_host_run(*a, **k):
        raise AssertionError("pytest ran on the host")

    monkeypatch.setattr(tr.subprocess, "run", no_host_run)
    monkeypatch.setattr(tr, "workspace_tree_hash", lambda root: "x")
    res = worker.verify_tests(str(tmp_path))
    assert res["status"] == "passed" and res["sandboxed"] is True and res["sandbox"] == "fake"
    assert made and str(made[0].workspace) == str(tmp_path)
    assert made[0].commands and "pytest" in made[0].commands[0]
    assert made[0].closed and not dump.exists()


def test_goal_check_is_kept_when_verification_cannot_run(monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    monkeypatch.delenv(worker.UNSAFE_HOST_VERIFY_ENV, raising=False)
    assert worker.verify_isolation_available() is False
    src = Path(worker.__file__).read_text()
    assert "and verify_isolation_available())" in src


# ── 2. non-git roots ─────────────────────────────────────────────────────────


def test_non_git_job_fails_with_a_clear_message_and_no_edit(tmp_path, monkeypatch):
    from scaffold.agent.host.queue import JobQueue
    proj = tmp_path / "plain"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n")
    q = JobQueue(tmp_path / "host")
    job = q.submit("change x", str(proj))
    w = worker.HostWorker(q, runner=worker.subprocess_runner, key_checker=lambda: [],
                          notifier=lambda *a, **k: None, log=lambda m: None)
    w.precheck = False
    monkeypatch.setattr(worker, "_free_tier_gate", lambda job: None)
    monkeypatch.setattr(worker, "SpendTracker", _NoSpend)
    claimed = q.claim_next(os.getpid(), None)
    out = w.run_job(claimed)
    assert out.state == "failed" and "not a git repository" in (out.error or "")
    assert (proj / "a.py").read_text() == "x = 1\n"
    assert sorted(p.name for p in proj.iterdir()) == ["a.py"]   # nothing written there
    assert not (q.job_dir(job.id) / worker.WORKSPACE_DIR).exists()


class _NoSpend:
    def __init__(self, job_dir):
        pass

    def prior(self):
        return 0.0

    def start(self, attempt):
        pass

    def close(self):
        return 0.0


# ── 4. checkpoint key across auto-continue ───────────────────────────────────


def test_checkpoint_env_keeps_the_original_goal_and_writes_the_note(tmp_path):
    job = JobSpec(id="j1", goal="Fix the parser", repo_path=str(tmp_path), attempts=2)
    env = worker.loop_checkpoint_env(job, tmp_path, "you stopped at step 40; tests pass")
    ckpt = tmp_path / worker.LOOP_CHECKPOINT_FILE
    assert env == {"AWOS_LOOP_CHECKPOINT_PATH": str(ckpt),
                   "AWOS_LOOP_CHECKPOINT_GOAL": "Fix the parser"}
    note = Path(str(ckpt) + ".note")
    assert note.read_text() == "you stopped at step 40; tests pass"
    # The next attempt without a NOTE removes the stale one.
    worker.loop_checkpoint_env(job, tmp_path, None)
    assert not note.exists()


# ── 7. worktree hygiene ──────────────────────────────────────────────────────


def test_worktree_gets_spotlight_marker_hidden_from_git(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    ws = tmp_path / "jobs" / "j" / "workspace"
    W.add_worktree(str(repo), ws, base)
    assert (ws / W.NEVER_INDEX).exists()
    assert _git(ws, "status", "--porcelain") == ""        # not an untracked file
    assert worker.changed_paths(str(ws), base) == []
    W.add_worktree(str(repo), tmp_path / "jobs" / "j" / "baseline", base)
    exclude = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) \
        / "info" / "exclude"
    assert exclude.read_text().count(f"/{W.NEVER_INDEX}") == 1   # added once


def test_worktree_creation_skips_lfs_smudge_and_hooks(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {tmp_path / 'hook_ran'}\n")
    hook.chmod(0o755)
    seen = {}
    real = W.subprocess.run

    def spy(cmd, *a, **k):
        if cmd[:3] == ["git", "worktree", "add"]:
            seen.update(k.get("env") or {})
        return real(cmd, *a, **k)

    monkeypatch.setattr(W.subprocess, "run", spy)
    W.add_worktree(str(repo), tmp_path / "ws", base)
    assert seen.get("GIT_LFS_SKIP_SMUDGE") == "1"
    assert not (tmp_path / "hook_ran").exists()            # hooks off (hardened env)


def test_snapshot_refused_with_nested_repo_keeps_worktree(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    ws = tmp_path / "jobs" / "j" / "workspace"
    W.add_worktree(str(repo), ws, base)
    nested = ws / "vendor" / "lib"
    nested.mkdir(parents=True)
    _git(nested, "init", "-q")
    (nested / "keep.txt").write_text("precious\n")
    assert W.nested_repos(ws) == [os.path.join("vendor", "lib")]
    res = W.remove_worktree(ws, "j", str(repo), base)
    assert res["removed"] is False and "nested git" in res["error"]
    assert (nested / "keep.txt").exists()


def _snap_ref(repo, job, stamp, sha):
    _git(repo, "update-ref", f"{W.SNAPSHOT_PREFIX}/{job}/{stamp}", sha)


def test_prune_snapshots_keeps_newest_per_job_drops_old_and_respects_pins(tmp_path):
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    now = time.time()

    def stamp(days_ago):
        return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now - days_ago * 86400))

    for i, d in enumerate((5, 4, 3, 2, 1)):
        _snap_ref(repo, "jobA", stamp(d), sha)
    _snap_ref(repo, "jobB", stamp(40), sha)                 # too old
    _snap_ref(repo, "jobB", stamp(1), sha)
    _snap_ref(repo, "jobP", stamp(90), sha)                 # pinned: kept
    deleted = W.prune_snapshots(repo, keep_per_job=3, max_age_days=30, pinned_jobs={"jobP"},
                                now=now)
    left = {(s["job_id"], s["ref"].rsplit("/", 1)[1]) for s in W.list_snapshots(repo)}
    assert len(deleted) == 3
    assert sorted(t for j, t in left if j == "jobA") == [stamp(3), stamp(2), stamp(1)]
    assert [t for j, t in left if j == "jobB"] == [stamp(1)]
    assert [t for j, t in left if j == "jobP"] == [stamp(90)]
    # the per-call cap
    for d in range(10, 16):
        _snap_ref(repo, "jobC", stamp(d + 30), sha)
    assert len(W.prune_snapshots(repo, max_removals=2, now=now)) == 2


def _finished_worktree(repo, jobs, jid, ts):
    base = _git(repo, "rev-parse", "HEAD")
    ws = jobs / jid / "workspace"
    W.add_worktree(str(repo), ws, base)
    W.claim(jid, str(repo), ws, base)
    W.release(ws, jid)
    rec = W.read_owner(ws)
    rec["last_used_ts"] = ts
    W.write_owner(ws, rec)
    return ws


def test_pinned_worktree_is_never_pruned(tmp_path):
    repo = _repo(tmp_path)
    jobs = tmp_path / "jobs"
    a = _finished_worktree(repo, jobs, "a", 1.0)
    b = _finished_worktree(repo, jobs, "b", 2.0)
    W.set_pinned(a, True)
    out = W.prune_worktrees(jobs, keep=0, is_final=lambda j: True)
    assert a.exists() and not b.exists()
    assert [e["job_id"] for e in out] == ["b"]
    W.claim("a", str(repo), a, None)                       # pin survives a claim
    assert W.read_owner(a)["pinned"] is True


def test_prune_rereads_owner_record_before_removing(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    jobs = tmp_path / "jobs"
    a = _finished_worktree(repo, jobs, "a", 1.0)
    real_list = W.list_worktrees

    def stale_then_claimed(root):
        entries = real_list(root)
        rec = W.read_owner(a)
        rec["pinned"] = True                                # changed after the inventory
        W.write_owner(a, rec)
        return entries

    monkeypatch.setattr(W, "list_worktrees", stale_then_claimed)
    assert W.prune_worktrees(jobs, keep=0, is_final=lambda j: True) == []
    assert a.exists()


def test_prune_caps_removals_per_pass(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    jobs = tmp_path / "jobs"
    for i in range(4):
        _finished_worktree(repo, jobs, f"j{i}", float(i))
    monkeypatch.setattr(W, "MAX_REMOVALS_PER_PASS", 2)
    out = W.prune_worktrees(jobs, keep=0, is_final=lambda j: True)
    assert len(out) == 2 and len(W.list_worktrees(jobs)) == 2


def test_retire_prunes_snapshots(tmp_path):
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    for d in range(6):
        _snap_ref(repo, "old", time.strftime("%Y%m%dT%H%M%SZ",
                                             time.gmtime(time.time() - (d + 1) * 3600)), sha)
    job_dir = tmp_path / "jobs" / "new"
    job_dir.mkdir(parents=True)
    out = W.retire("new", str(repo), job_dir)
    assert len(out["snapshots_pruned"]) == 3
    assert len(W.list_snapshots(repo, "old")) == 3
