"""
SemanticRetriever — semantic code search with graceful degradation.

Thin wrapper over VectorMemory (ChromaDB + fastembed). When ChromaDB
is unavailable or broken, returns empty results — the MultiResolutionContextPipeline
falls back to raw file content, and the worker continues unimpaired.

Cache: 100-entry LRU to avoid re-querying the same task repeatedly.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 100


class SemanticRetriever:
    """Unified semantic search over codebase with caching and graceful degradation."""

    def __init__(self, vector_memory: Any = None) -> None:
        """
        Args:
            vector_memory: Optional VectorMemory instance. If None or broken,
                           retrieve() returns empty and preload() returns 0.
        """
        self._vm = vector_memory
        self._cache: dict[str, list[dict[str, Any]]] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def retrieve(self, task_description: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Return relevant code chunks for a task description.

        Args:
            task_description: What the agent needs to do (used as semantic query).
            top_k: Maximum number of chunks to return.

        Returns:
            List of dicts with keys: file_path, start_line, end_line, text,
            symbol_name, score. Empty list if no results or retriever unavailable.
        """
        if not task_description or not task_description.strip():
            return []

        key = _cache_key(task_description)
        if key in self._cache:
            logger.debug("[SemanticRetriever] cache hit for %s", task_description[:50])
            return self._cache[key]

        chunks = self._query_vm(task_description, top_k)
        self._cache_set(key, chunks)
        return chunks

    def preload(self, repo_root: str) -> int:
        """Build semantic index for a codebase. Returns chunk count or 0."""
        if self._vm is None:
            logger.info("[SemanticRetriever] preload skipped — no VectorMemory")
            return 0

        try:
            count = self._vm.index_codebase(repo_root)
            logger.info("[SemanticRetriever] preloaded %d chunks from %s", count, repo_root)
            return count
        except Exception as exc:
            logger.warning("[SemanticRetriever] preload failed: %s", exc)
            return 0

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token count (1 token ≈ 4 chars)."""
        return len(text) // 4

    # ── Internals ──────────────────────────────────────────────────────────

    def _query_vm(self, task: str, top_k: int) -> list[dict[str, Any]]:
        """Try VectorMemory.query_code. Returns list of chunk dicts or empty."""
        if self._vm is None:
            return []

        try:
            results = self._vm.query_code(task, top_k=top_k)
        except Exception as exc:
            logger.warning("[SemanticRetriever] VectorMemory query failed: %s", exc)
            return []

        if not results:
            return []

        return [
            {
                "file_path": c.file_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "text": c.text,
                "symbol_name": c.symbol_name,
                "score": round(c.score, 4),
            }
            for c in results
        ]

    def _cache_set(self, key: str, chunks: list[dict[str, Any]]) -> None:
        """Add to cache. Evict oldest if over limit."""
        if len(self._cache) >= MAX_CACHE_SIZE:
            # Remove first-inserted key (Python 3.7+ dicts are insertion-ordered)
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = chunks


def _cache_key(text: str) -> str:
    """Deterministic short hash for cache lookup."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
