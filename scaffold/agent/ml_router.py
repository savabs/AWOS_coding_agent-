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

Action space (arm ids):
    An arm id is the INDEX of a rung in escalation_engine.LADDER — the same
    index decide() selects by and record_outcome() trains on. It is NOT
    EscalationLevel.value (those are sparse, e.g. Flash=1, Pro=6). The
    production router (build_ml_router) is sized to the ladder, and maps
    reward_store episodes (which store level.value) through
    escalation_engine.arm_for_level_value before replaying or fitting them.
"""

from __future__ import annotations

import logging
import pickle
import re
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

N_FEATURES = 10
N_ACTIONS  = 6
ACTION_NAMES = ["gemini_flash", "deepseek", "openai", "haiku", "sonnet", "openrouter"]

# Persisted-weights format. v3 = arms are ladder indices (see module docstring).
# Anything older was trained on EscalationLevel.value ids and is discarded.
WEIGHTS_FORMAT_VERSION = 3
ARM_SCHEME = "ladder_index"

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
        action_names: list[str] | None = None,
        action_map: Optional[Callable[[int], Optional[int]]] = None,
    ) -> None:
        self.n_features  = n_features
        self.n_actions   = n_actions
        if action_names is not None and len(action_names) != n_actions:
            raise ValueError("action_names must have n_actions entries")
        self.action_names = list(action_names) if action_names is not None else [
            ACTION_NAMES[a] if a < len(ACTION_NAMES) else f"action_{a}"
            for a in range(n_actions)
        ]
        # Maps a stored episode action id (EscalationLevel.value in
        # reward_store) to an arm id; None = identity. Returns None for ids
        # that are not an arm (dead/removed rungs) so they are skipped.
        self._action_map = action_map
        self.loaded_from_disk = False   # True once _load() accepted a file
        self.alpha       = alpha
        self.min_samples = min_samples
        self._weights_path = weights_path  # None = no persistence
        self._total_updates = 0
        self._gp: GPWorldModel | None = gp_model
        if gp_model is not None and action_map is not None:
            gp_model._action_map = action_map

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
        if self._action_map is not None and getattr(gp, "_action_map", None) is None:
            gp._action_map = self._action_map

    def _blocked(self, a: int, budget_mask: list[bool] | None) -> bool:
        """An arm is selectable only if the mask names it and allows it.

        A mask shorter than n_actions blocks the arms it does not cover —
        an arm with no mask entry has no rung to run, so it must never win.
        """
        if budget_mask is None:
            return False
        return a >= len(budget_mask) or not budget_mask[a]

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
            action_id (int in 0..n_actions-1, never a masked-out arm unless
            every arm is masked, in which case 0).
        """
        x = np.asarray(features, dtype=np.float64)
        if budget_mask is not None and all(
            self._blocked(a, budget_mask) for a in range(self.n_actions)
        ):
            return 0

        # ── Thompson Sampling (Phase 2) ──────────────────────────────────
        if self._gp is not None and self._gp.is_ready():
            sampled = []
            for a in range(self.n_actions):
                if self._blocked(a, budget_mask):
                    sampled.append(-np.inf)
                    continue
                mu, sigma = self._gp.predict(x, a)
                sampled.append(float(np.random.normal(mu, max(sigma, 1e-6))))
            chosen = int(np.argmax(sampled))
            logger.debug(
                "[gp_ts] Thompson samples: %s → chose action=%d (%s)",
                np.round(sampled, 3), chosen, self.action_names[chosen],
            )
            return chosen

        # ── LinUCB (Phase 1) ─────────────────────────────────────────────
        ucb_scores = np.zeros(self.n_actions)

        for a in range(self.n_actions):
            if self._blocked(a, budget_mask):
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
            self.action_names[chosen],
        )
        return chosen

    # ── Update ───────────────────────────────────────────────────────────

    def update(
        self, features: np.ndarray, action_id: int, reward: float, persist: bool = True,
    ) -> bool:
        """
        Online update after observing reward for (features, action_id).

        A_a += x · x^T
        b_a += r · x

        This is the exact LinUCB update from Li et al. 2010.

        action_id must be an arm id (0..n_actions-1). An out-of-range id is
        refused (logged, returns False) — it used to silently grow phantom
        arms that select() then treated as allowed.
        """
        x = np.asarray(features, dtype=np.float64)
        a = int(action_id)
        if not 0 <= a < self.n_actions:
            logger.warning(
                "[linucb] update ignored: action_id=%d is not an arm (0..%d)",
                a, self.n_actions - 1,
            )
            return False

        self._A[a] += np.outer(x, x)
        self._b[a] += reward * x
        self._total_updates += 1

        # Weights are tiny (n_actions x 110 floats): save every update so a
        # short run never loses what it learned (warm-start no longer
        # re-replays history on top of persisted weights).
        if persist:
            self._save()
        return True

    # ── Warm-Start ───────────────────────────────────────────────────────

    def warm_start(self, reward_store, max_episodes: int = 100) -> int:
        """Batch-replay historical episodes from RewardStore to prime LinUCB weights.

        Only primes a COLD router: if persisted weights were loaded, those
        episodes are already in them and replaying would count each twice,
        so this returns 0 without touching the weights. Episode action ids
        are mapped to arm ids through ``action_map`` (if set); episodes whose
        id is not an arm are skipped.

        Skips ReplayGate — these are trusted historical episodes that already
        passed gating when they were originally recorded. Uses prioritized
        experience replay (PER) to maximize learning per episode.

        Args:
            reward_store: RewardStore instance with recorded episodes.
            max_episodes: Maximum number of episodes to replay (default 100).

        Returns:
            Number of episodes successfully replayed.
        """
        if self.loaded_from_disk:
            logger.info(
                "[linucb] warm_start skipped: persisted weights loaded (%d updates)",
                self._total_updates,
            )
            return 0
        episodes = reward_store.get_prioritized(max_episodes)
        if not episodes:
            logger.info("[linucb] warm_start: no episodes in RewardStore")
            return 0

        replayed = 0
        for ep in episodes:
            raw_id = getattr(ep, "action_id", -1)
            action_id = self._action_map(raw_id) if self._action_map else raw_id
            if action_id is None or action_id < 0 or action_id >= self.n_actions:
                logger.debug(
                    "[linucb] warm_start: skipping action_id=%d (out of 0..%d)",
                    raw_id, self.n_actions - 1,
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
            self.update(features, action_id, reward, persist=False)
            replayed += 1
        if replayed:
            self._save()

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
            weights[self.action_names[a]] = np.round(theta, 4)
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
            "format_version": WEIGHTS_FORMAT_VERSION,
            "arm_scheme": ARM_SCHEME,
            "arm_names": list(self.action_names),
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
        """Load persisted weights only if they use the current arm scheme.

        Only v3 files (arms = ladder indices) with the same feature dimension
        and the same arm count are loaded; when the file names its arms, the
        names must match too. Anything else — v1/v2 files trained on
        EscalationLevel.value ids, a resized ladder, a renamed rung — is
        discarded: its arm i does not mean our arm i, so reusing it would
        credit one model with another's outcomes. Discarding leaves the
        router cold, so warm_start() re-primes it from the reward store.
        """
        if self._weights_path is None or not self._weights_path.exists():
            return
        try:
            with self._weights_path.open("rb") as f:
                payload = pickle.load(f)
            if not isinstance(payload, dict):
                raise ValueError("payload is not a dict")
        except Exception as exc:
            logger.warning("[linucb] could not read weights file: %s — starting fresh", exc)
            return

        fmt_ver = payload.get("format_version", 1)
        stored_actions = payload.get("n_actions", 0)
        stored_features = payload.get("n_features", 0)
        stored_names = payload.get("arm_names")

        reason = None
        if fmt_ver < WEIGHTS_FORMAT_VERSION or payload.get("arm_scheme") != ARM_SCHEME:
            reason = f"format v{fmt_ver} predates ladder-index arms"
        elif stored_features != self.n_features:
            reason = f"feature dim {stored_features} != {self.n_features}"
        elif stored_actions != self.n_actions:
            reason = f"{stored_actions} arms != {self.n_actions}"
        elif stored_names is not None and list(stored_names) != self.action_names:
            reason = f"arm names {stored_names} != {self.action_names}"
        if reason is not None:
            logger.warning("[linucb] discarding persisted weights (%s) — starting fresh", reason)
            return

        self._A = [np.array(a, dtype=np.float64) for a in payload["A"]]
        self._b = [np.array(b, dtype=np.float64) for b in payload["b"]]
        self._total_updates = payload.get("total_updates", 0)
        self.loaded_from_disk = True
        logger.info(
            "[linucb] loaded v%d: %d updates, %d arms", fmt_ver, self._total_updates, self.n_actions,
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
        self._models: dict[int, Any] = {}   # arm id → fitted GaussianProcessRegressor
        self._is_fitted = False
        # Episode action id (EscalationLevel.value) → arm id; set by the router.
        self._action_map: Optional[Callable[[int], Optional[int]]] = None

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
            arm = self._action_map(ep.action_id) if self._action_map else ep.action_id
            if arm is None:
                continue  # not a current rung (dead/removed model)
            by_action[arm][0].append(ep.features)
            by_action[arm][1].append(ep.reward)

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
    try:
        from .escalation_engine import LADDER, arm_for_level_value
    except ImportError:
        from escalation_engine import LADDER, arm_for_level_value  # type: ignore

    gp = GPWorldModel(gp_path=gp_path)
    # Pass the default path explicitly so production router loads persisted weights
    effective_path = weights_path if weights_path is not None else _DEFAULT_WEIGHTS_PATH
    # One arm per ladder rung, indexed exactly as EscalationEngine.decide()
    # selects and record_outcome() trains.
    return LinUCBRouter(
        n_actions=len(LADDER),
        alpha=alpha,
        min_samples=min_samples,
        weights_path=effective_path,
        gp_model=gp,
        action_names=[spec.model_id for spec in LADDER],
        action_map=arm_for_level_value,
    )
