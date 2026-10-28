#!/usr/bin/env python3
"""
compare_baseline.py — Diff latest PEI benchmark run against frozen baseline v1.

Usage:
    python3 scripts/compare_baseline.py
    python3 scripts/compare_baseline.py --baseline docs/benchmarks/baseline_v1_2026-06-25.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "docs" / "benchmarks" / "baseline_v1_2026-06-25.json"
BENCH_DIR = ROOT / ".awos" / "benchmarks"


def _latest_pei_json() -> Path | None:
    files = sorted(BENCH_DIR.glob("benchmark_vs_raw_*.json"), reverse=True)
    return files[0] if files else None


def _pct_delta(new: float, old: float) -> str:
    if old == 0:
        return "n/a"
    d = (new - old) / old * 100
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare latest PEI run to frozen baseline")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Frozen baseline JSON path",
    )
    args = parser.parse_args()

    if not args.baseline.exists():
        print(f"Baseline not found: {args.baseline}", file=sys.stderr)
        raise SystemExit(1)

    latest = _latest_pei_json()
    if not latest:
        print("No PEI runs in .awos/benchmarks/ — run: python3 scripts/benchmark_vs_raw_api.py")
        raise SystemExit(1)

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    current = json.loads(latest.read_text(encoding="utf-8"))

    b_pei = baseline["suites"]["pei_bug_fix"]
    c_sum = current.get("summary", {})

    print("=" * 60)
    print("BASELINE COMPARISON — PEI bug-fix suite")
    print("=" * 60)
    print(f"Frozen:  {args.baseline.name} ({baseline.get('frozen_at', '?')})")
    print(f"Current: {latest.name} ({current.get('timestamp', '?')})")
    print(f"Git now: ", end="")
    import subprocess
    print(subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).decode().strip())
    print()

    rows = [
        ("AWOS pass rate", b_pei["awos"]["pass_rate"], c_sum.get("awos_pass_rate", 0)),
        ("Raw pass rate", b_pei["raw"]["pass_rate"], c_sum.get("raw_pass_rate", 0)),
        ("AWOS total cost", b_pei["awos"]["total_cost_usd"], c_sum.get("awos_total_cost", 0)),
        ("Raw total cost", b_pei["raw"]["total_cost_usd"], c_sum.get("raw_total_cost", 0)),
        ("AWOS verified/$", b_pei["awos"]["verified_per_dollar"], c_sum.get("awos_verified_per_dollar", 0)),
        ("Raw verified/$", b_pei["raw"]["verified_per_dollar"], c_sum.get("raw_verified_per_dollar", 0)),
    ]

    print(f"{'Metric':<22} {'Baseline':>12} {'Current':>12} {'Delta':>10}")
    print("-" * 60)
    for name, old, new in rows:
        print(f"{name:<22} {old:>12.4g} {new:>12.4g} {_pct_delta(float(new), float(old)):>10}")

    print()
    print("Per-case (current run):")
    for case in current.get("cases", []):
        cid = case["case_id"]
        raw_ok = "✓" if case["raw"]["tests_pass"] else "✗"
        awos_ok = "✓" if case["awos"]["tests_pass"] else "✗"
        win = "AWOS wins" if case.get("awos_wins") else "tie/raw"
        print(f"  {cid}: raw={raw_ok}  awos={awos_ok}  ({win})")

    print()
    print("Mission suites (frozen in baseline only — re-run manually to compare):")
    for key in ("long_mission_awos", "phase_d_clawcode"):
        s = baseline["suites"].get(key, {})
        print(f"  {key}: {s.get('tasks_completed', '?')}/{s.get('tasks_planned', '?')} tasks, "
              f"{s.get('wall_sec', '?')}s, ${s.get('cost_usd', '?')}")

    print()
    gaps = baseline.get("known_gaps", [])
    if gaps:
        print("Baseline v1 known gaps (unchanged until v2):")
        for g in gaps[:3]:
            print(f"  - {g}")
        if len(gaps) > 3:
            print(f"  ... +{len(gaps) - 3} more in {args.baseline.name}")


if __name__ == "__main__":
    main()
