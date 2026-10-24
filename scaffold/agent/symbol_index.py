"""
SymbolIndex: Cross-file symbol extraction and dependency graph.

Phase 3 of AWOS — gives the Worker real cross-file awareness:
  - What functions/classes are in each file
  - Which files import each other (import graph)
  - API signatures from related files to prevent hallucination

Strategy:
  - Uses tree-sitter-python (fast, accurate) when available
  - Falls back to stdlib `ast` (always available)
  - Lazy: only parses files when needed, caches results
"""

import ast
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

# ── Optional tree-sitter (fast path) ─────────────────────────────────────────
_TS_AVAILABLE = False
_TS_PARSER = None

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import tree_sitter_python as _tspython
        from tree_sitter import Language, Parser as _TSParser
        _PY_LANG = Language(_tspython.language())
        _TS_PARSER = _TSParser(_PY_LANG)
        _TS_AVAILABLE = True
except Exception:
    pass


_SKIP_INDEX_PARTS = frozenset({"__pycache__", "venv", "site-packages"})


def should_index_py_file(py_path: Path, root: Path) -> bool:
    """Return True if py_path should be indexed under codebase root.

    Uses path parts relative to root so worktrees under `.awos/worktrees/` are
    not excluded by the `.awos` segment in the absolute path.
    """
    try:
        rel = py_path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    if not rel.parts or rel.suffix != ".py":
        return False
    return not any(
        part.startswith(".") or part in _SKIP_INDEX_PARTS for part in rel.parts
    )


@dataclass
class FileSymbols:
    """All symbols extracted from one file."""
    file_path: str
    classes: List[str] = field(default_factory=list)       # ["ClassName"]
    functions: List[str] = field(default_factory=list)     # ["func_name"]
    signatures: Dict[str, str] = field(default_factory=dict)  # name → signature line
    imports: List[str] = field(default_factory=list)       # ["os", "pathlib", ...]
    from_imports: Dict[str, List[str]] = field(default_factory=dict)  # "module" → ["names"]


class SymbolIndex:
    """
    Builds and queries a project-wide symbol and dependency index.

    Usage:
        idx = SymbolIndex(codebase_root)
        idx.build()                               # scan all .py files
        ctx = idx.get_context_for_task(file, action)  # worker-ready string
    """

    def __init__(self, codebase_root: str):
        self.root = Path(codebase_root)
        self._cache: Dict[str, FileSymbols] = {}     # path → FileSymbols
        self._dep_graph: Dict[str, Set[str]] = {}    # file → {files it imports}
        self._rev_graph: Dict[str, Set[str]] = {}    # file → {files that import it}
        self._built = False

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self, max_files: int = 80):
        """Scan the codebase and populate the index. Caps at max_files for speed."""
        py_files = sorted(self.root.rglob("*.py"))
        py_files = [p for p in py_files if should_index_py_file(p, self.root)][
            :max_files
        ]

        for path in py_files:
            self._parse_file(str(path))

        self._build_dep_graph()
        self._built = True

    def _parse_file(self, file_path: str) -> FileSymbols:
        """Parse one file; cache and return FileSymbols."""
        if file_path in self._cache:
            return self._cache[file_path]

        try:
            source = Path(file_path).read_text(errors="ignore")
        except Exception:
            syms = FileSymbols(file_path=file_path)
            self._cache[file_path] = syms
            return syms

        if _TS_AVAILABLE:
            syms = self._parse_ts(file_path, source)
        else:
            syms = self._parse_ast(file_path, source)

        self._cache[file_path] = syms
        return syms

    def _parse_ts(self, file_path: str, source: str) -> FileSymbols:
        """Parse using tree-sitter (accurate signatures)."""
        syms = FileSymbols(file_path=file_path)
        lines = source.splitlines()

        try:
            tree = _TS_PARSER.parse(source.encode("utf-8"))

            def walk(node, class_ctx=None):
                t = node.type

                if t == "class_definition":
                    name = _node_name(node)
                    if name:
                        syms.classes.append(name)
                        sig = lines[node.start_point[0]] if node.start_point[0] < len(lines) else ""
                        syms.signatures[name] = sig.strip()
                    for child in node.children:
                        walk(child, class_ctx=name)

                elif t == "function_definition":
                    name = _node_name(node)
                    if name:
                        if class_ctx:
                            qualified = f"{class_ctx}.{name}"
                        else:
                            qualified = name
                        syms.functions.append(qualified)
                        sig = lines[node.start_point[0]] if node.start_point[0] < len(lines) else ""
                        syms.signatures[qualified] = sig.strip()
                    for child in node.children:
                        walk(child, class_ctx=class_ctx)

                elif t == "import_statement":
                    for child in node.children:
                        if child.type in ("dotted_name", "identifier"):
                            syms.imports.append(child.text.decode("utf-8"))

                elif t == "import_from_statement":
                    module = ""
                    names = []
                    past_import_kw = False
                    for child in node.children:
                        ct = child.type
                        txt = child.text.decode("utf-8") if child.text else ""
                        if txt == "import":
                            past_import_kw = True
                        elif not past_import_kw:
                            # Before 'import' keyword — this is the module name
                            if ct in ("dotted_name", "relative_import", "identifier"):
                                module = txt.lstrip(".")
                        else:
                            # After 'import' keyword — these are the imported names
                            if ct == "import_from_as_names":
                                for nc in child.children:
                                    if nc.type in ("identifier", "dotted_name"):
                                        names.append(nc.text.decode("utf-8"))
                            elif ct == "aliased_import":
                                for nc in child.children:
                                    if nc.type == "identifier":
                                        names.append(nc.text.decode("utf-8"))
                                        break
                            elif ct == "identifier":
                                names.append(txt)
                    if module:
                        syms.from_imports.setdefault(module, []).extend(names)

                else:
                    for child in node.children:
                        walk(child, class_ctx=class_ctx)

            walk(tree.root_node)
        except Exception:
            # Fallback to ast on any tree-sitter error
            return self._parse_ast(file_path, source)

        return syms

    def _parse_ast(self, file_path: str, source: str) -> FileSymbols:
        """Parse using stdlib ast (fallback)."""
        syms = FileSymbols(file_path=file_path)
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return syms

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                syms.classes.append(node.name)
                if node.lineno <= len(lines):
                    syms.signatures[node.name] = lines[node.lineno - 1].strip()

            elif isinstance(node, ast.FunctionDef):
                syms.functions.append(node.name)
                if node.lineno <= len(lines):
                    syms.signatures[node.name] = lines[node.lineno - 1].strip()

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    syms.imports.append(alias.name)

            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [a.name for a in node.names]
                if mod:
                    syms.from_imports.setdefault(mod, []).extend(names)

        return syms

    # ── Dependency graph ──────────────────────────────────────────────────────

    def _build_dep_graph(self):
        """Map local imports to actual files in the project."""
        file_stems = {Path(p).stem: p for p in self._cache}

        for file_path, syms in self._cache.items():
            deps: Set[str] = set()
            all_imports = list(syms.imports) + list(syms.from_imports.keys())

            for mod in all_imports:
                stem = mod.split(".")[-1]
                if stem in file_stems and file_stems[stem] != file_path:
                    deps.add(file_stems[stem])

            self._dep_graph[file_path] = deps
            for dep in deps:
                self._rev_graph.setdefault(dep, set()).add(file_path)

    def _related_files(self, file_path: str, max_related: int = 3) -> List[str]:
        """Return files that are imported by or import the given file."""
        if not self._built:
            self._parse_file(file_path)
            self._build_dep_graph()

        imports = self._dep_graph.get(file_path, set())
        imported_by = self._rev_graph.get(file_path, set())
        related = list((imports | imported_by) - {file_path})
        return related[:max_related]

    # ── Public API ────────────────────────────────────────────────────────────

    def get_file_symbols(self, file_path: str) -> FileSymbols:
        """Get symbols for a file (parses on demand)."""
        return self._parse_file(file_path)

    def get_context_for_task(self, file_path: str, action: str) -> str:
        """
        Return a compact cross-file context block for the Worker prompt.

        Includes:
          - Target file's own classes/functions with signatures
          - Signatures from directly related files (imports/imported-by)
        """
        lines: List[str] = []

        # Target file symbols
        syms = self._parse_file(file_path)
        rel_path = _rel(file_path, self.root)

        lines.append(f"## Symbols in {rel_path}")
        for cls in syms.classes:
            sig = syms.signatures.get(cls, f"class {cls}")
            lines.append(f"  class {cls}: {sig}")
        for fn in syms.functions:
            sig = syms.signatures.get(fn, f"def {fn}(...)")
            lines.append(f"  def {fn}: {sig}")

        # Related files
        related = self._related_files(file_path)
        if related:
            lines.append("\n## APIs from related files")
            for rel_file in related:
                rpath = _rel(rel_file, self.root)
                rsyms = self._parse_file(rel_file)
                if not rsyms.classes and not rsyms.functions:
                    continue
                lines.append(f"\n### {rpath}")
                for cls in rsyms.classes[:4]:
                    sig = rsyms.signatures.get(cls, f"class {cls}")
                    lines.append(f"  {sig}")
                for fn in rsyms.functions[:6]:
                    sig = rsyms.signatures.get(fn, f"def {fn}(...)")
                    lines.append(f"  {sig}")

        return "\n".join(lines)

    def summary(self) -> str:
        """Human-readable summary for debugging."""
        total_sym = sum(len(s.classes) + len(s.functions) for s in self._cache.values())
        total_deps = sum(len(d) for d in self._dep_graph.values())
        return (
            f"SymbolIndex: {len(self._cache)} files, "
            f"{total_sym} symbols, {total_deps} dependencies"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _node_name(node) -> Optional[str]:
    """Extract name identifier from a tree-sitter node."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return Path(path).name
