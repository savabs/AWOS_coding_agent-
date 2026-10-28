#!/usr/bin/env python3
"""Build the Track C learning-over-use series from the R40 routine set.

R40 (scripts/routines/build_r40.py) is 48 one-job series. Six families share one
base snapshot across their four instances (f01, f03, f04, f07, f08, f09). This
script chains each such family into ONE multi-job series (job N = instance N,
same project), so a job_series run gives AWOS's persistent learning (notebook,
experience store) a chance to carry what it learned on instance 1..N-1 into N.
Job N starts from base + references of jobs 1..N-1 (job_series contract).

Optionally a same-base near-miss is appended as job 5 (--near-miss), to check
whether learning misleads on a look-alike task.

    python scripts/routines/build_learning_series.py --src ~/.awos-harness/routine_series \
        --out runs/track_c/series [--near-miss]

Pre-registration: docs/specs/ablation_learning_over_use.md. Read-only on --src.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

FAMILIES = ["f01", "f03", "f04", "f07", "f08", "f09"]
# Invalid when chained: job 2 (bucket_of -> component_bucket) already renamed the
# function the near-miss asks to alias, so its reference breaks the visible tests.
NM_EXCLUDE = {"r40_f07_nm_alias_bucket_of"}


def _files(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def _tree_diff(a: Path, b: Path) -> bool:
    return _files(a) != _files(b)


def build(src: Path, out: Path, near_miss: bool) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    built = {}
    for fam in FAMILIES:
        inst = sorted(d for d in src.glob(f"r40_{fam}_i*") if d.is_dir())
        nms = sorted(d for d in src.glob(f"r40_{fam}_nm_*") if d.is_dir())
        assert len(inst) == 4, (fam, inst)
        sdir = out / f"learn_{fam}"
        if sdir.exists():
            shutil.rmtree(sdir)
        shutil.copytree(inst[0] / "base", sdir / "base",
                        ignore=shutil.ignore_patterns("__pycache__"))
        seq = list(inst)
        if near_miss:
            seq += [nm for nm in nms if nm.name not in NM_EXCLUDE
                    and not _tree_diff(inst[0] / "base", nm / "base")]
        jobs = []
        for i, d in enumerate(seq, 1):
            if _tree_diff(inst[0] / "base", d / "base"):
                raise SystemExit(f"{d.name}: base differs from {inst[0].name}")
            (job_src,) = [j for j in (d / "jobs").iterdir() if j.is_dir()]
            slug = d.name.split("_", 3)[-1]
            dest = sdir / "jobs" / f"{i:02d}_{slug}"
            shutil.copytree(job_src, dest, ignore=shutil.ignore_patterns("__pycache__"))
            jobs.append({"job": i, "from": d.name, "near_miss": "_nm_" in d.name})
        built[sdir.name] = jobs
    (out / "learning_manifest.json").write_text(json.dumps(built, indent=2), encoding="utf-8")
    return built


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path.home() / ".awos-harness" / "routine_series")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--near-miss", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(a.src, a.out, a.near_miss), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
