#!/usr/bin/env python3
"""
cache_report.py — prompt-cache hit ratio and $ per component for a run.

Reads one or more llm_calls.jsonl files (scaffold/agent/llm_call_log.py) and
summarises, per component bucket (one_shot / agent / acceptance / arbiter /
other) and per serving provider:

    calls, input tokens, cached tokens, cache_hit_ratio (token-weighted),
    cost_usd (as logged: OpenRouter's usage.cost, which already prices cache
    reads), est_usd (providers.estimate_cost_cached on the logged counts) and
    nocache_usd (the same calls priced with every input token fresh), so
    saved_usd = nocache_usd - est_usd.

Backfill-friendly: older lines without cached_tokens / cache_hit_ratio /
cost_est_usd / request_type are tolerated (missing counts read as 0; the
arbiter is folded into "acceptance" when request_type was not logged).
Error lines (no reply) count as calls with zero tokens.

CLI:
    python scripts/cache_report.py <file-or-dir> [...] [--json]
A directory is searched recursively for llm_calls.jsonl.
See docs/specs/prompt_caching.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scaffold" / "agent"))

BUCKETS = ("one_shot", "agent", "acceptance", "arbiter", "other")


def bucket_of(line: dict) -> str:
    """The report bucket for one log line."""
    component = str(line.get("component") or "")
    if line.get("request_type") == "acceptance_arbitration" or component == "arbiter":
        return "arbiter"
    if component in ("one_shot", "agent", "acceptance"):
        return component
    return "other"


def _num(value: Any) -> float:
    try:
        if value is None or isinstance(value, bool):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def iter_lines(paths: Iterable[Path]) -> Iterable[dict]:
    """Every JSON object line in the given files; bad lines are skipped."""
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        line = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(line, dict):
                        yield line
        except OSError:
            continue


def find_logs(targets: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for target in targets:
        p = Path(target)
        if p.is_dir():
            out.extend(sorted(p.rglob("llm_calls.jsonl")))
        elif p.exists():
            out.append(p)
    return out


def _prices(model: str, cache: dict) -> dict:
    if model not in cache:
        try:
            from providers import price_table
            cache[model] = price_table(model)
        except Exception:
            cache[model] = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
    return cache[model]


def _new() -> dict:
    return dict(calls=0, errors=0, input_tokens=0, cached_tokens=0, cache_write_tokens=0,
                output_tokens=0, cost_usd=0.0, est_usd=0.0, nocache_usd=0.0)


def summarise(lines: Iterable[dict]) -> dict:
    """{"total": row, "by_component": {bucket: row}, "by_provider": {name: row}}.

    Each row gains cache_hit_ratio = cached / input (token-weighted, None when
    no input) and saved_usd = nocache_usd - est_usd."""
    total, comps, provs = _new(), defaultdict(_new), defaultdict(_new)
    price_cache: dict = {}
    for line in lines:
        model = str(line.get("requested_model") or line.get("response_model") or "")
        n_in = int(_num(line.get("input_tokens")))
        n_out = int(_num(line.get("output_tokens")))
        cached = min(int(_num(line.get("cached_tokens"))), n_in)
        written = min(int(_num(line.get("cache_write_tokens"))), n_in - cached)
        table = _prices(model, price_cache)
        nocache = (n_in * table["input"] + n_out * table["output"]) / 1_000_000
        if line.get("cost_est_usd") is not None:
            est = _num(line.get("cost_est_usd"))
        else:
            est = ((n_in - cached - written) * table["input"] + cached * table["cache_read"]
                   + written * table["cache_write"] + n_out * table["output"]) / 1_000_000
        provider = str(line.get("provider") or "unknown")
        for row in (total, comps[bucket_of(line)], provs[provider]):
            row["calls"] += 1
            row["errors"] += 1 if line.get("error") else 0
            row["input_tokens"] += n_in
            row["cached_tokens"] += cached
            row["cache_write_tokens"] += written
            row["output_tokens"] += n_out
            row["cost_usd"] += _num(line.get("cost_usd"))
            row["est_usd"] += est
            row["nocache_usd"] += nocache
    for row in [total, *comps.values(), *provs.values()]:
        row["cache_hit_ratio"] = (round(row["cached_tokens"] / row["input_tokens"], 4)
                                  if row["input_tokens"] else None)
        row["saved_usd"] = row["nocache_usd"] - row["est_usd"]
        for key in ("cost_usd", "est_usd", "nocache_usd", "saved_usd"):
            row[key] = round(row[key], 6)
    ordered = {b: comps[b] for b in BUCKETS if b in comps}
    return {"total": total, "by_component": ordered,
            "by_provider": dict(sorted(provs.items()))}


def _fmt_row(name: str, row: dict) -> str:
    ratio = "-" if row["cache_hit_ratio"] is None else f"{row['cache_hit_ratio']:.1%}"
    return (f"{name:<14}{row['calls']:>7}{row['input_tokens']:>12}{row['cached_tokens']:>12}"
            f"{ratio:>8}{row['cost_usd']:>11.4f}{row['est_usd']:>11.4f}"
            f"{row['nocache_usd']:>11.4f}{row['saved_usd']:>10.4f}")


def render(summary: dict, n_files: Optional[int] = None) -> str:
    head = (f"{'':<14}{'calls':>7}{'input':>12}{'cached':>12}{'hit%':>8}"
            f"{'cost$':>11}{'est$':>11}{'nocache$':>11}{'saved$':>10}")
    out = []
    if n_files is not None:
        out.append(f"{n_files} log file(s)")
    out += ["by component", head]
    out += [_fmt_row(k, v) for k, v in summary["by_component"].items()]
    out.append(_fmt_row("TOTAL", summary["total"]))
    out += ["", "by provider", head]
    out += [_fmt_row(k[:14], v) for k, v in summary["by_provider"].items()]
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("targets", nargs="+", help="llm_calls.jsonl files or dirs")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)
    logs = find_logs(args.targets)
    if not logs:
        print("no llm_calls.jsonl found", file=sys.stderr)
        return 1
    summary = summarise(iter_lines(logs))
    print(json.dumps(summary, indent=2) if args.json else render(summary, len(logs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
