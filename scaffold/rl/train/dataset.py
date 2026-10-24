"""
dataset.py — verified trajectories -> SFT JSONL for mlx-lm (Stage A, rejection-sampling SFT).

    python -m scaffold.rl.train.dataset \\
        --input oracle=runs/rl/demos_factory_train \\
        --input q4b=artifacts/collect-factory-train/trajectories.jsonl:2 \\
        --out ~/.cache/awos-models/rl/data/rft-v1 [--val-frac 0.1] [--max-per-task 4] [--seed 0] \\
        [--clean [--scripted-frac 0.3] [--max-repeats 1]] [--reasons reasons.jsonl] \\
        [--extra-rows corrections=runs/rl/corrections.jsonl[:W]]

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
    EVAL_SPLITS) are a hard error, not a filter: the whole build aborts. So is a "train" task whose
    factory family is an ood family or whose MiniWoB type/family is held out (clean.exam_reason).
Kept steps: valid (parsed by MicroAgent) with a GBNF grammar (json-format cloud steps are a
different action language) and a non-empty reply; exact duplicates of (system, user, reply) are
dropped across all sources (first occurrence wins, sources in --input order, then --extra-rows).

Validation split is by TASK ID (hash of seed + task id), so no task contributes to both sides.

Options (NEXT_WEEK_PLAN 0.4 / 0.5, docs/specs/mw_clean.md):
  --clean            clean.py: drop looping episodes and near-duplicate rows, cap scripted (oracle /
                     scripted) rows at --scripted-frac of train; stats.json["clean"] has before/after
                     counts per filter.
  --reasons FILE     merge the reasons.py cache: a row whose (user, action) key has a reason gets the
                     reply "# <reason>" + newline + action (an earlier reason line is replaced).
  --extra-rows [LABEL=]PATH[:WEIGHT]  (repeatable) rows from other tools in this file's output schema
                     (messages system/user/assistant, meta.task_id, meta.split). Every row is checked
                     (roles, one parseable action, at most one '# reason' line, prompt/completion equal
                     to chat.py when present); a bad row or an eval/ood/holdout task aborts the build.
                     They share dedupe, the task-id validation split, --clean and --reasons.

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

from scaffold.rl.train import clean as cl
from scaffold.rl.train.chat import render_completion, render_prompt

#: splits that are evaluation-only; training on them invalidates every reported number
EVAL_SPLITS = frozenset({"ood", "holdout", "test", "eval"})
#: splits known to be trainable; anything else is rejected too (fail closed on new names)
TRAIN_SPLITS = frozenset({"train"})


class EvalSplitError(AssertionError):
    """A trajectory from an evaluation split reached the training set builder."""


class RowSchemaError(ValueError):
    """An --extra-rows row is not in the dataset output schema."""


@dataclass
class Source:
    label: str
    paths: List[Path]
    weight: float = 1.0


@dataclass
class ExtraRows:
    label: str
    path: Path
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

def _split_weight(rest: str, spec: str) -> Tuple[str, float]:
    weight = 1.0
    head, sep, tail = rest.rpartition(":")
    if sep and head:
        try:
            weight = float(tail)
            rest = head
        except ValueError:
            pass
    if weight < 0:
        raise ValueError(f"negative weight in {spec!r}")
    return rest, weight


def parse_input(spec: str) -> Source:
    """LABEL=PATH[:WEIGHT]"""
    if "=" not in spec:
        raise ValueError(f"--input wants LABEL=PATH[:WEIGHT], got {spec!r}")
    label, _, rest = spec.partition("=")
    rest, weight = _split_weight(rest, spec)
    p = Path(rest).expanduser()
    if p.is_dir():
        paths = sorted(p.rglob("trajectories.jsonl"))
    else:
        paths = [p]
    if not paths or not all(x.exists() for x in paths):
        raise FileNotFoundError(f"no trajectories.jsonl under {p}")
    return Source(label=label, paths=paths, weight=weight)


def parse_extra(spec: str) -> ExtraRows:
    """[LABEL=]PATH[:WEIGHT]"""
    label, sep, rest = spec.partition("=")
    if not sep:
        label, rest = "", spec
    rest, weight = _split_weight(rest, spec)
    p = Path(rest).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"--extra-rows: no file {p}")
    return ExtraRows(label=label or f"extra:{p.stem}", path=p, weight=weight)


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
    why = cl.exam_reason(task.get("id") or "", split, task.get("source"), task.get("family"))
    if why:          # labelled train, but the family / task type is in the exam (mislabelled data)
        raise EvalSplitError(f"task {task.get('id')!r} is part of the exam: {why}")


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


def validate_row(r: Any, where: str) -> Tuple[str, str, str, dict]:
    """An --extra-rows row -> (system, user, reply, meta); RowSchemaError / EvalSplitError otherwise."""
    from scaffold.agent.desktop.micro_agent import ParseError, parse_text_action  # noqa: PLC0415
    if not isinstance(r, dict) or not isinstance(r.get("messages"), list) or not isinstance(r.get("meta"), dict):
        raise RowSchemaError(f"{where}: want an object with messages[] and meta{{}}")
    msgs = r["messages"]
    if [m.get("role") if isinstance(m, dict) else None for m in msgs] != ["system", "user", "assistant"] or \
            not all(isinstance(m.get("content"), str) and m["content"].strip() for m in msgs):
        raise RowSchemaError(f"{where}: messages must be system, user, assistant with non-empty text")
    system, user, reply = (m["content"] for m in msgs)
    meta = r["meta"]
    if not isinstance(meta.get("task_id"), str) or not meta["task_id"]:
        raise RowSchemaError(f"{where}: meta.task_id missing")
    check_split({"id": meta["task_id"], "split": meta.get("split"), "source": meta.get("task_source"),
                 "family": meta.get("family")})
    lines = [ln.strip() for ln in reply.strip().splitlines() if ln.strip()]
    notes = [ln for ln in lines if ln.startswith("#")]
    if len(notes) > 1 or (notes and (not lines[0].startswith("# ") or len(lines[0]) > 162)):
        raise RowSchemaError(f"{where}: at most one leading '# reason' line of <=160 chars")
    try:
        parse_text_action(reply)
    except ParseError as e:
        raise RowSchemaError(f"{where}: reply is not one action line: {e}") from None
    if "prompt" in r and r["prompt"] != render_prompt(system, user):
        raise RowSchemaError(f"{where}: prompt differs from chat.render_prompt(system, user)")
    if "completion" in r and r["completion"] != render_completion(reply):
        raise RowSchemaError(f"{where}: completion differs from chat.render_completion(reply)")
    return system, user, reply.strip(), meta


def task_is_val(task_id: str, frac: float, seed: int) -> bool:
    if frac <= 0:
        return False
    h = hashlib.sha256(f"{seed}:{task_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64 < frac


# ------------------------------------------------------------------- build --

def build(sources: List[Source], *, val_frac: float = 0.1, seed: int = 0, max_per_task: int = 0,
          max_examples: int = 0, boost_tasks: Optional[set] = None, boost_factor: int = 1,
          clean: bool = False, scripted_frac: float = cl.DEFAULT_SCRIPTED_FRAC,
          max_repeats: int = cl.DEFAULT_MAX_REPEATS, reasons: Optional[Dict[str, str]] = None,
          extra: Optional[List[ExtraRows]] = None) -> Tuple[List[Example], List[Example], Dict[str, Any]]:
    """-> (train, valid, stats). ``boost_tasks``: train examples of these task ids are written
    ``boost_factor`` times (e.g. tasks the small model gave up on or looped on). ``clean``: clean.py
    filters; ``reasons``: key -> reason (reasons.reasons_map); ``extra``: --extra-rows files."""
    from scaffold.rl.train.reasons import row_key, with_reason  # noqa: PLC0415
    rng = random.Random(seed)
    boost_tasks = boost_tasks or set()
    stats: Dict[str, Any] = {"sources": {}, "val_frac": val_frac, "seed": seed, "max_per_task": max_per_task,
                             "boost_tasks": len(boost_tasks), "boost_factor": boost_factor}
    seen: set = set()
    near_seen: set = set()
    train: List[Example] = []
    valid: List[Example] = []
    val_tasks, train_tasks = set(), set()
    rep = cl.CleanReport()
    rstat = Counter()

    def emit(st: dict, label: str, weight: float, system: str, user: str, reply: str, meta: dict) -> None:
        tid = meta["task_id"]
        is_val = task_is_val(tid, val_frac, seed)
        (val_tasks if is_val else train_tasks).add(tid)
        key = hashlib.sha256(json.dumps([system, user, reply.strip()]).encode()).digest()
        if key in seen:
            st["steps_dropped"]["duplicate"] += 1
            return
        seen.add(key)
        if clean:
            nk = cl.near_key(system, user, reply)
            rep.add("near_dedupe", 1, 0 if nk in near_seen else 1, "rows")
            if nk in near_seen:
                st["steps_dropped"]["near_duplicate"] += 1
                return
            near_seen.add(nk)
        reply = reply.strip()
        if reasons is not None:
            why = reasons.get(row_key(user, reply))
            rstat["applied" if why else "missing"] += 1
            if why:
                reply = with_reason(reply, why)
        st["examples_unique"] += 1
        ex = Example(system=system, user=user, reply=reply, meta=meta)
        copies = 1 if is_val else int(weight) + (1 if rng.random() < weight % 1 else 0)
        if not is_val and tid in boost_tasks:
            copies *= max(1, boost_factor)
        for _ in range(copies):
            (valid if is_val else train).append(ex)
            if not is_val:
                st["examples_written"] += 1

    def new_stats(files: List[str], weight: float) -> dict:
        return {"files": files, "weight": weight, "episodes_in": 0, "episodes_dropped": Counter(),
                "episodes_kept": 0, "steps_dropped": Counter(), "examples_unique": 0, "examples_written": 0}

    for src in sources:
        st = new_stats([str(p) for p in src.paths], src.weight)
        kept: Dict[str, List[dict]] = defaultdict(list)
        for path in src.paths:
            for rec in latest_records(path):
                st["episodes_in"] += 1
                why = episode_drop_reason(rec)
                rep.add("verified_success", 1, 0 if why else 1, "episodes")
                if why:
                    st["episodes_dropped"][why] += 1
                    continue
                if clean:
                    why = cl.loop_reason(rec["trajectory"]["steps"], max_repeats)
                    rep.add("no_loops", 1, 0 if why else 1, "episodes")
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
                for s in rec["trajectory"]["steps"]:
                    why = step_drop_reason(s)
                    if why:
                        st["steps_dropped"][why] += 1
                        continue
                    emit(st, src.label, src.weight, s["system"], s["user"], s["reply"], {
                        "src": src.label, "task_id": task["id"], "task_source": task.get("source"),
                        "family": task.get("family"), "kind": task.get("kind"), "split": task.get("split"),
                        "sample": int(rec.get("sample", 0)), "step": int(s.get("i", 0)),
                        "model": rec["trajectory"].get("model"), "action": s.get("action", "")})
        stats["sources"][src.label] = st

    for ex_src in extra or []:
        st = new_stats([str(ex_src.path)], ex_src.weight)
        st["rows_in"] = 0
        for n, r in enumerate(read_jsonl(ex_src.path), 1):
            system, user, reply, meta = validate_row(r, f"{ex_src.path}:{n}")
            st["rows_in"] += 1
            meta = dict(meta, src=ex_src.label)
            meta.setdefault("model", "extra")
            emit(st, ex_src.label, ex_src.weight, system, user, reply, meta)
        stats["sources"][ex_src.label] = st

    for st in stats["sources"].values():
        st["episodes_dropped"] = dict(st["episodes_dropped"])
        st["steps_dropped"] = dict(st["steps_dropped"])
    assert not (val_tasks & train_tasks), "a task id landed on both sides of the split"
    if clean:
        train, cap = cl.cap_scripted(train, scripted_frac, seed)
        rep.add("scripted_cap", cap["rows_in"], cap["rows_out"], "rows")
        rep.extra["scripted_cap_detail"] = cap
        stats["clean"] = rep.to_dict()
    if reasons is not None:
        stats["reasons"] = {"applied_unique": rstat["applied"], "missing_unique": rstat["missing"]}
    rng.shuffle(train)
    if max_examples and len(train) > max_examples:
        train = train[:max_examples]
    by = {k: Counter() for k in ("source", "family", "kind", "task_source", "verb")}
    for e in train:                                   # counted after the cap so stats match the files
        m = e.meta
        by["source"][m.get("src")] += 1
        by["family"][f"{m.get('task_source')}/{m.get('family')}"] += 1
        by["kind"][m.get("kind") or "?"] += 1
        by["task_source"][m.get("task_source") or "?"] += 1
        by["verb"][cl.action_line(e.reply).split("(")[0]] += 1
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
    ap.add_argument("--input", action="append", default=[], help="LABEL=PATH[:WEIGHT] (repeatable)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--val-frac", type=float, default=0.1, help="fraction of TASK IDS held out (default 0.1)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-per-task", type=int, default=0, help="cap kept episodes per (source, task); 0 = no cap")
    ap.add_argument("--max-examples", type=int, default=0, help="cap train examples after shuffling (smoke runs)")
    ap.add_argument("--boost-tasks", type=Path, default=None,
                    help="file of task ids (one per line, # comments) whose train examples are repeated")
    ap.add_argument("--boost-factor", type=int, default=3)
    ap.add_argument("--clean", action="store_true", help="drop loops + near-duplicates, cap scripted rows")
    ap.add_argument("--scripted-frac", type=float, default=cl.DEFAULT_SCRIPTED_FRAC,
                    help="--clean: max share of scripted (oracle) rows in train (default 0.30)")
    ap.add_argument("--max-repeats", type=int, default=cl.DEFAULT_MAX_REPEATS,
                    help="--clean: times one (observation, action) pair may occur in an episode (default 1)")
    ap.add_argument("--reasons", type=Path, default=None, help="reasons.py cache JSONL to merge")
    ap.add_argument("--extra-rows", action="append", default=[], help="[LABEL=]PATH[:WEIGHT] rows JSONL (repeatable)")
    a = ap.parse_args(argv)
    if not a.input and not a.extra_rows:
        ap.error("need at least one --input or --extra-rows")
    sources = [parse_input(s) for s in a.input]
    extra = [parse_extra(s) for s in a.extra_rows]
    boost = set()
    if a.boost_tasks:
        boost = {ln.strip() for ln in a.boost_tasks.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    reasons = None
    if a.reasons:
        from scaffold.rl.train.reasons import reasons_map  # noqa: PLC0415
        reasons = reasons_map(a.reasons)
    try:
        train, valid, stats = build(sources, val_frac=a.val_frac, seed=a.seed, max_per_task=a.max_per_task,
                                    max_examples=a.max_examples, boost_tasks=boost, boost_factor=a.boost_factor,
                                    clean=a.clean, scripted_frac=a.scripted_frac, max_repeats=a.max_repeats,
                                    reasons=reasons, extra=extra)
    except EvalSplitError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2
    except RowSchemaError as e:
        print(f"REFUSING bad --extra-rows row: {e}", file=sys.stderr)
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
    if "clean" in stats:
        print("clean (before -> after):")
        for k, v in stats["clean"].items():
            if isinstance(v, dict) and "in" in v:
                print(f"  {k:<17} {v['unit']:<8} {v['in']:>6} -> {v['out']:>6}  (-{v['dropped']})")
        cap = stats["clean"].get("scripted_cap_detail", {})
        if cap.get("skipped"):
            print(f"  scripted_cap skipped: {cap['skipped']}")
    if "reasons" in stats:
        print(f"reasons: {stats['reasons']}")
    print(f"-> {a.out}/train.jsonl, valid.jsonl, stats.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
