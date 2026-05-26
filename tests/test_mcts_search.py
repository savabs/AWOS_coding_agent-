"""
tests/test_mcts_search.py — Tests for P4 MCTSSearchEngine (14 tests).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.mcts_search import (
    PatchNode, MCTSSearchEngine, MCTSResult,
    _ast_check, apply_patch_to_content,
)


# ── PatchNode UCB ─────────────────────────────────────────────────────────────

class TestPatchNodeUCT(unittest.TestCase):

    def test_unvisited_node_uct_is_inf(self):
        node = PatchNode(approach="test")
        self.assertEqual(node.ucb(parent_visits=10), float("inf"))

    def test_visited_node_uct_formula(self):
        import math
        node = PatchNode(approach="x", visits=4, total_reward=2.0)
        parent_visits = 16
        expected = 0.5 + 1.414 * math.sqrt(math.log(16) / 4)
        self.assertAlmostEqual(node.ucb(parent_visits), expected, places=2)

    def test_q_value_zero_visits(self):
        node = PatchNode(approach="x")
        self.assertEqual(node.q_value, 0.0)

    def test_q_value_with_rewards(self):
        node = PatchNode(approach="x", visits=2, total_reward=1.2)
        self.assertAlmostEqual(node.q_value, 0.6, places=5)


# ── Select prefers unvisited ──────────────────────────────────────────────────

class TestMCTSSelection(unittest.TestCase):

    def test_select_prefers_unvisited(self):
        root = PatchNode(approach="root", visits=1)
        visited = PatchNode(approach="v", visits=3, total_reward=1.0, parent=root)
        unvisited = PatchNode(approach="u", parent=root)
        root.children = [visited, unvisited]
        engine = MCTSSearchEngine.__new__(MCTSSearchEngine)
        selected = engine._select(root)
        self.assertIs(selected, unvisited)

    def test_select_returns_leaf_when_no_children(self):
        root = PatchNode(approach="root", visits=1)
        engine = MCTSSearchEngine.__new__(MCTSSearchEngine)
        self.assertIs(engine._select(root), root)


# ── Apply patch helpers ───────────────────────────────────────────────────────

class TestApplyPatch(unittest.TestCase):

    def test_apply_patch_success(self):
        content = "def foo():\n    return 1\n"
        result = apply_patch_to_content(content, "return 1", "return 2")
        self.assertIn("return 2", result)

    def test_apply_patch_not_found_returns_none(self):
        result = apply_patch_to_content("x = 1", "not_here", "y = 2")
        self.assertIsNone(result)

    def test_ast_check_valid(self):
        self.assertTrue(_ast_check("def foo():\n    pass\n"))

    def test_ast_check_syntax_error(self):
        self.assertFalse(_ast_check("def foo(:"))


# ── Simulate ─────────────────────────────────────────────────────────────────

class TestMCTSSimulate(unittest.TestCase):

    def _make_engine(self):
        engine = MCTSSearchEngine.__new__(MCTSSearchEngine)
        engine.generate_fn = MagicMock(return_value=[])
        engine.evaluate_fn = MagicMock(return_value=0.5)
        engine.max_rollouts = 5
        engine.n_branches = 2
        engine.project_root = "."
        return engine

    def test_simulate_empty_search_returns_miss(self):
        engine = self._make_engine()
        node = PatchNode(approach="x", search="", replace="")
        reward = engine._simulate(node, "content")
        self.assertEqual(reward, 0.0)

    def test_simulate_search_not_found_miss(self):
        engine = self._make_engine()
        node = PatchNode(approach="x", search="MISSING", replace="y")
        reward = engine._simulate(node, "content without that string")
        self.assertEqual(reward, 0.0)

    def test_simulate_syntax_error_miss(self):
        engine = self._make_engine()
        node = PatchNode(approach="x", search="pass", replace="def foo(:")
        reward = engine._simulate(node, "def bar():\n    pass\n")
        self.assertEqual(reward, 0.0)
        self.assertFalse(node.static_ok)


# ── Backprop ──────────────────────────────────────────────────────────────────

class TestMCTSBackprop(unittest.TestCase):

    def test_backprop_updates_parent(self):
        engine = MCTSSearchEngine.__new__(MCTSSearchEngine)
        root = PatchNode(approach="root", visits=1)
        child = PatchNode(approach="child", parent=root)
        child.visits = 1
        child.total_reward = 0.9
        engine._backprop(child, 0.9)
        self.assertGreater(root.visits, 1)
        self.assertGreater(root.total_reward, 0)


# ── Full search (mocked generate/evaluate) ────────────────────────────────────

class TestMCTSSearch(unittest.TestCase):

    def test_search_returns_mcts_result(self):
        def gen_fn(task, file_content, codebase_context, n, parent_approach):
            return [{"search": "pass", "replace": "return 1", "reasoning": "fix"}]

        def eval_fn(patched, project_root):
            return 0.5

        engine = MCTSSearchEngine(
            generate_fn=gen_fn,
            evaluate_fn=eval_fn,
            max_rollouts=3,
            n_branches=1,
        )
        task = {"action": "add return", "file": "a.py"}
        file_content = "def foo():\n    pass\n"
        result = engine.search(task, file_content, {})
        self.assertIsInstance(result, MCTSResult)
        self.assertGreaterEqual(result.rollouts_used, 1)

    def test_search_early_stop_on_perfect_patch(self):
        def gen_fn(task, file_content, codebase_context, n, parent_approach):
            return [{"search": "pass", "replace": "return 1", "reasoning": "perfect"}]

        def eval_fn(patched, project_root):
            return 1.0  # perfect score → early stop

        engine = MCTSSearchEngine(
            generate_fn=gen_fn,
            evaluate_fn=eval_fn,
            max_rollouts=10,
            n_branches=1,
        )
        result = engine.search({"action": "fix", "file": "a.py"}, "def f():\n    pass\n", {})
        self.assertTrue(result.success)
        self.assertLess(result.rollouts_used, 10)


if __name__ == "__main__":
    unittest.main()
