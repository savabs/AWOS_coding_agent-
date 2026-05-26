"""
tests/test_process_reward_model.py — Tests for P5 ProcessRewardModel (10 tests).
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.process_reward_model import (
    ProcessRewardModel, RewardStore, MCTSTrace,
)


class TestPRMNotReady(unittest.TestCase):

    def test_predict_returns_neutral_when_not_ready(self):
        prm = ProcessRewardModel.__new__(ProcessRewardModel)
        prm.ready = False
        prm.W1 = prm.b1 = prm.W2 = prm.b2 = prm.W3 = prm.b3 = None
        result = prm.predict({"action": "fix bug"}, [])
        self.assertEqual(result, 0.5)


class TestPRMEncodings(unittest.TestCase):

    def setUp(self):
        self.prm = ProcessRewardModel.__new__(ProcessRewardModel)
        self.prm.ready = False
        self.prm.W1 = self.prm.b1 = None
        self.prm.W2 = self.prm.b2 = None
        self.prm.W3 = self.prm.b3 = None

    def test_encode_task_returns_10_dims(self):
        feats = self.prm._encode_task({"action": "fix bug in worker"})
        self.assertEqual(len(feats), 10)
        self.assertTrue(all(0 <= v <= 1 for v in feats))

    def test_encode_patch_returns_20_dims(self):
        edits = [{"old_string": "pass", "new_string": "return 1"}]
        feats = self.prm._encode_patch(edits)
        self.assertEqual(len(feats), 20)

    def test_encode_patch_empty_edits(self):
        feats = self.prm._encode_patch([])
        self.assertEqual(len(feats), 20)
        self.assertTrue(all(v == 0.0 for v in feats))


class TestPRMForward(unittest.TestCase):

    def test_forward_range_after_init(self):
        prm = ProcessRewardModel.__new__(ProcessRewardModel)
        prm.ready = False
        prm.W1 = prm.b1 = prm.W2 = prm.b2 = prm.W3 = prm.b3 = None
        prm._init_weights(input_dim=35)
        x = prm._build_features({"action": "fix", "complexity": 3}, [])
        result = prm._forward(x)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 1.0)


class TestRewardStore(unittest.TestCase):

    def test_get_traces_below_threshold_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            store = RewardStore(base_dir=td)
            # Only 10 traces → below MIN_TRACES=500
            for i in range(10):
                store.log_mcts_trace(MCTSTrace(
                    task_id=str(i), task_action="fix",
                    task_features=[0.0] * 10,
                    nodes=[{"depth": 0, "state": [], "reward": 0.5}],
                    winning_path=[0], total_rollouts=1, final_reward=0.5,
                ))
            result = store.get_mcts_traces(min_count=500)
            self.assertEqual(result, [])

    def test_log_and_retrieve_trace(self):
        with tempfile.TemporaryDirectory() as td:
            store = RewardStore(base_dir=td)
            trace = MCTSTrace(
                task_id="t1", task_action="add method",
                task_features=[0.1] * 10,
                nodes=[{"depth": 0, "state": [], "reward": 1.0}],
                winning_path=[0], total_rollouts=2, final_reward=1.0,
            )
            store.log_mcts_trace(trace)
            traces = store.get_mcts_traces(min_count=1)
            self.assertEqual(len(traces), 1)
            self.assertEqual(traces[0].task_id, "t1")

    def test_train_below_threshold_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            store = RewardStore(base_dir=td)
            prm = ProcessRewardModel.__new__(ProcessRewardModel)
            prm.ready = False
            prm.W1 = prm.b1 = prm.W2 = prm.b2 = prm.W3 = prm.b3 = None
            result = prm.train(store)
            self.assertFalse(result)


class TestPRMSaveLoad(unittest.TestCase):

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            weights_path = Path(td) / "prm_weights.pkl"
            prm = ProcessRewardModel.__new__(ProcessRewardModel)
            prm.ready = False
            prm.WEIGHTS_PATH = str(weights_path)
            prm._init_weights(input_dim=35)
            prm._save_weights()
            self.assertTrue(prm.ready)

            prm2 = ProcessRewardModel.__new__(ProcessRewardModel)
            prm2.ready = False
            prm2.W1 = prm2.b1 = prm2.W2 = prm2.b2 = prm2.W3 = prm2.b3 = None
            prm2.WEIGHTS_PATH = str(weights_path)
            prm2._load_if_exists()
            self.assertTrue(prm2.ready)

            x = prm._build_features({"action": "fix"}, [])
            self.assertAlmostEqual(prm._forward(x), prm2._forward(x), places=6)


if __name__ == "__main__":
    unittest.main()
