"""Tests for AWOS cheap-only mode (no Haiku/Sonnet escalation)."""

import os

import pytest


@pytest.fixture
def cheap_env(monkeypatch):
    monkeypatch.setenv("AWOS_CHEAP_ONLY", "true")
    monkeypatch.setenv("AWOS_PREMIUM_BUDGET", "0")


class TestCheapOnlyEscalation:
    def test_is_cheap_only_from_flag(self, cheap_env):
        from scaffold.agent.escalation_engine import is_cheap_only

        assert is_cheap_only() is True

    def test_never_escalates_to_sonnet(self, cheap_env):
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel

        engine = EscalationEngine(monthly_budget=20.0)
        task = {
            "action": "refactor entire authentication architecture from scratch",
            "file": "scaffold/agent/core/auth.py",
            "complexity": "high",
        }
        decision = engine.decide(task, failure_count=5, budget_remaining=20.0)
        assert decision.spec.level.value <= EscalationLevel.OPENAI.value

    def test_force_sonnet_downgraded(self, cheap_env):
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel

        engine = EscalationEngine(monthly_budget=20.0)
        task = {"action": "fix typo", "file": "test_foo.py", "complexity": "low"}
        decision = engine.decide(task, force_level=EscalationLevel.SONNET)
        assert decision.spec.level == EscalationLevel.OPENAI
        assert "cheap-only" in decision.reason.lower()

    def test_rotates_on_retry(self, cheap_env):
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel

        engine = EscalationEngine(monthly_budget=20.0)
        task = {"action": "add helper", "file": "utils.py", "complexity": "medium"}
        d1 = engine.decide(task, failure_count=1)
        d2 = engine.decide(task, failure_count=2)
        assert d1.spec.level in (EscalationLevel.DEEPSEEK, EscalationLevel.OPENAI)
        assert d2.spec.level in (EscalationLevel.DEEPSEEK, EscalationLevel.OPENAI)
        assert d1.spec.level != d2.spec.level

    def test_user_wants_best_stays_cheap(self, cheap_env):
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel

        engine = EscalationEngine(monthly_budget=20.0)
        task = {"action": "implement feature", "file": "main.py", "complexity": "high"}
        decision = engine.decide(task, user_wants_best=True, budget_remaining=20.0)
        assert decision.spec.level == EscalationLevel.OPENAI
