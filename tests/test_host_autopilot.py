"""Supervision autopilot in the host worker: credentials block, notify, digest, auto-continue."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scaffold.agent import key_health as kh
from scaffold.agent.host import autopilot, worker
from scaffold.agent.host.models import BLOCKED, DONE, FAILED, QUEUED
from scaffold.agent.host.queue import JobQueue


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


class Notes:
    def __init__(self):
        self.events = []

    def __call__(self, event, job_id, summary, report, root=None, dedupe_key=None):
        from scaffold.agent.notify import notify
        rec = notify(event, job_id, summary, report, root=root, dedupe_key=dedupe_key)
        if rec:
            self.events.append(rec)
        return rec


def _checks(status):
    return lambda: [kh.KeyCheck("anthropic", status, "ANTHROPIC_API_KEY", f"HTTP {status}",
                                "replace ANTHROPIC_API_KEY in .env"),
                    kh.KeyCheck("local", kh.NOT_CONFIGURED, "AWOS_LOCAL_BASE_URL", ""),
                    kh.KeyCheck("openrouter", kh.NOT_CONFIGURED, "OPENROUTER_API_KEY", "")]


def _worker(tmp_path, runner, checker=None):
    q = JobQueue(tmp_path / "host")
    notes = Notes()
    w = worker.HostWorker(q, runner=runner, log=lambda m: None, key_checker=checker, notifier=notes)
    w.precheck = True  # conftest sets AWOS_HOST_KEY_CHECK=0; the checker here is fake
    return q, w, notes


def _inbox(q):
    p = q.root / "inbox.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# ── credentials ──────────────────────────────────────────────────────────────


def test_bad_key_blocks_before_run_and_notifies_once(tmp_path):
    ran = []
    q, w, notes = _worker(tmp_path, lambda job, ctx: ran.append(1) or {"success": True},
                          checker=_checks(kh.UNAUTHORIZED))
    repo = _repo(tmp_path)
    a, b = q.submit("job a", repo), q.submit("job b", repo)
    w.serve(once=True)
    assert ran == []
    for jid in (a.id, b.id):
        job = q.get(jid)
        assert job.state == BLOCKED and job.error.startswith("blocked: credentials")
        assert job.result["action"] == "replace ANTHROPIC_API_KEY in .env"
        assert (q.job_dir(jid) / "blocked.md").exists()
    blocked = [e for e in _inbox(q) if e["event"] == "blocked"]
    assert len(blocked) == 1  # two jobs parked, one notification
    # not retried in a loop: a second pass leaves them blocked and silent
    w._last_unblock_check = float("-inf")
    w.serve(once=True)
    assert q.get(a.id).state == BLOCKED and len(_inbox(q)) == 1
    assert "replace ANTHROPIC_API_KEY" in (q.root / "digest.md").read_text()


def test_auth_error_in_result_blocks_instead_of_failing(tmp_path):
    state = {"status": kh.OK}
    q, w, _ = _worker(tmp_path, lambda job, ctx: {
        "success": False,
        "error": "Error code: 401 - {'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}}"},
        checker=lambda: _checks(state["status"])())
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    got = q.get(job.id)
    assert got.state == BLOCKED and got.attempts == 1


def test_keys_fixed_unblocks_and_runs(tmp_path):
    state = {"status": kh.UNAUTHORIZED}
    q, w, notes = _worker(tmp_path, lambda job, ctx: {"success": True},
                          checker=lambda: _checks(state["status"])())
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    assert q.get(job.id).state == BLOCKED
    state["status"] = kh.OK
    w._last_unblock_check = float("-inf")
    w.serve(once=True)
    got = q.get(job.id)
    assert got.state == DONE and got.attempts == 1  # the blocked attempt was refunded
    assert [e["event"] for e in notes.events] == ["blocked", "unblocked", "done"]
    # a new credentials problem after the fix notifies again (dedupe was cleared)
    state["status"] = kh.UNAUTHORIZED
    q.submit("g2", job.repo_path)
    w.serve(once=True)
    assert [e["event"] for e in notes.events][-1] == "blocked"


def test_unblock_and_cancel_blocked(tmp_path):
    q, w, _ = _worker(tmp_path, lambda job, ctx: {"success": True}, checker=_checks(kh.UNAUTHORIZED))
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    assert q.unblock(job.id) and q.get(job.id).state == QUEUED
    assert not q.unblock(job.id)
    w.serve(once=True)
    assert q.cancel(job.id) == "cancelled"


# ── notify on final state + digest ───────────────────────────────────────────


def test_final_state_notifies_with_report_and_writes_digest(tmp_path):
    q, w, notes = _worker(tmp_path, lambda job, ctx: {
        "success": True, "cost_usd": 0.0123, "tests": {"status": "passed", "passed": 3, "failed": 0}})
    job = q.submit("add a feature", _repo(tmp_path))
    w.serve(once=True)
    (ev,) = _inbox(q)
    assert ev["event"] == "done" and ev["job_id"] == job.id
    assert "tests passed 3p/0f" in ev["summary"] and "$0.0123" in ev["summary"]
    assert ev["report"].endswith("report.md")
    digest = (q.root / "digest.md").read_text()
    assert job.id[:8] in digest and "done" in digest and "Queued:** 0" in digest
    # owner digest: shows the finish once, then "0 since last digest"
    first = autopilot.digest_for_owner(q)
    assert "Finished (all time): 1" in first
    assert "Finished (since last digest): 0" in autopilot.digest_for_owner(q)


def test_failure_notifies(tmp_path):
    q, w, _ = _worker(tmp_path, lambda job, ctx: {"success": False, "error": "tests failed: 2"})
    q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    (ev,) = _inbox(q)
    assert ev["event"] == "failed" and "tests failed: 2" in ev["summary"]


# ── auto-continue ────────────────────────────────────────────────────────────


def _limit_runner(edit_on_attempts, notes_seen, limit_on=(1,)):
    def runner(job, ctx):
        ws, base = worker.prepare_workspace(job, ctx.job_dir)
        autopilot.record_attempt_start(ctx.job_dir, ws, base, job.attempts)
        notes_seen.append(autopilot.take_continue_note(ctx.job_dir))
        if job.attempts in edit_on_attempts:
            p = Path(ws) / "a.py"
            p.write_text(p.read_text() + f"y{job.attempts} = 2\n")
        if job.attempts in limit_on:
            return {"success": False, "error": "it ran out of turns (max_turns)",
                    "summary": "added half the feature", "workspace": ws, "base_commit": base}
        return {"success": True, "workspace": ws, "base_commit": base}
    return runner


def test_limit_stop_with_progress_continues_once_in_same_workspace(tmp_path):
    seen = []
    q, w, notes = _worker(tmp_path, _limit_runner({1, 2}, seen))
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    got = q.get(job.id)
    assert got.state == DONE and got.attempts == 2
    assert seen[0] is None and seen[1].startswith("continue from where you stopped: added half the feature")
    assert "files changed so far: a.py" in seen[1]
    assert [e["event"] for e in notes.events] == ["continued", "done"]


def test_no_progress_no_continuation(tmp_path):
    seen = []
    q, w, _ = _worker(tmp_path, _limit_runner(set(), seen))
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    got = q.get(job.id)
    assert got.state == FAILED and got.attempts == 1
    assert "no auto-continue: no progress" in got.error
    assert got.result["autocontinue"]["continued"] is False


def test_continuation_is_bounded(tmp_path, monkeypatch):
    seen = []
    q, w, _ = _worker(tmp_path, _limit_runner({1, 2, 3}, seen, limit_on=(1, 2, 3)))
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    got = q.get(job.id)
    assert got.state == FAILED and got.attempts == 2
    assert "auto-continue used 1/1" in got.error
    monkeypatch.setenv("AWOS_HOST_AUTOCONTINUE", "0")
    seen2 = []
    q2, w2, _ = _worker(tmp_path / "b", _limit_runner({1}, seen2))
    (tmp_path / "b").mkdir(exist_ok=True)
    j2 = q2.submit("g", _repo(tmp_path / "b"))
    w2.serve(once=True)
    assert q2.get(j2.id).state == FAILED and q2.get(j2.id).attempts == 1


def test_continuation_respects_budget(tmp_path):
    seen = []
    q, w, _ = _worker(tmp_path, _limit_runner({1}, seen))
    job = q.submit("g", _repo(tmp_path), budget_usd=0.05)
    (q.job_dir(job.id) / "spend.json").write_text(json.dumps({"prior_usd": 0.05, "open": None}))
    w.serve(once=True)
    got = q.get(job.id)
    assert got.state == FAILED and "budget" in got.error


def test_hard_failure_never_continues(tmp_path):
    seen = []

    def runner(job, ctx):
        ws, base = worker.prepare_workspace(job, ctx.job_dir)
        autopilot.record_attempt_start(ctx.job_dir, ws, base, job.attempts)
        (Path(ws) / "a.py").write_text("broken\n")
        return {"success": False, "error": "SyntaxError in planner", "workspace": ws, "base_commit": base}
    q, w, _ = _worker(tmp_path, runner)
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    assert q.get(job.id).state == FAILED and q.get(job.id).attempts == 1


def test_classify_stop():
    assert autopilot.classify_stop("it ran out of turns (max_turns)") == autopilot.LIMIT
    assert autopilot.classify_stop("timed out after 60 min (AWOS_JOB_TIMEOUT_MIN)") == autopilot.LIMIT
    assert autopilot.classify_stop("x", {"error": "wall_budget"}) == autopilot.LIMIT
    assert autopilot.classify_stop("child exited with code 1", log="anthropic.AuthenticationError: "
                                   "Error code: 401 invalid x-api-key") == autopilot.CREDENTIALS
    assert autopilot.classify_stop("2 test(s) failed after its edits") == autopilot.HARD


def test_real_child_gets_continue_note(tmp_path, monkeypatch):
    """_run_child writes the note into the child's goal (job file), once."""
    from scaffold.agent.host.models import JobSpec
    repo = _repo(tmp_path)
    q = JobQueue(tmp_path / "host")
    job = q.submit("do the thing", repo)
    jdir = q.job_dir(job.id)
    autopilot.queue_continuation(jdir, "half done", "diff changed")
    captured = {}

    class Stop(Exception):
        pass

    real_popen = subprocess.Popen

    def fake_popen(cmd, *a, **kw):
        if "_child" not in cmd:
            return real_popen(cmd, *a, **kw)
        captured["job"] = json.loads((jdir / "job_host_job.json").read_text())
        raise Stop
    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    ctx = worker.RunContext(job, q, worker._NullJournal(), lambda: False, lambda m: None)
    try:
        worker._run_child(job, ctx, jdir / "job_host_job.json", jdir / "r.json", 1.0,
                          worker.SpendTracker(jdir))
    except Stop:
        pass
    assert "NOTE: continue from where you stopped: half done" in captured["job"]["goal"]
    assert autopilot.take_continue_note(jdir) is None


def test_spend_survives_ledger_month_rollover(tmp_path, monkeypatch):
    """Live run 2026-10-11: the ledger dropped last month's 8 entries mid-run,
    so the index taken at attempt start pointed past the end and $0 was billed."""
    ledger = tmp_path / "budget.json"
    monkeypatch.setattr(worker, "LEDGER_PATH", ledger)
    ledger.write_text(json.dumps([{"cost": 1.0, "pid": 1}] * 8))
    spend = worker.SpendTracker(tmp_path / "job")
    spend.start(1)
    spend.set_pid(4242)
    ledger.write_text(json.dumps([{"cost": 0.001, "pid": 4242}, {"cost": 5.0, "pid": 7}]))
    assert abs(spend.close() - 0.001) < 1e-9


# ── deferred rollback: a stopped attempt's edits survive for the continuation ──


def test_git_manager_defers_rollback(tmp_path, monkeypatch):
    from scaffold.agent.git_manager import GitManager, apply_deferred
    f = tmp_path / "x.py"
    f.write_text("orig\n")
    gm = GitManager(str(tmp_path))
    gm.backup_file(str(f))
    f.write_text("edited\n")
    deferred = tmp_path / "d.jsonl"
    monkeypatch.setenv("AWOS_DEFER_ROLLBACK", str(deferred))
    gm.rollback_all()
    assert f.read_text() == "edited\n" and deferred.exists()
    assert apply_deferred(str(deferred)) == 1
    assert f.read_text() == "orig\n" and not deferred.exists()


def _deferring_runner(limit_on=(1,)):
    """Edits a.py, records the rollback the orchestrator would have applied."""
    from scaffold.agent.git_manager import record_restore

    def runner(job, ctx):
        ws, base = worker.prepare_workspace(job, ctx.job_dir)
        autopilot.record_attempt_start(ctx.job_dir, ws, base, job.attempts)
        autopilot.take_continue_note(ctx.job_dir)
        p = Path(ws) / "a.py"
        before = p.read_text()
        p.write_text(before + f"z{job.attempts} = 3\n")
        if job.attempts in limit_on:
            record_restore(str(ctx.job_dir / worker.DEFERRED_ROLLBACK), str(p), before)
            return {"success": False, "error": "AgentLoop stopped: max_turns", "workspace": ws,
                    "base_commit": base}
        return {"success": True, "workspace": ws, "base_commit": base}
    return runner


def _final_a_py(monkeypatch):
    seen = {}
    monkeypatch.setattr(worker, "retire_workspace",
                        lambda j, d, *a, **k: seen.setdefault("a", (d / "workspace" / "a.py").read_text()))
    return seen


def test_continuation_keeps_deferred_edits(tmp_path, monkeypatch):
    seen = _final_a_py(monkeypatch)
    q, w, _ = _worker(tmp_path, _deferring_runner())
    job = q.submit("g", _repo(tmp_path))
    w.serve(once=True)
    assert q.get(job.id).state == DONE
    assert "z1 = 3" in seen["a"] and "z2 = 3" in seen["a"]  # attempt 1's edits were kept


def test_failed_job_keeps_everything_it_did(tmp_path, monkeypatch):
    """v0.2.2 atomic outcome: a failed job is never half-reverted. Its deferred
    rollback is dropped, and the whole workspace goes to awos/<id>-failed."""
    monkeypatch.setenv("AWOS_HOST_AUTOCONTINUE", "0")
    seen = _final_a_py(monkeypatch)
    q, w, notes = _worker(tmp_path, _deferring_runner())
    repo = _repo(tmp_path)
    job = q.submit("g", repo)
    w.serve(once=True)
    done = q.get(job.id)
    assert done.state == FAILED
    assert seen["a"] == "x = 1\nz1 = 3\n"          # nothing rolled back
    assert not (q.job_dir(job.id) / worker.DEFERRED_ROLLBACK).exists()
    branch = done.result["handoff"]["branch"]
    assert branch == f"awos/{job.id.replace('-', '')[:8]}-failed"
    assert done.result["handoff"]["status"] == "handed_off_failed"
    shown = _git(repo, "show", f"{branch}:a.py")
    assert shown == "x = 1\nz1 = 3"                 # the branch holds all of it
    report = Path(done.result["handoff"]["report_path"]).read_text()
    assert "FAILED: the job did not finish" in report and "EVERYTHING it did" in report
    failed_note = [e for e in notes.events if e.get("event") == "failed"]
    assert failed_note and "-failed (failed: everything the job did" in failed_note[-1]["summary"]
