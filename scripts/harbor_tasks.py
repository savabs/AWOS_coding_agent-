#!/usr/bin/env python3
"""Task lists for the Harbor head-to-head (ci/HARBOR.md, .github/workflows/harbor.yml).

  metadata    read a downloaded dataset (`harbor dataset download
              terminal-bench/terminal-bench-2-1@6 -o DIR`) and write the per-task
              metadata the other commands use: ci/harbor_tb21_tasks.json
              (name, category, difficulty, timeouts, resources). Committed, so
              selection needs no network.
  sample      write ci/harbor_tasks_sample.txt: the 10 tasks of Harbor's
              `terminal-bench-sample@2.0` registry dataset, run from TB 2.1
  stratified  deterministic seeded sample of N tasks, stratified by difficulty
              (proportional, largest remainder) and spread over categories
              inside each difficulty; writes ci/harbor_tasks_<seed>.txt

A task file holds one Harbor task name per line (`terminal-bench/<task>`,
matched by `harbor run -i`); `#` lines are comments.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI = REPO / "ci"
METADATA = CI / "harbor_tb21_tasks.json"
DATASET = "terminal-bench/terminal-bench-2-1@6"
ORG = "terminal-bench"
#: `harbor dataset download terminal-bench-sample@2.0` (Harbor registry.json,
#: checked 2026-10-15): the 10-task TB sample. All 10 exist in TB 2.1.
SAMPLE_TASKS = (
    "build-cython-ext", "chess-best-move", "configure-git-webserver",
    "fix-code-vulnerability", "log-summary-date-ranges", "polyglot-c-py",
    "qemu-alpine-ssh", "qemu-startup", "regex-log", "sqlite-with-gcov",
)


def full_name(task: str) -> str:
    return task if "/" in task else f"{ORG}/{task}"


def read_metadata(path: Path = METADATA) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["tasks"]


def build_metadata(dataset_dir: Path) -> list[dict]:
    rows = []
    for toml_path in sorted(dataset_dir.glob("*/task.toml")):
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        meta, env = data.get("metadata", {}), data.get("environment", {})
        rows.append({
            "name": full_name(toml_path.parent.name),
            "category": meta.get("category") or "unknown",
            "difficulty": meta.get("difficulty") or "unknown",
            "agent_timeout_sec": (data.get("agent") or {}).get("timeout_sec"),
            "verifier_timeout_sec": (data.get("verifier") or {}).get("timeout_sec"),
            "cpus": env.get("cpus"), "memory_mb": env.get("memory_mb"),
            "storage_mb": env.get("storage_mb"), "gpus": env.get("gpus"),
            "allow_internet": env.get("allow_internet"),
            "docker_image": env.get("docker_image"),
        })
    return rows


def stratified(tasks: list[dict], seed: int, count: int) -> list[str]:
    """`count` task names: per difficulty a share proportional to its size
    (largest remainder, ties broken by the seeded order), and inside each
    difficulty a round robin over its categories (both orders seeded)."""
    if not 0 < count <= len(tasks):
        raise ValueError(f"count must be in 1..{len(tasks)}")
    rng = random.Random(seed)
    by_diff: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(tasks, key=lambda t: t["name"]):
        by_diff[t["difficulty"]].append(t)
    diffs = sorted(by_diff)
    quota = {d: count * len(by_diff[d]) / len(tasks) for d in diffs}
    alloc = {d: int(quota[d]) for d in diffs}
    tiebreak = {d: rng.random() for d in diffs}
    rest = count - sum(alloc.values())
    for d in sorted(diffs, key=lambda d: (-(quota[d] - alloc[d]), tiebreak[d]))[:rest]:
        alloc[d] += 1
    picked: list[str] = []
    for d in diffs:
        by_cat: dict[str, list[str]] = defaultdict(list)
        for t in by_diff[d]:
            by_cat[t["category"]].append(t["name"])
        cats = sorted(by_cat)
        rng.shuffle(cats)
        for c in cats:
            rng.shuffle(by_cat[c])
        chosen: list[str] = []
        while len(chosen) < alloc[d]:
            for c in cats:
                if by_cat[c] and len(chosen) < alloc[d]:
                    chosen.append(by_cat[c].pop())
        picked.extend(chosen)
    return sorted(picked)


def write_task_file(path: Path, names: list[str], header: list[str]) -> None:
    lines = [f"# {h}" for h in header] + list(names)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_task_file(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(full_name(line))
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: duplicate task names")
    return names


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("metadata")
    m.add_argument("--dataset-dir", required=True,
                   help="the dir holding <task>/task.toml (…/terminal-bench-2-1)")
    m.add_argument("--out", default=str(METADATA))
    s = sub.add_parser("sample")
    s.add_argument("--out", default=str(CI / "harbor_tasks_sample.txt"))
    st = sub.add_parser("stratified")
    st.add_argument("--seed", type=int, required=True)
    st.add_argument("--count", type=int, required=True)
    st.add_argument("--metadata", default=str(METADATA))
    st.add_argument("--out", default=None, help="default ci/harbor_tasks_<seed>.txt")
    a = p.parse_args(argv)
    if a.cmd == "metadata":
        rows = build_metadata(Path(a.dataset_dir))
        if not rows:
            print(f"no */task.toml under {a.dataset_dir}", file=sys.stderr)
            return 2
        Path(a.out).write_text(json.dumps({"dataset": DATASET, "tasks": rows}, indent=1) + "\n",
                               encoding="utf-8")
        print(f"[harbor_tasks] {len(rows)} task(s) -> {a.out}")
        return 0
    if a.cmd == "sample":
        write_task_file(Path(a.out), [full_name(t) for t in SAMPLE_TASKS],
                        [f"Harbor terminal-bench-sample@2.0 (10 tasks), run from {DATASET}.",
                         "Written by scripts/harbor_tasks.py sample."])
        print(f"[harbor_tasks] {len(SAMPLE_TASKS)} task(s) -> {a.out}")
        return 0
    tasks = read_metadata(Path(a.metadata))
    names = stratified(tasks, a.seed, a.count)
    out = Path(a.out) if a.out else CI / f"harbor_tasks_{a.seed}.txt"
    meta = {t["name"]: t for t in tasks}
    mix: dict[str, int] = defaultdict(int)
    for n in names:
        mix[meta[n]["difficulty"]] += 1
    write_task_file(out, names, [
        f"{len(names)} of {len(tasks)} {DATASET} tasks, seed {a.seed}, stratified by "
        "difficulty (proportional) and spread over categories.",
        "difficulty mix: " + ", ".join(f"{k}={v}" for k, v in sorted(mix.items())),
        f"Written by: python scripts/harbor_tasks.py stratified --seed {a.seed} "
        f"--count {a.count}"])
    print(f"[harbor_tasks] {len(names)} task(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
