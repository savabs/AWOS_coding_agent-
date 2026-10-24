#!/usr/bin/env python3
"""
Merge rl-eval artifacts into one baseline table (BASELINE_PROTOCOL.md section "Metrics").

    python3 scripts/rl/merge_baselines.py <artifacts_dir> [--out BASELINE.md] [--json baseline.json]

Input: any tree containing run dirs (runs/rl/<model>__<source>__<split>__shardKofN/), as
downloaded by `gh run download` or actions/download-artifact. A run dir is recognised by
run_meta.json (written by scripts/rl/ci_run_shard.sh) or by its name pattern.

Episode records are read from every *.jsonl file (and *.json files holding a list) under a
run dir. Accepted record shapes (the harness's exact layout is not fixed yet):
    {"task": TaskSpec, "trajectory": Trajectory, "score": Score}       (preferred)
    {"task_id", "success", "unsafe", "outcome", "steps", "seconds", "error", ...}  (flat)
Anything without a recognisable success/score is ignored. Episodes are de-duplicated by
(model, source, task_id), keeping the first.

Per (model, source, split):
    n           scored episodes (harness/infra errors excluded and counted separately)
    pass@1      share with Score.success and no unsafe code (temperature 0, one attempt)
    unsafe      count by code (must be 0)
    needs_user  share of episodes whose trajectory outcome is needs_user
    steps/succ  mean model steps over successful episodes
    s/step      total episode seconds / total steps
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

NAME_RE = re.compile(r"^(?P<model>[^/]+?)__(?P<source>[^_/]+)__(?P<split>[^_/]+)__shard(?P<k>\d+)of(?P<n>\d+)$")


def find_runs(root: Path) -> List[Path]:
    runs = set()
    for p in root.rglob("run_meta.json"):
        runs.add(p.parent)
    for p in root.rglob("*"):
        if p.is_dir() and NAME_RE.match(p.name):
            runs.add(p)
    # an artifact folder named like a run that merely contains the real run dir is not a run
    return sorted(r for r in runs if not any(o != r and r in o.parents for o in runs))


def run_meta(run: Path) -> dict:
    meta = {}
    mp = run / "run_meta.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text())
        except ValueError:
            meta = {}
    m = NAME_RE.match(run.name)
    if m:
        for k in ("model", "source", "split"):
            meta.setdefault(k, m.group(k))
    return meta


def iter_records(run: Path) -> Iterable[dict]:
    for p in sorted(run.rglob("*.jsonl")):
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec
    for p in sorted(run.rglob("*.json")):
        if p.name in ("run_meta.json",):
            continue
        try:
            d = json.loads(p.read_text(errors="replace"))
        except ValueError:
            continue
        if isinstance(d, list):
            yield from (r for r in d if isinstance(r, dict))
        elif isinstance(d, dict) and isinstance(d.get("episodes"), list):
            yield from (r for r in d["episodes"] if isinstance(r, dict))


def episode(rec: dict, meta: dict) -> Optional[dict]:
    score = rec.get("score") if isinstance(rec.get("score"), dict) else rec
    if "success" not in score:
        return None
    traj = rec.get("trajectory") or rec.get("traj") or {}
    task = rec.get("task") if isinstance(rec.get("task"), dict) else {}
    steps = traj.get("steps") if isinstance(traj, dict) else None
    n_steps = len(steps) if isinstance(steps, list) else rec.get("steps", rec.get("n_steps", 0))
    try:
        n_steps = int(n_steps or 0)
    except (TypeError, ValueError):
        n_steps = 0
    seconds = (traj.get("seconds") if isinstance(traj, dict) else None) or rec.get("seconds") or 0.0
    return {
        "model": str(traj.get("model") or rec.get("model") or meta.get("model") or "?"),
        "source": str(task.get("source") or rec.get("source") or meta.get("source") or "?"),
        "split": str(task.get("split") or rec.get("split") or meta.get("split") or "?"),
        "task_id": str(task.get("id") or traj.get("task_id") or rec.get("task_id") or rec.get("id") or ""),
        "success": bool(score.get("success")),
        "unsafe": list(score.get("unsafe") or []),
        "outcome": str(traj.get("outcome") or rec.get("outcome") or ""),
        "error": str(traj.get("error") or rec.get("error") or ""),
        "steps": n_steps,
        "seconds": float(seconds or 0.0),
    }


def collect(root: Path) -> List[dict]:
    seen = set()
    out = []
    for run in find_runs(root):
        meta = run_meta(run)
        # label from the run (the CI model label) wins over whatever the harness wrote
        for rec in iter_records(run):
            ep = episode(rec, meta)
            if ep is None:
                continue
            if meta.get("model"):
                ep["model"] = meta["model"]
            key = (ep["model"], ep["source"], ep["task_id"] or id(rec))
            if key in seen:
                continue
            seen.add(key)
            out.append(ep)
    return out


def aggregate(eps: List[dict]) -> List[dict]:
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for e in eps:
        groups[(e["model"], e["source"], e["split"])].append(e)
    rows = []
    for (model, source, split), g in sorted(groups.items()):
        errs = [e for e in g if e["error"]]
        ok = [e for e in g if not e["error"]]
        n = len(ok)
        wins = [e for e in ok if e["success"] and not e["unsafe"]]
        unsafe = Counter(c for e in ok for c in e["unsafe"])
        steps = sum(e["steps"] for e in ok)
        secs = sum(e["seconds"] for e in ok)
        rows.append({
            "model": model, "source": source, "split": split, "n": n, "infra_errors": len(errs),
            "pass1": (len(wins) / n) if n else None,
            "successes": len(wins),
            "unsafe": dict(unsafe), "unsafe_total": sum(unsafe.values()),
            "needs_user_rate": (sum(e["outcome"] == "needs_user" for e in ok) / n) if n else None,
            "steps_per_success": (sum(e["steps"] for e in wins) / len(wins)) if wins else None,
            "s_per_step": (secs / steps) if steps else None,
        })
    return rows


def _f(x, pct=False, nd=1):
    if x is None:
        return "–"
    return f"{100 * x:.{nd}f}%" if pct else f"{x:.{nd}f}"


def markdown(rows: List[dict], n_eps: int) -> str:
    lines = [
        "# Computer-use RL baseline (pre-registered protocol: BASELINE_PROTOCOL.md)",
        "",
        f"{n_eps} scored episodes. pass@1 = Score.success with no unsafe code, temperature 0, one attempt.",
        "",
        "| model | source | split | n | pass@1 | unsafe (by code) | needs_user | steps/success | s/step | infra errors |",
        "|---|---|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        unsafe = ", ".join(f"{k}:{v}" for k, v in sorted(r["unsafe"].items())) or "0"
        lines.append(
            f"| {r['model']} | {r['source']} | {r['split']} | {r['n']} | "
            f"{_f(r['pass1'], pct=True)} ({r['successes']}/{r['n']}) | {unsafe} | "
            f"{_f(r['needs_user_rate'], pct=True)} | {_f(r['steps_per_success'])} | "
            f"{_f(r['s_per_step'])} | {r['infra_errors']} |")
    if not rows:
        lines.append("| – | – | – | 0 | – | – | – | – | – | – |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    eps: List[dict] = []
    for r in a.root:
        eps.extend(collect(r))
    rows = aggregate(eps)
    md = markdown(rows, len(eps))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(md)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({"rows": rows, "n_episodes": len(eps)}, indent=1))
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
