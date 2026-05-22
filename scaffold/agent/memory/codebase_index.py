"""
codebase_index.py — Semantic index of the entire codebase for meaning-based search.

Replaces naive keyword matching with vector similarity search over code chunks.
"""
from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .vector_memory import VectorMemory

logger = logging.getLogger(__name__)


@dataclass
class CodeChunk:
    """A chunk of code (function, class, or module-level)."""
    file_path: str
    name: str
    chunk_type: str  # "class", "function", "module"
    code: str
    start_line: int
    end_line: int
    docstring: str = ""


class CodebaseIndex:
    """
    Semantic index of the project codebase.

    Parses Python files into function/class chunks, embeds them,
    and provides meaning-based code retrieval.
    """

    def __init__(
        self,
        project_root: str | Path,
        vector_memory: Optional[VectorMemory] = None,
    ) -> None:
        self.root = Path(project_root).resolve()
        self._vm = vector_memory

        # Use existing VectorMemory's ChromaDB client, or create own
        if self._vm is None:
            self._vm = VectorMemory()

        self._collection = self._vm.client.get_or_create_collection(
            name="awos_codebase",
            metadata={"hnsw:space": "cosine"},
        )

        # Track file hashes for incremental updates
        self._file_hashes: dict[str, str] = {}

    # ── Core API ──────────────────────────────────────────────────────

    def index_project(self, force: bool = False) -> dict[str, Any]:
        """Index all Python files in the project. Returns stats."""
        py_files = list(self._find_python_files())
        indexed = 0
        updated = 0
        unchanged = 0

        for fpath in py_files:
            current_hash = self._hash_file(fpath)
            rel_path = str(fpath.relative_to(self.root))

            if not force and self._file_hashes.get(rel_path) == current_hash:
                unchanged += 1
                continue

            # Remove old chunks for this file
            self._remove_file_chunks(rel_path)

            # Parse and index
            chunks = self._parse_file(fpath)
            for chunk in chunks:
                self._index_chunk(chunk, rel_path)

            self._file_hashes[rel_path] = current_hash
            if current_hash:
                updated += 1
            indexed += len(chunks)

        logger.info(
            "CodebaseIndex: %d files, %d chunks indexed (%d unchanged)",
            len(py_files), indexed, unchanged,
        )
        return {
            "files": len(py_files),
            "chunks": indexed,
            "updated": updated,
            "unchanged": unchanged,
        }

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """Find code chunks semantically similar to query."""
        if self._collection.count() == 0:
            return []

        query_embedding = self._vm._embed(query)
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        output = []
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i]
            output.append({
                "file": meta.get("file_path", ""),
                "name": meta.get("chunk_name", ""),
                "type": meta.get("chunk_type", ""),
                "lines": f"{meta.get('start_line', 0)}-{meta.get('end_line', 0)}",
                "code": results["documents"][0][i],
                "distance": results["distances"][0][i],
            })
        return output

    def get_context_for_query(self, query: str, max_chunks: int = 3) -> str:
        """Return formatted code context for injection into prompts."""
        results = self.search(query, n_results=max_chunks)
        if not results:
            return "(no relevant code found)"

        parts = []
        for r in results:
            parts.append(
                f"# {r['file']}::{r['name']} ({r['type']}, lines {r['lines']})\n"
                f"{r['code'][:800]}"
            )
        return "\n\n".join(parts)

    def stats(self) -> dict[str, Any]:
        return {
            "files": len(self._file_hashes),
            "chunks": self._collection.count(),
            "project_root": str(self.root),
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _find_python_files(self) -> list[Path]:
        """Find all Python files under project root, excluding common ignores."""
        files = []
        ignore = {".git", "__pycache__", ".venv", "venv", "node_modules", ".awos"}
        for fpath in self.root.rglob("*.py"):
            if any(part in ignore for part in fpath.parts):
                continue
            files.append(fpath)
        return files

    def _hash_file(self, fpath: Path) -> str:
        """MD5 hash of file contents for change detection."""
        try:
            return hashlib.md5(fpath.read_bytes()).hexdigest()
        except Exception:
            return ""

    def _remove_file_chunks(self, rel_path: str) -> None:
        """Delete all chunks belonging to a file."""
        try:
            # ChromaDB where filter for metadata
            self._collection.delete(where={"file_path": rel_path})
        except Exception:
            pass

    def _parse_file(self, fpath: Path) -> list[CodeChunk]:
        """Parse a Python file into chunks."""
        try:
            source = fpath.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
        except SyntaxError:
            logger.warning("Syntax error in %s, skipping", fpath)
            return []
        except Exception as e:
            logger.warning("Failed to parse %s: %s", fpath, e)
            return []

        lines = source.splitlines()
        chunks: list[CodeChunk] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.append(self._extract_chunk(fpath, node, lines, "function"))
            elif isinstance(node, ast.ClassDef):
                chunks.append(self._extract_chunk(fpath, node, lines, "class"))
                # Also index class methods
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        chunks.append(self._extract_chunk(fpath, item, lines, "method"))

        # If no top-level chunks, index whole file as one module chunk
        if not chunks:
            chunks.append(CodeChunk(
                file_path=str(fpath),
                name=fpath.stem,
                chunk_type="module",
                code="\n".join(lines[:50]),
                start_line=1,
                end_line=min(50, len(lines)),
            ))

        return chunks

    def _extract_chunk(
        self,
        fpath: Path,
        node: ast.AST,
        lines: list[str],
        chunk_type: str,
    ) -> CodeChunk:
        """Extract a code chunk from AST node."""
        start = node.lineno - 1 if node.lineno else 0
        end = node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else start + 1
        code = "\n".join(lines[start:end])

        name = getattr(node, "name", "unknown")
        docstring = ast.get_docstring(node) or ""  # type: ignore[arg-type]

        return CodeChunk(
            file_path=str(fpath),
            name=name,
            chunk_type=chunk_type,
            code=code,
            start_line=start + 1,
            end_line=end,
            docstring=docstring,
        )

    def _index_chunk(self, chunk: CodeChunk, rel_path: str) -> None:
        """Store a single chunk in the index."""
        doc_id = f"{rel_path}::{chunk.chunk_type}::{chunk.name}::{chunk.start_line}"

        # Include docstring in embedding text for better semantic capture
        text_to_embed = f"{chunk.name}\n{chunk.docstring}\n{chunk.code}"
        embedding = self._vm._embed(text_to_embed)

        self._collection.add(
            embeddings=[embedding],
            documents=[chunk.code],
            metadatas=[{
                "file_path": rel_path,
                "chunk_name": chunk.name,
                "chunk_type": chunk.chunk_type,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
            }],
            ids=[doc_id],
        )
