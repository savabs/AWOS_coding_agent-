"""
AWOS_GOAL_BUDGET_USD — one goal's spend cap across its tasks, retries, goal
checks and follow-up rounds.

The ledger is a fake that each mocked task writes to, so no key, no network,
and no write to the shared .awos/budget.json.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import orchestrator as orch_mod
from scaffold.agent.goal_check import GoalVerdict
from scaffold.agent.orchestrator import GOAL_BUDGET_REASON, Orchestrator


class _FakeLedger:
    """What BudgetLedger keeps in memory: records with a timestamp and a cost."""

    def __init__(self, records=()):
        self._records = list(records)

    def record(self, cost: float) -> None:
        self._records.append({"timestamp": datetime.now().isoformat(), "cost": cost,
                              "request_type": "agent_loop", "model": "m",
                              "pid": os.getpid()})


@pytest.fixture
def ledger():
    fake = _FakeLedger()
    with patch("scaffold.agent.orchestrator._ledgers", return_value=[fake]):
        yield fake


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("AWOS_GOAL_BUDGET_USD", raising=False)


# ── Reading the ledger ───────────────────────────────────────────────────────


def test_spend_counts_only_records_since_the_goal_started(ledger):
    before = (datetime.now() - timedelta(hours=1)).isoformat()
    ledger._records.append({"timestamp": before, "cost": 5.0, "pid": os.getpid()})
    start = time.time()
    ledger.record(0.25)
    ledger.record(0.50)
    ledger._records.append({"timestamp": "garbage", "cost": 9.0, "pid": os.getpid()})
    assert orch_mod._ledger_spend_since(start) == pytest.approx(0.75)


def test_another_processs_spend_is_not_this_goals(ledger):
    # A ledger created mid-goal loads budget.json, which holds what a parallel
    # long-task run wrote since this goal started. Untagged records are from
    # before records carried a pid, or from a writer that is not this process.
    start = time.time()
    ledger.record(0.25)
    now = datetime.now().isoformat()
    ledger._records.append({"timestamp": now, "cost": 1.5, "pid": os.getpid() + 1})
    ledger._records.append({"timestamp": now, "cost": 1.5})
    assert orch_mod._ledger_spend_since(start) == pytest.approx(0.25)


def test_ledger_records_carry_the_pid(tmp_path):
    from scaffold.agent.budget_ledger import BudgetLedger
    led = BudgetLedger(tmp_path / "budget.json")
    led.record("agent_loop", "m", 10, 5, 0.01)
    assert led._records[-1]["pid"] == os.getpid()
    start = time.time() - 1
    with patch("scaffold.agent.orchestrator._ledgers", return_value=[led]):
        assert orch_mod._ledger_spend_since(start) == pytest.approx(0.01)


def test_both_ledger_singletons_are_read():
    # record_api_usage writes through `budget_ledger`, the orchestrator
    # imports `scaffold.agent.budget_ledger`: two modules, two singletons.
    a, b = _FakeLedger(), _FakeLedger()
    mods = {"budget_ledger": MagicMock(_global_ledger=a),
            "scaffold.agent.budget_ledger": MagicMock(_global_ledger=b)}
    with patch.dict(sys.modules, mods), \
         patch("scaffold.agent.budget_ledger.get_ledger", return_value=a):
        start = time.time()
        a.record(0.1)
        b.record(0.2)
        assert orch_mod._ledger_spend_since(start) == pytest.approx(0.3)


def test_a_record_both_ledgers_hold_counts_once():
    # Live run: the second singleton was created after the first had written
    # the goal check's record, loaded it from budget.json, and the spend read
    # $1.16 for one $0.58 check.
    a = _FakeLedger()
    start = time.time()
    a.record(0.58)
    b = _FakeLedger(records=[dict(r) for r in a._records])
    with patch("scaffold.agent.orchestrator._ledgers", return_value=[a, b]):
        assert orch_mod._ledger_spend_since(start) == pytest.approx(0.58)


@pytest.mark.parametrize("raw,expected", [("", 2.0), ("0.5", 0.5), ("bogus", 2.0),
                                          ("0", None), ("-1", None)])
def test_budget_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv("AWOS_GOAL_BUDGET_USD", raw)
    assert orch_mod._goal_budget() == expected


# ── execute_feature stops starting work at the cap ──────────────────────────


@pytest.fixture
def feature_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_RUNTIME_SESSION", "true")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "fake-key-for-testing")
    monkeypatch.setenv("AWOS_E2E", "1")
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    monkeypatch.setenv("AWOS_GOAL_CHECK", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text("# config\n", encoding="utf-8")
    return repo


PLAN = [{"task_id": i, "task_type": "edit_file", "path": "config.py", "file": "config.py",
         "action": f"Step {i} of the migration", "complexity": "low"} for i in (1, 2, 3)]


def _run(repo, ledger, verdicts, cost_per_task=1.0):
    orch = Orchestrator()
    ran = []

    def task(task, ctx):
        ran.append(task["task_id"])
        ledger.record(cost_per_task)
        return {"task_id": task["task_id"], "success": True, "task": task}

    checker = MagicMock()
    checker.return_value.check.side_effect = verdicts
    with patch.object(orch, "_execute_single_task", side_effect=task), \
         patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=(["config.py"], "d")), \
         patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
        result = orch.execute_feature(goal="migrate settings", codebase_root=str(repo),
                                      pre_planned_tasks=[dict(t) for t in PLAN])
    return result, ran, checker


def test_tasks_stop_once_the_goal_budget_is_spent(feature_env, ledger, monkeypatch, capsys):
    monkeypatch.setenv("AWOS_GOAL_BUDGET_USD", "1.50")
    result, ran, checker = _run(feature_env, ledger, [GoalVerdict(True, verified=True)])
    # $1 after task 1 is under $1.50; $2 after task 2 is not, so task 3 never starts.
    assert ran == [1, 2]
    checker.return_value.check.assert_not_called()  # a goal check is new work too
    assert result["success"] is False and result["goal_complete"] is False
    assert result["incomplete_reason"] == GOAL_BUDGET_REASON
    assert GOAL_BUDGET_REASON in result["errors"]
    assert (result["tasks_completed"], result["tasks_failed"]) == (2, 0)
    out = capsys.readouterr().out
    assert "[BUDGET] goal budget $1.50 reached — stopping" in out
    assert out.count("[BUDGET] goal budget") == 1


def test_follow_up_round_not_started_past_the_budget(feature_env, ledger, monkeypatch):
    # The tasks fit ($0.90 of $1); the check finds a gap, but the ledger then
    # says the check itself took the goal over: no follow-up round starts.
    monkeypatch.setenv("AWOS_GOAL_BUDGET_USD", "1.00")
    gap = GoalVerdict(False, missing=[{"action": "fix cli", "file": "cli.py"}],
                      reasoning="cli.py left", verified=True)

    def check(*args):
        ledger.record(0.5)
        return gap

    orch = Orchestrator()
    checker = MagicMock()
    checker.return_value.check.side_effect = check
    ran = []

    def task(task, ctx):
        ran.append(task["task_id"])
        ledger.record(0.3)
        return {"task_id": task["task_id"], "success": True, "task": task}

    with patch.object(orch, "_execute_single_task", side_effect=task), \
         patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=(["config.py"], "d")), \
         patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
        result = orch.execute_feature(goal="migrate settings", codebase_root=str(feature_env),
                                      pre_planned_tasks=[dict(t) for t in PLAN])
    assert ran == [1, 2, 3]
    assert checker.return_value.check.call_count == 1
    assert result["success"] is False and result["incomplete_reason"] == GOAL_BUDGET_REASON


def test_budget_crossed_by_the_last_task_leaves_the_goal_unverified(feature_env, ledger,
                                                                      monkeypatch):
    # $2 after task 2 is under $2.50; task 3 takes it to $3. No task was left
    # unstarted, so the reason is the goal check that could not run, not
    # "goal budget exhausted" (work left undone).
    monkeypatch.setenv("AWOS_GOAL_BUDGET_USD", "2.50")
    result, ran, checker = _run(feature_env, ledger, [GoalVerdict(True, verified=True)])
    assert ran == [1, 2, 3]
    checker.return_value.check.assert_not_called()
    assert result["success"] is False and result["goal_complete"] is False
    assert result["incomplete_reason"] == orch_mod.GOAL_CHECK_BUDGET_REASON
    assert result["goal_check"]["source"] == "not_run"
    assert result["goal_check"]["verified"] is False
    assert (result["tasks_completed"], result["tasks_failed"]) == (3, 0)


def test_under_the_budget_nothing_changes(feature_env, ledger, monkeypatch, capsys):
    monkeypatch.setenv("AWOS_GOAL_BUDGET_USD", "10")
    done = GoalVerdict(True, reasoning="all readers migrated", verified=True, source="tool")
    result, ran, checker = _run(feature_env, ledger, [done])
    assert ran == [1, 2, 3]
    checker.return_value.check.assert_called_once()
    assert result["success"] is True and result["incomplete_reason"] == ""
    assert (result["tasks_completed"], result["tasks_failed"]) == (3, 0)
    assert "[BUDGET] goal budget" not in capsys.readouterr().out


# ── Retry attempts ───────────────────────────────────────────────────────────


def test_agent_loop_not_resumed_past_the_budget(capsys):
    # A resumable stop (repeated_tool_call) would get a second attempt; with
    # the goal's budget spent it does not.
    from test_agent_loop_retry import FIX, RED, STUCK, _run as run_task

    with patch.object(Orchestrator, "_goal_budget_exhausted", return_value=True):
        run = run_task([FIX, *STUCK], [RED])
    assert len(run.prompts) == 1
    assert run.out["success"] is False
    assert f"AgentLoop not resumed: {GOAL_BUDGET_REASON}" in capsys.readouterr().out
