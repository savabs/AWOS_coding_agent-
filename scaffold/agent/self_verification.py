"""
Self-Verification Engine — Phase 7.

In-memory pre-flight verifier. Simulates SEARCH/REPLACE and runs fast checks
(AST parse, import resolution, contract compliance) before the expensive
Verifier touches disk.

Cost: FREE (pure Python). Reduces disk-apply → rollback cycles.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Shared contract helper (import handles both package and direct run)
try:
    from .verifier import check_contract_compliance
except ImportError:
    from verifier import check_contract_compliance


# ── Python stdlib module names (for import resolution) ────────────────────────
_STDLIB_NAMES = {
    "abc", "aifc", "argparse", "array", "ast", "asyncio", "atexit", "audioop",
    "base64", "bdb", "binascii", "binhex", "bisect", "builtins", "bz2",
    "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs",
    "codeop", "collections", "colorsys", "compileall", "concurrent", "configparser",
    "contextlib", "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses",
    "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis", "distutils",
    "doctest",
    "email", "encodings", "enum", "errno",
    "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "fractions",
    "ftplib", "functools",
    "gc", "getopt", "getpass", "gettext", "glob", "graphlib", "grp", "gzip",
    "hashlib", "heapq", "hmac", "html", "http",
    "idlelib", "imaplib", "imghdr", "imp", "importlib", "inspect", "io",
    "ipaddress", "itertools",
    "json",
    "keyword",
    "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap", "modulefinder",
    "multiprocessing",
    "netrc", "nis", "nntplib", "numbers",
    "operator", "optparse", "os", "ossaudiodev",
    "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile", "pstats",
    "pty", "pwd", "py_compile", "pyclbr", "pydoc",
    "queue",
    "random", "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex", "shutil",
    "signal", "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver",
    "spwd", "sqlite3", "ssl", "stat", "statistics", "string", "stringprep",
    "struct", "subprocess", "sunau", "symtable", "sys", "sysconfig",
    "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "trace", "traceback", "tracemalloc", "tty", "turtle", "turtledemo", "types",
    "typing",
    "unicodedata", "unittest", "urllib", "uu", "uuid",
    "venv",
    "warnings", "wave", "weakref", "webbrowser", "winreg", "winsound",
    "wsgiref",
    "xdrlib", "xml", "xmlrpc",
    "zipapp", "zipfile", "zipimport", "zlib",
}


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class VerificationResult:
    """Outcome of a self-verification run."""

    passed: bool
    stage: str              # which check stage failed, or "all" if passed
    errors: list[str]
    error_context: str      # formatted for worker retry prompt injection


# ── SelfVerificationEngine ───────────────────────────────────────────────────


class SelfVerificationEngine:
    """
    In-memory pre-flight verifier.

    Simulates SEARCH/REPLACE against original content and runs a hierarchy
    of fast checks (fail-fast) before the Verifier writes to disk.
    """

    def verify(
        self,
        original_content: str,
        search_replace: dict,
        file_path: str,
        task_spec: Optional[dict] = None,
    ) -> VerificationResult:
        """
        Run all check stages. Return on first failure (fail fast).

        Args:
            original_content: full text of the file before change
            search_replace:   {"search": "...", "replace": "..."}
            file_path:        target file path (for error messages)
            task_spec:        optional Planner task dict (for contract checks)

        Returns:
            VerificationResult with passed, stage, errors, error_context
        """
        search_text = search_replace.get("search", "")
        replace_text = search_replace.get("replace", "")

        # Stage 1: Search string exists
        if not search_text:
            return VerificationResult(
                passed=False,
                stage="search_exists",
                errors=["Empty search text"],
                error_context="Self-verification: SEARCH string is empty.",
            )

        if search_text not in original_content:
            norm_search = " ".join(search_text.split())
            norm_content = " ".join(original_content.split())
            if norm_search not in norm_content:
                return VerificationResult(
                    passed=False,
                    stage="search_exists",
                    errors=["SEARCH string not found in file"],
                    error_context=(
                        "Self-verification: SEARCH string not found in the file. "
                        "It may have been hallucinated or the file may have changed."
                    ),
                )

        # Stage 2: Unique match (warn only — Verifier handles ambiguous replace)
        match_count = original_content.count(search_text)
        if match_count > 1:
            logger.debug(
                "[SelfVerify] SEARCH appears %d times in %s — ambiguous replace",
                match_count, file_path,
            )

        # Simulate the change in-memory
        modified_content = original_content.replace(search_text, replace_text, 1)

        # Stage 3: AST parse (Python only)
        ext = Path(file_path).suffix.lower()
        if ext == ".py":
            try:
                ast.parse(modified_content, filename=file_path)
            except SyntaxError as exc:
                return VerificationResult(
                    passed=False,
                    stage="syntax",
                    errors=[f"SyntaxError at line {exc.lineno}: {exc.msg}"],
                    error_context=(
                        f"Self-verification: Syntax error after applying change:\n"
                        f"  Line {exc.lineno}: {exc.text}\n"
                        f"  {' ' * (exc.offset - 1)}^\n"
                        f"Fix the syntax before returning."
                    ),
                )

        # Stage 4: Import resolution (Python only)
        if ext == ".py":
            import_errors = self._check_imports(modified_content, file_path)
            # Only treat stdlib/local missing imports as hard errors;
            # third-party missing imports are warnings.
            hard_errors = [e for e in import_errors if e.get("severity") == "error"]
            if hard_errors:
                msgs = [e["msg"] for e in hard_errors]
                return VerificationResult(
                    passed=False,
                    stage="import",
                    errors=msgs,
                    error_context=(
                        "Self-verification: Unresolvable imports detected:\n"
                        + "\n".join(f"  • {m}" for m in msgs)
                        + "\nFix imports before returning."
                    ),
                )

        # Stage 5: Contract compliance
        contract_errors = check_contract_compliance(modified_content, task_spec)
        if contract_errors:
            return VerificationResult(
                passed=False,
                stage="contract",
                errors=contract_errors,
                error_context=(
                    "Self-verification: Contract violation(s):\n"
                    + "\n".join(f"  • {e}" for e in contract_errors)
                    + "\nFix the code to match the specification exactly."
                ),
            )

        return VerificationResult(
            passed=True,
            stage="all",
            errors=[],
            error_context="",
        )

    # ── Stage helpers ─────────────────────────────────────────────────────────

    def _check_imports(self, content: str, file_path: str) -> list[dict]:
        """
        Check that top-level imports can be resolved.

        Returns list of dicts: {"severity": "error"|"warning", "msg": str}
        """
        errors: list[dict] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return errors  # syntax already caught in stage 3

        # Determine the project root (heuristic: look for src/ or first parent with __init__.py)
        proj_root = self._find_project_root(file_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    if not self._is_importable(mod, proj_root):
                        if mod in _STDLIB_NAMES:
                            errors.append(
                                {"severity": "error", "msg": f"Cannot resolve stdlib import: '{mod}'"}
                            )
                        else:
                            # Third-party — warning only (may be optional dep)
                            errors.append(
                                {"severity": "warning", "msg": f"Cannot resolve import: '{mod}' (third-party?)"}
                            )
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                if mod and not self._is_importable(mod, proj_root):
                    if mod in _STDLIB_NAMES:
                        errors.append(
                            {"severity": "error", "msg": f"Cannot resolve stdlib import: '{mod}'"}
                        )
                    else:
                        errors.append(
                            {"severity": "warning", "msg": f"Cannot resolve import: '{mod}' (third-party?)"}
                        )
        return errors

    def _find_project_root(self, file_path: str) -> Optional[Path]:
        """Heuristic: walk up from file looking for a directory with __init__.py or src/."""
        p = Path(file_path).resolve()
        for parent in p.parents:
            if (parent / "__init__.py").exists() or (parent / "src").is_dir():
                return parent
            # Also accept top-level .git as project boundary
            if (parent / ".git").is_dir():
                return parent
        return None

    def _is_importable(self, mod: str, proj_root: Optional[Path]) -> bool:
        """Check if a top-level module name is importable (stdlib, installed, or local)."""
        if mod in _STDLIB_NAMES:
            # Double-check via importlib (some stdlib modules are platform-specific)
            if importlib.util.find_spec(mod) is not None:
                return True
        else:
            if importlib.util.find_spec(mod) is not None:
                return True
        # Check if it's a local module in the project
        if proj_root is not None:
            candidate = proj_root / f"{mod}.py"
            if candidate.exists():
                return True
            candidate_pkg = proj_root / mod / "__init__.py"
            if candidate_pkg.exists():
                return True
            # Also check src/ subdir
            src = proj_root / "src"
            if src.is_dir():
                if (src / f"{mod}.py").exists() or (src / mod / "__init__.py").exists():
                    return True
        return False
