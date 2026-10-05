"""Planner failure must not end a job silently, and the planner model is pinnable.

A job-series run logged "primary planner failed (... invalid JSON ...) —
trying CheapPlanner" and then nothing: the fallback failed too, the orchestrator
returned a failure dict without printing it, and the job ran 0 agent turns.
Now each failure is printed and, when no planner produces a plan, the goal runs
as a single task. AWOS_PLANNER_MODEL pins every planner call's model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scaffold.agent import orchestrator as orch_mod
from scaffold.agent.orchestrator import Orchestrator

GOAL = "Merchants want a year at a glance. Add a `monthly CUSTOMER YEAR` command"


class _BadJsonPlanner:
    """Stands in for CheapPlanner: every plan() call fails like the live run."""

    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    def plan(self, *args, **kwargs):
        type(self).calls += 1
        raise ValueError(
            "planner model qwen3.7-plus returned invalid JSON: ''... "
            "Error: Expecting value: line 1 column 1 (char 0)"
        )


class _BadJsonPrimary:
    """A primary planner of another kind (not a CheapPlanner), failing the same way."""

    def __init__(self, *args, **kwargs):
        pass

    def plan(self, *args, **kwargs):
        return _BadJsonPlanner().plan(*args, **kwargs)


class _GoodPlanner:
    def __init__(self, *args, **kwargs):
        pass

    def plan(self, *args, **kwargs):
        return {"plan": [{"task_id": 1, "file": "a.py", "action": "do it", "complexity": "low"}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("AWOS_E2E", "1")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
    monkeypatch.setenv("AWOS_GOAL_CHECK", "0")
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    monkeypatch.delenv("AWOS_PLANNER_MODEL", raising=False)
    # These tests are about the planner chain: keep the small-goal gate
    # (AWOS_PLANNER=auto) from skipping the planner on this tiny repo.
    monkeypatch.setenv("AWOS_PLANNER", "always")
    # Other tests leave AWOS_CHEAP_ONLY set; the primary must be orch.planner.
    monkeypatch.setattr(orch_mod, "is_cheap_only", lambda: False)
    _BadJsonPlanner.calls = 0
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return repo


# ── fallback chain ───────────────────────────────────────────────────────────

def test_every_planner_fails_goal_runs_as_one_task(env, capsys):
    orch = Orchestrator()
    orch.planner = _BadJsonPrimary()
    plan, source, errors = None, None, None
    with patch.object(orch_mod, "CheapPlanner", _BadJsonPlanner):
        plan, source, errors = orch._plan_goal(GOAL, {})

    assert source == "goal_as_task"
    assert plan["plan"] == [{"task_id": 1, "file": "", "action": GOAL, "complexity": "high"}]
    assert _BadJsonPlanner.calls == 2  # primary, then the fallback
    assert len(errors) == 2
    assert errors[0].startswith("primary planner failed") and "invalid JSON" in errors[0]
    assert errors[1].startswith("fallback planner failed") and "invalid JSON" in errors[1]
    out = capsys.readouterr().out
    assert "fallback CheapPlanner failed" in out and "invalid JSON" in out
    assert "running the whole goal as one task" in out


def test_cheap_primary_failure_skips_the_identical_fallback(env, capsys):
    # The primary is a CheapPlanner, which retries an empty reply itself; a
    # CheapPlanner fallback would only repeat the same failing call.
    orch = Orchestrator()
    orch.planner = _BadJsonPlanner()
    with patch.object(orch_mod, "CheapPlanner", _BadJsonPlanner):
        plan, source, errors = orch._plan_goal(GOAL, {})
    assert source == "goal_as_task"
    assert _BadJsonPlanner.calls == 1
    assert len(errors) == 1 and errors[0].startswith("primary planner failed")
    out = capsys.readouterr().out
    assert "trying CheapPlanner fallback" not in out
    assert "running the whole goal as one task" in out


def test_primary_fails_fallback_succeeds(env):
    orch = Orchestrator()
    orch.planner = _BadJsonPlanner()
    with patch.object(orch_mod, "CheapPlanner", _GoodPlanner):
        plan, source, errors = orch._plan_goal(GOAL, {})
    assert source == "planner_fallback"
    assert plan["plan"][0]["action"] == "do it"
    assert len(errors) == 1 and errors[0].startswith("primary planner failed")


def test_empty_plan_is_a_failure_not_a_finished_goal(env):
    orch = Orchestrator()
    empty = MagicMock()
    empty.plan.return_value = {"plan": []}
    orch.planner = empty
    with patch.object(orch_mod, "CheapPlanner", lambda *a, **k: empty):
        plan, source, errors = orch._plan_goal(GOAL, {})
    assert source == "goal_as_task"
    assert "no tasks" in errors[0]


def test_execute_feature_runs_goal_when_planners_fail(env):
    """End to end: both planners fail, the goal still reaches the executor."""
    orch = Orchestrator()
    orch.planner = _BadJsonPrimary()
    executed: list = []

    def mock_execute(task, ctx):
        executed.append(task)
        return {"task_id": task["task_id"], "success": True, "task": task}

    with patch.object(orch_mod, "CheapPlanner", _BadJsonPlanner), \
         patch.object(orch, "_execute_single_task", side_effect=mock_execute), \
         patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
        report = orch.execute_feature(goal=GOAL, codebase_root=str(env), auto_approve_plan=True)

    assert len(executed) == 1
    assert executed[0]["action"] == GOAL
    assert report["plan_source"] == "goal_as_task"
    assert report["total_tasks"] == 1
    assert report["tasks_completed"] == 1
    assert len(report["planner_errors"]) == 2
    assert any("fallback planner failed" in e for e in report["errors"])


# ── AWOS_PLANNER_MODEL ───────────────────────────────────────────────────────

def _recording_client():
    client = MagicMock()
    reply = MagicMock()
    reply.choices = [MagicMock(message=MagicMock(
        content='{"plan": [{"task_id": 1, "file": "a.py", "action": "x", "complexity": "low"}]}'
    ))]
    reply.usage = None
    client.chat.completions.create.return_value = reply
    return client


def test_cheap_planner_default_model_unchanged(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-test")
    monkeypatch.delenv("AWOS_PLANNER_MODEL", raising=False)
    from scaffold.agent.cheap_planner import CheapPlanner

    assert CheapPlanner().model_name == "qwen3.7-plus"


def test_planner_model_env_pins_cheap_planner_calls(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("AWOS_PLANNER_MODEL", "deepseek/deepseek-v4-flash")
    from scaffold.agent.cheap_planner import CheapPlanner

    planner = CheapPlanner()
    assert planner.model_name == "deepseek/deepseek-v4-flash"
    assert "openrouter.ai" in str(planner.client.base_url)
    planner.client = _recording_client()
    planner.plan("goal", {})
    planner.refine_goal("goal", {})
    models = {c.kwargs["model"] for c in planner.client.chat.completions.create.call_args_list}
    assert models == {"deepseek/deepseek-v4-flash"}


def test_planner_model_env_reaches_openrouter_request(monkeypatch):
    """The routed client sends the pinned id (bare ids get the vendor prefix)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("AWOS_PLANNER_MODEL", "claude-haiku-4-5")
    from scaffold.agent.cheap_planner import CheapPlanner

    planner = CheapPlanner()
    inner_create = MagicMock(return_value=_recording_client().chat.completions.create())
    planner.client._inner.chat.completions.create = inner_create
    planner.plan("goal", {})
    assert inner_create.call_args.kwargs["model"] == "anthropic/claude-haiku-4.5"


def test_planner_model_env_pins_sonnet_planner(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-test")
    from scaffold.agent.planner import Planner

    monkeypatch.delenv("AWOS_PLANNER_MODEL", raising=False)
    assert Planner().model == "claude-sonnet-4-6"
    monkeypatch.setenv("AWOS_PLANNER_MODEL", "qwen/qwen3-coder")
    assert Planner().model == "qwen/qwen3-coder"


def test_orchestrator_planners_honour_env(env, monkeypatch):
    monkeypatch.setenv("AWOS_PLANNER_MODEL", "openai/gpt-5-mini")
    orch = Orchestrator()
    assert orch.planner.model_name == "openai/gpt-5-mini"
    # The fallback builds its own CheapPlanner: same model.
    assert orch_mod.CheapPlanner().model_name == "openai/gpt-5-mini"
