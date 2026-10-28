#!/usr/bin/env python3
"""
repetition_report.py — intent repetition rate per family (compiled tools, spec §11.1).

Reads the repetition meter's log (.awos/repetition/log.jsonl) and reports, for
the trailing window (default 28 days), the share of tasks whose key already
appeared earlier in the window. Two keys:

  structure  same goal template + touched-file pattern + action shape (the E7 key)
  intent     same goal template only (an upper bound)

The E7 prerequisite is an overall structure repeat rate >= 20%.

Usage:
  python scripts/repetition_report.py [--log PATH] [--days 28] [--json]
                                      [--min-n 1] [--threshold 0.20]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent.compiled.repetition import DEFAULT_LOG, read_log, repeat_rates  # noqa: E402


def build(entries: list[dict], days: float, now: float, threshold: float) -> dict:
    since = now - days * 86400.0
    window = [e for e in entries if float(e.get("ts", 0)) >= since - days * 86400.0]
    structure = repeat_rates(window, window_days=days, key="structure_key", since=since)
    intent = repeat_rates(window, window_days=days, key="intent_key", since=since)
    fams = {}
    for fam, s in structure["families"].items():
        i = intent["families"].get(fam, {})
        fams[fam] = {"n": s["n"], "structure_repeats": s["repeats"],
                     "structure_rate": round(s["rate"], 4), "distinct_shapes": s["keys"],
                     "intent_repeats": i.get("repeats", 0),
                     "intent_rate": round(i.get("rate", 0.0), 4)}
    rate = structure["overall"]["rate"]
    return {
        "window_days": days, "since": since, "now": now,
        "n": structure["overall"]["n"],
        "structure_rate": round(rate, 4),
        "intent_rate": round(intent["overall"]["rate"], 4),
        "threshold": threshold,
        "prerequisite_met": structure["overall"]["n"] > 0 and rate >= threshold,
        "families": dict(sorted(fams.items(), key=lambda kv: (-kv[1]["n"], kv[0]))),
    }


def to_text(rep: dict, min_n: int) -> str:
    lines = [f"repetition over the last {rep['window_days']:g} days: n={rep['n']}  "
             f"structure repeat rate={rep['structure_rate']:.1%}  "
             f"intent repeat rate={rep['intent_rate']:.1%}"]
    lines.append(f"E7 prerequisite (structure rate >= {rep['threshold']:.0%}): "
                 f"{'MET' if rep['prerequisite_met'] else 'NOT MET'}")
    if not rep["n"]:
        lines.append("(no entries in the window; the meter needs real task logs)")
        return "\n".join(lines)
    lines.append(f"{'family':<32} {'n':>4} {'struct':>8} {'intent':>8} {'shapes':>7}")
    for fam, f in rep["families"].items():
        if f["n"] < min_n:
            continue
        lines.append(f"{fam[:32]:<32} {f['n']:>4} {f['structure_rate']:>8.1%} "
                     f"{f['intent_rate']:>8.1%} {f['distinct_shapes']:>7}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--days", type=float, default=28.0)
    ap.add_argument("--min-n", type=int, default=1)
    ap.add_argument("--threshold", type=float, default=0.20)
    ap.add_argument("--now", type=float, default=None, help="epoch seconds (tests)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = build(read_log(a.log), a.days, a.now if a.now is not None else time.time(), a.threshold)
    print(json.dumps(rep, indent=2) if a.json else to_text(rep, a.min_n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
