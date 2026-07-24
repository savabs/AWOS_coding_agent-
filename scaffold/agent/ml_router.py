"""
ML Meta-Controller for AWOS — pure mathematical self-improvement.

No LLMs. No API calls. No growing context.
Real weight-based learning from task outcomes.

Components
----------
TaskFeatureExtractor
    Converts an AWOS task dict into a fixed 10-dimensional feature vector.
    All features normalized to [0, 1].

LinUCBRouter  (PRIMARY — Phase 1)
    Contextual Bandit using Linear UCB algorithm.
    Reference: Li et al., 2010. "A Contextual-Bandit Approach to
    Personalized News Article Recommendation." WWW 2010.

    Learns: which model tier works best for which task type.
    Updates: online, after every completed task (no batch needed).
    Weights: ~500 floats total. Stored in .awos/linucb_weights.pkl.

GPWorldModel  (SECONDARY — Phase 2 stub)
    Gaussian Process regression: P(success | features, action).
    Enables Thompson Sampling for principled exploration.
    Activates automatically when >= 50 episodes accumulated.

Action space (matches EscalationLevel in escalation_engine.py):
    0 = GEMINI_FLASH
    1 = DEEPSEEK
    2 = OPENAI (GPT-4o-mini)
    3 = HAIKU
    4 = SONNET
"""

from __future__ import annotations

import logging
import pickle
import re
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

N_FEATURES = 10
N_ACTIONS  = 6
ACTION_NAMES = ["gemini_flash", "deepseek", "openai", "haiku", "sonnet", "openrouter"]

_DEFAULT_WEIGHTS_PATH = Path(".awos") / "linucb_weights.pkl"
_DEFAULT_GP_PATH      = Path(".awos") / "gp_model.pkl"

# Keyword sets for feature extraction
_BUG_FIX_KW    = {"fix", "bug", "error", "patch", "broken", "crash", "typo", "wrong", "issue"}
_REFACTOR_KW   = {"refactor", "clean", "rename", "reorganize", "simplify", "move", "restructure"}
_NEW_FEAT_KW   = {"add", "create", "new", "implement", "build", "introduce", "write"}
_ARCH_KW       = {"architecture", "design", "system", "infrastructure", "pipeline", "framework"}
_TEST_KW       = {"test", "spec", "assert", "coverage", "unittest", "pytest"}


# ── Feature Extractor ──────────────────────────────────────────────────────

class TaskFeatureExtractor:
    """
    Converts an AWOS task dict → fixed 10-dim numpy feature vector.

    Feature index map:
        0  action_length_norm   word count / 50, clipped [0, 1]
        1  is_bug_fix           1.0 if action contains bug/fix keywords
        2  is_refactor          1.0 if action contains refactor keywords
        3  is_new_feature       1.0 if action contains creation keywords
        4  is_architecture      1.0 if action contains architecture keywords
        5  has_tests            1.0 if "test" in action or file path
        6  complexity_encoded   low=0.2, medium=0.5, high=0.8
        7  file_is_core         1.0 if file not under test/docs/readme
        8  failure_count_norm   failure_count / 3, clipped [0, 1]
        9  bias                 always 1.0 (intercept term)
    """

    def extract(self, task: dict, failure_count: int = 0) -> np.ndarray:
        action    = str(task.get("action", "")).lower()
        file_path = str(task.get("file", "")).lower()
        complexity = str(task.get("complexity", "medium")).lower()

        words = re.findall(r"\w+", action)
        word_set = set(words)

        features = np.zeros(N_FEATURES, dtype=np.float64)
        features[0] = min(len(words) / 50.0, 1.0)
        features[1] = 1.0 if word_set & _BUG_FIX_KW else 0.0
        features[2] = 1.0 if word_set & _REFACTOR_KW else 0.0
        features[3] = 1.0 if word_set & _NEW_FEAT_KW else 0.0
        features[4] = 1.0 if word_set & _ARCH_KW else 0.0
        features[5] = 1.0 if (word_set & _TEST_KW or "test" in file_path) else 0.0
        features[6] = {"low": 0.2, "medium": 0.5, "high": 0.8}.get(complexity, 0.5)
        features[7] = 0.0 if any(x in file_path for x in ("test", "doc", "readme", "spec")) else 1.0
        features[8] = min(failure_count / 3.0, 1.0)
        features[9] = 1.0  # bias term

        return features


# ── LinUCB Router ──────────────────────────────────────────────────────────

class LinUCBRouter:
    """
    Linear Upper Confidence Bound contextual bandit.

    Learns a linear reward model per action:
        E[reward | x, a] ≈ θ_a^T · x

    Selects actions using UCB:
        UCB_a(x) = θ_a^T · x  +  α · √(x^T · A_a^{-1} · x)
                   ^^^^^^^^^^^     ^^^^^^^^^^^^^^^^^^^^^^^^^^^
                   exploitation        exploration bonus
                   (learned reward)    (high = under-explored)

    Parameters
    ----------
    n_features : int
        Dimension of feature vector (default 10).
    n_actions : int
        Number of model tiers (default 5).
    alpha : float
        Exploration-exploitation trade-off.
        Higher → more exploration. Recommended: 0.5–2.0.
    min_samples : int
        Number of updates before this router overrides heuristics.
        Guards against cold-start degradation.
    budget_mask : list[bool] | None
        If given, masks out actions the budget can't afford.
    """

    def __init__(
        self,
        n_features: int = N_FEATURES,
        n_actions: int = N_ACTIONS,
        alpha: float = 1.0,
        min_samples: int = 20,
        weights_path: Path | None = None,
        gp_model: "GPWorldModel | None" = None,
    ) -> None:
        self.n_features  = n_features
        self.n_actions   = n_actions
        self.alpha       = alpha
        self.min_samples = min_samples
        self._weights_path = weights_path  # None = no persistence
        self._total_updates = 0
        self._gp: GPWorldModel | None = gp_model

        # Per-action accumulators (LinUCB disjoint model)
        # A_a: d×d matrix (feature covariance), init = identity
        # b_a: d-vector   (reward-weighted features), init = zeros
        self._A: list[np.ndarray] = [np.eye(n_features) for _ in range(n_actions)]
        self._b: list[np.ndarray] = [np.zeros(n_features) for _ in range(n_actions)]

        # Only load persisted weights when a path is explicitly provided
        if weights_path is not None:
            self._load()

    # ── Selection ────────────────────────────────────────────────────────

    def attach_gp(self, gp: "GPWorldModel") -> None:
        """Attach a fitted GPWorldModel to enable Thompson Sampling."""
        self._gp = gp

    def select(
        self,
        features: np.ndarray,
        budget_mask: list[bool] | None = None,
    ) -> int:
        """
        Select action. Policy priority:
          1. Thompson Sampling via GPWorldModel if it is ready (>= 50 episodes).
          2. LinUCB UCB otherwise.

        Args:
            features:     10-dim feature vector from TaskFeatureExtractor.
            budget_mask:  Boolean list length n_actions. False = action unavailable.

        Returns:
            action_id (int 0–n_actions).
        """
        x = np.asarray(features, dtype=np.float64)

        # ── Thompson Sampling (Phase 2) ──────────────────────────────────
        if self._gp is not None and self._gp.is_ready():
            sampled = []
            for a in range(self.n_actions):
                if budget_mask is not None and not budget_mask[a]:
                    sampled.append(-np.inf)
                    continue
                mu, sigma = self._gp.predict(x, a)
                sampled.append(float(np.random.normal(mu, max(sigma, 1e-6))))
            chosen = int(np.argmax(sampled))
            logger.debug(
                "[gp_ts] Thompson samples: %s → chose action=%d (%s)",
                np.round(sampled, 3), chosen, ACTION_NAMES[chosen],
            )
            return chosen

        # ── LinUCB (Phase 1) ─────────────────────────────────────────────
        ucb_scores = np.zeros(self.n_actions)

        for a in range(self.n_actions):
            if budget_mask is not None and not budget_mask[a]:
                ucb_scores[a] = -np.inf
                continue

            A_inv = np.linalg.inv(self._A[a])
            theta = A_inv @ self._b[a]

            exploitation = float(theta @ x)
            exploration  = self.alpha * float(np.sqrt(x @ A_inv @ x))
            ucb_scores[a] = exploitation + exploration

        chosen = int(np.argmax(ucb_scores))
        logger.debug(
            "[linucb] UCB scores: %s → chose action=%d (%s)",
            np.round(ucb_scores, 3),
            chosen,
            ACTION_NAMES[chosen],
        )
        return chosen

    # ── Update ───────────────────────────────────────────────────────────

    def update(self, features: np.ndarray, action_id: int, reward: float) -> None:
        """
        Online update after observing reward for (features, action_id).

        A_a += x · x^T
        b_a += r · x

        This is the exact LinUCB update from Li et al. 2010.
        """
        x = np.asarray(features, dtype=np.float64)
        a = int(action_id)

        # Defensive: action_id may exceed n_actions (e.g. a new
        # EscalationLevel was added without recreating the router).
        # Extend the per-action arrays rather than crashing.
        while a >= len(self._A):
            self._A.append(np.eye(self.n_features))
            self._b.append(np.zeros(self.n_features))

        self._A[a] += np.outer(x, x)
        self._b[a] += reward * x
        self._total_updates += 1

        if self._total_updates % 10 == 0:
            self._save()
            logger.debug("[linucb] saved weights after %d updates", self._total_updates)

    # ── Warm-Start ───────────────────────────────────────────────────────

    def warm_start(self, reward_store, max_episodes: int = 100) -> int:
        """Batch-replay historical episodes from RewardStore to prime LinUCB weights.

        Skips ReplayGate — these are trusted historical episodes that already
        passed gating when they were originally recorded. Uses prioritized
        experience replay (PER) to maximize learning per episode.

        Args:
            reward_store: RewardStore instance with recorded episodes.
            max_episodes: Maximum number of episodes to replay (default 100).

        Returns:
            Number of episodes successfully replayed.
        """
        episodes = reward_store.get_prioritized(max_episodes)
        if not episodes:
            logger.info("[linucb] warm_start: no episodes in RewardStore")
            return 0

        replayed = 0
        for ep in episodes:
            action_id = getattr(ep, "action_id", -1)
            if action_id < 0 or action_id >= self.n_actions:
                logger.debug(
                    "[linucb] warm_start: skipping action_id=%d (out of 0..%d)",
                    action_id, self.n_actions - 1,
                )
                continue

            features = np.asarray(getattr(ep, "features", []), dtype=np.float64)
            if features.shape[0] != self.n_features:
                logger.debug(
                    "[linucb] warm_start: skipping malformed features (dim=%d, expected %d)",
                    features.shape[0], self.n_features,
                )
                continue

            reward = float(getattr(ep, "reward", 0.0))
            self.update(features, action_id, reward)
            replayed += 1

        logger.info(
            "[linucb] warm_start: replayed %d episodes — total_updates=%d, is_ready=%s",
            replayed, self._total_updates, self.is_ready(),
        )
        return replayed

    # ── State ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        """True once enough data accumulated to trust LinUCB over heuristics."""
        return self._total_updates >= self.min_samples

    def total_updates(self) -> int:
        return self._total_updates

    def predict_success(self, features: np.ndarray) -> float:
        """
        Return an estimated success probability [0,1] for the given feature vector.
        Uses GP if available and fitted, else falls back to the best LinUCB UCB score.
        """
        if self._gp is not None and self._gp.is_fitted():
            try:
                return float(self._gp.predict_proba(features.reshape(1, -1))[0])
            except Exception:
                pass
        if not self.is_ready():
            return 0.5
        scores = []
        for a in range(self.n_actions):
            A_inv = np.linalg.inv(self._A[a])
            theta = A_inv @ self._b[a]
            scores.append(float(theta @ features))
        best = max(scores)
        return min(max((best + 1.0) / 2.0, 0.0), 1.0)

    def learned_weights(self) -> dict[str, np.ndarray]:
        """
        Return the learned θ_a vectors for each action.
        These tell you: "how much does each feature contribute to expected reward
        when using model X?"
        """
        weights = {}
        for a in range(self.n_actions):
            theta = np.linalg.inv(self._A[a]) @ self._b[a]
            weights[ACTION_NAMES[a]] = np.round(theta, 4)
        return weights

    def summary(self) -> dict[str, Any]:
        """Human-readable summary of learned policy."""
        feature_names = [
            "action_length", "is_bug_fix", "is_refactor", "is_new_feature",
            "is_architecture", "has_tests", "complexity", "file_is_core",
            "failure_count", "bias",
        ]
        weights = self.learned_weights()
        result: dict[str, Any] = {
            "total_updates": self._total_updates,
            "is_ready": self.is_ready(),
            "learned_weights": {},
        }
        for action_name, theta in weights.items():
            result["learned_weights"][action_name] = {
                fname: float(w) for fname, w in zip(feature_names, theta)
            }
        return result

    # ── Persistence ───────────────────────────────────────────────────────

    def _save(self) -> None:
        if self._weights_path is None:
            return
        self._weights_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": 2,
            "A": [a.tolist() for a in self._A],
            "b": [b.tolist() for b in self._b],
            "total_updates": self._total_updates,
            "alpha": self.alpha,
            "n_features": self.n_features,
            "n_actions": self.n_actions,
        }
        with self._weights_path.open("wb") as f:
            pickle.dump(payload, f, protocol=4)

    def _load(self) -> None:
        """Load persisted weights. Handles n_actions changes by padding/truncating.

        V2 format (format_version ≥ 2): pads new actions with identity matrices
        so learning from historical data is never lost when action space grows.
        V1 format (no format_version key): only loads on exact n_actions match.
        """
        if self._weights_path is None or not self._weights_path.exists():
            return
        try:
            with self._weights_path.open("rb") as f:
                payload = pickle.load(f)
        except Exception as exc:
            logger.warning("[linucb] could not read weights file: %s — starting fresh", exc)
            return

        fmt_ver = payload.get("format_version", 1)
        stored_actions = payload.get("n_actions", 0)
        stored_features = payload.get("n_features", 0)

        # Feature dimension mismatch — incompatible, start fresh
        if stored_features != self.n_features:
            logger.warning(
                "[linucb] feature dimension mismatch stored=%d current=%d — starting fresh",
                stored_features, self.n_features,
            )
            return

        # ── Exact match — direct load ──────────────────────────────────
        if stored_actions == self.n_actions:
            self._A = [np.array(a) for a in payload["A"]]
            self._b = [np.array(b) for b in payload["b"]]
            self._total_updates = payload.get("total_updates", 0)
            logger.info(
                "[linucb] loaded v%d: %d updates, %d actions",
                fmt_ver, self._total_updates, self.n_actions,
            )
            return

        # ── Action space mismatch (v1) — discard (can't pad v1 safely) ──
        if fmt_ver < 2:
            logger.warning(
                "[linucb] v1 weights: stored=%d actions current=%d — starting fresh "
                "(upgrade to v2 to preserve learning across action space changes)",
                stored_actions, self.n_actions,
            )
            return

        # ── Action space mismatch (v2+) — pad or truncate ──────────────
        stored_A = [np.array(a) for a in payload["A"]]
        stored_b = [np.array(b) for b in payload["b"]]

        if stored_actions < self.n_actions:
            # GROW: new actions start with identity (explore freely)
            self._A = stored_A + [
                np.eye(self.n_features) for _ in range(self.n_actions - stored_actions)
            ]
            self._b = stored_b + [
                np.zeros(self.n_features) for _ in range(self.n_actions - stored_actions)
            ]
            self._total_updates = payload.get("total_updates", 0)
            logger.info(
                "[linucb] loaded v%d: %d updates, padded %d→%d actions (new actions explore freely)",
                fmt_ver, self._total_updates, stored_actions, self.n_actions,
            )
        else:
            # SHRINK: drop actions that no longer exist
            self._A = stored_A[:self.n_actions]
            self._b = stored_b[:self.n_actions]
            self._total_updates = payload.get("total_updates", 0)
            logger.info(
                "[linucb] loaded v%d: %d updates, truncated %d→%d actions (dropped %d)",
                fmt_ver, self._total_updates, stored_actions, self.n_actions,
                stored_actions - self.n_actions,
            )

    def save_now(self) -> None:
        """Force an immediate save (call this on shutdown)."""
        self._save()


# ── GP World Model (Phase 2) ───────────────────────────────────────────────

class GPWorldModel:
    """
    Gaussian Process world model: predicts P(success | features, action).

    Enables Thompson Sampling:
        sample μ̃_a ~ N(μ_a, σ²_a) for each action
        choose a* = argmax μ̃_a

    This gives principled exploration: uncertain → explore, confident → exploit.

    Phase 2: activates automatically once >= 50 episodes in RewardStore.
    Currently a stub — fit() and predict() are implemented but not wired in.

    Requires: scikit-learn
    """

    MIN_EPISODES_TO_FIT = 50

    def __init__(self, gp_path: Path | None = None) -> None:
        self._gp_path = gp_path or _DEFAULT_GP_PATH
        self._models: dict[int, Any] = {}   # action_id → fitted GaussianProcessRegressor
        self._is_fitted = False

    def fit(self, episodes: list) -> None:
        """
        Fit one GP per action on accumulated episodes.

        Args:
            episodes: list of Episode objects from RewardStore.
        """
        try:
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import RBF, ConstantKernel
        except ImportError:
            logger.warning("[gp_world_model] scikit-learn not installed — GP skipped")
            return

        from collections import defaultdict
        by_action: dict[int, tuple[list, list]] = defaultdict(lambda: ([], []))

        for ep in episodes:
            by_action[ep.action_id][0].append(ep.features)
            by_action[ep.action_id][1].append(ep.reward)

        for a_id, (X_list, y_list) in by_action.items():
            if len(X_list) < 5:
                continue
            X = np.array(X_list)
            y = np.array(y_list)
            kernel = ConstantKernel(1.0) * RBF(length_scale=1.0)
            gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=2, normalize_y=True)
            gp.fit(X, y)
            self._models[a_id] = gp

        if self._models:
            self._is_fitted = True
            logger.info("[gp_world_model] fitted %d action GPs", len(self._models))

    def predict(self, features: np.ndarray, action_id: int) -> tuple[float, float]:
        """
        Returns (mean, std) of predicted reward for (features, action).
        std is the uncertainty — high std → explore this action.
        """
        if action_id not in self._models:
            return 0.5, 1.0   # unknown → high uncertainty
        x = np.asarray(features).reshape(1, -1)
        mean, std = self._models[action_id].predict(x, return_std=True)
        return float(mean[0]), float(std[0])

    def sample_action(self, features: np.ndarray) -> int:
        """
        Thompson Sampling: sample from posterior, act greedily on sample.
        Returns action_id with highest sampled reward.
        """
        x = np.asarray(features)
        sampled = []
        for a in range(N_ACTIONS):
            mu, sigma = self.predict(x, a)
            sampled.append(np.random.normal(mu, sigma))
        return int(np.argmax(sampled))

    def is_ready(self) -> bool:
        return self._is_fitted and bool(self._models)


# ── Convenience factory ────────────────────────────────────────────────────

def build_ml_router(
    alpha: float = 1.0,
    min_samples: int = 20,
    weights_path: Path | None = None,
    gp_path: Path | None = None,
) -> LinUCBRouter:
    """
    Build and return a LinUCBRouter with an attached GPWorldModel stub.
    GPWorldModel activates automatically once its fit() is called with >= 50 episodes.
    Call this once at AWOS startup.
    """
    gp = GPWorldModel(gp_path=gp_path)
    # Pass the default path explicitly so production router loads persisted weights
    effective_path = weights_path if weights_path is not None else _DEFAULT_WEIGHTS_PATH
    return LinUCBRouter(
        alpha=alpha,
        min_samples=min_samples,
        weights_path=effective_path,
        gp_model=gp,
    )
