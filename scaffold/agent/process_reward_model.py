"""
process_reward_model.py — Process Reward Model for AWOS MCTS (P5).

Predicts P(branch leads to passing tests) without running pytest.
Used as a cheap oracle at intermediate MCTS tree nodes to prune bad branches
before wasting test execution time.

Architecture (CodePRM, ACL 2025):
    Input: (task_features[10], patch_features[20], structural[5]) = 35 dims
    Hidden: 256 → 64 (ReLU)
    Output: scalar in [0, 1] (Sigmoid)
    ~200K parameters. Trains in <30s CPU once 500+ MCTS traces exist.

Gate:
    Dormant (prm.ready == False) until 500+ MCTS traces exist in
    .awos/mcts_traces.jsonl and weights are trained.

Usage:
    prm = ProcessRewardModel()
    if prm.ready:
        score = prm.predict(task, edits)  # fast, no pytest
    else:
        score = 0.5  # neutral — falls back to real test execution
"""

from __future__ import annotations

import json
import logging
import pickle
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False


# ── MCTSTrace dataclass (stored in .awos/mcts_traces.jsonl) ──────────────────

@dataclass
class MCTSTrace:
    """
    One recorded MCTS episode. Used as training data for ProcessRewardModel.
    Logged by orchestrator after every MCTS search call.
    """
    task_id: str
    task_action: str
    task_features: List[float]
    nodes: List[dict]          # {depth, state, reward, is_terminal, static_ok}
    winning_path: List[int]    # indices of nodes on the winning path
    total_rollouts: int
    final_reward: float
    timestamp: float = field(default_factory=time.time)


# ── RewardStore — persists traces + exposes PRM training data ─────────────────

class RewardStore:
    """
    Append-only store for MCTSTrace objects.
    Writes to .awos/mcts_traces.jsonl.
    """

    BASE_DIR = ".awos"
    TRACES_FILE = "mcts_traces.jsonl"

    def __init__(self, base_dir: str = BASE_DIR) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._traces_path = self.base_dir / self.TRACES_FILE

    def log_mcts_trace(self, trace: MCTSTrace) -> None:
        """Append one MCTSTrace to the JSONL store."""
        with open(self._traces_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace)) + "\n")

    def get_mcts_traces(self, min_count: int = 500) -> List[MCTSTrace]:
        """
        Load all traces from the store.
        Returns [] if fewer than min_count traces exist (PRM not ready yet).
        """
        if not self._traces_path.exists():
            return []
        lines = self._traces_path.read_text(encoding="utf-8").splitlines()
        traces = []
        for line in lines:
            try:
                data = json.loads(line)
                traces.append(MCTSTrace(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        if len(traces) < min_count:
            return []
        return traces


class MCTSTraceStore(RewardStore):
    """Trace store alias — count/recent helpers for orchestrator + PRM gate."""

    def count(self) -> int:
        if not self._traces_path.exists():
            return 0
        return sum(1 for line in self._traces_path.read_text(encoding="utf-8").splitlines() if line.strip())

    def get_recent(self, n: int) -> List[MCTSTrace]:
        if not self._traces_path.exists():
            return []
        lines = [ln for ln in self._traces_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        traces: List[MCTSTrace] = []
        for line in lines[-n:]:
            try:
                traces.append(MCTSTrace(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return traces


# ── Process Reward Model ──────────────────────────────────────────────────────

class ProcessRewardModel:
    """
    Small 3-layer MLP. Predicts P(branch leads to passing tests).
    Input: 35-dim feature vector.
    Dormant (ready=False) until 500+ MCTS traces and weights file exist.
    """

    WEIGHTS_PATH = ".awos/prm_weights.pkl"
    MIN_TRACES = 500

    def __init__(self) -> None:
        self.ready = False
        self.W1 = self.b1 = self.W2 = self.b2 = self.W3 = self.b3 = None
        self._load_if_exists()

    # ── Public API ────────────────────────────────────────────────────────────

    def predict(self, task: dict, edits: list) -> float:
        """
        Predict P(edits lead to passing tests) in [0, 1].
        Returns 0.5 (neutral) if model not ready — triggers real test execution.
        """
        if not self.ready or not _NUMPY_AVAILABLE:
            return 0.5
        x = self._build_features(task, edits)
        return float(self._forward(x))

    def train(self, reward_store: RewardStore) -> bool:
        """
        Train on accumulated MCTS traces from reward_store.
        Returns True if training succeeded, False if not enough data.
        """
        if not _NUMPY_AVAILABLE:
            logger.warning("[PRM] numpy not available — cannot train")
            return False

        traces = reward_store.get_mcts_traces(min_count=self.MIN_TRACES)
        if not traces:
            logger.info("[PRM] fewer than %d traces — not training yet", self.MIN_TRACES)
            return False

        X, y = self._build_training_data(traces)
        self._init_weights(input_dim=X.shape[1])
        self._sgd_train(X, y, epochs=50, lr=0.001)
        self._save_weights()
        self.ready = True
        logger.info("[PRM] trained on %d samples from %d traces", len(X), len(traces))
        return True

    # ── Feature engineering ───────────────────────────────────────────────────

    def _build_features(self, task: dict, edits: list):
        """Build 35-dim feature vector: 10 task + 20 patch + 5 structural."""
        import numpy as np
        task_feats = self._encode_task(task)
        patch_feats = self._encode_patch(edits)
        struct_feats = np.array([
            min(len(edits) / 10, 1.0),
            min(sum(len(e.get("new_string", "")) for e in edits) / 500, 1.0),
            1.0, 0.0, 0.0,
        ])
        return np.concatenate([task_feats, patch_feats, struct_feats])

    def _encode_task(self, task: dict):
        """10-dim task encoding."""
        import numpy as np
        action = task.get("action", "").lower()
        return np.array([
            min(len(action.split()) / 50, 1.0),
            float("bug" in action or "fix" in action),
            float("refactor" in action),
            float("add" in action or "implement" in action),
            float("architect" in action or "design" in action),
            float("test" in action),
            min(task.get("complexity", 3) / 10, 1.0)
                if isinstance(task.get("complexity"), (int, float)) else 0.3,
            float(str(task.get("file", "")).startswith("scaffold/agent/")),
            min(task.get("attempt", 0) / 5, 1.0),
            1.0,
        ])

    def _encode_patch(self, edits: list):
        """20-dim patch encoding (heuristic, fast)."""
        import numpy as np
        if not edits:
            return np.zeros(20)
        old_total = sum(len(e.get("old_string", "")) for e in edits)
        new_total = sum(len(e.get("new_string", "")) for e in edits)
        all_new = " ".join(e.get("new_string", "") for e in edits).lower()
        feats = np.zeros(20)
        feats[0] = min(len(edits) / 10, 1.0)
        feats[1] = min(old_total / 500, 1.0)
        feats[2] = min(new_total / 500, 1.0)
        feats[3] = min((new_total - old_total) / 200, 1.0) if old_total else 0.5
        feats[4] = float("def " in all_new)
        feats[5] = float("class " in all_new)
        feats[6] = float("import " in all_new)
        feats[7] = float("return " in all_new)
        feats[8] = float("raise " in all_new or "except" in all_new)
        feats[9] = float("test" in all_new)
        feats[10] = float("self." in all_new)
        feats[11] = float(any(c in all_new for c in ("()", "[]", "{}")))
        return feats

    # ── MLP ───────────────────────────────────────────────────────────────────

    def _init_weights(self, input_dim: int = 35) -> None:
        import numpy as np
        def xavier(fan_in, fan_out):
            limit = np.sqrt(6 / (fan_in + fan_out))
            return np.random.uniform(-limit, limit, (fan_in, fan_out))
        self.W1 = xavier(input_dim, 256); self.b1 = np.zeros(256)
        self.W2 = xavier(256, 64);       self.b2 = np.zeros(64)
        self.W3 = xavier(64, 1);         self.b3 = np.zeros(1)

    def _forward(self, x) -> float:
        import numpy as np
        h1 = np.maximum(0, x @ self.W1 + self.b1)
        h2 = np.maximum(0, h1 @ self.W2 + self.b2)
        out = 1 / (1 + np.exp(-(h2 @ self.W3 + self.b3)))
        return float(out[0])

    def _sgd_train(self, X, y, epochs: int, lr: float) -> None:
        import numpy as np
        batch_size = 32
        n = len(X)
        for _ in range(epochs):
            idx = np.random.permutation(n)
            for i in range(0, n, batch_size):
                bx = X[idx[i:i + batch_size]]
                by = y[idx[i:i + batch_size]].reshape(-1, 1)
                h1 = np.maximum(0, bx @ self.W1 + self.b1)
                h2 = np.maximum(0, h1 @ self.W2 + self.b2)
                pred = 1 / (1 + np.exp(-(h2 @ self.W3 + self.b3)))
                lg = (pred - by) / len(bx)
                dW3 = h2.T @ lg
                dh2 = lg @ self.W3.T * (h2 > 0)
                dW2 = h1.T @ dh2
                dh1 = dh2 @ self.W2.T * (h1 > 0)
                dW1 = bx.T @ dh1
                self.W3 -= lr * dW3; self.W2 -= lr * dW2; self.W1 -= lr * dW1

    def _build_training_data(self, traces: List[MCTSTrace]):
        import numpy as np
        X_rows, y_rows = [], []
        for trace in traces:
            for node in trace.nodes:
                task = {"action": trace.task_action, "complexity": 5}
                edits = node.get("state", [])
                x = self._build_features(task, edits)
                label = float(
                    trace.final_reward >= 1.0
                    and node.get("depth", 0) in trace.winning_path
                )
                X_rows.append(x)
                y_rows.append(label)
        return np.array(X_rows), np.array(y_rows)

    def _save_weights(self) -> None:
        Path(self.WEIGHTS_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(self.WEIGHTS_PATH, "wb") as f:
            pickle.dump((self.W1, self.b1, self.W2, self.b2, self.W3, self.b3), f)
        self.ready = True

    def _load_if_exists(self) -> None:
        path = Path(self.WEIGHTS_PATH)
        if not path.exists():
            return
        try:
            with open(path, "rb") as f:
                self.W1, self.b1, self.W2, self.b2, self.W3, self.b3 = pickle.load(f)
            self.ready = True
            logger.info("[PRM] loaded weights from %s", path)
        except Exception as exc:
            logger.warning("[PRM] failed to load weights: %s", exc)
