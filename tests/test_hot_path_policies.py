"""Tests for mcts_policy and learning_policy hot-path defaults."""

from __future__ import annotations

import pytest

from scaffold.agent.learning_policy import (
    apply_kernel_defaults,
    learning_enabled,
    prompt_evolution_enabled,
)
from scaffold.agent.mcts_policy import (
    mcts_enabled,
    mcts_rollout_budget,
    should_run_mcts_fallback,
)


class TestMCTSPolicy:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AWOS_MCTS_DISABLE", raising=False)
        monkeypatch.delenv("AWOS_USE_MCTS", raising=False)
        monkeypatch.setenv("AWOS_MCTS_DEFAULT", "true")
        assert mcts_enabled() is True

    def test_disabled_explicit(self, monkeypatch):
        monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
        assert mcts_enabled() is False

    def test_fallback_after_worker_fail_medium(self, monkeypatch):
        monkeypatch.setenv("AWOS_MCTS_DEFAULT", "true")
        task = {"complexity": "medium"}
        assert should_run_mcts_fallback(task, worker_failed=True) is True

    def test_skip_low_complexity(self, monkeypatch):
        monkeypatch.setenv("AWOS_MCTS_DEFAULT", "true")
        task = {"complexity": "low"}
        assert should_run_mcts_fallback(task, worker_failed=True) is False

    def test_low_with_override(self, monkeypatch):
        monkeypatch.setenv("AWOS_MCTS_DEFAULT", "true")
        monkeypatch.setenv("AWOS_MCTS_ON_LOW", "true")
        task = {"complexity": "low"}
        assert should_run_mcts_fallback(task, worker_failed=True) is True

    def test_no_fallback_if_worker_ok(self, monkeypatch):
        monkeypatch.setenv("AWOS_MCTS_DEFAULT", "true")
        assert should_run_mcts_fallback({"complexity": "high"}, worker_failed=False) is False

    def test_rollout_budget_high(self, monkeypatch):
        monkeypatch.setenv("AWOS_MCTS_ROLLOUTS", "9")
        assert mcts_rollout_budget({"complexity": "high"}) == 9


class TestLearningPolicy:
    def test_prompt_evolution_default_on(self, monkeypatch):
        monkeypatch.delenv("AWOS_PROMPT_EVOLUTION", raising=False)
        monkeypatch.setenv("AWOS_LEARNING_DEFAULT", "true")
        assert prompt_evolution_enabled() is True

    def test_prompt_evolution_disabled(self, monkeypatch):
        monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")
        assert prompt_evolution_enabled() is False

    def test_apply_kernel_defaults(self, monkeypatch):
        for key in (
            "AWOS_RUNTIME_SESSION",
            "AWOS_LEARNING_DEFAULT",
            "AWOS_MCTS_DEFAULT",
        ):
            monkeypatch.delenv(key, raising=False)
        apply_kernel_defaults()
        import os

        assert os.getenv("AWOS_RUNTIME_SESSION") == "true"
        assert os.getenv("AWOS_LEARNING_DEFAULT") == "true"
        assert os.getenv("AWOS_MCTS_DEFAULT") == "true"
