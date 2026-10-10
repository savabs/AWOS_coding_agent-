#!/usr/bin/env python3
"""build_r40.py — build the R40 routine-task set as job series.

Design: docs/specs/compiled_tools_design.md §11.2 (R40) and
docs/research/local_first_architecture_2026-10.md X4 ("routine-task set").

10 families x 4 parameterised instances + 8 near-misses = 48 tasks, each drawn
from the owner's development workflow on this repo (run a test target and
report, bump version + CHANGELOG, add a CLI flag, add a dataclass field, fix one
lint class, regenerate a markdown table, rename a function, add an env var, write
a unit test, fill a spec section). Near-misses are worded like a family but need
a different action; the family's routine must not solve them.

One series per task (r40_<fam>_<i1..i4|nm>_<slug>): job_series materialises job N
from base + the references of jobs 1..N-1, so instances in one series would
leak each other's solutions and could not be run, shuffled or subset alone. One
job per series matches ~/.awos-harness/real_series and keeps each base tiny.

    python scripts/routines/build_r40.py                 # build into ~/.awos-harness/routine_series
    python scripts/routines/build_r40.py --validate      # build, then job_series validate each
    python scripts/routines/build_r40.py --out DIR --only f03 --no-manifest

No model calls. Writes only under --out (r40_* dirs, manifest.json,
validation.json) and, unless --no-manifest, scripts/routines/r40_manifest.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import r40_families_a
import r40_families_b
from r40_common import REPO, Task

DEFAULT_OUT = Path.home() / ".awos-harness" / "routine_series"
BRANCH_MANIFEST = HERE / "r40_manifest.json"
FAMILY_KIND = {  # Tool = fixed params fill a fixed procedure; Routine = needs model-filled edits
    "f01_run_tests_report": "Tool", "f02_bump_version": "Tool", "f03_add_cli_flag": "Routine",
    "f04_dataclass_field": "Routine", "f05_fix_lint_class": "Tool", "f06_md_table_from_json": "Tool",
    "f07_rename_function": "Routine", "f08_config_env_var": "Routine",
    "f09_unit_test_pure_fn": "Routine", "f10_spec_section_from_json": "Tool",
}


def all_tasks() -> list[Task]:
    tasks: list[Task] = []
    for make in r40_families_a.FAMILIES + r40_families_b.FAMILIES:
        tasks.extend(make())
    names = [t.series for t in tasks]
    assert len(names) == len(set(names)), "duplicate series names"
    return tasks


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def write_series(task: Task, out: Path) -> Path:
    sdir = out / task.series
    if sdir.exists():
        shutil.rmtree(sdir)
    _write(sdir / "base", task.base)
    job = sdir / "jobs" / f"01_{task.slug}"
    spec = {"id": f"01_{task.slug}", "goal": task.goal, "max_turns": task.max_turns,
            "max_cost_usd": task.max_cost_usd, "timeout_min": task.timeout_min}
    _write(job, {"task.json": json.dumps(spec, indent=2, ensure_ascii=False) + "\n"})
    _write(job / "hidden_tests", task.hidden)
    _write(job / "reference", task.reference)
    label = "near-miss" if task.near_miss else f"instance {task.instance}/4"
    source = [f"# {task.series}", "",
              f"- R40 family: {task.family} ({FAMILY_KIND[task.family]}) — {label}",
              "- Built by scripts/routines/build_r40.py; spec: docs/specs/compiled_tools_design.md §11.2",
              f"- Hidden check: {task.check}",
              f"- Parameters (held out): `{json.dumps(task.params, ensure_ascii=False)}`"]
    if task.near_miss:
        source.append(f"- Near-miss: {task.near_miss_reason}. The family routine must refuse / fall through.")
    if task.sources:
        source.append(f"- Snapshot taken from: {', '.join(task.sources)}")
    source += ["- Synthetic workload (routine tasks shaped on this repo's history), not real issues.",
               f"- Write set (reference): {', '.join(sorted(task.reference))}", ""]
    (sdir / "SOURCE.md").write_text("\n".join(source), encoding="utf-8")
    return sdir


def manifest_entry(task: Task) -> dict:
    return {
        "series": task.series, "family": task.family, "kind": FAMILY_KIND[task.family],
        "instance": task.instance, "near_miss": task.near_miss,
        "near_miss_reason": task.near_miss_reason or None,
        "label": "near_miss" if task.near_miss else "instance",
        "goal": task.goal, "params": task.params, "check": task.check,
        "write_set": sorted(task.reference), "base_files": len(task.base),
        "sources": task.sources,
        "content_sha": _sha(json.dumps([task.base, task.hidden, task.reference, task.goal],
                                       sort_keys=True)),
    }


def build_manifest(tasks: list[Task]) -> dict:
    fams = sorted({t.family for t in tasks})
    return {
        "name": "R40 routine-task set",
        "spec": "docs/specs/compiled_tools_design.md#11.2; docs/research/local_first_architecture_2026-10.md X4",
        "workload": "synthetic (routine tasks from this repo's workflow); report as such",
        "layout": "one job-series per task; run with scripts/job_series.py --series-root <out>",
        "counts": {"tasks": len(tasks), "families": len(fams),
                   "instances": sum(not t.near_miss for t in tasks),
                   "near_misses": sum(t.near_miss for t in tasks)},
        "families": {f: {"kind": FAMILY_KIND[f],
                         "instances": [t.series for t in tasks if t.family == f and not t.near_miss],
                         "near_misses": [t.series for t in tasks if t.family == f and t.near_miss]}
                     for f in fams},
        "tasks": [manifest_entry(t) for t in tasks],
    }


def validate_one(series: str, out: Path) -> dict:
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "job_series.py"), "validate",
                           "--series", series, "--series-root", str(out)],
                          cwd=REPO, capture_output=True, text=True, timeout=900)
    return {"series": series, "ok": proc.returncode == 0,
            "output": (proc.stdout + proc.stderr).strip()[-1500:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="series root to write (default %(default)s)")
    ap.add_argument("--only", default=None, help="build only series whose name contains this")
    ap.add_argument("--validate", action="store_true", help="run job_series validate on each built series")
    ap.add_argument("--jobs", type=int, default=6, help="parallel validations (default 6)")
    ap.add_argument("--no-manifest", action="store_true",
                    help="don't rewrite scripts/routines/r40_manifest.json")
    args = ap.parse_args(argv)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    tasks = all_tasks()
    chosen = [t for t in tasks if not args.only or args.only in t.series]
    for t in chosen:
        write_series(t, out)
    manifest = build_manifest(tasks)
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if not args.only:
        (out / "manifest.json").write_text(text, encoding="utf-8")
        if not args.no_manifest:
            BRANCH_MANIFEST.write_text(text, encoding="utf-8")
    c = manifest["counts"]
    print(f"[r40] wrote {len(chosen)} series to {out} "
          f"({c['families']} families, {c['instances']} instances, {c['near_misses']} near-misses)")
    if not args.validate:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(lambda t: validate_one(t.series, out), chosen))
    for r in results:
        print(f"  {'ok     ' if r['ok'] else 'INVALID'} {r['series']}")
        if not r["ok"]:
            print("    " + r["output"].replace("\n", "\n    "))
    bad = [r["series"] for r in results if not r["ok"]]
    if not args.only:
        (out / "validation.json").write_text(json.dumps(
            {"ok": not bad, "valid": len(results) - len(bad), "total": len(results),
             "invalid": bad, "results": results}, indent=2) + "\n", encoding="utf-8")
    print(f"[r40] {len(results) - len(bad)}/{len(results)} series valid")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
