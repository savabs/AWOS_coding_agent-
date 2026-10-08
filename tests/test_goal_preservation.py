"""The user's goal reaches the worker verbatim, whatever the planner wrote.

backupd jobs 2 and 5 (2026-10-08): the planner returned ONE task, and its
action (a paraphrase that invented ".tar.gz" and "modification time" and
dropped "report it like backupd's other errors") replaced the goal in the
one-shot prompt, the agent-loop prompt and the acceptance generator.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent.orchestrator import Orchestrator, worker_task_text  # noqa: E402
from scaffold.agent.one_shot import OneShotResult  # noqa: E402

GOAL = ("Please add a `list` command. Anything in the folder that isn't one of our "
        "backup archives should be ignored. Report errors like backupd's other errors.")
ACTION = ("Add a `list` subcommand. Ignore files that do not match the archive "
          "extension (e.g., '.tar.gz'). Sort by the file's modification time.")
NOTE = "Planner's note (may be wrong; the goal above wins):"


def _ctx(goal=GOAL, **extra):
    return {"session": SimpleNamespace(goal=goal), "exploration": {}, **extra}


def _orch():
    orch = Orchestrator.__new__(Orchestrator)
    orch.tracker = None
    return orch


def _one_shot_text(task, ctx) -> str:
    seen = {}

    def fake_run(client, model, root, task_text, exploration, **kw):
        seen["text"] = task_text
        return OneShotResult(error="stop here", calls=1)

    with patch("scaffold.agent.one_shot.one_shot_enabled", return_value=True), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=object()), \
         patch("scaffold.agent.one_shot.run_one_shot", side_effect=fake_run), \
         patch("scaffold.agent.best_of_n.best_of_n", return_value=1):
        _orch()._one_shot_first(task, ctx, "m", "/nonexistent", None, 1, True)
    return seen["text"]


# ── single task whose action differs from the goal ──────────────────────────

def test_single_task_one_shot_prompt_has_goal_verbatim_and_note():
    text = _one_shot_text({"task_id": 1, "action": ACTION, "file": "backupd/cli.py"}, _ctx())
    assert text.startswith("Goal (verbatim):\n" + GOAL)
    assert NOTE + "\n" + ACTION in text
    assert text.index(GOAL) < text.index(NOTE) < text.index(ACTION)
    assert "Files to change: backupd/cli.py" in text


def test_single_task_agent_prompt_has_goal_verbatim_and_note():
    prompt = Orchestrator._agent_loop_prompt({"action": ACTION}, _ctx())
    assert prompt.startswith("Goal (verbatim):\n" + GOAL)
    assert NOTE + "\n" + ACTION in prompt
    assert prompt.count(GOAL) == 1


# ── identical text: no duplication, unchanged prompt ────────────────────────

def test_identical_action_is_not_duplicated():
    task = {"action": GOAL}
    assert worker_task_text(task, _ctx()) == GOAL
    prompt = Orchestrator._agent_loop_prompt(task, _ctx())
    assert prompt.count(GOAL) == 1 and NOTE not in prompt and "Goal (verbatim)" not in prompt
    assert prompt == Orchestrator._agent_loop_prompt(task, {})   # same as before the fix
    assert _one_shot_text(dict(task), _ctx()) == GOAL


def test_no_session_goal_keeps_the_action():
    assert worker_task_text({"action": ACTION}, {}) == ACTION
    assert worker_task_text({"action": ACTION}, {"session": MagicMock()}) == ACTION


# ── merged multi-task plan: unchanged, goal present once ───────────────────

def test_merged_plan_unchanged_and_goal_once():
    tasks = [{"task_id": 1, "file": "a.py", "action": "step one", "complexity": "low"},
             {"task_id": 2, "file": "b.py", "action": "step two", "complexity": "low"}]
    merged = Orchestrator._collapse_small_plan(GOAL, tasks, 4)[0]
    text = worker_task_text(merged, _ctx(_total_tasks=1))
    assert text == merged["action"]
    assert text.count(GOAL) == 1 and NOTE not in text
    prompt = Orchestrator._agent_loop_prompt(merged, _ctx(_total_tasks=1))
    assert prompt.count(GOAL) == 1
    assert _one_shot_text(merged, _ctx()).count(GOAL) == 1


def test_unmerged_multi_task_step_carries_goal_once_as_context():
    text = worker_task_text({"action": "step two"}, _ctx(_total_tasks=3))
    assert text.startswith("Goal (verbatim):\n" + GOAL)
    assert text.count(GOAL) == 1
    assert "step of a 3-task plan" in text and text.endswith("step two")


# ── acceptance generator sees the goal ──────────────────────────────────────

def test_acceptance_generator_receives_verbatim_goal():
    seen = {}

    def fake_build(text, root, exploration, **kw):
        seen["text"] = text
        return SimpleNamespace(generated=0, kept=[], cost_usd=0.0, dropped={}, error="",
                               active=False)

    long_action = ACTION + " " + "x" * 2000   # longer than the goal: used to win
    orch = _orch()
    with patch("scaffold.agent.acceptance.acceptance_enabled", return_value=True), \
         patch("scaffold.agent.acceptance.remove_leftovers"), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=object()), \
         patch("scaffold.agent.acceptance.build_suite", side_effect=fake_build):
        orch._acceptance_prepare({"action": long_action}, _ctx(), "m", "/nonexistent", None)
    assert seen["text"] == GOAL
    assert orch._acceptance_goal_text == GOAL
