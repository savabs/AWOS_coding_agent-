"""
GoalChecker and Orchestrator._goal_check_rounds — the whole-goal review that
stops a run "succeeding" after a partial change (1 of 8 read sites migrated).

The checker's model is a scripted client; the orchestrator's task batch and
checker are mocked, so no key and no network are needed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.agent_loop import AnthropicToolClient, ModelReply, ToolCall
from scaffold.agent.goal_check import (
    MAX_MISSING, GoalChecker, GoalVerdict, build_prompt, build_readonly_registry,
    parse_verdict, working_tree_changes,
)
from scaffold.agent.orchestrator import Orchestrator


# ── Parsing ──────────────────────────────────────────────────────────────────

INCOMPLETE = ('{"complete": false, "missing": [{"action": "Read the port from '
              'settings, not the ini", "file": "server.py"}], "reasoning": "server.py '
              'still parses app.ini"}')


def test_parses_bare_json():
    v = parse_verdict(INCOMPLETE)
    assert v.complete is False
    assert v.missing == [{"action": "Read the port from settings, not the ini",
                          "file": "server.py"}]
    assert "app.ini" in v.reasoning


def test_parses_fenced_json_with_prose_around_it():
    v = parse_verdict("Here is my verdict:\n```json\n" + INCOMPLETE + "\n```\nThanks.")
    assert v.complete is False and v.missing[0]["file"] == "server.py"


def test_parses_json_embedded_in_prose_with_braces_in_strings():
    text = ('I checked every caller. {"complete": true, "missing": [], '
            '"reasoning": "all reads use settings {\\"x\\"} now"} Done.')
    v = parse_verdict(text)
    assert v.complete is True and v.missing == []


def test_complete_but_listing_work_is_not_complete():
    v = parse_verdict('{"complete": true, "missing": [{"action": "fix cli.py", "file": "cli.py"}]}')
    assert v.complete is False


def test_missing_is_capped_and_blank_actions_dropped():
    items = ",".join(f'{{"action": "fix {i}", "file": "f{i}.py"}}' for i in range(9))
    v = parse_verdict(f'{{"complete": false, "missing": [{{"action": ""}}, {items}]}}')
    assert len(v.missing) == MAX_MISSING
    assert v.missing[0]["action"] == "fix 0"


@pytest.mark.parametrize("value", ['"false"', '"False"', '"0"', '"no"', "0", "false", "null"])
def test_string_or_falsy_complete_is_not_complete(value):
    # bool("false") is True: a model quoting its boolean must not pass a
    # partial change. With nothing listed missing, "not complete" is no usable
    # verdict (the checker is asked again), and above all not a "complete" one.
    v = parse_verdict(f'{{"complete": {value}, "missing": [], "reasoning": "r"}}')
    assert v is None


@pytest.mark.parametrize("value", ['"true"', '"True"', '"yes"', "true", "1"])
def test_string_true_with_nothing_missing_is_complete(value):
    v = parse_verdict(f'{{"complete": {value}, "missing": []}}')
    assert v.complete is True and v.verified is True


@pytest.mark.parametrize("text", ["", "The goal looks done to me.", "{not json}",
                                  '{"reasoning": "no complete key"}'])
def test_unparseable_is_none(text):
    assert parse_verdict(text) is None


def test_prompt_carries_goal_verbatim_and_truncates_the_diff():
    goal = "Move every setting from app.ini to env vars — ALL readers."
    prompt = build_prompt(goal, ["config.py"], "x" * 50000)
    assert goal in prompt and "- config.py" in prompt
    assert "diff truncated" in prompt and len(prompt) < 20000
    assert "do not trust the diff's scope" in prompt


# ── The checker itself ───────────────────────────────────────────────────────

def test_readonly_registry_has_no_edit_tool(tmp_path):
    registry = build_readonly_registry(str(tmp_path))
    for name in ("read_file", "list_dir", "find_files", "grep", "run_tests"):
        assert registry.get(name) is not None, name
    assert registry.get("edit_file") is None and registry.get("write_file") is None
    # run_command writes its workspace: only offered with a sandbox over a copy.
    assert registry.get("run_command") is None and registry.get("shell") is None


def test_prompt_without_a_diff_says_to_read_the_files():
    prompt = build_prompt("fix paginate()", ["buggy.py"], "")
    assert "not under version control" in prompt and "(empty)" not in prompt


class _Scripted(AnthropicToolClient):
    def __init__(self, replies):
        super().__init__(client=None, model="claude-test")
        self._replies = list(replies)

    def complete(self, system, messages, registry):
        return self._replies.pop(0) if self._replies else ModelReply(text="")


def test_checker_searches_then_returns_the_verdict_and_records_spend(tmp_path):
    (tmp_path / "server.py").write_text("PORT = ini.get('port')\n", encoding="utf-8")
    script = [
        ModelReply(text="", tool_calls=[ToolCall(id="t", name="grep",
                                                 arguments={"pattern": "ini"})],
                   input_tokens=100, output_tokens=10),
        ModelReply(text=INCOMPLETE, input_tokens=50, output_tokens=40),
    ]
    with patch("scaffold.agent.usage_record.record_api_usage") as record:
        v = GoalChecker(client=_Scripted(script)).check("migrate", str(tmp_path), [], "")
    assert v.complete is False and v.missing[0]["file"] == "server.py"
    assert (v.input_tokens, v.output_tokens) == (150, 50)
    assert record.call_args.kwargs["request_type"] == "goal_check"


def test_unparseable_reply_does_not_block_the_run(tmp_path):
    v = GoalChecker(client=_Scripted([ModelReply(text="Looks fine.")])).check(
        "goal", str(tmp_path), [], "")
    assert v.complete is True and "could not be parsed" in v.reasoning
    assert v.verified is False


def test_no_model_client_does_not_block_the_run(tmp_path):
    with patch("scaffold.agent.goal_check.client_for_model", side_effect=RuntimeError("no key")):
        v = GoalChecker().check("goal", str(tmp_path), [], "")
    assert v.complete is True and "no model client" in v.reasoning
    assert v.verified is False


def test_crashing_checker_does_not_block_the_run(tmp_path):
    class _Crashing(_Scripted):
        def complete(self, system, messages, registry):
            raise RuntimeError("provider 503")

    # A provider error inside the loop ends it with stop=model_error.
    v = GoalChecker(client=_Crashing([])).check("goal", str(tmp_path), [], "")
    assert v.complete is True and "model_error" in v.reasoning
    assert v.verified is False
    # A crash of the loop itself must not escape into execute_feature.
    with patch("scaffold.agent.agent_loop.AgentLoop.run", side_effect=RuntimeError("boom")):
        v = GoalChecker(client=_Scripted([])).check("goal", str(tmp_path), [], "")
    assert v.complete is True and "failed to run: boom" in v.reasoning
    assert v.verified is False


def test_a_real_verdict_is_verified():
    assert parse_verdict(INCOMPLETE).verified is True


def test_max_turns_from_env(monkeypatch):
    assert GoalChecker(client=object()).max_turns == 24
    monkeypatch.setenv("AWOS_GOAL_CHECK_MAX_TURNS", "7")
    assert GoalChecker(client=object()).max_turns == 7


def test_working_tree_changes_lists_modified_and_untracked(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "config.py").write_text("A = 1\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (tmp_path / "config.py").write_text("A = 2\n", encoding="utf-8")
    (tmp_path / "new.py").write_text("B = 1\n", encoding="utf-8")
    files, diff = working_tree_changes(str(tmp_path))
    assert files == ["config.py", "new.py"]
    assert "+A = 2" in diff and "new.py" in diff


# ── Structured verdict: the submit_verdict tool ─────────────────────────────


def _submit(complete, missing=(), reasoning="r", call_id="v"):
    return ModelReply(text="", tool_calls=[ToolCall(
        id=call_id, name="submit_verdict",
        arguments={"complete": complete, "missing": list(missing), "reasoning": reasoning})],
        input_tokens=20, output_tokens=5)


class _Recording(_Scripted):
    """Also counts the real model calls and keeps the last message list."""

    def __init__(self, replies):
        super().__init__(replies)
        self.calls = 0
        self.last_messages = None

    def complete(self, system, messages, registry):
        self.calls += 1
        self.last_messages = list(messages)
        return super().complete(system, messages, registry)


def test_verdict_via_tool_ends_the_check(tmp_path):
    missing = [{"action": "--to must include orders at 23:59:59 on that day",
                "file": "ordertool/export.py"}]
    client = _Recording([_submit(False, missing, "ran export --to 2024-01-31"),
                         ModelReply(text="should never be asked for")])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client).check("filters", str(tmp_path), [], "")
    assert v.verified is True and v.complete is False and v.source == "tool"
    assert v.missing == missing and "2024-01-31" in v.reasoning
    # The call ended the review: no second paid model turn.
    assert client.calls == 1
    assert (v.input_tokens, v.output_tokens) == (20, 5)


def test_tool_verdict_of_complete_with_work_listed_is_incomplete(tmp_path):
    client = _Recording([_submit(True, [{"action": "fix cli", "file": "cli.py"}])])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client).check("g", str(tmp_path), [], "")
    assert v.complete is False and v.verified is True


def test_tool_missing_is_capped_and_paths_made_relative(tmp_path):
    seen = {}

    def factory(ws):
        seen["ws"] = ws
        return None

    class _Abs(_Recording):
        def complete(self, system, messages, registry):
            items = [{"action": f"fix {i}", "file": os.path.join(seen["ws"], f"m{i}.py")}
                     for i in range(8)]
            self._replies = [_submit(False, items)]
            return super().complete(system, messages, registry)

    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=_Abs([]), sandbox_factory=factory).check(
            "g", str(tmp_path), [], "")
    assert len(v.missing) == MAX_MISSING
    assert v.missing[0]["file"] == "m0.py"


def test_invalid_tool_args_are_an_error_the_model_can_fix(tmp_path):
    bad = ModelReply(text="", tool_calls=[ToolCall(id="b", name="submit_verdict",
                                                   arguments={"reasoning": "forgot"})])
    client = _Recording([bad, _submit(True, [], "all good")])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client).check("g", str(tmp_path), [], "")
    assert v.complete is True and v.verified is True and client.calls == 2


def test_no_verdict_gets_one_nudge_then_the_tool_answers(tmp_path):
    # The benchmark's failure: the loop ran out of turns while still looking.
    looking = [ModelReply(text="", tool_calls=[ToolCall(id=f"t{i}", name="find_files",
                                                        arguments={"pattern": f"*{i}.py"})])
               for i in range(3)]
    client = _Recording(looking + [_submit(False, [{"action": "add --status", "file": "cli.py"}])])
    with patch("scaffold.agent.usage_record.record_api_usage") as record:
        v = GoalChecker(client=client, max_turns=3).check("g", str(tmp_path), [], "")
    assert client.calls == 4
    assert v.verified is True and v.complete is False and v.source == "nudge_tool"
    assert v.missing == [{"action": "add --status", "file": "cli.py"}]
    # The nudge continues the same conversation and says what to do.
    last = client.last_messages[-1]
    text = last["content"] if isinstance(last["content"], str) else last["content"][-1]["text"]
    assert text == "Call submit_verdict now."
    assert len(client.last_messages) > 2
    # Its tokens are billed with the rest.
    assert record.call_args.kwargs["input_tokens"] == 20


def test_empty_final_message_gets_the_nudge_too(tmp_path):
    client = _Recording([ModelReply(text=""), ModelReply(text=INCOMPLETE)])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client).check("g", str(tmp_path), [], "")
    assert v.verified is True and v.complete is False and v.source == "nudge_text"


def test_no_verdict_even_after_the_nudge_is_unverified(tmp_path):
    client = _Recording([ModelReply(text="I think it is fine."), ModelReply(text="Yes.")])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client).check("g", str(tmp_path), [], "")
    # Per check: the loop's one turn and exactly one nudge; two checks.
    assert client.calls == 4
    assert v.complete is True and v.verified is False and v.source == ""
    assert "could not be parsed" in v.reasoning and "2 check(s)" in v.reasoning


def test_no_verdict_runs_one_fresh_check_before_the_fallback(tmp_path):
    # The benchmark's migration: the checker ran out of turns, the nudge got
    # no verdict, and the fallback let 18 of 22 hidden tests through.
    looking = [ModelReply(text="", tool_calls=[ToolCall(id=f"t{i}", name="find_files",
                                                        arguments={"pattern": f"*{i}.py"})],
                          input_tokens=10, output_tokens=1)
               for i in range(2)]
    gap = [{"action": "server.py still reads app.ini", "file": "server.py"}]
    client = _Recording(looking + [ModelReply(text="still looking", input_tokens=10,
                                              output_tokens=1),
                                   _submit(False, gap)])
    workspaces = []

    def factory(ws):
        workspaces.append(ws)
        return None

    with patch("scaffold.agent.usage_record.record_api_usage") as record:
        v = GoalChecker(client=client, max_turns=2, sandbox_factory=factory).check(
            "g", str(tmp_path), [], "")
    assert v.verified is True and v.complete is False and v.source == "tool"
    assert v.missing == gap
    # Fresh loop: the second check opened with just the goal prompt ...
    assert len(client.last_messages) == 1
    # ... on a fresh copy of the workspace.
    assert len(workspaces) == 2 and workspaces[0] != workspaces[1]
    # Both checks' tokens count, and each check was billed.
    assert (v.input_tokens, v.output_tokens) == (50, 8)
    assert record.call_count == 2


def test_no_retry_when_the_goal_budget_is_spent(tmp_path):
    client = _Recording([ModelReply(text="I think it is fine."), ModelReply(text="Yes.")])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client, can_continue=lambda: False).check(
            "g", str(tmp_path), [], "")
    assert client.calls == 2 and v.verified is False and "1 check(s)" in v.reasoning


# ── The checker's model ──────────────────────────────────────────────────────


def test_checker_model_env_override_wins(monkeypatch):
    from scaffold.agent.goal_check import goal_check_model
    monkeypatch.setenv("AWOS_GOAL_CHECK_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    assert goal_check_model("deepseek/deepseek-v4-flash") == "openai/gpt-4o-mini"


def test_checker_model_defaults_to_haiku_via_openrouter(monkeypatch):
    from scaffold.agent.agent_loop import _price_for
    from scaffold.agent.goal_check import goal_check_model
    monkeypatch.delenv("AWOS_GOAL_CHECK_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    model = goal_check_model("deepseek/deepseek-v4-flash")
    assert model == "anthropic/claude-haiku-4.5"
    assert _price_for(model) == (1.00, 5.00)  # priced, so the budget sees it


@pytest.mark.parametrize("worker", ["claude-sonnet-4-6", "anthropic/claude-haiku-4.5",
                                    "replay", "local"])
def test_checker_model_keeps_a_free_or_stronger_worker(monkeypatch, worker):
    # Haiku would turn a $0 run into ~$0.6 a check, or downgrade Sonnet.
    from scaffold.agent.goal_check import goal_check_model
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    assert goal_check_model(worker) == worker


@pytest.mark.parametrize("env", [{"AWOS_BASE_URL": "http://localhost:11434/v1"},
                                 {"AWOS_PROVIDER": "anthropic"},
                                 {"AWOS_PROVIDER": "deepseek"}])
def test_checker_model_is_the_workers_off_openrouter(monkeypatch, env):
    # The worker is not routed through OpenRouter although the key is set:
    # Haiku's OpenRouter id would go to a server that does not serve it.
    from scaffold.agent.goal_check import goal_check_model
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert goal_check_model("qwen2.5-coder") == "qwen2.5-coder"


def test_checker_model_is_logged_and_capped_at_the_goals_remaining_budget(monkeypatch, capsys):
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    monkeypatch.delenv("AWOS_GOAL_BUDGET_USD", raising=False)
    orch = _orchestrator()
    orch._last_agent_model = "deepseek/deepseek-v4-flash"
    orch._goal_started_at, orch._goal_budget_hit = 0.0, False
    checker = MagicMock()
    checker.return_value.check.return_value = GoalVerdict(True, verified=True)
    with patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=([], "")), \
         patch("scaffold.agent.orchestrator._ledger_spend_since", return_value=1.25):
        orch._goal_check_rounds("g", {"codebase_root": "."}, False)
    assert ("[GOAL CHECK] checker model: anthropic/claude-haiku-4.5 "
            "(worker: deepseek/deepseek-v4-flash)") in capsys.readouterr().out
    remaining = checker.call_args.kwargs["remaining_budget"]
    with patch("scaffold.agent.orchestrator._ledger_spend_since", return_value=1.25):
        assert remaining() == pytest.approx(0.75)  # of the default $2.00


def test_checker_model_without_openrouter_is_the_workers(monkeypatch):
    from scaffold.agent.goal_check import goal_check_model
    monkeypatch.delenv("AWOS_GOAL_CHECK_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert goal_check_model("claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert goal_check_model(None) is None


def test_cost_cap_is_not_nudged(tmp_path, monkeypatch):
    client = _Recording([ModelReply(text="", tool_calls=[ToolCall(id="t", name="list_dir",
                                                                  arguments={})],
                                    input_tokens=10_000_000, output_tokens=10_000_000)])
    client.model = "claude-haiku-4-5"
    monkeypatch.setenv("AWOS_MAX_RUN_COST", "0.01")
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client).check("g", str(tmp_path), [], "")
    assert client.calls == 1 and v.verified is False and "cost_cap" in v.reasoning


# ── What a check may spend ───────────────────────────────────────────────────


def _looking(n, tokens_in=100_000):
    """n turns that each search and decide nothing; Haiku prices 100k in at $0.10."""
    return [ModelReply(text="", tool_calls=[ToolCall(id=f"t{i}", name="find_files",
                                                     arguments={"pattern": f"*{i}.py"})],
                       input_tokens=tokens_in, output_tokens=10)
            for i in range(n)]


def test_check_stops_at_what_the_goal_has_left(tmp_path):
    # With no cap a check ran all 40 turns whatever the goal had left: one
    # Haiku check measured $0.58 against a worker's $0.02.
    client = _Recording(_looking(10))
    client.model = "claude-haiku-4-5"
    with patch("scaffold.agent.usage_record.record_api_usage") as record:
        v = GoalChecker(client=client, max_turns=10,
                        remaining_budget=lambda: 0.15).check("g", str(tmp_path), [], "")
    # $0.10 after turn 1 is under $0.15; $0.20 after turn 2 is not. A cost cap
    # is neither nudged nor retried.
    assert client.calls == 2
    assert v.verified is False and "cost_cap" in v.reasoning
    assert record.call_args.kwargs["input_tokens"] == 200_000


def test_per_check_cap_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_GOAL_CHECK_MAX_COST", "0.05")
    client = _Recording(_looking(10))
    client.model = "claude-haiku-4-5"
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client, max_turns=10,
                        remaining_budget=lambda: 5.0).check("g", str(tmp_path), [], "")
    assert client.calls == 1 and "cost_cap" in v.reasoning


def test_no_check_when_nothing_is_left(tmp_path):
    client = _Recording(_looking(3))
    with patch("scaffold.agent.usage_record.record_api_usage") as record:
        v = GoalChecker(client=client, remaining_budget=lambda: 0.0).check(
            "g", str(tmp_path), [], "")
    assert client.calls == 0 and record.call_count == 0
    assert v.verified is False and "cost_cap" in v.reasoning


def test_no_retry_the_goal_cannot_afford(tmp_path):
    # The first check ran out of turns ($0.10 + a $0.10 nudge); $0.15 left
    # would stop the retry at the cap without a verdict, so it is not started.
    left = iter([1.0, 0.15])
    client = _Recording(_looking(1) + [ModelReply(text="still looking",
                                                  input_tokens=100_000, output_tokens=10),
                                       _submit(True)])
    client.model = "claude-haiku-4-5"
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client, max_turns=1,
                        remaining_budget=lambda: next(left)).check("g", str(tmp_path), [], "")
    assert client.calls == 2 and v.verified is False and "1 check(s)" in v.reasoning


def test_affordable_retry_still_runs(tmp_path):
    client = _Recording(_looking(1) + [ModelReply(text="still looking",
                                                  input_tokens=100_000, output_tokens=10),
                                       _submit(True)])
    client.model = "claude-haiku-4-5"
    with patch("scaffold.agent.usage_record.record_api_usage"):
        # An explicit per-check cap: the default ($0.20) is below this check's cost.
        v = GoalChecker(client=client, max_turns=1, max_cost_usd=1.0,
                        remaining_budget=lambda: 1.0).check("g", str(tmp_path), [], "")
    assert client.calls == 3 and v.verified is True and v.complete is True


def test_default_per_check_cap(monkeypatch):
    from scaffold.agent.goal_check import DEFAULT_CHECK_MAX_COST
    monkeypatch.delenv("AWOS_GOAL_CHECK_MAX_COST", raising=False)
    assert GoalChecker(client=object()).max_cost_usd == DEFAULT_CHECK_MAX_COST
    monkeypatch.setenv("AWOS_GOAL_CHECK_MAX_COST", "0.5")
    assert GoalChecker(client=object()).max_cost_usd == 0.5


def test_checker_loop_uses_the_tight_context(monkeypatch, tmp_path):
    # 3 kept turns and 3000-char tool results: the lever that took a check
    # from ~500k input tokens down.
    import scaffold.agent.agent_loop as al
    from scaffold.agent import goal_check

    seen = {}
    orig_init = al.AgentLoop.__init__

    def spy(self, *a, **kw):
        seen.update(keep=kw.get("keep_turns"), chars=kw.get("tool_result_chars"))
        orig_init(self, *a, **kw)

    monkeypatch.setattr(al.AgentLoop, "__init__", spy)
    client = _Recording([_submit(True)])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        GoalChecker(client=client).check("g", str(tmp_path), [], "")
    assert seen == {"keep": goal_check.CHECK_KEEP_TURNS,
                    "chars": goal_check.CHECK_TOOL_RESULT_CHARS}


# ── Behavioural verification on a copy ──────────────────────────────────────


class _FakeSandbox:
    """Runs commands with cwd = the workspace it was built over; no confinement."""

    instances: list = []

    def __init__(self, workspace):
        self.workspace = workspace
        self.closed = 0
        _FakeSandbox.instances.append(self)

    def run(self, command, timeout_sec=120):
        from scaffold.agent.sandbox import SandboxResult
        r = subprocess.run(command, shell=True, cwd=self.workspace, capture_output=True,
                           text=True, timeout=timeout_sec)
        return SandboxResult(exit_code=r.returncode, stdout=r.stdout, stderr=r.stderr,
                             timed_out=False, duration_sec=0.0)

    def close(self):
        self.closed += 1


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    for skipped in (".git", ".venv", "node_modules", "pkg/__pycache__"):
        (root / skipped).mkdir(parents=True, exist_ok=True)
        (root / skipped / "junk").write_text("x", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _FakeSandbox.instances = []
    return root


def _run_cmd(command, call_id="c"):
    return ModelReply(text="", tool_calls=[ToolCall(id=call_id, name="run_command",
                                                    arguments={"command": command})])


def test_checker_runs_commands_on_a_copy_never_the_real_root(project):
    script = [
        _run_cmd(f"ls -a; ls -a pkg; {sys.executable} pkg/app.py", "c1"),
        _run_cmd("echo HACKED > pkg/app.py && touch created.txt && rm -rf pkg/__pycache__", "c2"),
        _submit(True, [], "ran it"),
    ]
    client = _Recording(script)
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client, sandbox_factory=_FakeSandbox).check(
            "g", str(project), [], "")
    assert v.verified is True
    sb = _FakeSandbox.instances[0]
    assert os.path.realpath(sb.workspace) != os.path.realpath(project)
    # The real tree is untouched by what the checker ran.
    assert (project / "pkg" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert not (project / "created.txt").exists()
    assert (project / "pkg" / "__pycache__" / "junk").exists()
    # The copy left out VCS, envs, caches and secrets.
    listing = client.last_messages[2]["content"][0]["content"]
    names = set(listing.split("\n"))
    assert "pkg" in names and "hi" in names
    assert not names & {".git", ".venv", "node_modules", ".env", "__pycache__"}
    # And it is gone afterwards, its sandbox closed once.
    assert not os.path.exists(sb.workspace) and sb.closed == 1


def test_sandbox_closed_and_copy_removed_when_the_review_crashes(project):
    with patch("scaffold.agent.agent_loop.AgentLoop.run", side_effect=RuntimeError("boom")):
        v = GoalChecker(client=_Scripted([]), sandbox_factory=_FakeSandbox).check(
            "g", str(project), [], "")
    assert "failed to run: boom" in v.reasoning and v.verified is False
    sb = _FakeSandbox.instances[0]
    assert sb.closed == 1
    assert not os.path.exists(os.path.dirname(sb.workspace))


def test_copy_removed_when_the_sandbox_cannot_start(project):
    made = []

    def factory(ws):
        made.append(ws)
        raise RuntimeError("docker gone")

    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=_Recording([_submit(True)]), sandbox_factory=factory).check(
            "g", str(project), [], "")
    assert v.verified is True and not os.path.exists(made[0])


def test_no_sandbox_means_no_run_command_and_the_prompt_says_so(project):
    seen = {}

    class _Peek(_Recording):
        def complete(self, system, messages, registry):
            seen["tools"] = [t["name"] for t in registry.list_tools()]
            seen["prompt"] = messages[0]["content"]
            return super().complete(system, messages, registry)

    with patch("scaffold.agent.usage_record.record_api_usage"):
        GoalChecker(client=_Peek([_submit(True)]), sandbox_factory=lambda ws: None).check(
            "g", str(project), [], "")
    assert "run_command" not in seen["tools"] and "submit_verdict" in seen["tools"]
    assert "edit_file" not in seen["tools"]
    assert "run_command is not available" in seen["prompt"]


def test_registry_over_a_sandbox_offers_run_command(tmp_path):
    registry = build_readonly_registry(str(tmp_path), sandbox=_FakeSandbox(str(tmp_path)))
    assert registry.get("run_command") is not None
    assert registry.get("run_tests").sandbox is not None
    assert registry.get("edit_file") is None


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"),
                    reason="needs macOS Seatbelt")
def test_real_seatbelt_sandbox_cannot_reach_the_real_root(project):
    from scaffold.agent.sandbox import SeatbeltSandbox
    boxes = []

    def factory(ws):
        boxes.append(SeatbeltSandbox(ws))
        return boxes[-1]

    target = project / "pkg" / "app.py"
    script = [_run_cmd(f"echo HACKED > pkg/app.py; echo HACKED > '{target}'"), _submit(True)]
    with patch("scaffold.agent.usage_record.record_api_usage"):
        GoalChecker(client=_Recording(script), sandbox_factory=factory).check(
            "g", str(project), [], "")
    assert target.read_text(encoding="utf-8") == "print('hi')\n"
    assert not boxes[0].tmpdir.exists()  # closed


def test_prompt_tells_the_checker_to_try_the_behaviour():
    from scaffold.agent.goal_check import INSTRUCTIONS, SYSTEM_PROMPT
    text = SYSTEM_PROMPT + build_prompt("g", [], "")
    for phrase in ("TRY the goal's behaviour", "error case", "boundary", "probe scripts",
                   "traceback", "clean error message", "COPY", "submit_verdict"):
        assert phrase in text, phrase
    assert "concrete and verifiable" in INSTRUCTIONS and "23:59:59" in INSTRUCTIONS
    assert f"At most {MAX_MISSING} items" in INSTRUCTIONS


# ── Unusable verdicts, a copy that cannot be made, host reads ───────────────


@pytest.mark.parametrize("missing", [[], [{"action": "", "file": "a.py"}], "not json"])
def test_incomplete_with_no_usable_item_is_refused_and_asked_again(tmp_path, missing):
    # It used to be recorded, and the goal failed with zero follow-up rounds.
    bad = ModelReply(text="", tool_calls=[ToolCall(id="b", name="submit_verdict", arguments={
        "complete": False, "missing": missing, "reasoning": "something is off"})])
    fixed = _submit(False, [{"action": "--to must include 23:59", "file": "export.py"}])
    client = _Recording([bad, fixed])
    with patch("scaffold.agent.usage_record.record_api_usage"):
        v = GoalChecker(client=client).check("g", str(tmp_path), [], "")
    assert client.calls == 2
    assert "needs at least one missing item" in str(client.last_messages[-1]["content"])
    assert v.complete is False and v.missing == [{"action": "--to must include 23:59",
                                                  "file": "export.py"}]


def _peek_tools(project, **checker_kwargs):
    seen = {}

    class _Peek(_Recording):
        def complete(self, system, messages, registry):
            seen["tools"] = [t["name"] for t in registry.list_tools()]
            seen["prompt"] = messages[0]["content"]
            seen["registry"] = registry
            return super().complete(system, messages, registry)

    with patch("scaffold.agent.usage_record.record_api_usage"):
        GoalChecker(client=_Peek([_submit(True)]), **checker_kwargs).check(
            "g", str(project), [], "")
    return seen


def test_no_copy_means_no_code_runs_on_the_real_tree(project):
    # A failed copy fell back to the real tree with run_tests still offered,
    # unsandboxed: the model's conftest.py would run with the owner's rights.
    with patch("scaffold.agent.goal_check._copy_workspace", side_effect=OSError("boom")):
        seen = _peek_tools(project, sandbox_factory=_FakeSandbox)
    assert "run_tests" not in seen["tools"] and "run_command" not in seen["tools"]
    assert "read_file" in seen["tools"] and not _FakeSandbox.instances
    assert "Nothing can be run in this review" in seen["prompt"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_special_and_unreadable_files_do_not_break_the_copy(project):
    os.mkfifo(project / "pkg" / "pipe")
    locked = project / "pkg" / "locked.txt"
    locked.write_text("x", encoding="utf-8")
    locked.chmod(0)
    locked_dir = project / "private"
    locked_dir.mkdir()
    locked_dir.chmod(0)
    try:
        seen = _peek_tools(project, sandbox_factory=_FakeSandbox)
    finally:
        locked.chmod(0o644)
        locked_dir.chmod(0o755)
    # The copy was made, so the review runs on it, in the sandbox.
    assert "run_tests" in seen["tools"] and "run_command" in seen["tools"]
    assert os.path.realpath(_FakeSandbox.instances[0].workspace) != os.path.realpath(project)


def test_copy_leaves_out_runtime_state_and_stops_at_the_size_cap(project, monkeypatch):
    from scaffold.agent.goal_check import _copy_workspace
    (project / ".awos" / "goals").mkdir(parents=True)
    (project / ".awos" / "goals" / "g.json").write_text("{}", encoding="utf-8")
    (project / "dist").mkdir()
    (project / "dist" / "big.whl").write_bytes(b"0" * 1024)
    tmp, copy = _copy_workspace(str(project))
    try:
        assert os.path.exists(os.path.join(copy, "pkg", "app.py"))
        assert not os.path.exists(os.path.join(copy, ".awos"))
        assert not os.path.exists(os.path.join(copy, "dist"))
    finally:
        import shutil
        shutil.rmtree(tmp)

    (project / "pkg" / "data.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    monkeypatch.setenv("AWOS_GOAL_CHECK_MAX_COPY_MB", "1")
    seen = _peek_tools(project, sandbox_factory=_FakeSandbox)
    assert "run_tests" not in seen["tools"] and not _FakeSandbox.instances


def test_host_read_tools_stay_inside_the_copy(project, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("TOKEN=abc", encoding="utf-8")
    (project / "pkg" / "link.txt").symlink_to(outside)
    (project / "pkg" / ".env.local").write_text("KEY=1", encoding="utf-8")
    registry = build_readonly_registry(str(project))
    for path in (str(outside), "pkg/link.txt", "../secret.txt", ".env", "pkg/.env.local",
                 os.path.expanduser("~/.zshrc")):
        r = registry.execute("read_file", {"path": path})
        assert not r.success and "Refused" in r.error, path
    assert registry.execute("read_file", {"path": "pkg/app.py"}).success
    grep = registry.execute("grep", {"pattern": "TOKEN|KEY|SECRET"})
    assert grep.success and grep.data["hits"] == []
    found = registry.execute("find_files", {"pattern": "../*.txt"})
    assert found.data["matches"] == []
    assert not registry.execute("list_dir", {"path": ".."}).success


# ── Orchestrator wiring ──────────────────────────────────────────────────────

def _orchestrator():
    orch = Orchestrator.__new__(Orchestrator)
    orch.execution_log = []
    orch._pause_requested = False
    orch._run_files_changed = {"config.py"}
    orch.tracker = None
    orch._run_task_batch = MagicMock(side_effect=lambda tasks, ctx, par: [
        {"task_id": t["task_id"], "success": True, "task": t} for t in tasks])
    return orch


MISSING = [{"action": "Read the port from settings", "file": "server.py"},
           {"action": "Read the host from settings", "file": "cli.py"}]


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for name in ("AWOS_GOAL_CHECK_ROUNDS", "AWOS_GOAL_CHECK_MODEL", "AWOS_GOAL_CHECK_MAX_TURNS",
                 "AWOS_GOAL_CHECK_MAX_COST", "AWOS_MAX_RUN_COST", "AWOS_BASE_URL",
                 "AWOS_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    # conftest turns the check off for the rest of the suite; here it is
    # the subject (and its checker is always mocked).
    monkeypatch.setenv("AWOS_GOAL_CHECK", "1")
    # No real sandbox (or docker container) unless a test injects one.
    monkeypatch.setenv("AWOS_SANDBOX", "none")


def _rounds(verdicts, monkeypatch, **env):
    monkeypatch.setenv("AWOS_EXECUTOR", env.pop("executor", "agent_loop"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    orch = _orchestrator()
    checker = MagicMock()
    checker.return_value.check.side_effect = verdicts
    with patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=([], "")):
        out = orch._goal_check_rounds("migrate settings", {"codebase_root": "."}, False)
    return out, orch, checker


def test_complete_runs_no_extra_tasks(monkeypatch):
    (ok, extra, _), orch, checker = _rounds([GoalVerdict(True, reasoning="all done")], monkeypatch)
    assert ok is True and extra == []
    orch._run_task_batch.assert_not_called()
    # Without a git diff, the files the run recorded are what the checker sees.
    assert checker.return_value.check.call_args.args[2] == ["config.py"]


def test_incomplete_runs_follow_ups_then_checks_again(monkeypatch):
    verdicts = [GoalVerdict(False, missing=MISSING, reasoning="2 readers left", verified=True),
                GoalVerdict(True, reasoning="all readers migrated", verified=True)]
    (ok, extra, _), orch, checker = _rounds(verdicts, monkeypatch)
    assert ok is True and len(extra) == 2
    tasks = orch._run_task_batch.call_args.args[0]
    assert [t["task_id"] for t in tasks] == ["gc1_1", "gc1_2"]
    assert tasks[0] == {"task_id": "gc1_1", "action": "Read the port from settings",
                        "file": "server.py", "complexity": "low"}
    assert checker.return_value.check.call_count == 2
    assert any(e["status"] == "goal_incomplete" for e in orch.execution_log)


def test_still_incomplete_after_max_rounds_fails(monkeypatch):
    stuck = GoalVerdict(False, missing=MISSING[:1], reasoning="server.py still reads the ini")
    (ok, extra, reason), orch, checker = _rounds([stuck] * 5, monkeypatch,
                                                 AWOS_GOAL_CHECK_ROUNDS="2")
    assert ok is False
    assert orch._run_task_batch.call_count == 2 and len(extra) == 2
    assert checker.return_value.check.call_count == 3
    assert "server.py still reads the ini" in reason
    assert orch.execution_log[-1]["status"] == "failed"


def _failing_follow_ups(verdicts, monkeypatch):
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    orch = _orchestrator()

    def batch(tasks, ctx, par):
        # What _execute_single_task logs for a follow-up that stops early.
        for t in tasks:
            orch.execution_log.append({"task_id": t["task_id"], "status": "failed",
                                       "reason": "AgentLoop stopped: repeated_tool_call"})
        return [{"task_id": t["task_id"], "success": False, "task": t} for t in tasks]

    orch._run_task_batch.side_effect = batch
    checker = MagicMock()
    checker.return_value.check.side_effect = verdicts
    with patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=(["a.py"], "d")):
        out = orch._goal_check_rounds("g", {"codebase_root": "."}, False)
    return out, orch, checker


def test_failed_follow_up_is_superseded_when_the_goal_is_then_complete(monkeypatch):
    # The checker wrongly sent a finished fix back; the follow-up had nothing
    # to do and stopped. The re-check is the arbiter, so the run succeeds.
    verdicts = [GoalVerdict(False, missing=MISSING[:1], reasoning="left", verified=True),
                GoalVerdict(True, reasoning="already fixed", verified=True)]
    (ok, extra, _), orch, checker = _failing_follow_ups(verdicts, monkeypatch)
    assert ok is True and checker.return_value.check.call_count == 2
    assert extra[0]["superseded"] is True
    assert not [e for e in orch.execution_log if e["status"] == "failed"]
    assert orch.execution_log[-1] == {"task_id": "gc1_1", "status": "superseded",
                                      "reason": "AgentLoop stopped: repeated_tool_call"}


def test_unverified_complete_never_clears_failed_follow_ups(monkeypatch):
    # Round 1 found a real gap; the follow-up failed and was rolled back.
    # Round 2's checker ran out of turns: that "complete" is a fallback, not
    # a finding, and must not turn the partial change into a success.
    verdicts = [GoalVerdict(False, missing=MISSING[:1], reasoning="server.py reads app.ini",
                            verified=True),
                GoalVerdict(True, reasoning="could not be parsed (stop=max_turns); treated as complete")]
    (ok, extra, reason), orch, checker = _failing_follow_ups(verdicts, monkeypatch)
    assert ok is False
    assert not any(r.get("superseded") for r in extra)
    assert [e["task_id"] for e in orch.execution_log if e["status"] == "failed"] == ["gc1_1", "goal_check"]
    assert "not verified complete" in reason


def test_unverified_complete_on_the_first_check_stays_advisory(monkeypatch):
    (ok, extra, _), orch, _ = _rounds([GoalVerdict(True, reasoning="no model client")], monkeypatch)
    assert ok is True and extra == []


def test_checker_out_of_turns_after_a_gap_fails_the_run_end_to_end(monkeypatch, tmp_path):
    # The reviewer's reproduction, with the real GoalChecker and AgentLoop:
    # first an INCOMPLETE verdict, then a checker that only searches until
    # max_turns. The failed follow-up must still count.
    replies = [ModelReply(text=INCOMPLETE)] + [
        ModelReply(text="", tool_calls=[ToolCall(id=f"t{i}", name="find_files",
                                                 arguments={"pattern": f"*{i}.py"})])
        for i in range(10)
    ]
    client = _Scripted(replies)
    factory = lambda **kw: GoalChecker(client=client, max_turns=3)  # noqa: E731
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    orch = _orchestrator()

    def batch(tasks, ctx, par):
        for t in tasks:
            orch.execution_log.append({"task_id": t["task_id"], "status": "failed",
                                       "reason": "2 test(s) failed after the edit"})
        return [{"task_id": t["task_id"], "success": False, "task": t} for t in tasks]

    orch._run_task_batch.side_effect = batch
    with patch("scaffold.agent.goal_check.GoalChecker", side_effect=factory), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=(["a.py"], "d")), \
         patch("scaffold.agent.usage_record.record_api_usage"):
        ok, extra, reason = orch._goal_check_rounds("migrate", {"codebase_root": str(tmp_path)}, False)
    assert ok is False
    assert [r["superseded"] for r in extra if "superseded" in r] == []
    assert any(e["task_id"] == "gc1_1" and e["status"] == "failed" for e in orch.execution_log)


def test_pause_during_follow_ups_stops_the_rounds(monkeypatch):
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    orch = _orchestrator()

    def batch(tasks, ctx, par):
        orch._pause_requested = True
        return [{"task_id": t["task_id"], "success": True, "task": t} for t in tasks]

    orch._run_task_batch.side_effect = batch
    checker = MagicMock()
    checker.return_value.check.side_effect = [GoalVerdict(False, missing=MISSING, verified=True)] * 3
    with patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=([], "")):
        ok, extra, _ = orch._goal_check_rounds("g", {"codebase_root": "."}, False)
    assert ok is False and len(extra) == 2
    assert checker.return_value.check.call_count == 1


def test_incomplete_with_nothing_listed_fails_without_follow_ups(monkeypatch):
    (ok, extra, reason), orch, _ = _rounds(
        [GoalVerdict(False, reasoning="unclear", verified=True)], monkeypatch)
    assert ok is False and extra == []
    orch._run_task_batch.assert_not_called()


@pytest.mark.parametrize("rounds,batches", [("0", 0), ("bogus", 2)])
def test_round_cap_from_env(monkeypatch, rounds, batches):
    stuck = GoalVerdict(False, missing=MISSING[:1], reasoning="left", verified=True)
    (ok, _, _), orch, _ = _rounds([stuck] * 5, monkeypatch, AWOS_GOAL_CHECK_ROUNDS=rounds)
    assert ok is False and orch._run_task_batch.call_count == batches


def test_checker_model_from_env_then_last_agent_model(monkeypatch):
    done = GoalVerdict(True, verified=True)
    _, _, checker = _rounds([done], monkeypatch, AWOS_GOAL_CHECK_MODEL="cheap-model")
    assert checker.call_args.kwargs["model"] == "cheap-model"
    monkeypatch.delenv("AWOS_GOAL_CHECK_MODEL")
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    orch = _orchestrator()
    orch._last_agent_model = "worker-model"
    checker = MagicMock()
    checker.return_value.check.side_effect = [done]
    with patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=([], "")):
        orch._goal_check_rounds("g", {"codebase_root": "."}, False)
    assert checker.call_args.kwargs["model"] == "worker-model"


def test_checker_model_is_haiku_not_the_worker_under_openrouter(monkeypatch):
    done = GoalVerdict(True, verified=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    orch = _orchestrator()
    orch._last_agent_model = "deepseek/deepseek-v4-flash"
    checker = MagicMock()
    checker.return_value.check.side_effect = [done]
    with patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=([], "")):
        orch._goal_check_rounds("g", {"codebase_root": "."}, False)
    assert checker.call_args.kwargs["model"] == "anthropic/claude-haiku-4.5"


def test_failed_follow_ups_keep_going_to_the_round_cap(monkeypatch):
    verdicts = [GoalVerdict(False, missing=MISSING, reasoning="left")] * 3
    (ok, extra, _), orch, checker = _failing_follow_ups(verdicts, monkeypatch)
    assert ok is False and len(extra) == 4
    assert checker.return_value.check.call_count == 3
    assert not any(r.get("superseded") for r in extra)
    assert orch.execution_log[-1]["task_id"] == "goal_check"


def test_disabled_by_env(monkeypatch):
    (ok, extra, _), orch, checker = _rounds([GoalVerdict(False, missing=MISSING)], monkeypatch,
                                            AWOS_GOAL_CHECK="0")
    assert ok is True and extra == []
    checker.assert_not_called()


def test_other_executors_skip_it(monkeypatch):
    (ok, extra, _), orch, checker = _rounds([GoalVerdict(False, missing=MISSING)], monkeypatch,
                                            executor="react")
    assert ok is True
    checker.assert_not_called()


# ── execute_feature: the check decides the run's success ─────────────────────


@pytest.fixture
def feature_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_RUNTIME_SESSION", "true")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "fake-key-for-testing")
    monkeypatch.setenv("AWOS_E2E", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text("# config\n", encoding="utf-8")
    return repo


def _run_feature(repo, rounds_result):
    orch = Orchestrator()
    plan = [{"task_id": 1, "task_type": "edit_file", "path": "config.py", "file": "config.py",
             "action": "Read settings from env", "complexity": "low"}]
    ok_task = lambda task, ctx: {"task_id": task["task_id"], "success": True, "task": task}  # noqa: E731
    with patch.object(orch, "_execute_single_task", side_effect=ok_task), \
         patch.object(orch, "_goal_check_rounds", return_value=rounds_result) as rounds, \
         patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
        result = orch.execute_feature(goal="migrate settings", codebase_root=str(repo),
                                      pre_planned_tasks=plan)
    rounds.assert_called_once()
    return result


def test_partial_change_fails_the_feature(feature_env):
    # Every task passed, but the goal is not done: the run must not succeed.
    result = _run_feature(feature_env, (False, [], "server.py still reads ini"))
    assert result["success"] is False
    assert result["goal_complete"] is False
    assert result["goal_check_reasoning"] == "server.py still reads ini"
    assert result["tasks_completed"] == 1 and result["tasks_failed"] == 0


def test_superseded_follow_up_is_not_a_failed_task(feature_env):
    extra = [{"task_id": "gc1_1", "success": False, "superseded": True},
             {"task_id": "gc1_2", "success": True}]
    result = _run_feature(feature_env, (True, extra, "all readers migrated"))
    assert result["success"] is True
    assert (result["tasks_completed"], result["tasks_failed"]) == (2, 0)


def test_failed_follow_up_fails_the_feature(feature_env):
    extra = [{"task_id": "gc1_1", "success": False}]
    result = _run_feature(feature_env, (False, extra, "not verified complete"))
    assert result["success"] is False and result["tasks_failed"] == 1


def _run_feature_with_checker(repo, verdicts, monkeypatch):
    """execute_feature with the real _goal_check_rounds and a mocked checker."""
    monkeypatch.setenv("AWOS_EXECUTOR", "agent_loop")
    orch = Orchestrator()
    plan = [{"task_id": 1, "task_type": "edit_file", "path": "config.py", "file": "config.py",
             "action": "Read settings from env", "complexity": "low"}]
    ok_task = lambda task, ctx: {"task_id": task["task_id"], "success": True, "task": task}  # noqa: E731
    checker = MagicMock()
    checker.return_value.check.side_effect = verdicts
    with patch.object(orch, "_execute_single_task", side_effect=ok_task), \
         patch("scaffold.agent.goal_check.GoalChecker", checker), \
         patch("scaffold.agent.goal_check.working_tree_changes", return_value=(["config.py"], "d")), \
         patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
        return orch.execute_feature(goal="migrate settings", codebase_root=str(repo),
                                    pre_planned_tasks=plan)


def test_fallback_complete_is_visible_as_unverified(feature_env, monkeypatch, capsys):
    # No verdict even after the fresh second check: the run still passes
    # (fail-open), but the result and the log both say it was not verified.
    fallback = GoalVerdict(True, reasoning="goal check reply could not be parsed "
                                           "(stop=max_turns, 2 check(s)); treated as complete")
    result = _run_feature_with_checker(feature_env, [fallback], monkeypatch)
    assert result["success"] is True
    assert result["goal_check"] == {"verified": False, "source": "fallback", "complete": True,
                                    "reasoning": fallback.reasoning}
    assert "[GOAL CHECK] UNVERIFIED complete [via fallback]" in capsys.readouterr().out


def test_real_verdict_is_reported_verified(feature_env, monkeypatch, capsys):
    done = GoalVerdict(True, reasoning="ran export with both flags", verified=True, source="tool")
    result = _run_feature_with_checker(feature_env, [done], monkeypatch)
    assert result["success"] is True
    assert result["goal_check"] == {"verified": True, "source": "tool", "complete": True,
                                    "reasoning": "ran export with both flags"}
    out = capsys.readouterr().out
    assert "[GOAL CHECK] complete [via tool]" in out and "UNVERIFIED" not in out


# ── Rollback keeps earlier tasks' work ───────────────────────────────────────


def _git_repo(root):
    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    git("init", "-q")
    (root / "config.py").write_text("ORIGINAL\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")


@pytest.mark.parametrize("use_git", [True, False])
def test_failed_task_rolls_back_to_the_last_kept_state(tmp_path, use_git):
    # T1 migrates config.py and is kept; a follow-up then edits config.py and
    # fails. Its rollback must leave T1's work, not the pre-run file.
    from scaffold.agent.git_manager import GitManager

    path = tmp_path / "config.py"
    if use_git:
        _git_repo(tmp_path)
    else:
        path.write_text("ORIGINAL\n", encoding="utf-8")
    git = GitManager(str(tmp_path))
    git.setup("migrate settings")
    git.backup_file(str(path))
    path.write_text("T1 MIGRATED\n", encoding="utf-8")
    git.record_modified(str(path))            # T1 succeeded
    path.write_text("FOLLOW-UP BROKE IT\n", encoding="utf-8")
    git.rollback_file(str(path))              # the follow-up failed
    assert path.read_text(encoding="utf-8") == "T1 MIGRATED\n"


def test_failed_task_on_an_untouched_file_still_restores_the_original(tmp_path):
    from scaffold.agent.git_manager import GitManager

    _git_repo(tmp_path)
    git = GitManager(str(tmp_path))
    git.setup("migrate settings")
    path = tmp_path / "config.py"
    path.write_text("BROKEN\n", encoding="utf-8")
    git.rollback_file(str(path))
    assert path.read_text(encoding="utf-8") == "ORIGINAL\n"
