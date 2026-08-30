#!/usr/bin/env python3
"""
AWOS Model Validation — tests every model for content, speed, and format.
Usage: python3 scripts/validate_models.py         
       python3 scripts/validate_models.py --json   # JSON output
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
load_dotenv()

KEY = os.getenv("OPENCODE_GO_API_KEY")
BASE = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
TIMEOUT = 8  # seconds per model


def test_openai_model(model_id: str) -> dict[str, Any]:
    """Test via OpenAI-compatible /v1/chat/completions endpoint."""
    from openai import OpenAI
    client = OpenAI(api_key=KEY, base_url=BASE, timeout=TIMEOUT)
    result = {"model": model_id, "endpoint": "chat/completions", "status": "unknown"}

    # Non-streaming test
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "Say hello in 3 words"}],
            max_tokens=30, temperature=0, stream=False,
        )
        total_ms = (time.perf_counter() - t0) * 1000
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None
        content = msg.content or "" if msg else ""
        result["latency_ms"] = round(total_ms, 1)
        result["finish_reason"] = choice.finish_reason if choice else "?"
        result["tokens"] = resp.usage.completion_tokens if resp.usage else 0
        result["reasoning_tokens"] = (
            resp.usage.completion_tokens_details.reasoning_tokens
            if resp.usage and resp.usage.completion_tokens_details
            else 0
        )

        # Check for reasoning_content in raw response
        raw_content = content
        raw_reasoning = ""
        if msg and not content:
            extra = getattr(msg, 'model_extra', {}) or {}
            raw_reasoning = extra.get('reasoning_content', '') or ''

        if raw_content:
            result["status"] = "✅"
            result["content_field"] = "content"
            result["content_length"] = len(raw_content)
        elif raw_reasoning:
            result["status"] = "✅"
            result["content_field"] = "reasoning_content"
            result["content_length"] = len(raw_reasoning)
        else:
            result["status"] = "⬜"
            result["content_field"] = "empty"
            result["content_length"] = 0

    except Exception as e:
        result["status"] = "❌"
        result["error"] = str(e)[:80]
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    return result


def test_anthropic_model(model_id: str) -> dict[str, Any]:
    """Test via Anthropic-compatible /v1/messages endpoint."""
    result = {"model": model_id, "endpoint": "messages", "status": "unknown"}
    headers = {
        "x-api-key": KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model_id,
        "max_tokens": 30,
        "messages": [{"role": "user", "content": "Say hello in 3 words"}],
    }

    # Non-streaming test
    t0 = time.perf_counter()
    try:
        resp = requests.post(f"{BASE}/messages", json=body, headers=headers, timeout=TIMEOUT)
        total_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            result["status"] = "❌"
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:80]}"
        else:
            data = resp.json()
            content = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")
            if content:
                result["status"] = "✅"
                result["content_field"] = "content"
                result["content_length"] = len(content)
            else:
                result["status"] = "⬜"
                result["content_field"] = "empty"
                result["content_length"] = 0
        result["latency_ms"] = round(total_ms, 1)
    except Exception as e:
        result["status"] = "❌"
        result["error"] = str(e)[:80]
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    return result


def test_streaming(model_id: str, protocol: str) -> dict[str, Any]:
    """Test streaming support and TTFB."""
    result = {"ttfb_ms": None, "stream_total_ms": None, "stream_chars": 0, "stream_ok": False}
    t_total = time.perf_counter()
    ttfb = None
    chars = 0

    try:
        if protocol == "anthropic":
            headers = {
                "x-api-key": KEY, "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body = {
                "model": model_id, "max_tokens": 30,
                "messages": [{"role": "user", "content": "Say hello in 3 words"}],
                "stream": True,
            }
            resp = requests.post(f"{BASE}/messages", json=body, headers=headers, timeout=TIMEOUT, stream=True)
            if resp.status_code != 200:
                result["stream_error"] = f"HTTP {resp.status_code}"
                return result
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {}).get("text", "")
                        if delta:
                            if ttfb is None:
                                ttfb = (time.perf_counter() - t_total) * 1000
                            chars += len(delta)
                    elif event.get("type") == "message_stop":
                        break
                except Exception:
                    continue
        else:
            from openai import OpenAI
            client = OpenAI(api_key=KEY, base_url=BASE, timeout=TIMEOUT)
            stream = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "Say hello in 3 words"}],
                max_tokens=30, temperature=0, stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = delta.content or ""
                if not content:
                    extra = getattr(delta, 'model_extra', {}) or {}
                    content = extra.get('reasoning_content', '') or ''
                if content:
                    if ttfb is None:
                        ttfb = (time.perf_counter() - t_total) * 1000
                    chars += len(content)

        result["ttfb_ms"] = round(ttfb, 1) if ttfb else None
        result["stream_total_ms"] = round((time.perf_counter() - t_total) * 1000, 1)
        result["stream_chars"] = chars
        result["stream_ok"] = chars > 0
    except Exception as e:
        result["stream_error"] = str(e)[:80]

    return result


def validate_all() -> list[dict[str, Any]]:
    """Test all models and return results."""
    results = []
    
    # Models organized by protocol
    openai_models = [
        ("grok-4.5", "Grok 4.5"),
        ("deepseek-v4-pro", "DeepSeek V4 Pro"),
        ("deepseek-v4-flash", "DeepSeek V4 Flash"),
        ("kimi-k2.7-code", "Kimi K2.7 Code"),
        ("kimi-k2.6", "Kimi K2.6"),
        ("kimi-k3", "Kimi K3"),
        ("glm-5.2", "GLM-5.2"),
        ("glm-5.1", "GLM-5.1"),
        ("mimo-v2.5", "MiMo V2.5"),
        ("mimo-v2.5-pro", "MiMo V2.5 Pro"),
    ]
    anthropic_models = [
        ("minimax-m3", "MiniMax M3"),
        ("minimax-m2.7", "MiniMax M2.7"),
        ("qwen3.7-plus", "Qwen3.7 Plus"),
        ("qwen3.7-max", "Qwen3.7 Max"),
        ("qwen3.6-plus", "Qwen3.6 Plus"),
    ]

    for model_id, name in openai_models:
        r = test_openai_model(model_id)
        r["display_name"] = name
        if r["status"] != "❌":
            s = test_streaming(model_id, "openai")
            r.update(s)
        results.append(r)
        _print_result(r)

    for model_id, name in anthropic_models:
        r = test_anthropic_model(model_id)
        r["display_name"] = name
        if r["status"] != "❌":
            s = test_streaming(model_id, "anthropic")
            r.update(s)
        results.append(r)
        _print_result(r)

    # Summary
    total = len(results)
    working = sum(1 for r in results if r["status"] == "✅")
    empty = sum(1 for r in results if r["status"] == "⬜")
    failed = sum(1 for r in results if r["status"] == "❌")
    
    print(f"\n{'='*55}")
    print(f"  SUMMARY: {working} working · {empty} empty · {failed} failed of {total} total")
    
    if working > 0:
        fastest = min((r for r in results if r["status"] == "✅"), key=lambda x: x.get("latency_ms", 9999))
        print(f"  FASTEST: {fastest['display_name']} ({fastest['latency_ms']:.0f}ms)")
        cheapest_w = [r for r in results if r["status"] == "✅"]
        print(f"  WORKING MODELS: {', '.join(r['display_name'] for r in cheapest_w)}")

    return results


def _print_result(r: dict) -> None:
    icon = r.get("status", "❓")
    name = r.get("display_name", r["model"])
    cf = r.get("content_field", "")
    latency = r.get("latency_ms", 0)
    chars = r.get("content_length", 0)
    ttfb = r.get("ttfb_ms", None)
    error = r.get("error", r.get("stream_error", ""))
    
    line = f"  {icon} {name:22s} {r['model']:20s} {r['endpoint']:18s} {latency:4.0f}ms  chars={chars}"
    if ttfb:
        line += f"  ttfb={ttfb:.0f}ms"
    if cf and cf not in ("empty", "content"):
        line += f"  [{cf}]"
    if error:
        line += f"  {error[:50]}"
    print(line)


if __name__ == "__main__":
    results = validate_all()
    if "--json" in sys.argv:
        print("\nJSON:")
        print(json.dumps(results, indent=2))
