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
