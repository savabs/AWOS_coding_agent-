"""Regression tests for stale learned-router actions and the current model ladder."""

from types import SimpleNamespace

from scaffold.agent.escalation_engine import EscalationEngine


class _StaleRouter:
    n_actions = 6

    def __init__(self, action_id):
        self.action_id = action_id

    def is_ready(self):
        return True

    def select(self, _features, budget_mask=None):
        return self.action_id

    def total_updates(self):
        return 48


def _task():
    return {
        "task_id": 1,
        "file": "buggy.py",
        "action": "fix the incorrect cache eviction behavior",
        "complexity": "medium",
    }


def test_stale_learned_action_falls_back_to_current_candidate(monkeypatch):
    monkeypatch.setenv("AWOS_CHEAP_ONLY", "true")
    engine = EscalationEngine(ml_router=_StaleRouter(action_id=5))

    decision = engine.decide(_task(), budget_remaining=5.0)

    assert decision.spec.provider == "opencode"
    assert decision.spec.model_id in {"deepseek-v4-flash", "deepseek-v4-pro"}


def test_current_ladder_action_remains_selectable(monkeypatch):
    monkeypatch.setenv("AWOS_CHEAP_ONLY", "true")
    engine = EscalationEngine(ml_router=_StaleRouter(action_id=1))

    decision = engine.decide(_task(), budget_remaining=5.0)

    assert decision.spec.provider == "opencode"
    assert decision.spec.model_id == "deepseek-v4-pro"
