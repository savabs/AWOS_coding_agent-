#!/usr/bin/env python3
"""
arbiter_replay.py — $0 offline replay of acceptance arbitration (trick T6).

Reads job_series run logs (read-only), finds every [ACCEPTANCE] arbitration
event, ties it to the run's hidden-test result, and writes a frozen pool plus a
v1 confusion summary. See docs/specs/sanitized_arbiter.md §5.

    python scripts/arbiter_replay.py LOG [LOG ...] [--out pool.json]

The logs do not record the failing tests' failure text, so the v2 toxic-test
triage cannot be replayed exactly. The pool marks each event's "toxic
candidate" proxy: every kept test failed after a visible-green edit
("check: k kept, 0 passed"), the only shape in which a byte-identical
environment/import failure on all tests is possible. That is an upper bound.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADER = re.compile(r"^#{5,} \[(\S+)\] job (\d+) \(\d+/\d+\)(?: repeat (\d+)/\d+)?: (\S+) #{5,}")
SERIES = re.compile(r"^\[job_series\] series=(\S+)")
PREFIX = re.compile(r"^\[(\S+) j(\d+)\] (?:\+\d+:\d+ )?(.*)$")
RESULT = re.compile(r"^(SOLVED|not solved|INVALID[^—]*) — hidden (\d+)/(\d+)")
CHECK = re.compile(r"\[ACCEPTANCE\] check: (\d+) kept, (\d+) passed")
ARB_REPLY = re.compile(r"\[ACCEPTANCE\] arbitration reply: (.*)$")
ARB_SUM = re.compile(r"\[ACCEPTANCE\] arbitration: (\d+) failing -> (\d+) wrong_test, "
                     r"(\d+) code_incomplete")
REPAIR = re.compile(r"\[ACCEPTANCE\] repair (passed|failed)")
KEPT = re.compile(r"\[ACCEPTANCE\] kept: (.*)$")


def parse_log(path: Path) -> list:
    events, series, block = [], "?", {}
    pending = None  # arbitration event waiting for its result line
    last_check = None
    for raw in path.read_text(errors="replace").splitlines():
        m = SERIES.match(raw)
        if m:
            series = m.group(1)
            continue
        m = HEADER.match(raw)
        if m:
            block = {"arm": m.group(1), "job": int(m.group(2)), "repeat": int(m.group(3) or 1),
                     "job_name": m.group(4), "series": series}
            pending, last_check = None, None
            continue
        m = PREFIX.match(raw)
        if not m:
            continue
        body = m.group(3)
        if (k := KEPT.search(body)):
            block["kept"] = [s.strip() for s in k.group(1).split(",")]
        elif (c := CHECK.search(body)):
            last_check = (int(c.group(1)), int(c.group(2)))
            if pending is not None and pending.get("repair") is not None:
                pending["check_after_repair"] = last_check
        elif (r := ARB_REPLY.search(body)):
            pending = dict(block, log=path.name, check_before=last_check, reply=r.group(1),
                           labels={}, repair=None)
            try:
                pending["labels"] = json.loads(r.group(1))
            except ValueError:
                pending["labels"] = {}
        elif (s := ARB_SUM.search(body)):
            if pending is None or "failing" in pending:  # older logs: no reply line
                pending = dict(block, log=path.name, check_before=last_check, reply="",
                               labels={}, repair=None)
            pending["failing"], pending["wrong"], pending["incomplete"] = map(int, s.groups())
        elif (p := REPAIR.search(body)) and pending is not None:
            pending["repair"] = p.group(1)
        elif (res := RESULT.match(body)) and pending is not None:
            pending["result"] = res.group(1).split(" (")[0].strip()
            pending["hidden"] = f"{res.group(2)}/{res.group(3)}"
            pending["solved"] = res.group(1) == "SOLVED"
            events.append(pending)
            pending = None
    return events


def classify(ev: dict) -> str:
    """v1 outcome class of one arbitration event, judged by the hidden tests."""
    if ev.get("result", "").startswith("INVALID"):
        return "invalid"
    if not ev.get("incomplete"):
        return "true_accept" if ev["solved"] else "false_accept"
    if ev.get("repair") is None:
        return "incomplete_no_repair_" + ("solved" if ev["solved"] else "unsolved")
    if ev["repair"] == "failed":  # green edit restored: the repair changed nothing
        return "false_repair" if ev["solved"] else "repair_failed_unsolved"
    return "repair_passed_solved" if ev["solved"] else "repair_passed_unsolved"


def toxic_candidate(ev: dict) -> bool:
    """All kept tests failed after the green edit (proxy upper bound for v2 triage)."""
    chk = ev.get("check_before")
    return bool(chk) and chk[1] == 0 and chk[0] == ev.get("failing", -1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    pool = []
    for p in args.logs:
        pool.extend(parse_log(Path(p)))
    for ev in pool:
        ev["v1_class"] = classify(ev)
        ev["toxic_candidate"] = toxic_candidate(ev)
    if args.out:
        Path(args.out).write_text(json.dumps(pool, indent=1) + "\n")
    by: dict = {}
    for ev in pool:
        key = (ev["log"], ev["v1_class"])
        by[key] = by.get(key, 0) + 1
    print(f"{len(pool)} arbitration events")
    for (log, cls), n in sorted(by.items()):
        print(f"  {log:16s} {cls:28s} {n}")
    tox = [ev for ev in pool if ev["toxic_candidate"]]
    print(f"toxic candidates (all kept failing after green edit): {len(tox)}")
    for ev in tox:
        print(f"  {ev['log']} {ev['series']} j{ev['job']:02d} r{ev['repeat']} "
              f"{ev['v1_class']} hidden {ev['hidden']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
