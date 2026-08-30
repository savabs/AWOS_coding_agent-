"""
provider_health.py — Fast balance/validity checks for LLM providers.

Checks API keys have sufficient balance BEFORE making expensive calls.
Caches results with TTL so we don't check every request.
Used by chat service and escalation engine to skip dead providers.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

# ── Cache ──────────────────────────────────────────────────────────────────
_HEALTH_CACHE: dict[str, "ProviderHealth"] = {}
_CACHE_TTL_SEC = 300  # 5 minutes
_CHECK_TIMEOUT_SEC = 2.0  # fail fast


@dataclass
class ProviderHealth:
    provider: str
    available: bool
    balance_ok: bool
    latency_ms: float
    checked_at: float = field(default_factory=time.time)
    error: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def stale(self) -> bool:
        return (time.time() - self.checked_at) > _CACHE_TTL_SEC

    @property
    def can_use(self) -> bool:
        return self.available and self.balance_ok


def _cached(provider: str) -> Optional["ProviderHealth"]:
    h = _HEALTH_CACHE.get(provider)
    if h and not h.stale:
        return h
    return None


def _store(h: "ProviderHealth") -> None:
    _HEALTH_CACHE[h.provider] = h


# ── DeepSeek ────────────────────────────────────────────────────────────────
def check_deepseek() -> ProviderHealth:
    cached = _cached("deepseek")
    if cached:
        return cached

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        h = ProviderHealth("deepseek", available=False, balance_ok=False, latency_ms=0, error="No DEEPSEEK_API_KEY")
        _store(h)
        return h

    t0 = time.perf_counter()
    client = None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=_CHECK_TIMEOUT_SEC)
        # Minimal completion — 1 token to verify balance
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            temperature=0,
        )
        latency = (time.perf_counter() - t0) * 1000
        h = ProviderHealth("deepseek", available=True, balance_ok=True, latency_ms=round(latency, 1))
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        err = str(e)[:120]
        balance_ok = "402" not in err and "insufficient" not in err.lower() and "balance" not in err.lower()
        h = ProviderHealth("deepseek", available=False, balance_ok=balance_ok, latency_ms=round(latency, 1), error=err)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    _store(h)
    return h


# ── Anthropic ───────────────────────────────────────────────────────────────
def check_anthropic() -> ProviderHealth:
    cached = _cached("anthropic")
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        h = ProviderHealth("anthropic", available=False, balance_ok=False, latency_ms=0, error="No ANTHROPIC_API_KEY")
        _store(h)
        return h

    t0 = time.perf_counter()
    client = None
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key, timeout=_CHECK_TIMEOUT_SEC)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        latency = (time.perf_counter() - t0) * 1000
        h = ProviderHealth("anthropic", available=True, balance_ok=True, latency_ms=round(latency, 1))
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        err = str(e)[:120]
        balance_ok = not any(kw in err.lower() for kw in ("402", "429", "billing", "payment", "credit", "balance"))
        h = ProviderHealth("anthropic", available=False, balance_ok=balance_ok, latency_ms=round(latency, 1), error=err)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    _store(h)
    return h


# ── OpenAI ──────────────────────────────────────────────────────────────────
def check_openai() -> ProviderHealth:
    cached = _cached("openai")
    if cached:
        return cached

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        h = ProviderHealth("openai", available=False, balance_ok=False, latency_ms=0, error="No OPENAI_API_KEY")
        _store(h)
        return h

    t0 = time.perf_counter()
    client = None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, timeout=_CHECK_TIMEOUT_SEC)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        latency = (time.perf_counter() - t0) * 1000
        h = ProviderHealth("openai", available=True, balance_ok=True, latency_ms=round(latency, 1))
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        err = str(e)[:120]
        balance_ok = not any(kw in err.lower() for kw in ("402", "429", "billing", "payment", "credit", "quota"))
        h = ProviderHealth("openai", available=False, balance_ok=balance_ok, latency_ms=round(latency, 1), error=err)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    _store(h)
    return h


# ── Gemini ──────────────────────────────────────────────────────────────────
def check_gemini() -> ProviderHealth:
    cached = _cached("gemini")
    if cached:
        return cached

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        h = ProviderHealth("gemini", available=False, balance_ok=False, latency_ms=0, error="No GEMINI_API_KEY")
        _store(h)
        return h

    t0 = time.perf_counter()
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        resp = model.generate_content("hi", generation_config={"max_output_tokens": 1})
        latency = (time.perf_counter() - t0) * 1000
        h = ProviderHealth("gemini", available=True, balance_ok=True, latency_ms=round(latency, 1))
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        err = str(e)[:120]
        balance_ok = not any(kw in err.lower() for kw in ("402", "429", "billing", "quota", "exceeded"))
        h = ProviderHealth("gemini", available=False, balance_ok=balance_ok, latency_ms=round(latency, 1), error=err)

    _store(h)
    return h


# ── Bulk Check ──────────────────────────────────────────────────────────────
def check_all() -> dict[str, ProviderHealth]:
    """Run all health checks and return results."""
    results = {}
    checks = [
        ("opencode", check_opencode_go),
        ("deepseek", check_deepseek),
        ("anthropic", check_anthropic),
        ("openai", check_openai),
        ("gemini", check_gemini),
    ]
    for name, fn in checks:
        results[name] = fn()
    return results


def get_healthy_providers() -> list[str]:
    """Return list of providers that are ready to use."""
    healthy = []
    for name in ("deepseek", "anthropic", "openai", "gemini"):
        h = _cached(name)
        if h is None:
            # Force check if never checked
            checks = {"deepseek": check_deepseek, "anthropic": check_anthropic,
                       "openai": check_openai, "gemini": check_gemini}
            h = checks[name]()
        if h and h.can_use:
            healthy.append(name)
    return healthy


def clear_cache() -> None:
    _HEALTH_CACHE.clear()


# ── OpenCode Go ─────────────────────────────────────────────────────────────
def check_opencode_go() -> ProviderHealth:
    """Check OpenCode Go subscription — list models to verify key validity."""
    cached = _cached("opencode")
    if cached:
        return cached

    api_key = os.getenv("OPENCODE_GO_API_KEY")
    if not api_key:
        h = ProviderHealth("opencode", available=False, balance_ok=False, latency_ms=0, error="No OPENCODE_GO_API_KEY")
        _store(h)
        return h

    t0 = time.perf_counter()
    client = None
    try:
        from openai import OpenAI
        base = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
        client = OpenAI(api_key=api_key, base_url=base, timeout=_CHECK_TIMEOUT_SEC)
        # List models to verify key + subscription
        resp = client.models.list()
        model_count = len(resp.data) if hasattr(resp, 'data') else 0
        latency = (time.perf_counter() - t0) * 1000
        h = ProviderHealth("opencode", available=True, balance_ok=True, latency_ms=round(latency, 1))
        h.metadata["models"] = model_count
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        err = str(e)[:120]
        balance_ok = not any(kw in err.lower() for kw in ("402", "insufficient", "balance", "subscription"))
        h = ProviderHealth("opencode", available=False, balance_ok=balance_ok, latency_ms=round(latency, 1), error=err)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    _store(h)
    return h

# OpenCode Go model catalog — (model_id, name, input_$/MTok, output_$/MTok, complexity_floor)
OPENCODE_GO_MODELS = [
    ("mimo-v2.5",        "MiMo-V2.5",        0.14, 0.28, 0),   # T0: cheapest, for simple tasks
    ("deepseek-v4-flash", "DeepSeek V4 Flash", 0.14, 0.28, 3),  # T1: cheap, good for most coding
    ("minimax-m3",        "MiniMax M3",        0.30, 1.20, 4),  # T2: mid-tier
    ("deepseek-v4-pro",   "DeepSeek V4 Pro",   0.435, 0.87, 5), # T2: mid-tier, strong coding
    ("qwen3.7-plus",      "Qwen3.7 Plus",      0.40, 1.60, 5),  # T3: strong general
    ("kimi-k2.7-code",    "Kimi K2.7 Code",    0.95, 4.00, 6),  # T4: specialized coding
    ("glm-5.2",           "GLM-5.2",           1.40, 4.40, 7),  # T5: very strong
    ("qwen3.7-max",       "Qwen3.7 Max",       2.50, 7.50, 8),  # T5: strong
    ("grok-4.5",          "Grok 4.5",          2.00, 6.00, 9),  # T6: best, expensive
]
