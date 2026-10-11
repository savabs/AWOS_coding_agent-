#!/usr/bin/env python3
"""
cu_verify_receipts.py — independent verifier for the receipts computer-use
demo (docs/specs/computer_use_first_demo.md §Independent verifier).

Reads ONLY the end state of the folder and truth.json (written by the seeder
outside the sandbox). It never reads the agent transcript, plan or undo log,
and it imports nothing from AWOS (stdlib only).

    python scripts/cu_verify_receipts.py --truth ~/AWOS_cu_runs/r1/truth.json [--json out.json]
Exit 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_([a-z0-9]+(?:-[a-z0-9]+)*)_(\d+\.\d{2})(\.[a-z]+)$")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify(truth: dict) -> dict:
    folder = Path(truth["folder"])
    checks = {"V1": [], "V2": [], "V3": [], "V4": []}   # lists of failure messages
    alias_to_vendor = {}
    for r in truth["receipts"]:
        for a in r["aliases"]:
            alias_to_vendor[a] = r["vendor"]

    files = {}          # rel path -> sha
    dirs = set()
    for dp, dns, fns in os.walk(folder):
        for d in dns:
            dirs.add(os.path.relpath(os.path.join(dp, d), folder))
        for f in fns:
            p = Path(dp) / f
            files[os.path.relpath(p, folder)] = sha(p)
    by_hash = {}
    for rel, h in files.items():
        by_hash.setdefault(h, []).append(rel)

    # V1: each receipt at the right name, bytes unchanged
    for r in truth["receipts"]:
        locs = by_hash.get(r["sha256"], [])
        if len(locs) != 1:
            checks["V1"].append(f"{r['file']}: found {len(locs)} copies {locs}")
            continue
        rel = locs[0]
        parts = Path(rel).parts
        m = NAME_RE.match(parts[-1])
        want_month = r["date"][:7]
        if len(parts) != 2 or parts[0] != want_month:
            checks["V1"].append(f"{r['file']}: at {rel}, expected folder {want_month}/")
        if not m:
            checks["V1"].append(f"{r['file']}: name {parts[-1]!r} does not match YYYY-MM-DD_vendor_amount.ext")
            continue
        y, mo, d, vendor, amount, ext = m.groups()
        if f"{y}-{mo}-{d}" != r["date"]:
            checks["V1"].append(f"{r['file']}: date {y}-{mo}-{d} != {r['date']}")
        if vendor not in r["aliases"]:
            checks["V1"].append(f"{r['file']}: vendor {vendor!r} not in {r['aliases']}")
        if amount != r["total"]:
            checks["V1"].append(f"{r['file']}: amount {amount} != {r['total']}")
        if ext != r["ext"]:
            checks["V1"].append(f"{r['file']}: ext {ext} != {r['ext']}")

    # V2: nothing left at top level; non-receipts unchanged in place
    for n in truth["non_receipts"]:
        if files.get(n["file"]) != n["sha256"]:
            checks["V2"].append(f"non-receipt {n['file']} moved or changed")
    receipt_hashes = {r["sha256"] for r in truth["receipts"]}
    for rel, h in files.items():
        if "/" not in rel and h in receipt_hashes:
            checks["V2"].append(f"receipt left at top level: {rel}")

    # V3: no file lost or added; only YYYY-MM dirs
    known = receipt_hashes | {n["sha256"] for n in truth["non_receipts"]}
    for rel, h in files.items():
        if h not in known and rel != "summary.csv":
            checks["V3"].append(f"unexpected file {rel}")
    for h in known:
        if h not in by_hash:
            checks["V3"].append(f"file with sha {h[:10]} lost")
    want_dirs = {r["date"][:7] for r in truth["receipts"]}
    if dirs != want_dirs:
        checks["V3"].append(f"dirs {sorted(dirs)} != expected {sorted(want_dirs)}")

    # V4: summary.csv
    exp = {}
    for r in truth["receipts"]:
        c, t = exp.get(r["vendor"], (0, 0.0))
        exp[r["vendor"]] = (c + 1, round(t + float(r["total"]), 2))
    sp = folder / "summary.csv"
    if not sp.is_file():
        checks["V4"].append("summary.csv missing")
    else:
        rows = list(csv.reader(io.StringIO(sp.read_text(encoding="utf-8-sig"))))
        rows = [x for x in rows if any(c.strip() for c in x)]
        if not rows or [c.strip().lower() for c in rows[0]] != ["vendor", "count", "total"]:
            checks["V4"].append(f"bad header {rows[:1]}")
        got = {}
        for x in rows[1:]:
            if len(x) != 3:
                checks["V4"].append(f"bad row {x}")
                continue
            v = alias_to_vendor.get(x[0].strip())
            if v is None:
                checks["V4"].append(f"unknown vendor {x[0]!r}")
                continue
            if v in got:
                checks["V4"].append(f"vendor {v} appears twice")
            try:
                got[v] = (int(x[1]), float(x[2].replace("$", "")))
            except ValueError:
                checks["V4"].append(f"unparseable row {x}")
        for v, (c, t) in exp.items():
            if v not in got:
                checks["V4"].append(f"vendor {v} missing")
            elif got[v][0] != c or abs(got[v][1] - t) > 0.01:
                checks["V4"].append(f"vendor {v}: got {got[v]}, expected ({c}, {t:.2f})")

    receipts_ok = len(truth["receipts"]) - len({m.split(":")[0] for m in checks["V1"]})
    return {"pass": all(not v for v in checks.values()),
            "checks": {k: {"pass": not v, "failures": v} for k, v in checks.items()},
            "receipts_correct": receipts_ok, "receipts_total": len(truth["receipts"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    truth = json.loads(Path(a.truth).expanduser().read_text())
    res = verify(truth)
    if a.json:
        Path(a.json).expanduser().write_text(json.dumps(res, indent=1))
    print(("PASS" if res["pass"] else "FAIL")
          + f"  receipts correct {res['receipts_correct']}/{res['receipts_total']}")
    for k, v in res["checks"].items():
        print(f"  {k}: {'ok' if v['pass'] else 'FAIL'}")
        for f in v["failures"][:10]:
            print(f"     - {f}")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
