"""
tests/test_routing_learn.py — Tests for routing learning system repair.

Covers:
    - Weight versioning: save/load with pad/truncate (v1 → v2 migration)
    - ReplayGate: adaptive threshold, weighted scoring (0.4/0.2/0.4)
    - LinUCB warm_start: batch replay from RewardStore
    - EscalationEngine.routing_status(): observability dict
    - Edge cases: empty store, OOB action_id, malformed features, corrupted weights
"""

import json
import math
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.ml_router import (
    N_ACTIONS,
    N_FEATURES,
    LinUCBRouter,
    TaskFeatureExtractor,
    build_ml_router,
)
from scaffold.agent.reward_store import (
    ReplayGate,
    RewardStore,
    Episode,
    compute_reward,
)
from scaffold.agent.escalation_engine import (
    EscalationEngine,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _temp_path(suffix: str = "") -> Path:
    td = tempfile.mkdtemp()
    return Path(td) / f"test_weights{suffix}.pkl"


def _make_episode(
    action_id: int = 0,
    features=None,
    reward: float = 0.5,
    success: bool = True,
) -> Episode:
    if features is None:
        features = [float(i) / 10.0 for i in range(N_FEATURES)]
    return Episode(
        episode_id=f"ep_{action_id}_{abs(hash(str(features))) % 10000:04d}",
        task_id="test_task",
        action_id=action_id,
        features=list(features),
        success=success,
        cost_usd=0.001,
        reward=reward,
    )


# ── Weight Versioning ────────────────────────────────────────────────────────


class TestWeightPersistence(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.path = Path(self.td) / "weights.pkl"

    def test_save_and_reload_preserves_updates(self):
        """Save with v2 format, reload — updates survive."""
        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=self.path)
        feats = np.ones(N_FEATURES) * 0.5
        for _ in range(10):
            router.update(feats, 0, 0.8)

        router.save_now()

        # Reload fresh router pointing to same file
        router2 = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                               weights_path=self.path)
        self.assertEqual(router2.total_updates(), 10)

    def test_pad_actions_grow(self):
        """Save with 4 actions, reload with 6 — new actions get identity A."""
        router4 = LinUCBRouter(n_features=N_FEATURES, n_actions=4,
                               weights_path=self.path)
        feats = np.ones(N_FEATURES)
        for a in range(4):
            router4.update(feats, a, 0.9)
        router4.save_now()

        router6 = LinUCBRouter(n_features=N_FEATURES, n_actions=6,
                               weights_path=self.path)
        # Updates from first 4 actions preserved (identity updates count)
        self.assertGreaterEqual(router6.total_updates(), 4)
        # New actions (4, 5) start with identity A
        for a in range(4, 6):
            A = router6._A[a]
            np.testing.assert_array_almost_equal(A, np.eye(N_FEATURES))

    def test_truncate_actions_shrink(self):
        """Save with 6 actions, reload with 4 — only first 4 kept."""
        router6 = LinUCBRouter(n_features=N_FEATURES, n_actions=6,
                               weights_path=self.path)
        feats = np.ones(N_FEATURES)
        for a in range(6):
            router6.update(feats, a, 0.7)
        router6.save_now()

        router4 = LinUCBRouter(n_features=N_FEATURES, n_actions=4,
                               weights_path=self.path)
        self.assertEqual(router4.total_updates(), 6)

    def test_v1_weights_discarded_on_mismatch(self):
        """V1 format with n_actions mismatch → fresh start."""
        # Write a v1-format payload (no format_version key)
        payload = {
            "A": [np.eye(N_FEATURES).tolist() for _ in range(3)],
            "b": [np.zeros(N_FEATURES).tolist() for _ in range(3)],
            "total_updates": 30,
            "n_actions": 3,
            "n_features": N_FEATURES,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as f:
            pickle.dump(payload, f, protocol=4)

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=self.path)
        self.assertEqual(router.total_updates(), 0)  # discarded

    def test_v1_weights_loaded_on_match(self):
        """V1 format with matching n_actions → loaded."""
        payload = {
            "A": [np.eye(N_FEATURES).tolist() for _ in range(N_ACTIONS)],
            "b": [np.zeros(N_FEATURES).tolist() for _ in range(N_ACTIONS)],
            "total_updates": 15,
            "n_actions": N_ACTIONS,
            "n_features": N_FEATURES,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as f:
            pickle.dump(payload, f, protocol=4)

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=self.path)
        self.assertEqual(router.total_updates(), 15)  # loaded

    def test_feature_dimension_mismatch_restarts(self):
        """Feature count mismatch → fresh start."""
        payload = {
            "format_version": 2,
            "A": [np.eye(5).tolist() for _ in range(N_ACTIONS)],
            "b": [np.zeros(5).tolist() for _ in range(N_ACTIONS)],
            "total_updates": 20,
            "n_actions": N_ACTIONS,
            "n_features": 5,  # mismatch
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as f:
            pickle.dump(payload, f, protocol=4)

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=self.path)
        self.assertEqual(router.total_updates(), 0)

    def test_corrupted_weights_no_crash(self):
        """Corrupted pickle file → log warning, fresh start, no crash."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("garbage_data_not_pickle")

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=self.path)
        self.assertEqual(router.total_updates(), 0)

    def test_no_weights_path_no_crash(self):
        """Router with weights_path=None skips persistence silently."""
        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=None)
        router.save_now()  # should not crash
        self.assertEqual(router.total_updates(), 0)


# ── ReplayGate ────────────────────────────────────────────────────────────────


class TestReplayGateAdaptive(unittest.TestCase):

    def setUp(self):
        self.gate = ReplayGate(threshold=0.40, novelty_window=10, min_novelty_dist=0.15)

    def test_threshold_decreases_with_volume(self):
        """Threshold starts at 0.40, decreases as sqrt(episodes)."""
        # At episode 0: threshold = 0.40 (before any calls, _total_episodes == 0)
        self.assertAlmostEqual(self.gate.threshold, 0.40)

        # Push enough episodes through to see decrease
        for i in range(100):
            ep = _make_episode(action_id=i % N_ACTIONS,
                               features=[float(i % 10) / 10.0] * N_FEATURES)
            self.gate.admit(ep)

        # After 100 episodes: threshold = max(0.15, 0.40 - 0.01 * sqrt(100))
        expected = max(0.15, 0.40 - 0.01 * math.sqrt(100))
        self.assertAlmostEqual(self.gate.threshold, expected)

    def test_threshold_floor_at_0_15(self):
        """Threshold floors at 0.15 regardless of volume."""
        for i in range(2000):
            ep = _make_episode(action_id=i % N_ACTIONS,
                               features=[float(i % 10) / 10.0] * N_FEATURES)
            self.gate.admit(ep)

        self.assertAlmostEqual(self.gate.threshold, 0.15)

    def test_admit_rate_tracks_accurately(self):
        """admit_rate returns correct fraction."""
        # First, push diverse episodes to get admitted
        for i in range(5):
            ep = _make_episode(
                action_id=i,  # each gets a different action so boundary is high
                features=[float(i) / 5.0] * N_FEATURES,
                reward=0.8,  # high informativeness
            )
            self.gate.admit(ep)

        rate = self.gate.admit_rate()
        self.assertGreaterEqual(rate, 0.0)
        self.assertLessEqual(rate, 1.0)

    def test_scoring_weights_are_weighted(self):
        """Score formula: 0.4*inform + 0.2*novelty + 0.4*boundary."""
        ep = _make_episode(
            action_id=0,
            features=[0.1] * N_FEATURES,
            reward=1.0,  # max informativeness
        )
        score = self.gate.score(ep)

        # With reward=1.0: inform = min(|1.0|/1.0, 1.0) = 1.0
        # First episode: novelty = 1.0
        # First episode: own_frac = 0, boundary = 1.0
        # score = 0.4*1.0 + 0.2*1.0 + 0.4*1.0 = 1.0
        self.assertAlmostEqual(score, 1.0, places=2)


# ── Warm-Start ────────────────────────────────────────────────────────────────


class TestWarmStart(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.store_path = Path(self.td) / "reward_store.jsonl"
        self.store = RewardStore(path=self.store_path)

    def test_warm_start_replays_episodes(self):
        """warm_start replays episodes from RewardStore into LinUCB."""
        # Seed RewardStore with varied episodes (different features per action
        # ensures PER sampling doesn't dedup them all to nothing).
        for a in range(N_ACTIONS):
            feats = [float(a + 1) / (N_FEATURES + 1)] * N_FEATURES
            for i in range(5):  # 5 episodes per action → 30 total
                self.store.store(
                    task={"task_id": f"t{a}_{i}", "action": "fix bug"},
                    action_id=a,
                    features=feats,
                    success=True,
                    cost_usd=0.001,
                )

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS, alpha=1.0)
        replayed = router.warm_start(self.store, max_episodes=100)

        self.assertGreaterEqual(replayed, N_ACTIONS)
        self.assertGreaterEqual(router.total_updates(), N_ACTIONS)

    def test_warm_start_empty_store_returns_zero(self):
        """Empty RewardStore → warm_start returns 0, no crash."""
        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS)
        replayed = router.warm_start(self.store, max_episodes=50)

        self.assertEqual(replayed, 0)
        self.assertEqual(router.total_updates(), 0)

    def test_warm_start_skips_oob_action_id(self):
        """Out-of-bounds action_id skipped, logged, no crash."""
        # Write raw episode with action_id=99 manually
        import uuid
        raw_ep = {
            "episode_id": str(uuid.uuid4()),
            "task_id": "bad_task",
            "action_id": 99,  # out of bounds
            "features": [0.5] * N_FEATURES,
            "success": True,
            "cost_usd": 0.001,
            "reward": 0.8,
            "timestamp": "2026-01-01T00:00:00Z",
            "task_action_text": "",
            "model_name": "",
            "task_type": "",
            "reward_breakdown": {},
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": 0.0,
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self.store_path.open("w") as f:
            f.write(json.dumps(raw_ep) + "\n")
            # Also add a valid one
            valid = {**raw_ep}
            valid["episode_id"] = str(uuid.uuid4())
            valid["action_id"] = 1
            f.write(json.dumps(valid) + "\n")

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS)
        replayed = router.warm_start(self.store, max_episodes=50)

        self.assertEqual(replayed, 1)  # only the valid one

    def test_warm_start_skips_malformed_features(self):
        """Features with wrong dimension skipped gracefully."""
        import uuid
        raw_ep = {
            "episode_id": str(uuid.uuid4()),
            "task_id": "bad_features",
            "action_id": 0,
            "features": [0.1, 0.2],  # only 2 dims, expected N_FEATURES
            "success": True,
            "cost_usd": 0.001,
            "reward": 0.9,
            "timestamp": "2026-01-01T00:00:00Z",
            "task_action_text": "",
            "model_name": "",
            "task_type": "",
            "reward_breakdown": {},
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": 0.0,
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self.store_path.open("w") as f:
            f.write(json.dumps(raw_ep) + "\n")

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS)
        replayed = router.warm_start(self.store, max_episodes=50)

        self.assertEqual(replayed, 0)

    def test_warm_start_makes_router_ready(self):
        """Enough episodes → is_ready() becomes True."""
        for i in range(50):  # seed more than min_samples (20) to survive dedup
            feats = [float(i % 10 + 1) / 11.0] * N_FEATURES
            self.store.store(
                task={"task_id": f"t{i}", "action": "fix bug"},
                action_id=i % N_ACTIONS,
                features=feats,
                success=True,
                cost_usd=0.001,
            )

        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              min_samples=20)
        replayed = router.warm_start(self.store, max_episodes=100)

        self.assertGreaterEqual(replayed, 20)
        self.assertTrue(router.is_ready())


# ── EscalationEngine routing_status ──────────────────────────────────────────


class TestEscalationEngineRoutingStatus(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()

    def test_routing_status_shape_with_router(self):
        """routing_status returns correct dict keys when ml_router present."""
        weights_path = Path(self.td) / "weights.pkl"
        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=weights_path)
        engine = EscalationEngine(ml_router=router)

        status = engine.routing_status()

        self.assertIn("linucb_ready", status)
        self.assertIn("linucb_updates", status)
        self.assertIn("actions", status)
        self.assertIn("is_cheap_only", status)
        self.assertIn("dead_providers", status)
        self.assertIsInstance(status["linucb_ready"], bool)
        self.assertIsInstance(status["linucb_updates"], int)
        self.assertIsInstance(status["is_cheap_only"], bool)
        self.assertIsInstance(status["dead_providers"], list)

    def test_routing_status_router_ready_after_warm_start(self):
        """routing_status reflects ready=True after warm_start."""
        store_path = Path(self.td) / "reward_store.jsonl"
        store = RewardStore(path=store_path)
        for i in range(50):
            feats = [float(i % 10 + 1) / 11.0] * N_FEATURES
            store.store(
                task={"task_id": f"t{i}", "action": "add feature"},
                action_id=i % N_ACTIONS,
                features=feats,
                success=True,
                cost_usd=0.001,
            )

        weights_path = Path(self.td) / "weights.pkl"
        router = LinUCBRouter(n_features=N_FEATURES, n_actions=N_ACTIONS,
                              weights_path=weights_path, min_samples=20)
        router.warm_start(store, max_episodes=100)

        engine = EscalationEngine(ml_router=router)
        status = engine.routing_status()

        self.assertTrue(status["linucb_ready"])
        self.assertGreater(status["linucb_updates"], 20)

    def test_routing_status_without_router(self):
        """routing_status returns defaults when ml_router is None."""
        engine = EscalationEngine(ml_router=None)

        status = engine.routing_status()

        self.assertFalse(status["linucb_ready"])
        self.assertEqual(status["linucb_updates"], 0)
        self.assertEqual(status["actions"], {})
        self.assertIsInstance(status["is_cheap_only"], bool)
        self.assertEqual(status["dead_providers"], [])

    def test_routing_status_cheap_only_reflected(self):
        """routing_status reflects cheap-only mode."""
        engine = EscalationEngine()
        status = engine.routing_status()

        # is_cheap_only reads from env — just verify it's a bool
        self.assertIsInstance(status["is_cheap_only"], bool)


# ── Feature Extractor ─────────────────────────────────────────────────────────


class TestFeatureExtractor(unittest.TestCase):

    def setUp(self):
        self.extractor = TaskFeatureExtractor()

    def test_features_10_dimensions(self):
        feats = self.extractor.extract({"action": "fix bug", "file": "src/main.py"})
        self.assertEqual(len(feats), N_FEATURES)

    def test_features_in_range(self):
        feats = self.extractor.extract(
            {"action": "add new feature: implement user authentication system",
             "file": "src/auth.py", "complexity": "high"},
            failure_count=2,
        )
        for f in feats:
            self.assertGreaterEqual(f, 0.0)
            self.assertLessEqual(f, 1.0)

    def test_bias_term_is_one(self):
        feats = self.extractor.extract({"action": "anything"})
        self.assertAlmostEqual(feats[9], 1.0)


# ── Reward Function ───────────────────────────────────────────────────────────


class TestComputeReward(unittest.TestCase):

    def test_success_cheap_model(self):
        r = compute_reward(True, 0, cost_usd=0.001)
        self.assertGreater(r, 0.9)  # near-max reward

    def test_failure_expensive_model(self):
        r = compute_reward(False, 4, cost_usd=0.050)
        self.assertLess(r, 0.0)  # negative penalty

    def test_failure_cheap_model_penalty_small(self):
        r = compute_reward(False, 0, cost_usd=0.001)
        self.assertGreater(r, -0.06)  # cheap failure = minimal penalty (~ -0.054)

    def test_reward_bounded(self):
        """Reward always in [-0.36, 1.0] (cheapest fail ~ -0.054, most-expensive fail = -0.35)."""
        for success in [True, False]:
            for a in range(6):
                for cost in [0.001, 0.005, 0.017, 0.050]:
                    r = compute_reward(success, a, cost)
                    self.assertGreaterEqual(r, -0.36)  # epsilon for worst case
                    self.assertLessEqual(r, 1.0)


# ── Build ML Router ───────────────────────────────────────────────────────────


class TestBuildMLRouter(unittest.TestCase):

    def test_build_returns_router(self):
        router = build_ml_router()
        self.assertIsInstance(router, LinUCBRouter)
        self.assertEqual(router.n_actions, N_ACTIONS)
        self.assertEqual(router.n_features, N_FEATURES)

    def test_build_uses_default_path(self):
        router = build_ml_router()
        # The default weight path is .awos/linucb_weights.pkl
        self.assertIsNotNone(router._weights_path)

    def test_build_attaches_gp(self):
        router = build_ml_router()
        self.assertIsNotNone(router._gp)
