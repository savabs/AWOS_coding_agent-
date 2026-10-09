"""
T5b fixes in scaffold/agent/agent_loop.py:

1. The reread guard records the lines a read actually served (read_file's
   result.data start_line/end_line under AWOS_SKELETON_VIEW=1), not the lines
   asked for: a skeleton records nothing, a capped range records what was
   shown, a symbol read records the symbol's lines (docs/specs/skeleton_viewer.md,
   Risks — the live real_parse_249 false "already read" notes).
2. show_symbol is registered only with AWOS_SKELETON_VIEW=1.
3. estimate_cost prices cached input at the cache-read rate when the usage
   reports cached tokens, and at full rate otherwise (docs/specs/prompt_caching.md).

The model is stubbed; the read tools are the real ones over a temp file.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

import scaffold.agent.agent_loop as agent_loop
from scaffold.agent.agent_loop import (
    AgentLoop,
    ModelReply,
    OpenAIToolClient,
    AnthropicToolClient,
    ToolCall,
    build_coding_registry,
    estimate_cost,
)
from scaffold.agent.tools.base import ToolRegistry
from scaffold.agent.tools.filesystem import ReadFileTool, ShowSymbolTool

REREAD = "You already read these lines"
FLASH = "deepseek/deepseek-v4-flash"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AWOS_PROVIDER", "AWOS_AGENT_MODEL", "AWOS_PRICE_TABLE",
                 "AWOS_SKELETON_VIEW", "AWOS_SKELETON_MAX_LINES", "AWOS_SKELETON_MIN_LINES"):
        monkeypatch.delenv(name, raising=False)


class Client:
    def __init__(self, replies):
        self._replies = list(replies)
        self.seen: list[tuple[str, str]] = []

    def complete(self, system, messages, registry):
        return self._replies.pop(0) if self._replies else ModelReply(text="done")

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text}

    def format_tool_results(self, calls, results):
        for c, r in zip(calls, results):
            self.seen.append((c.name, r.text if r.success else f"ERR {r.error}"))
        return [{"role": "user", "content": "x"}]


_ids = iter(range(100_000))


def _read(**args):
    return ModelReply(tool_calls=[ToolCall(id=f"c{next(_ids)}", name="read_file", arguments=args)])


def _show(**args):
    return ModelReply(tool_calls=[ToolCall(id=f"c{next(_ids)}", name="show_symbol", arguments=args)])


def _big_py(tmp_path, n_funcs=100):
    """A ~1000-line Python file: f0 .. f99, 10 lines each (f_k at 10k+1)."""
    parts = []
    for k in range(n_funcs):
        parts.append(f"def f{k}(x):\n" + "".join(f"    x = x + {i}\n" for i in range(8)) + "    return x\n")
    (tmp_path / "big.py").write_text("".join(parts))
    return "big.py"


def _registry(tmp_path):
    reg = ToolRegistry()
    reg.project_root = str(tmp_path)
    reg.register(ReadFileTool(project_root=str(tmp_path), confine=True))
    reg.register(ShowSymbolTool(project_root=str(tmp_path), confine=True))
    return reg


def _run(tmp_path, replies):
    client = Client(replies)
    loop = AgentLoop(_registry(tmp_path), client, max_turns=50,
                     post_green_turns=0, no_progress_turns=0, wall_s=0)
    return loop.run("task"), client


# ── 1. reread guard ──────────────────────────────────────────────────────────


def test_capped_ranges_and_skeleton_do_not_mark_unseen_lines_read(tmp_path, monkeypatch):
    """The live parse_249 sequence: skeleton, 650-870 (650-749 served),
    750-870 (750-849 served), then 850-870 — never shown, so it must be sent."""
    monkeypatch.setenv("AWOS_SKELETON_VIEW", "1")
    path = _big_py(tmp_path)
    outcome, client = _run(tmp_path, [
        _read(path=path),                                   # skeleton
        _read(path=path, start_line=650, end_line=870),     # capped to 650-749
        _read(path=path, start_line=750, end_line=870),     # capped to 750-849
        _read(path=path, start_line=850, end_line=870),
        _read(path=path, start_line=851, end_line=870),
    ])
    texts = [t for _, t in client.seen]
    assert "too long to show whole" in texts[0]
    assert "lines 650–749" in texts[1] and "lines 750–849" in texts[2]
    assert "lines 850–870" in texts[3] and "lines 851–870" in texts[4]
    assert not any(REREAD in t for t in texts)
    assert outcome.reread_notes == 0


def test_guard_still_trips_on_lines_really_served_three_times(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SKELETON_VIEW", "1")
    path = _big_py(tmp_path)
    outcome, client = _run(tmp_path, [
        _read(path=path, start_line=850, end_line=870),
        _read(path=path, start_line=851, end_line=870),
        _read(path=path, start_line=852, end_line=869),
        _read(path=path, start_line=855, end_line=860),
    ])
    texts = [t for _, t in client.seen]
    assert all(REREAD not in t for t in texts[:3])
    assert texts[3].startswith(f"{REREAD} ({path} 855-860) at turn 3")
    assert outcome.reread_notes == 1


def test_skeleton_reads_record_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SKELETON_VIEW", "1")
    path = _big_py(tmp_path)
    # Three skeletons (vary the spelling to dodge the repeat rule), then lines.
    outcome, client = _run(tmp_path, [
        _read(path=path), _read(path=f"./{path}"), _read(path=str(tmp_path / path)),
        _read(path=path, start_line=1, end_line=50),
    ])
    texts = [t for _, t in client.seen]
    assert "lines 1–50" in texts[3]
    assert outcome.reread_notes == 0


def test_symbol_reads_record_the_symbols_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SKELETON_VIEW", "1")
    path = _big_py(tmp_path)  # f5 is lines 51-60
    outcome, client = _run(tmp_path, [
        _read(path=path, symbol="f5"),
        _read(path=f"./{path}", symbol="f5"),
        _show(path=path, symbol="f5"),
        _read(path=path, start_line=100, end_line=120),  # outside f5: sent
        _read(path=path, start_line=52, end_line=58),    # inside f5, served 3x
    ])
    texts = [t for _, t in client.seen]
    assert all("def f5" in t for t in texts[:3])
    assert REREAD not in texts[0] and REREAD not in texts[1]  # symbol reads never pre-noted
    assert "lines 100–120" in texts[3]
    assert texts[4].startswith(f"{REREAD} ({path} 52-58)")
    assert outcome.reread_notes == 1


def test_symbol_reads_do_not_block_whole_file_or_other_symbols(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SKELETON_VIEW", "1")
    path = _big_py(tmp_path)
    outcome, client = _run(tmp_path, [
        _read(path=path, symbol="f5"), _read(path=path, symbol="f6"),
        _read(path=path, symbol="f7"), _read(path=path, symbol="f8"),
        _read(path=path),
    ])
    texts = [t for _, t in client.seen]
    assert "def f8" in texts[3] and "too long to show whole" in texts[4]
    assert outcome.reread_notes == 0


def test_served_span_unit():
    call = ToolCall(id="1", name="read_file", arguments={"path": "a.py"})
    asked = ("/r/a.py", 1, agent_loop._EOF_LINE)
    ok = lambda data: SimpleNamespace(success=True, data=data)  # noqa: E731
    # No served range reported (flag off): the asked span, unchanged.
    assert agent_loop._served_span(call, ok({}), asked, "/r") == asked
    # Skeleton.
    assert agent_loop._served_span(call, ok({"start_line": 0, "end_line": 0}), asked, "/r") is None
    # Window that reaches the end of the file counts as through EOF.
    got = agent_loop._served_span(call, ok({"start_line": 1, "end_line": 40, "total_lines": 40}),
                                  asked, "/r")
    assert got == ("/r/a.py", 1, agent_loop._EOF_LINE)
    # Failure records nothing it was not sent.
    assert agent_loop._served_span(
        call, SimpleNamespace(success=False, data={"start_line": 1, "end_line": 9}), asked, "/r") is None


def test_flag_off_guard_unchanged(tmp_path):
    """Flag off: read_file reports no served range, the asked span is recorded
    as before, so three reads of a range then a sub-range is noted."""
    (tmp_path / "s.py").write_text("".join(f"x{i} = {i}\n" for i in range(600)))
    outcome, client = _run(tmp_path, [
        _read(path="s.py", start_line=440, end_line=480),
        _read(path="s.py", start_line=448, end_line=479),
        _read(path="./s.py", start_line="448", end_line="479"),
        _read(path="s.py", start_line=450, end_line=470),
        _read(path="s.py", symbol="x"),  # ignored with the flag off: whole-file span
    ])
    texts = [t for _, t in client.seen]
    assert "lines 440–480" in texts[0]
    assert texts[3].startswith(f"{REREAD} (s.py 450-470) at turn 3")
    assert "lines 1–600" in texts[4]
    assert outcome.reread_notes == 1


# ── 2. show_symbol registration ──────────────────────────────────────────────


def test_show_symbol_registered_only_with_flag(tmp_path, monkeypatch):
    off = build_coding_registry(str(tmp_path), sandbox=False)
    assert "show_symbol" not in off.names()
    monkeypatch.setenv("AWOS_SKELETON_VIEW", "1")
    on = build_coding_registry(str(tmp_path), sandbox=False)
    assert "show_symbol" in on.names()
    # The older tools keep their order; show_symbol only adds.
    assert [n for n in on.names() if n != "show_symbol"] == off.names()


# ── 3. cache-aware cost ──────────────────────────────────────────────────────


def test_estimate_cost_without_cache_counts_is_full_rate():
    full = (1000 * 0.089 + 100 * 0.177) / 1_000_000
    assert estimate_cost(FLASH, 1000, 100) == pytest.approx(full)
    assert estimate_cost(FLASH, 1000, 100, 0, 0) == pytest.approx(full)


def test_estimate_cost_prices_cached_input_at_cache_rate():
    # Flash cache read = 0.2 x input.
    want = (200 * 0.089 + 800 * 0.089 * 0.2 + 100 * 0.177) / 1_000_000
    got = estimate_cost(FLASH, 1000, 100, cached_tokens=800)
    assert got == pytest.approx(want)
    assert got < estimate_cost(FLASH, 1000, 100)


def test_estimate_cost_unpriced_model_still_zero():
    assert estimate_cost("nobody/unknown-model", 1000, 100, cached_tokens=500) == 0.0


def _openai_raw(prompt, completion, cached=None):
    usage = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    message = SimpleNamespace(content="done", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                           usage=SimpleNamespace(**usage) if cached is None else _NS(usage))


class _NS(SimpleNamespace):
    def __init__(self, d):
        super().__init__(**{k: (_NS(v) if isinstance(v, dict) else v) for k, v in d.items()})


class _FakeOpenAI:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response))


def test_openai_client_reads_cached_tokens():
    reply = OpenAIToolClient(_FakeOpenAI(_openai_raw(1000, 50, cached=600)), FLASH).complete(
        "s", [], ToolRegistry())
    assert (reply.input_tokens, reply.cached_tokens) == (1000, 600)
    reply = OpenAIToolClient(_FakeOpenAI(_openai_raw(1000, 50)), FLASH).complete(
        "s", [], ToolRegistry())
    assert (reply.input_tokens, reply.cached_tokens) == (1000, 0)


def test_anthropic_client_input_is_whole_prompt():
    usage = SimpleNamespace(input_tokens=100, output_tokens=10,
                            cache_read_input_tokens=900, cache_creation_input_tokens=0)
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")], usage=usage)
    fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    reply = AnthropicToolClient(fake, "claude-haiku-4-5").complete("s", [], ToolRegistry())
    assert (reply.input_tokens, reply.cached_tokens) == (1000, 900)


def test_loop_cost_uses_cached_tokens(tmp_path):
    class C(Client):
        def complete(self, system, messages, registry):
            return ModelReply(text="done", input_tokens=10_000, output_tokens=100,
                              cached_tokens=9_000)
    C.model = FLASH
    client = C([])
    outcome = AgentLoop(_registry(tmp_path), client, max_turns=3, post_green_turns=0,
                        no_progress_turns=0, wall_s=0).run("t")
    assert outcome.cached_tokens == 9_000
    assert outcome.cost_usd == pytest.approx(estimate_cost(FLASH, 10_000, 100, 9_000))
    assert outcome.cost_usd < estimate_cost(FLASH, 10_000, 100)
    assert outcome.to_dict()["cached_tokens"] == 9_000


def test_loop_cost_without_cached_tokens_unchanged(tmp_path):
    class C(Client):
        def complete(self, system, messages, registry):
            return ModelReply(text="done", input_tokens=10_000, output_tokens=100)
    C.model = FLASH
    outcome = AgentLoop(_registry(tmp_path), C([]), max_turns=3, post_green_turns=0,
                        no_progress_turns=0, wall_s=0).run("t")
    assert outcome.cost_usd == pytest.approx(estimate_cost(FLASH, 10_000, 100))
