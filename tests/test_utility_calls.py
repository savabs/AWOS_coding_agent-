"""
Step 2 — stop silent corruption: utility calls are bounded, checked and paid for.

providers.utility_chat (reasoning per role, token floor, one retry with
reasoning off, typed errors, spend on every call), the notebook's rewrite
validation, the integration reviewer's model choice, and _cheap_call.
Hermetic: fake OpenAI-shaped clients, ledger writes patched out.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import integration_reviewer as ir
from scaffold.agent import project_notebook as nb
from scaffold.agent import providers
from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.providers import (
    EmptyReplyError, ModelReplyError, TruncatedReplyError, reasoning_for, utility_chat,
)
from scaffold.agent.self_correction import SelfCorrectionEngine

RECORD = "scaffold.agent.usage_record.record_api_usage"


class _Scripted:
    """OpenAI-shaped client replying from a script of (content, finish_reason)."""

    def __init__(self, *replies, error=None):
        self.calls: list[dict] = []
        self._replies = list(replies)
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        content, finish = self._replies.pop(0) if self._replies else ("", "stop")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                     finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )


MSG = [{"role": "user", "content": "hi"}]


# ── providers: the policy ────────────────────────────────────────────────────


def test_reasoning_per_role_and_planner_alias(monkeypatch):
    for name in ("AWOS_REASONING_NOTEBOOK", "AWOS_REASONING_PLANNER", "AWOS_PLANNER_REASONING"):
        monkeypatch.delenv(name, raising=False)
    assert reasoning_for("notebook") == {"effort": "low"}
    monkeypatch.setenv("AWOS_REASONING_NOTEBOOK", "off")
    assert reasoning_for("notebook") == providers.REASONING_OFF
    monkeypatch.setenv("AWOS_PLANNER_REASONING", "high")
    assert providers.planner_reasoning() == {"effort": "high"}
    monkeypatch.setenv("AWOS_REASONING_PLANNER", "minimal")  # the new name wins
    assert providers.planner_reasoning() == {"effort": "minimal"}
    monkeypatch.setenv("AWOS_REASONING_REVIEW", "default")
    assert reasoning_for("review") is None


def test_good_reply_is_one_call_with_floor_and_recorded_spend():
    client = _Scripted(("answer", "stop"))
    with patch(RECORD) as record:
        text, info = utility_chat(client, "review", "deepseek/deepseek-v4-flash", MSG,
                                  max_tokens=500, request_type="integration_review")
    assert text == "answer" and info["calls"] == 1
    assert client.calls[0]["max_tokens"] == providers.UTILITY_MIN_MAX_TOKENS
    assert "extra_body" not in client.calls[0]  # a direct client gets no reasoning field
    assert record.call_args.kwargs["request_type"] == "integration_review"
    assert record.call_args.kwargs["input_tokens"] == 100


def test_truncated_reply_retries_with_reasoning_off_then_raises():
    client = _Scripted(("partial", "length"), ("still partial", "length"))
    with patch(RECORD) as record, pytest.raises(TruncatedReplyError) as caught:
        utility_chat(client, "notebook", "m", MSG, send_reasoning=True)
    assert [c["extra_body"]["reasoning"] for c in client.calls] == [
        reasoning_for("notebook"), providers.REASONING_OFF]
    assert record.call_count == 2  # the wasted call is paid for too
    assert isinstance(caught.value, ModelReplyError)
    assert caught.value.text == "still partial"


def test_empty_reply_retries_once_and_can_recover():
    client = _Scripted(("", "stop"), ("ok now", "stop"))
    with patch(RECORD) as record:
        text, info = utility_chat(client, "critique", "m", MSG)
    assert text == "ok now" and info["calls"] == 2 and record.call_count == 2

    client = _Scripted(("  ", "stop"), ("", None))
    with patch(RECORD), pytest.raises(EmptyReplyError):
        utility_chat(client, "critique", "m", MSG)
    assert len(client.calls) == 2


def test_transport_errors_propagate():
    client = _Scripted(error=RuntimeError("503"))
    with patch(RECORD), pytest.raises(RuntimeError):
        utility_chat(client, "critique", "m", MSG)


def test_chat_client_max_retries_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    assert providers.chat_client(max_retries=0)._inner.max_retries == 0
    assert providers.chat_client()._inner.max_retries == providers.model_max_retries()
    routed = providers.without_sdk_retries(providers.chat_client())
    assert routed._inner.max_retries == 0
    # Still routed: bare ids are rewritten.
    assert isinstance(routed, providers._Routed)


def test_routed_client_sends_reasoning(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    client = providers.chat_client()
    sent = {}

    def fake_create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")],
            usage=None)

    client._inner.chat.completions.create = fake_create
    with patch(RECORD):
        utility_chat(client, "review", "deepseek-chat", MSG)
    assert sent["extra_body"]["reasoning"] == reasoning_for("review")
    assert sent["model"] == "deepseek/deepseek-chat"


# ── notebook: validate before writing ────────────────────────────────────────

OLD = """## How to work here
- Tests: `python -m pytest -q`
- Run: `python -m ordertool --help`
## Layout and conventions
- Errors: raise OrderToolError (ordertool/errors.py)
- Money is integer cents
## Pitfalls
- --to excluded the last day; fixed with an end-of-day bound
## Past jobs
- add CSV export -> verified
- add --to -> tests pass, not independently verified
"""
NEW_OK = OLD.replace("## Past jobs\n", "## Past jobs\n") + "- add --json -> verified\n"


@pytest.fixture
def notebook_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_NOTEBOOK", "1")
    monkeypatch.delenv("AWOS_PROJECT_ID", raising=False)
    path = nb.notebook_path("proj")
    path.parent.mkdir(parents=True)
    path.write_text(OLD, encoding="utf-8")
    return path


def _nb_update(client):
    with patch(RECORD):
        return nb.update_notebook("proj", "add --json", {"success": True},
                                  "TASK: x\n - run_tests() ok", client=client)


def test_notebook_happy_path_writes_a_backup(notebook_dir, capsys):
    out = _nb_update(_Scripted((NEW_OK, "stop")))
    assert out is not None and "add --json" in notebook_dir.read_text(encoding="utf-8")
    assert notebook_dir.with_suffix(".md.bak").read_text(encoding="utf-8") == OLD
    assert "[notebook] updated" in capsys.readouterr().out


@pytest.mark.parametrize("reply, finish, reason", [
    (NEW_OK, "length", "reply cut off"),
    ("", "stop", "empty reply"),
    (NEW_OK.split("## Past jobs")[0], "stop", "missing 'Past jobs'"),
    ("## How to work here\n- Tests: pytest\n## Layout and conventions\n- x\n"
     "## Pitfalls\n- y\n## Past jobs\n- a -> verified\n- b -> verified\n- c -> verified\n",
     "stop", "chars would replace"),
    (OLD.replace("- --to excluded the last day; fixed with an end-of-day bound\n", "")
     + "- add --json -> verified\n- pad " + "p" * 80 + "\n",
     "stop", "section 'Pitfalls' emptied"),
    (OLD.replace("- add CSV export -> verified\n", "")
        .replace("- Money is integer cents\n", "- Money is integer cents; totals round half-up\n"),
     "stop", "past jobs 2 -> 1"),
])
def test_notebook_rejections_keep_the_file(notebook_dir, capsys, reply, finish, reason):
    out = _nb_update(_Scripted((reply, finish), (reply, finish)))
    assert out is None
    assert notebook_dir.read_text(encoding="utf-8") == OLD
    assert not notebook_dir.with_suffix(".md.bak").exists()
    printed = capsys.readouterr().out
    assert "[notebook] update rejected (" in printed and reason in printed
    assert f"; kept {notebook_dir}" in printed


def test_notebook_past_jobs_may_shrink_at_the_cap():
    old_jobs = "\n".join(f"- job {i} -> verified" for i in range(nb.MAX_PAST_JOBS))
    old = OLD.split("## Past jobs")[0] + "## Past jobs\n" + old_jobs + "\n"
    # The model dropped the two oldest and added one: 14 < 15, allowed at the cap.
    reply = old.replace("- job 0 -> verified\n- job 1 -> verified\n", "") + "- job new -> verified\n"
    new = nb.parse_notebook(reply)
    assert nb.rewrite_rejection(old, reply, new) is None
    below_cap = OLD  # 2 jobs
    fewer = OLD.replace("- add CSV export -> verified\n", "- add CSV export (again) -> verified\n") \
        .replace("- add --to -> tests pass, not independently verified\n", "")
    assert "past jobs" in nb.rewrite_rejection(below_cap, fewer, nb.parse_notebook(fewer))


def test_notebook_first_write_has_no_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_NOTEBOOK", "1")
    out = _nb_update(_Scripted((NEW_OK, "stop")))
    assert out is not None
    assert not nb.notebook_path("proj").with_suffix(".md.bak").exists()


# ── integration reviewer ─────────────────────────────────────────────────────


@pytest.fixture
def review_env(monkeypatch):
    for name in ("AWOS_REVIEW_MODEL", "AWOS_AGENT_MODEL", "AWOS_INTEGRATION_REVIEW",
                 "AWOS_CHEAP_ONLY"):
        monkeypatch.delenv(name, raising=False)


LOG = [{"task_id": 1, "status": "completed", "reason": "ok"}]


def _reviewer(client):
    r = ir.IntegrationReviewer(client=client)
    r._get_diff = lambda _root: "+x = 1\n"
    return r


def test_review_uses_the_run_agent_model_and_records_spend(review_env):
    client = _Scripted(("VERDICT: NEEDS_FIX\nISSUES:\n- cli.py not updated\nSUGGESTIONS:\n- None\n",
                        "stop"))
    with patch(RECORD) as record:
        result = _reviewer(client).review(".", LOG, agent_model="deepseek/deepseek-v4-flash")
    assert client.calls[0]["model"] == "deepseek/deepseek-v4-flash"
    assert result["passed"] is False and result["issues"] == ["cli.py not updated"]
    assert result["suggestions"] == [] and result["model_used"] == "deepseek/deepseek-v4-flash"
    assert record.call_args.kwargs["request_type"] == "integration_review"


def test_review_model_env_wins_then_agent_model_env(review_env, monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_MODEL", "qwen/qwen3.7-plus")
    assert ir.review_model() == "qwen/qwen3.7-plus"
    assert ir.review_model("deepseek/deepseek-v4-flash") == "deepseek/deepseek-v4-flash"
    monkeypatch.setenv("AWOS_REVIEW_MODEL", "anthropic/claude-haiku-4.5")
    assert ir.review_model("deepseek/deepseek-v4-flash") == "anthropic/claude-haiku-4.5"


def test_review_disable_flag_and_no_model_skip(review_env, monkeypatch):
    client = _Scripted(("VERDICT: PASS", "stop"))
    monkeypatch.setenv("AWOS_INTEGRATION_REVIEW", "0")
    result = _reviewer(client).review(".", LOG, agent_model="m")
    assert result["skipped"] is True and "AWOS_INTEGRATION_REVIEW" in result["skip_reason"]
    monkeypatch.delenv("AWOS_INTEGRATION_REVIEW")
    result = _reviewer(client).review(".", LOG)
    assert result["skipped"] is True and "no review model" in result["skip_reason"]
    assert client.calls == []


def test_review_unusable_reply_is_skipped_not_passed_silently(review_env):
    client = _Scripted(("", "length"), ("", "length"))
    with patch(RECORD):
        result = _reviewer(client).review(".", LOG, agent_model="m")
    assert result["skipped"] is True and "unusable review reply" in result["skip_reason"]


# ── _cheap_call ──────────────────────────────────────────────────────────────


def _orch(client):
    orch = Orchestrator.__new__(Orchestrator)
    orch.tracker = None
    orch.worker = SimpleNamespace(opencode_client=client, client=None,
                                  openrouter_client=None, openai_client=None,
                                  anthropic_client=None)
    return orch


def test_cheap_call_keeps_the_engines_signature(monkeypatch):
    monkeypatch.delenv("AWOS_AGENT_MODEL", raising=False)
    client = _Scripted(("a critique", "stop"))
    orch = _orch(client)
    caller = orch._cheap_call  # what PostMortemEngine & co. receive
    with patch(RECORD) as record:
        assert caller("prompt") == "a critique"
    assert client.calls[0]["model"] == "qwen3.7-plus"
    assert record.call_args.kwargs["request_type"] == "cheap_critique"


def test_cheap_call_uses_the_pinned_model(monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_MODEL", "deepseek/deepseek-v4-flash")
    client = _Scripted(("ok", "stop"))
    orch = _orch(MagicMock())
    with patch.object(providers, "chat_client", return_value=client) as make, patch(RECORD):
        assert orch._cheap_call("p", role="post_mortem") == "ok"
    make.assert_called_once_with(max_retries=0)
    assert client.calls[0]["model"] == "deepseek/deepseek-v4-flash"


def test_cheap_call_truncated_is_empty_or_raises(monkeypatch):
    monkeypatch.delenv("AWOS_AGENT_MODEL", raising=False)
    client = _Scripted(*[("half a sent", "length")] * 4)
    orch = _orch(client)
    with patch(RECORD):
        assert orch._cheap_call("p") == ""
        assert len(client.calls) == 2  # one retry, not 3 x 3
        with pytest.raises(ModelReplyError):
            orch._cheap_call("p", raise_bad_reply=True)


def test_cheap_call_transport_error_retries_once(monkeypatch):
    monkeypatch.delenv("AWOS_AGENT_MODEL", raising=False)
    client = _Scripted(error=RuntimeError("503"))
    orch = _orch(client)
    with patch("scaffold.agent.orchestrator.time.sleep"), patch(RECORD):
        assert orch._cheap_call("p") == ""
    assert len(client.calls) == 2


def test_truncated_critique_is_not_persisted(monkeypatch):
    monkeypatch.delenv("AWOS_AGENT_MODEL", raising=False)
    orch = _orch(_Scripted(("The fix is to", "length"), ("The fix is to", "length")))
    orch.self_correction = SelfCorrectionEngine()
    orch.error_store = MagicMock()
    with patch(RECORD):
        orch._persist_worker_failure_pattern({"file": "a.py", "action": "x"}, 1, "boom")
    orch.error_store.save.assert_not_called()

    orch = _orch(_Scripted(("Import the helper first.", "stop")))
    orch.self_correction = SelfCorrectionEngine()
    orch.error_store = MagicMock()
    with patch(RECORD):
        orch._persist_worker_failure_pattern({"file": "a.py", "action": "x"}, 1, "boom")
    orch.error_store.save.assert_called_once()
