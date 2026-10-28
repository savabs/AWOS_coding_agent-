"""
cost.py — o4-mini price table, image token counts, per-run cost estimate, and the hard USD cap.

Prices are USD per 1M tokens, checked 2026-10-25 on https://developers.openai.com/api/docs/pricing
(platform.openai.com/docs/pricing redirects there):
    o4-mini  Standard  input $1.10  cached input $0.275  output $4.40
             Flex      input $0.55  cached input $0.138  output $2.20   (service_tier="flex")
Reasoning tokens are billed as output tokens.

Image tokens, checked 2026-10-25 on https://developers.openai.com/api/docs/guides/images-vision: o4-mini
is a patch-based model, patches = ceil(w/32) * ceil(h/32), billable tokens = ceil(patches * 1.72). That page
lists no patch budget for o4-mini. Earlier versions of the page gave 1536 patches for the o4-mini /
gpt-4.1-mini family, so the estimate uses 1536 (--image-patch-budget). A budget of 0 means "no resize",
which is the worst case for full-page screenshots.

NOTE (2026-10-25): https://developers.openai.com/api/docs/deprecations lists o4-mini and o4-mini-2025-04-16
for shutdown on 2026-10-23 (replacement gpt-5.6-terra). Calls can now fail with model_not_found. A
replacement model is not the published judge, so its numbers are not comparable with the leaderboard.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

PRICING_URL = "https://developers.openai.com/api/docs/pricing"
VISION_URL = "https://developers.openai.com/api/docs/guides/images-vision"
PRICES_CHECKED = "2026-10-25"

# model -> service tier -> (input, cached input, output) USD per 1M tokens
PRICES: Dict[str, Dict[str, tuple]] = {
    "o4-mini": {"standard": (1.10, 0.275, 4.40), "flex": (0.55, 0.138, 2.20)},
    "o4-mini-2025-04-16": {"standard": (1.10, 0.275, 4.40), "flex": (0.55, 0.138, 2.20)},
}
PATCH = 32
O4_MINI_MULTIPLIER = 1.72
DEFAULT_PATCH_BUDGET = 1536

# Expected output tokens per call (visible answer + reasoning), used by the dry-run estimate only. Visible
# answers in the published o4-mini records (data/evaluation_results/.../webjudge_o4-mini) average ~640 chars
# per screenshot judgment (~160 tokens), ~120 chars of key points and ~370 chars of final verdict; the rest is
# a reasoning allowance (o4-mini's default effort). The cap never relies on these: it reserves max_output_tokens.
EXPECTED_OUTPUT = {"key_points": 500, "image": 700, "outcome": 1500}
# Share of screenshots scoring >= 3 in the published o4-mini records (Claude 3.5 run: 0.366); estimate only.
EXPECTED_KEY_FRACTION = 0.37
MAX_IMAGE = 50


def text_tokens(s: str) -> int:
    """~4 characters per token (no tokenizer dependency); +4 per message is added by the caller."""
    return math.ceil(len(s or "") / 4)


def image_tokens(w: int, h: int, multiplier: float = O4_MINI_MULTIPLIER,
                 patch_budget: int = DEFAULT_PATCH_BUDGET) -> int:
    """OpenAI's patch method: count 32px patches; above the budget, shrink proportionally so the patch
    grid fits (the documented shrink factor with its integer-grid adjustment); bill ceil(patches * m)."""
    if w <= 0 or h <= 0:
        return 0
    patches = math.ceil(w / PATCH) * math.ceil(h / PATCH)
    if patch_budget and patches > patch_budget:
        shrink = math.sqrt(PATCH * PATCH * patch_budget / (w * h))
        adj = shrink * min(math.floor(w * shrink / PATCH) / (w * shrink / PATCH),
                           math.floor(h * shrink / PATCH) / (h * shrink / PATCH))
        patches = math.ceil(w * adj / PATCH) * math.ceil(h * adj / PATCH)
        patches = min(patches, patch_budget)
    return math.ceil(patches * multiplier)


def usd(prices: tuple, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    pin, pcached, pout = prices
    cached = min(cached_tokens, prompt_tokens)
    return ((prompt_tokens - cached) * pin + cached * pcached + completion_tokens * pout) / 1e6


def prices_for(model: str, tier: str = "standard") -> tuple:
    if model not in PRICES:
        raise KeyError(f"no price for {model!r}: add it to scaffold/rl/web_judge/cost.py PRICES with a source")
    return PRICES[model][tier]


class CapReached(RuntimeError):
    pass


class Budget:
    """Thread-safe hard cap. Before each call, reserve() its worst case (input estimate x safety + the full
    max_output_tokens). The call goes ahead only if spent + outstanding reservations + this one <= max_usd.
    settle() replaces the reservation by the billed cost from the API's usage numbers."""

    def __init__(self, max_usd: Optional[float]):
        self.max_usd = max_usd
        self.spent = 0.0
        self.reserved = 0.0
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0
        self.refused = 0
        self._lock = threading.Lock()

    def reserve(self, amount: float) -> float:
        with self._lock:
            if self.max_usd is not None and self.spent + self.reserved + amount > self.max_usd + 1e-12:
                self.refused += 1
                raise CapReached(f"cap ${self.max_usd:.2f}: spent ${self.spent:.4f} + reserved "
                                 f"${self.reserved:.4f} + next call worst case ${amount:.4f}")
            self.reserved += amount
            return amount

    def settle(self, reserved: float, cost: float, prompt_tokens: int = 0, completion_tokens: int = 0,
               cached_tokens: int = 0) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
            self.spent += cost
            self.calls += 1
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.cached_tokens += cached_tokens

    def release(self, reserved: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)

    def as_dict(self) -> dict:
        return {"max_usd": self.max_usd, "spent_usd": round(self.spent, 6), "calls": self.calls,
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "cached_tokens": self.cached_tokens, "refused_calls": self.refused}


def message_tokens(messages, image_token_fn) -> int:
    """Estimated prompt tokens of a chat request: text at ~4 chars/token, images via image_token_fn(url)."""
    n = 3
    for m in messages:
        n += 4
        c = m.get("content")
        if isinstance(c, str):
            n += text_tokens(c)
            continue
        for part in c or []:
            if part.get("type") == "text":
                n += text_tokens(part.get("text", ""))
            elif part.get("type") == "image_url":
                n += image_token_fn(part)
    return n


@dataclass
class EpisodeEstimate:
    calls: int
    prompt_tokens: int
    expected_output_tokens: int
    worst_output_tokens: int
    images: int


def estimate_episode(task: str, actions: Iterable[str], sizes, *, patch_budget: int = DEFAULT_PATCH_BUDGET,
                     key_fraction: float = EXPECTED_KEY_FRACTION, max_output_tokens: int = 8192,
                     expected_output: Optional[dict] = None, worst_key_images: bool = False) -> EpisodeEstimate:
    """Token estimate for one episode's WebJudge pass: 1 key-point call + 1 call per screenshot + 1 outcome
    call carrying the key screenshots (expected share key_fraction, or all up to 50 with worst_key_images)."""
    from . import prompts as P
    exp = dict(EXPECTED_OUTPUT, **(expected_output or {}))
    actions = list(actions)
    sizes = list(sizes)
    kp_text = 120                                           # characters of key points (published average)
    img_tok = [image_tokens(w, h, patch_budget=patch_budget) for w, h in sizes]
    kp = 7 + 8 + text_tokens(P.KEY_POINTS_SYSTEM) + text_tokens(P.KEY_POINTS_PROMPT.format(task=task))
    per_image_text = 11 + text_tokens(P.IMAGE_SYSTEM) + text_tokens(P.IMAGE_PROMPT) + text_tokens(task) + kp_text // 4
    images = sum(per_image_text + t for t in img_tok)
    n_key = len(img_tok) if worst_key_images else round(len(img_tok) * key_fraction)
    n_key = min(n_key, MAX_IMAGE)
    key_tok = sorted(img_tok, reverse=True)[:n_key] if worst_key_images else (
        [sum(img_tok) / len(img_tok)] * n_key if img_tok else [])
    outcome = (11 + text_tokens(P.OUTCOME_SYSTEM) + text_tokens(P.OUTCOME_PROMPT) + text_tokens(task) + kp_text // 4
               + sum(text_tokens(a) + 2 for a in actions) + n_key * 40 + int(sum(key_tok)))
    calls = 2 + len(img_tok)
    expected_out = exp["key_points"] + exp["image"] * len(img_tok) + exp["outcome"]
    return EpisodeEstimate(calls=calls, prompt_tokens=kp + images + outcome, expected_output_tokens=expected_out,
                           worst_output_tokens=calls * max_output_tokens, images=len(img_tok))
