"""Tests for ReAct worker, coding tools, and exploration phase."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scaffold.agent.coding_tools import (
    EditFileTool,
    RunTestsTool,
    WorkspaceHFDownloadFileTool,
    build_coding_tool_registry,
    code_runner_enabled,
    format_tool_catalog,
    github_tools_enabled,
    hf_tools_enabled,
    research_tools_enabled,
)
from scaffold.agent.exploration_phase import (
    _extract_keywords,
    enrich_codebase_context,
    run_exploration,
)
from scaffold.agent.react_worker import (
    ReActWorker,
    _compact_messages,
    _static_prefix,
    _summarize_observation,
    react_worker_enabled,
)
from scaffold.agent.tools.base import ToolResult


def test_summarize_run_tests():
    result = ToolResult.ok(
        "long raw output",
        {"passed": 3, "failed": 1, "errors": 0, "pass_rate": 0.75, "no_tests_found": False},
    )
    obs = _summarize_observation("run_tests", result)
    assert obs.startswith("TESTS:")
    assert "3 passed" in obs and "1 failed" in obs


def test_summarize_run_tests_no_tests():
    result = ToolResult.ok("raw", {"no_tests_found": True})
    assert _summarize_observation("run_tests", result) == "TESTS: no tests found"


def test_summarize_grep_caps_lines():
    hits = [{"file": f"a{i}.py", "line": i, "text": f"line {i}"} for i in range(20)]
    result = ToolResult.ok("raw", {"hits": hits})
    obs = _summarize_observation("grep", result)
    assert obs.startswith("GREP hits:")
    assert "+5 more" in obs
    assert obs.count("a") >= 15


def test_summarize_failure_and_fallback():
    assert _summarize_observation("shell", ToolResult.fail("boom")) == "ERROR: boom"
    assert _summarize_observation("fetch_url", ToolResult.ok("some text")) == "some text"


def test_static_prefix_is_invariant_leading_two_messages():
    system = {"role": "system", "content": "stable core"}
    task = {"role": "user", "content": "TASK"}
    messages = [system, task]
    for n in range(1, 9):
        messages.append({"role": "assistant", "content": '{"action":"read_file","action_input":{}}'})
        messages.append({"role": "user", "content": f"OBSERVATION (OK):\nstep {n}"})

    assert _static_prefix(messages) == [system, task]
    # compaction preserves the exact same leading prefix across turns
    assert _static_prefix(_compact_messages(messages, keep_turns=3)) == [system, task]


def test_compact_messages_bounds_history():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "TASK"},
    ]
    for n in range(1, 11):
        messages.append({"role": "assistant", "content": '{"action":"read_file","action_input":{}}'})
        messages.append({"role": "user", "content": f"OBSERVATION (OK):\nstep {n}"})

    compacted = _compact_messages(messages, keep_turns=3)
    assert compacted[0]["role"] == "system"
    assert compacted[1]["content"] == "TASK"
    # system + task + 1 summary + 3 kept turns * 2 messages
    assert len(compacted) == 2 + 1 + 3 * 2
    assert compacted[2]["content"].startswith("Earlier steps (compact)")
    # last turn preserved in full
    assert compacted[-1]["content"] == "OBSERVATION (OK):\nstep 10"


def test_compact_messages_keeps_full_when_under_limit():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": '{"action":"read_file","action_input":{}}'},
        {"role": "user", "content": "OBSERVATION (OK):\nstep 1"},
    ]
    compacted = _compact_messages(messages, keep_turns=3)
    assert compacted == messages


@pytest.fixture
def tmp_project(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "hello.py").write_text(
        'def greet():\n    return "hello"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_react_worker_enabled_default():
    os.environ.pop("AWOS_REACT_WORKER", None)
    assert react_worker_enabled() is True


def test_extract_keywords():
    kws = _extract_keywords("Add calculate_awos_costs function to awos.py stats CLI")
    assert any("calculate" in k.lower() or "awos" in k.lower() for k in kws)


def test_run_exploration(tmp_project):
    result = run_exploration("find greet function in pkg", str(tmp_project))
    assert "exploration_summary" in result
    assert "greet" in result["exploration_summary"].lower() or result["grep_hits"]


def test_enrich_codebase_context(tmp_project):
    exploration = run_exploration("greet hello pkg", str(tmp_project))
    ctx = enrich_codebase_context({"architecture": "test"}, exploration)
    assert ctx.get("exploration")
    assert "files" in ctx


def test_edit_file_search_replace(tmp_project):
    tool = EditFileTool(tmp_project)
    target = "pkg/hello.py"
    result = tool.execute(
        {
            "path": target,
            "old_string": 'return "hello"',
            "new_string": 'return "hello world"',
        }
    )
    assert result.success, result.error
    content = (tmp_project / target).read_text()
    assert "hello world" in content


def test_edit_file_create_new(tmp_project):
    tool = EditFileTool(tmp_project)
    result = tool.execute(
        {
            "path": "pkg/new_module.py",
            "old_string": "",
            "new_string": "X = 1\n",
        }
    )
    assert result.success
    assert (tmp_project / "pkg/new_module.py").exists()


def test_tool_registry_catalog(tmp_project):
    reg = build_coding_tool_registry(tmp_project)
    catalog = format_tool_catalog(reg)
    assert "edit_file" in catalog
    assert "grep" in catalog
    assert "web_search" in catalog
    assert "fetch_url" in catalog
    assert "github_search_code" in catalog
    assert "hf_search_models" in catalog
    assert "run_python" in catalog
    assert reg.get("github_read_file") is not None
    assert reg.get("hf_model_info") is not None
    assert len(reg) >= 19


def test_benchmark_profile_excludes_side_effecting_and_external_tools(tmp_project):
    reg = build_coding_tool_registry(tmp_project, profile="benchmark")

    assert reg.get("read_file") is not None
    assert reg.get("grep") is not None
    assert reg.get("edit_file") is not None
    assert reg.get("write_file") is not None
    assert reg.get("run_tests") is not None
    assert reg.get("finish") is not None
    for blocked in (
        "shell",
        "web_search",
        "fetch_url",
        "github_search_code",
        "hf_search_models",
        "run_python",
    ):
        assert reg.get(blocked) is None

    blocked_result = reg.execute("shell", {"command": "echo should-not-run"})
    assert not blocked_result.success
    assert "Unknown tool" in blocked_result.error


def test_explore_profile_is_read_only(tmp_project):
    reg = build_coding_tool_registry(tmp_project, profile="explore")

    assert reg.get("read_file") is not None
    assert reg.get("grep") is not None
    assert reg.get("edit_file") is None
    assert reg.get("write_file") is None
    assert reg.get("run_tests") is None
    assert reg.get("shell") is None


def test_unknown_agent_profile_fails_closed(tmp_project):
    with pytest.raises(ValueError, match="Unknown agent profile"):
        build_coding_tool_registry(tmp_project, profile="unrestricted")


def test_build_profile_retains_autonomous_tools(tmp_project):
    reg = build_coding_tool_registry(tmp_project, profile="build")

    assert reg.get("shell") is not None
    assert reg.get("edit_file") is not None
    assert reg.get("run_tests") is not None
    assert reg.get("finish") is not None


def test_external_tool_flags_default_on():
    os.environ.pop("AWOS_ENABLE_WEB_TOOLS", None)
    os.environ.pop("AWOS_ENABLE_GITHUB_TOOLS", None)
    os.environ.pop("AWOS_ENABLE_HF_TOOLS", None)
    os.environ.pop("AWOS_ENABLE_CODE_RUNNER", None)
    assert research_tools_enabled() is True
    assert github_tools_enabled() is True
    assert hf_tools_enabled() is True
    assert code_runner_enabled() is True


def test_web_search_dispatchable(tmp_project, monkeypatch):
    """Model registry must expose web_search on the hot path."""
    from scaffold.agent.tools.web_search import WebSearchTool

    def fake_execute(self, args):
        from scaffold.agent.tools.base import ToolResult

        return ToolResult.ok(
            text="1. Example\n   https://example.com\n   snippet",
            data={"results": [{"title": "Example", "url": "https://example.com"}]},
        )

    monkeypatch.setattr(WebSearchTool, "execute", fake_execute)
    reg = build_coding_tool_registry(tmp_project)
    result = reg.execute("web_search", {"query": "pytest fixture tmp_path"})
    assert result.success, result.error
    assert "Example" in result.text


def test_github_search_code_dispatchable(tmp_project, monkeypatch):
    from scaffold.agent.tools.github_tool import GitHubSearchCodeTool

    def fake_execute(self, args):
        from scaffold.agent.tools.base import ToolResult

        return ToolResult.ok(
            text="Code search: 'ToolRegistry' — 1 total\nawos/repo/tools.py",
            data={"query": args["query"], "results": []},
        )

    monkeypatch.setattr(GitHubSearchCodeTool, "execute", fake_execute)
    reg = build_coding_tool_registry(tmp_project)
    result = reg.execute(
        "github_search_code", {"query": "ToolRegistry language:python"}
    )
    assert result.success, result.error


def test_hf_download_blocks_escape(tmp_project):
    tool = WorkspaceHFDownloadFileTool(tmp_project)
    result = tool.execute(
        {
            "repo_id": "org/model",
            "filename": "config.json",
            "save_to": "../../../etc/passwd",
        }
    )
    assert not result.success
    assert "escapes workspace" in result.error.lower()


def test_run_python_blocked_without_env(tmp_project):
    os.environ.pop("ALLOW_CODE_EXEC", None)
    reg = build_coding_tool_registry(tmp_project)
    result = reg.execute("run_python", {"code": "print(1)"})
    assert not result.success
    assert "ALLOW_CODE_EXEC" in result.error


def test_run_tests_blocked_without_env(tmp_project):
    os.environ.pop("AWOS_SAFE_TO_RUN_TESTS", None)
    tool = RunTestsTool(tmp_project)
    result = tool.execute({})
    assert not result.success
    assert "AWOS_SAFE_TO_RUN_TESTS" in result.error


def test_trial_budget_guard_blocks_react_request():
    calls = []
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: calls.append(kwargs),
            )
        )
    )
    worker = ReActWorker.__new__(ReActWorker)
    worker.opencode_client = client
    worker.client = None
    worker.openrouter_client = None
    worker.gemini_client = None
    worker.anthropic_client = None
    worker._dead_providers = set()
    worker.model = "deepseek-v4-pro"
    tracker = SimpleNamespace(
        check_guard=lambda estimated_cost: (False, "cap exhausted")
    )
    spec = SimpleNamespace(
        provider="opencode",
        model_id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        cost_per_req=0.002,
        input_price=0.435,
        output_price=0.87,
    )

    with pytest.raises(RuntimeError, match="Trial budget guard stopped"):
        worker._call_model(
            [{"role": "user", "content": "task"}],
            model_spec=spec,
            tracker=tracker,
        )
    assert calls == []


def test_benchmark_rejects_provider_model_drift(monkeypatch):
    worker = ReActWorker.__new__(ReActWorker)
    worker.opencode_client = object()
    worker._dead_providers = set()
    monkeypatch.setenv("AWOS_BENCHMARK_MODE", "1")
    spec = SimpleNamespace(provider="google", model_id="gemini-test", name="Gemini")

    with pytest.raises(RuntimeError, match="Benchmark provider contract violation"):
        worker._call_model([{"role": "user", "content": "task"}], model_spec=spec)


def test_react_parse_action():
    worker = ReActWorker.__new__(ReActWorker)
    raw = json.dumps(
        {
            "thought": "read file",
            "action": "read_file",
            "action_input": {"path": "a.py"},
        }
    )
    parsed = worker._parse_action(raw)
    assert parsed["action"] == "read_file"


def test_react_execute_mocked(tmp_project):
    """Full ReAct loop with mocked LLM — no API keys required."""
    os.environ["AWOS_SAFE_TO_RUN_TESTS"] = "0"
    os.environ["AWOS_REACT_MAX_TURNS"] = "5"

    worker = ReActWorker.__new__(ReActWorker)
    worker.model = "mock"
    worker._dead_providers = set()

    responses = [
        json.dumps(
            {
                "thought": "grep for greet",
                "action": "grep",
                "action_input": {
                    "pattern": "greet",
                    "root": ".",
                    "file_glob": "*.py",
                    "literal": "true",
                },
            }
        ),
        json.dumps(
            {
                "thought": "update return value",
                "action": "edit_file",
                "action_input": {
                    "path": "pkg/hello.py",
                    "old_string": 'return "hello"',
                    "new_string": 'return "react_ok"',
                },
            }
        ),
        json.dumps(
            {
                "thought": "done",
                "action": "finish",
                "action_input": {"summary": "Updated greet to return react_ok"},
            }
        ),
    ]
    call_iter = iter(responses)

    def fake_call(messages, model_spec=None, tracker=None, **kwargs):
        text = next(call_iter)
        return (
            text,
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": 0.0,
                "total_tokens": 15,
            },
            "mock",
        )

    worker._call_model = fake_call  # type: ignore

    result = worker.execute_task(
        task={
            "action": "Make greet return react_ok in pkg/hello.py",
            "file": "pkg/hello.py",
        },
        file_content=(tmp_project / "pkg/hello.py").read_text(),
        codebase_context={"architecture": "test"},
        project_root=str(tmp_project),
    )

    assert result["success"] is True, result.get("error")
    assert "react_ok" in (tmp_project / "pkg/hello.py").read_text()
    assert result["summary"]
    print("[PROOF] ReAct mock loop: grep → edit_file → finish OK")


def _run_react_with_untracked_shell_edit(tmp_path, *, change_file: bool):
    target = tmp_path / "target.py"
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")

    class FakeRegistry:
        def execute(self, action, action_input):
            if action == "shell" and change_file:
                target.write_text("value = 2\n", encoding="utf-8")
            if action == "run_tests":
                return SimpleNamespace(
                    success=True,
                    text="Tests: 1 passed, 0 failed",
                    error="",
                    data={"passed": 1, "failed": 0, "errors": 0, "pass_rate": 1.0},
                )
            return SimpleNamespace(success=True, text="ok", error="", data=None)

    worker = ReActWorker.__new__(ReActWorker)
    worker.model = "mock"
    worker._dead_providers = set()
    responses = iter(
        [
            json.dumps(
                {
                    "thought": "edit",
                    "action": "shell",
                    "action_input": {"command": "edit"},
                }
            ),
            json.dumps(
                {
                    "thought": "test",
                    "action": "run_tests",
                    "action_input": {"paths": "target.py"},
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "finish",
                    "action_input": {"summary": "done"},
                }
            ),
        ]
    )
    worker._call_model = lambda *args, **kwargs: (
        next(responses),
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        "mock",
    )

    with (
        patch(
            "scaffold.agent.react_worker.build_coding_tool_registry",
            lambda *args, **kwargs: FakeRegistry(),
        ),
        patch(
            "scaffold.agent.react_worker.format_tool_catalog", lambda *_args: "tools"
        ),
    ):
        return worker.execute_task(
            task={"action": "fix target.py", "file": "target.py"},
            file_content=original,
            codebase_context={},
            project_root=str(tmp_path),
        )


def test_react_allows_distinct_exploration_before_edit(tmp_path, monkeypatch):
    target = tmp_path / "target.py"
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")

    class FakeRegistry:
        def execute(self, action, action_input):
            if action == "edit_file":
                target.write_text("value = 2\n", encoding="utf-8")
            if action == "run_tests":
                return SimpleNamespace(
                    success=True,
                    text="Tests: 1 passed, 0 failed",
                    error="",
                    data={"passed": 1, "failed": 0, "errors": 0, "pass_rate": 1.0},
                )
            return SimpleNamespace(success=True, text="ok", error="", data=None)

    worker = ReActWorker.__new__(ReActWorker)
    worker.model = "mock"
    worker._dead_providers = set()
    responses = iter(
        [
            json.dumps(
                {
                    "thought": "inspect",
                    "action": "read_file",
                    "action_input": {"path": "target.py"},
                }
            ),
            json.dumps(
                {
                    "thought": "search",
                    "action": "grep",
                    "action_input": {"pattern": "value", "root": "."},
                }
            ),
            json.dumps(
                {
                    "thought": "inspect again",
                    "action": "read_file",
                    "action_input": {"path": "target.py", "start_line": 1},
                }
            ),
            json.dumps(
                {
                    "thought": "edit",
                    "action": "edit_file",
                    "action_input": {
                        "path": "target.py",
                        "old_string": "value = 1",
                        "new_string": "value = 2",
                    },
                }
            ),
            json.dumps(
                {
                    "thought": "verify",
                    "action": "run_tests",
                    "action_input": {"paths": "target.py"},
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "finish",
                    "action_input": {"summary": "verified"},
                }
            ),
        ]
    )
    worker._call_model = lambda *args, **kwargs: (
        next(responses),
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        "mock",
    )
    monkeypatch.setenv("AWOS_BENCHMARK_MODE", "1")
    monkeypatch.setenv("AWOS_REACT_MAX_TURNS", "5")

    with (
        patch(
            "scaffold.agent.react_worker.build_coding_tool_registry",
            lambda *args, **kwargs: FakeRegistry(),
        ),
        patch(
            "scaffold.agent.react_worker.format_tool_catalog", lambda *_args: "tools"
        ),
    ):
        result = worker.execute_task(
            task={"action": "fix target.py", "file": "target.py"},
            file_content=original,
            codebase_context={},
            project_root=str(tmp_path),
        )

    assert result["success"] is True, result.get("error")
    assert result["files_changed"] == ["target.py"]


def test_react_completes_from_verified_evidence_without_finish(tmp_path, monkeypatch):
    target = tmp_path / "target.py"
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")

    class FakeRegistry:
        def execute(self, action, action_input):
            if action == "edit_file":
                target.write_text("value = 2\n", encoding="utf-8")
            if action == "run_tests":
                return SimpleNamespace(
                    success=True,
                    text="Tests: 1 passed, 0 failed",
                    error="",
                    data={"passed": 1, "failed": 0, "errors": 0, "pass_rate": 1.0},
                )
            return SimpleNamespace(success=True, text="ok", error="", data=None)

    worker = ReActWorker.__new__(ReActWorker)
    worker.model = "mock"
    worker._dead_providers = set()
    responses = iter(
        [
            json.dumps(
                {
                    "thought": "inspect",
                    "action": "read_file",
                    "action_input": {"path": "target.py"},
                }
            ),
            json.dumps(
                {
                    "thought": "edit",
                    "action": "edit_file",
                    "action_input": {
                        "path": "target.py",
                        "old_string": "value = 1",
                        "new_string": "value = 2",
                    },
                }
            ),
            json.dumps(
                {
                    "thought": "confirm",
                    "action": "read_file",
                    "action_input": {"path": "target.py"},
                }
            ),
            json.dumps(
                {
                    "thought": "verify",
                    "action": "run_tests",
                    "action_input": {"paths": "target.py"},
                }
            ),
        ]
    )
    worker._call_model = lambda *args, **kwargs: (
        next(responses),
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        "mock",
    )
    monkeypatch.setenv("AWOS_BENCHMARK_MODE", "1")

    with (
        patch(
            "scaffold.agent.react_worker.build_coding_tool_registry",
            lambda *args, **kwargs: FakeRegistry(),
        ),
        patch(
            "scaffold.agent.react_worker.format_tool_catalog", lambda *_args: "tools"
        ),
        patch("scaffold.agent.react_worker._MAX_TURNS", 4),
    ):
        result = worker.execute_task(
            task={"action": "fix target.py", "file": "target.py"},
            file_content=original,
            codebase_context={},
            project_root=str(tmp_path),
        )

    assert result["success"] is True, result.get("error")
    assert result["summary"] == "Verified edit completed after the model turn limit"
    assert result["files_changed"] == ["target.py"]


def test_react_detects_untracked_shell_edit(tmp_path):
    result = _run_react_with_untracked_shell_edit(tmp_path, change_file=True)

    assert result["success"] is True
    assert result["files_changed"] == ["target.py"]


def test_react_does_not_report_unchanged_shell_task_as_success(tmp_path):
    result = _run_react_with_untracked_shell_edit(tmp_path, change_file=False)

    assert result["success"] is False
    assert result["files_changed"] == []


# ── Regression: verify-only tasks must not require edits (Bug 3 fix) ──────────


def test_task_needs_edits_fix_task_requires_edits():
    """A 'fix' task should require edits."""
    task = {"action": "Fix the bug in add function: return a-b instead of a+b"}
    assert ReActWorker._task_needs_edits(task) is True


def test_task_needs_edits_verify_task_does_not_require_edits():
    """A verify task mentioning 'fix' in passing should NOT require edits."""
    task = {"action": "Verify all tests pass after the fix"}
    assert ReActWorker._task_needs_edits(task) is False


def test_task_needs_edits_pure_verify_task():
    """A pure verification task should not require edits."""
    task = {"action": "Verify the implementation is correct"}
    assert ReActWorker._task_needs_edits(task) is False


def test_task_needs_edits_test_task_does_not_require_edits():
    """A test task should not require edits."""
    task = {"action": "Run tests to check the fix worked"}
    assert ReActWorker._task_needs_edits(task) is False


def test_task_needs_edits_edit_task_still_requires_edits():
    """An edit task should still require edits even with verify-like words."""
    task = {"action": "Edit calculator.py to fix the add function"}
    assert ReActWorker._task_needs_edits(task) is True
