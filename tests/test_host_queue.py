"""
Tests for scaffold/agent/host/{models,queue,worker}.py and the host CLI (M1).

No model is ever called: runners are fakes, the subprocess runner is only
exercised for its env handling and the orphan-kill path uses `sleep`.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scaffold.agent.host import worker as host_worker  # noqa: E402
from scaffold.agent.host.models import JobSpec, host_root  # noqa: E402
from scaffold.agent.host.queue import JobQueue  # noqa: E402
from scaffold.agent.host.worker import (HostWorker, RunContext, StopRequested,  # noqa: E402
                                        child_env, resolve_runner, sleep_runner)
from scaffold.agent.job_host import pid_alive, process_start  # noqa: E402

PY = sys.executable


def _quiet(_m):
    pass


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "host"
    monkeypatch.setenv("AWOS_HOST_DIR", str(r))
    return r


@pytest.fixture
def queue(root):
    return JobQueue(root)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    return d


# ── models ────────────────────────────────────────────────────────────────────


def test_host_root_env_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("AWOS_HOST_DIR", str(tmp_path / "x"))
    assert host_root() == tmp_path / "x"
    monkeypatch.delenv("AWOS_HOST_DIR")
    assert host_root() == Path.home() / ".awos" / "host"


def test_jobspec_validation(repo):
    with pytest.raises(ValueError):
        JobSpec.new("  ", repo)
    with pytest.raises(ValueError):
        JobSpec.new("g", repo, privacy="public")
    with pytest.raises(ValueError):
        JobSpec.new("g", repo, kind="robotics")
    with pytest.raises(ValueError):
        JobSpec.new("g", repo / "missing")
    with pytest.raises(ValueError):
        JobSpec.new("g", repo, budget_usd=-1)


# ── queue ─────────────────────────────────────────────────────────────────────


def test_submit_round_trip_keeps_goal_verbatim(queue, repo):
    goal = "  Fix the bug\n  in pagination  "
    job = queue.submit(goal, repo, budget_usd=0.5, privacy="local_only")
    got = queue.get(job.id)
    assert got.goal == goal
    assert len(got.id) == 32 and got.state == "queued" and got.attempts == 0
    assert (got.budget_usd, got.privacy, got.kind) == (0.5, "local_only", "coding")
    assert got.repo_path == str(repo.resolve())
    assert queue.job_dir(job.id).is_dir()
    assert queue.get(job.id[:10]).id == job.id  # unambiguous prefix


def test_claim_is_fifo_and_atomic(queue, repo):
    ids = [queue.submit(f"g{i}", repo).id for i in range(5)]
    claimed: list[str] = []
    lock = threading.Lock()

    def grab():
        q = JobQueue(queue.root)
        while True:
            job = q.claim_next()
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=grab) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids) and len(claimed) == len(set(claimed))
    assert all(j.state == "running" and j.attempts == 1 for j in queue.list())


def test_claim_order_is_submission_order(queue, repo):
    a, b = queue.submit("a", repo), queue.submit("b", repo)
    assert queue.claim_next().id == a.id
    assert queue.claim_next().id == b.id
    assert queue.claim_next() is None


def test_transitions_are_guarded(queue, repo):
    job = queue.submit("g", repo)
    assert not queue.finish(job.id, "done")  # not running
    queue.claim_next()
    assert queue.finish(job.id, "done", result={"success": True})
    assert not queue.finish(job.id, "failed")  # already final
    assert queue.get(job.id).result == {"success": True}
    with pytest.raises(ValueError):
        queue.finish(job.id, "queued")


def test_cancel_queued_running_final_missing(queue, repo):
    a, b = queue.submit("a", repo), queue.submit("b", repo)
    assert queue.cancel(b.id) == "cancelled"
    queue.claim_next()
    assert queue.cancel(a.id) == "requested"
    assert queue.cancel_requested(a.id)
    queue.finish(a.id, "cancelled")
    assert queue.cancel(a.id) == "final"
    assert queue.cancel("nope") == "missing"


def test_recover_requeues_then_fails_after_max_attempts(queue, repo, monkeypatch):
    monkeypatch.setenv("AWOS_HOST_MAX_ATTEMPTS", "2")
    job = queue.submit("g", repo)
    queue.claim_next()
    assert [j.state for j in queue.recover()] == ["queued"]
    assert queue.get(job.id).attempts == 1
    queue.claim_next()
    assert [j.state for j in queue.recover()] == ["failed"]
    assert "interrupted 2 times" in queue.get(job.id).error


def test_recover_cancels_when_cancel_was_requested(queue, repo):
    job = queue.submit("g", repo)
    queue.claim_next()
    queue.cancel(job.id)
    queue.recover()
    assert queue.get(job.id).state == "cancelled"


def test_recover_kills_live_orphan_child(queue, repo):
    job = queue.submit("g", repo)
    queue.claim_next()
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        queue.set_child(job.id, proc.pid, process_start(proc.pid))
        queue.recover(kill_child=host_worker._kill_orphan)
        proc.wait(timeout=10)
        assert queue.get(job.id).state == "queued"
        assert queue.get(job.id).child_pid is None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_recover_spares_reused_pid(queue, repo):
    """A pid whose start time does not match is not ours: never kill it."""
    job = queue.submit("g", repo)
    queue.claim_next()
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        queue.set_child(job.id, proc.pid, "Thu Jan  1 00:00:00 1970")
        queue.recover(kill_child=host_worker._kill_orphan)
        time.sleep(0.2)
        assert proc.poll() is None and pid_alive(proc.pid)
    finally:
        proc.kill()


# ── worker ────────────────────────────────────────────────────────────────────


def _worker(queue, runner, **kw):
    w = HostWorker(queue, runner=runner, log=_quiet)
    w._handoff = None  # use the minimal report regardless of other branches
    return w


def test_worker_runs_all_to_done_with_report(queue, repo, monkeypatch):
    monkeypatch.setenv("AWOS_HOST_FAKE_STEPS", "2")
    monkeypatch.setenv("AWOS_HOST_FAKE_STEP_SEC", "0")
    ids = [queue.submit(f"g{i}", repo).id for i in range(3)]
    _worker(queue, sleep_runner).serve(poll_sec=0, once=True)
    for jid in ids:
        job = queue.get(jid)
        assert job.state == "done" and job.attempts == 1
        assert job.result["steps_run"] == 2
        assert (queue.job_dir(jid) / "report.md").exists()
        assert job.result["handoff"]["report"].endswith("report.md")
        assert "running (attempt 1)" in (queue.job_dir(jid) / "worker.log").read_text()
    assert (queue.root / "heartbeat").exists()


def test_worker_marks_failure_and_exception(queue, repo):
    a, b = queue.submit("a", repo), queue.submit("b", repo)

    def runner(job, ctx):
        if job.goal == "a":
            return {"success": False, "error": "tests still red"}
        raise RuntimeError("boom")

    _worker(queue, runner).serve(poll_sec=0, once=True)
    assert (queue.get(a.id).state, queue.get(a.id).error) == ("failed", "tests still red")
    assert (queue.get(b.id).state, queue.get(b.id).error) == ("failed", "boom")


def test_stop_finishes_step_and_requeues_without_burning_attempt(queue, repo):
    job = queue.submit("g", repo)
    w = _worker(queue, None)
    steps = []

    def runner(j, ctx):
        for i in range(5):
            if ctx.should_stop():
                raise StopRequested("stop")
            steps.append(i)
            if i == 1:
                w.request_stop()  # as SIGTERM would, mid-step
            ctx.step(f"s{i}")
        return {"success": True}

    w.runner = runner
    w.serve(poll_sec=0, once=True)
    got = queue.get(job.id)
    assert steps == [0, 1]  # step 1 completed, step 2 never started
    assert (got.state, got.attempts) == ("queued", 0)


def test_cancel_running_job_at_step_boundary(queue, repo):
    job = queue.submit("g", repo)

    def runner(j, ctx):
        queue.cancel(j.id)
        if ctx.should_stop():
            raise StopRequested("cancel seen")
        return {"success": True}

    _worker(queue, runner).serve(poll_sec=0, once=True)
    assert queue.get(job.id).state == "cancelled"


def test_journal_shim_and_journal_failures_never_fail_job(queue, repo, monkeypatch):
    class BadJournal:
        def append(self, step):
            raise OSError("disk full")

        def steps(self):
            raise OSError("disk full")

        def replay_plan(self):
            raise OSError("disk full")

    monkeypatch.setattr(host_worker, "open_journal", lambda d: BadJournal())
    monkeypatch.setenv("AWOS_HOST_FAKE_STEP_SEC", "0")
    job = queue.submit("g", repo)
    _worker(queue, sleep_runner).serve(poll_sec=0, once=True)
    assert queue.get(job.id).state == "done"


def test_resume_skips_journaled_steps(queue, repo, monkeypatch):
    """With a journal that remembers, a re-run skips finished steps."""
    store: dict[str, list] = {}

    class MemJournal:
        def __init__(self, d):
            self.d = str(d)
            store.setdefault(self.d, [])

        def append(self, step):
            store[self.d].append(step)

        def steps(self):
            return list(store[self.d])

        def replay_plan(self):
            return {"done": [s["step"] for s in store[self.d]]}

    monkeypatch.setattr(host_worker, "open_journal", MemJournal)
    monkeypatch.setenv("AWOS_HOST_FAKE_STEPS", "3")
    monkeypatch.setenv("AWOS_HOST_FAKE_STEP_SEC", "0")
    job = queue.submit("g", repo)
    # Attempt 1 finishes two steps and then "crashes".
    claimed = queue.claim_next()
    MemJournal(queue.job_dir(job.id)).append({"step": "fake_step_1", "idempotency_key": "fake_step_1"})
    MemJournal(queue.job_dir(job.id)).append({"step": "fake_step_2", "idempotency_key": "fake_step_2"})
    assert claimed.attempts == 1
    seen = {}

    def runner(j, ctx):
        seen["replay"] = ctx.replay
        return sleep_runner(j, ctx)

    _worker(queue, runner).serve(poll_sec=0, once=True)  # recovers then reruns
    got = queue.get(job.id)
    assert (got.state, got.attempts) == ("done", 2)
    assert (got.result["steps_run"], got.result["steps_skipped"]) == (1, 2)
    assert "fake_step_1" in seen["replay"]["done"]


def test_handoff_is_called_and_its_failure_does_not_change_outcome(queue, repo):
    calls = []
    a, b = queue.submit("a", repo), queue.submit("b", repo)
    w = _worker(queue, lambda j, c: {"success": True})

    def handoff(job, workspace, outcome):
        calls.append((job["id"], workspace, outcome["state"]))
        if job["goal"] == "b":
            raise RuntimeError("git broke")
        return {"branch": f"awos/{job['id'][:8]}", "report": "r", "summary": "s"}

    w._handoff = handoff
    w.serve(poll_sec=0, once=True)
    assert calls == [(a.id, str(repo.resolve()), "done"), (b.id, str(repo.resolve()), "done")]
    assert queue.get(a.id).result["handoff"]["branch"] == f"awos/{a.id[:8]}"
    assert queue.get(b.id).state == "done"
    assert queue.get(b.id).result["handoff"] == {"error": "git broke"}


def test_single_worker_lock(queue):
    w1, w2 = HostWorker(queue, log=_quiet), HostWorker(queue, log=_quiet)
    h = w1._lock()
    try:
        with pytest.raises(RuntimeError):
            w2._lock()
    finally:
        h.close()


def test_child_env_budget_and_local_only(repo, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-not-printed")
    cloud = JobSpec.new("g", repo, budget_usd=0.25, privacy="cloud_ok")
    local = JobSpec.new("g", repo, privacy="local_only")
    env_c, env_l = child_env(cloud), child_env(local)
    assert env_c["AWOS_MAX_RUN_COST"] == "0.25" and env_c["OPENROUTER_API_KEY"]
    assert env_l["AWOS_JOB_PRIVACY"] == "local_only"
    assert all(env_l[k] == "" for k in host_worker.CLOUD_KEYS)


def test_resolve_runner(monkeypatch):
    assert resolve_runner("sleep") is sleep_runner
    assert resolve_runner(None) is host_worker.subprocess_runner
    monkeypatch.setenv("AWOS_HOST_RUNNER", "scaffold.agent.host.worker:sleep_runner")
    assert resolve_runner(None) is sleep_runner
    with pytest.raises(ValueError):
        resolve_runner("nonsense")


# ── CLI ───────────────────────────────────────────────────────────────────────


def _awos(root, *args, timeout=60):
    env = {**os.environ, "AWOS_HOST_DIR": str(root), "AWOS_HOST_FAKE_STEP_SEC": "0"}
    return subprocess.run([PY, str(REPO / "awos.py"), *args], cwd=str(REPO), env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_cli_parser_is_additive():
    import awos
    p = awos.build_parser()
    a = p.parse_args(["serve"])
    assert (a.worker, a.port) == (False, 8765)  # dashboard stays the default
    a = p.parse_args(["submit", "g"])
    assert (a.repo, a.root) == (None, ".")  # old job_host path unchanged
    a = p.parse_args(["submit", "g", "--repo", ".", "--budget", "2", "--privacy", "local_only"])
    assert (a.repo, a.budget, a.privacy) == (".", 2.0, "local_only")
    assert p.parse_args(["status"]).job_id is None
    assert p.parse_args(["cancel", "abc"]).job_id == "abc"


def test_cli_end_to_end(root, repo):
    r = _awos(root, "submit", "goal one", "--repo", str(repo), "--budget", "0.5")
    assert r.returncode == 0, r.stderr
    jid = r.stdout.strip().splitlines()[-1]
    r2 = _awos(root, "submit", "goal two", "--repo", str(repo))
    jid2 = r2.stdout.strip().splitlines()[-1]
    assert "cancelled" in _awos(root, "cancel", jid2).stdout
    assert "queued" in _awos(root, "status").stdout
    r = _awos(root, "serve", "--worker", "--once", "--runner", "sleep", "--poll", "0")
    assert r.returncode == 0, r.stderr
    show = _awos(root, "status", jid).stdout
    body = show.split("\n\nJob dir:")[0]
    assert json.loads(body[body.index("{"):])["state"] == "done"
    assert "cancelled" in _awos(root, "status").stdout


def test_cli_budget_without_repo_is_rejected(root):
    r = _awos(root, "submit", "g", "--budget", "1")
    assert r.returncode != 0 and "--repo" in r.stderr


def test_sigterm_on_serve_requeues_and_exits(root, repo):
    q = JobQueue(root)
    job = q.submit("g", repo)
    env = {**os.environ, "AWOS_HOST_DIR": str(root), "AWOS_HOST_FAKE_STEPS": "3",
           "AWOS_HOST_FAKE_STEP_SEC": "1.5"}
    proc = subprocess.Popen([PY, str(REPO / "awos.py"), "serve", "--worker", "--runner", "sleep",
                             "--poll", "0.2"], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 30
        while time.time() < deadline and q.get(job.id).state != "running":
            time.sleep(0.1)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    got = q.get(job.id)
    assert proc.returncode == 0, out
    assert (got.state, got.attempts) == ("queued", 0), out
    assert "stopped cleanly" in out


# ── subprocess runner, with a fake child script in place of job_host.py ──────

FAKE_CHILD = """
import json, os, signal, sys, time
job = json.load(open(sys.argv[2]))
caps = os.environ.get("FAKE_CHILD_CAPS")
if caps:
    with open(caps, "a") as fh:
        fh.write(json.dumps([os.environ.get("AWOS_MAX_RUN_COST"),
                             os.environ.get("AWOS_GOAL_BUDGET_USD")]) + "\\n")
spend = float(os.environ.get("FAKE_CHILD_SPEND", "0"))
if spend:
    ledger = os.environ["FAKE_CHILD_LEDGER"]
    entries = json.load(open(ledger)) if os.path.exists(ledger) else []
    entries.append({"cost": spend, "pid": os.getpid()})
    json.dump(entries, open(ledger, "w"))
time.sleep(float(os.environ.get("FAKE_CHILD_SLEEP", "0")))
if job["attempts"] <= int(os.environ.get("FAKE_CHILD_SIGKILL_UNTIL", "0")):
    os.kill(os.getpid(), signal.SIGKILL)
json.dump({"success": True, "goal": job["goal"], "root": job["root"],
           "cap": os.environ.get("AWOS_MAX_RUN_COST"),
           "goal_cap": os.environ.get("AWOS_GOAL_BUDGET_USD")}, open(sys.argv[3], "w"))
"""


@pytest.fixture
def fake_child(tmp_path, monkeypatch):
    from scaffold.agent import job_host
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD)
    monkeypatch.setattr(job_host, "__file__", str(script))
    # Keep the real repo's spend ledger and legacy host lock out of tests.
    monkeypatch.setattr(host_worker, "LEDGER_PATH", tmp_path / "budget.json", raising=False)
    monkeypatch.setattr(host_worker, "LEGACY_HOST_LOCK", tmp_path / "legacy" / "host.lock",
                        raising=False)
    monkeypatch.setenv("FAKE_CHILD_LEDGER", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FAKE_CHILD_CAPS", str(tmp_path / "caps.jsonl"))
    return script


def _caps(tmp_path):
    return [json.loads(line) for line in (tmp_path / "caps.jsonl").read_text().splitlines()]


def test_subprocess_runner_runs_child_and_records_pid(queue, repo, fake_child):
    job = queue.submit("real goal", repo, budget_usd=0.3)
    seen = {}
    orig = RunContext.on_child

    def spy(self, pid):
        seen["pid"] = pid
        orig(self, pid)
        seen["row"] = queue.get(self.job.id).child_pid

    RunContext.on_child = spy
    try:
        _worker(queue, host_worker.subprocess_runner).serve(poll_sec=0, once=True)
    finally:
        RunContext.on_child = orig
    got = queue.get(job.id)
    assert got.state == "done", got.error
    assert got.result["goal"] == "real goal" and got.result["cap"] == "0.3"
    assert seen["row"] == seen["pid"] and got.child_pid is None


def test_subprocess_runner_stop_terminates_child_and_requeues(queue, repo, fake_child, monkeypatch):
    monkeypatch.setenv("FAKE_CHILD_SLEEP", "30")
    monkeypatch.setenv("AWOS_HOST_STOP_GRACE_SEC", "5")
    job = queue.submit("g", repo)
    w = _worker(queue, host_worker.subprocess_runner)
    threading.Timer(1.0, w.request_stop).start()
    t0 = time.time()
    w.serve(poll_sec=0, once=True)
    assert time.time() - t0 < 15
    got = queue.get(job.id)
    assert (got.state, got.attempts) == ("queued", 0)


# ── review fixes (regressions) ────────────────────────────────────────────────


def test_child_env_budget_bounds_the_whole_goal(repo, monkeypatch):
    """budget_usd sets the goal cap too, not only the per-task cap; 0 = no cap."""
    monkeypatch.setenv("AWOS_GOAL_BUDGET_USD", "2.0")
    monkeypatch.setenv("AWOS_MAX_RUN_COST", "9")
    env = child_env(JobSpec.new("g", repo, budget_usd=0.1))
    assert (env["AWOS_GOAL_BUDGET_USD"], env["AWOS_MAX_RUN_COST"]) == ("0.1", "0.1")
    env = child_env(JobSpec.new("g", repo, budget_usd=0))
    assert env["AWOS_GOAL_BUDGET_USD"] == "0" and "AWOS_MAX_RUN_COST" not in env


def test_retry_after_signal_kill_gets_only_the_budget_left(queue, repo, fake_child, tmp_path,
                                                           monkeypatch):
    """An OOM-style kill is retried, and the retry's caps exclude attempt 1's spend."""
    monkeypatch.setenv("FAKE_CHILD_SPEND", "0.4")
    monkeypatch.setenv("FAKE_CHILD_SIGKILL_UNTIL", "1")
    job = queue.submit("g", repo, budget_usd=1.0)
    _worker(queue, host_worker.subprocess_runner).serve(poll_sec=0, once=True)
    got = queue.get(job.id)
    assert (got.state, got.attempts) == ("done", 2), got.error
    assert _caps(tmp_path) == [["1", "1"], ["0.6", "0.6"]]
    spent = json.loads((queue.job_dir(job.id) / "spend.json").read_text())
    assert spent["prior_usd"] == pytest.approx(0.8) and spent["open"] is None


def test_retry_fails_when_earlier_attempts_used_the_budget(queue, repo, fake_child, tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("FAKE_CHILD_SPEND", "1.0")
    monkeypatch.setenv("FAKE_CHILD_SIGKILL_UNTIL", "1")
    job = queue.submit("g", repo, budget_usd=1.0)
    _worker(queue, host_worker.subprocess_runner).serve(poll_sec=0, once=True)
    got = queue.get(job.id)
    assert (got.state, got.attempts) == ("failed", 2), got.error
    assert "used up by earlier attempts" in got.error
    assert len(_caps(tmp_path)) == 1  # no second child was started


def test_signal_killed_child_is_retried_then_fails_after_max_attempts(queue, repo, fake_child,
                                                                      monkeypatch):
    monkeypatch.setenv("FAKE_CHILD_SIGKILL_UNTIL", "99")
    monkeypatch.setenv("AWOS_HOST_MAX_ATTEMPTS", "2")
    job = queue.submit("g", repo, budget_usd=0)
    _worker(queue, host_worker.subprocess_runner).serve(poll_sec=0, once=True)
    got = queue.get(job.id)
    assert (got.state, got.attempts) == ("failed", 2)
    assert "killed by signal 9" in got.error


def test_interrupted_attempt_spend_counts_from_its_last_snapshot(tmp_path, monkeypatch):
    ledger = tmp_path / "budget.json"
    monkeypatch.setattr(host_worker, "LEDGER_PATH", ledger, raising=False)
    ledger.write_text(json.dumps([{"cost": 5.0}]))
    t = host_worker.SpendTracker(tmp_path / "job")
    t.start(1)
    ledger.write_text(json.dumps([{"cost": 5.0}, {"cost": 0.25}]))
    assert t.snapshot() == pytest.approx(0.25)
    # the worker is killed here; spend after the snapshot is not this attempt's
    ledger.write_text(json.dumps([{"cost": 5.0}, {"cost": 0.25}, {"cost": 3.0}]))
    assert host_worker.SpendTracker(tmp_path / "job").prior() == pytest.approx(0.25)


def test_local_only_blanks_every_cloud_api_key(repo, monkeypatch, tmp_path):
    keys = ("TAVILY_API_KEY", "SERP_API_KEY", "LLM_API_KEY", "SOMENEWPROVIDER_API_KEY")
    for k in keys:
        monkeypatch.setenv(k, "secret-not-printed")
    monkeypatch.setenv("AWOS_LOCAL_API_KEY", "local-key")
    env = child_env(JobSpec.new("g", repo, privacy="local_only"))
    assert all(env[k] == "" for k in keys)
    assert env["AWOS_LOCAL_API_KEY"] == "local-key"
    assert child_env(JobSpec.new("g", repo))["TAVILY_API_KEY"] == "secret-not-printed"
    # a key only in the repo .env would be refilled by dotenv: it is blanked too
    dotenv = tmp_path / ".env"
    dotenv.write_text("# x\nexport DOTENV_ONLY_API_KEY=abc\nLOCAL_API_KEY=keep\n")
    names = host_worker.local_only_blanked_keys({}, dotenv_path=dotenv)
    assert "DOTENV_ONLY_API_KEY" in names and "LOCAL_API_KEY" not in names


def test_default_runner_excludes_the_legacy_host(queue, repo, fake_child, monkeypatch):
    """Legacy `awos host` and the default runner share the spend ledger: never both."""
    import fcntl
    lock_path = host_worker.LEGACY_HOST_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    job = queue.submit("g", repo)
    with open(lock_path, "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="legacy"):
            _worker(queue, host_worker.subprocess_runner).serve(poll_sec=0, once=True)
        assert queue.get(job.id).state == "queued"
        HostWorker(queue, log=_quiet)._lock().close()  # the worker lock was released
    seen = {}

    def probe(job, ctx, *rest):
        with open(lock_path, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen["legacy_could_start"] = True
            except OSError:
                seen["legacy_could_start"] = False
        return {"success": True}

    monkeypatch.setattr(host_worker, "_run_child", probe)
    _worker(queue, host_worker.subprocess_runner).serve(poll_sec=0, once=True)
    assert seen == {"legacy_could_start": False}
    assert queue.get(job.id).state == "done"


def test_losing_worker_does_not_wipe_live_worker_pid(queue):
    w1, w2 = HostWorker(queue, log=_quiet), HostWorker(queue, log=_quiet)
    h = w1._lock()
    try:
        with pytest.raises(RuntimeError):
            w2._lock()
        assert (queue.root / "worker.lock").read_text() == str(os.getpid())
    finally:
        h.close()


def test_get_prefix_treats_like_wildcards_literally(queue, repo):
    job = queue.submit("g", repo)
    assert queue.get("%%%%") is None
    assert queue.get("______") is None
    assert queue.get(job.id[:6]).id == job.id
