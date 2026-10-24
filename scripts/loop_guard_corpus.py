#!/usr/bin/env python3
"""
loop_guard_corpus.py — false-positive check for the loop breaker on real replies.

AWOS keeps only 600 chars of each one-shot reply, so the corpus is the
Aider arm's run logs from past job series: the same model (DeepSeek V4
Flash), the same SEARCH/REPLACE edit format, the full reply text (rich wraps
prose at 80 columns; code is unchanged). Each job's section of a run.log is
one sample; solved/unsolved comes from the series' job_series_<ts>.json.

Read-only. Usage:
    python scripts/loop_guard_corpus.py [--root <dir with job_series_*.json>] [-v]
Env AWOS_LOOP_CORPUS_ROOT overrides the default root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scaffold" / "agent"))

import loop_guard  # noqa: E402

DEFAULT_ROOT = Path("/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos")
_SECTION = re.compile(r"^===== \[aider j(\d+)\]", re.M)
_PREFIX = re.compile(r"^\+\d+:\d+ ?", re.M)
# Harness / aider chrome that is not model text.
_CHROME = re.compile(r"^(Added .* to the chat\.|Tokens: |Cost: |Applied edit to |Commit |"
                     r"\[harness_aider\]|Aider v|Model: |Git repo: |Repo-map: |"
                     r"Update git |Warning: |https://aider|Only \d+ reflections|"
                     r"Did you mean|Are you sure|# \d+ SEARCH/REPLACE block)")


def _sections(log: str) -> dict[int, str]:
    out: dict[int, str] = {}
    marks = list(_SECTION.finditer(log))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(log)
        body = _PREFIX.sub("", log[m.end():end])
        body = "\n".join(l for l in body.splitlines() if not _CHROME.match(l))
        out[int(m.group(1))] = body
    return out


def _log_for(run_dir: Path, repeat: int) -> Path | None:
    for cand in (run_dir / f"r{repeat}" / "aider" / "run.log", run_dir / "aider" / "run.log"):
        if cand.is_file():
            return cand
    return None


def measure(root: Path | None = None, verbose: bool = False) -> dict:
    root = Path(os.environ.get("AWOS_LOOP_CORPUS_ROOT") or root or DEFAULT_ROOT)
    stats = {"solved_runs": 0, "solved_trips": 0, "unsolved_runs": 0, "unsolved_trips": 0,
             "trips": []}
    if not root.is_dir():
        stats["solved_trip_rate"] = 0.0
        return stats
    seen: set = set()
    for series in sorted(root.glob("job_series_*.json")):
        if "revalidated" in series.name:
            continue
        try:
            data = json.loads(series.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        run_dir = root / "job_series" / series.stem.replace("job_series_", "")
        cache: dict = {}
        for r in data.get("results") or []:
            if r.get("arm") != "aider" or r.get("invalid"):
                continue
            rep = int(r.get("repeat") or 1)
            log = _log_for(run_dir, rep)
            if log is None:
                continue
            key = (str(log), int(r.get("job") or 0))
            if key in seen:
                continue
            seen.add(key)
            if str(log) not in cache:
                cache[str(log)] = _sections(log.read_text(encoding="utf-8", errors="replace"))
            text = cache[str(log)].get(int(r.get("job") or 0))
            if not text or "SEARCH" not in text:
                continue
            trip = loop_guard.detect(text)
            kind = "solved" if r.get("solved") else "unsolved"
            stats[f"{kind}_runs"] += 1
            if trip:
                stats[f"{kind}_trips"] += 1
                ctx = text[max(0, trip.cut - 200): trip.cut + 200]
                stats["trips"].append({"series": series.stem, "job": r.get("job"),
                                       "solved": bool(r.get("solved")), "kind": trip.kind,
                                       "at": trip.at, "chars": len(text), "context": ctx})
    n = stats["solved_runs"]
    stats["solved_trip_rate"] = stats["solved_trips"] / n if n else 0.0
    u = stats["unsolved_runs"]
    stats["unsolved_trip_rate"] = stats["unsolved_trips"] / u if u else 0.0
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    s = measure(Path(a.root) if a.root else None)
    print(f"solved runs: {s['solved_runs']}, trips: {s['solved_trips']} "
          f"({100 * s['solved_trip_rate']:.1f}%)")
    print(f"unsolved runs: {s['unsolved_runs']}, trips: {s['unsolved_trips']} "
          f"({100 * s.get('unsolved_trip_rate', 0):.1f}%)")
    for t in s["trips"]:
        print(f"- {t['series']} j{t['job']} solved={t['solved']} {t['kind']} at {t['at']}/{t['chars']}")
        if a.v:
            print("    " + t["context"].replace("\n", "\n    "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
