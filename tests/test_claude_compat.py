"""Direct-Anthropic path: Claude 5.x param rules, the OpenAI-shaped adapter,
client routing, and the per-run components record (no network)."""
import json
from types import SimpleNamespace

import pytest

from scaffold.agent import claude_compat as cc
from scaffold.agent import providers


# ── param rules ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,rejects", [
    ("claude-haiku-5-5", True),
    ("anthropic/claude-haiku-5.5", True),
    ("claude-sonnet-5-5", True),
    ("claude-opus-4-7", True),
    ("claude-fable-5-1", True),
    ("claude-sonnet-4-6", False),
    ("claude-haiku-4-5", False),
    ("claude-3-5-sonnet-20241022", False),
    ("deepseek-v4-flash", False),
])
def test_claude_rejects_sampling(model, rejects):
    assert cc.claude_rejects_sampling(model) is rejects


def test_sampling_kwargs():
    assert cc.sampling_kwargs("claude-haiku-5-5", 0.3) == {}
    assert cc.sampling_kwargs("deepseek-v4-flash", 0.3) == {"temperature": 0.3}
    assert cc.sampling_kwargs("gpt-4o-mini", None) == {}


# ── adapter ──────────────────────────────────────────────────────────────────

class _FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _anthropic_reply(text="hello", stop="end_turn", **usage):
    u = dict(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
             cache_creation_input_tokens=0)
    u.update(usage)
    return SimpleNamespace(
        id="msg_1", model="claude-haiku-5-5", stop_reason=stop,
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(**u))


def _adapter(response=None):
    inner = SimpleNamespace(messages=_FakeMessages(response or _anthropic_reply()))
    return cc.AnthropicChatClient(inner), inner.messages


def test_request_drops_sampling_and_converts_system(monkeypatch):
    monkeypatch.setenv("AWOS_ANTHROPIC_CACHE", "1")
    monkeypatch.setenv("AWOS_UTILITY_CACHE", "1")
    client, _ = _adapter()
    req = client.build_request(
        model="claude-haiku-5-5", max_tokens=50, temperature=0, top_p=0.9,
        messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": "U"}],
        extra_body={"reasoning": {"effort": "low"}, "provider": {"order": ["x"]}},
        response_format={"type": "json_object"})
    assert "temperature" not in req and "top_p" not in req
    assert req["system"][0]["text"] == "SYS"
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert req["output_config"] == {"effort": "low"}
    assert "extra_body" not in req and "response_format" not in req
    assert req["max_tokens"] == 50


def test_cache_auto_only_for_multi_turn(monkeypatch):
    monkeypatch.delenv("AWOS_UTILITY_CACHE", raising=False)
    monkeypatch.delenv("AWOS_ANTHROPIC_CACHE", raising=False)
    client, _ = _adapter()
    single = client.build_request(model="claude-haiku-5-5", messages=[
        {"role": "system", "content": "S"}, {"role": "user", "content": "U"}])
    assert single["system"] == "S" and single["messages"][0]["content"] == "U"
    multi = client.build_request(model="claude-haiku-5-5", messages=[
        {"role": "system", "content": "S"}, {"role": "user", "content": "U"},
        {"role": "assistant", "content": "A"}, {"role": "user", "content": "fix"}])
    assert multi["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert multi["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    monkeypatch.setenv("AWOS_ANTHROPIC_CACHE", "0")
    off = client.build_request(model="claude-haiku-5-5", messages=multi["messages"][:1] + [
        {"role": "assistant", "content": "A"}, {"role": "user", "content": "x"}])
    assert "system" not in off


def test_request_keeps_temperature_on_older_claude():
    client, _ = _adapter()
    req = client.build_request(model="claude-sonnet-4-6", temperature=0.2,
                               messages=[{"role": "user", "content": "U"}])
    assert req["temperature"] == 0.2


def test_reasoning_off_maps_to_low_effort():
    client, _ = _adapter()
    req = client.build_request(model="claude-haiku-5-5",
                               messages=[{"role": "user", "content": "U"}],
                               extra_body={"reasoning": {"enabled": False}})
    assert req["output_config"] == {"effort": "low"}


def test_non_claude_model_refused():
    client, _ = _adapter()
    with pytest.raises(ValueError):
        client.chat.completions.create(model="deepseek-v4-flash",
                                       messages=[{"role": "user", "content": "x"}])


def test_stream_refused_with_typeerror():
    client, _ = _adapter()
    with pytest.raises(TypeError):
        client.chat.completions.create(model="claude-haiku-5-5", stream=True,
                                       messages=[{"role": "user", "content": "x"}])


def test_response_is_openai_shaped_with_cache_usage():
    client, calls = _adapter(_anthropic_reply(
        "ok", stop="max_tokens", input_tokens=10, cache_read_input_tokens=80,
        cache_creation_input_tokens=5))
    resp = client.chat.completions.create(model="claude-haiku-5-5",
                                          messages=[{"role": "user", "content": "x"}])
    assert resp.choices[0].message.content == "ok"  # thinking block dropped
    assert resp.choices[0].finish_reason == "length"
    assert resp.usage.prompt_tokens == 95
    assert resp.usage.prompt_tokens_details.cached_tokens == 80
    tok = providers.usage_tokens(resp.usage)
    assert tok == {"input": 95, "cached": 80, "cache_write": 5, "output": 20}


def test_utility_chat_on_adapter_records_spend_and_drops_temperature(monkeypatch):
    client, calls = _adapter()
    seen = []
    monkeypatch.setattr(providers, "_record_utility_spend",
                        lambda rt, model, resp, tracker: seen.append(rt) or 0.001)
    text, info = providers.utility_chat(client, "review", "claude-haiku-5-5",
                                        [{"role": "user", "content": "hi"}], temperature=0.3)
    assert text == "hello" and info["calls"] == 1
    assert seen == ["review"]
    assert "temperature" not in calls.calls[0]
    assert calls.calls[0]["output_config"]["effort"] in cc._EFFORTS


def test_utility_chat_drops_temperature_for_claude5_via_any_client(monkeypatch):
    sent = []

    class _Chat:
        def create(self, **kw):
            sent.append(kw)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="x"), finish_reason="stop")], usage=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Chat()))
    monkeypatch.setattr(providers, "_record_utility_spend", lambda *a, **k: 0.0)
    providers.utility_chat(client, "review", "anthropic/claude-haiku-5.5",
                           [{"role": "user", "content": "x"}], temperature=0.3, top_p=0.5)
    assert "temperature" not in sent[0] and "top_p" not in sent[0]


# ── routing ──────────────────────────────────────────────────────────────────

@pytest.fixture
def direct_env(monkeypatch):
    for name in ("OPENROUTER_API_KEY", "AWOS_PROVIDER", "AWOS_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AWOS_AGENT_MODEL", "claude-haiku-5-5")
    return monkeypatch


def test_default_client_is_adapter_when_run_model_is_claude(direct_env):
    assert isinstance(providers.chat_client(max_retries=0), cc.AnthropicChatClient)


def test_claude_model_with_other_direct_key_goes_to_anthropic(direct_env):
    c = providers.chat_client("opencode-key", "https://opencode.example/v1",
                              model="claude-haiku-5-5")
    assert isinstance(c, cc.AnthropicChatClient)


def test_non_claude_model_keeps_its_direct_provider(direct_env):
    c = providers.chat_client("ds-key", "https://api.deepseek.com", model="deepseek-chat")
    assert not isinstance(c, cc.AnthropicChatClient) and c is not None


def test_direct_key_callers_unchanged_without_model(direct_env):
    c = providers.chat_client("ds-key", "https://api.deepseek.com")
    assert not isinstance(c, cc.AnthropicChatClient)


def test_no_adapter_without_anthropic_key(direct_env):
    direct_env.delenv("ANTHROPIC_API_KEY")
    assert providers.chat_client() is None


def test_no_adapter_for_non_claude_run_model(direct_env):
    direct_env.setenv("AWOS_AGENT_MODEL", "deepseek-v4-flash")
    assert providers.chat_client() is None


def test_openrouter_still_wins(direct_env):
    direct_env.setenv("OPENROUTER_API_KEY", "or-key")
    assert not isinstance(providers.chat_client(model="claude-haiku-5-5"),
                          cc.AnthropicChatClient)


def test_cheap_planner_uses_anthropic_for_claude_planner(direct_env):
    from scaffold.agent.cheap_planner import CheapPlanner
    direct_env.setenv("AWOS_PLANNER_MODEL", "claude-haiku-5-5")
    p = CheapPlanner()
    assert isinstance(p.client, cc.AnthropicChatClient)
    req = p._plan_request("plan this", {"effort": "low"})
    assert "temperature" not in req
    assert req["extra_body"] == {"reasoning": {"effort": "low"}}


def test_one_shot_client_on_direct_anthropic(direct_env):
    from scaffold.agent import one_shot
    client = one_shot.one_shot_client()
    assert isinstance(client, cc.AnthropicChatClient)
    assert one_shot._sends_reasoning(client)


def test_notebook_client_on_direct_anthropic(direct_env):
    from scaffold.agent import project_notebook
    direct_env.setenv("AWOS_NOTEBOOK_MODEL", "claude-haiku-5-5")
    assert isinstance(project_notebook._default_client(), cc.AnthropicChatClient)


# ── components record ────────────────────────────────────────────────────────

def _orch():
    from scaffold.agent.orchestrator import Orchestrator, _new_components
    o = Orchestrator.__new__(Orchestrator)
    o._components = _new_components()
    return o


def test_components_from_report_and_sites():
    o = _orch()
    o._component("acceptance", status="ran", active=True)
    o._component_add("acceptance", generated=6, kept=3)
    o._component_add("acceptance", generated=2, kept=1)
    o._component("one_shot", status="used", result="fell_back")
    comps = o._finish_components({
        "plan_source": "planner", "success": True,
        "integration_review": {"passed": True, "skipped": True, "skip_reason": "single task"}})
    assert comps["planner"]["status"] == "ok"
    assert comps["acceptance"] == {"status": "ran", "active": True, "generated": 8, "kept": 4}
    assert comps["one_shot"]["status"] == "used"
    assert comps["integration_review"] == {"status": "skipped", "reason": "single task"}
    assert comps["arbiter"] == {"status": "not_run"}


def test_planner_failure_and_skip_recorded():
    o = _orch()
    failed = o._finish_components({"plan_source": "goal_as_task",
                                   "planner_errors": ["primary planner failed: 400"]})
    assert failed["planner"]["status"] == "failed" and "400" in failed["planner"]["error"]
    skipped = _orch()._finish_components({"plan_source": "skipped_small_goal"})
    assert skipped["planner"] == {"status": "skipped", "source": "skipped_small_goal"}


def test_execute_feature_emits_components_line(monkeypatch, tmp_path, capsys):
    from scaffold.agent.orchestrator import Orchestrator
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_SERIES_ARM", "off")
    o = Orchestrator.__new__(Orchestrator)

    def _impl(self, *a, **k):
        self._component("goal_check", status="ran")
        return {"success": True, "plan_source": "planner"}

    monkeypatch.setattr(Orchestrator, "_execute_feature_impl", _impl)
    report = o.execute_feature("goal", str(tmp_path))
    assert report["components"]["goal_check"]["status"] == "ran"
    line = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[COMPONENTS] ")]
    assert len(line) == 1
    assert json.loads(line[0][len("[COMPONENTS] "):])["planner"]["status"] == "ok"
    assert json.loads((tmp_path / "components.json").read_text())["goal_check"]["status"] == "ran"


# ── Claude 5.x thinks by default ─────────────────────────────────────────────

def _thinking_only(stop):
    reply = _anthropic_reply("", stop=stop, output_tokens=4096)
    reply.content = [SimpleNamespace(type="thinking", thinking="")]
    reply.usage.output_tokens_details = SimpleNamespace(thinking_tokens=4096)
    return reply


def test_thinking_tokens_reach_usage_and_call_log():
    from scaffold.agent import llm_call_log
    client, _ = _adapter(_thinking_only("max_tokens"))
    resp = client.chat.completions.create(model="claude-haiku-5-5",
                                          messages=[{"role": "user", "content": "x"}])
    assert resp.usage.completion_tokens_details.reasoning_tokens == 4096
    assert llm_call_log.fields_from_response(resp)["reasoning_tokens"] == 4096
    raw = llm_call_log.fields_from_response(_thinking_only("end_turn"))
    assert raw["reasoning_tokens"] == 4096 and raw["visible_chars"] == 0


@pytest.mark.parametrize("stop,error", [
    ("max_tokens", providers.TruncatedReplyError),
    ("end_turn", providers.EmptyReplyError),
])
def test_thinking_only_reply_is_retried_then_refused(monkeypatch, stop, error):
    client, calls = _adapter(_thinking_only(stop))
    monkeypatch.setattr(providers, "_record_utility_spend", lambda *a, **k: 0.0)
    with pytest.raises(error):
        providers.utility_chat(client, "review", "claude-haiku-5-5",
                               [{"role": "user", "content": "x"}])
    assert len(calls.calls) == 2  # the retry asks at low effort
    assert calls.calls[1]["output_config"] == {"effort": "low"}
