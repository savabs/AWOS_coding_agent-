"""ResponseCache: exact-request keys, deterministic-only caching, sliding TTL,
errors as misses, and the two callers (gui_chat / unified_agent) keying on what
they actually send."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scaffold.agent.response_cache import ResponseCache, cacheable  # noqa: E402

T0 = {"temperature": 0, "max_tokens": 100}
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Explain X"}]


def key(c, **kw):
    args = dict(provider="openrouter", model_version="", template=None, messages=MSGS,
                sampling=T0)
    args.update(kw)
    model = args.pop("model", "deepseek-chat")
    return c.make_key(model, **args)


def test_key_covers_every_request_field():
    c = ResponseCache()
    k = key(c)
    assert len(k) == 64 and k == key(c)                        # sha256, deterministic
    assert key(c, provider="deepseek") != k
    assert key(c, model="deepseek-reasoner") != k
    assert key(c, model_version="2026-10") != k
    assert key(c, template="grammar-a") != key(c, template="grammar-b")
    assert key(c, messages=[{"role": "system", "content": "other"}, MSGS[1]]) != k
    assert key(c, sampling={"temperature": 0, "max_tokens": 200}) != k
    assert key(c, sampling={"temperature": 0, "max_tokens": 100, "top_p": 0.5}) != k


def test_no_normalisation():
    c = ResponseCache()
    a = key(c, messages=[{"role": "user", "content": "Explain X"}])
    assert key(c, messages=[{"role": "user", "content": "explain x"}]) != a
    assert key(c, messages=[{"role": "user", "content": "Explain  X"}]) != a
    assert key(c, messages=[{"role": "user", "content": " Explain X"}]) != a


def test_system_and_user_input_are_part_of_the_messages():
    c = ResponseCache()
    a = c.make_key("m", "q", system="s1", sampling=T0)
    assert a != c.make_key("m", "q", system="s2", sampling=T0)
    assert a == c.make_key("m", messages=[{"role": "system", "content": "s1"},
                                          {"role": "user", "content": "q"}], sampling=T0)


def test_only_temperature_zero_is_cached():
    c = ResponseCache()
    assert cacheable({"temperature": 0}) and cacheable({"temperature": 0.0})
    for s in (None, {}, {"temperature": None}, {"temperature": 0.3}, {"temperature": 1},
              {"temperature": "hot"}, {"temperature": True}):
        assert not cacheable(s)
        assert key(c, sampling=s) is None
    c.set(None, "x")
    assert c.get(None) is None and c.stats()["entries"] == 0


def test_sliding_ttl_with_written_and_read_times():
    now = [1000.0]
    c = ResponseCache(ttl_seconds=10, clock=lambda: now[0])
    k = key(c)
    c.set(k, "answer", model="m")
    now[0] += 8
    assert c.get(k) == "answer"                                # read refreshes the window
    e = c._cache[k]
    assert e.written_at == 1000.0 and e.read_at == 1008.0
    now[0] += 8                                                # 16 s after write, 8 after read
    assert c.get(k) == "answer"
    now[0] += 11
    assert c.get(k) is None and k not in c._cache              # idle longer than ttl


def test_max_age_caps_sliding_ttl():
    now = [0.0]
    c = ResponseCache(ttl_seconds=10, max_age_seconds=25, clock=lambda: now[0])
    k = key(c)
    c.set(k, "a")
    for _ in range(3):
        now[0] += 8
        assert c.get(k) == "a"
    now[0] += 2                                                # 26 s since written
    assert c.get(k) is None


def test_errors_are_misses():
    c = ResponseCache()

    class Boom:
        def __str__(self):
            raise RuntimeError("x")

    # default=str in json.dumps calls str() -> raises -> no key, never an exception
    assert c.make_key("m", messages=[{"content": Boom()}], sampling=T0) is None
    assert c.get(object()) is None                             # unhashable/odd key -> miss
    c._cache["bad"] = "not-an-entry"
    assert c.get("bad") is None
    c.set(key(c), None)                                        # non-string response ignored
    assert c.stats()["errors"] >= 2


def test_eviction_keeps_recently_read():
    now = [0.0]
    c = ResponseCache(ttl_seconds=10_000, clock=lambda: now[0])
    c.MAX_ENTRIES = 3
    keys = [key(c, messages=[{"role": "user", "content": str(i)}]) for i in range(4)]
    for k in keys[:3]:
        now[0] += 1
        c.set(k, "v")
    now[0] += 1
    c.get(keys[0])                                             # oldest write, newest read
    c.set(keys[3], "v")
    assert keys[0] in c._cache and keys[1] not in c._cache


def test_gui_chat_keys_use_real_route(monkeypatch, tmp_path):
    from scaffold.agent import gui_chat

    svc = gui_chat.GuiChatService.__new__(gui_chat.GuiChatService)
    svc.response_cache = ResponseCache()
    session = gui_chat.ChatSession(chat_id="t")
    for var in ("OPENROUTER_API_KEY", "OPENCODE_GO_API_KEY", "DEEPSEEK_API_KEY",
                "ANTHROPIC_API_KEY", "AWOS_CHEAP_ONLY", "AWOS_PREMIUM_BUDGET"):
        monkeypatch.delenv(var, raising=False)
    calls = []
    real = svc.response_cache.make_key

    def spy(model, user_input=None, **kw):
        calls.append((model, kw))
        return real(model, user_input, **kw)

    svc.response_cache.make_key = spy
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert svc._qa_run_cache_key("SYS", "USER", session) is None   # temperature 0.3
    model, kw = calls[-1]
    assert model == "deepseek-chat" and kw["provider"] == "deepseek"
    assert kw["messages"][0] == {"role": "system", "content": "SYS"}
    assert kw["messages"][-1] == {"role": "user", "content": "USER"}
    assert kw["sampling"]["temperature"] == 0.3
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert svc._qa_stream_cache_key("SYS", "USER", session) is None
    model, kw = calls[-1]
    assert model == "claude-haiku-4-5" and kw["provider"] == "anthropic" and kw["system"] == "SYS"
