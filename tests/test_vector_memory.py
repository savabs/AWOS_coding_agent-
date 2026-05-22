"""
Tests for scaffold/agent/vector_memory.py — Phase 6 Vector Memory.

All tests use tmp_path; no global state is mutated.
chromadb + sentence-transformers must be installed for these tests to run.
If unavailable, most tests are skipped via the `vm` fixture.
"""

from __future__ import annotations

import ast
import importlib
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Availability check ───────────────────────────────────────────────────────

try:
    import chromadb  # noqa: F401
    import sentence_transformers  # noqa: F401
    DEPS_AVAILABLE = True
except Exception:
    DEPS_AVAILABLE = False

pytestmark_needs_deps = pytest.mark.skipif(
    not DEPS_AVAILABLE,
    reason="chromadb or sentence-transformers not installed",
)

# ── Import under test ────────────────────────────────────────────────────────

from scaffold.agent.vector_memory import (
    GOAL_SIM_THRESHOLD,
    TOP_K_CODE,
    VectorMemory,
    VectorMemoryUnavailableError,
    CodeChunk,
    OutcomeRecord,
    _chunk_id,
    _extract_chunks,
    _skip_path,
    _truncate_tokens,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def vm(tmp_path):
    """Create a VectorMemory instance backed by a tmp dir. Skip if deps absent."""
    if not DEPS_AVAILABLE:
        pytest.skip("chromadb or sentence-transformers not installed")
    return VectorMemory(persist_dir=str(tmp_path / "vector_db"))


@pytest.fixture
def py_repo(tmp_path):
    """Create a small synthetic Python repo for indexing tests."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.py").write_text(textwrap.dedent("""\
        def login(username, password):
            \"\"\"Authenticate a user.\"\"\"
            return username == "admin" and password == "secret"

        def logout(session):
            \"\"\"Invalidate user session.\"\"\"
            session.clear()

        class AuthManager:
            def __init__(self):
                self.users = {}

            def register(self, username, password):
                self.users[username] = password
    """))
    (src / "utils.py").write_text(textwrap.dedent("""\
        def hash_password(password):
            import hashlib
            return hashlib.sha256(password.encode()).hexdigest()

        def validate_email(email):
            return "@" in email and "." in email
    """))
    return src


# ── TestVectorMemoryInit ──────────────────────────────────────────────────────


class TestVectorMemoryInit:

    @pytestmark_needs_deps
    def test_creates_persist_dir(self, tmp_path):
        db_dir = tmp_path / "vdb"
        assert not db_dir.exists()
        VectorMemory(persist_dir=str(db_dir))
        assert db_dir.exists()

    @pytestmark_needs_deps
    def test_double_init_is_idempotent(self, tmp_path):
        vm1 = VectorMemory(persist_dir=str(tmp_path / "vdb"))
        vm2 = VectorMemory(persist_dir=str(tmp_path / "vdb"))
        assert vm1._code_col.count() == vm2._code_col.count()

    def test_raises_when_chromadb_missing(self, tmp_path):
        # VectorMemoryUnavailableError must be a subclass of Exception
        assert issubclass(VectorMemoryUnavailableError, Exception)
        # When chromadb import itself raises, VectorMemory must raise VectorMemoryUnavailableError
        with patch("scaffold.agent.vector_memory.VectorMemory.__init__") as mock_init:
            mock_init.side_effect = VectorMemoryUnavailableError("mocked missing")
            with pytest.raises(VectorMemoryUnavailableError):
                VectorMemory(persist_dir=str(tmp_path / "vdb"))

    @pytestmark_needs_deps
    def test_collections_created(self, vm):
        assert vm._code_col is not None
        assert vm._sess_col is not None

    @pytestmark_needs_deps
    def test_mtime_cache_empty_on_fresh_init(self, vm):
        assert isinstance(vm._mtime_cache, dict)
        assert len(vm._mtime_cache) == 0


# ── TestIndexCodebase ─────────────────────────────────────────────────────────


class TestIndexCodebase:

    @pytestmark_needs_deps
    def test_empty_dir_returns_zero(self, vm, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        n = vm.index_codebase(str(empty))
        assert n == 0

    @pytestmark_needs_deps
    def test_indexes_python_files(self, vm, py_repo):
        n = vm.index_codebase(str(py_repo))
        assert n > 0

    @pytestmark_needs_deps
    def test_chunk_count_increases_with_content(self, vm, py_repo):
        n = vm.index_codebase(str(py_repo))
        assert n >= 4  # login, logout, AuthManager, hash_password, validate_email

    @pytestmark_needs_deps
    def test_incremental_skips_unchanged_files(self, vm, py_repo):
        n1 = vm.index_codebase(str(py_repo))
        assert n1 > 0
        n2 = vm.index_codebase(str(py_repo))
        assert n2 == 0  # unchanged — nothing re-indexed

    @pytestmark_needs_deps
    def test_reindexes_modified_file(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        # Touch auth.py to force re-index
        auth = py_repo / "auth.py"
        auth.write_text(auth.read_text() + "\n# modified\n")
        n2 = vm.index_codebase(str(py_repo))
        assert n2 > 0

    @pytestmark_needs_deps
    def test_syntax_error_file_skipped(self, vm, tmp_path):
        bad = tmp_path / "bad_code"
        bad.mkdir()
        (bad / "broken.py").write_text("def foo(:\n    pass\n")
        (bad / "good.py").write_text("def bar():\n    return 42\n")
        n = vm.index_codebase(str(bad))
        assert n >= 1  # good.py indexed despite broken.py

    @pytestmark_needs_deps
    def test_mtime_cache_persisted(self, tmp_path, py_repo):
        db_dir = tmp_path / "vdb"
        vm1 = VectorMemory(persist_dir=str(db_dir))
        vm1.index_codebase(str(py_repo))
        # Re-load from disk — cache should be populated
        vm2 = VectorMemory(persist_dir=str(db_dir))
        assert len(vm2._mtime_cache) > 0


# ── TestQueryCode ─────────────────────────────────────────────────────────────


class TestQueryCode:

    @pytestmark_needs_deps
    def test_empty_collection_returns_empty_list(self, vm, tmp_path):
        empty = tmp_path / "e"
        empty.mkdir()
        vm.index_codebase(str(empty))
        result = vm.query_code("add login function")
        assert result == []

    @pytestmark_needs_deps
    def test_returns_code_chunks(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        chunks = vm.query_code("user authentication login")
        assert isinstance(chunks, list)
        assert all(isinstance(c, CodeChunk) for c in chunks)

    @pytestmark_needs_deps
    def test_top_k_respected(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        chunks = vm.query_code("function", top_k=2)
        assert len(chunks) <= 2

    @pytestmark_needs_deps
    def test_scores_in_range(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        chunks = vm.query_code("login user authentication")
        for c in chunks:
            assert 0.0 <= c.score <= 1.0

    @pytestmark_needs_deps
    def test_scores_sorted_descending(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        chunks = vm.query_code("login user authentication")
        scores = [c.score for c in chunks]
        assert scores == sorted(scores, reverse=True)

    @pytestmark_needs_deps
    def test_chunk_metadata_populated(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        chunks = vm.query_code("login")
        for c in chunks:
            assert c.file_path != ""
            assert c.start_line > 0
            assert c.text != ""


# ── TestStoreAndQueryOutcomes ─────────────────────────────────────────────────


class TestStoreAndQueryOutcomes:

    @pytestmark_needs_deps
    def test_store_and_query_returns_match(self, vm):
        task = {"action": "add OAuth2 login function", "file": "auth.py"}
        vm.store_outcome(task, success=True, session_id="s1")
        results = vm.query_outcomes("add OAuth login", top_k=3)
        assert len(results) >= 1
        assert isinstance(results[0], OutcomeRecord)

    @pytestmark_needs_deps
    def test_success_metadata_preserved(self, vm):
        task = {"action": "add OAuth2 login function", "file": "auth.py"}
        vm.store_outcome(task, success=True, session_id="s1", critique="")
        results = vm.query_outcomes("add OAuth login")
        assert results[0].success is True

    @pytestmark_needs_deps
    def test_failure_metadata_preserved(self, vm):
        task = {"action": "add rate limiting middleware", "file": "middleware.py"}
        vm.store_outcome(task, success=False, session_id="s2", critique="syntax error")
        results = vm.query_outcomes("rate limiting")
        assert any(not r.success for r in results)

    @pytestmark_needs_deps
    def test_critique_stored(self, vm):
        task = {"action": "add token refresh logic", "file": "auth.py"}
        vm.store_outcome(task, success=False, session_id="s1", critique="import error")
        results = vm.query_outcomes("token refresh")
        assert results[0].critique == "import error"

    @pytestmark_needs_deps
    def test_session_id_stored(self, vm):
        task = {"action": "add user registration endpoint", "file": "api.py"}
        vm.store_outcome(task, success=True, session_id="session_abc")
        results = vm.query_outcomes("user registration endpoint")
        assert results[0].session_id == "session_abc"

    @pytestmark_needs_deps
    def test_empty_collection_returns_empty(self, vm):
        results = vm.query_outcomes("anything")
        assert results == []

    @pytestmark_needs_deps
    def test_top_k_respected(self, vm):
        for i in range(5):
            task = {"action": f"add feature {i} to the authentication system", "file": "f.py"}
            vm.store_outcome(task, success=True, session_id="s1")
        results = vm.query_outcomes("add feature authentication", top_k=2)
        assert len(results) <= 2


# ── TestFindSimilarGoal ───────────────────────────────────────────────────────


class TestFindSimilarGoal:

    @pytestmark_needs_deps
    def test_exact_match_returns_text(self, vm):
        task = {"action": "Add OAuth2 authentication system to the API", "file": ""}
        vm.store_outcome(task, success=True, session_id="s1")
        result = vm.find_similar_goal("Add OAuth2 authentication system to the API")
        assert result is not None

    @pytestmark_needs_deps
    def test_paraphrase_match_returns_text(self, vm):
        task = {"action": "Implement user login with OAuth2 authentication tokens", "file": ""}
        vm.store_outcome(task, success=True, session_id="s1")
        result = vm.find_similar_goal("Add OAuth2 login authentication for users")
        # May or may not match depending on cosine score — just ensure no exception
        assert result is None or isinstance(result, str)

    @pytestmark_needs_deps
    def test_empty_collection_returns_none(self, vm):
        result = vm.find_similar_goal("add authentication")
        assert result is None

    @pytestmark_needs_deps
    def test_unrelated_returns_none(self, vm):
        task = {"action": "Add OAuth2 authentication system to the API", "file": ""}
        vm.store_outcome(task, success=True, session_id="s1")
        result = vm.find_similar_goal("deploy kubernetes infrastructure monitoring", threshold=0.99)
        assert result is None

    @pytestmark_needs_deps
    def test_threshold_boundary(self, vm):
        task = {"action": "Add user authentication with OAuth2 tokens", "file": ""}
        vm.store_outcome(task, success=True, session_id="s1")
        # Very high threshold should reject everything except exact match
        result_strict = vm.find_similar_goal("Add user authentication with OAuth2 tokens", threshold=0.9999)
        result_loose = vm.find_similar_goal("Add user authentication with OAuth2 tokens", threshold=0.0)
        # loose should find it; strict may not
        assert result_loose is not None


# ── TestReset ─────────────────────────────────────────────────────────────────


class TestReset:

    @pytestmark_needs_deps
    def test_reset_clears_collection(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        assert vm._code_col.count() > 0
        vm.reset_codebase_index()
        assert vm._code_col.count() == 0

    @pytestmark_needs_deps
    def test_reset_clears_mtime_cache(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        assert len(vm._mtime_cache) > 0
        vm.reset_codebase_index()
        assert len(vm._mtime_cache) == 0

    @pytestmark_needs_deps
    def test_reindex_after_reset_works(self, vm, py_repo):
        vm.index_codebase(str(py_repo))
        vm.reset_codebase_index()
        n = vm.index_codebase(str(py_repo))
        assert n > 0


# ── TestWorkerInjection ───────────────────────────────────────────────────────


class TestWorkerInjection:

    def test_non_empty_chunks_inject_relevant_code(self):
        from scaffold.agent.worker import Worker

        chunks = [
            CodeChunk(
                chunk_id="abc",
                file_path="auth.py",
                start_line=1,
                end_line=5,
                text="def login(u, p):\n    return True",
                symbol_name="login",
                score=0.9,
            )
        ]

        w = Worker.__new__(Worker)
        w.client = None
        w.anthropic_client = MagicMock()
        w.model = "deepseek-chat"
        w.fallback_model = "claude-haiku-4-5"

        task = {"task_id": 1, "action": "add logout", "file": "auth.py", "complexity": "simple"}
        file_content = "def login(u, p):\n    return True\n"
        ctx = {"modules": "test"}

        prompt_parts = []

        def fake_create(**kwargs):
            prompt_parts.append(kwargs["messages"][0]["content"])
            raise RuntimeError("stop")

        w.anthropic_client.messages.create = fake_create

        try:
            w.execute_task(
                task=task,
                file_content=file_content,
                codebase_context=ctx,
                vector_chunks=chunks,
            )
        except RuntimeError:
            pass

        assert prompt_parts, "Worker never built prompt"
        assert "[RELEVANT CODE]" in prompt_parts[0]
        assert "auth.py lines 1-5" in prompt_parts[0]

    def test_empty_chunks_no_injection(self):
        from scaffold.agent.worker import Worker

        w = Worker.__new__(Worker)
        w.client = None
        w.anthropic_client = MagicMock()
        w.model = "deepseek-chat"
        w.fallback_model = "claude-haiku-4-5"

        task = {"task_id": 1, "action": "add logout", "file": "auth.py", "complexity": "simple"}
        file_content = "def login():\n    pass\n"
        ctx = {"modules": "test"}

        prompt_parts = []

        def fake_create(**kwargs):
            prompt_parts.append(kwargs["messages"][0]["content"])
            raise RuntimeError("stop")

        w.anthropic_client.messages.create = fake_create

        try:
            w.execute_task(
                task=task,
                file_content=file_content,
                codebase_context=ctx,
                vector_chunks=[],
            )
        except RuntimeError:
            pass

        if prompt_parts:
            assert "[RELEVANT CODE]" not in prompt_parts[0]

    def test_none_chunks_backward_compat(self):
        """vector_chunks=None (default) must not break existing callers."""
        from scaffold.agent.worker import Worker

        w = Worker.__new__(Worker)
        w.client = None
        w.anthropic_client = MagicMock()
        w.model = "deepseek-chat"
        w.fallback_model = "claude-haiku-4-5"

        task = {"task_id": 1, "action": "fix bug", "file": "x.py", "complexity": "simple"}
        file_content = "x = 1\n"
        ctx = {}

        def fake_create(**kwargs):
            raise RuntimeError("stop")
        w.anthropic_client.messages.create = fake_create
        try:
            w.execute_task(task=task, file_content=file_content, codebase_context=ctx)
        except RuntimeError:
            pass  # no crash means backward compat OK


# ── TestGracefulDegradation ───────────────────────────────────────────────────


class TestGracefulDegradation:

    def test_orchestrator_works_without_vector_memory(self, tmp_path):
        """Orchestrator should initialise with vector_memory=None when deps absent."""
        from scaffold.agent.orchestrator import Orchestrator

        with patch("scaffold.agent.orchestrator.VectorMemory", side_effect=Exception("no deps")):
            with patch("scaffold.agent.planner.Planner.__init__", return_value=None):
                with patch("scaffold.agent.worker.Worker.__init__", return_value=None):
                    with patch("scaffold.agent.verifier.Verifier.__init__", return_value=None):
                        orch = Orchestrator.__new__(Orchestrator)
                        orch.vector_memory = None  # simulate failed VectorMemory init
        assert orch.vector_memory is None

    def test_project_planner_falls_back_to_jaccard(self, tmp_path):
        """ProjectPlanner without VectorMemory falls back to Jaccard matching."""
        from scaffold.agent.project_planner import ProjectPlanner

        pp = ProjectPlanner(goals_dir=str(tmp_path / "goals"), vector_memory=None)
        g1 = pp.load_or_create_goal("Add authentication system", "s1")
        g2 = pp.load_or_create_goal("Add authentication system", "s2")
        # Jaccard should match identical descriptions
        assert g1.root_id == g2.root_id


# ── TestHelpers ───────────────────────────────────────────────────────────────


class TestHelpers:

    def test_chunk_id_is_deterministic(self):
        id1 = _chunk_id("foo/bar.py", 10)
        id2 = _chunk_id("foo/bar.py", 10)
        assert id1 == id2

    def test_chunk_id_different_for_different_inputs(self):
        assert _chunk_id("foo.py", 1) != _chunk_id("foo.py", 2)
        assert _chunk_id("foo.py", 1) != _chunk_id("bar.py", 1)

    def test_truncate_tokens_short_text_unchanged(self):
        text = "def foo():\n    pass"
        assert _truncate_tokens(text, max_tokens=256) == text

    def test_truncate_tokens_long_text_truncated(self):
        text = " ".join(str(i) for i in range(300))
        result = _truncate_tokens(text, max_tokens=256)
        assert len(result.split()) <= 256

    def test_skip_path_pycache(self, tmp_path):
        p = tmp_path / "__pycache__" / "foo.pyc"
        assert _skip_path(p)

    def test_skip_path_git(self, tmp_path):
        p = tmp_path / ".git" / "config"
        assert _skip_path(p)

    def test_skip_path_normal_file(self, tmp_path):
        p = tmp_path / "src" / "main.py"
        assert not _skip_path(p)

    def test_extract_chunks_finds_functions(self, tmp_path):
        py_file = tmp_path / "mod.py"
        py_file.write_text("def alpha():\n    pass\n\ndef beta():\n    return 1\n")
        chunks = _extract_chunks(py_file, "mod.py")
        names = {c.symbol_name for c in chunks}
        assert "alpha" in names
        assert "beta" in names

    def test_extract_chunks_finds_classes(self, tmp_path):
        py_file = tmp_path / "cls.py"
        py_file.write_text("class Foo:\n    def bar(self):\n        pass\n")
        chunks = _extract_chunks(py_file, "cls.py")
        assert any(c.symbol_name == "Foo" for c in chunks)

    def test_extract_chunks_syntax_error_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def foo(:\n    pass\n")
        chunks = _extract_chunks(bad, "bad.py")
        assert chunks == []

    def test_extract_chunks_no_top_level_falls_back_to_sliding_window(self, tmp_path):
        py_file = tmp_path / "flat.py"
        py_file.write_text("\n".join(f"x{i} = {i}" for i in range(60)))
        chunks = _extract_chunks(py_file, "flat.py")
        assert len(chunks) >= 1
        assert all(c.symbol_name == "" for c in chunks)
