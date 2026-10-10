"""
skeleton.py — bounded views of large source files (trick T5).

A large file read whole costs the model its context and, in practice, turns:
the agent loop has spent 26 turns reading parse/__init__.py without editing.
SWE-agent's ACI found a 100-line window beats both the full file (18.0% vs
12.7% resolved) and a 30-line window (14.3%); Agentless localizes better from
a skeleton (class/def signatures) than from full files with ~85% fewer tokens.
Spec: docs/specs/skeleton_viewer.md.

Three pure functions over source text (no I/O, no flags):
  skeleton(text)               line-numbered outline of a Python module
  show_symbol(text, "A.b")     one symbol's full body, line-numbered
  window(text, start, n=100)   n line-numbered lines from `start`

tools/filesystem.py decides when to use them (AWOS_SKELETON_VIEW).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional

#: Lines shown by window() and the per-call cap on explicit ranges.
WINDOW_LINES = 100
#: Longest signature or docstring line kept in a skeleton entry.
_MAX_SIG = 160
_MAX_DOC = 100

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
#: Compound statements whose bodies may hold defs worth listing
#: (`if sys.version_info ...: def f`, `try: import x except: def x`).
_BLOCKS = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.While)


@dataclass
class Symbol:
    """One class or function: where it is and how it is called."""

    qualname: str
    kind: str  # "class" | "def" | "async def"
    first: int  # first line, decorators included
    lineno: int  # the def/class line
    last: int
    signature: str
    decorators: list[tuple[int, str]] = field(default_factory=list)
    doc: str = ""
    depth: int = 0


def numbered(lines: list[str], first: int) -> str:
    """`lines` prefixed cat -n style, numbering from `first`."""
    return "".join(f"{first + i:6}\t{line if line.endswith(chr(10)) else line + chr(10)}"
                   for i, line in enumerate(lines))


def window(text: str, start: int = 1, n: int = WINDOW_LINES) -> str:
    """`n` line-numbered lines of `text` from 1-indexed `start` (clamped)."""
    lines = text.splitlines(keepends=True)
    start = min(max(1, int(start)), max(1, len(lines)))
    return numbered(lines[start - 1:start - 1 + max(1, int(n))], start)


def _clip(s: str, limit: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        bases += [ast.unparse(k) for k in node.keywords]
        return f"class {node.name}" + (f"({', '.join(bases)})" if bases else "") + ":"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    sig = f"{prefix} {node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    return sig + ":"


def _doc_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node, clean=True) or ""
    first = next((ln.strip() for ln in doc.splitlines() if ln.strip()), "")
    return _clip(first, _MAX_DOC)


def _assigned_names(node: ast.AST) -> list[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    names: list[str] = []
    for t in targets:
        for sub in ast.walk(t):
            if isinstance(sub, ast.Name):
                names.append(sub.id)
    return names


def _walk(body: list[ast.stmt], prefix: str, depth: int,
          symbols: list[Symbol], assigns: list[tuple[int, int, list[str]]],
          in_function: bool) -> None:
    """Collect defs (any depth) and assignment names (module/class level only)."""
    for node in body:
        if isinstance(node, _DEFS):
            qual = f"{prefix}{node.name}"
            kind = ("class" if isinstance(node, ast.ClassDef)
                    else "async def" if isinstance(node, ast.AsyncFunctionDef) else "def")
            decos = [(d.lineno, "@" + _clip(ast.unparse(d), _MAX_SIG)) for d in node.decorator_list]
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            symbols.append(Symbol(
                qualname=qual, kind=kind, first=first, lineno=node.lineno,
                last=getattr(node, "end_lineno", node.lineno) or node.lineno,
                signature=_clip(_signature(node), _MAX_SIG), decorators=decos,
                doc=_doc_line(node), depth=depth,
            ))
            _walk(node.body, qual + ".", depth + 1, symbols, assigns,
                  in_function=not isinstance(node, ast.ClassDef))
        elif isinstance(node, _BLOCKS):
            for part in ("body", "orelse", "finalbody"):
                _walk(getattr(node, part, []) or [], prefix, depth, symbols, assigns, in_function)
            for handler in getattr(node, "handlers", []) or []:
                _walk(handler.body, prefix, depth, symbols, assigns, in_function)
        elif not in_function:
            names = _assigned_names(node)
            if names:
                assigns.append((node.lineno, depth, names))


def symbols(text: str) -> list[Symbol]:
    """Every class and function in `text`, in source order. Raises SyntaxError."""
    found: list[Symbol] = []
    _walk(ast.parse(text).body, "", 0, found, [], in_function=False)
    return found


def skeleton(text: str, name: str = "") -> str:
    """
    Line-numbered outline of Python source: module docstring line, assigned
    names, then every class/def (nested, indented) with its decorators, line
    range, signature and first docstring line. Raises SyntaxError.
    """
    tree = ast.parse(text)
    total = len(text.splitlines())
    syms: list[Symbol] = []
    assigns: list[tuple[int, int, list[str]]] = []
    _walk(tree.body, "", 0, syms, assigns, in_function=False)

    rows: list[tuple[int, str]] = []
    doc = _doc_line(tree)
    if doc:
        rows.append((1, f'"""{doc}"""'))
    # Consecutive module-level assignments fold into one row to stay short.
    run: list[str] = []
    run_line = 0
    for lineno, depth, names in assigns:
        if depth:
            continue  # class attributes go under their class below
        if run and lineno - run_line > 3:
            rows.append((run_line, "= " + _clip(", ".join(run), _MAX_SIG)))
            run = []
        if not run:
            run_line = lineno
        run.extend(names)
    if run:
        rows.append((run_line, "= " + _clip(", ".join(run), _MAX_SIG)))
    class_attrs: dict[int, list[str]] = {}
    for s in syms:
        if s.kind == "class":
            attrs = [n for ln, d, ns in assigns if d == s.depth + 1 and s.lineno < ln <= s.last
                     for n in ns]
            if attrs:
                class_attrs[s.lineno] = attrs

    for s in syms:
        pad = "    " * s.depth
        for ln, deco in s.decorators:
            rows.append((ln, pad + deco))
        line = f"{pad}{s.signature}  # L{s.lineno}-{s.last}"
        if s.doc:
            line += f'  "{s.doc}"'
        rows.append((s.lineno, line))
        if s.lineno in class_attrs:
            rows.append((s.lineno, pad + "    = " + _clip(", ".join(class_attrs[s.lineno]), _MAX_SIG)))

    rows.sort(key=lambda r: r[0])
    head = f"SKELETON of {name or 'file'} ({total} lines; bodies elided, L<a>-<b> = line range)\n"
    return head + "".join(f"{ln:6}\t{body}\n" for ln, body in rows)


def find_symbol(text: str, query: str) -> tuple[Optional[Symbol], list[Symbol]]:
    """
    (match, candidates) for a dotted name. An exact qualname wins; otherwise a
    unique suffix match ("method" or "Class.method"). With no single match,
    candidates lists the symbols whose last part matches, for the error text.
    """
    query = query.strip().strip("`'\"").removesuffix("()")
    syms = symbols(text)
    exact = [s for s in syms if s.qualname == query]
    if exact:
        return exact[0], []
    suffix = [s for s in syms if s.qualname.endswith("." + query)]
    if len(suffix) == 1:
        return suffix[0], []
    tail = query.rsplit(".", 1)[-1]
    return None, suffix or [s for s in syms if s.qualname.rsplit(".", 1)[-1] == tail]


def show_symbol(text: str, query: str) -> str:
    """
    The full source of one class/function (decorators included), line-numbered.
    Raises KeyError with a useful message when the name does not resolve.
    """
    match, candidates = find_symbol(text, query)
    if match is None:
        if candidates:
            names = ", ".join(f"{c.qualname} (L{c.lineno})" for c in candidates[:12])
            raise KeyError(f"'{query}' is ambiguous or inexact; candidates: {names}")
        raise KeyError(f"No class or function named '{query}'")
    lines = text.splitlines(keepends=True)
    head = f"{match.kind} {match.qualname}: lines {match.first}-{match.last}\n"
    return head + numbered(lines[match.first - 1:match.last], match.first)
