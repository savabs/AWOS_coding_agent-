"""The plan prompt asks for outcomes, not guessed mechanisms.

Long-task runs showed the planner prescribing "lru_cache parse_date" for a
perf goal nobody had measured, and "click errors" in an argparse app. These
tests pin the rules that stop that, and that the JSON contract the
orchestrator parses did not move.
"""

import json
from types import SimpleNamespace

import pytest

from scaffold.agent.cheap_planner import CheapPlanner


class _FakeClient:
    """Records the prompt and returns a canned completion."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
            usage=None,
        )


def _planner(reply: str) -> tuple[CheapPlanner, _FakeClient]:
    planner = CheapPlanner.__new__(CheapPlanner)
    client = _FakeClient(reply)
    planner.client = client
    planner.model_name = "test-model"
    return planner, client


_ONE_TASK = json.dumps({
    "plan": [{
        "task_id": 1,
        "file": "app/dates.py",
        "action": "parse_day accepts '2024-02-29'; check with the date tests",
        "complexity": "low",
    }],
    "reasoning": "one place",
    "total_tasks": 1,
})

_CONTEXT = {
    "modules": "app",
    "architecture": "small CLI",
    "files": ["app/dates.py", "app/cli.py"],
    "symbols": ["app/dates.py: parse_day"],
}


@pytest.fixture
def prompt() -> str:
    planner, client = _planner(_ONE_TASK)
    planner.plan("Fix leap-day parsing in parse_day", _CONTEXT)
    return client.prompts[0]


def test_prompt_asks_for_outcomes_not_mechanisms(prompt):
    assert "OUTCOMES and acceptance, not mechanisms" in prompt
    assert "Never name a library, function, framework or" in prompt
    assert "technique that is not shown in the CODEBASE section" in prompt


def test_prompt_says_performance_is_one_measure_first_task(prompt):
    assert "plan exactly ONE" in prompt
    assert "measure first" in prompt
    assert "run_command" in prompt
    assert "re-measure against the target" in prompt
    assert "keep every result identical" in prompt
    assert "Never split performance work by" in prompt


def test_prompt_says_wide_goals_search_the_whole_codebase(prompt):
    assert "find every place that" in prompt
    assert "search the whole" in prompt
    assert "instead of listing guessed files" in prompt


def test_prompt_carries_explicit_requirements_verbatim(prompt):
    assert "explicit requirement of the goal into the tasks VERBATIM" in prompt
    for item in ("flags", "error behaviour", "boundaries", "formats"):
        assert item in prompt


def test_prompt_keeps_original_rules(prompt):
    assert "A fix in one place is ONE task" in prompt
    assert '"write a failing test" its own task' in prompt
    assert "Existing tests are the contract" in prompt
    assert "Order tasks so dependencies are handled first" in prompt
    assert "Estimate complexity" in prompt


def test_json_contract_unchanged():
    planner, _ = _planner("```json\n" + _ONE_TASK + "\n```")
    result = planner.plan("Fix leap-day parsing in parse_day", _CONTEXT)
    assert set(result) == {"plan", "reasoning", "total_tasks"}
    assert result["total_tasks"] == 1
    assert set(result["plan"][0]) == {"task_id", "file", "action", "complexity"}


def test_one_place_fix_is_one_task():
    planner, _ = _planner(_ONE_TASK)
    result = planner.plan("Fix leap-day parsing in parse_day", _CONTEXT)
    assert len(result["plan"]) == 1


def test_task_missing_fields_still_rejected():
    bad = json.dumps({"plan": [{"task_id": 1, "file": "a.py"}], "total_tasks": 1})
    planner, _ = _planner(bad)
    with pytest.raises(RuntimeError):
        planner.plan("anything", _CONTEXT)
