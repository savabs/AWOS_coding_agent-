"""
Tests for trick T5: skeleton / bounded file viewer (AWOS_SKELETON_VIEW).

Spec: docs/specs/skeleton_viewer.md. The real-file tests read a series base
read-only and skip when the harness is not on this machine.
"""

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import skeleton as sk
from scaffold.agent.tools.filesystem import ReadFileTool, ShowSymbolTool

REAL = Path("/Users/becmachlean/.awos-harness/real_series/real_parse_137/base/parse/__init__.py")

SAMPLE = '''"""Module doc line.

More text."""
import os

X = 1
Y: int = 2


def top(a, b=3, *args, **kw) -> int:
    """Top-level function."""
    def inner():
        return 1
    return inner()


class Outer(Base, metaclass=Meta):
    """Outer class."""
    attr = 5

    @property
    def value(self):
        return self.attr

    @staticmethod
    @other(1)
    def helper(x):
        """Helper doc."""
        return x

    class Inner:
        def value(self):
            return 0


async def coro():
    pass
'''


def _on(**extra):
    env = {"AWOS_SKELETON_VIEW": "1"}
    env.update(extra)
    return mock.patch.dict(os.environ, env)


class SkeletonFunctionTests(unittest.TestCase):
    def test_lists_every_def_with_its_line(self):
        out = sk.skeleton(SAMPLE, "sample.py")
        lines = SAMPLE.splitlines()
        for node in ast.walk(ast.parse(SAMPLE)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.assertIn(f"{node.lineno:6}\t", out)
                row = next(r for r in out.splitlines() if r.startswith(f"{node.lineno:6}\t")
                           and node.name in r)
                self.assertIn(f"L{node.lineno}-{node.end_lineno}", row)
                self.assertIn(node.name, lines[node.lineno - 1])

    def test_signatures_decorators_docs_and_names(self):
        out = sk.skeleton(SAMPLE)
        self.assertIn('"""Module doc line."""', out)
        self.assertIn("= X, Y", out)
        self.assertIn("def top(a, b=3, *args, **kw) -> int:", out)
        self.assertIn('"Top-level function."', out)
        self.assertIn("class Outer(Base, metaclass=Meta):", out)
        self.assertIn("@property", out)
        self.assertIn("@other(1)", out)
        self.assertIn("= attr", out)
        self.assertIn("        def value(self):", out)  # Outer.Inner.value, depth 2
        self.assertIn("async def coro():", out)
        self.assertNotIn("return", out)  # bodies elided

    def test_show_symbol_exact_body(self):
        lines = SAMPLE.splitlines(keepends=True)
        body = sk.show_symbol(SAMPLE, "Outer.helper")
        sym, _ = sk.find_symbol(SAMPLE, "Outer.helper")
        self.assertEqual(sym.first, 25)  # first decorator line
        expected = sk.numbered(lines[sym.first - 1:sym.last], sym.first)
        self.assertTrue(body.endswith(expected))
        self.assertIn("lines 25-29", body)

    def test_show_symbol_suffix_and_ambiguity(self):
        self.assertIn("Inner.value", sk.show_symbol(SAMPLE, "Inner.value"))
        with self.assertRaises(KeyError) as cm:
            sk.show_symbol(SAMPLE, "value")
        self.assertIn("Outer.value", str(cm.exception))
        self.assertIn("Outer.Inner.value", str(cm.exception))
        with self.assertRaises(KeyError):
            sk.show_symbol(SAMPLE, "nope")

    def test_window(self):
        text = "".join(f"line{i}\n" for i in range(1, 251))
        w = sk.window(text, 120)
        self.assertEqual(len(w.splitlines()), 100)
        self.assertTrue(w.startswith("   120\tline120"))
        self.assertIn("line219", w)
        self.assertNotIn("line220", w)


@unittest.skipUnless(REAL.is_file(), "real_series harness not present")
class RealFileTests(unittest.TestCase):
    def setUp(self):
        self.text = REAL.read_text()
        self.lines = self.text.splitlines(keepends=True)

    def test_real_skeleton_has_all_defs_with_correct_lines(self):
        out = sk.skeleton(self.text, REAL.name)
        rows = out.splitlines()
        count = 0
        for node in ast.walk(ast.parse(self.text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                count += 1
                tag = f"L{node.lineno}-{node.end_lineno}"
                row = [r for r in rows if tag in r and f" {node.name}" in r]
                self.assertTrue(row, f"{node.name} {tag} missing")
                self.assertTrue(row[0].startswith(f"{node.lineno:6}\t"))
        self.assertGreater(count, 40)
        self.assertLess(len(out), len(self.text) / 4)

    def test_real_show_symbol_is_exact(self):
        for q in ("Parser.evaluate_result", "extract_format", "Parser._handle_field"):
            sym, _ = sk.find_symbol(self.text, q)
            body = sk.show_symbol(self.text, q)
            self.assertTrue(body.endswith(sk.numbered(self.lines[sym.first - 1:sym.last], sym.first)))

    def test_tool_returns_skeleton_and_symbol(self):
        tool = ReadFileTool(project_root=str(REAL.parent.parent), confine=True)
        with _on(), mock.patch("builtins.print") as printed:
            res = tool.execute({"path": "parse/__init__.py"})
            self.assertTrue(res.success)
            self.assertTrue(res.data["skeleton"])
            self.assertIn("SKELETON of __init__.py", res.text)
            self.assertIn("symbol=", res.text)
            logged = " ".join(str(c.args[0]) for c in printed.call_args_list)
            self.assertRegex(logged, r"\[SKELETON\] __init__.py \d+ lines -> skeleton \d+ chars")
            res = tool.execute({"path": "parse/__init__.py", "symbol": "Parser.evaluate_result"})
            self.assertTrue(res.success)
            self.assertIn("def evaluate_result(self, m):", res.text)


class ReadFileToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        big = SAMPLE + "".join(f"def f{i}():\n    return {i}\n\n" for i in range(150))
        (self.root / "big.py").write_text(big)
        (self.root / "small.py").write_text(SAMPLE)
        (self.root / "big.txt").write_text("".join(f"row {i}\n" for i in range(1, 501)))
        (self.root / "broken.py").write_text("def (\n" * 400)
        self.tool = ReadFileTool(project_root=str(self.root), confine=True)
        self.big = big

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_off_is_unchanged(self):
        for env in ({}, {"AWOS_SKELETON_VIEW": "0"}):
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("AWOS_SKELETON_VIEW", None) if not env else None
                res = self.tool.execute({"path": "big.py"})
                self.assertEqual(res.data["content"], self.big)
                self.assertNotIn("symbol", self.tool.parameters)
                res = self.tool.execute({"path": "big.py", "start_line": 1, "end_line": 400})
                self.assertEqual(res.data["content"], "".join(self.big.splitlines(True)[:400]))

    def test_on_small_file_whole(self):
        with _on():
            res = self.tool.execute({"path": "small.py"})
        self.assertEqual(res.data["content"], SAMPLE)

    def test_on_explicit_range_works_and_is_capped(self):
        lines = self.big.splitlines(keepends=True)
        with _on(), mock.patch("builtins.print"):
            res = self.tool.execute({"path": "big.py", "start_line": 10, "end_line": 20})
            self.assertEqual(res.data["content"], sk.numbered(lines[9:20], 10))
            self.assertNotIn("Continue with", res.text)
            res = self.tool.execute({"path": "big.py", "start_line": 50, "end_line": 400})
            self.assertEqual(res.data["content"], sk.numbered(lines[49:149], 50))
            self.assertIn("Continue with start_line=150", res.text)
            res = self.tool.execute({"path": "big.py", "start_line": "[5, 7]"})
            self.assertEqual(res.data["content"], sk.numbered(lines[4:7], 5))
        with _on(AWOS_SKELETON_MAX_LINES="30"), mock.patch("builtins.print"):
            res = self.tool.execute({"path": "big.py", "start_line": 1, "end_line": 400})
            self.assertEqual(len(res.data["content"].splitlines()), 30)

    def test_on_non_python_first_window(self):
        with _on(), mock.patch("builtins.print"):
            res = self.tool.execute({"path": "big.txt"})
        self.assertIn("(lines 1–100 of 500)", res.text)
        self.assertIn("row 100", res.data["content"])
        self.assertNotIn("row 101", res.data["content"])

    def test_on_unparsable_python_falls_back_to_window(self):
        with _on(), mock.patch("builtins.print"):
            res = self.tool.execute({"path": "broken.py"})
        self.assertTrue(res.success)
        self.assertIn("(lines 1–100 of 400)", res.text)

    def test_min_lines_threshold(self):
        with _on(AWOS_SKELETON_MIN_LINES="10000"):
            res = self.tool.execute({"path": "big.py"})
        self.assertEqual(res.data["content"], self.big)

    def test_symbol_errors(self):
        with _on():
            res = self.tool.execute({"path": "big.py", "symbol": "value"})
            self.assertFalse(res.success)
            self.assertIn("candidates", res.error or res.text)
            res = self.tool.execute({"path": "big.txt", "symbol": "x"})
            self.assertFalse(res.success)

    def test_show_symbol_tool(self):
        tool = ShowSymbolTool(project_root=str(self.root), confine=True)
        self.assertTrue(tool.validate({"path": "big.py"}))
        with mock.patch("builtins.print"):
            res = tool.execute({"path": "big.py", "symbol": "top"})
        self.assertTrue(res.success)
        self.assertIn("def inner():", res.text)
        self.assertFalse(tool.execute({"path": "../etc/passwd", "symbol": "x"}).success)


if __name__ == "__main__":
    unittest.main()
