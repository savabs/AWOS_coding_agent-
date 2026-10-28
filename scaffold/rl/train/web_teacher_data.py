"""
web_teacher_data.py — judged teacher episodes (live web, Mind2Web train tasks) -> SFT rows in the dataset.py
output schema (docs/specs/web_teacher.md).

    python -m scaffold.rl.train.web_teacher_data \\
        --runs merged/teach/ [--runs ...]                     # trajectories.jsonl (searched recursively)
        --verdicts judged/results.json [--verdicts ...]       # WebJudge results.json or <out>.verdicts.jsonl
        --exclude Online_Mind2Web.json [--exclude ...]        # REQUIRED: the test set
        [--dev-ids scaffold/rl/web/data/dev_ids.json] [--recovery-from-failed] \\
        --out runs/rl/teacher_rows.jsonl
    python -m scaffold.rl.train.dataset ... --extra-rows teacher=runs/rl/teacher_rows.jsonl

Kept episodes (rejection sampling: success comes only from the judge, never from done()):
  * task.source == "mind2web-train" and task.split == "train"; a task id in the dev id file, or a task text
    that fuzzy-matches any Online-Mind2Web task (mind2web_data.Exclusion), aborts the whole build
    (ContaminationError), it is not a filter;
  * no infra error, not blocked, harness_status ok;
  * the WebJudge verdict is status "judged" with predicted_label 1 (a task with conflicting verdicts across
    files is dropped);
  * the episode ended with done("...") (an episode the judge passed but the agent abandoned is not imitated).
Kept steps: valid (parsed, grammar-checked, not rejected by the agent's code), with a grammar and a
non-empty reply; exact duplicates of (system, user, reply) are dropped. Each row is checked with
dataset.validate_row before it is written.

Recovery rows: a valid step right after an invalid / rejected one (its prompt's history shows the rejection)
is tagged meta.recovery = true. With --recovery-from-failed such rows are also taken from episodes the judge
FAILED (not blocked, no infra error), except done / needs_user steps (a claim on a failed task is not a
target): the recovering move is local evidence, the episode's outcome is not.

Row meta: task_id = the base task (m2w-<annotation_id>; repeats share it, so dataset.py's validation split
keeps them together), split "train", task_source "mind2web-train", family, episode, step, teacher, recovery.
A stats JSON lands next to the rows (<out>.stats.json, counts only).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from scaffold.rl.train.dataset import EvalSplitError, Example, latest_records, validate_row
from scaffold.rl.train.mind2web_data import (DEFAULT_JACCARD, DEFAULT_THRESHOLD, ContaminationError, Exclusion,
                                             load_benchmark)

TRAIN_SOURCE = "mind2web-train"
TERMINAL = ("done(", "needs_user(")


# ------------------------------------------------------------------ inputs --

def trajectory_files(paths: Sequence[Path]) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        p = Path(p)
        out += sorted(p.rglob("trajectories.jsonl")) if p.is_dir() else [p]
    if not out:
        raise FileNotFoundError(f"no trajectories.jsonl under {', '.join(map(str, paths))}")
    return out


def _verdict_rows(path: Path) -> Iterable[dict]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("runs"), dict):            # scripts/web/judge.py results.json
        for r in doc["runs"].values():
            yield from (r or {}).get("tasks") or []
        return
    if isinstance(doc, list):
        yield from (v for v in doc if isinstance(v, dict))
        return
    for ln in raw.splitlines():                                               # <out>.verdicts.jsonl
        if ln.strip():
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            yield row.get("verdict") if isinstance(row.get("verdict"), dict) else row


def load_verdicts(paths: Sequence[Path]) -> Tuple[Dict[str, dict], int]:
    """task_id -> verdict; tasks whose files disagree on the label are dropped. -> (verdicts, conflicts)."""
    seen: Dict[str, dict] = {}
    bad = set()
    for p in paths:
        for v in _verdict_rows(Path(p)):
            tid = str(v.get("task_id") or "")
            if not tid or v.get("status") not in ("judged", "blocked", "infra_error"):
                continue
            if tid in seen and seen[tid].get("predicted_label") != v.get("predicted_label"):
                bad.add(tid)
            seen.setdefault(tid, v)
    for t in bad:
        seen.pop(t, None)
    return seen, len(bad)


# ------------------------------------------------------------------ rules --

def _harness(traj: dict) -> dict:
    return next((e for e in reversed(traj.get("events") or []) if e.get("event") == "harness"), {})


def check_task(task: dict, excl: Exclusion, dev: set) -> None:
    """Hard contamination gate: raises, never filters."""
    tid = str(task.get("id") or "")
    base = str((task.get("meta") or {}).get("base_task") or tid)
    if task.get("source") != TRAIN_SOURCE or task.get("split") != "train":
        raise EvalSplitError(f"task {tid!r} is source={task.get('source')!r} split={task.get('split')!r}: only "
                             f"{TRAIN_SOURCE!r} train tasks may become teacher rows")
    if tid in dev or base in dev:
        raise ContaminationError(f"task {tid!r} is an Online-Mind2Web dev id")
    hit = excl.match(str(task.get("goal") or ""))
    if hit is not None:
        raise ContaminationError(f"task {tid!r} matches Online-Mind2Web task {hit[0].task_id!r} "
                                 f"(ratio {hit[1]:.2f}, jaccard {hit[2]:.2f})")


def episode_drop_reason(rec: dict, verdicts: Dict[str, dict]) -> Optional[str]:
    """None = a judged success to imitate; else a reason code ('judge_failed' episodes may still give
    recovery rows)."""
    traj = rec.get("trajectory") or {}
    h = _harness(traj)
    if traj.get("error"):
        return "infra_error"
    if h.get("blocked"):
        return "blocked"
    if not traj.get("steps"):
        return "no_steps"
    v = verdicts.get(str(rec["task"].get("id")))
    if v is None:
        return "not_judged"
    if v.get("status") != "judged" or v.get("harness_status", "ok") != "ok":
        return f"verdict_{v.get('status')}"
    if int(v.get("predicted_label") or 0) != 1:
        return "judge_failed"
    if not str(traj["steps"][-1].get("action") or "").startswith("done("):
        return "not_done"
    return None


def usable(step: dict) -> bool:
    return bool(step.get("valid", True) and step.get("grammar") and (step.get("reply") or "").strip())


def episode_rows(rec: dict, *, recovery_only: bool = False) -> List[Tuple[Example, bool]]:
    """(Example, is_recovery) per kept step of one episode."""
    task, traj = rec["task"], rec["trajectory"]
    steps = traj.get("steps") or []
    meta0 = task.get("meta") or {}
    out: List[Tuple[Example, bool]] = []
    for j, s in enumerate(steps):
        if not usable(s):
            continue
        rec_row = j > 0 and not steps[j - 1].get("valid", True)
        if recovery_only and (not rec_row or str(s.get("action") or "").startswith(TERMINAL)):
            continue
        meta = {"task_id": str(meta0.get("base_task") or task["id"]), "split": "train", "task_source": TRAIN_SOURCE,
                "family": task.get("family"), "episode": task["id"], "step": s.get("i", j + 1),
                "teacher": traj.get("model"), "recovery": rec_row, "kind": "teacher",
                "judged": "failed" if recovery_only else "success"}
        out.append((Example(s["system"], s["user"], s["reply"].strip(), meta), rec_row))
    return out


# ------------------------------------------------------------------ build --

def build(runs: Sequence[Path], verdict_paths: Sequence[Path], bench_paths: Sequence[Path], *,
          dev_ids_path: Optional[Path] = None, recovery_from_failed: bool = False,
          threshold: float = DEFAULT_THRESHOLD, jaccard: float = DEFAULT_JACCARD) -> Tuple[List[dict], Dict[str, Any]]:
    from scaffold.rl.web.source import committed_dev_ids
    if not bench_paths:
        raise ValueError("--exclude (Online-Mind2Web task file) is required")
    excl = Exclusion(load_benchmark(bench_paths), threshold, jaccard)
    dev = set(committed_dev_ids(dev_ids_path) if dev_ids_path else committed_dev_ids())
    if not dev:
        raise ValueError("no committed dev ids: refusing (the dev split could not be checked)")
    verdicts, conflicts = load_verdicts(verdict_paths)
    recs: List[dict] = []
    for p in trajectory_files(runs):
        recs += latest_records(p)
    drops: Counter = Counter()
    rows: List[dict] = []
    seen = set()
    kept_eps, rec_rows, kept_steps = 0, 0, []
    by_site: Counter = Counter()
    for rec in recs:
        check_task(rec["task"], excl, dev)
        why = episode_drop_reason(rec, verdicts)
        if why is None:
            pairs = episode_rows(rec)
            kept_eps += 1
            kept_steps.append(len(pairs))
            by_site[rec["task"].get("family") or "?"] += 1
        elif why == "judge_failed" and recovery_from_failed:
            pairs = episode_rows(rec, recovery_only=True)
            drops[why] += 1
        else:
            drops[why] += 1
            continue
        for ex, is_rec in pairs:
            key = (ex.system, ex.user, ex.reply)
            if key in seen:
                drops["duplicate_row"] += 1
                continue
            seen.add(key)
            row = ex.to_json()
            validate_row(row, f"{ex.meta['episode']} step {ex.meta['step']}")
            rows.append(row)
            rec_rows += int(is_rec)
    stats = {"episodes": len(recs), "kept_episodes": kept_eps, "verdicts": len(verdicts),
             "verdict_conflicts": conflicts, "dropped": dict(drops.most_common()), "rows": len(rows),
             "recovery_rows": rec_rows, "rows_from_failed_episodes": sum(r["meta"]["judged"] == "failed" for r in rows),
             "rows_per_kept_episode": round(sum(kept_steps) / kept_eps, 2) if kept_eps else 0.0,
             "kept_by_site": dict(by_site.most_common()), "recovery_from_failed": recovery_from_failed}
    return rows, stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.web_teacher_data",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", action="append", type=Path, required=True)
    ap.add_argument("--verdicts", action="append", type=Path, required=True)
    ap.add_argument("--exclude", action="append", type=Path, required=True)
    ap.add_argument("--dev-ids", type=Path, default=None)
    ap.add_argument("--recovery-from-failed", action="store_true")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        rows, stats = build(a.runs, a.verdicts, a.exclude, dev_ids_path=a.dev_ids,
                            recovery_from_failed=a.recovery_from_failed)
    except (EvalSplitError, ValueError, OSError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        a.out.unlink(missing_ok=True)
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    Path(str(a.out) + ".stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps({k: v for k, v in stats.items() if k != "kept_by_site"}, indent=1))
    print(f"-> {a.out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
