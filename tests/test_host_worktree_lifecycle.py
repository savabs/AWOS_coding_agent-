"""D17 worktree lifecycle: owner record, cap with LRU prune, snapshot-before-delete."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scaffold.agent.host import worker
from scaffold.agent.host import workspace as W
from scaffold.agent.host.models import DONE, FAILED, RUNNING
from scaffold.agent.host.queue import JobQueue


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "a.py").write_text("x = 1\n")
    (r / ".gitignore").write_text("*.log\nbuild/\n")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return r


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _job_ws(q: JobQueue, repo: Path, goal: str = "g"):
    job = q.submit(goal, repo)
    jdir = q.job_dir(job.id)
    ws, base = worker.prepare_workspace(job, jdir)
    return job, jdir, Path(ws), base


# ── owner record ─────────────────────────────────────────────────────────────

def test_prepare_workspace_writes_owner_record_next_to_it(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    rec = json.loads((jdir / "workspace.owner.json").read_text())
    assert rec["job_id"] == job.id and rec["base_commit"] == base
    assert rec["owner_pid"] == os.getpid() and rec["owner_host"]
    assert rec["state"] == W.ACTIVE and rec["repo_path"] == str(repo)
    assert rec["created_at"] and rec["last_used_at"]
    assert W.owner_alive(rec) is True
    # A retry reuses the worktree: same created_at, fresher last use.
    first_used = rec["last_used_ts"]
    assert worker.prepare_workspace(job, jdir) == (str(ws), base)
    again = W.read_owner(ws)
    assert again["created_at"] == rec["created_at"] and again["last_used_ts"] >= first_used


def test_owner_alive_checks_pid_start_and_host():
    assert W.owner_alive(None) is False
    assert W.owner_alive({"owner_pid": _dead_pid(), "owner_started": "x"}) is False
    assert W.owner_alive({"owner_pid": os.getpid(), "owner_host": "elsewhere"}) is None


def test_max_worktrees_env(monkeypatch):
    monkeypatch.delenv("AWOS_MAX_WORKTREES", raising=False)
    assert W.max_worktrees() == 20
    monkeypatch.setenv("AWOS_MAX_WORKTREES", "3")
    assert W.max_worktrees() == 3
    monkeypatch.setenv("AWOS_MAX_WORKTREES", "junk")
    assert W.max_worktrees() == 20


# ── snapshot ─────────────────────────────────────────────────────────────────

def _dirty_up(ws: Path) -> None:
    (ws / "a.py").write_text("x = 2\n")                     # unstaged edit
    (ws / "staged.py").write_text("s = 1\n")
    _git(ws, "add", "staged.py")                            # staged new file
    (ws / "new.py").write_text("n = 1\n")                   # untracked
    (ws / "run.log").write_text("noise\n")                  # gitignored
    (ws / "build").mkdir()
    (ws / "build" / "out.bin").write_text("b\n")            # gitignored dir
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\0pyc")


def test_snapshot_keeps_uncommitted_and_untracked_respecting_gitignore(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    _dirty_up(ws)
    status_before = _git(ws, "status", "--porcelain")
    snap = W.snapshot(ws, job.id, base)
    assert snap["ref"].startswith(f"refs/awos/snapshots/{job.id}/")
    files = set(_git(repo, "ls-tree", "-r", "--name-only", snap["ref"]).splitlines())
    assert {"a.py", "staged.py", "new.py", ".gitignore"} <= files
    assert not {"run.log", "build/out.bin", "__pycache__/a.cpython-313.pyc"} & files
    assert _git(repo, "show", f"{snap['ref']}:a.py") == "x = 2"
    assert _git(repo, "rev-parse", f"{snap['ref']}^") == base
    # The worktree itself is untouched: same status, same index, same HEAD.
    assert _git(ws, "status", "--porcelain") == status_before
    assert _git(ws, "rev-parse", "HEAD") == base
    # Hidden ref: no branch, nothing in the user's checkout.
    assert _git(repo, "branch", "--list") .count("\n") == 0
    assert _git(repo, "status", "--porcelain") == ""
    assert [s["ref"] for s in W.list_snapshots(repo, job.id)] == [snap["ref"]]


def test_snapshot_keeps_commits_on_the_detached_head(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    (ws / "a.py").write_text("x = 3\n")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "job commit")
    head = _git(ws, "rev-parse", "HEAD")
    snap = W.snapshot(ws, job.id, base)    # clean tree, but HEAD moved: must be saved
    assert "ref" in snap and _git(repo, "rev-parse", f"{snap['ref']}^") == head


def test_clean_worktree_at_base_needs_no_snapshot(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    (ws / "x.log").write_text("ignored only\n")
    assert W.snapshot(ws, job.id, base) == {"clean": True, "commit": base}
    assert W.list_snapshots(repo) == []


def test_two_snapshots_in_one_second_get_distinct_refs(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    (ws / "a.py").write_text("x = 9\n")
    a, b = W.snapshot(ws, job.id, base), W.snapshot(ws, job.id, base)
    assert a["ref"] != b["ref"] and len(W.list_snapshots(repo, job.id)) == 2


# ── removal ──────────────────────────────────────────────────────────────────

def test_remove_snapshots_first_and_snapshot_restores(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    _dirty_up(ws)
    worker.remove_workspace(job, jdir)
    assert not ws.exists() and not (jdir / "workspace.owner.json").exists()
    assert str(ws) not in _git(repo, "worktree", "list")
    [snap] = W.list_snapshots(repo, job.id)
    dest = tmp_path / "restored"
    W.restore_snapshot(repo, snap["ref"], dest)
    assert (dest / "a.py").read_text() == "x = 2\n"
    assert (dest / "new.py").read_text() == "n = 1\n" and (dest / "staged.py").exists()
    with pytest.raises(ValueError):
        W.restore_snapshot(repo, "refs/heads/master", tmp_path / "nope")


def test_failed_snapshot_keeps_the_worktree(repo, tmp_path, monkeypatch):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    (ws / "a.py").write_text("precious\n")

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(W, "snapshot", boom)
    res = W.remove_worktree(ws, job.id, str(repo), base)
    assert res["removed"] is False and "disk full" in res["error"]
    assert (ws / "a.py").read_text() == "precious\n"
    with pytest.raises(RuntimeError, match="snapshot failed"):
        worker.remove_workspace(job, jdir)
    assert ws.exists()


# ── cap + prune ──────────────────────────────────────────────────────────────

def _finish(q: JobQueue, job, jdir: Path, state: str = DONE, ts: float = 0.0) -> None:
    W.release(jdir / "workspace", job.id)
    rec = W.read_owner(jdir / "workspace")
    rec["last_used_ts"] = ts
    W.write_owner(jdir / "workspace", rec)
    q.claim_next(os.getpid(), None) if q.get(job.id).state != RUNNING else None
    q.finish(job.id, state, result={"success": state == DONE})


def _is_final(q: JobQueue):
    return lambda jid: (None if q.get(jid) is None
                        else q.get(jid).state in (DONE, FAILED, "cancelled"))


def test_prune_removes_least_recently_used_finished_ones(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    made = [_job_ws(q, repo, f"g{i}") for i in range(4)]
    for i, (job, jdir, ws, base) in enumerate(made):
        (ws / "a.py").write_text(f"x = {10 + i}\n")
        _finish(q, job, jdir, ts=100.0 + i)
    pruned = W.prune_worktrees(q.root / "jobs", keep=2, is_final=_is_final(q))
    gone = {p["job_id"] for p in pruned if p["removed"]}
    assert gone == {made[0][0].id, made[1][0].id}          # the two oldest
    assert made[2][2].exists() and made[3][2].exists()
    for job, *_ in made[:2]:
        [snap] = W.list_snapshots(repo, job.id)              # nothing lost
        assert _git(repo, "show", f"{snap['ref']}:a.py").startswith("x = 1")
    assert W.prune_worktrees(q.root / "jobs", keep=2, is_final=_is_final(q)) == []


def test_live_worktrees_are_never_pruned_even_above_the_cap(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    running, rdir, rws, _ = _job_ws(q, repo, "running")      # job not finished
    q.claim_next(os.getpid(), None)
    held, hdir, hws, _ = _job_ws(q, repo, "held")            # final, but a live worker holds it
    q.claim_next(os.getpid(), None)
    q.finish(held.id, DONE, result={"success": True})
    rec = W.read_owner(hws)
    rec["last_used_ts"] = 1.0
    W.write_owner(hws, rec)
    unknown_dir = q.root / "jobs" / "deadbeef"               # legacy, job unknown to the queue
    unknown_dir.mkdir(parents=True)
    _git(repo, "worktree", "add", "-q", "--detach", str(unknown_dir / "workspace"))
    pruned = W.prune_worktrees(q.root / "jobs", keep=0, is_final=_is_final(q))
    assert pruned == [] and rws.exists() and hws.exists() and (unknown_dir / "workspace").exists()
    assert W.protected_reason({"job_id": running.id, "record": W.read_owner(rws)},
                              _is_final(q)) == "job not finished"
    assert W.protected_reason({"job_id": held.id, "record": W.read_owner(hws)},
                              _is_final(q)) == "owner alive"
    # The owning worker died: now it may go.
    rec["owner_pid"] = _dead_pid()
    W.write_owner(hws, rec)
    pruned = W.prune_worktrees(q.root / "jobs", keep=0, is_final=_is_final(q))
    assert [p["job_id"] for p in pruned] == [held.id] and not hws.exists()


def test_legacy_worktree_of_a_finished_job_is_prunable(repo, tmp_path):
    q = JobQueue(tmp_path / "host")
    job, jdir, ws, base = _job_ws(q, repo)
    (jdir / "workspace.owner.json").unlink()                 # made before owner records
    q.claim_next(os.getpid(), None)
    q.finish(job.id, FAILED, result={"success": False})
    [entry] = W.list_worktrees(q.root / "jobs")
    assert entry["legacy"] and W.protected_reason(entry, _is_final(q)) is None
    assert W.prune_worktrees(q.root / "jobs", keep=0, is_final=_is_final(q))[0]["removed"]


# ── worker integration ───────────────────────────────────────────────────────

def _editing_runner(job, ctx):
    ws, base = worker.prepare_workspace(job, ctx.job_dir)
    worker.prepare_baseline(job, ctx.job_dir, base)
    (Path(ws) / "a.py").write_text(f"x = '{job.goal}'\n")
    (Path(ws) / "scratch.txt").write_text("untracked\n")
    return {"success": True, "workspace": ws, "base_commit": base}


def _serve(tmp_path, repo, goals):
    q = JobQueue(tmp_path / "host")
    w = worker.HostWorker(q, runner=_editing_runner, log=lambda m: None,
                          key_checker=None, notifier=lambda *a, **k: None)
    jobs = [q.submit(g, repo) for g in goals]
    w.serve(once=True)
    return q, jobs


def test_finished_job_keeps_workspace_drops_baseline(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_MAX_WORKTREES", "5")
    q, [job] = _serve(tmp_path, repo, ["one"])
    jdir = q.job_dir(job.id)
    assert q.get(job.id).state == DONE
    assert (jdir / "workspace" / "a.py").exists() and not (jdir / "baseline").exists()
    rec = W.read_owner(jdir / "workspace")
    assert rec["state"] == W.FINISHED and rec["released_at"]
    assert _git(repo, "status", "--porcelain") == ""         # user's checkout untouched


def test_worker_enforces_the_cap_with_snapshots(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_MAX_WORKTREES", "1")
    q, jobs = _serve(tmp_path, repo, ["one", "two", "three"])
    alive = [j for j in jobs if (q.job_dir(j.id) / "workspace").exists()]
    assert [j.goal for j in alive] == ["three"]
    for j in jobs[:2]:
        [snap] = W.list_snapshots(repo, j.id)
        assert _git(repo, "show", f"{snap['ref']}:scratch.txt") == "untracked"
        assert f"'{j.goal}'" in _git(repo, "show", f"{snap['ref']}:a.py")
    lines = _git(repo, "worktree", "list").splitlines()
    assert len(lines) == 2                                   # main checkout + the kept one


def test_cap_zero_removes_at_once(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_MAX_WORKTREES", "0")
    q, [job] = _serve(tmp_path, repo, ["only"])
    assert not (q.job_dir(job.id) / "workspace").exists()
    assert len(W.list_snapshots(repo, job.id)) == 1
