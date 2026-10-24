"""licence: cache, daily revalidation, 14-day offline grace, free-tier counter and gate."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scaffold.agent import licence

KEY = "awos_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
DAY = 86400
T0 = datetime(2026, 10, 15, 12, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_HOME", str(tmp_path / "awoshome"))
    monkeypatch.delenv("AWOS_FREE_JOBS_PER_MONTH", raising=False)
    monkeypatch.setenv("AWOS_LICENCE_URL", "https://licence.invalid/api/license/check")
    return tmp_path / "awoshome"


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setattr(licence, "ENABLED_IN_TESTS", True)


class Server:
    """Fake /api/license/check."""

    def __init__(self, answer=None, status=200):
        self.answer = answer if answer is not None else {
            "active": True, "plan": "pro", "current_period_end": "2026-11-15T00:00:00Z",
            "grace_until": None}
        self.status = status
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload))
        if self.status is None:
            return None, None
        return self.status, self.answer


# ── activation + cache ───────────────────────────────────────────────────────


def test_activate_stores_key_private_and_reports_active(home, capsys):
    srv = Server()
    assert licence.activate(KEY.lower().replace("awos_", "AWOS_", 1).replace("AWOS_", "awos_"), http=srv) == 0
    assert srv.calls[0][0] == "https://licence.invalid/api/license/check"
    assert srv.calls[0][1]["key"] == KEY
    path = home / "licence.json"
    data = json.loads(path.read_text())
    assert data["key"] == KEY and data["active"] is True
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "AWOS Pro is active" in capsys.readouterr().out


def test_activate_rejects_malformed_key_without_network(home):
    srv = Server()
    assert licence.activate("sk-ant-not-a-licence", http=srv) == 2
    assert srv.calls == []


def test_revalidates_at_most_daily(home):
    srv = Server()
    licence.revalidate(KEY, http=srv, now=T0)
    assert licence.is_pro(http=srv, now=T0 + 3600) is True
    assert licence.is_pro(http=srv, now=T0 + 20 * 3600) is True
    assert len(srv.calls) == 1
    assert licence.is_pro(http=srv, now=T0 + DAY + 1) is True
    assert len(srv.calls) == 2


def test_offline_grace_is_14_days_from_last_good_check(home):
    srv = Server()
    licence.revalidate(KEY, http=srv, now=T0)
    srv.status = None  # offline from now on
    assert licence.is_pro(http=srv, now=T0 + 2 * DAY) is True
    assert licence.is_pro(http=srv, now=T0 + 13 * DAY) is True
    assert licence.is_pro(http=srv, now=T0 + 15 * DAY) is False
    srv.status = 200  # back online
    assert licence.is_pro(http=srv, now=T0 + 16 * DAY) is True


def test_offline_retry_is_hourly_not_daily(home):
    srv = Server()
    licence.revalidate(KEY, http=srv, now=T0)
    srv.status = None
    licence.is_pro(http=srv, now=T0 + DAY + 1)
    n = len(srv.calls)
    licence.is_pro(http=srv, now=T0 + DAY + 600)
    assert len(srv.calls) == n
    licence.is_pro(http=srv, now=T0 + DAY + 3700)
    assert len(srv.calls) == n + 1


def test_server_saying_inactive_ends_pro_immediately(home):
    srv = Server()
    licence.revalidate(KEY, http=srv, now=T0)
    srv.answer = {"active": False, "plan": "pro", "current_period_end": None, "grace_until": None}
    assert licence.is_pro(http=srv, now=T0 + DAY + 1) is False


def test_no_key_is_not_pro_and_makes_no_call(home):
    srv = Server()
    assert licence.is_pro(http=srv) is False
    assert srv.calls == []


# ── free tier ────────────────────────────────────────────────────────────────


def test_gate_disabled_under_pytest_by_default(home):
    for i in range(40):
        assert licence.gate_job_start(f"job{i}", "anthropic").allowed
    assert not (home / "usage.json").exists()


def test_free_tier_counts_cloud_jobs_and_blocks_over_limit(home, gate_on, monkeypatch):
    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "3")
    srv = Server()
    for i in range(3):
        g = licence.gate_job_start(f"job{i}", "anthropic", http=srv, now=T0)
        assert g.allowed and g.counted and g.used == i + 1
    # a retry of a counted job is not counted again and still runs
    assert licence.gate_job_start("job1", "anthropic", http=srv, now=T0).allowed
    g = licence.gate_job_start("job3", "anthropic", http=srv, now=T0)
    assert not g.allowed
    assert "3 free cloud-model jobs" in g.message
    assert "https://awos.tirramind.com/pro/" in g.message or "/pro/" in g.message
    assert "awos activate" in g.message
    assert licence.jobs_used(T0) == 3
    assert srv.calls == []  # no licence stored -> no network


def test_local_model_jobs_are_unlimited_and_uncounted(home, gate_on, monkeypatch):
    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "1")
    for i in range(10):
        assert licence.gate_job_start(f"l{i}", "local", now=T0).allowed
    assert licence.jobs_used(T0) == 0
    assert licence.gate_job_start("c0", "anthropic", now=T0).allowed
    assert not licence.gate_job_start("c1", "anthropic", now=T0).allowed
    assert licence.gate_job_start("l99", "local", now=T0).allowed


def test_counter_resets_each_month(home, gate_on, monkeypatch):
    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "1")
    assert licence.gate_job_start("a", "anthropic", now=T0).allowed
    assert not licence.gate_job_start("b", "anthropic", now=T0).allowed
    nov = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc).timestamp()
    assert licence.gate_job_start("b", "anthropic", now=nov).allowed
    assert licence.jobs_used(nov) == 1


def test_pro_is_unlimited(home, gate_on, monkeypatch):
    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "0")
    srv = Server()
    licence.revalidate(KEY, http=srv, now=T0)
    for i in range(30):
        g = licence.gate_job_start(f"p{i}", "anthropic", http=srv, now=T0 + 60)
        assert g.allowed and g.pro


def test_default_limit_constant_and_env_override(monkeypatch):
    monkeypatch.delenv("AWOS_FREE_JOBS_PER_MONTH", raising=False)
    assert licence.FREE_JOBS_PER_MONTH == 25 == licence.free_jobs_per_month()
    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "100")
    assert licence.free_jobs_per_month() == 100
    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "lots")
    assert licence.free_jobs_per_month() == 25


def test_gate_never_raises(home, gate_on):
    (home).mkdir(parents=True, exist_ok=True)
    (home / "usage.json").write_text("{broken")
    (home / "licence.json").write_text("[]")
    assert licence.gate_job_start("x", "anthropic", http=Server(status=None), now=T0).allowed


# ── host worker integration ──────────────────────────────────────────────────


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "base"], check=True)
    return repo


def test_worker_blocks_over_limit_job_without_running_it(home, gate_on, tmp_path, monkeypatch):
    from scaffold.agent.host import worker
    from scaffold.agent.host.models import BLOCKED, DONE
    from scaffold.agent.host.queue import JobQueue

    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("AWOS_PROVIDER", raising=False)
    monkeypatch.delenv("AWOS_AGENT_MODEL", raising=False)
    ran = []
    q = JobQueue(tmp_path / "host")
    w = worker.HostWorker(q, runner=lambda job, ctx: ran.append(job.id) or {"success": True},
                          log=lambda m: None, key_checker=None,
                          notifier=lambda *a, **k: None)
    repo = _repo(tmp_path)
    a, b = q.submit("job a", repo), q.submit("job b", repo)
    w.serve(once=True)
    assert ran == [a.id]
    assert q.get(a.id).state == DONE
    jb = q.get(b.id)
    assert jb.state == BLOCKED and jb.error == "blocked: free tier limit"
    assert jb.result["blocked"] == "free_tier"
    assert "/pro/" in jb.result["summary"]


def test_worker_local_only_job_not_gated(home, gate_on, tmp_path, monkeypatch):
    from scaffold.agent.host import worker
    from scaffold.agent.host.models import DONE
    from scaffold.agent.host.queue import JobQueue

    monkeypatch.setenv("AWOS_FREE_JOBS_PER_MONTH", "0")
    q = JobQueue(tmp_path / "host")
    w = worker.HostWorker(q, runner=lambda job, ctx: {"success": True}, log=lambda m: None,
                          key_checker=None, notifier=lambda *a, **k: None)
    repo = _repo(tmp_path)
    j = q.submit("job", repo, privacy="local_only")
    w.serve(once=True)
    assert q.get(j.id).state == DONE
