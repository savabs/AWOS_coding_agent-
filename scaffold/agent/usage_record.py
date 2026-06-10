"""
usage_record.py — Record actual API token usage from provider responses.

Providers return token counts (not dollars). We persist tokens as the
primary measure and derive cost only when needed for budget limits.
"""

from __future__ import annotations

from typing import Any, Optional


def cost_from_tokens(
    input_tokens: int,
    output_tokens: int,
    input_price: float,
    output_price: float,
) -> float:
    """USD cost from token counts and $/MTok rates."""
    return (
        (input_tokens / 1_000_000) * input_price
        + (output_tokens / 1_000_000) * output_price
    )


def record_api_usage(
    *,
    request_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    input_price: float,
    output_price: float,
    tracker: Any = None,
) -> dict[str, float | int]:
    """
    Record one API call to TokenTracker + BudgetLedger.

    Returns dict with input_tokens, output_tokens, cost_usd, total_tokens.
    """
    cost = cost_from_tokens(input_tokens, output_tokens, input_price, output_price)

    if tracker is not None:
        tracker.record(request_type, model, input_tokens, output_tokens, cost)

    try:
        from budget_ledger import get_ledger
        get_ledger().record(
            request_type=request_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
    except Exception:
        pass

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost,
    }


def merge_usage(
    accum: dict[str, float | int],
    recorded: dict[str, float | int],
) -> dict[str, float | int]:
    """Add recorded usage into accum dict (mutates and returns accum)."""
    accum["input_tokens"] = int(accum.get("input_tokens", 0)) + int(recorded.get("input_tokens", 0))
    accum["output_tokens"] = int(accum.get("output_tokens", 0)) + int(recorded.get("output_tokens", 0))
    accum["cost_usd"] = float(accum.get("cost_usd", 0.0)) + float(recorded.get("cost_usd", 0.0))
    accum["total_tokens"] = int(accum["input_tokens"]) + int(accum["output_tokens"])
    return accum


def empty_usage() -> dict[str, float | int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}


def merge_result_usage(
    accum: dict[str, float | int],
    result: Optional[dict],
) -> dict[str, float | int]:
    """Merge usage fields from a worker result dict into accum."""
    if not result:
        return accum
    accum["input_tokens"] = int(accum.get("input_tokens", 0)) + int(result.get("input_tokens", 0))
    accum["output_tokens"] = int(accum.get("output_tokens", 0)) + int(result.get("output_tokens", 0))
    accum["cost_usd"] = float(accum.get("cost_usd", 0.0)) + float(result.get("cost_usd", 0.0))
    accum["total_tokens"] = int(accum["input_tokens"]) + int(accum["output_tokens"])
    return accum
