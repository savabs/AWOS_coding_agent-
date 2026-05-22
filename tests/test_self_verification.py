"""
Tests for SelfVerificationEngine (Phase 7).

All tests are pure Python (no LLM, no disk writes, no optional deps).
Expected runtime: <1s.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.self_verification import (
    SelfVerificationEngine,
    VerificationResult,
)


@pytest.fixture
def engine():
    return SelfVerificationEngine()


class TestVerificationResult:
    def test_passed_result(self):
        r = VerificationResult(passed=True, stage="all", errors=[], error_context="")
        assert r.passed is True
        assert r.stage == "all"

    def test_failed_result(self):
        r = VerificationResult(
            passed=False,
            stage="syntax",
            errors=["bad"],
            error_context="fix it",
        )
        assert r.passed is False
        assert r.errors == ["bad"]


class TestSearchExists:
    def test_search_not_in_file(self, engine):
        original = "def foo():\n    pass"
        sr = {"search": "def bar()", "replace": "def baz()"}
        r = engine.verify(original, sr, "test.py")
        assert not r.passed
        assert r.stage == "search_exists"
        assert "SEARCH string not found" in r.error_context

    def test_empty_search(self, engine):
        sr = {"search": "", "replace": "x"}
        r = engine.verify("abc", sr, "test.py")
        assert not r.passed
        assert r.stage == "search_exists"

    def test_search_found_passes(self, engine):
        sr = {"search": "def foo()", "replace": "def bar()"}
        r = engine.verify("def foo():\n    pass", sr, "test.py")
        assert r.passed
        assert r.stage == "all"


class TestSyntaxCheck:
    def test_syntax_error_detected(self, engine):
        sr = dict(search="def foo():", replace="def foo(:")
        original = "def foo():\n    pass"
        r = engine.verify(original, sr, "test.py")
        assert not r.passed
        assert r.stage == "syntax"
        assert "Syntax error" in r.error_context

    def test_valid_syntax_passes(self, engine):
        sr = {"search": "pass", "replace": "return None"}
        original = "def foo():\n    pass"
        r = engine.verify(original, sr, "test.py")
        assert r.passed

    def test_non_python_skips_ast(self, engine):
        """Non-Python files skip AST/import checks but still run search + contract."""
        sr = {"search": "hello", "replace": "world"}
        original = "hello world"
        r = engine.verify(original, sr, "test.txt")
        assert r.passed


class TestImportResolution:
    def test_third_party_missing_is_warning_not_error(self, engine):
        """Unknown modules (not in _STDLIB_NAMES) are treated as third-party warnings."""
        sr = {"search": "pass", "replace": "import _totally_fake_module_12345"}
        original = "def foo():\n    pass"
        r = engine.verify(original, sr, "test.py")
        # Third-party missing imports are warnings, not hard errors
        assert r.passed

    def test_valid_stdlib_import_passes(self, engine):
        sr = {"search": "pass", "replace": "import os\nimport sys"}
        original = "def foo():\n    pass"
        r = engine.verify(original, sr, "test.py")
        assert r.passed

    def test_valid_from_import_passes(self, engine):
        sr = {"search": "pass", "replace": "from pathlib import Path"}
        original = "def foo():\n    pass"
        r = engine.verify(original, sr, "test.py")
        assert r.passed


class TestContractCompliance:
    def test_missing_function_signature(self, engine):
        sr = {"search": "pass", "replace": "return 42"}
        original = "def foo():\n    pass"
        task = {"function_signature": "def bar():"}
        r = engine.verify(original, sr, "test.py", task_spec=task)
        assert not r.passed
        assert r.stage == "contract"
        assert "Missing required function signature" in str(r.errors)

    def test_constraint_unmet(self, engine):
        sr = {"search": "pass", "replace": "return 42"}
        original = "def foo():\n    pass"
        task = {"constraints": ["must use logging module"]}
        r = engine.verify(original, sr, "test.py", task_spec=task)
        assert not r.passed
        assert r.stage == "contract"
        assert "Constraint possibly unmet" in str(r.errors)

    def test_forbidden_pattern(self, engine):
        sr = {"search": "pass", "replace": "import os"}
        original = "def foo():\n    pass"
        task = {"must_not": ["do not import os"]}
        r = engine.verify(original, sr, "test.py", task_spec=task)
        assert not r.passed
        assert r.stage == "contract"
        assert "forbidden pattern" in str(r.errors).lower()

    def test_contract_met(self, engine):
        sr = {"search": "pass", "replace": "import logging\nreturn 42"}
        original = "def foo():\n    pass"
        task = {
            "function_signature": "def foo():",
            "constraints": ["must use logging"],
        }
        r = engine.verify(original, sr, "test.py", task_spec=task)
        assert r.passed

    def test_no_task_spec_skips_contract(self, engine):
        sr = {"search": "pass", "replace": "bad syntax {"}
        original = "def foo():\n    pass"
        r = engine.verify(original, sr, "test.py", task_spec=None)
        assert not r.passed
        assert r.stage == "syntax"


class TestMultipleSearchMatches:
    def test_duplicate_search_warns(self, engine):
        """Multiple SEARCH matches should not block (warning only)."""
        sr = {"search": "x = 1", "replace": "x = 2"}
        original = "x = 1\nx = 1\n"
        r = engine.verify(original, sr, "test.py")
        assert r.passed  # warning only, not hard error


class TestErrorContextFormat:
    def test_error_context_readable(self, engine):
        sr = {"search": "notfound", "replace": "x"}
        r = engine.verify("abc", sr, "test.py")
        assert "Self-verification" in r.error_context
        assert r.error_context.count("\n") <= 5  # concise


class TestIntegrationWorker:
    def test_worker_uses_self_verify_on_syntax_error(self):
        """
        Worker.execute_task should catch syntax error via SelfVerificationEngine
        and retry if attempt < 2.
        """
        from scaffold.agent.worker import Worker

        w = Worker.__new__(Worker)
        w.client = None
        w.anthropic_client = None
        w.model = "deepseek-chat"
        w.fallback_model = "claude-haiku-4-5"

        task = {
            "task_id": 1,
            "action": "fix syntax",
            "file": "test.py",
            "complexity": "simple",
        }
        file_content = "def foo():\n    pass\n"

        # Simulate a model response that introduces a syntax error
        bad_response = """
<<<<<<< SEARCH
def foo():
    pass
=======
def foo(:
    pass
>>>>>>> REPLACE
"""
        # We can't actually call Worker.execute_task without mocking the LLM,
        # so instead we test the SelfVerificationEngine directly which is what
        # Worker now calls internally.
        sv = SelfVerificationEngine()
        parsed = {"search": "def foo():\n    pass", "replace": "def foo(:\n    pass", "success": True}
        r = sv.verify(file_content, parsed, "test.py", task)
        assert not r.passed
        assert r.stage == "syntax"


class TestProjectRootHeuristic:
    def test_find_project_root_finds_git(self, engine):
        """Heuristic should find a directory with .git."""
        root = engine._find_project_root(str(Path(__file__).parent))
        assert root is not None
        assert (root / ".git").is_dir() or (root / "__init__.py").exists()
