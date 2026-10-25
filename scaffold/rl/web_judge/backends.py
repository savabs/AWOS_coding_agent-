"""
backends.py — chat-completion backends for WebJudge. Stdlib only (urllib), no SDK needed.

    OpenAICompatBackend(kind="o4mini")      api.openai.com, o4-mini; usage-based cost under a Budget cap
    OpenAICompatBackend(kind="webjudge7b")  any OpenAI-compatible server (vLLM serving osunlp/WebJudge-7B),
                                            upstream's request: max_tokens=512, temperature=0; no cost

Every backend has generate(messages, purpose) -> str, where purpose is "key_points" | "image" | "outcome"
(fake backends in the tests answer per purpose). The API key is read from the environment and only ever
goes into the Authorization header: it is never logged, stored or put into an error message.
"""
from __future__ import annotations

import base64
import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from . import cost as C
from .layout import image_size_bytes

OPENAI_BASE = "https://api.openai.com/v1"
WEBJUDGE_7B = "osunlp/WebJudge-7B"
WEBJUDGE_7B_REVISION = "bb550f8e301855933db5490eb8128217b3aca5dc"   # HF main, 2025-05-12, checked 2026-10-25
UPSTREAM_MAX_TOKENS = 512          # upstream OpenaiEngine.generate default
INPUT_SAFETY = 1.25                # the cap reserves estimated prompt tokens x this (estimate is ~4 chars/token)


class BackendError(RuntimeError):
    """A call failed after retries. fatal=True means no later call can succeed either (bad key, model
    shut down, no quota): the run stops instead of marking every episode judge_error."""

    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal


Transport = Callable[[str, dict, dict, float], dict]


def urllib_transport(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode(errors="replace")[:600]
        except Exception:
            detail = ""
        raise HTTPStatusError(e.code, detail) from None


class HTTPStatusError(RuntimeError):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


FATAL_MARKERS = ("model_not_found", "does not exist", "invalid_api_key", "insufficient_quota",
                 "has been deprecated", "is not supported", "unsupported_parameter", "unsupported_value")


def _is_fatal(e: Exception) -> bool:
    if isinstance(e, HTTPStatusError):
        if e.status in (401, 403, 404):
            return True
        if e.status == 400 or e.status == 429:
            return any(m in e.detail for m in FATAL_MARKERS)
    return False


def _retryable(e: Exception) -> bool:
    if isinstance(e, HTTPStatusError):
        return e.status in (408, 409, 429) or e.status >= 500
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError))


def data_url_tokens(part: dict, patch_budget: int) -> int:
    url = (part.get("image_url") or {}).get("url", "")
    try:
        size = image_size_bytes(base64.b64decode(url.split(",", 1)[1][:2_000_000]))
    except Exception:
        size = None
    w, h = size or (2048, 2048)                       # unknown size: assume a large image (conservative)
    return C.image_tokens(w, h, patch_budget=patch_budget)


class OpenAICompatBackend:
    def __init__(self, kind: str, *, base_url: Optional[str] = None, model: Optional[str] = None,
                 api_key_env: Optional[str] = None, max_output_tokens: Optional[int] = None,
                 service_tier: str = "standard", budget: Optional[C.Budget] = None,
                 patch_budget: int = C.DEFAULT_PATCH_BUDGET, timeout: float = 300.0, tries: int = 3,
                 transport: Transport = urllib_transport, sleep: Callable[[float], None] = time.sleep):
        if kind not in ("o4mini", "webjudge7b"):
            raise ValueError(f"unknown backend kind {kind!r}")
        self.kind = kind
        self.reasoning = kind == "o4mini"
        self.base_url = (base_url or (OPENAI_BASE if self.reasoning else
                                      os.environ.get("WEBJUDGE_BASE_URL", "http://127.0.0.1:8000/v1"))).rstrip("/")
        self.model = model or ("o4-mini" if self.reasoning else WEBJUDGE_7B)
        self.api_key_env = api_key_env or ("OPENAI_API_KEY" if self.reasoning else "WEBJUDGE_API_KEY")
        self.max_output_tokens = max_output_tokens or (8192 if self.reasoning else UPSTREAM_MAX_TOKENS)
        self.service_tier = service_tier
        self.prices = C.prices_for(self.model, service_tier) if self.reasoning else None
        if self.reasoning and budget is None:
            raise ValueError("the o4mini backend needs a Budget (hard USD cap)")
        self.budget = budget
        self.patch_budget = patch_budget
        self.timeout = timeout
        self.tries = tries
        self.transport = transport
        self.sleep = sleep
        if self.reasoning and not os.environ.get(self.api_key_env):
            raise BackendError(f"{self.api_key_env} is not set", fatal=True)

    @property
    def name(self) -> str:
        return f"{self.kind}:{self.model}" + (f":{self.service_tier}" if self.reasoning else "")

    def body(self, messages) -> dict:
        if self.reasoning:
            # deviation 1 (NOTICE): o-series refuse max_tokens and temperature != 1
            b = {"model": self.model, "messages": messages, "max_completion_tokens": self.max_output_tokens}
            if self.service_tier == "flex":
                b["service_tier"] = "flex"
            return b
        return {"model": self.model, "messages": messages, "max_tokens": self.max_output_tokens, "temperature": 0}

    def worst_case_usd(self, messages) -> float:
        if not self.prices:
            return 0.0
        est = C.message_tokens(messages, lambda p: data_url_tokens(p, self.patch_budget))
        return C.usd(self.prices, math.ceil(est * INPUT_SAFETY), self.max_output_tokens)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        key = os.environ.get(self.api_key_env)
        if key:
            h["Authorization"] = "Bearer " + key
        return h

    def _post(self, body: dict) -> dict:
        url = self.base_url + "/chat/completions"
        last: Optional[Exception] = None
        for attempt in range(self.tries):
            try:
                return self.transport(url, body, self._headers(), self.timeout)
            except Exception as e:                       # noqa: BLE001 - classified below
                if _is_fatal(e):
                    raise BackendError(f"{self.name}: {e}", fatal=True) from None
                if not _retryable(e):
                    raise BackendError(f"{self.name}: {e}") from None
                last = e
                if attempt + 1 < self.tries:
                    self.sleep(2.0 * 4 ** attempt)       # 2 s, 8 s (upstream: backoff.expo, max_tries=3)
        raise BackendError(f"{self.name}: failed after {self.tries} tries: {last}")

    def generate(self, messages, purpose: str = "") -> str:
        reserved = self.budget.reserve(self.worst_case_usd(messages)) if self.budget else 0.0
        try:
            resp = self._post(self.body(messages))
        except BaseException:
            if self.budget:
                # a failed request may still be billed; keep the worst case as spent unless it was refused
                self.budget.settle(reserved, reserved if self.prices else 0.0)
            raise
        usage = resp.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        if self.budget:
            billed = C.usd(self.prices, pt, ct, cached) if (self.prices and usage) else reserved
            self.budget.settle(reserved, billed, pt, ct, cached)
        try:
            return resp["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            raise BackendError(f"{self.name}: malformed response (no choices[0].message)") from None
