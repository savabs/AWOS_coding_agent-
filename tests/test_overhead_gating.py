"""Ablation 2: fixed-overhead gating of the planner and the integration reviewer.

Over 48 pinned jobs the planner (~53 s) and the reviewer (~47 s) were ~31% of
job wall time; 71 of 72 plans collapsed into one task anyway, and the review
is advisory. AWOS_PLANNER=auto skips the planner for a goal whose exploration
hit few files; AWOS_INTEGRATION_REVIEW=auto reviews only multi-task goals;
AWOS_REVIEW_TIMEOUT_S caps the review call (it once hung jobs for 754 s).
No network: planners, executors and the model client are fakes.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scaffold.agent import integration_reviewer as ir
from scaffold.agent import orchestrator as orch_mod
from scaffold.agent.orchestrator import Orchestrator

GOAL = "Add a `list` command that prints one line per backup"
RECORD = "scaffold.agent.usage_record.record_api_usage"


class _CountingPlanner:
    """Stands in for every planner; plans two tasks over `files` files."""

    calls = 0
    files = ["a.py", "b.py", "c.py", "d.py", "e.py"]

    def __init__(self, *args, **kwargs):
        pass

    def plan(self, *args, **kwargs):
        type(self).calls += 1
        half = len(self.files) // 2
        return {"plan": [
            {"task_id": 1, "files": self.files[:half], "action": "part one", "complexity": "low"},
            {"task_id": 2, "files": self.files[half:], "action": "part two", "complexity": "low"},
        ]}


class _Scripted:
    """OpenAI-shaped client: replies `content`, or sleeps `delay` seconds first."""

    def __init__(self, content="VERDICT: PASS\nISSUES:\nSUGGESTIONS:\n", delay=0.0):
        self.calls: list = []
        self._content = content
        self._delay = delay
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._delay:
            time.sleep(self._delay)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


def _exploration(n_files: int) -> dict:
    files = [f"src/m{i}.py" for i in range(n_files)]
    return {"exploration_summary": "notes", "keywords": ["list"],
            "grep_hits": [{"file": f, "line": 1, "text": "x"} for f in files],
            "candidate_files": files, "hit_files": files}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("AWOS_E2E", "1")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
    monkeypatch.setenv("AWOS_GOAL_CHECK", "0")
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    monkeypatch.setenv("AWOS_EXPLORATION_PHASE", "1")
    monkeypatch.setenv("AWOS_AGENT_MODEL", "fake/model")
    for name in ("AWOS_PLANNER", "AWOS_PLANNER_MAX_FILES", "AWOS_INTEGRATION_REVIEW",
                 "AWOS_REVIEW_TIMEOUT_S", "AWOS_REVIEW_MODEL", "AWOS_PLANNER_MODEL",
                 "AWOS_SINGLE_TASK_MAX_FILES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(orch_mod, "is_cheap_only", lambda: False)
    _CountingPlanner.calls = 0
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return repo


def _run(repo, monkeypatch, n_hit_files: int, client=None):
    """execute_feature with fake exploration, planners, executor and reviewer client."""
    monkeypatch.setattr(orch_mod, "run_exploration", lambda goal, root: _exploration(n_hit_files))
    orch = Orchestrator()
    orch.planner = _CountingPlanner()
    orch.integration_reviewer = ir.IntegrationReviewer(client=client or _Scripted())
    orch.integration_reviewer._get_diff = lambda _root: "+x = 1\n"
    executed: list = []

    def mock_execute(task, ctx):
        executed.append(task)
        return {"task_id": task["task_id"], "success": True, "task": task}

    with patch.object(orch_mod, "CheapPlanner", _CountingPlanner), \
         patch.object(orch, "_execute_single_task", side_effect=mock_execute), \
         patch(RECORD), \
         patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
        report = orch.execute_feature(goal=GOAL, codebase_root=str(repo), auto_approve_plan=True)
    return report, executed


# ── planner gate ─────────────────────────────────────────────────────────────

def test_auto_skips_planner_for_small_goal(env, monkeypatch, capsys):
    report, executed = _run(env, monkeypatch, n_hit_files=6)
    assert _CountingPlanner.calls == 0
    assert report["plan_source"] == "skipped_small_goal"
    assert len(executed) == 1 and executed[0]["action"] == GOAL
    assert executed[0]["file"] == ""
    out = capsys.readouterr().out
    assert "[PLANNER] skipped: small goal (6 file(s) hit ≤ AWOS_PLANNER_MAX_FILES=6)" in out


def test_auto_plans_when_more_files_hit(env, monkeypatch):
    monkeypatch.setenv("AWOS_SINGLE_TASK_MAX_FILES", "0")  # keep the 2-task split
    report, executed = _run(env, monkeypatch, n_hit_files=7)
    assert _CountingPlanner.calls == 1
    assert report["plan_source"] == "planner"
    assert len(executed) == 2


def test_max_files_env_moves_the_threshold(env, monkeypatch):
    monkeypatch.setenv("AWOS_PLANNER_MAX_FILES", "2")
    report, _ = _run(env, monkeypatch, n_hit_files=3)
    assert report["plan_source"] == "planner"


def test_never_skips_even_a_large_goal(env, monkeypatch):
    monkeypatch.setenv("AWOS_PLANNER", "never")
    report, executed = _run(env, monkeypatch, n_hit_files=20)
    assert _CountingPlanner.calls == 0
    assert report["plan_source"] == "skipped_by_env"
    assert len(executed) == 1


def test_always_plans_a_small_goal(env, monkeypatch):
    monkeypatch.setenv("AWOS_PLANNER", "always")
    report, _ = _run(env, monkeypatch, n_hit_files=1)
    assert _CountingPlanner.calls == 1
    assert report["plan_source"] == "planner"


def test_no_exploration_data_keeps_the_planner(monkeypatch):
    monkeypatch.delenv("AWOS_PLANNER", raising=False)
    assert orch_mod._planner_skip_reason({}) is None
    assert orch_mod._planner_skip_reason(_exploration(0)) is not None


def test_gate_only_for_agent_loop(env, monkeypatch):
    monkeypatch.setenv("AWOS_EXECUTOR", "worker")
    monkeypatch.setattr(orch_mod, "run_exploration", lambda goal, root: _exploration(1))
    orch = Orchestrator()
    with patch.object(orch, "_plan_goal",
                      return_value=({"plan": []}, "planner", [])) as plan_goal:
        try:
            orch.execute_feature(goal=GOAL, codebase_root=str(env), auto_approve_plan=True)
        except Exception:
            pass
    assert plan_goal.called


# ── reviewer gate ────────────────────────────────────────────────────────────

def test_review_skipped_for_single_task_in_auto(env, monkeypatch):
    client = _Scripted()
    report, _ = _run(env, monkeypatch, n_hit_files=2, client=client)
    assert report["integration_review"]["skipped"] is True
    assert report["integration_review"]["skip_reason"] == "single task"
    assert client.calls == []


def test_review_runs_for_two_tasks_in_auto(env, monkeypatch):
    monkeypatch.setenv("AWOS_SINGLE_TASK_MAX_FILES", "0")
    client = _Scripted()
    report, executed = _run(env, monkeypatch, n_hit_files=10, client=client)
    assert len(executed) == 2
    assert not report["integration_review"].get("skipped")
    assert len(client.calls) == 1
    # One SDK attempt, capped by the review timeout.
    assert client.calls[0]["timeout"] == ir.DEFAULT_REVIEW_TIMEOUT_S


@pytest.fixture
def review_env(monkeypatch):
    for name in ("AWOS_REVIEW_MODEL", "AWOS_AGENT_MODEL", "AWOS_INTEGRATION_REVIEW",
                 "AWOS_REVIEW_TIMEOUT_S", "AWOS_CHEAP_ONLY"):
        monkeypatch.delenv(name, raising=False)


def _reviewer(client):
    r = ir.IntegrationReviewer(client=client)
    r._get_diff = lambda _root: "+x = 1\n"
    return r


LOG = [{"task_id": 1, "status": "completed", "reason": "ok"}]


def test_review_off_disables(review_env, monkeypatch):
    monkeypatch.setenv("AWOS_INTEGRATION_REVIEW", "0")
    client = _Scripted()
    result = _reviewer(client).review(".", LOG, agent_model="m", n_tasks=3)
    assert result["skipped"] is True and client.calls == []


def test_review_one_forces_for_single_task(review_env, monkeypatch):
    monkeypatch.setenv("AWOS_INTEGRATION_REVIEW", "1")
    client = _Scripted()
    with patch(RECORD):
        result = _reviewer(client).review(".", LOG, agent_model="m", n_tasks=1)
    assert not result.get("skipped") and len(client.calls) == 1


def test_review_auto_with_unknown_task_count_runs(review_env):
    client = _Scripted()
    with patch(RECORD):
        result = _reviewer(client).review(".", LOG, agent_model="m")
    assert not result.get("skipped")
    assert ir.review_mode() == "auto"


def test_review_timeout_returns_skipped_without_raising(review_env, monkeypatch):
    monkeypatch.setenv("AWOS_REVIEW_TIMEOUT_S", "0.2")
    client = _Scripted(delay=2.0)
    start = time.monotonic()
    with patch(RECORD):
        result = _reviewer(client).review(".", LOG, agent_model="m", n_tasks=2)
    assert time.monotonic() - start < 1.5
    assert result["skipped"] is True and result["skip_reason"] == "timeout"
    assert result["passed"] is True
    assert client.calls[0]["timeout"] == pytest.approx(0.2)


def test_review_timeout_thread_is_daemon(review_env, monkeypatch):
    monkeypatch.setenv("AWOS_REVIEW_TIMEOUT_S", "0.1")
    with patch(RECORD):
        _reviewer(_Scripted(delay=1.0)).review(".", LOG, agent_model="m", n_tasks=2)
    leftovers = [t for t in threading.enumerate() if t.name == "integration-review"]
    assert all(t.daemon for t in leftovers)


def test_review_timeout_env_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("AWOS_REVIEW_TIMEOUT_S", "abc")
    assert ir.review_timeout_s() == ir.DEFAULT_REVIEW_TIMEOUT_S
    monkeypatch.setenv("AWOS_REVIEW_TIMEOUT_S", "-5")
    assert ir.review_timeout_s() == ir.DEFAULT_REVIEW_TIMEOUT_S
