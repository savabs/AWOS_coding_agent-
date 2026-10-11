"""Hermetic tests: harness_aider on Anthropic direct (claude-haiku-5-5), no network."""
from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("harness_aider_anth", REPO / "scripts" / "harness_aider.py")
ha = importlib.util.module_from_spec(_spec)
sys.modules["harness_aider_anth"] = ha
_spec.loader.exec_module(ha)

FAKE_ANTH = "sk-ant-FAKEKEYFORTESTS"
FAKE_OR = "sk-or-v1-FAKEKEYFORTESTS"
HAIKU = {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125}


def test_resolve_provider():
    assert ha.resolve_provider("claude-haiku-5-5") == "anthropic"
    assert ha.resolve_provider("deepseek/deepseek-v4-flash") == "openrouter"
    # an OpenRouter-style anthropic id stays on OpenRouter (the aider-frontier arm)
    assert ha.resolve_provider("anthropic/claude-sonnet-5.5") == "openrouter"
    assert ha.resolve_provider("claude-haiku-5-5", "openrouter") == "openrouter"


def test_aider_model_name():
    assert ha.aider_model_name("claude-haiku-5-5", "anthropic") == "anthropic/claude-haiku-5-5"
    assert ha.aider_model_name("anthropic/claude-haiku-5-5", "anthropic") == "anthropic/claude-haiku-5-5"
    assert ha.aider_model_name("deepseek/deepseek-v4-flash") == "openrouter/deepseek/deepseek-v4-flash"


def test_haiku_55_prices_match_awos():
    assert ha.anthropic_prices("claude-haiku-5-5") == HAIKU
    meta = ha.anthropic_metadata("claude-haiku-5-5")["anthropic/claude-haiku-5-5"]
    assert meta["input_cost_per_token"] == pytest.approx(1e-7)
    assert meta["output_cost_per_token"] == pytest.approx(5e-7)
    assert meta["cache_read_input_token_cost"] == pytest.approx(1e-8)
    assert ha.anthropic_metadata("claude-unknown-9") is None


def test_child_env_keeps_only_the_anthropic_key():
    parent = {"PATH": "/bin", "OPENROUTER_API_KEY": FAKE_OR, "OPENAI_BASE_URL": "x"}
    env = ha.child_env(parent, {"ANTHROPIC_API_KEY": FAKE_ANTH}, ha.ANTHROPIC_KEY_ENV)
    assert env["ANTHROPIC_API_KEY"] == FAKE_ANTH
    assert "OPENROUTER_API_KEY" not in env and "OPENAI_BASE_URL" not in env


def test_settings_and_command_for_anthropic(tmp_path):
    (s,) = ha.model_settings("claude-haiku-5-5", "anthropic")
    name = "anthropic/claude-haiku-5-5"
    assert s["name"] == s["weak_model_name"] == s["editor_model_name"] == name
    assert s["cache_control"] is True
    cmd = ha.build_command(["aider"], "claude-haiku-5-5", "g", tmp_path, "t", provider="anthropic")
    assert cmd[cmd.index("--model") + 1] == name
    assert "--cache-prompts" in cmd
    assert "--cache-prompts" not in ha.build_command(["aider"], "m/x", "g", tmp_path, "t")


def test_cost_from_exact_tokens_at_flat_prices():
    t = ha.UsageTracker(HAIKU)
    # litellm: prompt_tokens include cache reads and writes
    t.feed(ha.USAGE_TAG + json.dumps({"prompt_tokens": 10000, "completion_tokens": 1000,
                                      "cache_hit_tokens": 0, "cache_write_tokens": 8000,
                                      "cost": 1.0, "session_cost": 1.0}))
    t.feed(ha.USAGE_TAG + json.dumps({"prompt_tokens": 150000, "completion_tokens": 2000,
                                      "cache_hit_tokens": 8000, "cache_write_tokens": 0,
                                      "cost": 9.0, "session_cost": 10.0}))
    cost, src = t.cost()
    expect = (2000 * 0.10 + 8000 * 0.125 + 1000 * 0.50
              + 142000 * 0.10 + 8000 * 0.01 + 2000 * 0.50) / 1e6
    assert src == "token_price_table" and cost == pytest.approx(expect)


FAKE_AIDER = r'''
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("aider 0.0.fake"); sys.exit(0)
with open(os.environ["FAKE_ARGV_LOG"], "w") as f:
    json.dump({"argv": args, "anth": os.environ.get("ANTHROPIC_API_KEY"),
               "or": os.environ.get("OPENROUTER_API_KEY")}, f)
for i in range(2):
    print("[aider-usage] " + json.dumps({"prompt_tokens": 20000, "completion_tokens": 500,
          "cache_hit_tokens": 15000 if i else 0, "cache_write_tokens": 0 if i else 15000,
          "cost": 0.5, "session_cost": 0.5 * (i + 1), "openrouter_cost": None}), flush=True)
print("Applied edit to a.py", flush=True)
print("[aider-final] " + json.dumps({"test_outcome": True, "reflections": 0, "edit_format": "diff",
                                     "edited": ["a.py"], "in_chat": ["a.py"]}), flush=True)
'''


def test_main_on_anthropic_direct(tmp_path, monkeypatch, capsys):
    script = tmp_path / "fake_aider.py"
    script.write_text(FAKE_AIDER)
    monkeypatch.setenv("AWOS_AIDER_CMD", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")
    job = tmp_path / "job"
    job.mkdir()
    (job / "task.json").write_text(json.dumps({"id": "j", "goal": "fix it", "max_turns": 40,
                                               "max_cost_usd": 0.3, "timeout_min": 15}))
    project, state = tmp_path / "project", tmp_path / "state"
    project.mkdir(), state.mkdir()
    src_env = tmp_path / "source.env"      # the source .env (AWOS_JOB_DOTENV), not copied
    src_env.write_text(f"ANTHROPIC_API_KEY={FAKE_ANTH}\nOPENROUTER_API_KEY={FAKE_OR}\n")
    monkeypatch.setenv("AWOS_JOB_DOTENV", str(src_env))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    log = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    monkeypatch.chdir(state)

    assert ha.main([str(job), str(project), "--model", "claude-haiku-5-5"]) == 0
    seen = json.loads(log.read_text())
    assert seen["anth"] == FAKE_ANTH and seen["or"] is None
    assert not any(FAKE_ANTH in a for a in seen["argv"])
    assert seen["argv"][seen["argv"].index("--model") + 1] == "anthropic/claude-haiku-5-5"
    meta = json.loads(Path(seen["argv"][seen["argv"].index("--model-metadata-file") + 1]).read_text())
    assert "anthropic/claude-haiku-5-5" in meta
    printed = capsys.readouterr().out
    assert FAKE_ANTH not in printed and "provider=anthropic" in printed and "key=set" in printed

    r = json.loads((state / "report.json").read_text())
    assert r["success"] is True and r["provider"] == "anthropic"
    assert r["cost_source"] == "token_price_table" and r["cache_prompts"] is True
    expect = ((5000 * 0.10 + 15000 * 0.125 + 500 * 0.50)
              + (5000 * 0.10 + 15000 * 0.01 + 500 * 0.50)) / 1e6
    assert r["usage"]["cost_usd"] == pytest.approx(expect, abs=1e-6)
    assert r["usage"]["models"] == ["claude-haiku-5-5"]
    assert r["cache_write_tokens"] == 15000 and r["cache_hit_tokens"] == 15000
