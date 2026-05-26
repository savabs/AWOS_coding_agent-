"""
tests/test_fault_localizer.py — Tests for P1.5 FaultLocalizer (12 tests).
"""

import sys
import textwrap
import unittest
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.fault_localizer import FaultLocalizer, LocalizedFault, _extract_symbols


class TestExtractSymbols(unittest.TestCase):

    def test_extracts_function(self):
        src = "def foo():\n    pass\n"
        symbols = _extract_symbols(src)
        names = [s[0] for s in symbols]
        self.assertIn("foo", names)

    def test_extracts_class(self):
        src = "class Bar:\n    def baz(self): pass\n"
        symbols = _extract_symbols(src)
        names = [s[0] for s in symbols]
        self.assertIn("Bar", names)
        self.assertIn("baz", names)

    def test_syntax_error_returns_empty(self):
        self.assertEqual(_extract_symbols("def foo(:"), [])

    def test_start_end_lines(self):
        src = "def foo():\n    return 1\n"
        symbols = _extract_symbols(src)
        foo = next(s for s in symbols if s[0] == "foo")
        self.assertEqual(foo[1], 1)
        self.assertGreaterEqual(foo[2], 2)


class TestFaultLocalizerLevelOne(unittest.TestCase):

    def _make_project(self, tmpdir: Path):
        (tmpdir / "worker.py").write_text("def execute(): pass\n")
        (tmpdir / "planner.py").write_text("def plan(): pass\n")
        return tmpdir

    def test_stack_trace_hits_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_project(root)
            fl = FaultLocalizer()
            trace = f'File "{root / "worker.py"}", line 1, in execute\n'
            faults = fl.localize("fix execute bug", error_trace=trace, project_root=td)
            files = [f.file_path for f in faults]
            self.assertTrue(any("worker" in f for f in files))

    def test_no_trace_returns_empty_or_short(self):
        with tempfile.TemporaryDirectory() as td:
            fl = FaultLocalizer()
            faults = fl.localize("fix something", error_trace="", project_root=td)
            self.assertIsInstance(faults, list)

    def test_recently_modified_boosted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "recent.py").write_text("x=1\n")
            fl = FaultLocalizer()
            faults = fl.localize(
                "fix recent", error_trace="", project_root=td,
                recently_modified=["recent.py"]
            )
            if faults:
                self.assertTrue(any("recent" in f.file_path for f in faults))


class TestFaultLocalizerLevel2(unittest.TestCase):

    def test_symbol_in_query_boosted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = textwrap.dedent("""\
                def authenticate(token):
                    pass
                def logout():
                    pass
            """)
            (root / "auth.py").write_text(src)
            fl = FaultLocalizer()
            trace = f'File "{root / "auth.py"}", line 1, in authenticate\n'
            faults = fl.localize("fix authenticate bug", trace, project_root=td)
            if faults:
                top = faults[0]
                self.assertIn("authenticate", top.symbol_name)


class TestFaultLocalizerContextBlock(unittest.TestCase):

    def test_empty_faults_returns_empty(self):
        fl = FaultLocalizer()
        result = fl.to_context_block([], project_root="/tmp")
        self.assertEqual(result, "")

    def test_context_block_has_snippet(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("def foo():\n    return 1\n")
            fault = LocalizedFault(
                file_path="a.py",
                symbol_name="foo",
                start_line=1, end_line=2,
                confidence=0.8,
                reason="test",
            )
            fl = FaultLocalizer()
            block = fl.to_context_block([fault], project_root=td)
            self.assertIn("foo", block)

    def test_context_block_missing_file_skipped(self):
        fl = FaultLocalizer()
        fault = LocalizedFault(
            file_path="does_not_exist.py",
            symbol_name="missing",
            start_line=1, end_line=5,
            confidence=0.5,
            reason="test",
        )
        block = fl.to_context_block([fault], project_root="/tmp")
        self.assertEqual(block, "")


if __name__ == "__main__":
    unittest.main()
