"""
B1 — grammar-constrained loop replies for the local provider
(scaffold/agent/loop_grammar.py, AWOS_LOOP_GRAMMAR). Fakes only: a fake
llama-server SDK object (text replies, usage, `timings`), a fixed sandbox.
The grammar is checked with tests/gbnf_check.py (a small GBNF recogniser;
llama.cpp's validator binary is not installed), over a corpus of good and
bad replies, and the parser is checked to agree with it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from gbnf_check import matches, parse_grammar  # noqa: E402
import loop_request_capture as cap  # noqa: E402
from scaffold.agent import edit_grammar  # noqa: E402
from scaffold.agent import loop_executor as lx  # noqa: E402
from scaffold.agent import loop_grammar as lg  # noqa: E402
from scaffold.agent.agent_loop import AnthropicToolClient, OpenAIToolClient  # noqa: E402

MARK = lx.SUBMIT_MARKER
G = lg.GRAMMAR


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("AWOS_LOOP_GRAMMAR", "AWOS_PROVIDER", "AWOS_AGENT_MODEL", "AWOS_LOOP_BREAKER",
              "AWOS_ANTHROPIC_CACHE", "AWOS_REASONING_AGENT"):
        monkeypatch.delenv(k, raising=False)


# ── the checker itself ───────────────────────────────────────────────────────


def test_checker_handles_edit_grammar_dialect():
    g = edit_grammar.build_grammar(["def add(a, b):", "    return a - b"])
    good = "fix:\n<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE"
    assert matches(g, good)
    assert not matches(g, good.replace("    return a - b\n=", "    return a * b\n="))


def test_grammar_parses_and_every_rule_is_defined():
    rules = parse_grammar(G)
    assert {"root", "pre", "pline", "act", "sub"} <= set(rules)
    assert G == lg.build_grammar()  # deterministic: same bytes every call


# ── corpus ───────────────────────────────────────────────────────────────────

GOOD = [
    # (reply, kind, command-or-summary)
    ("```bash\nls -la\n```", "bash", "ls -la"),
    ("I will look at the file first.\n```bash\ncat calc.py\n```", "bash", "cat calc.py"),
    ("Plan:\n\n1. read\n2. fix\n```bash\nnl -ba calc.py | sed -n '1,20p'\n```", "bash",
     "nl -ba calc.py | sed -n '1,20p'"),
    ("Write the file.\n```bash\ncat <<'EOF' > t.py\nprint(\"hi\")\nx = `echo`\n``y\nEOF\n```",
     "bash", "cat <<'EOF' > t.py\nprint(\"hi\")\nx = `echo`\n``y\nEOF"),
    ("```bash\n  cd src && python -c 'import x; print(x.__file__)'\n```", "bash",
     "  cd src && python -c 'import x; print(x.__file__)'"),
    ("```bash\nsed -i '' 's/a - b/a + b/' calc.py\n\n  ```not a fence at col 0\n```", "bash",
     "sed -i '' 's/a - b/a + b/' calc.py\n\n  ```not a fence at col 0"),
    ("Check COMPLETE status: Cat the file.\nCOMPLETE is not the marker\n```bash\nls\n```", "bash",
     "ls"),
    (f"{MARK}", "submit", ""),
    (f"{MARK}\nFixed add() to return a + b.", "submit", "Fixed add() to return a + b."),
    (f"All tests pass.\n{MARK}\nChanged calc.py.\nRan the tests.", "submit",
     "Changed calc.py.\nRan the tests."),
    (f"```bash\necho {MARK}\n```", "bash", f"echo {MARK}"),
]

BAD = [
    "I think the bug is in calc.py.",                         # no envelope
    "```bash\nls\n```\n```bash\npwd\n```",                    # two actions
    "```bash\nls\n```\nDone.",                                # text after the block
    "```python\nprint(1)\n```",                               # wrong fence
    '{"command": "ls"}',                                      # JSON envelope
    "```bash\n```",                                           # empty block
    "```bash\n\n```",                                         # blank-only block
    "```bash\nls\n",                                          # unclosed
    f"{MARK} done",                                           # text on the marker line
    "`ls` first\n```bash\nls\n```",                           # preamble line starts with `
    "\n".join(["x"] * 25) + "\n```bash\nls\n```",             # preamble over 24 lines
    "x" * 401 + "\n```bash\nls\n```",                         # preamble line over 400 chars
    "```bash\n`which python`\n```",                           # first command line starts with `
    f"{MARK}\n" + "\n".join(["s"] * 17),                      # summary over 16 lines
]

THINK = "<think>\nhmm\n</think>\n```bash\nls\n```"


@pytest.mark.parametrize("reply, kind, payload", GOOD)
def test_grammar_admits_good_replies_and_parser_agrees(reply, kind, payload):
    assert matches(G, reply), reply
    parsed = lg.parse_reply(reply)
    assert parsed.kind == kind
    assert (parsed.command if kind == "bash" else parsed.summary) == payload


@pytest.mark.parametrize("reply", BAD)
def test_grammar_rejects_bad_replies(reply):
    assert not matches(G, reply), reply


def test_think_block_is_plain_preamble_text():
    # Thinking is switched off in grammar mode (enable_thinking=false); were a
    # template to emit one anyway, short <think> lines are admitted as preamble.
    assert matches(G, THINK)
    assert lg.parse_reply(THINK).command == "ls"


def test_preamble_limits_are_inclusive():
    assert matches(G, "\n".join(["x"] * 24) + "\n```bash\nls\n```")
    assert matches(G, "x" * 400 + "\n```bash\nls\n```")
    assert matches(G, f"{MARK}\n" + "\n".join(["s"] * 16))


def test_parser_is_lenient_on_unconstrained_text():
    assert lg.parse_reply("ok\r\n  ```bash\r\n  ls\r\n  ```\r\nthanks").kind == "bash"
    assert lg.parse_reply("```bash\nls\n```\n```bash\npwd\n```").command == "ls"
    for bad in ("nothing here", "```bash\nls", "```bash\n   \n```"):
        p = lg.parse_reply(bad)
        assert p.kind == "invalid" and p.error


# ── enablement ───────────────────────────────────────────────────────────────


def _local_client(sdk=None):
    return OpenAIToolClient(sdk or SimpleNamespace(), "local/qwen3.5-9b")


def test_default_on_for_local_provider(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    assert lg.enabled_for(_local_client())
    assert isinstance(lg.wrap_for_loop(_local_client()), lg.GrammarLoopClient)


def test_env_zero_turns_it_off(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_LOOP_GRAMMAR", "0")
    assert not lg.enabled_for(_local_client())


def test_off_by_default_for_non_local_openai(monkeypatch):
    assert not lg.enabled_for(_local_client())
    monkeypatch.setenv("AWOS_LOOP_GRAMMAR", "1")
    assert lg.enabled_for(_local_client())


@pytest.mark.parametrize("env", [None, "1", "0"])
def test_anthropic_never_wrapped(monkeypatch, env):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    if env is not None:
        monkeypatch.setenv("AWOS_LOOP_GRAMMAR", env)
    client = AnthropicToolClient(SimpleNamespace(), "claude-haiku-5-5")
    assert lg.wrap_for_loop(client) is client


# ── fake llama-server ────────────────────────────────────────────────────────


class FakeLlamaServer:
    """OpenAI-SDK-shaped llama-server: text replies, usage with
    prompt_tokens_details.cached_tokens, and the top-level `timings`."""

    def __init__(self, replies, *, cached_details=True):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.cached_details = cached_details
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw)))
        n = len(self.calls)
        text = self.replies.pop(0)
        prompt = 1000 + 200 * n
        cache_n = 0 if n == 1 else prompt - 150
        usage = {"prompt_tokens": prompt, "completion_tokens": 40, "total_tokens": prompt + 40}
        if self.cached_details:
            usage["prompt_tokens_details"] = {"cached_tokens": cache_n}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None),
                                     finish_reason="stop")],
            usage=SimpleNamespace(**{k: (SimpleNamespace(**v) if isinstance(v, dict) else v)
                                     for k, v in usage.items()}),
            model="qwen3.5-9b",
            timings={"cache_n": cache_n, "prompt_n": prompt - cache_n,
                     "prompt_ms": 2.0 * (prompt - cache_n), "prompt_per_second": 500.0,
                     "predicted_n": 40, "predicted_ms": 1000.0, "predicted_per_second": 40.0},
        )


def _run_local(tmp_path, replies, monkeypatch, **kw):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    sdk = FakeLlamaServer(replies, **kw)
    client = OpenAIToolClient(sdk, "local/qwen3.5-9b", max_tokens=2048)
    sandbox = cap.FixedSandbox(str(tmp_path))
    ex = lx.LoopExecutor(client, lx.build_loop_registry(sandbox), "Fix add() in calc.py",
                         workspace=str(tmp_path), gate=lambda f, s: {"green": True})
    return ex, ex.run(), sdk


def test_request_carries_grammar_and_no_tools(tmp_path, monkeypatch):
    _, run, sdk = _run_local(tmp_path, ["Look.\n```bash\nls\n```", f"{MARK}\ndone"],
                             monkeypatch)
    assert run.success and run.stop_reason == "submitted" and run.grammar
    for call in sdk.calls:
        assert "tools" not in call and "tool_choice" not in call
        assert call["extra_body"]["grammar"] == G
        assert call["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
        assert call["messages"][0] == {"role": "system", "content": lx.SYSTEM_PROMPT}
    second = sdk.calls[1]["messages"]
    # Assistant turn = the raw reply text; the observation = a plain user message.
    assert second[2] == {"role": "assistant", "content": "Look.\n```bash\nls\n```"}
    assert second[3]["role"] == "user" and second[3]["content"].startswith("<returncode>0")
    assert "```bash" in second[1]["content"] and "<task>\nFix add() in calc.py\n</task>" in \
        second[1]["content"]


def test_bash_action_is_executed_through_the_sandbox(tmp_path, monkeypatch):
    ran = []

    class Rec(cap.FixedSandbox):
        def run(self, command, timeout_sec=120):
            ran.append(command)
            return super().run(command, timeout_sec)

    monkeypatch.setenv("AWOS_PROVIDER", "local")
    sdk = FakeLlamaServer(["```bash\ncat <<'EOF' > a.txt\nhi\nEOF\n```", f"{MARK}\nok"])
    ex = lx.LoopExecutor(OpenAIToolClient(sdk, "local/q"), lx.build_loop_registry(Rec(str(tmp_path))),
                         "goal", workspace=str(tmp_path), gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.tool_calls == 1 and run.success
    assert ran[0] == lx.bash_command("cat <<'EOF' > a.txt\nhi\nEOF")


def test_echo_marker_in_bash_block_submits(tmp_path, monkeypatch):
    _, run, _ = _run_local(tmp_path, [f"```bash\necho {MARK}\n```"], monkeypatch)
    assert run.success and run.submits == 1


def test_parse_failure_falls_back_to_counted_nudge(tmp_path, monkeypatch):
    # A server that ignored the grammar: the nudge (text-protocol wording) still
    # works, and every such reply is counted.
    _, run, sdk = _run_local(tmp_path, ["just talking", "```bash\nls\n```", f"{MARK}\nok"],
                             monkeypatch)
    assert run.success and run.format_errors == 1 and run.parse_failures == 1
    nudge = sdk.calls[1]["messages"][-1]
    assert nudge["role"] == "user" and "```bash" in nudge["content"]
    assert "tool call" not in nudge["content"]


def test_three_parse_failures_stop(tmp_path, monkeypatch):
    _, run, _ = _run_local(tmp_path, ["a", "b", "c"], monkeypatch)
    assert run.stop_reason == "format_error" and run.parse_failures == 3


def test_component_telemetry_and_call_log(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(log))
    _, run, _ = _run_local(tmp_path, ["```bash\nls\n```", "```bash\npwd\n```", f"{MARK}\nok"],
                           monkeypatch)
    comp = run.component()
    # prompts 1200, 1400, 1600 = 4200; cached 0, 1250, 1450 = 2700
    assert comp["input_tokens"] == 4200 and comp["cached_tokens"] == 2700
    assert comp["cached_fraction"] == round(2700 / 4200, 4)
    assert comp["mean_prefill_ms"] == round((2400 + 300 + 300) / 3, 1)
    assert comp["tokens_per_s"] == 40.0
    assert comp["grammar"] is True and comp["format_errors"] == 0 and comp["parse_failures"] == 0
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(rows) == 3 and all(r["component"] == "loop" and r["loop_grammar"] for r in rows)
    assert rows[1]["cached_tokens"] == 1250 and rows[1]["prompt_eval_tokens"] == 150
    assert rows[1]["prompt_eval_ms"] == 300.0 and rows[1]["gen_tokens_per_s"] == 40.0
    assert rows[1]["server_cache_n"] == 1250 and rows[1]["cache_hit_ratio"] == round(1250 / 1400, 4)


def test_cached_tokens_from_timings_when_usage_has_none(tmp_path, monkeypatch):
    # An older llama-server: no prompt_tokens_details, only timings.cache_n.
    _, run, _ = _run_local(tmp_path, ["```bash\nls\n```", f"{MARK}\nok"], monkeypatch,
                           cached_details=False)
    assert run.cached_tokens == 1250


def test_timings_in_model_extra_are_read():
    from scaffold.agent.agent_loop import response_timings
    from scaffold.agent.llm_call_log import timing_fields

    class SDKObj:  # the OpenAI SDK keeps unknown fields in model_extra
        model_extra = {"timings": {"prompt_n": 7, "prompt_ms": 3.5, "cache_n": 93,
                                   "predicted_n": 10, "predicted_ms": 250.0,
                                   "predicted_per_second": 40.0}}
        choices = [SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")]
        usage = None

    assert response_timings(SDKObj())["cache_n"] == 93
    t = timing_fields(SDKObj())
    assert t == {"prompt_eval_tokens": 7, "prompt_eval_ms": 3.5, "server_cache_n": 93,
                 "gen_tokens": 10, "gen_ms": 250.0, "gen_tokens_per_s": 40.0}


def test_anthropic_cache_fields_in_timing_fields():
    from scaffold.agent.llm_call_log import timing_fields
    resp = SimpleNamespace(content=[], stop_reason="end_turn", usage=SimpleNamespace(
        input_tokens=5, output_tokens=1, cache_read_input_tokens=900,
        cache_creation_input_tokens=40))
    assert timing_fields(resp) == {"cache_read_tokens": 900, "cache_creation_tokens": 40}


# ── Anthropic / tool path unchanged (golden from before this change) ─────────


def _golden() -> dict:
    return json.loads(cap.GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("env", [{}, {"AWOS_LOOP_GRAMMAR": "1"},
                                 {"AWOS_LOOP_GRAMMAR": "1", "AWOS_PROVIDER": "local"}])
def test_anthropic_requests_byte_for_byte_unchanged(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    got = cap.capture_anthropic()
    assert json.dumps(got) == json.dumps(_golden()["anthropic"])


def test_openai_tool_path_unchanged_when_grammar_off(monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_GRAMMAR", "0")
    got = cap.capture_openai_tools()
    assert json.dumps(got) == json.dumps(_golden()["openai_tools"])


# ── prefix drift telemetry ───────────────────────────────────────────────────


def test_prefix_drift_formula():
    prev = {"prompt_n": 150, "cache_n": 1050, "predicted_n": 40}
    # the slot held 1240 tokens; the stop token (1) is never decoded into it
    assert lg.prefix_drift(prev, {"cache_n": 1239}) == 0
    assert lg.prefix_drift(prev, {"cache_n": 1300}) == 0
    assert lg.prefix_drift(prev, {"cache_n": 1000}) == 239
    assert lg.prefix_drift(prev, {}) is None and lg.prefix_drift(None, {"cache_n": 1}) is None


def test_drift_counters_on_fake_server(tmp_path, monkeypatch):
    # FakeLlamaServer's cache_n = prompt - 150 while prev prompt + 40 generated
    # = prompt - 160 are in the slot: no drift.
    ex, run, _ = _run_local(tmp_path, ["```bash\nls\n```", "```bash\npwd\n```", f"{MARK}\nok"],
                            monkeypatch)
    c = ex.client
    assert isinstance(c, lg.GrammarLoopClient)
    assert c.prefix_drift_checked == 2 and c.prefix_drift_calls == 0
    assert c.prefix_drift_tokens == 0


class _DriftSDK(FakeLlamaServer):
    """cache_n far below what the previous call left in the slot."""

    def _create(self, **kw):
        r = super()._create(**kw)
        if len(self.calls) > 1:
            r.timings["cache_n"] = 500
        return r


def test_drift_counted_and_on_reply(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    sdk = _DriftSDK(["```bash\nls\n```", "```bash\npwd\n```"])
    c = lg.GrammarLoopClient(OpenAIToolClient(sdk, "local/q"))
    msgs = [{"role": "user", "content": "task"}]
    r1 = c.complete("S", msgs)
    assert r1.prefix_drift is None                          # first call: not judged
    msgs = msgs + [c.format_assistant_turn(r1), {"role": "user", "content": "obs"}]
    r2 = c.complete("S", msgs)
    # prev: prompt 1200, cache 0, predicted 40 -> expected 1239; got 500
    assert r2.prefix_drift == 739
    assert (c.prefix_drift_calls, c.prefix_drift_tokens, c.prefix_drift_checked) == (1, 739, 1)


def test_rewritten_history_is_not_drift(monkeypatch):
    sdk = _DriftSDK(["```bash\nls\n```", "```bash\npwd\n```"])
    c = lg.GrammarLoopClient(OpenAIToolClient(sdk, "local/q"))
    r1 = c.complete("S", [{"role": "user", "content": "task"}])
    r2 = c.complete("S", [{"role": "user", "content": "compacted task"},
                          c.format_assistant_turn(r1)])
    assert r2.prefix_drift is None and c.prefix_drift_checked == 0


def test_draft_counts_on_reply_and_in_usage_tokens():
    from scaffold.agent import providers

    class SpecSDK(FakeLlamaServer):
        def _create(self, **kw):
            r = super()._create(**kw)
            r.timings.update(draft_n=48, draft_n_accepted=36)
            return r

    c = lg.GrammarLoopClient(OpenAIToolClient(SpecSDK(["```bash\nls\n```"]), "local/q"))
    r = c.complete("S", [{"role": "user", "content": "t"}])
    assert (r.draft_tokens, r.draft_accepted) == (48, 36)
    tok = providers.usage_tokens({"prompt_tokens": 10, "completion_tokens": 5},
                                 {"draft_n": 48, "draft_n_accepted": 36})
    assert tok["draft"] == 48 and tok["draft_accepted"] == 36 and tok["draft_acceptance"] == 0.75
    assert "draft" not in providers.usage_tokens({"prompt_tokens": 1}, {"cache_n": 0})
    assert providers.usage_tokens({"prompt_tokens": 1}, {"draft_n": 0})["draft_acceptance"] is None
    assert providers.usage_tokens({"prompt_tokens": 1},
                                  {"draft_n": 4, "draft_n_accepted": 9})["draft_accepted"] == 4
    assert providers.draft_acceptance(10, 7) == 0.7 and providers.draft_acceptance(0, 0) is None


def test_draft_fields_in_timing_fields_and_call_log(tmp_path, monkeypatch):
    from scaffold.agent.llm_call_log import timing_fields

    class SpecSDK(FakeLlamaServer):
        def _create(self, **kw):
            r = super()._create(**kw)
            r.timings.update(draft_n=20, draft_n_accepted=15)
            return r

    resp = SpecSDK(["x"])._create(model="m", messages=[])
    t = timing_fields(resp)
    assert t["draft_tokens"] == 20 and t["draft_accepted"] == 15 and t["draft_acceptance"] == 0.75
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(log))
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    sdk = SpecSDK(["```bash\nls\n```", f"{MARK}\nok"])
    ex = lx.LoopExecutor(OpenAIToolClient(sdk, "local/q"),
                         lx.build_loop_registry(cap.FixedSandbox(str(tmp_path))), "g",
                         workspace=str(tmp_path), gate=lambda f, s: {"green": True})
    ex.run()
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert rows and all(r["draft_acceptance"] == 0.75 for r in rows)


def test_id_slot_sent_only_with_multiple_slots(monkeypatch):
    sdk = FakeLlamaServer(["```bash\nls\n```", "```bash\nls\n```"])
    lg.GrammarLoopClient(OpenAIToolClient(sdk, "local/q")).complete("S", [])
    assert "id_slot" not in sdk.calls[0]["extra_body"]
    monkeypatch.setenv("AWOS_LOCAL_SLOTS", "2")
    monkeypatch.setenv("AWOS_LOCAL_SLOT_ID", "1")
    lg.GrammarLoopClient(OpenAIToolClient(sdk, "local/q")).complete("S", [])
    assert sdk.calls[1]["extra_body"]["id_slot"] == 1


# ── Qwen3 template: does the cached prefix survive the next step? ────────────
# A reconstruction (from the published Qwen3 chat template) of only the rules
# that matter here: the "last real user query" scan, the assistant think-block
# rule, tool messages as <tool_response> in a user turn, and the
# enable_thinking=false generation prompt. Tools/tool_calls are left out (the
# grammar mode sends none). This is NOT the Qwen3.5 GGUF's own template, which
# could not be read here; measure prefix_drift on the real server before
# turning AWOS_LOOP_THINK_STUB on by default.
QWEN3_LIKE_TEMPLATE = r"""
{%- set ns = namespace(multi_step_tool=true, last_query_index=messages|length - 1) %}
{%- for message in messages[::-1] %}
  {%- set index = (messages|length - 1) - loop.index0 %}
  {%- if ns.multi_step_tool and message.role == "user" and message.content is string and not(message.content.startswith('<tool_response>') and message.content.endswith('</tool_response>')) %}
    {%- set ns.multi_step_tool = false %}
    {%- set ns.last_query_index = index %}
  {%- endif %}
{%- endfor %}
{%- for message in messages %}
  {%- if message.role == "user" or message.role == "system" %}
    {{- '<|im_start|>' + message.role + '\n' + message.content + '<|im_end|>' + '\n' }}
  {%- elif message.role == "assistant" %}
    {%- set content = message.content %}
    {%- set reasoning_content = '' %}
    {%- if message.reasoning_content is defined and message.reasoning_content is not none %}
      {%- set reasoning_content = message.reasoning_content %}
    {%- elif '</think>' in content %}
      {%- set reasoning_content = content.split('</think>')[0].rstrip('\n').split('<think>')[-1].lstrip('\n') %}
      {%- set content = content.split('</think>')[-1].lstrip('\n') %}
    {%- endif %}
    {%- if loop.index0 > ns.last_query_index and (loop.last or reasoning_content) %}
      {{- '<|im_start|>assistant\n<think>\n' + reasoning_content.strip('\n') + '\n</think>\n\n' + content.lstrip('\n') }}
    {%- else %}
      {{- '<|im_start|>assistant\n' + content }}
    {%- endif %}
    {{- '<|im_end|>\n' }}
  {%- elif message.role == "tool" %}
    {%- if loop.first or (messages[loop.index0 - 1].role != "tool") %}{{- '<|im_start|>user' }}{%- endif %}
    {{- '\n<tool_response>\n' + message.content + '\n</tool_response>' }}
    {%- if loop.last or (messages[loop.index0 + 1].role != "tool") %}{{- '<|im_end|>\n' }}{%- endif %}
  {%- endif %}
{%- endfor %}
{%- if add_generation_prompt %}
  {{- '<|im_start|>assistant\n' }}
  {%- if enable_thinking is defined and enable_thinking is false %}{{- '<think>\n\n</think>\n\n' }}{%- endif %}
{%- endif %}
"""


class TemplateLlamaServer(FakeLlamaServer):
    """Renders each request through the template and models the slot: it holds
    prompt + generated text after a call; cache_n = common prefix with the next
    prompt (characters stand in for tokens); the stop token counts in
    predicted_n but is not kept, as in llama-server."""

    def __init__(self, replies):
        super().__init__(replies)
        # Optional test dependency (renders the chat template); skip without it.
        jinja2 = pytest.importorskip("jinja2")
        self.tpl = jinja2.Environment().from_string(QWEN3_LIKE_TEMPLATE)
        self.slot = ""
        self.prompts: list[str] = []

    def _create(self, **kw):
        import os as _os
        self.calls.append(json.loads(json.dumps(kw)))
        kwargs = kw["extra_body"]["chat_template_kwargs"]
        prompt = self.tpl.render(messages=kw["messages"], add_generation_prompt=True, **kwargs)
        self.prompts.append(prompt)
        text = self.replies.pop(0)
        cache_n = len(_os.path.commonprefix([self.slot, prompt]))
        self.slot = prompt + text
        usage = SimpleNamespace(prompt_tokens=len(prompt), completion_tokens=len(text) + 1,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=cache_n))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None),
                                     finish_reason="stop")],
            usage=usage, model="qwen3",
            timings={"cache_n": cache_n, "prompt_n": len(prompt) - cache_n,
                     "predicted_n": len(text) + 1, "prompt_ms": 1.0, "predicted_ms": 1.0})


THREE_STEPS = ["Look.\n```bash\nls\n```", "Read.\n```bash\ncat calc.py\n```",
               "Fix.\n```bash\nsed -i 's/-/+/' calc.py\n```", f"{MARK}\ndone"]


def _template_run(tmp_path, monkeypatch, stub: bool):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    if stub:
        monkeypatch.setenv("AWOS_LOOP_THINK_STUB", "1")
    else:
        monkeypatch.delenv("AWOS_LOOP_THINK_STUB", raising=False)
    sdk = TemplateLlamaServer(list(THREE_STEPS))
    ex = lx.LoopExecutor(OpenAIToolClient(sdk, "local/qwen3"),
                         lx.build_loop_registry(cap.FixedSandbox(str(tmp_path))), "Fix add()",
                         workspace=str(tmp_path), gate=lambda f, s: {"green": True})
    run = ex.run()
    return ex.client, run, sdk


def test_qwen3_template_breaks_prefix_without_stub(tmp_path, monkeypatch):
    c, run, sdk = _template_run(tmp_path, monkeypatch, stub=False)
    assert run.success
    # Every later step re-renders the previous assistant turn without the empty
    # think block the reply was generated after: drift on each judged call.
    assert c.prefix_drift_checked == 3 and c.prefix_drift_calls == 3
    assert "<think>" not in sdk.prompts[1].split("<|im_start|>assistant\n<think>")[0]
    assert c.prefix_drift_tokens > 0


def test_think_stub_keeps_prefix_stable(tmp_path, monkeypatch):
    c, run, sdk = _template_run(tmp_path, monkeypatch, stub=True)
    assert run.success
    assert c.prefix_drift_checked == 3 and c.prefix_drift_calls == 0
    for a, b in zip(sdk.prompts, sdk.prompts[1:]):
        assert b.startswith(a)                       # each prompt extends the last
    second = sdk.calls[1]["messages"]
    assert second[2] == {"role": "assistant", "content": THREE_STEPS[0],
                         "reasoning_content": "\n"}
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == "g1"


def test_stub_in_content_alone_is_split_off_by_the_template():
    # Why the flag uses reasoning_content and not a stub inside `content`: the
    # template splits '</think>' out of content, and an empty block is dropped.
    jinja2 = pytest.importorskip("jinja2")
    tpl = jinja2.Environment().from_string(QWEN3_LIKE_TEMPLATE)
    msgs = [{"role": "user", "content": "t"},
            {"role": "assistant", "content": "<think>\n\n</think>\n\nA"},
            {"role": "tool", "content": "obs"}]
    out = tpl.render(messages=msgs, add_generation_prompt=True, enable_thinking=False)
    assert "<|im_start|>assistant\nA<|im_end|>" in out


def test_loop_run_sums_drift_into_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_MODELS_DIR", str(tmp_path / "models"))
    ws = tmp_path / "ws"
    ws.mkdir()
    sdk = _DriftSDK(["```bash\nls\n```", "```bash\npwd\n```", f"{MARK}\nok"])
    client = OpenAIToolClient(sdk, "local/qwen3.5-9b", max_tokens=2048)
    ex = lx.LoopExecutor(client, lx.build_loop_registry(cap.FixedSandbox(str(ws))),
                         "Fix add() in calc.py", workspace=str(ws),
                         gate=lambda f, s: {"green": True})
    run = ex.run()
    tel = run.telemetry()
    assert run.success and tel["prefix_drift_checked"] == 2
    assert tel["prefix_drift_calls"] == 2 and tel["prefix_drift_tokens"] > 0


def test_local_run_holds_a_task_lease_and_releases_it(tmp_path, monkeypatch):
    from scaffold.agent import local_runtime

    env_dir = tmp_path / "models"
    monkeypatch.setenv("AWOS_MODELS_DIR", str(env_dir))
    seen = []

    class Watching(FakeLlamaServer):
        def _create(self, **kw):
            seen.append(len(local_runtime.live_leases()))
            return super()._create(**kw)

    monkeypatch.setenv("AWOS_PROVIDER", "local")
    client = OpenAIToolClient(Watching(["```bash\nls\n```", f"{MARK}\nok"]), "local/q",
                              max_tokens=2048)
    ex = lx.LoopExecutor(client, lx.build_loop_registry(cap.FixedSandbox(str(tmp_path))),
                         "Fix it", workspace=str(tmp_path), gate=lambda f, s: {"green": True})
    assert ex.run().success
    assert seen and all(n == 1 for n in seen)
    assert local_runtime.live_leases() == []


def test_cloud_run_takes_no_lease(tmp_path, monkeypatch):
    from scaffold.agent import local_runtime

    monkeypatch.setenv("AWOS_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("AWOS_PROVIDER", raising=False)
    ex = lx.LoopExecutor(type("C", (), {"model": "claude-haiku-5-5"})(), None, "x")
    assert type(lx._local_task_lease(ex)).__name__ == "nullcontext"
    assert local_runtime.live_leases() == []
