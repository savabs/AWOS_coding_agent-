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
    # partial change.
    v = parse_verdict(f'{{"complete": {value}, "missing": [], "reasoning": "r"}}')
    assert v.complete is False


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
    # The sandbox lets run_command write the workspace, so the checker never gets it.
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
    assert GoalChecker(client=object()).max_turns == 25
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
    for name in ("AWOS_GOAL_CHECK_ROUNDS", "AWOS_GOAL_CHECK_MODEL", "AWOS_GOAL_CHECK_MAX_TURNS"):
        monkeypatch.delenv(name, raising=False)
    # conftest turns the check off for the rest of the suite; here it is
    # the subject (and its checker is always mocked).
    monkeypatch.setenv("AWOS_GOAL_CHECK", "1")


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
