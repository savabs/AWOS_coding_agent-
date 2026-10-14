"""The app's worker auto-start guard: start one worker if none, never two."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scaffold.agent.app import workerctl

# A stand-in worker: takes worker.lock the way HostWorker._lock does, writes its
# pid, and holds it until killed. argv[1] is the host root; it is named
# "serve --worker" in argv so the pid-reuse guard recognises it.
HOLDER = r"""
import fcntl, os, sys, time
h = open(os.path.join(sys.argv[1], "worker.lock"), "a+")
try:
    fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(3)          # another worker serves this root
h.seek(0); h.truncate(); h.write(str(os.getpid())); h.flush()
time.sleep(60)
"""


def _holder(root: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", HOLDER, str(root), "serve", "--worker"])


@pytest.fixture
def procs():
    started: list[subprocess.Popen] = []
    yield started
    for p in started:
        p.kill()
        p.wait()


def _wait_running(root: Path, sec: float = 5.0) -> bool:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        if workerctl.worker_running(root):
            return True
        time.sleep(0.05)
    return False


def test_no_lock_file_means_no_worker(tmp_path):
    assert workerctl.worker_running(tmp_path) is False


def test_stale_lock_file_with_dead_pid_is_not_a_worker(tmp_path):
    (tmp_path / "worker.lock").write_text("999999")
    assert workerctl.worker_running(tmp_path) is False


def test_live_holder_is_detected(tmp_path, procs):
    procs.append(_holder(tmp_path))
    assert _wait_running(tmp_path)
    assert workerctl.worker_pid(tmp_path) == procs[0].pid


def test_held_lock_with_foreign_pid_is_still_detected(tmp_path, procs):
    """pid-reuse guard: a live pid that is not a worker falls back to the lock probe."""
    procs.append(_holder(tmp_path))
    assert _wait_running(tmp_path)
    (tmp_path / "worker.lock").write_text(str(1))   # pid 1 (launchd): alive, not a worker
    assert workerctl.worker_running(tmp_path) is True


def test_ensure_worker_spawns_once_when_none_runs(tmp_path, procs):
    calls = []

    def spawn(cmd, root):
        calls.append(list(cmd))
        p = _holder(root)
        procs.append(p)
        return p

    assert workerctl.ensure_worker(tmp_path, spawn=spawn) == "started"
    assert len(calls) == 1 and calls[0][-2:] == ["serve", "--worker"]
    # a second call (e.g. the next task, or another window) does not spawn again
    assert workerctl.ensure_worker(tmp_path, spawn=spawn) == "running"
    assert len(calls) == 1


def test_ensure_worker_never_spawns_when_one_is_running(tmp_path, procs):
    procs.append(_holder(tmp_path))
    assert _wait_running(tmp_path)

    def spawn(cmd, root):
        raise AssertionError("must not spawn a second worker")

    assert workerctl.ensure_worker(tmp_path, spawn=spawn) == "running"


def test_concurrent_app_windows_spawn_one_worker(tmp_path, procs):
    calls = []
    lock = threading.Lock()

    def spawn(cmd, root):
        with lock:
            calls.append(1)
        p = _holder(root)
        procs.append(p)
        return p

    results = []
    threads = [threading.Thread(target=lambda: results.append(workerctl.ensure_worker(tmp_path, spawn=spawn)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert sorted(results) == ["running"] * 3 + ["started"]


def test_worker_that_exits_at_once_is_reported(tmp_path):
    def spawn(cmd, root):
        return subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])

    assert workerctl.ensure_worker(tmp_path, spawn=spawn, wait_sec=3) == "failed"


# ── v0.2.1: the open app re-checks the worker periodically ──────────────────


def _app(root: Path, check_sec: float = 15.0):
    import io

    from scaffold.agent.app.session import App
    from scaffold.agent.app.ui import Term
    from scaffold.agent.host.queue import JobQueue

    app = App(root, term=Term(out=io.StringIO(), inp=io.StringIO(), force_plain=True),
              queue=JobQueue(root / "host"), auto_worker=True)
    app.worker_check_sec = check_sec
    return app


def _queued():
    from types import SimpleNamespace

    from scaffold.agent.host.models import QUEUED
    return [SimpleNamespace(state=QUEUED)]


@pytest.fixture
def counted_spawn(monkeypatch, procs):
    calls = []
    lock = threading.Lock()

    def spawn(cmd, root):
        with lock:
            calls.append(list(cmd))
        p = _holder(root)
        procs.append(p)
        return p

    monkeypatch.delenv("AWOS_APP_NO_WORKER", raising=False)
    monkeypatch.setattr(workerctl, "_spawn_detached", spawn)
    return calls


def test_periodic_check_restarts_a_dead_worker_once(tmp_path, counted_spawn, procs):
    app = _app(tmp_path)
    assert app.check_worker(_queued(), force=True) == "started"
    assert len(counted_spawn) == 1
    assert "started it again" in app.term.out.getvalue()
    # worker alive now: the next check does nothing
    assert app.check_worker(_queued(), force=True) is None
    assert len(counted_spawn) == 1
    # the worker dies mid-session: the next periodic check starts one again
    procs[0].kill()
    procs[0].wait()
    assert app.check_worker(_queued(), force=True) == "started"
    assert len(counted_spawn) == 2


def test_periodic_check_waits_for_its_interval(tmp_path, counted_spawn):
    app = _app(tmp_path, check_sec=15.0)
    assert app.check_worker(_queued()) is None        # opened just now: too early
    assert counted_spawn == []
    app._last_worker_check -= 16                      # 16 s later
    assert app.check_worker(_queued()) == "started"
    assert len(counted_spawn) == 1


def test_periodic_check_leaves_an_idle_queue_alone(tmp_path, counted_spawn):
    app = _app(tmp_path)
    assert app.check_worker([], force=True) is None
    assert counted_spawn == []


def test_periodic_checks_from_two_windows_start_one_worker(tmp_path, counted_spawn):
    apps = [_app(tmp_path) for _ in range(3)]
    results = []
    threads = [threading.Thread(target=lambda a=a: results.append(a.check_worker(_queued(), force=True)))
               for a in apps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(counted_spawn) == 1                    # single-instance guard holds
    assert results.count("started") == 1


def test_tick_runs_the_periodic_check(tmp_path, monkeypatch):
    app = _app(tmp_path)
    seen = []
    monkeypatch.setattr(app, "check_worker", lambda jobs=None, force=False: seen.append(jobs))
    app.tick()
    assert len(seen) == 1


def test_worker_command_runs_serve_worker(monkeypatch):
    monkeypatch.setenv("AWOS_APP_WORKER_ARGS", "--poll 0.2")
    cmd = workerctl.worker_command()
    assert cmd[0] == sys.executable and cmd[1].endswith("awos.py")
    assert cmd[2:] == ["serve", "--worker", "--poll", "0.2"]
