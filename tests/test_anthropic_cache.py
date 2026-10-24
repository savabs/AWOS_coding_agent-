"""Prompt-cache breakpoints on direct Anthropic calls (2026-10-11)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scaffold", REPO / "scaffold" / "agent"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scaffold.agent.agent_loop import with_cache_breakpoints  # noqa: E402

EPH = {"type": "ephemeral"}


def test_marks_tools_system_and_last_message():
    tools = [{"name": "a"}, {"name": "b"}]
    msgs = [{"role": "user", "content": "goal"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "x"}]}]
    system, out, tl = with_cache_breakpoints("sys", msgs, tools)
    assert system == [{"type": "text", "text": "sys", "cache_control": EPH}]
    assert tl[-1]["cache_control"] == EPH and "cache_control" not in tl[0]
    assert out[-1]["content"][-1]["cache_control"] == EPH
    assert "cache_control" not in str(out[:-1])
    # the caller's objects are untouched
    assert "cache_control" not in tools[-1]
    assert "cache_control" not in msgs[-1]["content"][-1]


def test_string_last_message_becomes_a_block():
    _, out, _ = with_cache_breakpoints("s", [{"role": "user", "content": "hi"}], [])
    assert out[0]["content"] == [{"type": "text", "text": "hi", "cache_control": EPH}]


def test_off_switch(monkeypatch):
    monkeypatch.setenv("AWOS_ANTHROPIC_CACHE", "0")
    msgs = [{"role": "user", "content": "hi"}]
    assert with_cache_breakpoints("s", msgs, [{"name": "a"}]) == ("s", msgs, [{"name": "a"}])


# ── C11: cache TTL, keep-alive through a long gate, 1-hour write pricing ─────

import copy  # noqa: E402
import math  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from scaffold.agent import agent_loop as al  # noqa: E402
from scaffold.agent import loop_executor as lx  # noqa: E402
from scaffold.agent import providers  # noqa: E402

EPH_1H = {"type": "ephemeral", "ttl": "1h"}
HAIKU = "claude-haiku-5-5"
TOOLS = [{"name": "bash", "description": "run", "input_schema": {"type": "object"}}]
REG = SimpleNamespace(anthropic_schemas=lambda: [dict(t) for t in TOOLS])


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("AWOS_ANTHROPIC_CACHE", al.CACHE_TTL_ENV, al.KEEPALIVE_INTERVAL_ENV,
                 al.KEEPALIVE_RETRY_P_ENV, al.KEEPALIVE_MAX_ENV, "AWOS_PRICE_TABLE",
                 "AWOS_PROVIDER", "AWOS_REASONING_AGENT"):
        monkeypatch.delenv(name, raising=False)


class FakeSDK:
    """Fake anthropic.Anthropic(): records each messages.create body; a
    max_tokens=0 request answers like the API (no content, stop max_tokens)
    and reports the prefix as a cache read."""

    def __init__(self, script=(), prefix=10_000):
        self.script = list(script)
        self.calls: list[dict] = []
        self.prefix = prefix
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        if kw.get("max_tokens") == 0:
            usage = SimpleNamespace(input_tokens=0, output_tokens=0,
                                    cache_read_input_tokens=self.prefix,
                                    cache_creation_input_tokens=0)
            return SimpleNamespace(content=[], usage=usage, stop_reason="max_tokens")
        kind, payload = self.script.pop(0) if self.script else ("text", "ok")
        if kind == "bash":
            content = [SimpleNamespace(type="tool_use", id=f"tu{len(self.calls)}", name="bash",
                                       input={"command": payload})]
            stop = "tool_use"
        else:
            content = [SimpleNamespace(type="text", text=payload)]
            stop = "end_turn"
        usage = SimpleNamespace(input_tokens=100, output_tokens=20,
                                cache_read_input_tokens=self.prefix - 500,
                                cache_creation_input_tokens=500)
        return SimpleNamespace(content=content, usage=usage, stop_reason=stop)

    def pings(self):
        return [c for c in self.calls if c.get("max_tokens") == 0]


def _wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


# request bodies: TTL modes

def test_default_ttl_is_auto_and_bodies_stay_byte_identical():
    assert al.anthropic_cache_ttl() == "auto"
    sdk = FakeSDK()
    al.AnthropicToolClient(sdk, HAIKU).complete("sys", [{"role": "user", "content": "hi"}], REG)
    body = sdk.calls[0]
    assert body["tools"][-1]["cache_control"] == EPH
    assert body["system"][0]["cache_control"] == EPH
    assert body["messages"][-1]["content"][-1]["cache_control"] == EPH
    assert "ttl" not in str(body)


@pytest.mark.parametrize("value,expect", [("5m", EPH), ("auto", EPH), ("1h", EPH_1H),
                                          ("bogus", EPH)])
def test_ttl_env_sets_every_breakpoint(monkeypatch, value, expect):
    monkeypatch.setenv(al.CACHE_TTL_ENV, value)
    sdk = FakeSDK()
    al.AnthropicToolClient(sdk, HAIKU).complete("sys", [{"role": "user", "content": "hi"}], REG)
    body = sdk.calls[0]
    marks = [body["tools"][-1]["cache_control"], body["system"][0]["cache_control"],
             body["messages"][-1]["content"][-1]["cache_control"]]
    assert marks == [expect] * 3


def test_explicit_ttl_argument_and_markers_are_not_shared():
    system, msgs, tools = with_cache_breakpoints("s", [{"role": "user", "content": "x"}],
                                                 [{"name": "a"}], ttl="1h")
    assert system[0]["cache_control"] == EPH_1H == tools[-1]["cache_control"]
    system[0]["cache_control"]["ttl"] = "mutated"
    assert al._EPHEMERAL_1H == EPH_1H and msgs[-1]["content"][-1]["cache_control"] == EPH_1H


# cost math

def test_price_table_has_1h_write_at_2x_for_claude_only():
    t = providers.price_table(HAIKU)
    assert t["cache_write"] == pytest.approx(0.125)       # 1.25 x $0.10
    assert t["cache_write_1h"] == pytest.approx(0.20)     # 2 x $0.10
    flash = providers.price_table("deepseek-v4-flash")
    assert flash["cache_write_1h"] == pytest.approx(flash["cache_write"])


def test_estimate_cost_prices_1h_writes_at_the_1h_rate():
    # 10k prompt: 6k read, 4k written of which 3k with the 1h TTL; 1k out.
    t = providers.price_table(HAIKU)
    expect = (6000 * t["cache_read"] + 1000 * t["cache_write"] + 3000 * t["cache_write_1h"]
              + 1000 * t["output"]) / 1e6
    got = providers.estimate_cost_cached(HAIKU, 10_000, 1000, 6000, 4000, 3000)
    assert got == pytest.approx(expect)
    assert al.estimate_cost(HAIKU, 10_000, 1000, 6000, 4000, 3000) == pytest.approx(expect)
    # without the 1h split everything is 5-minute priced, as before
    assert providers.estimate_cost_cached(HAIKU, 10_000, 1000, 6000, 4000) == pytest.approx(
        (6000 * t["cache_read"] + 4000 * t["cache_write"] + 1000 * t["output"]) / 1e6)
    # a 1h count above the write count is clamped to it
    assert providers.estimate_cost_cached(HAIKU, 10_000, 0, 0, 100, 999) == pytest.approx(
        100 * t["cache_write_1h"] / 1e6 + 9900 * t["input"] / 1e6)


def test_1h_override_in_price_table_file(tmp_path, monkeypatch):
    import json
    path = tmp_path / "p.json"
    path.write_text(json.dumps({HAIKU: {"input": 1.0}}))
    monkeypatch.setenv("AWOS_PRICE_TABLE", str(path))
    t = providers.price_table(HAIKU)
    assert (t["cache_write"], t["cache_write_1h"]) == (pytest.approx(1.25), pytest.approx(2.0))
    path.write_text(json.dumps({HAIKU: {"cache_write_1h": 0.3}}))
    assert providers.price_table(HAIKU)["cache_write_1h"] == 0.3


def _usage_1h(write_1h, write_5m=0, read=0, fresh=10, out=5):
    return SimpleNamespace(input_tokens=fresh, output_tokens=out, cache_read_input_tokens=read,
                           cache_creation_input_tokens=write_1h + write_5m,
                           cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=write_1h,
                                                          ephemeral_5m_input_tokens=write_5m))


def test_usage_1h_breakdown_reaches_reply_and_response_cost():
    usage = _usage_1h(write_1h=8000, write_5m=1000, read=500)
    assert providers.usage_cache_write_1h(usage) == 8000
    assert providers.usage_cache_write_1h({"cache_creation_input_tokens": 10,
                                           "cache_creation": {"ephemeral_1h_input_tokens": 50}}) == 10
    assert providers.usage_cache_write_1h(None) == 0
    assert providers.usage_cache_write_1h(SimpleNamespace(prompt_tokens=5)) == 0
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], usage=usage)
    cost, src = providers.response_cost(HAIKU, response)
    assert src == "estimate"
    assert cost == pytest.approx(providers.estimate_cost_cached(HAIKU, 9510, 5, 500, 9000, 8000))

    sdk = FakeSDK()
    sdk.messages.create = lambda **kw: response
    reply = al.AnthropicToolClient(sdk, HAIKU).complete("s", [{"role": "user", "content": "x"}],
                                                        REG)
    assert (reply.input_tokens, reply.cached_tokens, reply.cache_write_tokens,
            reply.cache_write_1h_tokens) == (9510, 500, 9000, 8000)


# break-even ping budget

def test_ping_budget_is_the_break_even():
    # Haiku 5.5: read 0.1x, 5m write 1.25x -> floor(p * 1.15 / 0.1)
    assert al.keepalive_ping_budget(HAIKU) == 5                       # p = 0.5
    assert al.keepalive_ping_budget(HAIKU, retry_p=1.0) == 11
    assert al.keepalive_ping_budget(HAIKU, retry_p=0.0) == 0
    # Sonnet 5.5 in the table: read 0.05x -> floor(0.5 * 1.2 / 0.05)
    assert al.keepalive_ping_budget("claude-sonnet-5-5") == 12
    # no write premium over a read worth saving, or unpriced: no pings
    assert al.keepalive_ping_budget("totally-unknown") == 0
    for model in (HAIKU, "claude-sonnet-5-5"):
        t = providers.price_table(model)
        k = al.keepalive_ping_budget(model, retry_p=1.0)
        # k pings cost less than the miss they prevent; k + 1 would not
        assert k * t["cache_read"] <= t["cache_write"] - t["cache_read"] + 1e-12
        assert (k + 1) * t["cache_read"] > t["cache_write"] - t["cache_read"]


def test_ping_budget_env(monkeypatch):
    monkeypatch.setenv(al.KEEPALIVE_RETRY_P_ENV, "0.2")
    assert al.keepalive_ping_budget(HAIKU) == math.floor(0.2 * 1.15 / 0.1 + 1e-9)
    monkeypatch.setenv(al.KEEPALIVE_MAX_ENV, "3")
    assert al.keepalive_ping_budget(HAIKU) == 3


def test_keepalive_interval_env(monkeypatch):
    assert al.keepalive_interval_s() == 270.0
    monkeypatch.setenv(al.KEEPALIVE_INTERVAL_ENV, "9999")
    assert al.keepalive_interval_s() == 290.0           # never past the 5-minute TTL
    monkeypatch.setenv(al.KEEPALIVE_INTERVAL_ENV, "nope")
    assert al.keepalive_interval_s() == 270.0


# the ping itself

def test_ping_repeats_the_last_body_with_max_tokens_zero():
    sdk = FakeSDK()
    client = al.AnthropicToolClient(sdk, HAIKU)
    times = iter([100.0, 400.0])
    client._clock = lambda: next(times)
    client.complete("sys", [{"role": "user", "content": "goal"}], REG)
    assert client._last_request_at == 100.0
    reply = client.keep_alive_ping()
    real, ping = sdk.calls
    assert ping["max_tokens"] == 0
    assert {k: v for k, v in ping.items() if k != "max_tokens"} == \
           {k: v for k, v in real.items() if k != "max_tokens"}
    assert client._last_request_at == 400.0              # TTL restarts from the ping
    assert (reply.output_tokens, reply.text, reply.tool_calls) == (0, "", [])
    assert reply.cached_tokens == 10_000
    # one cache read of the prefix, nothing else
    assert al.estimate_cost(HAIKU, reply.input_tokens, 0, reply.cached_tokens) == \
        pytest.approx(10_000 * 0.01 / 1e6)


def test_no_ping_without_a_previous_request():
    assert al.AnthropicToolClient(FakeSDK(), HAIKU).keep_alive_ping() is None


def _warm_client(sdk=None, **complete_kw):
    sdk = sdk or FakeSDK()
    client = al.AnthropicToolClient(sdk, HAIKU)
    client.complete("sys", [{"role": "user", "content": "goal"}], REG, **complete_kw)
    return sdk, client


def test_keep_warm_pings_until_the_budget_then_stops(monkeypatch):
    monkeypatch.setenv(al.KEEPALIVE_MAX_ENV, "3")
    sdk, client = _warm_client()
    warm = client.keep_warm()
    warm.interval = 0.01
    with warm:
        assert _wait_for(lambda: len(sdk.pings()) == 3)
        time.sleep(0.05)                                   # budget spent: no 4th
    assert len(sdk.pings()) == 3 and len(warm.replies) == 3


def test_keep_warm_short_wait_sends_nothing():
    sdk, client = _warm_client()
    with client.keep_warm() as warm:                       # 270 s interval
        time.sleep(0.02)
    assert sdk.pings() == [] and warm.replies == []


@pytest.mark.parametrize("case", ["no_continue", "ttl_5m", "ttl_1h", "cache_off",
                                  "tool_choice", "fresh"])
def test_keep_warm_disabled_cases(monkeypatch, case):
    kw = {"tool_choice": "bash"} if case == "tool_choice" else {}
    if case == "ttl_5m":
        monkeypatch.setenv(al.CACHE_TTL_ENV, "5m")
    if case == "ttl_1h":
        monkeypatch.setenv(al.CACHE_TTL_ENV, "1h")
    if case == "cache_off":
        monkeypatch.setenv("AWOS_ANTHROPIC_CACHE", "0")
    if case == "fresh":
        sdk, client = FakeSDK(), al.AnthropicToolClient(FakeSDK(), HAIKU)
    else:
        sdk, client = _warm_client(**kw)
    warm = client.keep_warm(may_continue=case != "no_continue")
    assert warm.budget == 0
    warm.interval = 0.001
    with warm:
        time.sleep(0.02)
    assert sdk.pings() == []


def test_failed_ping_stops_quietly(monkeypatch):
    monkeypatch.setenv(al.KEEPALIVE_MAX_ENV, "5")
    sdk, client = _warm_client()

    def boom(**kw):
        sdk.calls.append(kw)
        raise RuntimeError("overloaded")
    sdk.messages.create = boom
    warm = client.keep_warm()
    warm.interval = 0.001
    with warm:
        assert _wait_for(lambda: len(sdk.calls) >= 2)
        time.sleep(0.02)
    assert len(sdk.calls) == 2 and warm.replies == []


# the loop: keep-alive during the success gate, accounted in the run's cost

def _loop(tmp_path, sdk, gate, **kw):
    ws = tmp_path / "ws"
    ws.mkdir()

    class Sandbox:
        backend = "fake"

        def run(self, command, timeout_sec=120):
            from scaffold.agent.sandbox import SandboxResult
            return SandboxResult(0, "ok\n", "", False, 0.0)

        def close(self):
            pass

    client = al.AnthropicToolClient(sdk, HAIKU)
    ex = lx.LoopExecutor(client, lx.build_loop_registry(Sandbox()), "Fix it",
                         workspace=str(ws), gate=gate, **kw)
    return ex.run()


def test_loop_keeps_cache_warm_through_a_long_red_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "keepalive_interval_s", lambda: 0.01)
    monkeypatch.setenv(al.KEEPALIVE_MAX_ENV, "2")
    sdk = FakeSDK([("bash", "ls"), ("text", f"{lx.SUBMIT_MARKER}\ntry 1"),
                   ("text", f"{lx.SUBMIT_MARKER}\ntry 2")])
    rounds = []

    def gate(files, submission):                 # "the full suite": outlasts the TTL
        rounds.append(submission)
        if len(rounds) == 1:
            assert _wait_for(lambda: len(sdk.pings()) == 2)
            return {"green": False, "retry": True, "feedback": "test_x failed"}
        return {"green": True}

    run = _loop(tmp_path, sdk, gate)
    assert run.success and run.gate_retries == 1
    real = [c for c in sdk.calls if c["max_tokens"] != 0]
    pings = sdk.pings()
    assert len(real) == 3 and len(pings) == 2
    # each ping is the submit request again (same prefix, same breakpoints) ...
    submit_req = real[1]
    for p in pings:
        assert {k: v for k, v in p.items() if k != "max_tokens"} == \
               {k: v for k, v in submit_req.items() if k != "max_tokens"}
    # ... and the request after the gate extends that prefix
    n = len(submit_req["messages"])
    strip = lambda m: {**m, "content": [{k: v for k, v in b.items() if k != "cache_control"}
                                        for b in m["content"]]
                       if isinstance(m["content"], list) else m["content"]}
    assert [strip(m) for m in real[2]["messages"][:n - 1]] == \
           [strip(m) for m in submit_req["messages"][:n - 1]]
    # the pings' cache reads are in the run's cost and tokens
    t = providers.price_table(HAIKU)
    call = (100 * t["input"] + 9500 * t["cache_read"] + 500 * t["cache_write"]
            + 20 * t["output"]) / 1e6
    ping = 10_000 * t["cache_read"] / 1e6
    assert run.cost_usd == pytest.approx(3 * call + 2 * ping)
    assert run.cached_tokens == 3 * 9500 + 2 * 10_000


def test_loop_no_ping_when_the_gate_cannot_send_feedback(tmp_path, monkeypatch):
    # Last feedback round spent: whatever the gate says, no request on this
    # prefix follows, so a warm cache would never be read.
    monkeypatch.setattr(al, "keepalive_interval_s", lambda: 0.01)
    sdk = FakeSDK([("text", f"{lx.SUBMIT_MARKER}\ndone")])

    def gate(files, submission):
        time.sleep(0.05)
        return {"green": True}

    run = _loop(tmp_path, sdk, gate, gate_retries_=0)
    assert run.success and sdk.pings() == []


def test_loop_short_gate_costs_nothing_extra(tmp_path):
    sdk = FakeSDK([("text", f"{lx.SUBMIT_MARKER}\ndone")])
    run = _loop(tmp_path, sdk, lambda f, s: {"green": True})
    assert run.success and sdk.pings() == [] and len(sdk.calls) == 1
