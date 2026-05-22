"""
VectorMemory — Semantic codebase indexing and session outcome retrieval (Phase 6).

Uses ChromaDB + all-MiniLM-L6-v2 (local, zero API cost) to provide:
  1. Codebase chunk retrieval  — inject relevant functions/classes into Worker prompts.
  2. Session outcome memory    — cross-session recall of past task results.
  3. Semantic goal matching    — cosine similarity supplement to Jaccard in ProjectPlanner.

Graceful degradation: if chromadb or sentence-transformers are not installed,
VectorMemoryUnavailableError is raised on __init__ and all callers fall back to
existing non-vector paths.

References:
  - arXiv:2510.04905 (Oct 2025) — RACG survey; dense retrieval > sparse for code.
  - ChromaDB PersistentClient + SentenceTransformerEmbeddingFunction docs.
  - all-MiniLM-L6-v2 HuggingFace model card: 384-dim, 256 token max.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

VECTOR_DB_DIR = ".awos/vector_db"
CODEBASE_COLLECTION = "codebase_chunks"
SESSION_COLLECTION = "session_memory"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
MAX_CHUNK_TOKENS = 256        # MiniLM hard limit (approximate via whitespace split)
CHUNK_OVERLAP_LINES = 10      # for fallback sliding window
SLIDING_WINDOW_LINES = 50     # lines per sliding-window chunk
TOP_K_CODE = 5                # chunks injected into worker prompt
TOP_K_OUTCOMES = 3            # past outcomes returned
GOAL_SIM_THRESHOLD = 0.82     # cosine threshold for goal matching
MAX_SESSION_DOCS = 10_000     # evict oldest docs above this
EVICT_BATCH = 1_000           # how many to evict at once


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class CodeChunk:
    """A snippet of source code with location metadata and retrieval score."""
    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    text: str          # full source text (may exceed 256 tokens)
    symbol_name: str   # function/class name, or "" for sliding-window chunks
    score: float = 0.0


@dataclass
class OutcomeRecord:
    """A past task execution outcome stored in session_memory."""
    task_action: str
    file_path: str
    success: bool
    session_id: str
    created_at: str
    critique: str
    score: float = 0.0


# ── Error ────────────────────────────────────────────────────────────────────


class VectorMemoryUnavailableError(Exception):
    """Raised when chromadb or sentence-transformers are not installed."""


# ── VectorMemory ─────────────────────────────────────────────────────────────


class VectorMemory:
    """
    Persistent semantic memory backed by ChromaDB.

    Two collections:
      - codebase_chunks: AST-chunked Python source code
      - session_memory:  past task outcomes (action, success, critique)
    """

    def __init__(self, persist_dir: str = VECTOR_DB_DIR) -> None:
        try:
            import chromadb
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )
        except Exception as exc:
            raise VectorMemoryUnavailableError(
                f"chromadb or sentence-transformers unavailable: {exc}. "
                "Run: pip install chromadb sentence-transformers"
            ) from exc

        self._dir = Path(persist_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._mtime_path = self._dir / "mtime_cache.json"

        self._embed_fn = SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )

        self._client = chromadb.PersistentClient(path=str(self._dir))

        # Cosine metric for [0,1] similarity scores
        self._code_col = self._client.get_or_create_collection(
            CODEBASE_COLLECTION,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self._sess_col = self._client.get_or_create_collection(
            SESSION_COLLECTION,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self._mtime_cache: Dict[str, float] = self._load_mtime_cache()
        logger.info(
            "[VectorMemory] init — code_chunks=%d session_docs=%d",
            self._code_col.count(),
            self._sess_col.count(),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def index_codebase(self, root: str) -> int:
        """
        Incrementally index all .py files under *root*.
        Returns the number of new/updated chunks written.
        """
        root_path = Path(root)
        new_count = 0

        for py_file in sorted(root_path.rglob("*.py")):
            if _skip_path(py_file):
                continue

            rel = str(py_file.relative_to(root_path))
            mtime = py_file.stat().st_mtime

            if self._mtime_cache.get(rel) == mtime:
                continue  # unchanged

            chunks = _extract_chunks(py_file, rel)
            if not chunks:
                self._mtime_cache[rel] = mtime
                continue

            # Delete old chunks for this file
            try:
                self._code_col.delete(where={"file_path": rel})
            except Exception:
                pass  # collection may not have any docs for this file

            ids = [c.chunk_id for c in chunks]
            docs = [_truncate_tokens(c.text) for c in chunks]
            metas = [
                {
                    "file_path": c.file_path,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "symbol_name": c.symbol_name,
                    "mtime": mtime,
                }
                for c in chunks
            ]
            self._code_col.upsert(ids=ids, documents=docs, metadatas=metas)
            self._mtime_cache[rel] = mtime
            new_count += len(chunks)

        self._save_mtime_cache()
        logger.info("[VectorMemory] indexed %d new/updated chunks", new_count)
        return new_count

    def query_code(
        self,
        query: str,
        top_k: int = TOP_K_CODE,
        file_filter: Optional[str] = None,
    ) -> List[CodeChunk]:
        """
        Semantic search over codebase_chunks.
        Returns up to *top_k* CodeChunk objects sorted by score descending.
        """
        try:
            kwargs: dict = {"query_texts": [query], "n_results": min(top_k, max(1, self._code_col.count()))}
            if file_filter:
                kwargs["where"] = {"file_path": file_filter}
            results = self._code_col.query(**kwargs)
            return _parse_code_results(results)
        except Exception as exc:
            logger.warning("[VectorMemory] query_code failed: %s", exc)
            return []

    def store_outcome(
        self,
        task: dict,
        success: bool,
        session_id: str,
        critique: str = "",
    ) -> None:
        """Store a task outcome into session_memory."""
        try:
            action = task.get("action", "")
            doc_id = uuid.uuid4().hex
            self._sess_col.add(
                ids=[doc_id],
                documents=[action],
                metadatas=[
                    {
                        "file_path": task.get("file", ""),
                        "success": success,
                        "session_id": session_id,
                        "created_at": _now_iso(),
                        "critique": critique[:500],  # cap stored critique length
                    }
                ],
            )
            self._evict_session_if_needed()
        except Exception as exc:
            logger.warning("[VectorMemory] store_outcome failed: %s", exc)

    def query_outcomes(
        self,
        description: str,
        top_k: int = TOP_K_OUTCOMES,
    ) -> List[OutcomeRecord]:
        """Semantic search over session_memory outcomes."""
        try:
            count = self._sess_col.count()
            if count == 0:
                return []
            results = self._sess_col.query(
                query_texts=[description],
                n_results=min(top_k, count),
            )
            return _parse_outcome_results(results)
        except Exception as exc:
            logger.warning("[VectorMemory] query_outcomes failed: %s", exc)
            return []

    def find_similar_goal(
        self,
        description: str,
        threshold: float = GOAL_SIM_THRESHOLD,
    ) -> Optional[str]:
        """
        Search session_memory for a past task action similar to *description*.
        Returns the matching document text if cosine score >= threshold, else None.
        """
        try:
            count = self._sess_col.count()
            if count == 0:
                return None
            results = self._sess_col.query(
                query_texts=[description],
                n_results=1,
            )
            if not results["documents"] or not results["documents"][0]:
                return None
            # ChromaDB cosine distance → similarity = 1 - distance
            dist = results["distances"][0][0]
            score = 1.0 - dist
            if score >= threshold:
                return results["documents"][0][0]
            return None
        except Exception as exc:
            logger.warning("[VectorMemory] find_similar_goal failed: %s", exc)
            return None

    def reset_codebase_index(self) -> None:
        """Delete and recreate the codebase_chunks collection. Clears mtime cache."""
        try:
            self._client.delete_collection(CODEBASE_COLLECTION)
        except Exception:
            pass
        self._code_col = self._client.get_or_create_collection(
            CODEBASE_COLLECTION,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self._mtime_cache = {}
        self._save_mtime_cache()
        logger.info("[VectorMemory] codebase index reset")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_mtime_cache(self) -> Dict[str, float]:
        if not self._mtime_path.exists():
            return {}
        try:
            with open(self._mtime_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_mtime_cache(self) -> None:
        tmp = self._mtime_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._mtime_cache, fh)
            os.replace(str(tmp), str(self._mtime_path))
        except OSError as exc:
            logger.warning("[VectorMemory] mtime cache save failed: %s", exc)

    def _evict_session_if_needed(self) -> None:
        try:
            count = self._sess_col.count()
            if count <= MAX_SESSION_DOCS:
                return
            # Retrieve oldest EVICT_BATCH ids (ChromaDB doesn't sort, so get all and sort by created_at)
            all_items = self._sess_col.get(include=["metadatas"])
            if not all_items["ids"]:
                return
            pairs = sorted(
                zip(all_items["ids"], all_items["metadatas"]),
                key=lambda x: x[1].get("created_at", ""),
            )
            to_delete = [pid for pid, _ in pairs[:EVICT_BATCH]]
            self._sess_col.delete(ids=to_delete)
            logger.info("[VectorMemory] evicted %d old session docs", len(to_delete))
        except Exception as exc:
            logger.warning("[VectorMemory] eviction failed: %s", exc)


# ── File-level helpers ────────────────────────────────────────────────────────


def _skip_path(path: Path) -> bool:
    parts = path.parts
    return any(p in ("__pycache__", ".git", ".venv", "venv", "node_modules") for p in parts)


def _chunk_id(file_path: str, start_line: int) -> str:
    key = f"{file_path}:{start_line}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _truncate_tokens(text: str, max_tokens: int = MAX_CHUNK_TOKENS) -> str:
    """Truncate text to approximately max_tokens words (whitespace tokens)."""
    tokens = text.split()
    if len(tokens) <= max_tokens:
        return text
    return " ".join(tokens[:max_tokens])


def _extract_chunks(py_file: Path, rel_path: str) -> List[CodeChunk]:
    """Parse a .py file and extract CodeChunks by AST top-level symbols."""
    try:
        source = py_file.read_text(errors="ignore")
        lines = source.splitlines()
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, OSError) as exc:
        logger.warning("[VectorMemory] skipping %s: %s", rel_path, exc)
        return []

    chunks: List[CodeChunk] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.col_offset != 0:  # top-level only
            continue
        start = node.lineno - 1  # 0-indexed
        end = getattr(node, "end_lineno", start + 1)
        body_lines = lines[start:end]
        text = "\n".join(body_lines)
        if not text.strip():
            continue
        chunks.append(
            CodeChunk(
                chunk_id=_chunk_id(rel_path, node.lineno),
                file_path=rel_path,
                start_line=node.lineno,
                end_line=end,
                text=text,
                symbol_name=node.name,
            )
        )

    # Fallback: if no top-level symbols found, use sliding window
    if not chunks and lines:
        chunks = _sliding_window_chunks(lines, rel_path)

    return chunks


def _sliding_window_chunks(
    lines: List[str],
    rel_path: str,
    window: int = SLIDING_WINDOW_LINES,
    overlap: int = CHUNK_OVERLAP_LINES,
) -> List[CodeChunk]:
    chunks = []
    step = window - overlap
    i = 0
    while i < len(lines):
        end = min(i + window, len(lines))
        text = "\n".join(lines[i:end])
        if text.strip():
            chunks.append(
                CodeChunk(
                    chunk_id=_chunk_id(rel_path, i + 1),
                    file_path=rel_path,
                    start_line=i + 1,
                    end_line=end,
                    text=text,
                    symbol_name="",
                )
            )
        i += step
    return chunks


def _parse_code_results(results: dict) -> List[CodeChunk]:
    chunks = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        score = max(0.0, 1.0 - dist)
        chunks.append(
            CodeChunk(
                chunk_id=meta.get("chunk_id", ""),
                file_path=meta.get("file_path", ""),
                start_line=int(meta.get("start_line", 0)),
                end_line=int(meta.get("end_line", 0)),
                text=doc,
                symbol_name=meta.get("symbol_name", ""),
                score=score,
            )
        )
    chunks.sort(key=lambda c: c.score, reverse=True)
    return chunks


def _parse_outcome_results(results: dict) -> List[OutcomeRecord]:
    records = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        score = max(0.0, 1.0 - dist)
        records.append(
            OutcomeRecord(
                task_action=doc,
                file_path=meta.get("file_path", ""),
                success=bool(meta.get("success", False)),
                session_id=meta.get("session_id", ""),
                created_at=meta.get("created_at", ""),
                critique=meta.get("critique", ""),
                score=score,
            )
        )
    records.sort(key=lambda r: r.score, reverse=True)
    return records


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
