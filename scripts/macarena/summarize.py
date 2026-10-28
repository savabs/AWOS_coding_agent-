"""Merge per-task MacArena results into one table (AWOS code, MIT).

    python scripts/macarena/summarize.py <dir-with-result.json-files> [--json summary.json]

Prints a markdown table (and appends it to $GITHUB_STEP_SUMMARY when set). For the noop agent
a "pass" is an evaluator false pass: the final-state check accepted an untouched VM.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path
from typing import List, Optional


def load(root: Path) -> List[dict]:
    rows = []
    for p in sorted(root.rglob("result.json")):
        try:
            rows.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return rows


def table(rows: List[dict]) -> str:
    out = ["| agent | task | family | apps | status | score | steps | wall s | pre rc | eval detail |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r.get("agent", ""), r.get("family", ""), r.get("task_id", ""))):
        ev = r.get("eval") or {}
        checks = ev.get("checks") or []
        detail = ""
        if checks:
            c = checks[0]
            detail = f"rc={c.get('rc')} out={str(c.get('output', '')).strip()[:40]!r}"
            if c.get("error"):
                detail += f" err={c['error'][:30]}"
        if r.get("missing_apps"):
            detail = "missing: " + ",".join(r["missing_apps"])
        if r.get("error"):
            detail = str(r["error"])[:60]
        out.append(f"| {r.get('agent')} | {r.get('task_id', '')[:8]} | {r.get('family', '')} | "
                   f"{','.join(r.get('apps') or [])[:30]} | {r.get('status')} | {r.get('score')} | "
                   f"{r.get('steps', 0)} | {r.get('wall_s', '')} | {(r.get('pre') or {}).get('rc', '')} | "
                   f"{detail.replace('|', '/')} |")
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[r.get("agent")][r.get("status")] += 1
    out.append("")
    for agent, c in sorted(by.items()):
        ran = c["pass"] + c["fail"] + c["eval_error"]
        line = f"**{agent}**: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items()))
        if ran:
            line += f" | pass rate {c['pass']}/{ran}"
            if agent == "noop":
                line += f" (= evaluator false-pass rate {c['pass'] / ran:.0%})"
        out.append(line)
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rows = load(a.root)
    md = f"## MacArena pilot ({len(rows)} results)\n\n" + table(rows)
    print(md)
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if s:
        with open(s, "a") as f:
            f.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
