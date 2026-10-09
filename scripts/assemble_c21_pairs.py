#!/usr/bin/env python3
"""Assemble the C2.1 confirmation run and its controls into one combined file.

    python scripts/assemble_c21_pairs.py <awos_dir> <out.json>

Same row selection as the C2.1 scoring script: arm `c21` = valid, non-partial
rows of job_series_*.json files named at or after job_series_20261008T2201
(backupd + 7 real-issue targets); arm `ctrl` = backupd `exp0` rows of
job_series_ablationB_combined.json plus the 7 targets' `acc2` rows of
job_series_ablationC2_combined.json. Tasks are paired by task id, not by run
time, so the controls are not same-run controls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SINCE = "job_series_20261008T2201"
TARGETS = ["real_more-itertools_1304", "real_parse_249", "real_pyparsing_647", "real_tabulate_176",
           "real_toolz_634", "real_toolz_635", "real_boltons_458"]
KEEP = ("solved", "billed_usd", "cost_usd", "turns", "invalid", "invalid_reason", "minutes")


def _row(arm: str, task: str, rep: int, r: dict) -> dict:
    out = {k: r.get(k) for k in KEEP}
    out.update(arm=arm, id=task, repeat=rep)
    return out


def assemble(awos: Path) -> dict:
    rows: list[dict] = []
    for f in sorted(awos.glob("job_series_2026*.json")):
        if f.name < SINCE or f.name.endswith("_revalidated.json"):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("partial"):
            continue
        for r in d.get("results") or []:
            if d["series"] == "backupd":
                rows.append(_row("c21", f"backupd_{r['job']:02d}", int(r.get("repeat") or 1), r))
            elif d["series"] in TARGETS:
                rows.append(_row("c21", d["series"], int(r.get("repeat") or 1), r))
    b = json.loads((awos / "job_series_ablationB_combined.json").read_text(encoding="utf-8"))
    for r in b["results"]:
        if r["arm"] == "exp0":
            rows.append(_row("ctrl", f"backupd_{r['job']:02d}", int(r.get("repeat") or 1), r))
    c2 = json.loads((awos / "job_series_ablationC2_combined.json").read_text(encoding="utf-8"))
    for r in c2["results"]:
        if r["arm"] == "acc2" and r.get("id") in TARGETS:
            rows.append(_row("ctrl", r["id"], int(r.get("repeat") or 1), r))
    ids = sorted({r["id"] for r in rows})
    for r in rows:
        r["job"] = ids.index(r["id"]) + 1
    return {"series": "c21_vs_controls", "arms": ["c21", "ctrl"], "repeat": 2,
            "sources": "scripts/assemble_c21_pairs.py", "results": rows}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    data = assemble(Path(argv[0]))
    Path(argv[1]).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    n = {a: sum(1 for r in data["results"] if r["arm"] == a and not r.get("invalid")) for a in data["arms"]}
    s = {a: sum(1 for r in data["results"] if r["arm"] == a and not r.get("invalid") and r["solved"])
         for a in data["arms"]}
    print(f"[assemble_c21] wrote {argv[1]}: " + ", ".join(f"{a} {s[a]}/{n[a]}" for a in data["arms"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
