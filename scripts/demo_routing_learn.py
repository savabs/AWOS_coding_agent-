#!/usr/bin/env python3
"""
demo_routing_learn.py — Live proof for routing learning system repair.

Simulates 200+ routing decisions from AWOS history, verifies:
  1. LinUCB weight persistence across n_actions changes (pad/truncate)
  2. ReplayGate adaptive threshold relaxing as data accumulates
  3. warm_start from RewardStore primes LinUCB weights
  4. routing_status() returns correct observability snapshot
  5. Weights file uses format_version=2

Usage:
    python3 scripts/demo_routing_learn.py          # full demo
    python3 scripts/demo_routing_learn.py --verify # exit 0/1
"""

import logging
import os
import pickle
import random
import shutil
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from scaffold.agent.ml_router import (
    N_ACTIONS,
    N_FEATURES,
    ACTION_NAMES,
    LinUCBRouter,
    TaskFeatureExtractor,
)
from scaffold.agent.reward_store import (
    ReplayGate,
    RewardStore,
)
from scaffold.agent.escalation_engine import (
    EscalationEngine,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-5s %(name)s %(message)s",
)
logger = logging.getLogger("demo_routing_learn")

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
DIM = "\033[2m{}\033[0m"


def header(msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = PASS if ok else FAIL
    suffix = f"  {DIM.format(detail)}" if detail else ""
    print(f"  {mark} {label}{suffix}")
    return ok


def main(args: list[str]) -> int:
    all_ok = True

    # ── Setup: temp workspace ────────────────────────────────────────────
    workspace = Path(tempfile.mkdtemp(prefix="awos_demo_routing_"))
    awos_dir = workspace / ".awos"
    awos_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Workspace: %s", workspace)

    try:
        # ══════════════════════════════════════════════════════════════════
        header("1. Seed RewardStore with simulated AWOS history")

        store_path = awos_dir / "reward_store.jsonl"
        store = RewardStore(path=store_path)
        extractor = TaskFeatureExtractor()

        task_templates = [
            {"action": "fix bug in authentication module", "file": "src/auth.py", "complexity": "medium"},
            {"action": "add new feature: implement user dashboard", "file": "src/dashboard.py", "complexity": "high"},
            {"action": "refactor database connection pooling", "file": "src/db/pool.py", "complexity": "high"},
            {"action": "write unit tests for payment service", "file": "tests/test_payment.py", "complexity": "low"},
            {"action": "fix typo in README documentation", "file": "README.md", "complexity": "low"},
            {"action": "implement caching layer for API responses", "file": "src/api/cache.py", "complexity": "medium"},
            {"action": "patch security vulnerability in file upload", "file": "src/upload.py", "complexity": "medium"},
            {"action": "create new CLI command for reports", "file": "src/cli/reports.py", "complexity": "medium"},
            {"action": "rewrite error handling in message queue", "file": "src/mq/errors.py", "complexity": "high"},
            {"action": "add parameterized test suite for models", "file": "tests/test_models.py", "complexity": "low"},
        ]

        # Simulated outcomes: action_id → (success_rate, avg_cost)
        action_outcomes = {
            0: (0.30, 0.001),   # Gemini Flash — 30% success
            1: (0.55, 0.001),   # DeepSeek — 55% success
            2: (0.50, 0.003),   # OpenAI — 50% success
            3: (0.65, 0.017),   # Haiku — 65% success
            4: (0.78, 0.050),   # Sonnet — 78% success
            5: (0.60, 0.002),   # OpenRouter — 60% success
        }

        total_episodes = 0
        for _ in range(200):
            task = random.choice(task_templates)
            action_id = random.choices(
                [0, 1, 2, 3, 4, 5],
                weights=[5, 61, 20, 8, 1, 5],  # mirrors real distribution
                k=1,
            )[0]
            sr, cost = action_outcomes[action_id]
            success = random.random() < sr
            features = list(extractor.extract(task, failure_count=random.randint(0, 2)))

            store.store(
                task=task,
                action_id=action_id,
                features=features,
                success=success,
                cost_usd=cost,
            )
            total_episodes += 1

        all_ok &= check(
            "RewardStore seeded",
            ok=(store.total_episodes() >= 200),
            detail=f"{store.total_episodes()} episodes written",
        )

        # ══════════════════════════════════════════════════════════════════
        header("2. ReplayGate adaptive threshold")

        gate = ReplayGate(threshold=0.40, novelty_window=50)
        episodes = store.get_recent(200)

        initial_threshold = gate.threshold
        admitted = 0
        for ep in episodes:
            if gate.admit(ep):
                admitted += 1

        final_threshold = gate.threshold
        admit_rate = gate.admit_rate()

        print(f"  Initial threshold: {initial_threshold:.3f}")
        print(f"  Final threshold:   {final_threshold:.3f}")
        print(f"  Episodes admitted: {admitted}/{len(episodes)} ({admit_rate:.1%})")

        all_ok &= check(
            "Threshold decreased with volume",
            ok=(final_threshold < initial_threshold),
            detail=f"{initial_threshold:.3f} → {final_threshold:.3f}",
        )

        all_ok &= check(
            "Admit rate improved over old 20.5%",
            ok=(admit_rate > 0.25),
            detail=f"{admit_rate:.1%}",
        )

        # ══════════════════════════════════════════════════════════════════
        header("3. Weight versioning — save/load with pad/truncate")

        # 3a: Start with a 4-action router (simulating old AWOS)
        weights_path = awos_dir / "linucb_weights.pkl"
        router4 = LinUCBRouter(n_features=N_FEATURES, n_actions=4, weights_path=weights_path)
        feats = np.ones(N_FEATURES) * 0.5
        for _ in range(30):
            router4.update(feats, random.randint(0, 3), 0.8)
        router4.save_now()

        # Verify weights file has format_version=2
        with weights_path.open("rb") as f:
            saved = pickle.load(f)
        fmt_ok = saved.get("format_version") == 2
        all_ok &= check(
            "Weights file uses format_version=2",
            ok=fmt_ok,
            detail=f"format_version={saved.get('format_version')}, "
                    f"n_actions={saved.get('n_actions')}, "
                    f"updates={saved.get('total_updates')}",
        )

        # 3b: Reload with 6 actions (simulating current AWOS) → pad
        router6 = LinUCBRouter(n_features=N_FEATURES, n_actions=6, weights_path=weights_path)
        all_ok &= check(
            "Weights survived 4→6 action pad",
            ok=(router6.total_updates() > 0),
            detail=f"updates={router6.total_updates()}, is_ready={router6.is_ready()}",
        )

        # 3c: New actions should have identity A matrix (explore freely)
        for a in range(4, 6):
            is_identity = np.allclose(router6._A[a], np.eye(N_FEATURES))
            all_ok &= check(
                f"Action {a} ({ACTION_NAMES[a]}) starts with identity matrix",
                ok=is_identity,
            )

        # 3d: Save 6-action version, reload with 4 actions → truncate
        router6.save_now()
        router4b = LinUCBRouter(n_features=N_FEATURES, n_actions=4, weights_path=weights_path)
        all_ok &= check(
            "Weights survived 6→4 action truncation",
            ok=(router4b.total_updates() > 0),
            detail=f"updates={router4b.total_updates()}",
        )

        # ══════════════════════════════════════════════════════════════════
        header("4. warm_start from RewardStore")

        # Fresh router — no prior knowledge
        fresh_path = awos_dir / "linucb_fresh.pkl"
        fresh_router = LinUCBRouter(
            n_features=N_FEATURES, n_actions=N_ACTIONS,
            weights_path=fresh_path, min_samples=20,
        )

        assert not fresh_router.is_ready(), "fresh router should not be ready"

        replayed = fresh_router.warm_start(store, max_episodes=100)

        all_ok &= check(
            "warm_start replayed episodes",
            ok=(replayed > 0),
            detail=f"{replayed} episodes replayed",
        )

        all_ok &= check(
            "[linucb] is_ready=True after warm_start",
            ok=fresh_router.is_ready(),
            detail=f"total_updates={fresh_router.total_updates()}",
        )

        fresh_router.save_now()

        # Verify persistence of warm-started weights
        reloaded = LinUCBRouter(
            n_features=N_FEATURES, n_actions=N_ACTIONS,
            weights_path=fresh_path, min_samples=20,
        )
        all_ok &= check(
            "Warm-started weights persist across reload",
            ok=(reloaded.total_updates() > 0),
            detail=f"updates={reloaded.total_updates()}",
        )

        # ══════════════════════════════════════════════════════════════════
        header("5. routing_status() observability")

        engine = EscalationEngine(ml_router=fresh_router)
        status = engine.routing_status()

        all_ok &= check(
            "routing_status linucb_ready=True",
            ok=status["linucb_ready"],
        )
        all_ok &= check(
            "routing_status has linucb_updates",
            ok=(status["linucb_updates"] > 0),
            detail=str(status["linucb_updates"]),
        )
        all_ok &= check(
            "routing_status has actions dict",
            ok=isinstance(status["actions"], dict) and len(status["actions"]) > 0,
            detail=f"{len(status['actions'])} actions",
        )
        all_ok &= check(
            "routing_status has is_cheap_only",
            ok=isinstance(status["is_cheap_only"], bool),
        )
        all_ok &= check(
            "routing_status has dead_providers",
            ok=("dead_providers" in status),
        )

        # Pretty-print the routing snapshot
        print(f"\n  {DIM.format('── Routing Snapshot ──')}")
        print(f"  LinUCB ready:      {status['linucb_ready']}")
        print(f"  Total updates:     {status['linucb_updates']}")
        print(f"  Cheap-only mode:   {status['is_cheap_only']}")
        print(f"  Dead providers:    {status['dead_providers'] or 'none'}")
        if status["actions"]:
            top_action = max(
                status["actions"].items(),
                key=lambda kv: abs(kv[1].get("bias", 0.0)),
            )
            print(f"  Top action:        {top_action[0]}")

        # ══════════════════════════════════════════════════════════════════
        header("6. Engine routing_status() without router (graceful)")

        engine2 = EscalationEngine(ml_router=None)
        status2 = engine2.routing_status()
        all_ok &= check(
            "routing_status defaults when no router",
            ok=(not status2["linucb_ready"] and status2["linucb_updates"] == 0),
            detail=f"ready={status2['linucb_ready']}, updates={status2['linucb_updates']}",
        )

        # ══════════════════════════════════════════════════════════════════
        header("7. Learned weights — does LinUCB learn the right policy?")

        weights = fresh_router.learned_weights()
        bias_values = {
            name: float(w[9])  # index 9 is the bias term
            for name, w in weights.items()
        }

        # DeepSeek (action 1) should have a higher bias than Gemini (action 0)
        # because DeepSeek was seeded at 55% success vs Gemini's 30%
        deepseek_bias = bias_values.get("deepseek", 0.0)
        gemini_bias = bias_values.get("gemini_flash", 0.0)

        print(f"\n  {DIM.format('── Learned Bias Terms (index 9) ──')}")
        for name, bias in sorted(bias_values.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {name:20s}  bias={bias:+.4f}")

        all_ok &= check(
            "DeepSeek has learned a strong preference (bias > 0.3 from 61% of episodes)",
            ok=deepseek_bias > 0.3,
            detail=f"deepseek={deepseek_bias:+.4f}",
        )

        all_ok &= check(
            "Gemini has lower bias (30% success rate in only 5% of episodes)",
            ok=gemini_bias < deepseek_bias,
            detail=f"gemini={gemini_bias:+.4f} vs deepseek={deepseek_bias:+.4f}",
        )

        # ══════════════════════════════════════════════════════════════════
        header("Result")

        if all_ok:
            print(f"\n  {PASS} All checks passed — routing learning system is operational.")
            print("\n  The self-learning loop is proven:")
            print("    1. RewardStore accumulates outcome data")
            print("    2. warm_start primes LinUCB from history")
            print("    3. ReplayGate adaptively admits more over time")
            print("    4. Weights persist across action space changes")
            print("    5. routing_status provides observability")
        else:
            print(f"\n  {FAIL} Some checks failed — see above for details.")

    finally:
        if not os.environ.get("KEEP_DEMO_DIR"):
            shutil.rmtree(workspace, ignore_errors=True)
            logger.debug("Cleaned up %s", workspace)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
