"""T7 — cache-aware cost and cache telemetry (docs/specs/prompt_caching.md)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scaffold" / "agent"))

import llm_call_log  # noqa: E402
import providers  # noqa: E402

spec = importlib.util.spec_from_file_location("cache_report", ROOT / "scripts" / "cache_report.py")
cache_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache_report)

FLASH = "deepseek/deepseek-v4-flash"


@pytest.fixture(autouse=True)
def _cloud(monkeypatch):
    for name in ("AWOS_PROVIDER", "AWOS_AGENT_MODEL", "AWOS_PRICE_TABLE"):
        monkeypatch.delenv(name, raising=False)


def _openai_response(prompt=1000, completion=100, cached=None, write=None, cost=None,
                     timings=None):
    details = {}
    if cached is not None:
        details["cached_tokens"] = cached
    if write is not None:
        details["cache_write_tokens"] = write
    usage = {"prompt_tokens": prompt, "completion_tokens": completion}
    if details:
        usage["prompt_tokens_details"] = details
    if cost is not None:
        usage["cost"] = cost
    resp = {"model": FLASH, "usage": usage,
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
    if timings is not None:
        resp["timings"] = timings
    return resp


# ── usage parsing ────────────────────────────────────────────────────────────

def test_usage_tokens_openrouter_with_cache_fields():
    usage = {"prompt_tokens": 5000, "completion_tokens": 50,
             "prompt_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 500}}
    assert providers.usage_tokens(usage) == {
        "input": 5000, "cached": 4000, "cache_write": 500, "output": 50}


def test_usage_tokens_without_cache_fields():
    usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=30)
    assert providers.usage_tokens(usage) == {
        "input": 1200, "cached": 0, "cache_write": 0, "output": 30}


def test_usage_tokens_deepseek_direct_and_llama_server():
    ds = {"prompt_tokens": 900, "completion_tokens": 9, "prompt_cache_hit_tokens": 640}
    assert providers.usage_tokens(ds)["cached"] == 640
    llama = {"prompt_tokens": 300, "completion_tokens": 3}
    assert providers.usage_tokens(llama, {"cache_n": 236})["cached"] == 236


def test_usage_tokens_anthropic_adds_cache_back_into_input():
    usage = {"input_tokens": 100, "output_tokens": 20,
             "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100}
    assert providers.usage_tokens(usage) == {
        "input": 1000, "cached": 800, "cache_write": 100, "output": 20}


def test_usage_tokens_clamps_and_tolerates_garbage():
    assert providers.usage_tokens({"prompt_tokens": 10, "completion_tokens": 1,
                                   "prompt_tokens_details": {"cached_tokens": 99}})["cached"] == 10
    assert providers.usage_tokens(None) == {"input": 0, "cached": 0, "cache_write": 0, "output": 0}
    assert providers.usage_tokens({"prompt_tokens": "x"})["input"] == 0


# ── pricing ──────────────────────────────────────────────────────────────────

def test_estimate_cost_cached_matches_plain_estimate_without_cache():
    from agent_loop import estimate_cost
    assert providers.estimate_cost_cached(FLASH, 10_000, 500) == pytest.approx(
        estimate_cost(FLASH, 10_000, 500))


def test_estimate_cost_cached_prices_cache_reads_at_factor():
    t = providers.price_table(FLASH)
    assert t["cache_read"] == pytest.approx(t["input"] * 0.2)
    cost = providers.estimate_cost_cached(FLASH, 10_000, 500, cached_tokens=8_000)
    expected = (2_000 * t["input"] + 8_000 * t["cache_read"] + 500 * t["output"]) / 1e6
    assert cost == pytest.approx(expected)
    assert cost < providers.estimate_cost_cached(FLASH, 10_000, 500)


def test_cache_write_priced_at_write_factor_for_anthropic():
    t = providers.price_table("claude-haiku-4-5")
    assert t["cache_write"] == pytest.approx(t["input"] * 1.25)
    cost = providers.estimate_cost_cached("claude-haiku-4-5", 1000, 0, 0, 1000)
    assert cost == pytest.approx(1000 * t["cache_write"] / 1e6)


def test_unlisted_model_gets_no_discount_and_unpriced_is_zero():
    assert providers.estimate_cost_cached("totally-unknown-model", 1000, 100, 900) == 0.0
    t = providers.price_table("gemini-2.0-flash")
    assert t["cache_read"] == pytest.approx(t["input"] * 0.25)


def test_price_table_override_file(tmp_path, monkeypatch):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"deepseek-v4-flash": {"input": 0.1, "output": 0.2,
                                                      "cache_read": 0.01}}))
    monkeypatch.setenv("AWOS_PRICE_TABLE", str(path))
    t = providers.price_table(FLASH)
    assert (t["input"], t["output"], t["cache_read"]) == (0.1, 0.2, 0.01)
    assert t["cache_write"] == pytest.approx(0.1)
    cost = providers.estimate_cost_cached(FLASH, 1_000_000, 0, 1_000_000)
    assert cost == pytest.approx(0.01)


def test_local_mode_costs_zero(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    assert providers.estimate_cost_cached(FLASH, 10_000, 1000, 5000) == 0.0


def test_response_cost_prefers_provider_cost():
    assert providers.response_cost(FLASH, _openai_response(cost=0.0042)) == (0.0042, "provider")
    cost, source = providers.response_cost(FLASH, _openai_response(cached=900))
    assert source == "estimate"
    assert cost == pytest.approx(providers.estimate_cost_cached(FLASH, 1000, 100, 900))


def test_record_utility_spend_is_cache_aware():
    resp = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=10_000, completion_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=9_000)))
    cost = providers._record_utility_spend("probe", FLASH, resp, None)
    assert cost == pytest.approx(providers.estimate_cost_cached(FLASH, 10_000, 100, 9_000))


# ── llm_call_log fields ──────────────────────────────────────────────────────

def test_fields_from_response_cache_fields():
    resp = _openai_response(5000, 10, cached=4000, write=200)
    f = {**llm_call_log.fields_from_response(resp), **llm_call_log.cache_fields(resp)}
    assert f["cached_tokens"] == 4000
    assert f["cache_write_tokens"] == 200
    assert f["cache_hit_ratio"] == 0.8


def test_fields_from_response_without_cache_fields():
    resp = _openai_response(5000, 10)
    f = {**llm_call_log.fields_from_response(resp), **llm_call_log.cache_fields(resp)}
    assert f["cached_tokens"] is None and f["cache_hit_ratio"] is None
    assert f["cache_write_tokens"] is None


def test_fields_from_response_llama_timings():
    resp = _openai_response(300, 3, timings={"cache_n": 150})
    f = {**llm_call_log.fields_from_response(resp), **llm_call_log.cache_fields(resp)}
    assert f["cached_tokens"] == 150 and f["cache_hit_ratio"] == 0.5


def test_fields_from_response_anthropic_ratio_uses_whole_prompt():
    resp = {"model": "claude-haiku-4-5", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 100, "output_tokens": 5,
                      "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}}
    f = {**llm_call_log.fields_from_response(resp), **llm_call_log.cache_fields(resp)}
    assert f["cached_tokens"] == 900 and f["cache_hit_ratio"] == 0.9


def test_record_response_logs_cache_and_cost_source(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(log))
    llm_call_log.record_response("one_shot", FLASH, _openai_response(
        5000, 10, cached=4000, cost=0.0003), cost_usd=0.9)
    llm_call_log.record_response("agent", FLASH, _openai_response(5000, 10), cost_usd=0.0005)
    llm_call_log.record_call("acceptance", FLASH, FLASH, "stop", 100, 1, cached_tokens=25)
    a, b, c = [json.loads(x) for x in log.read_text().splitlines()]
    assert a["cost_usd"] == 0.0003 and a["cost_source"] == "provider"
    assert a["cache_hit_ratio"] == 0.8
    assert a["cost_est_usd"] == pytest.approx(
        providers.estimate_cost_cached(FLASH, 5000, 10, 4000), rel=1e-6)
    assert b["cost_usd"] == 0.0005 and b["cost_source"] == "estimate"
    assert b["cache_hit_ratio"] is None
    assert c["cache_hit_ratio"] == 0.25


# ── report aggregation ───────────────────────────────────────────────────────

def _write(path, lines):
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_bucket_of():
    assert cache_report.bucket_of({"component": "agent"}) == "agent"
    assert cache_report.bucket_of({"component": "acceptance",
                                   "request_type": "acceptance_arbitration"}) == "arbiter"
    assert cache_report.bucket_of({"component": "acceptance"}) == "acceptance"
    assert cache_report.bucket_of({"component": "planner"}) == "other"


def test_summarise_aggregates_and_tolerates_old_logs(tmp_path):
    run = tmp_path / "run" / "r1" / "off" / "state" / ".awos"
    run.mkdir(parents=True)
    _write(run / "llm_calls.jsonl", [
        # new-style line
        {"component": "agent", "requested_model": FLASH, "input_tokens": 1000,
         "output_tokens": 10, "cached_tokens": 800, "cache_hit_ratio": 0.8,
         "cost_usd": 0.0001, "provider": "GMICloud",
         "cost_est_usd": providers.estimate_cost_cached(FLASH, 1000, 10, 800)},
        # old-style line: no cached_tokens, no cost_est_usd
        {"component": "one_shot", "requested_model": FLASH, "input_tokens": 1000,
         "output_tokens": 10, "cost_usd": 0.0002, "provider": "DeepInfra"},
        {"component": "acceptance", "request_type": "acceptance_arbitration",
         "requested_model": FLASH, "input_tokens": 500, "output_tokens": 5,
         "cached_tokens": None, "cost_usd": None},
        {"component": "agent", "requested_model": FLASH, "error": "Timeout",
         "input_tokens": None, "output_tokens": None},
    ])
    (run / "llm_calls.jsonl").open("a").write("not json\n")
    logs = cache_report.find_logs([str(tmp_path / "run")])
    assert len(logs) == 1
    s = cache_report.summarise(cache_report.iter_lines(logs))
    assert s["total"]["calls"] == 4 and s["total"]["errors"] == 1
    assert s["total"]["input_tokens"] == 2500 and s["total"]["cached_tokens"] == 800
    assert s["total"]["cache_hit_ratio"] == pytest.approx(800 / 2500, abs=1e-4)
    assert s["by_component"]["agent"]["cache_hit_ratio"] == 0.8
    assert s["by_component"]["one_shot"]["cache_hit_ratio"] == 0.0
    assert s["by_component"]["arbiter"]["calls"] == 1
    assert list(s["by_component"]) == ["one_shot", "agent", "arbiter"]
    assert s["total"]["cost_usd"] == pytest.approx(0.0003)
    old_est = providers.estimate_cost_cached(FLASH, 1000, 10)
    assert s["by_component"]["one_shot"]["est_usd"] == pytest.approx(old_est, abs=1e-6)
    assert s["by_component"]["one_shot"]["saved_usd"] == pytest.approx(0.0, abs=1e-9)
    assert s["by_component"]["agent"]["saved_usd"] > 0
    assert set(s["by_provider"]) == {"GMICloud", "DeepInfra", "unknown"}
    text = cache_report.render(s, 1)
    assert "TOTAL" in text and "arbiter" in text


def test_cli_missing_logs_returns_1(tmp_path, capsys):
    assert cache_report.main([str(tmp_path)]) == 1
