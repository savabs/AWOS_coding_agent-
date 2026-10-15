"""
harbor_agents/awos_agent.py (AWOS as a Harbor installed agent) and
harbor_agents/pricing.py. The adapter tests need the `harbor` package
(pip install harbor==0.24.0) and are skipped without it; pricing needs nothing.
A fake environment records the commands; no container, no model call.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harbor_agents import pricing  # noqa: E402


# ── pricing ─────────────────────────────────────────────────────────────────


def test_short_prompt_card():
    # 50K fresh input + 1K output: 50_000*0.10 + 1_000*0.50 per M
    assert pricing.request_cost("claude-haiku-5-5", 50_000, 1_000) == pytest.approx(0.0055)


def test_cache_read_and_write_multipliers():
    c = pricing.request_cost("anthropic/claude-haiku-5-5", 10_000, 0, cache_read=6_000,
                             cache_write_5m=2_000, cache_write_1h=1_000)
    fresh = 1_000 * 0.10
    expected = fresh + 6_000 * 0.01 + 2_000 * 0.125 + 1_000 * 0.20
    assert c == pytest.approx(expected / 1e6)


def test_long_prompt_card_is_per_request():
    at = pricing.request_cost("claude-haiku-5-5", 100_000, 100)
    above = pricing.request_cost("claude-haiku-5-5", 100_001, 100)
    assert at == pytest.approx((100_000 * 0.10 + 100 * 0.50) / 1e6)
    assert above == pytest.approx((100_001 * 0.50 + 100 * 2.50) / 1e6)
    # the split looks at the whole prompt, cached part included
    cached = pricing.request_cost("claude-haiku-5-5", 150_000, 0, cache_read=140_000)
    assert cached == pytest.approx((10_000 * 0.50 + 140_000 * 0.05) / 1e6)


def test_unpriced_model_and_clamping():
    assert pricing.request_cost("gpt-x", 10, 10) is None
    assert pricing.total_cost("gpt-x", [{"prompt": 1}]) is None
    # cache counts larger than the prompt are clamped, never negative fresh input
    assert pricing.request_cost("claude-haiku-5-5", 100, 0, cache_read=500) == pytest.approx(
        100 * 0.01 / 1e6)


# ── adapter ─────────────────────────────────────────────────────────────────

try:
    from harbor.models.agent.context import AgentContext

    from harbor_agents import awos_agent as aa
except ImportError:  # harbor not installed: only the pricing tests run
    AgentContext = aa = None
needs_harbor = pytest.mark.skipif(aa is None, reason="pip install harbor==0.24.0")


class FakeEnv:
    default_user = None

    def __init__(self):
        self.execs: list[dict] = []
        self.uploads: list[tuple] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.execs.append({"command": command, "env": env or {}, "user": user})
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    async def upload_dir(self, source_dir, target_dir):
        files = sorted(str(p.relative_to(source_dir)) for p in Path(source_dir).rglob("*")
                       if p.is_file())
        self.uploads.append((target_dir, files))


def _agent(tmp_path, **kw):
    return aa.AwosAgent(logs_dir=tmp_path, model_name="anthropic/claude-haiku-5-5", **kw)


@needs_harbor
def test_options_and_name(tmp_path):
    a = _agent(tmp_path, git_ref="HEAD", cost_cap=1.5, max_steps=50)
    assert aa.AwosAgent.name() == "awos"
    assert a.options.cost_cap == 1.5 and a.options.max_steps == 50
    assert _agent(tmp_path).options.max_steps == 1000
    with pytest.raises(ValueError):
        _agent(tmp_path, no_such_option=1)


@needs_harbor
def test_export_source_is_the_runtime_package(tmp_path):
    sha = aa.export_source("HEAD", tmp_path)
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    assert sha == head
    assert (tmp_path / "scaffold" / "agent" / "loop_executor.py").is_file()
    assert (tmp_path / "scaffold" / "agent" / "sandbox.py").is_file()
    assert not (tmp_path / "tests").exists() and not (tmp_path / ".awos").exists()
    assert (tmp_path / "AWOS_GIT_SHA").read_text().strip() == head


@needs_harbor
def test_install_uploads_source_and_pins_sdks(tmp_path):
    env = FakeEnv()
    a = _agent(tmp_path)
    asyncio.run(a.install(env))
    target, files = env.uploads[0]
    assert target == aa.REMOTE_SRC and "scaffold/agent/loop_executor.py" in files
    joined = "\n".join(e["command"] for e in env.execs)
    assert "anthropic==0.125.0" in joined and "openai==3.3.1" in joined
    assert "import scaffold.agent.headless_loop" in joined
    assert a.version() and len(a.version()) == 12


@needs_harbor
def test_run_sets_container_sandbox_and_hides_goal_in_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    env = FakeEnv()
    a = _agent(tmp_path, cost_cap=0.75, cmd_timeout_s=300)
    asyncio.run(a.run("Make hello.txt say hi; it's 'quoted'", env, AgentContext()))
    call = env.execs[-1]
    e = call["env"]
    assert e["AWOS_SANDBOX"] == "container" and e["AWOS_PROVIDER"] == "anthropic"
    assert e[aa.GOAL_ENV] == "Make hello.txt say hi; it's 'quoted'"
    assert e["AWOS_LOOP_CMD_TIMEOUT_S"] == "300"
    cmd = call["command"]
    assert "-m scaffold.agent.headless_loop" in cmd and "--goal-env HARBOR_AWOS_GOAL" in cmd
    assert "--model claude-haiku-5-5" in cmd and "--cost-cap 0.75" in cmd
    assert "--max-steps 1000" in cmd and "hello.txt" not in cmd
    assert "sk-test" not in cmd


@needs_harbor
def test_run_refuses_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError):
        asyncio.run(_agent(tmp_path).run("x", FakeEnv(), AgentContext()))


@needs_harbor
def test_populate_context_from_calls_even_when_killed(tmp_path):
    rows = [
        {"call": 1, "input_tokens": 12_000, "cached_tokens": 0, "cache_write_tokens": 10_000,
         "output_tokens": 300, "cost_usd_awos": 0.002},
        {"call": 2, "input_tokens": 120_000, "cached_tokens": 110_000,
         "cache_write_tokens": 0, "output_tokens": 100, "cost_usd_awos": 0.003},
    ]
    (tmp_path / aa.CALLS_FILE).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n{\"call\": 3, \"inp", encoding="utf-8")
    a = _agent(tmp_path)
    ctx = AgentContext()
    a.populate_context_post_run(ctx)
    assert ctx.n_input_tokens == 132_000 and ctx.n_cache_tokens == 110_000
    assert ctx.n_output_tokens == 400
    expected = (pricing.request_cost("claude-haiku-5-5", 12_000, 300, 0, 10_000)
                + pricing.request_cost("claude-haiku-5-5", 120_000, 100, 110_000, 0))
    assert ctx.cost_usd == pytest.approx(expected)
    assert ctx.metadata["stop_reason"] == "killed_or_unfinished"
    assert ctx.metadata["n_cache_write_tokens"] == 10_000
    (tmp_path / aa.RESULT_FILE).write_text(json.dumps({"stop_reason": "submitted", "steps": 2}))
    ctx2 = AgentContext()
    a.populate_context_post_run(ctx2)
    assert ctx2.metadata["stop_reason"] == "submitted" and ctx2.metadata["steps"] == 2


@needs_harbor
def test_harbor_resolves_the_import_path():
    from harbor.agents.factory import _import_agent_class

    assert _import_agent_class("harbor_agents.awos_agent:AwosAgent") is aa.AwosAgent
