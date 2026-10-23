"""
dataset.py — verified trajectories -> SFT JSONL for mlx-lm (Stage A, rejection-sampling SFT).

    python -m scaffold.rl.train.dataset \\
        --input oracle=runs/rl/demos_factory_train \\
        --input q4b=artifacts/collect-factory-train/trajectories.jsonl:2 \\
        --out ~/.cache/awos-models/rl/data/rft-v1 [--val-frac 0.1] [--max-per-task 4] [--seed 0]

``--input LABEL=PATH[:WEIGHT]``: PATH is a trajectories.jsonl, a run directory containing one, or
a directory tree of them (searched recursively: GitHub artifacts unpack as one dir per shard).
WEIGHT (default 1) mixes sources: each kept example of that source is written floor(W) times plus
once more with probability frac(W) (0.5 = a random half, 2 = every example twice).

Kept episodes (rejection sampling; rewards come only from the verifier, never from done()):
  * no infra error; latest row per (task id, sample) within a file wins (runner semantics);
  * score.success is true AND score.unsafe is empty;
  * kind == "pause": the episode really stopped (trajectory.outcome == "needs_user");
    kind act/answer: it did not end in needs_user;
  * task.split is NOT an eval split. Eval splits (factory "ood", miniwob "holdout", anything in
    EVAL_SPLITS) are a hard error, not a filter: the whole build aborts.
Kept steps: valid (parsed by MicroAgent) with a GBNF grammar (json-format cloud steps are a
different action language) and a non-empty reply; exact duplicates of (system, user, reply) are
dropped across all sources (first occurrence wins, sources in --input order).

Validation split is by TASK ID (hash of seed + task id), so no task contributes to both sides.

Each output line: {"messages": [system, user, assistant=reply], "prompt": str, "completion": str,
"meta": {...}}. prompt/completion are chat.render_prompt / render_completion: exactly the text
llama-server builds from LocalGBNFModel's request (Qwen3.5 template, enable_thinking=False) and the
reply + <|im_end|>. The trainer (kaggle/rl-sft/train.py) tokenises them separately and puts the
loss on the completion only. stats.json reports kept/dropped counts by source, family, kind, split.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scaffold.rl.train.chat import render_completion, render_prompt

#: splits that are evaluation-only; training on them invalidates every reported number
EVAL_SPLITS = frozenset({"ood", "holdout", "test", "eval"})
#: splits known to be trainable; anything else is rejected too (fail closed on new names)
TRAIN_SPLITS = frozenset({"train"})


class EvalSplitError(AssertionError):
    """A trajectory from an evaluation split reached the training set builder."""


@dataclass
class Source:
    label: str
    paths: List[Path]
    weight: float = 1.0


@dataclass
class Example:
    system: str
    user: str
    reply: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"messages": [{"role": "system", "content": self.system},
                             {"role": "user", "content": self.user},
                             {"role": "assistant", "content": self.reply}],
                # the exact inference text (chat.py): train on prompt -> completion, loss on completion
                "prompt": render_prompt(self.system, self.user),
                "completion": render_completion(self.reply),
                "meta": self.meta}


# ------------------------------------------------------------------ inputs --

def parse_input(spec: str) -> Source:
    """LABEL=PATH[:WEIGHT]"""
    if "=" not in spec:
        raise ValueError(f"--input wants LABEL=PATH[:WEIGHT], got {spec!r}")
    label, _, rest = spec.partition("=")
    weight = 1.0
    head, sep, tail = rest.rpartition(":")
    if sep and head:
        try:
            weight = float(tail)
            rest = head
        except ValueError:
            pass
    p = Path(rest).expanduser()
    if p.is_dir():
        paths = sorted(p.rglob("trajectories.jsonl"))
    else:
        paths = [p]
    if not paths or not all(x.exists() for x in paths):
        raise FileNotFoundError(f"no trajectories.jsonl under {p}")
    if weight < 0:
        raise ValueError(f"negative weight in {spec!r}")
    return Source(label=label, paths=paths, weight=weight)


def read_jsonl(path: Path) -> Iterable[dict]:
    with open(path) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except ValueError:
                continue                                  # torn last line of an interrupted run


def latest_records(path: Path) -> List[dict]:
    """Latest row per (task id, sample), in first-seen order (runner.summarize semantics)."""
    latest: Dict[Tuple[str, int], dict] = {}
    for r in read_jsonl(path):
        latest[(r["task"]["id"], int(r.get("sample", 0)))] = r
    return list(latest.values())


# --------------------------------------------------------------- filtering --

def check_split(task: dict) -> None:
    split = task.get("split")
    if split in EVAL_SPLITS or split not in TRAIN_SPLITS:
        raise EvalSplitError(f"task {task.get('id')!r} has split={split!r}: only {sorted(TRAIN_SPLITS)} "
                             f"may be trained on (eval splits {sorted(EVAL_SPLITS)} are never allowed)")


def episode_drop_reason(rec: dict) -> Optional[str]:
    """None if the episode is a verified, safe success worth imitating; else a short reason code."""
    task, traj, score = rec["task"], rec.get("trajectory") or {}, rec.get("score") or {}
    check_split(task)
    if traj.get("error"):
        return "infra_error"
    if not score.get("success"):
        return "not_success"
    if score.get("unsafe"):
        return "unsafe"
    outcome = traj.get("outcome") or ""
    if task.get("kind") == "pause":
        if outcome != "needs_user":
            return "pause_not_stopped"
    elif outcome == "needs_user":
        return "act_ended_needs_user"
    if not traj.get("steps"):
        return "no_steps"                         # e.g. a product guard stopped before any model call
    return None


def step_drop_reason(step: dict) -> Optional[str]:
    if not step.get("valid", True):
        return "invalid"
    if not step.get("grammar"):
        return "no_grammar"
    if not (step.get("reply") or "").strip():
        return "empty_reply"
    return None


def task_is_val(task_id: str, frac: float, seed: int) -> bool:
    if frac <= 0:
        return False
    h = hashlib.sha256(f"{seed}:{task_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64 < frac


# ------------------------------------------------------------------- build --

def build(sources: List[Source], *, val_frac: float = 0.1, seed: int = 0, max_per_task: int = 0,
          max_examples: int = 0, boost_tasks: Optional[set] = None,
          boost_factor: int = 1) -> Tuple[List[Example], List[Example], Dict[str, Any]]:
    """-> (train, valid, stats). ``boost_tasks``: train examples of these task ids are written
    ``boost_factor`` times (e.g. tasks the small model gave up on or looped on)."""
    rng = random.Random(seed)
    boost_tasks = boost_tasks or set()
    stats: Dict[str, Any] = {"sources": {}, "val_frac": val_frac, "seed": seed, "max_per_task": max_per_task,
                             "boost_tasks": len(boost_tasks), "boost_factor": boost_factor}
    seen: set = set()
    train: List[Example] = []
    valid: List[Example] = []
    by = {k: Counter() for k in ("source", "family", "kind", "task_source", "verb")}
    val_tasks, train_tasks = set(), set()
    for src in sources:
        st = {"files": [str(p) for p in src.paths], "weight": src.weight, "episodes_in": 0,
              "episodes_dropped": Counter(), "episodes_kept": 0, "steps_dropped": Counter(),
              "examples_unique": 0, "examples_written": 0}
        kept: Dict[str, List[dict]] = defaultdict(list)
        for path in src.paths:
            for rec in latest_records(path):
                st["episodes_in"] += 1
                why = episode_drop_reason(rec)
                if why:
                    st["episodes_dropped"][why] += 1
                    continue
                kept[rec["task"]["id"]].append(rec)
        for tid, recs in kept.items():
            recs.sort(key=lambda r: (len(r["trajectory"]["steps"]), int(r.get("sample", 0))))
            if max_per_task and len(recs) > max_per_task:
                st["episodes_dropped"]["over_task_cap"] += len(recs) - max_per_task
                recs = recs[:max_per_task]
            for rec in recs:
                st["episodes_kept"] += 1
                task = rec["task"]
                is_val = task_is_val(task["id"], val_frac, seed)
                (val_tasks if is_val else train_tasks).add(task["id"])
                for s in rec["trajectory"]["steps"]:
                    why = step_drop_reason(s)
                    if why:
                        st["steps_dropped"][why] += 1
                        continue
                    key = hashlib.sha256(json.dumps([s["system"], s["user"], s["reply"].strip()]).encode()).digest()
                    if key in seen:
                        st["steps_dropped"]["duplicate"] += 1
                        continue
                    seen.add(key)
                    st["examples_unique"] += 1
                    ex = Example(system=s["system"], user=s["user"], reply=s["reply"].strip(), meta={
                        "src": src.label, "task_id": task["id"], "task_source": task.get("source"),
                        "family": task.get("family"), "kind": task.get("kind"), "split": task.get("split"),
                        "sample": int(rec.get("sample", 0)), "step": int(s.get("i", 0)),
                        "model": rec["trajectory"].get("model"), "action": s.get("action", "")})
                    copies = 1 if is_val else int(src.weight) + (1 if rng.random() < src.weight % 1 else 0)
                    if not is_val and task["id"] in boost_tasks:
                        copies *= max(1, boost_factor)
                    for _ in range(copies):
                        (valid if is_val else train).append(ex)
                        if not is_val:
                            st["examples_written"] += 1
                            by["source"][src.label] += 1
                            by["family"][f"{task.get('source')}/{task.get('family')}"] += 1
                            by["kind"][task.get("kind") or "?"] += 1
                            by["task_source"][task.get("source") or "?"] += 1
                            by["verb"][ex.reply.splitlines()[-1].split("(")[0]] += 1
        st["episodes_dropped"] = dict(st["episodes_dropped"])
        st["steps_dropped"] = dict(st["steps_dropped"])
        stats["sources"][src.label] = st
    assert not (val_tasks & train_tasks), "a task id landed on both sides of the split"
    rng.shuffle(train)
    if max_examples and len(train) > max_examples:
        train = train[:max_examples]
    stats.update({"train_examples": len(train), "valid_examples": len(valid),
                  "train_tasks": len({e.meta["task_id"] for e in train}), "valid_tasks": len(val_tasks),
                  "train_by": {k: dict(v.most_common()) for k, v in by.items()},
                  "chars": _len_stats([len(e.system) + len(e.user) + len(e.reply) for e in train + valid]),
                  "reply_chars": _len_stats([len(e.reply) for e in train + valid])})
    return train, valid, stats


def _len_stats(xs: List[int]) -> Dict[str, int]:
    if not xs:
        return {}
    xs = sorted(xs)
    q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
    return {"min": xs[0], "p50": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": xs[-1]}


def write(out: Path, train: List[Example], valid: List[Example], stats: Dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("valid", valid)):
        with open(out / f"{name}.jsonl", "w") as fh:
            for e in rows:
                fh.write(json.dumps(e.to_json(), ensure_ascii=False) + "\n")
    (out / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.dataset", description=__doc__.split("\n\n")[0])
    ap.add_argument("--input", action="append", required=True, help="LABEL=PATH[:WEIGHT] (repeatable)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--val-frac", type=float, default=0.1, help="fraction of TASK IDS held out (default 0.1)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-per-task", type=int, default=0, help="cap kept episodes per (source, task); 0 = no cap")
    ap.add_argument("--max-examples", type=int, default=0, help="cap train examples after shuffling (smoke runs)")
    ap.add_argument("--boost-tasks", type=Path, default=None,
                    help="file of task ids (one per line, # comments) whose train examples are repeated")
    ap.add_argument("--boost-factor", type=int, default=3)
    a = ap.parse_args(argv)
    sources = [parse_input(s) for s in a.input]
    boost = set()
    if a.boost_tasks:
        boost = {ln.strip() for ln in a.boost_tasks.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    try:
        train, valid, stats = build(sources, val_frac=a.val_frac, seed=a.seed, max_per_task=a.max_per_task,
                                    max_examples=a.max_examples, boost_tasks=boost, boost_factor=a.boost_factor)
    except EvalSplitError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2
    if not train:
        print("no training examples survived the filters", file=sys.stderr)
        return 1
    write(a.out.expanduser(), train, valid, stats)
    print(json.dumps({k: stats[k] for k in ("train_examples", "valid_examples", "train_tasks", "valid_tasks",
                                            "chars", "train_by")}, indent=1))
    for lab, st in stats["sources"].items():
        print(f"[{lab}] episodes in={st['episodes_in']} kept={st['episodes_kept']} dropped={st['episodes_dropped']} "
              f"steps dropped={st['steps_dropped']} unique={st['examples_unique']} written={st['examples_written']}")
    print(f"-> {a.out}/train.jsonl, valid.jsonl, stats.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
