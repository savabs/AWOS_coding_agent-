"""LinUCB arm ids: trained and selected on the same ladder index.

Regression for the routing bug where record_outcome() trained arm
``level.value`` (Flash=1, Pro=6) while decide() selected by LADDER index
(Flash=0, Pro=1), the mask covered 2 of 6 arms (the rest counted as
allowed), and every orchestrator start re-replayed reward_store episodes on
top of the persisted weights.
"""

from __future__ import annotations

import pickle
from types import SimpleNamespace

import numpy as np
import pytest

from scaffold.agent.escalation_engine import (
    LADDER,
    EscalationEngine,
    EscalationLevel,
    arm_for_level,
    arm_for_level_value,
)
from scaffold.agent.ml_router import (
    WEIGHTS_FORMAT_VERSION,
    LinUCBRouter,
    TaskFeatureExtractor,
    build_ml_router,
)

FLASH = LADDER[0].level
PRO = LADDER[1].level
TASK = {"action": "fix the crash in the parser", "file": "src/parser.py", "complexity": "low"}


def _router(tmp_path, **kw):
    return build_ml_router(
        min_samples=kw.pop("min_samples", 1),
        weights_path=tmp_path / "linucb_weights.pkl",
        gp_path=tmp_path / "gp.pkl",
        **kw,
    )


def _theta(router, arm, x):
    return float((np.linalg.inv(router._A[arm]) @ router._b[arm]) @ x)


class _Store:
    def __init__(self, episodes):
        self.episodes = episodes
        self.calls = 0

    def get_prioritized(self, n):
        self.calls += 1
        return self.episodes[:n]


def _ep(action_id, reward=1.0):
    x = TaskFeatureExtractor().extract(TASK)
    return SimpleNamespace(action_id=action_id, features=list(x), reward=reward)


# ── arm scheme ────────────────────────────────────────────────────────────


def test_arms_are_ladder_indices(tmp_path):
    r = _router(tmp_path)
    assert r.n_actions == len(LADDER)
    assert arm_for_level(FLASH) == 0 and arm_for_level(PRO) == 1
    assert arm_for_level_value(FLASH.value) == 0
    assert arm_for_level_value(PRO.value) == 1
    # Levels that are not rungs map to no arm.
    assert arm_for_level(EscalationLevel.SONNET) is None
    assert arm_for_level_value(99) is None


def test_flash_success_raises_flash_arm_not_pro(tmp_path):
    r = _router(tmp_path)
    eng = EscalationEngine(ml_router=r)
    x = TaskFeatureExtractor().extract(TASK)
    before_flash, before_pro = _theta(r, 0, x), _theta(r, 1, x)
    eng.record_outcome("t1", FLASH, True, task=TASK, reward=1.0)
    assert r.total_updates() == 1
    assert _theta(r, 0, x) > before_flash
    assert _theta(r, 1, x) == pytest.approx(before_pro)


def test_pro_outcome_trains_pro_arm(tmp_path):
    r = _router(tmp_path)
    eng = EscalationEngine(ml_router=r)
    x = TaskFeatureExtractor().extract(TASK)
    eng.record_outcome("t2", PRO, True, task=TASK, reward=1.0)
    assert _theta(r, 1, x) > 0
    assert _theta(r, 0, x) == pytest.approx(0.0)


def test_off_ladder_level_does_not_train(tmp_path):
    r = _router(tmp_path)
    eng = EscalationEngine(ml_router=r)
    eng.record_outcome("t3", EscalationLevel.SONNET, True, task=TASK, reward=1.0)
    assert r.total_updates() == 0
    assert len(r._A) == len(LADDER)


def test_learned_flash_preference_is_what_decide_picks(tmp_path):
    r = _router(tmp_path, min_samples=5)
    r.alpha = 0.05
    eng = EscalationEngine(ml_router=r)
    for i in range(10):
        eng.record_outcome(f"f{i}", FLASH, True, task=TASK, reward=1.0)
        eng.record_outcome(f"p{i}", PRO, False, task=TASK, reward=-0.5)
    d = eng.decide(TASK, budget_remaining=100.0)
    assert "LinUCB" in d.reason
    assert d.spec.level == FLASH


# ── selection bounds ──────────────────────────────────────────────────────


def test_update_rejects_non_arm_ids(tmp_path):
    r = _router(tmp_path)
    x = TaskFeatureExtractor().extract(TASK)
    assert r.update(x, 6, 1.0) is False
    assert len(r._A) == len(LADDER)
    assert r.total_updates() == 0


def test_short_mask_blocks_uncovered_arms():
    # A generic 6-arm router given a 2-entry (ladder) mask must never pick 2..5.
    r = LinUCBRouter(n_actions=6, min_samples=1)
    x = TaskFeatureExtractor().extract(TASK)
    for _ in range(20):
        r.update(x, 5, 1.0, persist=False)  # make an uncovered arm look great
    for _ in range(20):
        assert r.select(x, budget_mask=[True, True]) in (0, 1)
    assert r.select(x, budget_mask=[False, True]) == 1


def test_selection_never_leaves_ladder(tmp_path):
    r = _router(tmp_path)
    rng = np.random.default_rng(0)
    for _ in range(50):
        x = rng.random(10)
        r.update(x, int(rng.integers(0, len(LADDER))), float(rng.normal()), persist=False)
        a = r.select(x, budget_mask=[True] * len(LADDER))
        assert 0 <= a < len(LADDER)
    eng = EscalationEngine(ml_router=r)
    for _ in range(20):
        d = eng.decide(TASK, budget_remaining=100.0)
        assert d.spec in LADDER


# ── persistence ───────────────────────────────────────────────────────────


def test_old_format_pickle_is_discarded(tmp_path):
    n = 10
    old = {  # v2, trained on level.value ids with 6 arms
        "format_version": 2,
        "A": [(np.eye(n) * 7).tolist() for _ in range(6)],
        "b": [np.ones(n).tolist() for _ in range(6)],
        "total_updates": 424,
        "alpha": 1.0,
        "n_features": n,
        "n_actions": 6,
    }
    (tmp_path / "linucb_weights.pkl").write_bytes(pickle.dumps(old))
    r = _router(tmp_path)
    assert r.loaded_from_disk is False
    assert r.total_updates() == 0
    assert len(r._A) == len(LADDER)
    assert all(np.allclose(A, np.eye(n)) for A in r._A)


def test_same_count_but_old_version_is_discarded(tmp_path):
    n = 10
    old = {"format_version": 2, "A": [np.eye(n).tolist()] * len(LADDER),
           "b": [np.ones(n).tolist()] * len(LADDER), "total_updates": 9,
           "n_features": n, "n_actions": len(LADDER)}
    (tmp_path / "linucb_weights.pkl").write_bytes(pickle.dumps(old))
    assert _router(tmp_path).total_updates() == 0


def test_v3_roundtrip_loads(tmp_path):
    r1 = _router(tmp_path)
    x = TaskFeatureExtractor().extract(TASK)
    r1.update(x, 1, 1.0)  # saved immediately
    payload = pickle.loads((tmp_path / "linucb_weights.pkl").read_bytes())
    assert payload["format_version"] == WEIGHTS_FORMAT_VERSION
    assert payload["arm_names"] == [s.model_id for s in LADDER]
    r2 = _router(tmp_path)
    assert r2.loaded_from_disk is True
    assert r2.total_updates() == 1
    assert np.allclose(r2._b[1], r1._b[1])


# ── warm start ────────────────────────────────────────────────────────────


def test_warm_start_maps_level_values_to_arms(tmp_path):
    r = _router(tmp_path)
    x = TaskFeatureExtractor().extract(TASK)
    # reward_store keeps level.value: Flash=1, Pro=6; 0/2/3 are dead models.
    store = _Store([_ep(FLASH.value), _ep(PRO.value), _ep(0), _ep(3)])
    assert r.warm_start(store, max_episodes=50) == 2
    assert _theta(r, 0, x) > 0 and _theta(r, 1, x) > 0
    assert len(r._A) == len(LADDER)


def test_no_replay_when_weights_exist(tmp_path):
    r1 = _router(tmp_path)
    store = _Store([_ep(FLASH.value) for _ in range(5)])
    assert r1.warm_start(store) == 5  # cold: primes and persists
    r2 = _router(tmp_path)
    assert r2.loaded_from_disk is True
    assert r2.total_updates() == 5
    assert r2.warm_start(store) == 0
    assert r2.total_updates() == 5  # not double counted
    assert store.calls == 1


def test_gp_fit_uses_arm_ids(tmp_path):
    pytest.importorskip("sklearn")
    r = _router(tmp_path)
    eps = [_ep(PRO.value, reward=float(i % 2)) for i in range(6)]
    for i, e in enumerate(eps):
        e.features[0] = i / 10.0
    r._gp.fit(eps + [_ep(3) for _ in range(6)])
    assert set(r._gp._models) == {1}
