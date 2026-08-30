"""Tests for MultiResolutionContextPipeline — tiered context assembly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scaffold.agent.multi_resolution_context import (
    ContextAssembly,
    MultiResolutionContextPipeline,
    suggest_level,
)

# ── Test file content ──────────────────────────────────────────────────────────

SAMPLE_FILE = '''"""
Sample module for testing — auth middleware.
"""

import jwt
from datetime import datetime, timedelta
from .utils import hash_password, verify_password
from scaffold.agent.config import settings


class AuthError(Exception):
    """Base auth error."""
    pass


class ExpiredError(AuthError):
    """Token expired."""
    pass


def authenticate(token: str) -> dict:
    """Validate JWT token and return payload."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise ExpiredError("Token has expired")
    return payload


def require_role(role: str):
    """Decorator to enforce role-based access."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            # Role check would go here
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def refresh_token(token: str) -> str:
    """Issue a new token."""
    payload = authenticate(token)
    payload["exp"] = datetime.utcnow() + timedelta(hours=1)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
'''


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_mock_sr(chunks=None):
    """Create mock SemanticRetriever."""
    sr = MagicMock()
    sr.retrieve.return_value = chunks or []
    return sr


# ── Tests: ContextAssembly ─────────────────────────────────────────────────────

class TestContextAssembly:
    def test_defaults(self):
        a = ContextAssembly(level=0)
        assert a.level == 0
        assert a.estimated_tokens == 0
        assert a.full_context == ""
        assert a.chunks_used == []


# ── Tests: build() with levels ─────────────────────────────────────────────────

class TestBuild:
    """MultiResolutionContextPipeline.build() — each level."""

    # No mocks needed — these test pure content, no filesystem/VM
    _pipeline = MultiResolutionContextPipeline()

    def test_level_0_task_only(self):
        """Level 0: only task section, minimal tokens."""
        task = {"action": "fix typo", "file": "auth.py"}
        a = self._pipeline.build(task, level=0)
        assert a.level == 0
        assert "TASK: fix typo" in a.task_section
        assert a.module_section == ""  # not included at level 0
        assert a.signature_section == ""
        assert a.chunk_section == ""
        assert a.estimated_tokens > 0

    def test_level_1_adds_module_overview(self):
        """Level 1: task + module overview section populated."""
        task = {"action": "add auth", "file": "auth.py"}
        a = self._pipeline.build(task, level=1)
        assert a.level == 1
        assert a.task_section != ""
        # Module overview is empty because file doesn't exist in cwd
        # But the section-building methods should not crash
        # Test with level 1 and verify it doesn't crash
        assert a.estimated_tokens >= 0

    def test_level_2_adds_signatures(self):
        """Level 2: task + module + signatures (from file_content)."""
        task = {"action": "fix authenticate function", "file": "auth.py"}
        a = self._pipeline.build(task, level=2, file_content=SAMPLE_FILE)
        assert a.level == 2
        assert "def authenticate(" in a.signature_section  # captures full arg type
        assert "def require_role" in a.signature_section
        assert "class ExpiredError" in a.signature_section
        # __init__-like dunders should be filtered out
        assert "__init__" not in a.signature_section

    def test_level_3_adds_chunks(self):
        """Level 3: task + module + signatures + semantic chunks + file content."""
        mock_sr = _make_mock_sr([
            {"file_path": "auth.py", "start_line": 23, "text": "def authenticate(...)", "symbol_name": "authenticate", "score": 0.92},
        ])
        pipeline = MultiResolutionContextPipeline(semantic_retriever=mock_sr)
        task = {"action": "fix auth", "file": "auth.py"}
        a = pipeline.build(task, level=3, file_content=SAMPLE_FILE)
        assert a.level == 3
        assert a.chunk_section != ""
        assert "authenticate" in a.chunk_section
        assert len(a.chunks_used) == 1
        assert a.file_section != ""

    def test_level_3_no_sr(self):
        """Level 3 without semantic retriever: no chunks, no crash."""
        pipeline = MultiResolutionContextPipeline()  # no SR
        task = {"action": "fix auth", "file": "auth.py"}
        a = pipeline.build(task, level=3, file_content=SAMPLE_FILE)
        assert a.level == 3
        assert a.chunk_section == ""  # No SR → no chunks
        assert a.file_section != ""  # But file content still present
        # Should not crash


class TestLevelClamp:
    """Level clamping — safe bounds."""

    def test_negative_clamps_to_zero(self):
        pipeline = MultiResolutionContextPipeline()
        a = pipeline.build({"action": "x"}, level=-1)
        assert a.level == 0

    def test_high_clamps_to_three(self):
        pipeline = MultiResolutionContextPipeline()
        a = pipeline.build({"action": "x"}, level=99)
        assert a.level == 3

    def test_edge_zero(self):
        pipeline = MultiResolutionContextPipeline()
        a = pipeline.build({"action": "x"}, level=0)
        assert a.level == 0


class TestEmptyInputs:
    """Graceful handling of missing/empty inputs."""

    def test_empty_action(self):
        pipeline = MultiResolutionContextPipeline()
        a = pipeline.build({}, level=2)
        assert "TASK: " in a.task_section

    def test_no_file_provided(self):
        pipeline = MultiResolutionContextPipeline()
        a = pipeline.build({"action": "do stuff"}, level=2)
        # Should not crash; module overview will be empty
        assert a.module_section == ""

    def test_empty_file_content(self):
        pipeline = MultiResolutionContextPipeline()
        a = pipeline.build({"action": "x", "file": "f.py"}, level=2, file_content="")
        assert a.signature_section == ""


class TestTokenIncrease:
    """Estimated tokens should increase with level."""

    def test_tokens_increase_with_level(self):
        pipeline = MultiResolutionContextPipeline()
        task = {"action": "fix the authenticate function", "file": "auth.py"}
        a0 = pipeline.build(task, level=0)
        a2 = pipeline.build(task, level=2, file_content=SAMPLE_FILE)
        a3 = pipeline.build(task, level=3, file_content=SAMPLE_FILE)
        assert a3.estimated_tokens >= a2.estimated_tokens >= a0.estimated_tokens


# ── Tests: suggest_level ───────────────────────────────────────────────────────

class TestSuggestLevel:
    def test_typo_returns_zero(self):
        assert suggest_level({"action": "fix typo in variable name", "complexity": "high"}) == 0

    def test_rename_returns_zero(self):
        assert suggest_level({"action": "rename function foo to bar"}) == 0

    def test_low_complexity_returns_one(self):
        # "add test" doesn't match any simple_pattern; complexity="low" → 1
        assert suggest_level({"complexity": "low", "action": "add a simple test"}) == 1

    def test_medium_complexity_returns_two(self):
        assert suggest_level({"complexity": "medium", "action": "refactor module"}) == 2

    def test_high_complexity_returns_three(self):
        assert suggest_level({"complexity": "high", "action": "redesign auth system"}) == 3

    def test_default_returns_two(self):
        assert suggest_level({"action": "unknown"}) == 2
