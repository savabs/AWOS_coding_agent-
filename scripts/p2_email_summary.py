#!/usr/bin/env python3
"""Summarise p2-email workflow artifacts: one row per persona x seed x arm, then
mean +- sd per persona x arm. Prints Markdown; writes <dir>/summary.json.

    python3 scripts/p2_email_summary.py artifacts/
"""
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def usage_totals(run_dir: Path):
    calls = toks = 0
    for ph in ("calibration", "evaluation"):
        f = run_dir / "agent" / f"usage_{ph}.json"
        if f.exists():
            u = json.loads(f.read_text())
            calls += int(u.get("calls", 0))
            toks += int(u.get("prompt_tokens", 0))
    return calls, toks


def main(root: str) -> int:
    rows = []
    for res in sorted(Path(root).rglob("results.json")):
        run_dir = res.parent
        name = run_dir.name  # <persona>_s<seed>_<arm>
        m = re.match(r"^(.+)_s(\d+)_(on_nounc|on|off)$", name)
        persona, seed, arm = m.groups() if m else (name, "?", "?")
        r = json.loads(res.read_text())
        e = r["phases"]["evaluation"]
        calls, toks = usage_totals(run_dir)
        rows.append({
            "persona": persona, "seed": seed, "arm": arm,
            "urgP": e["urgent"]["precision"], "urgR": e["urgent"]["recall"],
            "composite": e["composite"], "category": e["category_accuracy"],
            "inj": f'{e["injection"]["handled"]}/{e["injection"]["n"]}',
            "scoping": (r.get("scoping_case") or {}).get("passed"),
            "useful": r.get("useful"), "violations": (r.get("safety") or {}).get("violations"),
            "calls": calls, "prompt_tokens": toks,
        })
    if not rows:
        print("no results found")
        return 1
    print("## p2-email runs\n")
    print("| persona | seed | arm | urgent P/R | composite | category | inj | scoping | useful | calls | prompt tok |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for x in rows:
        print(f'| {x["persona"]} | {x["seed"]} | {x["arm"]} | {x["urgP"]:.2f}/{x["urgR"]:.2f} | '
              f'{x["composite"]:.1f} | {x["category"]:.2f} | {x["inj"]} | {x["scoping"]} | {x["useful"]} | '
              f'{x["calls"]} | {x["prompt_tokens"]} |')
    agg = defaultdict(list)
    for x in rows:
        agg[(x["persona"], x["arm"])].append(x)
    print("\n## mean (sd) per persona x arm\n")
    print("| persona | arm | n | urgent P | urgent R | composite | useful | calls | prompt tok |")
    print("|---|---|---|---|---|---|---|---|---|")

    def ms(v):
        return f"{statistics.mean(v):.2f} ({statistics.pstdev(v):.2f})" if v else "-"
    for (p, a), xs in sorted(agg.items()):
        print(f'| {p} | {a} | {len(xs)} | {ms([x["urgP"] for x in xs])} | {ms([x["urgR"] for x in xs])} | '
              f'{ms([x["composite"] for x in xs])} | {sum(bool(x["useful"]) for x in xs)}/{len(xs)} | '
              f'{statistics.mean(x["calls"] for x in xs):.1f} | {statistics.mean(x["prompt_tokens"] for x in xs):.0f} |')
    (Path(root) / "summary.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "artifacts"))
