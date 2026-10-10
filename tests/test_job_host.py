"""
Tests for scaffold/agent/job_host.py — the always-on job host (M1).

The child runner is a stub in the host-loop tests, so no model is called;
run_in_child is tested against real child processes that never reach a model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scaffold.agent import job_host  # noqa: E402
from scaffold.agent.job_host import JobHost, JobStore  # noqa: E402

DEAD_PID = 2**22 + 12345  # above macOS/Linux pid_max defaults: cannot exist


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


def _quiet(_msg):
    pass


def test_submit_round_trip(store, tmp_path):
    job = store.submit("  do the thing  ", root=tmp_path)
    loaded = store.get(job.id)
    assert loaded == job
    assert loaded.goal == "do the thing"
    assert loaded.root == str(tmp_path.resolve())
    assert (loaded.status, loaded.ability, loaded.attempts) == ("queued", "coding", 1)
    assert json.loads(store.path(job.id).read_text())["id"] == job.id


def test_submit_rejects_empty_goal(store, tmp_path):
    with pytest.raises(ValueError):
        store.submit("   ", root=tmp_path)


def test_atomic_write_leaves_no_temp_file(store, tmp_path, monkeypatch):
    job = store.submit("goal", root=tmp_path)
    before = store.path(job.id).read_text()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(job_host.os, "replace", boom)
    job.goal = "changed"
    with pytest.raises(OSError):
        store.save(job)
    assert store.path(job.id).read_text() == before  # original untouched
    monkeypatch.undo()
    store.save(job)
    assert not [p for p in store.dir.iterdir() if p.name.startswith(".")]
    assert store.get(job.id).goal == "changed"


def test_next_queued_is_fifo(store, tmp_path):
    first = store.submit("first", root=tmp_path)
    second = store.submit("second", root=tmp_path)
    assert store.next_queued().id == first.id
    store.mark_running(first)
    assert store.next_queued().id == second.id
    assert [j.id for j in store.list()] == [first.id, second.id]


def test_recover_requeues_running_job_with_dead_pid(store, tmp_path):
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=DEAD_PID)
    recovered = store.recover()
    assert [j.id for j in recovered] == [job.id]
    loaded = store.get(job.id)
    assert (loaded.status, loaded.attempts, loaded.pid) == ("queued", 2, None)


def test_recover_leaves_live_running_job_alone(store, tmp_path):
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=os.getpid())  # this process: alive, host alive
    assert store.recover() == []
    assert store.get(job.id).status == "running"


@pytest.fixture
def orphan():
    """A live child in its own group, standing in for a job's child process."""
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_recover_kills_orphan_child_of_dead_host(store, tmp_path, orphan):
    # The child is alive, so only the dead host makes this an interruption;
    # the orphan must die before the job is queued again.
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=orphan.pid)
    job.host_pid = DEAD_PID
    store.save(job)
    store.recover()
    assert orphan.wait(timeout=2) == -9
    loaded = store.get(job.id)
    assert (loaded.status, loaded.attempts, loaded.pid) == ("queued", 2, None)


def test_recover_never_kills_a_reused_child_pid(store, tmp_path, orphan):
    # After a reboot the recorded pid names some unrelated process: its start
    # time does not match, so it is left alone and the job counts as dead.
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=orphan.pid)
    job.pid_started = "Mon Jan  1 00:00:00 2001"
    job.host_pid = DEAD_PID
    store.save(job)
    store.recover()
    assert orphan.poll() is None
    assert store.get(job.id).status == "queued"


def test_recover_requeues_when_both_pids_were_reused(store, tmp_path, orphan):
    # Host pid and child pid both name live, unrelated processes: before
    # start times were kept, the job stayed "running" forever.
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=orphan.pid)
    job.pid_started = job.host_started = "Mon Jan  1 00:00:00 2001"
    store.save(job)
    assert [j.id for j in store.recover()] == [job.id]
    assert orphan.poll() is None
    assert store.get(job.id).status == "queued"


def test_recover_sees_a_reused_host_pid_as_a_dead_host(store, tmp_path, orphan):
    # The host pid is alive but names another process (start differs): the
    # host is gone, so its genuine child is an orphan to kill.
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=orphan.pid)
    job.host_started = "Mon Jan  1 00:00:00 2001"
    store.save(job)
    store.recover()
    assert orphan.wait(timeout=2) == -9
    assert store.get(job.id).status == "queued"


def test_recover_leaves_unverifiable_pid_alone(store, tmp_path, orphan):
    # A record written before start times were kept: the pid cannot be
    # checked, so it is not killed (the child's own watcher ends it).
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=orphan.pid)
    job.pid_started, job.host_pid = None, DEAD_PID
    store.save(job)
    store.recover()
    assert orphan.poll() is None
    assert store.get(job.id).status == "queued"


def test_mark_running_records_start_times(store, tmp_path, orphan):
    job = store.mark_running(store.submit("goal", root=tmp_path), pid=orphan.pid)
    assert job.pid_started and job.pid_started == job_host.process_start(orphan.pid)
    assert job.host_started == job_host.process_start(os.getpid())
    assert job_host.process_matches(orphan.pid, job.pid_started) is True
    assert job_host.process_matches(DEAD_PID, job.pid_started) is False


def test_recover_fails_job_after_max_attempts(store, tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_JOB_MAX_ATTEMPTS", "2")
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=DEAD_PID)
    store.recover()  # attempt 2 queued
    job = store.get(job.id)
    assert (job.status, job.attempts) == ("queued", 2)  # not failed one early
    store.mark_running(job, pid=DEAD_PID)
    store.recover()  # attempt 3 > 2 -> failed
    loaded = store.get(job.id)
    assert loaded.status == "failed"
    assert "AWOS_JOB_MAX_ATTEMPTS=2" in loaded.error
    assert store.report_path(job.id).exists()


def test_unknown_ability_fails_cleanly(store, tmp_path):
    job = store.submit("goal", root=tmp_path, ability="spreadsheets")
    called = []
    host = JobHost(store, runner=lambda *a: called.append(a) or {"success": True}, log=_quiet)
    host.serve(once=True)
    loaded = store.get(job.id)
    assert loaded.status == "failed"
    assert "unknown ability 'spreadsheets'" in loaded.error
    assert called == []
    assert "spreadsheets" in store.report_path(job.id).read_text()


def test_serve_once_processes_every_queued_job(store, tmp_path):
    ok = store.submit("works", root=tmp_path)
    bad = store.submit("reports failure", root=tmp_path)
    crash = store.submit("crashes", root=tmp_path)
    seen = []

    def runner(st, job, on_start):
        on_start(os.getpid())
        saved = st.get(job.id)
        assert saved.status == "running"
        # recover() can only kill an orphan whose pid and start were saved.
        assert (saved.pid, saved.pid_started) == (os.getpid(), job_host.process_start(os.getpid()))
        seen.append(job.goal)
        if job.goal == "crashes":
            raise RuntimeError("child exited with code 1")
        return {"success": job.goal == "works", "tasks_completed": 1}

    JobHost(store, runner=runner, log=_quiet).serve(once=True)
    assert seen == ["works", "reports failure", "crashes"]
    assert store.get(ok.id).status == "done"
    assert store.get(ok.id).result == {"success": True, "tasks_completed": 1}
    assert store.get(bad.id).status == "failed"
    assert store.get(crash.id).error == "child exited with code 1"
    report = store.report_path(ok.id).read_text()
    assert "works" in report and "**Status:** done" in report


def test_serve_recovers_interrupted_job_before_running(store, tmp_path):
    job = store.submit("goal", root=tmp_path)
    store.mark_running(job, pid=DEAD_PID)
    attempts = []
    JobHost(store, runner=lambda st, j, cb: attempts.append(j.attempts) or {"success": True},
            log=_quiet).serve(once=True)
    assert attempts == [2]
    assert store.get(job.id).status == "done"


def test_failure_reason_reaches_the_job_error(store, tmp_path):
    job = store.submit("goal", root=tmp_path)
    JobHost(store, runner=lambda st, j, cb: {"success": False, "error": "server.py still reads ini"},
            log=_quiet).serve(once=True)
    assert store.get(job.id).error == "server.py still reads ini"


# ── run_in_child: the real child process ──────────────────────────────────────


def test_child_that_writes_no_result_raises(store, tmp_path):
    job = store.submit("goal", root=tmp_path, ability="nope")  # KeyError in the child
    with pytest.raises(RuntimeError, match="no result"):
        job_host.run_in_child(store, job, on_start=lambda pid: None)


def test_stale_result_from_a_previous_attempt_is_not_reused(store, tmp_path):
    # A crashed retry must not be reported with the last attempt's success.
    job = store.submit("goal", root=tmp_path, ability="nope")
    store.result_path(job.id).write_text(json.dumps({"success": True}))
    with pytest.raises(RuntimeError, match="no result"):
        job_host.run_in_child(store, job, on_start=lambda pid: None)


def test_child_timeout_kills_its_process_group(store, tmp_path, monkeypatch):
    pidfile = tmp_path / "grandchild.pid"
    fake_python = tmp_path / "fake_python"
    fake_python.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > '{pidfile}'\nexec sleep 60\n")
    fake_python.chmod(0o755)
    monkeypatch.setattr(job_host.sys, "executable", str(fake_python))
    monkeypatch.setenv("AWOS_JOB_TIMEOUT_MIN", "0.05")  # 3 s: a fresh script can be slow to first exec
    job = store.submit("goal", root=tmp_path)
    started = []
    with pytest.raises(RuntimeError, match="timed out"):
        job_host.run_in_child(store, job, on_start=started.append)
    assert not job_host.pid_alive(started[0])
    grandchild = int(pidfile.read_text())
    import time

    for _ in range(20):  # killed; the zombie is reaped by init shortly after
        if not job_host.pid_alive(grandchild):
            break
        time.sleep(0.1)
    assert not job_host.pid_alive(grandchild)


def test_host_git_calls_ignore_config_that_runs_commands(store, tmp_path):
    # Sandboxed code could once write .git/config; the host's own git must
    # not run what a planted core.fsmonitor names.
    from scaffold.agent.goal_check import working_tree_changes

    root = tmp_path / "ws"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    marker = tmp_path / "pwned"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(hook)], check=True)
    (root / "a b.txt").write_text("x")
    assert any("a b.txt" in ln for ln in job_host._changed_files(str(root)))
    assert working_tree_changes(str(root))[0] == ["a b.txt"]
    assert not marker.exists()


def test_second_host_is_refused(store):
    first = JobHost(store, log=_quiet)._lock()
    try:
        with pytest.raises(RuntimeError, match="already serving"):
            JobHost(store, log=_quiet)._lock()
    finally:
        first.close()


def test_report_lists_changed_files_in_git_root(store, tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "new.txt").write_text("x")
    job = store.submit("goal", root=root)
    store.mark_done(job, {"success": True})
    assert "new.txt" in job_host.write_report(store, job).read_text()


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parser():
    sys.path.insert(0, str(REPO / "scaffold" / "agent"))
    import awos

    return awos.build_parser()


def test_cli_parses_job_commands():
    p = _parser()
    a = p.parse_args(["submit", "fix it", "--root", "/tmp", "--ability", "coding"])
    assert (a.command, a.goal, a.root, a.ability) == ("submit", "fix it", "/tmp", "coding")
    assert p.parse_args(["submit", "g"]).ability == "coding"
    assert p.parse_args(["host", "--once"]).once is True
    assert p.parse_args(["host"]).once is False
    assert p.parse_args(["jobs", "--all"]).all is True
    assert p.parse_args(["job", "j_abc"]).job_id == "j_abc"
    # the dashboard keeps its own `serve`
    assert p.parse_args(["serve", "--port", "9000"]).port == 9000


def test_cli_help_runs():
    out = subprocess.run([sys.executable, str(REPO / "awos.py"), "submit", "--help"],
                         capture_output=True, text=True, cwd=REPO, timeout=60)
    assert out.returncode == 0 and "--ability" in out.stdout
