"""Tests for SemanticRetriever — semantic code search with graceful degradation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock


import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scaffold.agent.semantic_retriever import SemanticRetriever


# ── Helpers ────────────────────────────────────────────────────────────────────

@dataclass
class MockCodeChunk:
    """Simulate vector_memory.CodeChunk."""
    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    text: str
    symbol_name: str
    score: float = 0.85


def _make_mock_vm(chunks=None):
    """Create a mock VectorMemory with query_code returning given chunks."""
    vm = MagicMock()
    vm.query_code.return_value = chunks or []
    vm.index_codebase.return_value = 42
    return vm


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestRetrieve:
    """SemanticRetriever.retrieve() — core path."""

    def test_retrieve_with_mock_vm(self):
        """retrieve should return dicts from VectorMemory CodeChunks."""
        chunks = [
            MockCodeChunk("c1", "a.py", 10, 20, "def foo(): pass", "foo", 0.92),
            MockCodeChunk("c2", "b.py", 5, 15, "class Bar:", "Bar", 0.81),
        ]
        vm = _make_mock_vm(chunks)
        sr = SemanticRetriever(vector_memory=vm)

        result = sr.retrieve("fix foo function", top_k=3)

        assert len(result) == 2
        assert result[0]["file_path"] == "a.py"
        assert result[0]["symbol_name"] == "foo"
        assert result[0]["score"] == 0.92
        assert result[1]["file_path"] == "b.py"
        vm.query_code.assert_called_once_with("fix foo function", top_k=3)

    def test_retrieve_vm_none(self):
        """retrieve with vm=None should return empty, no crash."""
        sr = SemanticRetriever(vector_memory=None)
        result = sr.retrieve("do something")
        assert result == []

    def test_retrieve_vm_error(self):
        """retrieve should return empty when ChromaDB throws."""
        vm = MagicMock()
        vm.query_code.side_effect = RuntimeError("ChromaDB internal error")
        sr = SemanticRetriever(vector_memory=vm)

        result = sr.retrieve("do something")
        assert result == []

    def test_retrieve_empty_task(self):
        """Empty or whitespace task should return empty immediately."""
        vm = _make_mock_vm([])
        sr = SemanticRetriever(vector_memory=vm)

        assert sr.retrieve("") == []
        assert sr.retrieve("   ") == []
        # VM should NOT be called for empty queries
        vm.query_code.assert_not_called()

    def test_cache_hit(self):
        """Same task twice should return cached result (no second query)."""
        chunks = [MockCodeChunk("c1", "a.py", 1, 5, "code", "fn", 0.9)]
        vm = _make_mock_vm(chunks)
        sr = SemanticRetriever(vector_memory=vm)

        first = sr.retrieve("build auth")
        second = sr.retrieve("build auth")

        assert first == second
        # Called only once — second time is cache hit
        assert vm.query_code.call_count == 1

    def test_cache_different_tasks(self):
        """Different tasks should not hit cache."""
        chunks = [MockCodeChunk("c1", "a.py", 1, 5, "code", "fn", 0.9)]
        vm = _make_mock_vm(chunks)
        sr = SemanticRetriever(vector_memory=vm)

        sr.retrieve("task A")
        sr.retrieve("task B")

        assert vm.query_code.call_count == 2


class TestPreload:
    """SemanticRetriever.preload() — index delegation."""

    def test_preload_delegates(self):
        """preload should call index_codebase and return count."""
        vm = _make_mock_vm()
        sr = SemanticRetriever(vector_memory=vm)

        count = sr.preload("/some/repo")
        assert count == 42
        vm.index_codebase.assert_called_once_with("/some/repo")

    def test_preload_no_vm_returns_zero(self):
        """preload with no vm returns 0, no crash."""
        sr = SemanticRetriever(vector_memory=None)
        assert sr.preload("/tmp") == 0

    def test_preload_error_returns_zero(self):
        """preload when index_codebase throws returns 0, no crash."""
        vm = MagicMock()
        vm.index_codebase.side_effect = RuntimeError("disk full")
        sr = SemanticRetriever(vector_memory=vm)
        assert sr.preload("/tmp") == 0


class TestEstimateTokens:
    """Token estimation helper."""

    def test_estimate_tokens(self):
        assert SemanticRetriever.estimate_tokens("hello world") == 2  # 11 chars // 4
        assert SemanticRetriever.estimate_tokens("") == 0
        assert SemanticRetriever.estimate_tokens("a" * 100) == 25
