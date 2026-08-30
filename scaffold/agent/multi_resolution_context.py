"""
MultiResolutionContextPipeline — tiered context assembly for AWOS tasks.

Builds context at 4 levels:
  Level 0 — task description only (~40 tokens)
  Level 1 — + module overview (pre-computed, cached) (~100 tokens)
  Level 2 — + function/class signatures (~300 tokens)
  Level 3 — + semantic code chunks from SemanticRetriever (~1,200 tokens)

Each level is composable: Level N includes all sections from Levels 0..N.
The orchestrator chooses a level based on task complexity.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Regex patterns ─────────────────────────────────────────────────────────────

_RE_IMPORT = re.compile(r"^(?:from\s+\S+\s+)?import\s+", re.MULTILINE)
_RE_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE)
_RE_CLASS = re.compile(r"^\s*class\s+(\w+)", re.MULTILINE)


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class ContextAssembly:
    """Result of context assembly at a given level."""

    level: int
    task_section: str = ""
    module_section: str = ""
    signature_section: str = ""
    chunk_section: str = ""
    history_section: str = ""
    file_section: str = ""
    full_context: str = ""
    estimated_tokens: int = 0
    chunks_used: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ModuleOverview:
    """Pre-computed overview of a source file."""

    file_path: str
    summary: str = ""  # single sentence description
    imports: list[str] = field(default_factory=list)
    top_level_symbols: list[str] = field(default_factory=list)  # def/class names
    dependencies: list[str] = field(default_factory=list)  # other files this imports


# ── Pipeline ───────────────────────────────────────────────────────────────────


class MultiResolutionContextPipeline:
    """Build tiered context for AWOS worker tasks.

    Usage:
        pipeline = MultiResolutionContextPipeline(semantic_retriever=sr)
        assembly = pipeline.build(task, level=2, file_content="...")
        # Pass assembly to worker.execute_task(context_assembly=assembly)
    """

    def __init__(
        self,
        semantic_retriever: Any = None,
        project_root: Optional[str] = None,
    ) -> None:
        self._sr = semantic_retriever
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._module_cache: dict[str, Optional[ModuleOverview]] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def build(
        self,
        task: dict[str, Any],
        level: int = 3,
        file_content: str = "",
    ) -> ContextAssembly:
        """Build context assembly for a task at the specified level.

        Args:
            task: Task dict with keys: action, file, complexity (optional).
            level: 0-3. Clamped to range.
            file_content: Raw file content for the target file (optional).

        Returns:
            ContextAssembly with full_context ready for worker prompt.
        """
        level = max(0, min(level, 3))  # clamp to 0-3
        action = task.get("action", task.get("task", ""))
        file_path = task.get("file", "")

        assembly = ContextAssembly(level=level)
        assembly.task_section = f"TASK: {action}"

        if level >= 1:
            self._add_module_overview(assembly, file_path)
        if level >= 2:
            self._add_signatures(assembly, file_content)
        if level >= 3:
            self._add_chunks(assembly, action)
            if file_content:
                assembly.file_section = self._format_file_section(file_path, file_content)

        self._rebuild_context(assembly)
        return assembly

    # ── Level builders ─────────────────────────────────────────────────────

    def _add_module_overview(self, assembly: ContextAssembly, file_path: str) -> None:
        """Level 1: add module overview (imports + top-level symbols)."""
        if not file_path:
            return

        overview = self._get_module_overview(file_path)
        if overview is None:
            return

        lines = [f"MODULE: {file_path}"]
        if overview.summary:
            lines.append(f"  Summary: {overview.summary}")
        if overview.top_level_symbols:
            lines.append(f"  Exports: {', '.join(overview.top_level_symbols[:15])}")
        if overview.imports:
            # Show first 8 imports
            shown = overview.imports[:8]
            if len(overview.imports) > 8:
                shown.append(f"... ({len(overview.imports) - 8} more)")
            lines.append(f"  Imports: {', '.join(shown)}")
        assembly.module_section = "\n".join(lines)

    @staticmethod
    def _add_signatures(assembly: ContextAssembly, file_content: str) -> None:
        """Level 2: add function/class signatures extracted from file content."""
        if not file_content:
            return

        defs: list[str] = []
        classes: list[str] = []

        for m in _RE_DEF.finditer(file_content):
            name = m.group(1)
            # Skip dunder methods
            if name.startswith("_") and name.endswith("_"):
                continue
            args = m.group(2).strip()
            defs.append(f"def {name}({args})" if args else f"def {name}()")

        for m in _RE_CLASS.finditer(file_content):
            classes.append(f"class {m.group(1)}")

        if not defs and not classes:
            return

        lines = ["RELEVANT SYMBOLS:"]
        for c in classes[:5]:
            lines.append(f"  {c}")
        for d in defs[:10]:
            lines.append(f"  {d}")

        total = len(defs) + len(classes)
        shown = len(classes[:5]) + len(defs[:10])
        if total > shown:
            lines.append(f"  ... ({total - shown} more symbols)")
        assembly.signature_section = "\n".join(lines)

    def _add_chunks(self, assembly: ContextAssembly, action: str) -> None:
        """Level 3: add semantic code chunks from SemanticRetriever."""
        if self._sr is None or not action:
            return

        try:
            chunks = self._sr.retrieve(action, top_k=3)
        except Exception as exc:
            logger.warning("[MultiResContext] chunk retrieval failed: %s", exc)
            return

        if not chunks:
            return

        lines = ["RELEVANT CODE:"]
        for c in chunks:
            fpath = c.get("file_path", "?")
            line = c.get("start_line", "?")
            sym = c.get("symbol_name", "")
            text = c.get("text", "")
            # Cap text at 600 chars per chunk
            text = text[:600]
            tag = f"-- {fpath}:{line}"
            if sym:
                tag += f" ({sym})"
            lines.append(tag)
            lines.append(text)
            lines.append("")
        assembly.chunk_section = "\n".join(lines)
        assembly.chunks_used = chunks

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_module_overview(self, file_path: str) -> Optional[ModuleOverview]:
        """Get or build a ModuleOverview for a file. Results are cached."""
        if file_path in self._module_cache:
            return self._module_cache[file_path]

        full_path = self._project_root / file_path
        if not full_path.exists():
            self._module_cache[file_path] = None
            return None

        try:
            content = full_path.read_text(errors="ignore")
        except Exception:
            self._module_cache[file_path] = None
            return None

        # Extract imports
        imports: list[str] = []
        deps: list[str] = []
        for m in _RE_IMPORT.finditer(content):
            line = m.group().strip().rstrip("#").strip()
            imports.append(line)
            # Track local deps (from .module import or import project.module)
            if line.startswith("from .") or "scaffold" in line or "agent" in line:
                match = re.search(r"from\s+([\w.]+)|import\s+([\w.]+)", line)
                if match:
                    dep = match.group(1) or match.group(2)
                    if dep:
                        deps.append(dep)

        # Extract top-level symbols
        symbols: list[str] = []
        for m in _RE_DEF.finditer(content):
            name = m.group(1)
            if name.startswith("_"):
                continue  # skip private
            symbols.append(f"def {name}(...)")
        for m in _RE_CLASS.finditer(content):
            symbols.append(f"class {m.group(1)}")

        # Build summary
        summary_parts: list[str] = []
        if symbols:
            summary_parts.append(f"{len(symbols)} top-level symbols")
        if imports:
            summary_parts.append(f"{len(imports)} imports")

        overview = ModuleOverview(
            file_path=file_path,
            summary="; ".join(summary_parts) if summary_parts else "Python module",
            imports=imports[:20],
            top_level_symbols=symbols[:20],
            dependencies=deps[:10],
        )
        self._module_cache[file_path] = overview
        return overview

    @staticmethod
    def _format_file_section(file_path: str, content: str) -> str:
        """Format file content for context, truncated to 2000 chars."""
        truncated = content[:2000]
        if len(content) > 2000:
            truncated += f"\n... [truncated {len(content) - 2000} chars]"
        return f"FILE: {file_path}\n\n{truncated}"

    def _rebuild_context(self, assembly: ContextAssembly) -> None:
        """Rebuild full_context and estimated_tokens from sections."""
        sections = [
            assembly.task_section,
            assembly.module_section,
            assembly.signature_section,
            assembly.chunk_section,
            assembly.history_section,
            assembly.file_section,
        ]
        assembly.full_context = "\n\n".join(s for s in sections if s)
        assembly.estimated_tokens = len(assembly.full_context) // 4


# ── Standalone helper ──────────────────────────────────────────────────────────


def suggest_level(task: dict[str, Any]) -> int:
    """Suggest a context level based on task complexity hints.

    Returns 0-3. This is a simple heuristic; future work replaces it
    with a ContextStrategyBandit (LinUCB).
    """
    action = task.get("action", task.get("task", "")).lower()
    complexity = task.get("complexity", "medium")

    # Pattern-based shortcuts
    simple_patterns = ["typo", "rename", "variable name", "import", "comment",
                        "docstring", "logging", "print"]
    if any(p in action for p in simple_patterns):
        return 0

    # Complexity-based
    if complexity == "low":
        return 1
    if complexity == "medium":
        return 2
    if complexity == "high":
        return 3

    # Default: semantic chunks
    return 2
