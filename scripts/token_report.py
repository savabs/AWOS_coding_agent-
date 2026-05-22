#!/usr/bin/env python3
"""
token_report.py — Parse .aider.llm.history and report token usage + cost.
Usage: python scripts/token_report.py [--history FILE]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# API pricing per million tokens — keep in sync with memories/repo/models_pricing_catalog.md
# Sources: docs.anthropic.com pricing, ai.google.dev/gemini-api/docs/pricing, api-docs.deepseek.com/quick_start/pricing
PRICING = {
    # Anthropic Claude (prompt caching supported; 5m cache write column)
    "claude-sonnet-4-5":  {"input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00},
    "claude-haiku-3-5":   {"input": 0.80, "cache_write": 1.00, "cache_read": 0.08, "output": 4.00},
    "claude-haiku-4-5":   {"input": 1.00, "cache_write": 1.25, "cache_read": 0.10, "output": 5.00},
    "claude-opus-4-5":    {"input": 5.00, "cache_write": 6.25, "cache_read": 0.50, "output": 25.00},
    # Google Gemini (free tier available; cache_read via context caching API)
    "gemini-2.5-pro":     {"input": 1.25,  "cache_write": 1.25,  "cache_read": 0.125, "output": 10.00},
    "gemini-2.5-flash":   {"input": 0.30,  "cache_write": 0.30,  "cache_read": 0.03,  "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "cache_write": 0.10, "cache_read": 0.01, "output": 0.40},
    # DeepSeek (server-side cache automatic; cache_read = 1/10 of miss price)
    "deepseek-chat":      {"input": 0.14,  "cache_write": 0.14,  "cache_read": 0.0028, "output": 0.28},
    "deepseek-reasoner":  {"input": 0.14,  "cache_write": 0.14,  "cache_read": 0.0028, "output": 0.28},
}
DEFAULT_MODEL = "claude-sonnet-4-5"


def parse_history(path: Path) -> list[dict]:
    """Extract usage blocks from .aider.llm.history JSON-lines or mixed format."""
    records = []
    text = path.read_text(encoding="utf-8")

    # Each request/response pair in aider history is separated by "---"
    # Usage appears in response JSON blocks
    # Pattern: find JSON objects containing "usage" key
    json_pattern = re.compile(r'\{[^{}]*"usage"[^{}]*\}', re.DOTALL)

    for match in json_pattern.finditer(text):
        try:
            obj = json.loads(match.group())
            usage = obj.get("usage", {})
            if usage and ("input_tokens" in usage or "output_tokens" in usage):
                records.append({
                    "model": obj.get("model", DEFAULT_MODEL),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                })
        except (json.JSONDecodeError, KeyError):
            continue

    return records


def calc_cost(record: dict) -> float:
    model_key = next((k for k in PRICING if record["model"].startswith(k)), DEFAULT_MODEL)
    p = PRICING[model_key]
    mtok = 1_000_000
    cost = (
        record["input_tokens"] * p["input"] / mtok
        + record["cache_creation_input_tokens"] * p["cache_write"] / mtok
        + record["cache_read_input_tokens"] * p["cache_read"] / mtok
        + record["output_tokens"] * p["output"] / mtok
    )
    return cost


def cache_hit_rate(records: list[dict]) -> float:
    total_cacheable = sum(
        r["cache_creation_input_tokens"] + r["cache_read_input_tokens"] for r in records
    )
    total_reads = sum(r["cache_read_input_tokens"] for r in records)
    return (total_reads / total_cacheable * 100) if total_cacheable > 0 else 0.0


def main():
    parser = argparse.ArgumentParser(description="Token usage report from Aider LLM history")
    parser.add_argument("--history", default=".aider.llm.history", help="Path to LLM history file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-request breakdown")
    args = parser.parse_args()

    history_path = Path(args.history)
    if not history_path.exists():
        print(f"No history found at {history_path}")
        print("Run aider with --llm-history-file set (already in .aider.conf.yml)")
        sys.exit(0)

    records = parse_history(history_path)
    if not records:
        print(f"No usage records found in {history_path}")
        sys.exit(0)

    print(f"\n{'─'*70}")
    print(f"  Token Report — {history_path}  ({len(records)} requests)")
    print(f"{'─'*70}")

    if args.verbose:
        print(f"\n{'#':>4}  {'Model':<22}  {'Input':>7}  {'CacheW':>7}  {'CacheR':>7}  {'Output':>7}  {'Cost':>8}")
        print(f"{'─'*4}  {'─'*22}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*8}")
        for i, r in enumerate(records, 1):
            cost = calc_cost(r)
            model_short = r["model"].replace("claude-", "")[:22]
            print(f"{i:>4}  {model_short:<22}  {r['input_tokens']:>7}  "
                  f"{r['cache_creation_input_tokens']:>7}  {r['cache_read_input_tokens']:>7}  "
                  f"{r['output_tokens']:>7}  ${cost:>7.5f}")
        print()

    # Totals
    totals = {
        "input_tokens": sum(r["input_tokens"] for r in records),
        "cache_creation_input_tokens": sum(r["cache_creation_input_tokens"] for r in records),
        "cache_read_input_tokens": sum(r["cache_read_input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "model": records[-1]["model"] if records else DEFAULT_MODEL,
    }
    total_cost = sum(calc_cost(r) for r in records)
    hit_rate = cache_hit_rate(records)

    total_input_all = totals["input_tokens"] + totals["cache_creation_input_tokens"] + totals["cache_read_input_tokens"]

    print(f"  Total requests:       {len(records):>10}")
    print(f"  Input tokens (fresh): {totals['input_tokens']:>10,}")
    print(f"  Cache writes:         {totals['cache_creation_input_tokens']:>10,}  (125% cost)")
    print(f"  Cache reads:          {totals['cache_read_input_tokens']:>10,}  (10% cost) ✓")
    print(f"  Output tokens:        {totals['output_tokens']:>10,}  (500% cost)")
    print(f"  Total input tokens:   {total_input_all:>10,}")
    print(f"  Cache hit rate:       {hit_rate:>9.1f}%  (target: >70%)")
    print(f"  {'─'*40}")
    print(f"  Estimated cost:       ${total_cost:>9.5f}")

    # Cost without caching (what you'd pay with no cache)
    model_key = next((k for k in PRICING if totals["model"].startswith(k)), DEFAULT_MODEL)
    p = PRICING[model_key]
    cost_no_cache = (total_input_all * p["input"] + totals["output_tokens"] * p["output"]) / 1_000_000
    savings = cost_no_cache - total_cost
    if savings > 0:
        pct = savings / cost_no_cache * 100
        print(f"  Cost without cache:   ${cost_no_cache:>9.5f}")
        print(f"  Cache savings:        ${savings:>9.5f}  ({pct:.0f}%)")

    print(f"{'─'*70}\n")


if __name__ == "__main__":
    main()
