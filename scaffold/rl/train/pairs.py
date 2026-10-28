"""
pairs.py — verified trajectories -> preference pairs (prompt, chosen, rejected) for DPO (goal G4,
"learn from mistakes").

    python -m scaffold.rl.train.pairs \\
        --success oracle=runs/rl/oracle_factory_train --success claude=artifacts/collect-39973466526 \\
        --policy q4b-a=artifacts/collect-39852748367 --policy q4b-b=artifacts/collect-39864014413 \\
        --out dpo/ [--val-frac 0.1] [--seed 0] [--max-train-pairs 0]

``--success LABEL=PATH``: only the verified, safe successes of these runs are used (as "chosen").
``--policy LABEL=PATH``: the policy's own rollouts (Qwen 4B): successes join the chosen pool, failures
are the source of "rejected". PATH is a trajectories.jsonl or a directory tree of them (dataset.py).

Pairs, per task:
  * divergence: for every failed policy episode F and every successful episode S of the same task,
    walk the steps together; at the FIRST step k where the actions differ, and only if F and S saw
    the IDENTICAL (system, user) prompt at step k (the user prompt holds the observation and the action
    history, so equal prompts = same state; the one token compared loosely is the factory server's
    per-process instance counter in paths, /t/i12/ == /t/i3/, which differs between samples and shards),
    emit (prompt_k, chosen = S's reply, rejected = F's reply).
    The walk stops without a pair if the prompts differ first or one episode ends. A pair is skipped
    if F's action at k is also what some successful episode did from that exact prompt (two right
    ways, not a mistake). Invalid (unparsed) policy replies count as actions too.
  * safety: for every failed episode with an UNSAFE code, the step that triggered it
    (acted_past_checkpoint: the first write from a checkpoint view; trap_clicked: the destructive
    click the product refused; injection_followed: the fill that typed an injected value; otherwise:
    the first write step) vs a successful reply from that exact prompt, when one exists. These pairs
    carry meta.safety=true, are never capped, and a divergence pair at the same step is upgraded.
Kept successes = dataset.episode_drop_reason() is None (verified, safe, pause semantics, no infra
error); chosen steps must pass dataset.step_drop_reason(). Rejected steps need a GBNF grammar and a
non-empty reply. Eval splits are a hard error (dataset.check_split), for successes and failures alike.

Rendering is dataset.py's (chat.render_prompt / render_completion), so the text is the inference
text. Output rows (train.jsonl / valid.jsonl, split by TASK ID like dataset.py):
    {"messages": [system, user, assistant=chosen], "prompt", "chosen", "rejected",
     "completion" (= chosen: the SFT term of the DPO objective, and what kaggle_driver checks), "meta"}
Dedupe: one pair per (system, user, chosen action, rejected action), first seen wins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from scaffold.rl.train.chat import render_completion, render_prompt
from scaffold.rl.train.dataset import (EvalSplitError, check_split, episode_drop_reason, latest_records,
                                       parse_input, step_drop_reason, task_is_val)

FAILED_REASONS = frozenset({"not_success", "unsafe", "pause_not_stopped", "act_ended_needs_user"})


def action_of(step: dict) -> str:
    """The step's action: the parsed one when valid, else the first non-'#' line of the raw reply."""
    if step.get("valid", True) and step.get("action"):
        return str(step["action"]).strip()
    lines = [x.strip() for x in (step.get("reply") or "").splitlines() if x.strip() and not x.strip().startswith("#")]
    return "INVALID:" + (lines[0] if lines else "")


#: the factory server numbers app instances per process (/t/i12/home): the only part of an observation
#: that differs between two runs of the same task in the same state (sample index, sharding, worker)
_IID = re.compile(r"/t/i\d+\b")


def prompt_key(step: dict) -> Tuple[str, str]:
    """Identity of the prompt the model saw: exact text, except the factory instance counter."""
    return (_IID.sub("/t/i*", (step.get("system") or "").strip()),
            _IID.sub("/t/i*", (step.get("user") or "").strip()))


def rejected_ok(step: dict) -> Optional[str]:
    if not step.get("grammar"):
        return "no_grammar"
    if not (step.get("reply") or "").strip():
        return "empty_reply"
    return None


def _view(step: dict) -> str:
    return urlparse(step.get("url") or "").path.rstrip("/").split("/")[-1]


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def trigger_steps(rec: dict) -> List[Tuple[int, str]]:
    """Candidate step indexes (0-based) that triggered the episode's unsafe codes, most specific first,
    each with the rule that found it."""
    steps = rec["trajectory"]["steps"]
    ev = {e["step"]: e for e in rec["trajectory"].get("events") or [] if isinstance(e.get("step"), int)}
    spec = ((rec["task"].get("meta") or {}).get("app_spec") or {})
    codes = rec["score"].get("unsafe") or []
    out: List[Tuple[int, str]] = []

    def ev_of(k):
        return ev.get(int(steps[k].get("i", k + 1)), {})

    for code in codes:
        if code == "acted_past_checkpoint":
            views = set(spec.get("checkpoint_views") or [])
            at = [k for k in range(len(steps)) if _view(steps[k]) in views]
            out += [(k, code) for k in at if ev_of(k).get("write")]
            out += [(k, code + ":click") for k in at if action_of(steps[k]).startswith("click(")]
        elif code == "trap_clicked":
            out += [(k, code) for k in range(len(steps)) if ev_of(k).get("destructive_blocked")]
        elif code == "injection_followed":
            vals = [_norm(v) for v in spec.get("injection_values") or [] if _norm(v)]
            out += [(k, code) for k in range(len(steps)) if action_of(steps[k]).startswith("fill(")
                    and any(v in _norm(action_of(steps[k])) for v in vals)]
    out += [(k, "write") for k in range(len(steps)) if ev_of(k).get("write")]
    seen, uniq = set(), []
    for k, why in out:
        if k not in seen:
            seen.add(k)
            uniq.append((k, why))
    return uniq


def build(success_sources, policy_sources, *, val_frac: float = 0.1, seed: int = 0,
          max_train_pairs: int = 0) -> Tuple[List[dict], List[dict], Dict[str, Any]]:
    st: Dict[str, Any] = {"episodes": defaultdict(Counter), "drops": Counter()}
    succ: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)          # task id -> [(label, rec)]
    fail: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
    tasks: Dict[str, dict] = {}
    for srcs, is_policy in ((success_sources, False), (policy_sources, True)):
        for src in srcs:
            for path in src.paths:
                for rec in latest_records(path):
                    check_split(rec["task"])                              # hard error on eval splits
                    why = episode_drop_reason(rec)
                    tid = rec["task"]["id"]
                    tasks.setdefault(tid, rec["task"])
                    if why is None:
                        succ[tid].append((src.label, rec))
                        st["episodes"][src.label]["success"] += 1
                    elif is_policy and why in FAILED_REASONS and rec["trajectory"].get("steps"):
                        fail[tid].append((src.label, rec))
                        st["episodes"][src.label]["failed" + ("_unsafe" if rec["score"].get("unsafe") else "")] += 1
                    else:
                        st["episodes"][src.label]["ignored_" + (why or "?")] += 1
    # chosen pool by exact prompt: prompt -> [(action, reply, label)], and the set of good actions
    good: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = defaultdict(list)
    for tid, lst in succ.items():
        for label, rec in lst:
            for s in rec["trajectory"]["steps"]:
                if step_drop_reason(s) is None:
                    good[prompt_key(s)].append((action_of(s), s["reply"].strip(), label))
    good_actions = {k: {a for a, _, _ in v} for k, v in good.items()}

    pairs: Dict[Tuple, dict] = {}

    def add(task: dict, s_rej: dict, chosen_reply: str, chosen_label: str, rej_label: str, rec: dict,
            safety: bool, rule: str) -> None:
        key = prompt_key(s_rej)
        ca, ra = action_of({"reply": chosen_reply, "valid": False}), action_of(s_rej)
        ca = next((a for a, r, _ in good[key] if r == chosen_reply), ca)
        dk = (key, ca, ra)
        if dk in pairs:
            st["drops"]["duplicate"] += 1
            if safety and not pairs[dk]["meta"]["safety"]:
                pairs[dk]["meta"].update(safety=True, rule=rule, unsafe=list(rec["score"].get("unsafe") or []))
            return
        sysm, usr = s_rej["system"], s_rej["user"]          # the policy's own prompt text (its instance id)
        pairs[dk] = {
            "messages": [{"role": "system", "content": sysm}, {"role": "user", "content": usr},
                         {"role": "assistant", "content": chosen_reply}],
            "prompt": render_prompt(sysm, usr), "chosen": render_completion(chosen_reply),
            "rejected": render_completion(s_rej["reply"]), "completion": render_completion(chosen_reply),
            "meta": {"task_id": task["id"], "task_source": task.get("source"), "family": task.get("family"),
                     "kind": task.get("kind"), "split": task.get("split"), "step": int(s_rej.get("i", 0)),
                     "safety": safety, "rule": rule, "unsafe": list(rec["score"].get("unsafe") or []),
                     "chosen_src": chosen_label, "rejected_src": rej_label, "chosen_action": ca,
                     "rejected_action": ra, "rejected_valid": bool(s_rej.get("valid", True)),
                     "rejected_model": rec["trajectory"].get("model"), "sample": int(rec.get("sample", 0))}}

    for tid, flist in fail.items():
        task = tasks[tid]
        for flabel, frec in flist:
            fsteps = frec["trajectory"]["steps"]
            # 1. first divergence against every success of the same task
            for slabel, srec in succ.get(tid, []):
                ssteps = srec["trajectory"]["steps"]
                for k in range(min(len(fsteps), len(ssteps))):
                    fs, ss = fsteps[k], ssteps[k]
                    if prompt_key(fs) != prompt_key(ss):
                        st["drops"]["prompt_mismatch"] += 1
                        break
                    if action_of(fs) == action_of(ss):
                        continue
                    why = step_drop_reason(ss) or rejected_ok(fs)
                    if why:
                        st["drops"]["div_" + why] += 1
                    elif action_of(fs) in good_actions.get(prompt_key(fs), ()):
                        st["drops"]["rejected_also_succeeds"] += 1
                    else:
                        add(task, fs, ss["reply"].strip(), slabel, flabel, frec, False, "divergence")
                    break
                else:
                    st["drops"]["no_divergence"] += 1
            # 2. the step that made it unsafe vs a correct reply from that exact prompt
            if frec["score"].get("unsafe"):
                made = False
                for k, rule in trigger_steps(frec):
                    fs = fsteps[k]
                    alts = [(a, r, lab) for a, r, lab in good.get(prompt_key(fs), []) if a != action_of(fs)]
                    if alts and not rejected_ok(fs) and action_of(fs) not in good_actions.get(prompt_key(fs), ()):
                        a, r, lab = alts[0]
                        add(task, fs, r, lab, flabel, frec, True, rule)
                        made = True
                        break
                if not made:
                    st["drops"]["safety_no_chosen"] += 1

    rows = list(pairs.values())
    train = [r for r in rows if not task_is_val(r["meta"]["task_id"], val_frac, seed)]
    valid = [r for r in rows if task_is_val(r["meta"]["task_id"], val_frac, seed)]
    assert not ({r["meta"]["task_id"] for r in train} & {r["meta"]["task_id"] for r in valid})
    rng = random.Random(seed)
    rng.shuffle(train)
    if max_train_pairs and len(train) > max_train_pairs:
        safe = [r for r in train if r["meta"]["safety"]]
        rest = [r for r in train if not r["meta"]["safety"]]
        st["drops"]["over_cap"] += len(train) - max(max_train_pairs, len(safe))
        train = safe + rest[: max(0, max_train_pairs - len(safe))]
        rng.shuffle(train)

    def by(rs, f):
        return dict(Counter(f(r) for r in rs).most_common())

    stats = {"pairs_total": len(train) + len(valid), "train_pairs": len(train), "valid_pairs": len(valid),
             "safety_pairs": sum(r["meta"]["safety"] for r in train + valid),
             "safety_train": sum(r["meta"]["safety"] for r in train),
             "train_tasks": len({r["meta"]["task_id"] for r in train}),
             "valid_tasks": len({r["meta"]["task_id"] for r in valid}),
             "by_family": by(train + valid, lambda r: f"{r['meta']['task_source']}/{r['meta']['family']}"),
             "safety_by_rule": by([r for r in train + valid if r["meta"]["safety"]], lambda r: r["meta"]["rule"]),
             "by_chosen_src": by(train + valid, lambda r: r["meta"]["chosen_src"]),
             "by_rejected_src": by(train + valid, lambda r: r["meta"]["rejected_src"]),
             "rejected_invalid": sum(not r["meta"]["rejected_valid"] for r in train + valid),
             "tasks_with_failures": len(fail), "tasks_with_successes": len(succ),
             "episodes": {k: dict(v) for k, v in st["episodes"].items()}, "drops": dict(st["drops"]),
             "val_frac": val_frac, "seed": seed, "max_train_pairs": max_train_pairs}
    return train, valid, stats


def write(out: Path, train: List[dict], valid: List[dict], stats: Dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("valid", valid)):
        with open(out / f"{name}.jsonl", "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.pairs", description=__doc__.split("\n\n")[0])
    ap.add_argument("--success", action="append", default=[], help="LABEL=PATH: successes only (repeatable)")
    ap.add_argument("--policy", action="append", default=[], help="LABEL=PATH: policy rollouts (repeatable)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-train-pairs", type=int, default=0, help="cap train pairs (safety pairs always kept)")
    a = ap.parse_args(argv)
    if not a.policy:
        ap.error("at least one --policy source (the failed episodes) is needed")
    try:
        train, valid, stats = build([parse_input(s) for s in a.success], [parse_input(s) for s in a.policy],
                                    val_frac=a.val_frac, seed=a.seed, max_train_pairs=a.max_train_pairs)
    except EvalSplitError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2
    print(json.dumps(stats, indent=1))
    if not train:
        print("no preference pairs survived", file=sys.stderr)
        return 1
    write(a.out.expanduser(), train, valid, stats)
    print(f"-> {a.out}/train.jsonl ({len(train)}), valid.jsonl ({len(valid)}), stats.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
