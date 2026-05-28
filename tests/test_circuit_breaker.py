"""
Unit tests for the provider circuit-breaker.

Covers:
  - Worker._is_permanent_failure() error classification
  - _dead_providers populated on credit-depleted error
  - Escalation routing skips dead providers
  - _architect_step skips dead providers
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_worker():
    w = MagicMock()
    w._dead_providers = set()
    from scaffold.agent.worker import Worker
    w._is_permanent_failure = Worker._is_permanent_failure
    return w


def _make_escalation(dead_providers=None):
    from scaffold.agent.escalation_engine import EscalationEngine
    eng = EscalationEngine(monthly_budget=20.0)
    return eng, dead_providers or set()


# ── _is_permanent_failure ────────────────────────────────────────────────────

class TestIsPermanentFailure:
    def test_credit_balance_too_low(self):
        from scaffold.agent.worker import Worker
        assert Worker._is_permanent_failure(
            "Error code: 400 - your credit balance is too low"
        )

    def test_insufficient_quota(self):
        from scaffold.agent.worker import Worker
        assert Worker._is_permanent_failure("insufficient_quota exceeded")

    def test_payment_required(self):
        from scaffold.agent.worker import Worker
        assert Worker._is_permanent_failure("Payment required to continue")

    def test_billing_keyword(self):
        from scaffold.agent.worker import Worker
        assert Worker._is_permanent_failure("billing issue detected")

    def test_transient_network_error_not_permanent(self):
        from scaffold.agent.worker import Worker
        assert not Worker._is_permanent_failure("Connection timeout after 30s")

    def test_rate_limit_not_permanent(self):
        from scaffold.agent.worker import Worker
        assert not Worker._is_permanent_failure("Rate limit exceeded — retry after 60s")

    def test_server_error_not_permanent(self):
        from scaffold.agent.worker import Worker
        assert not Worker._is_permanent_failure("Internal server error 500")


# ── Dead-provider gating in Worker.execute_task ───────────────────────────────

class TestWorkerCircuitBreaker:
    def _minimal_worker(self):
        """Instantiate Worker with mocked clients to avoid real API calls."""
        from scaffold.agent.worker import Worker
        w = Worker.__new__(Worker)
        w._dead_providers = set()
        w.client = MagicMock()
        w.anthropic_client = MagicMock()
        w.openai_client = None
        w.model = "deepseek-chat"
        w.fallback_model = "claude-haiku-4-5"
        return w

    def test_anthropic_skipped_when_tripped(self):
        """When anthropic is dead, execute_task must not call anthropic_client."""
        from scaffold.agent.worker import Worker
        w = self._minimal_worker()
        w._dead_providers.add("anthropic")

        # DeepSeek succeeds
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="SEARCH:\n```\nx\n```\n\nREPLACE:\n```\ny\n```\n\nREASONING: test"))]
        mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        w.client.chat.completions.create.return_value = mock_resp

        task = {"task_id": 1, "action": "add x", "file": "foo.py", "complexity": "low"}
        with patch("scaffold.agent.worker.get_ledger") as mock_ledger:
            mock_ledger.return_value.check_budget.return_value = (True, "")
            w.execute_task(task=task, file_content="x = 1\n", codebase_context={})

        w.anthropic_client.messages.create.assert_not_called()

    def test_deepseek_skipped_when_tripped(self):
        """When deepseek is dead, execute_task must not call deepseek client."""
        from scaffold.agent.worker import Worker
        w = self._minimal_worker()
        w._dead_providers.add("deepseek")

        # Anthropic (primary since deepseek is dead) succeeds
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="SEARCH:\n```\nx\n```\n\nREPLACE:\n```\ny\n```\n\nREASONING: test")]
        mock_resp.usage = MagicMock(input_tokens=10, output_tokens=5)
        w.anthropic_client.messages.create.return_value = mock_resp

        from scaffold.agent.escalation_engine import LADDER, EscalationLevel, LEVEL_MAP
        anthropic_spec = LEVEL_MAP[EscalationLevel.HAIKU]

        task = {"task_id": 1, "action": "add x", "file": "foo.py", "complexity": "high"}
        with patch("scaffold.agent.worker.get_ledger") as mock_ledger:
            mock_ledger.return_value.check_budget.return_value = (True, "")
            w.execute_task(
                task=task, file_content="x = 1\n", codebase_context={},
                model_spec=anthropic_spec,
            )

        w.client.chat.completions.create.assert_not_called()

    def test_permanent_error_trips_provider(self):
        """A 400-credit error on deepseek should add 'deepseek' to _dead_providers."""
        from scaffold.agent.worker import Worker
        w = self._minimal_worker()

        credit_err = Exception(
            "Error code: 400 - {'error': {'message': 'your credit balance is too low'}}"
        )
        w.client.chat.completions.create.side_effect = credit_err

        # anthropic provides response so execute_task doesn't raise
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="SEARCH:\n```\nx\n```\n\nREPLACE:\n```\ny\n```\n\nREASONING: test")]
        mock_resp.usage = MagicMock(input_tokens=10, output_tokens=5)
        w.anthropic_client.messages.create.return_value = mock_resp

        task = {"task_id": 1, "action": "add x", "file": "foo.py", "complexity": "low"}
        with patch("scaffold.agent.worker.get_ledger") as mock_ledger:
            mock_ledger.return_value.check_budget.return_value = (True, "")
            try:
                w.execute_task(task=task, file_content="x = 1\n", codebase_context={})
            except Exception:
                pass

        assert "deepseek" in w._dead_providers

    def test_transient_error_does_not_trip_provider(self):
        """A network timeout on deepseek must NOT trip the circuit breaker."""
        from scaffold.agent.worker import Worker
        w = self._minimal_worker()

        w.client.chat.completions.create.side_effect = Exception("Connection timeout after 30s")

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="SEARCH:\n```\nx\n```\n\nREPLACE:\n```\ny\n```\n\nREASONING: test")]
        mock_resp.usage = MagicMock(input_tokens=10, output_tokens=5)
        w.anthropic_client.messages.create.return_value = mock_resp

        task = {"task_id": 1, "action": "add x", "file": "foo.py", "complexity": "low"}
        with patch("scaffold.agent.worker.get_ledger") as mock_ledger:
            mock_ledger.return_value.check_budget.return_value = (True, "")
            try:
                w.execute_task(task=task, file_content="x = 1\n", codebase_context={})
            except Exception:
                pass

        assert "deepseek" not in w._dead_providers


# ── EscalationEngine routing skips dead providers ─────────────────────────────

class TestEscalationSkipsDeadProviders:
    def test_anthropic_tiers_skipped_when_dead(self):
        from scaffold.agent.escalation_engine import EscalationEngine

        eng = EscalationEngine(monthly_budget=20.0)
        task = {"action": "fix bug", "file": "x.py", "complexity": "high"}

        # With anthropic dead, escalation should NOT choose Haiku or Sonnet
        decision = eng.decide(
            task=task,
            failure_count=5,
            budget_remaining=10.0,
            dead_providers={"anthropic"},
        )
        assert decision.spec.provider != "anthropic"

    def test_deepseek_skipped_when_dead(self):
        from scaffold.agent.escalation_engine import EscalationEngine

        eng = EscalationEngine(monthly_budget=20.0)
        task = {"action": "fix bug", "file": "x.py", "complexity": "low"}

        decision = eng.decide(
            task=task,
            failure_count=0,
            budget_remaining=10.0,
            dead_providers={"deepseek"},
        )
        assert decision.spec.provider != "deepseek"

    def test_empty_dead_providers_uses_normal_routing(self):
        from scaffold.agent.escalation_engine import EscalationEngine

        eng = EscalationEngine(monthly_budget=20.0)
        task = {"action": "fix bug", "file": "x.py", "complexity": "low"}

        decision = eng.decide(task=task, dead_providers=set())
        # Default for simple task is DeepSeek
        assert decision.spec.provider == "deepseek"
