"""
mcts_search.py — MCTS-guided patch synthesis for AWOS worker.

Instead of single-shot SEARCH/REPLACE generation, explores a tree of candidate
patches guided by execution feedback (test pass rate) as the reward signal.

Architecture (arXiv 2602.00129 — CodePilot, Feb 2026):
  - Nodes:            candidate patch approaches (SEARCH/REPLACE pairs)
  - Selection:        UCB1 — balances exploitation vs exploration
  - Expansion:        LLM samples N diverse candidate patches
  - Simulation:       tiered evaluation — AST check first, pytest only on survivors
  - Backpropagation:  Q-values updated up the tree path

Tiered evaluation (cost-optimised):
  Tier 1: AST parse (free, <1ms)  — eliminates malformed patches
  Tier 2: pytest run (real cost)  — only for patches passing Tier 1
  Early exit: returns immediately on first patch achieving pass_rate >= 1.0

Plugs into worker.py via execute_task(use_mcts=True, project_root=...).
"""

from __future__ import annotations

import ast
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

C_PUCT: float = 1.414          # UCB exploration constant (sqrt(2) — standard MCTS)
_STATIC_REWARD: float = 0.05   # reward for a patch that parses but tests skipped
_MISS_REWARD: float = 0.0      # reward when search string not found in file


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class PatchNode:
    """One node in the MCTS search tree — a candidate patch."""
    approach: str                          # high-level plan text (from LLM reasoning)
    search: str = ""                       # SEARCH block
    replace: str = ""                      # REPLACE block
    parent: Optional["PatchNode"] = None
    children: List["PatchNode"] = field(default_factory=list)
    visits: int = 0
    total_reward: float = 0.0
    pass_rate: float = 0.0
    static_ok: bool = False
    is_terminal: bool = False              # True when pass_rate >= 1.0

    @property
    def q_value(self) -> float:
        return self.total_reward / self.visits if self.visits > 0 else 0.0

    def ucb(self, parent_visits: int) -> float:
        if self.visits == 0:
            return float("inf")
        return self.q_value + C_PUCT * math.sqrt(math.log(parent_visits) / self.visits)

    def is_root(self) -> bool:
        return self.parent is None

    def select_child(self) -> Optional["PatchNode"]:
        if not self.children:
            return None
        return max(self.children, key=lambda c: c.ucb(self.visits))

    def best_child_by_visits(self) -> Optional["PatchNode"]:
        if not self.children:
            return None
        return max(self.children, key=lambda c: c.visits)


@dataclass
class MCTSResult:
    """Return type from MCTSSearchEngine.search()."""
    success: bool
    search: str
    replace: str
    reasoning: str
    pass_rate: float
    rollouts_used: int
    elapsed_sec: float
    model_used: str = "mcts"


# ── Engine ────────────────────────────────────────────────────────────────────

class MCTSSearchEngine:
    """
    MCTS-guided patch synthesis.

    Usage:
        engine = MCTSSearchEngine(
            generate_fn=worker._generate_n_patches,
            evaluate_fn=worker._evaluate_patch,
            max_rollouts=10,
            n_branches=3,
            project_root=".",
        )
        result = engine.search(task, file_content, codebase_context)
        if result.success:
            apply_patch(result.search, result.replace)
    """

    def __init__(
        self,
        generate_fn: Callable[..., List[Dict[str, str]]],
        evaluate_fn: Callable[[str, str], float],
        max_rollouts: int = 10,
        n_branches: int = 3,
        project_root: str = ".",
    ) -> None:
        """
        Args:
            generate_fn:   (task, file_content, context, n, parent_approach) → list of
                           {"search": str, "replace": str, "reasoning": str}
            evaluate_fn:   (patched_content, project_root) → pass_rate float [0,1]
            max_rollouts:  budget cap — total LLM expansions allowed
            n_branches:    how many diverse patches to generate per expansion
            project_root:  working directory for test execution
        """
        self.generate_fn = generate_fn
        self.evaluate_fn = evaluate_fn
        self.max_rollouts = max_rollouts
        self.n_branches = n_branches
        self.project_root = project_root

    def search(
        self,
        task: Dict[str, Any],
        file_content: str,
        codebase_context: Dict[str, Any],
    ) -> MCTSResult:
        """
        Run MCTS over the patch space and return the best patch found.

        Early-exits as soon as a perfect patch (pass_rate=1.0) is found.
        Falls back to the best partial patch if budget exhausted.
        """
        t0 = time.time()
        root = PatchNode(approach="root")
        root.visits = 1  # prevent div-by-zero in UCB

        best = MCTSResult(
            success=False, search="", replace="",
            reasoning="MCTS: no passing patch found within budget",
            pass_rate=0.0, rollouts_used=0, elapsed_sec=0.0,
        )

        for rollout in range(self.max_rollouts):
            # ── Selection ────────────────────────────────────────────────
            node = self._select(root)

            # ── Expansion ────────────────────────────────────────────────
            if not node.is_terminal and not node.children:
                self._expand(node, task, file_content, codebase_context)

            # ── Simulation ───────────────────────────────────────────────
            for child in node.children:
                if child.visits > 0:
                    continue  # already evaluated

                reward = self._simulate(child, file_content)

                # Track best result seen so far
                if child.pass_rate > best.pass_rate:
                    best = MCTSResult(
                        success=child.pass_rate >= 1.0,
                        search=child.search,
                        replace=child.replace,
                        reasoning=f"MCTS rollout {rollout + 1}: {child.approach[:120]}",
                        pass_rate=child.pass_rate,
                        rollouts_used=rollout + 1,
                        elapsed_sec=round(time.time() - t0, 2),
                    )

                # ── Backpropagation ───────────────────────────────────────
                self._backprop(child, reward)

                # Early exit on perfect patch
                if child.is_terminal:
                    logger.info(
                        "[MCTS] Perfect patch at rollout %d (%.1fs)",
                        rollout + 1, time.time() - t0,
                    )
                    best.rollouts_used = rollout + 1
                    best.elapsed_sec = round(time.time() - t0, 2)
                    return best

        best.rollouts_used = self.max_rollouts
        best.elapsed_sec = round(time.time() - t0, 2)
        logger.info(
            "[MCTS] Budget exhausted — best pass_rate=%.2f (%d rollouts, %.1fs)",
            best.pass_rate, self.max_rollouts, time.time() - t0,
        )
        return best

    # ── Tree policy ───────────────────────────────────────────────────────────

    def _select(self, root: PatchNode) -> PatchNode:
        """Traverse tree to most promising unexplored leaf (UCB1)."""
        node = root
        while node.children:
            unvisited = [c for c in node.children if c.visits == 0]
            if unvisited:
                return unvisited[0]
            next_node = node.select_child()
            if next_node is None:
                break
            node = next_node
        return node

    def _expand(
        self,
        node: PatchNode,
        task: Dict[str, Any],
        file_content: str,
        codebase_context: Dict[str, Any],
    ) -> None:
        """Ask LLM for N diverse patch candidates and attach as children."""
        try:
            patches = self.generate_fn(
                task=task,
                file_content=file_content,
                codebase_context=codebase_context,
                n=self.n_branches,
                parent_approach=node.approach if not node.is_root() else "",
            )
            for p in patches:
                child = PatchNode(
                    approach=p.get("reasoning", "")[:200],
                    search=p.get("search", ""),
                    replace=p.get("replace", ""),
                    parent=node,
                )
                node.children.append(child)
            logger.debug("[MCTS] Expanded node → %d children", len(node.children))
        except Exception as exc:
            logger.warning("[MCTS] _expand failed: %s", exc)

    def _simulate(self, node: PatchNode, original_content: str) -> float:
        """
        Evaluate a patch node. Returns reward in [0, 1].

        Tier 1 (free):  AST parse check — rejects malformed patches immediately.
        Tier 2 (cost):  pytest run     — only if Tier 1 passes.
        """
        if not node.search:
            return _MISS_REWARD

        # Apply patch
        patched = original_content.replace(node.search, node.replace, 1)
        if patched == original_content:
            # Search string not found — no change made
            logger.debug("[MCTS] search string not found in file")
            return _MISS_REWARD

        # Tier 1: syntax check (free)
        if not _ast_check(patched):
            node.static_ok = False
            logger.debug("[MCTS] Tier 1 FAIL (syntax error)")
            return _MISS_REWARD

        node.static_ok = True

        # Tier 2: test execution
        try:
            pass_rate = self.evaluate_fn(patched, self.project_root)
        except Exception as exc:
            logger.debug("[MCTS] evaluate_fn raised: %s", exc)
            pass_rate = _STATIC_REWARD  # static passed at least

        node.pass_rate = pass_rate
        node.is_terminal = pass_rate >= 1.0
        return pass_rate

    def _backprop(self, node: PatchNode, reward: float) -> None:
        """Propagate reward up through all ancestors."""
        n: Optional[PatchNode] = node
        while n is not None:
            n.visits += 1
            n.total_reward += reward
            n = n.parent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ast_check(source: str) -> bool:
    """Return True if source parses as valid Python, False on SyntaxError."""
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def apply_patch_to_content(content: str, search: str, replace: str) -> Optional[str]:
    """
    Apply a SEARCH/REPLACE patch to file content.
    Returns patched content or None if search string not found.
    """
    if search not in content:
        return None
    return content.replace(search, replace, 1)


def write_and_run_tests(
    patched_content: str,
    original_path: str,
    project_root: str,
    timeout_sec: int = 30,
) -> float:
    """
    Write patched_content to a temp copy of original_path, run project tests,
    return pass_rate. Used as the evaluate_fn for MCTSSearchEngine.

    Requires AWOS_SAFE_TO_RUN_TESTS=1 to be set (same gate as TestRunner).
    """
    if os.environ.get("AWOS_SAFE_TO_RUN_TESTS") != "1":
        logger.debug("[MCTS] Test execution skipped (AWOS_SAFE_TO_RUN_TESTS not set)")
        return _STATIC_REWARD

    try:
        from .test_runner import TestRunner
    except ImportError:
        from test_runner import TestRunner

    orig = Path(original_path)
    backup = orig.read_text(encoding="utf-8", errors="ignore")
    try:
        orig.write_text(patched_content, encoding="utf-8")
        runner = TestRunner(project_root=project_root, timeout_sec=timeout_sec)
        result = runner.run()
        return result.pass_rate
    finally:
        orig.write_text(backup, encoding="utf-8")  # always restore
