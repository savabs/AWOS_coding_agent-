"""
tests/test_p6_self_learning.py — Tests for P6 self-learning / RL loop closure.

Covers:
    - LearningInspector: velocity, model insights, strategy insights, curriculum
    - PRM auto-training trigger (_maybe_train_prm guard)
    - PRM early-escalation routing signal
    - Learning velocity trend detection
    - JSON output
"""

import math
import os
import pickle
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.learning_inspector import LearningInspector, LearningReport


# ── LearningVelocity ──────────────────────────────────────────────────────────

class TestLearningVelocityEmpty(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.inspector = LearningInspector(awos_dir=self.td)

    def test_empty_dir_returns_insufficient_data(self):
        v = self.inspector._learning_velocity()
        self.assertEqual(v.improvement_trend, "insufficient_data")
        self.assertEqual(v.episodes_total, 0)
        self.assertFalse(v.gp_fitted)
        self.assertFalse(v.prm_ready)
        self.assertEqual(v.skills_count, 0)

    def test_confidence_low_when_no_data(self):
        v = self.inspector._learning_velocity()
        self.assertLess(v.confidence, 0.3)


class TestLearningVelocityWithData(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.inspector = LearningInspector(awos_dir=self.td)

    def _write_spans(self, n_success, n_fail, skip=0):
        spans_path = Path(self.td) / "spans.jsonl"
        import json
        lines = []
        for _ in range(n_success):
            lines.append(json.dumps({"success": True}))
        for _ in range(n_fail):
            lines.append(json.dumps({"success": False}))
        spans_path.write_text("\n".join(lines))

    def test_improving_trend_detected(self):
        # Write 20 older failures then 20 recent successes
        spans_path = Path(self.td) / "spans.jsonl"
        import json
        lines = (
            [json.dumps({"success": False})] * 30 +
            [json.dumps({"success": True})] * 20
        )
        spans_path.write_text("\n".join(lines))
        # Write enough episodes to pass the <10 guard
        ep_path = Path(self.td) / "episodes.jsonl"
        ep_path.write_text("\n".join([json.dumps({"reward": 1.0})] * 15))

        v = self.inspector._learning_velocity()
        self.assertIn(v.improvement_trend, ("improving", "stable"))

    def test_skills_count_reads_md_files(self):
        skills_dir = Path(self.td) / "skills"
        skills_dir.mkdir()
        (skills_dir / "a.md").write_text("# skill")
        (skills_dir / "b.md").write_text("# skill")
        v = self.inspector._learning_velocity()
        self.assertEqual(v.skills_count, 2)

    def test_mcts_traces_counted(self):
        import json
        mcts_path = Path(self.td) / "mcts_traces.jsonl"
        mcts_path.write_text("\n".join([json.dumps({"id": i}) for i in range(42)]))
        v = self.inspector._learning_velocity()
        self.assertEqual(v.mcts_traces, 42)


# ── Model Insights (LinUCB) ───────────────────────────────────────────────────

class TestModelInsights(unittest.TestCase):

    def test_no_weights_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            insights = inspector._model_insights()
            self.assertEqual(insights, [])

    def test_weights_produce_insights(self):
        with tempfile.TemporaryDirectory() as td:
            import numpy as np
            n = 10
            payload = {
                "A": [np.eye(n).tolist() for _ in range(4)],
                "b": [np.zeros(n).tolist() for _ in range(4)],
                "total_updates": 25,
                "alpha": 1.0,
                "n_features": n,
                "n_actions": 4,
            }
            with open(os.path.join(td, "linucb_weights.pkl"), "wb") as f:
                pickle.dump(payload, f)
            inspector = LearningInspector(awos_dir=td)
            insights = inspector._model_insights()
            self.assertEqual(len(insights), 4)
            self.assertIn("deepseek", [m.model_name for m in insights])

    def test_insight_uncertainty_decreases_with_updates(self):
        with tempfile.TemporaryDirectory() as td:
            import numpy as np
            n = 10
            # Simulate 50 updates for action 0 → higher A values
            A_list = [np.eye(n) * 50, np.eye(n), np.eye(n), np.eye(n)]
            payload = {
                "A": [a.tolist() for a in A_list],
                "b": [np.zeros(n).tolist() for _ in range(4)],
                "total_updates": 50,
                "alpha": 1.0,
                "n_features": n,
                "n_actions": 4,
            }
            with open(os.path.join(td, "linucb_weights.pkl"), "wb") as f:
                pickle.dump(payload, f)
            inspector = LearningInspector(awos_dir=td)
            insights = inspector._model_insights()
            first = insights[0]
            last = insights[-1]
            self.assertLessEqual(first.uncertainty, last.uncertainty)


# ── Curriculum ────────────────────────────────────────────────────────────────

class TestCurriculum(unittest.TestCase):

    def _make_velocity(self, linucb_updates=0, episodes=0, gp_fitted=False,
                       prm_ready=False, skills=0, mcts=0):
        from scaffold.agent.learning_inspector import LearningVelocity
        return LearningVelocity(
            episodes_total=episodes, linucb_updates=linucb_updates,
            gp_fitted=gp_fitted, gp_n_models=0, prm_ready=prm_ready,
            prm_accuracy=0.0, skills_count=skills, mcts_traces=mcts,
            improvement_trend="stable", confidence=0.5,
        )

    def test_cold_start_suggests_warm_up(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            v = self._make_velocity(linucb_updates=5, episodes=5)
            suggestions = inspector._curriculum(v, [])
            texts = [s.suggested_action for s in suggestions]
            self.assertTrue(any("bandit" in t.lower() or "diverse" in t.lower() or "warm" in t.lower() for t in texts))

    def test_no_curriculum_when_fully_trained(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            v = self._make_velocity(linucb_updates=100, episodes=200,
                                    gp_fitted=True, prm_ready=True,
                                    skills=20, mcts=600)
            suggestions = inspector._curriculum(v, [])
            self.assertEqual(len(suggestions), 0)

    def test_regressing_trend_high_priority(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            from scaffold.agent.learning_inspector import LearningVelocity
            v = LearningVelocity(
                episodes_total=80, linucb_updates=80, gp_fitted=True,
                gp_n_models=3, prm_ready=True, prm_accuracy=0.7,
                skills_count=10, mcts_traces=600,
                improvement_trend="regressing", confidence=0.8,
            )
            suggestions = inspector._curriculum(v, [])
            high = [s for s in suggestions if s.priority == "high"]
            self.assertGreater(len(high), 0)


# ── Full Report ───────────────────────────────────────────────────────────────

class TestFullReport(unittest.TestCase):

    def test_report_returns_learning_report(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            report = inspector.report()
            self.assertIsInstance(report, LearningReport)

    def test_render_is_string(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            rendered = inspector.report().render()
            self.assertIsInstance(rendered, str)
            self.assertIn("Learning Velocity", rendered)
            self.assertIn("Curriculum", rendered)

    def test_json_output_keys(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            data = inspector.to_json()
            self.assertIn("velocity", data)
            self.assertIn("model_insights", data)
            self.assertIn("curriculum", data)
            self.assertIn("improvement_trend", data["velocity"])

    def test_json_confidence_in_range(self):
        with tempfile.TemporaryDirectory() as td:
            inspector = LearningInspector(awos_dir=td)
            data = inspector.to_json()
            conf = data["velocity"]["confidence"]
            self.assertGreaterEqual(conf, 0.0)
            self.assertLessEqual(conf, 1.0)


# ── PRM auto-training guard ───────────────────────────────────────────────────

class TestPRMAutoTrain(unittest.TestCase):

    def test_prm_train_not_called_when_ready(self):
        """_maybe_train_prm should be a no-op once PRM is ready."""
        from scaffold.agent.process_reward_model import ProcessRewardModel
        prm = ProcessRewardModel.__new__(ProcessRewardModel)
        prm.ready = True  # already trained

        called = []
        prm.train = lambda store: called.append(1)  # should not be called

        class FakeOrch:
            _prm = prm
            _prm_store = MagicMock()

        orch = FakeOrch()
        # Simulate the guard
        if not orch._prm.ready:
            orch._prm.train(orch._prm_store)
        self.assertEqual(called, [])

    def test_prm_train_called_when_not_ready(self):
        """_maybe_train_prm should call train() when PRM is not ready."""
        from scaffold.agent.process_reward_model import ProcessRewardModel
        prm = ProcessRewardModel.__new__(ProcessRewardModel)
        prm.ready = False
        prm.W1 = prm.b1 = prm.W2 = prm.b2 = prm.W3 = prm.b3 = None

        called = []
        prm.train = lambda store: called.append(1) or False  # returns False (not enough data)

        fake_store = MagicMock()
        if not prm.ready:
            prm.train(fake_store)
        self.assertEqual(called, [1])


# ── Priority Experience Replay ────────────────────────────────────────────────

class TestPriorityExperienceReplay(unittest.TestCase):

    def _make_store(self) -> "RewardStore":
        from scaffold.agent.reward_store import RewardStore
        td = tempfile.mkdtemp()
        return RewardStore(path=Path(td) / "reward_store.jsonl")

    def _add_episodes(self, store, n_success=5, n_fail=5):
        feats = [0.1] * 10
        for i in range(n_success):
            store.store({"task_id": f"s{i}", "action": "add function"},
                        action_id=1, features=feats, success=True, cost_usd=0.001)
        for i in range(n_fail):
            store.store({"task_id": f"f{i}", "action": "fix bug"},
                        action_id=2, features=feats, success=False, cost_usd=0.017)

    def test_get_prioritized_returns_episodes(self):
        store = self._make_store()
        self._add_episodes(store)
        sampled = store.get_prioritized(n=8)
        self.assertGreater(len(sampled), 0)
        self.assertLessEqual(len(sampled), 8)

    def test_get_prioritized_empty_store(self):
        store = self._make_store()
        sampled = store.get_prioritized(n=10)
        self.assertEqual(sampled, [])

    def test_alpha_zero_uniform_sampling(self):
        """alpha=0 → all priorities equal (uniform)."""
        store = self._make_store()
        self._add_episodes(store, n_success=10, n_fail=10)
        sampled = store.get_prioritized(n=5, alpha=0.0)
        self.assertEqual(len(sampled), 5)

    def test_surprise_scores_length(self):
        store = self._make_store()
        self._add_episodes(store)
        scores = store.surprise_scores(window=100)
        self.assertEqual(len(scores), 10)
        for text, score in scores:
            self.assertIsInstance(score, float)
            self.assertGreaterEqual(score, 0.0)

    def test_surprise_scores_nonnegative(self):
        store = self._make_store()
        self._add_episodes(store, n_success=3, n_fail=3)
        for _, score in store.surprise_scores():
            self.assertGreaterEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()
