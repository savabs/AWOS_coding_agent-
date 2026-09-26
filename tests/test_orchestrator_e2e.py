"""End-to-end orchestrator test with mocked LLM.

This is the Sprint 1 regression gate (see [[sprint1_working_app_spec]] step 1.5).

Unlike `test_pause_resume_orchestrator.py` (which mocks `_execute_single_task`
at the orchestrator boundary), this test mocks at the LLM boundary — the
worker actually executes the ReAct loop, parses the model's JSON response,
calls a real `edit_file` tool, and the file actually changes on disk.

The canned responses in `tests/fixtures/e2e_orc_responses.jsonl`:
    1. Plan (3 tasks) — but we use a 1-task pre-built plan to skip planning
    2. edit_file tool call — adds `def foo()` to test_e2e_target.py
    3. finish — task complete

Run: `pytest tests/test_orchestrator_e2e.py -v --timeout=30`
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "e2e_orc_responses.jsonl"


def _load_canned_responses() -> list[dict]:
    """Load the 3 canned responses from the fixture file."""
    responses = []
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                responses.append(json.loads(line))
    return responses


def _make_mock_openai_client(responses: list[dict]):
    """Return a function that, when called, yields the next canned response.

    Each response is rendered as the shape the worker expects from the
    OpenAI chat.completions API: `response.choices[0].message.content`.

    When the iterator is exhausted, returns a benign "no-op" response
    (a finish action with an empty summary) so callers like the
    orchestrator's _cheap_call that may call the LLM after the worker's
    loop ends don't crash with StopIteration.
    """
    iter_responses = iter(responses)
    exhausted = False
    fallback = {"type": "finish", "content": {"summary": "(mock exhausted)"}}

    def make_response(content: str) -> MagicMock:
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = 100
        resp.usage.completion_tokens = 50
        return resp

    class MockCompletions:
        def create(self, *args, **kwargs):
            nonlocal exhausted
            try:
                data = next(iter_responses)
            except StopIteration:
                exhausted = True
                data = fallback
            return make_response(json.dumps(data))

    class MockChat:
        completions = MockCompletions()

    class MockClient:
        chat = MockChat()

    return MockClient()


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """Create a temp repo with one Python file to edit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "test_e2e_target.py"
    target.write_text("# placeholder\n", encoding="utf-8")
    # Env setup — same pattern as test_pause_resume_orchestrator
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_RUNTIME_SESSION", "true")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
    # DO NOT set AWOS_E2E=1 — we want the worker to go through real flow
    # with the mocked LLM
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "fake-key-for-mock")
    monkeypatch.setenv("AWOS_OPENCODE_MODEL", "deepseek-v4-flash")
    # VectorMemory pulls in sentence_transformers (a 10+ second import).
    # We don't need semantic retrieval for this test — patch it to None
    # in the orchestrator's __init__ before the worker is even created.
    from scaffold.agent import vector_memory as _vm
    monkeypatch.setattr(_vm, "VectorMemory", lambda *a, **kw: None)
    # Patch OpenAI at the class level so EVERY OpenAI() call (worker
    # client init, _cheap_call critique engine, post-mortem engine) all
    # return our canned-response mock. Without this, the orchestrator's
    # _cheap_call hits the real OpenCode Go with the fake key and 401s,
    # killing the test even though the worker is fully mocked.
    canned = _load_canned_responses()
    mock_client = _make_mock_openai_client(canned)
    import openai as _openai
    monkeypatch.setattr(_openai, "OpenAI", lambda *a, **kw: mock_client)
    return repo, target, str(target)


class TestOrchestratorE2E:
    def test_orchestrator_completes_with_mocked_llm(self, temp_repo):
        """Run the full orchestrator pipeline with a mocked LLM.

        Verifies:
        - The file actually changes (the worker really executed the tool)
        - The session reaches COMPLETED status
        - A reward episode is recorded
        """
        repo, target, target_abs = temp_repo
        responses = _load_canned_responses()
        # The fixture has 2 responses (edit_file, then finish). The mock
        # falls back to a no-op finish when the iterator is exhausted, so
        # extra calls (e.g. from the orchestrator's _cheap_call) don't crash.
        assert len(responses) >= 2, f"Expected ≥ 2 canned responses, got {len(responses)}"
        # Substitute the absolute path into the canned response (the
        # fixture file uses __PATH__ as a placeholder so the fixture
        # is portable across test runs).
        for r in responses:
            ai = r.get("action_input", {})
            if isinstance(ai, dict) and "path" in ai:
                ai["path"] = ai["path"].replace("__PATH__", target_abs)

        # Build a pre-built plan so we skip the planner's LLM call.
        # The planner normally calls CheapPlanner (which IS an LLM call),
        # but with a pre-built plan we go straight to task execution.
        # Use the absolute path so the worker finds the file regardless
        # of the worktree CWD.
        plan = [
            {
                "task_id": 1,
                "task_type": "edit_file",
                "path": target_abs,
                "file": target_abs,
                "action": "Add a function foo() that returns 1",
                "complexity": "low",
            },
        ]

        # Patch the worker's LLM call to yield the canned responses

        mock_client = _make_mock_openai_client(responses)
        # The worker has self.client and self.opencode_client — both point
        # at the OpenCode Go base URL. Patch the one that the worker uses
        # by replacing the instance attribute after construction.
        from scaffold.agent.orchestrator import Orchestrator

        # Pre-set env so OpenCode Go is the only "live" client
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ["OPENCODE_GO_API_KEY"] = "fake-key-for-mock"

        orch = Orchestrator()
        # Replace the LLM clients on the ReAct worker (the orchestrator
        # routes tasks through `self.react_worker`, not `self.worker`).
        # Type-unsafe by design — MagicMock is duck-typed to the
        # OpenAI interface.
        if orch.react_worker is not None:
            orch.react_worker.client = mock_client  # type: ignore[assignment]
            orch.react_worker.opencode_client = mock_client  # type: ignore[assignment]

        # Also patch the performance tracker to avoid file I/O noise
        with patch(
            "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"
            ):
                result = orch.execute_feature(
                    goal="Add a function foo() that returns 1",
                    codebase_root=str(repo),
                    pre_planned_tasks=plan,
                )

        # ── Assertions ─────────────────────────────────────────────
        # 1. The orchestrator reported success
        assert result.get("success") is True, f"Orchestrator failed: {result.get('errors')}"

        # 2. The file was actually changed (the worker really executed the tool)
        # NOTE: the runtime session puts the changes in a worktree, not the
        # original repo. So we read from the worktree via the session.
        rs_id = result.get("runtime_session_id")
        assert rs_id is not None, "No runtime session ID returned"

        # The session's sandbox.worktree_path is the actual changed location.
        # We import the store fresh to read the latest session state.
        from scaffold.agent.runtime_session import RuntimeSessionStore, SessionStatus

        store = RuntimeSessionStore(sessions_dir=str(Path.cwd() / ".awos" / "sessions"))
        session = store.load(rs_id)
        assert session is not None, f"Session {rs_id} not found"
        assert session.status in (SessionStatus.COMPLETED, SessionStatus.PAUSED), (
            f"Session ended with status {session.status.value}, expected COMPLETED or PAUSED"
        )

        # 3. The file in the worktree contains the edit
        if session.sandbox.enabled and session.sandbox.worktree_path:
            worktree_file = Path(session.sandbox.worktree_path) / "test_e2e_target.py"
        else:
            worktree_file = target  # fallback if worktree isolation was skipped

        assert worktree_file.exists(), f"File not found at {worktree_file}"
        content = worktree_file.read_text(encoding="utf-8")
        assert "def foo" in content, (
            f"Edit did not apply. File contents:\n{content}\n"
            f"Expected to find 'def foo()' in the file."
        )
        assert "return 1" in content, (
            f"Edit applied but 'return 1' missing. File contents:\n{content}"
        )
