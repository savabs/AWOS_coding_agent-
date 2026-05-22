"""
Tests for the ML Meta-Controller:
  - RewardStore (persistence, reward computation, summary)
  - TaskFeatureExtractor (feature shape, value ranges)
  - LinUCBRouter (select, update, persistence, is_ready)
  - GPWorldModel (predict returns (mean, std), sample_action returns valid int)
  - EscalationEngine integration (ml_router=None backward compat, ml_router active)
"""

import sys
import os
import tempfile
import json
from pathlib import Path

import numpy as np
import pytest

# Ensure scaffold is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── RewardStore ─────────────────────────────────────────────────────────────

class TestRewardStore:
    def _make_store(self, tmp_path):
        from scaffold.agent.reward_store import RewardStore
        return RewardStore(path=tmp_path / "reward_store.jsonl")

    def _sample_task(self):
        return {"task_id": "t1", "action": "fix bug in parser", "complexity": "low", "file": "parser.py"}

    def test_store_returns_episode(self, tmp_path):
        store = self._make_store(tmp_path)
        ep = store.store(self._sample_task(), action_id=1, features=[0.1]*10, success=True, cost_usd=0.001)
        assert ep.success is True
        assert ep.action_id == 1
        assert ep.reward > 0

    def test_total_episodes_increments(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.total_episodes() == 0
        store.store(self._sample_task(), action_id=1, features=[0.1]*10, success=True)
        store.store(self._sample_task(), action_id=2, features=[0.2]*10, success=False)
        assert store.total_episodes() == 2

    def test_get_recent_returns_correct_count(self, tmp_path):
        store = self._make_store(tmp_path)
        for i in range(5):
            store.store(self._sample_task(), action_id=1, features=[0.1]*10, success=(i % 2 == 0))
        recent = store.get_recent(3)
        assert len(recent) == 3

    def test_persists_across_reload(self, tmp_path):
        from scaffold.agent.reward_store import RewardStore
        path = tmp_path / "rs.jsonl"
        s1 = RewardStore(path=path)
        s1.store(self._sample_task(), action_id=1, features=[0.5]*10, success=True)
        s1.store(self._sample_task(), action_id=2, features=[0.3]*10, success=False)

        s2 = RewardStore(path=path)
        assert s2.total_episodes() == 2
        eps = s2.get_recent(10)
        assert eps[0].success is True
        assert eps[1].success is False

    def test_reward_higher_for_success(self):
        from scaffold.agent.reward_store import compute_reward
        r_success = compute_reward(success=True,  action_id=1, cost_usd=0.001)
        r_fail    = compute_reward(success=False, action_id=1, cost_usd=0.001)
        assert r_success > r_fail
        assert r_success == pytest.approx(0.999)   # 1 - 0.05*0.02
        assert r_fail == pytest.approx(-0.0538, abs=1e-3)  # -0.001 - ~0.0528

    def test_reward_cheaper_model_slightly_higher(self):
        from scaffold.agent.reward_store import compute_reward
        r_cheap     = compute_reward(success=True, action_id=1, cost_usd=0.001)  # deepseek
        r_expensive = compute_reward(success=True, action_id=3, cost_usd=0.05)   # sonnet
        assert r_cheap >= r_expensive
        assert r_cheap == pytest.approx(0.999)    # 1 - 0.05*(0.001/0.05)
        assert r_expensive == pytest.approx(0.95)  # 1 - 0.05*1.0

    def test_sonnet_failure_is_negative(self):
        from scaffold.agent.reward_store import compute_reward
        r = compute_reward(success=False, action_id=3, cost_usd=0.05)  # sonnet fail
        assert r < 0, f"Sonnet failure should be negative, got {r}"
        assert r == pytest.approx(-0.35)  # -0.05 opportunity - 0.30 max penalty

    def test_reward_breakdown_tracked(self, tmp_path):
        store = self._make_store(tmp_path)
        ep = store.store(self._sample_task(), action_id=3, features=[0.1]*10, success=False, cost_usd=0.05)
        bd = ep.reward_breakdown
        assert "base_value" in bd
        assert "opportunity_cost" in bd
        assert "failure_penalty" in bd
        assert "cost_ratio" in bd
        assert "params" in bd
        assert bd["base_value"] == 0.0
        assert bd["opportunity_cost"] == pytest.approx(-0.05)
        assert bd["failure_penalty"] == pytest.approx(-0.30)
        assert bd["total_reward"] == pytest.approx(ep.reward)
        assert bd["params"]["spread"] == 50.0  # 0.05 / 0.001

    def test_success_rate(self, tmp_path):
        store = self._make_store(tmp_path)
        for i in range(4):
            store.store(self._sample_task(), action_id=1, features=[0.1]*10, success=(i < 3))
        rate = store.success_rate(action_id=1, window=10)
        assert rate == pytest.approx(0.75)

    def test_summary_structure(self, tmp_path):
        store = self._make_store(tmp_path)
        store.store(self._sample_task(), action_id=1, features=[0.1]*10, success=True)
        s = store.summary()
        assert "total_episodes" in s
        assert "overall_success_rate" in s
        assert "by_action" in s
        assert "opus" not in s.get("by_action", {})


# ─── TaskFeatureExtractor ─────────────────────────────────────────────────────

class TestTaskFeatureExtractor:
    def _extractor(self):
        from scaffold.agent.ml_router import TaskFeatureExtractor
        return TaskFeatureExtractor()

    def test_output_shape(self):
        ext = self._extractor()
        task = {"action": "fix the login bug", "complexity": "medium", "file": "auth.py"}
        feat = ext.extract(task, failure_count=0)
        assert feat.shape == (10,)

    def test_all_values_in_range(self):
        ext = self._extractor()
        tasks = [
            {"action": "add new authentication system", "complexity": "high", "file": "src/auth.py"},
            {"action": "fix bug in parser", "complexity": "low", "file": "tests/test_parser.py"},
            {"action": "refactor database pipeline", "complexity": "medium", "file": "db/pipeline.py"},
        ]
        for task in tasks:
            feat = ext.extract(task, failure_count=2)
            assert np.all(feat >= 0.0), f"Negative feature in {feat}"
            assert np.all(feat <= 1.0), f"Feature > 1.0 in {feat}"

    def test_bias_term_always_one(self):
        ext = self._extractor()
        task = {"action": "rename function", "complexity": "low", "file": "util.py"}
        feat = ext.extract(task, failure_count=0)
        assert feat[9] == 1.0

    def test_bug_fix_flag(self):
        ext = self._extractor()
        task_bug    = {"action": "fix the crash in parser", "complexity": "low"}
        task_nobug  = {"action": "add new feature", "complexity": "low"}
        assert ext.extract(task_bug,   0)[1] == 1.0
        assert ext.extract(task_nobug, 0)[1] == 0.0

    def test_complexity_encoding(self):
        ext = self._extractor()
        for label, expected in [("low", 0.2), ("medium", 0.5), ("high", 0.8)]:
            task = {"action": "do something", "complexity": label}
            assert ext.extract(task, 0)[6] == pytest.approx(expected)

    def test_failure_count_saturates(self):
        ext = self._extractor()
        task = {"action": "fix bug", "complexity": "medium"}
        f3 = ext.extract(task, failure_count=3)[8]
        f9 = ext.extract(task, failure_count=9)[8]
        assert f3 == pytest.approx(1.0)
        assert f9 == pytest.approx(1.0)

    def test_test_file_detected(self):
        ext = self._extractor()
        task = {"action": "update something", "complexity": "low", "file": "tests/test_utils.py"}
        feat = ext.extract(task, 0)
        assert feat[5] == 1.0   # has_tests
        assert feat[7] == 0.0   # file_is_core = False for test files


# ─── LinUCBRouter ─────────────────────────────────────────────────────────────

class TestLinUCBRouter:
    def _router(self, tmp_path, alpha=1.0, min_samples=5):
        from scaffold.agent.ml_router import LinUCBRouter
        return LinUCBRouter(alpha=alpha, min_samples=min_samples,
                            weights_path=tmp_path / "linucb.pkl")

    def _feat(self):
        return np.array([0.3, 1.0, 0.0, 0.0, 0.0, 0.0, 0.5, 1.0, 0.0, 1.0])

    def test_select_returns_valid_action(self, tmp_path):
        router = self._router(tmp_path)
        action = router.select(self._feat())
        assert 0 <= action <= 3

    def test_not_ready_before_min_samples(self, tmp_path):
        router = self._router(tmp_path, min_samples=5)
        assert not router.is_ready()

    def test_ready_after_min_samples(self, tmp_path):
        router = self._router(tmp_path, min_samples=5)
        feat = self._feat()
        for _ in range(5):
            router.update(feat, action_id=1, reward=1.0)
        assert router.is_ready()

    def test_update_modifies_weights(self, tmp_path):
        router = self._router(tmp_path)
        A_before = router._A[1].copy()
        router.update(self._feat(), action_id=1, reward=1.0)
        assert not np.allclose(router._A[1], A_before)

    def test_b_vector_increases_on_positive_reward(self, tmp_path):
        router = self._router(tmp_path)
        b_before = router._b[1].copy()
        router.update(self._feat(), action_id=1, reward=1.0)
        assert not np.allclose(router._b[1], b_before)

    def test_budget_mask_excludes_action(self, tmp_path):
        router = self._router(tmp_path, min_samples=1)
        router.update(self._feat(), action_id=3, reward=1.0)  # give high reward to sonnet
        # Mask out sonnet (action 3)
        mask = [True, True, True, False]
        for _ in range(10):
            action = router.select(self._feat(), budget_mask=mask)
            assert action in (0, 1, 2), f"Expected action in [0,1,2], got {action}"

    def test_persists_and_reloads(self, tmp_path):
        from scaffold.agent.ml_router import LinUCBRouter
        path = tmp_path / "weights.pkl"
        r1 = LinUCBRouter(min_samples=3, weights_path=path)
        feat = self._feat()
        for _ in range(5):
            r1.update(feat, action_id=2, reward=1.0)
        r1.save_now()

        r2 = LinUCBRouter(min_samples=3, weights_path=path)
        assert r2.total_updates() == 5
        assert r2.is_ready()
        assert np.allclose(r2._A[2], r1._A[2])

    def test_summary_contains_all_actions(self, tmp_path):
        router = self._router(tmp_path)
        s = router.summary()
        assert "learned_weights" in s
        assert set(s["learned_weights"].keys()) == {"gemini_flash", "deepseek", "haiku", "sonnet"}

    def test_learns_to_prefer_rewarded_action(self, tmp_path):
        """After many updates rewarding action 1 (deepseek), it should be preferred."""
        router = self._router(tmp_path, min_samples=5, alpha=0.1)  # low alpha = less exploration
        feat = self._feat()
        for _ in range(30):
            router.update(feat, action_id=1, reward=1.0)  # deepseek always wins
            router.update(feat, action_id=3, reward=0.0)  # sonnet always fails
        chosen = router.select(feat)
        assert chosen == 1, f"Expected deepseek (1), got {chosen}"


# ─── GPWorldModel ─────────────────────────────────────────────────────────────

class TestGPWorldModel:
    def test_predict_before_fit_returns_defaults(self, tmp_path):
        from scaffold.agent.ml_router import GPWorldModel
        gp = GPWorldModel(gp_path=tmp_path / "gp.pkl")
        mean, std = gp.predict(np.zeros(10), action_id=0)
        assert isinstance(mean, float)
        assert isinstance(std, float)
        assert std > 0   # high uncertainty before fitting

    def test_sample_action_returns_valid_int(self, tmp_path):
        from scaffold.agent.ml_router import GPWorldModel, N_ACTIONS
        gp = GPWorldModel(gp_path=tmp_path / "gp.pkl")
        action = gp.sample_action(np.zeros(10))
        assert 0 <= action < N_ACTIONS

    def test_not_ready_before_fit(self, tmp_path):
        from scaffold.agent.ml_router import GPWorldModel
        gp = GPWorldModel(gp_path=tmp_path / "gp.pkl")
        assert not gp.is_ready()


# ─── EscalationEngine integration ─────────────────────────────────────────────

class TestEscalationEngineIntegration:
    def _task(self, complexity="medium"):
        return {"task_id": "t1", "action": "fix the login bug", "complexity": complexity, "file": "auth.py"}

    def test_backward_compat_no_ml_router(self):
        from scaffold.agent.escalation_engine import EscalationEngine
        engine = EscalationEngine(monthly_budget=20.0)
        decision = engine.decide(self._task())
        assert decision.spec is not None
        assert "LinUCB" not in decision.reason

    def test_heuristic_mode_when_not_ready(self, tmp_path):
        from scaffold.agent.escalation_engine import EscalationEngine
        from scaffold.agent.ml_router import build_ml_router
        router = build_ml_router(min_samples=20, weights_path=tmp_path / "w.pkl")
        engine = EscalationEngine(monthly_budget=20.0, ml_router=router)
        decision = engine.decide(self._task())
        assert "LinUCB" not in decision.reason  # not ready yet → heuristic

    def test_linucb_takes_over_when_ready(self, tmp_path):
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel
        from scaffold.agent.ml_router import build_ml_router, TaskFeatureExtractor
        router = build_ml_router(min_samples=5, weights_path=tmp_path / "w.pkl")
        ext = TaskFeatureExtractor()
        task = self._task()
        feat = ext.extract(task, 0)
        for _ in range(5):
            router.update(feat, action_id=1, reward=1.0)
        assert router.is_ready()

        engine = EscalationEngine(monthly_budget=20.0, ml_router=router)
        decision = engine.decide(task)
        assert "LinUCB" in decision.reason

    def test_record_outcome_updates_linucb(self, tmp_path):
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel
        from scaffold.agent.ml_router import build_ml_router
        router = build_ml_router(min_samples=100, weights_path=tmp_path / "w.pkl")
        engine = EscalationEngine(monthly_budget=20.0, ml_router=router)
        before = router.total_updates()
        engine.record_outcome(
            "t1", EscalationLevel.DEEPSEEK, success=True,
            task=self._task()
        )
        assert router.total_updates() == before + 1

    def test_record_outcome_backward_compat(self):
        """Old callers that don't pass task= still work."""
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel
        engine = EscalationEngine(monthly_budget=20.0)
        engine.record_outcome("t1", EscalationLevel.DEEPSEEK, success=True)
        assert engine.failure_count("t1") == 0

    def test_force_level_overrides_linucb(self, tmp_path):
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel
        from scaffold.agent.ml_router import build_ml_router, TaskFeatureExtractor
        router = build_ml_router(min_samples=5, weights_path=tmp_path / "w.pkl")
        ext = TaskFeatureExtractor()
        task = self._task()
        feat = ext.extract(task, 0)
        for _ in range(10):
            router.update(feat, action_id=1, reward=1.0)
        engine = EscalationEngine(monthly_budget=20.0, ml_router=router)
        decision = engine.decide(task, force_level=EscalationLevel.SONNET)
        assert decision.spec.level == EscalationLevel.SONNET
        assert decision.forced is True


# ─── ReplayGate ──────────────────────────────────────────────────────────────

class TestReplayGate:
    def _make_ep(self, reward: float, features: list, action_id: int = 1, ep_id: str = "ep1"):
        from scaffold.agent.reward_store import Episode
        return Episode(
            episode_id=ep_id,
            task_id="t1",
            action_id=action_id,
            features=features,
            success=(reward > 0),
            cost_usd=0.001,
            latency_ms=500.0,
            reward=reward,
        )

    def test_high_reward_admitted(self):
        from scaffold.agent.reward_store import ReplayGate
        gate = ReplayGate(threshold=0.4)
        ep = self._make_ep(reward=0.95, features=[0.5] * 10)
        assert gate.admit(ep) is True

    def test_near_zero_reward_filtered_with_saturated_gate(self):
        from scaffold.agent.reward_store import ReplayGate
        gate = ReplayGate(threshold=0.4)
        feat = [0.5] * 10
        # Saturate novelty buffer with identical features and action 1
        for i in range(60):
            gate.admit(self._make_ep(reward=0.9, features=feat, action_id=1, ep_id=f"ep_sat_{i}"))
        # Now near-zero reward + same features + majority action → all 3 components low
        ep = self._make_ep(reward=0.001, features=feat, action_id=1, ep_id="ep_low")
        score = gate.score(ep)
        # informativeness ≈ 0, novelty ≈ 0 (same features), boundary ≈ 0 (majority action)
        assert score < 0.4, f"Expected score < 0.4, got {score}"
        assert gate.admit(ep) is False

    def test_duplicate_features_reduce_novelty(self):
        from scaffold.agent.reward_store import ReplayGate
        gate = ReplayGate(threshold=0.4, min_novelty_dist=0.15)
        feat = [0.5] * 10
        ep1 = self._make_ep(reward=0.9, features=feat, ep_id="ep1")
        ep2 = self._make_ep(reward=0.9, features=feat, ep_id="ep2")
        gate.admit(ep1)  # fills novelty buffer
        score2 = gate.score(ep2)
        # Novelty component will be ~0 for identical features
        assert score2 < gate.score(ep1) or score2 < 0.7

    def test_score_in_unit_interval(self):
        from scaffold.agent.reward_store import ReplayGate
        gate = ReplayGate()
        for r in [1.0, 0.5, -0.3, 0.0]:
            ep = self._make_ep(reward=r, features=[float(i) * 0.1 for i in range(10)])
            s = gate.score(ep)
            assert 0.0 <= s <= 1.0, f"score={s} out of [0,1] for reward={r}"


# ─── GPWorldModel Thompson Sampling ──────────────────────────────────────────

class TestGPWorldModel:
    def test_predict_returns_mean_std_when_unfitted(self):
        from scaffold.agent.ml_router import GPWorldModel
        gp = GPWorldModel()
        mean, std = gp.predict([0.1] * 10, action_id=0)
        assert mean == pytest.approx(0.5)
        assert std == pytest.approx(1.0)

    def test_is_ready_false_before_fit(self):
        from scaffold.agent.ml_router import GPWorldModel
        gp = GPWorldModel()
        assert gp.is_ready() is False

    def test_sample_action_valid_range(self):
        from scaffold.agent.ml_router import GPWorldModel, N_ACTIONS
        gp = GPWorldModel()
        a = gp.sample_action(np.array([0.1] * 10))
        assert 0 <= a < N_ACTIONS

    def test_linucb_uses_thompson_sampling_when_gp_ready(self, tmp_path):
        from scaffold.agent.ml_router import LinUCBRouter, GPWorldModel, N_ACTIONS
        from unittest.mock import MagicMock, patch
        router = LinUCBRouter(weights_path=tmp_path / "w.pkl")
        mock_gp = MagicMock(spec=GPWorldModel)
        mock_gp.is_ready.return_value = True
        mock_gp.predict.return_value = (0.8, 0.1)
        router.attach_gp(mock_gp)
        feat = np.array([0.1] * 10)
        action = router.select(feat)
        assert mock_gp.predict.called
        assert 0 <= action < N_ACTIONS


# ─── StrategyConfig ──────────────────────────────────────────────────────────

class TestStrategyConfig:
    def _task(self, action: str) -> dict:
        return {"task_id": 1, "action": action, "file": "x.py", "complexity": "medium"}

    def test_strategies_dict_has_four_entries(self):
        from scaffold.agent.strategy_config import STRATEGIES
        assert len(STRATEGIES) == 4
        assert set(STRATEGIES) == {"direct", "cot", "step_by_step", "rubber_duck"}

    def test_classify_bug_fix(self):
        from scaffold.agent.strategy_config import StrategyRouter
        r = StrategyRouter()
        assert r.classify(self._task("fix the crash in login")) == "bug_fix"

    def test_classify_new_feature(self):
        from scaffold.agent.strategy_config import StrategyRouter
        r = StrategyRouter()
        assert r.classify(self._task("add oauth2 login")) == "new_feature"

    def test_classify_refactor(self):
        from scaffold.agent.strategy_config import StrategyRouter
        r = StrategyRouter()
        assert r.classify(self._task("refactor token validation")) == "refactor"

    def test_cold_start_returns_first_unexplored(self):
        from scaffold.agent.strategy_config import StrategyRouter, STRATEGIES
        r = StrategyRouter()
        task = self._task("add new feature")
        chosen = r.select(task)
        assert chosen.name in STRATEGIES

    def test_update_increments_count(self, tmp_path):
        from scaffold.agent.strategy_config import StrategyRouter
        r = StrategyRouter(weights_path=tmp_path / "s.json")
        task = self._task("fix bug in parser")
        r.update(task, "direct", reward=0.9)
        s = r.summary()
        assert s["bug_fix"]["direct"]["count"] == 1
        assert s["bug_fix"]["direct"]["mean_reward"] == pytest.approx(0.9)

    def test_ucb_prefers_higher_mean_reward(self, tmp_path):
        from scaffold.agent.strategy_config import StrategyRouter
        r = StrategyRouter(weights_path=tmp_path / "s.json")
        task = self._task("refactor the auth module")
        # Prime direct with high reward, cot with low reward (many trials to suppress exploration)
        for _ in range(20):
            r.update(task, "direct", reward=0.9)
            r.update(task, "cot", reward=-0.1)
            r.update(task, "step_by_step", reward=0.9)
            r.update(task, "rubber_duck", reward=0.9)
        chosen = r.select(task)
        assert chosen.name != "cot"

    def test_persistence_roundtrip(self, tmp_path):
        from scaffold.agent.strategy_config import StrategyRouter
        p = tmp_path / "s.json"
        r1 = StrategyRouter(weights_path=p)
        task = self._task("add new widget")
        r1.update(task, "cot", reward=0.7)
        r2 = StrategyRouter(weights_path=p)
        s = r2.summary()
        assert s["new_feature"]["cot"]["count"] == 1
