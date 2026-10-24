"""
first_wrong.py — first-wrong-step finder + correction data (plan items 0.2 + 0.3).

    ~/.awos/rl-venv/bin/python -m scaffold.rl.train.first_wrong \\
        --policy q4b=artifacts/rl-qwen3.5-4b-factory-train \\
        --out runs/rl/first_wrong/ [--source factory|miniwob] [--max-episodes 0] [--val-frac 0.1] \\
        [--seed 0] [--prompt-check strict|observation] [--max-checks 60] [--dry-run]

``--policy LABEL=PATH``: the policy's own rollouts (trajectories.jsonl or a tree of them, as
runner.py writes them, e.g. unpacked rl-eval / collect artifacts). Only FAILED episodes are used
(pairs.FAILED_REASONS). Train split only: every record goes through dataset.check_split AND is looked
up in the live source (factory catalog / MiniWoB holdout list), so a record whose split field was
edited cannot smuggle an ood family or holdout type in. Eval splits are a hard error (exit 2).
``--dry-run``: read + filter + count the work, start no browser. No paid API is ever called.

Method (deterministic; no model, no judge). For a failed trajectory with agent replies r_1..r_n and
the task's scripted oracle o_1..o_m (factory catalog steps / miniwob oracles.py):
  * state s_k = the environment after replaying r_1..r_k through the REAL harness (run_episode:
    same TaskSource, same seeded task, MicroAgent + headless Chromium, same guards as the original).
  * recoverable(s_k) <=> some oracle suffix o_j..o_m played from s_k reaches Score.success with no
    unsafe code (suffixes tried from the last one that worked: usually 1-2 episodes per state). A check
    in which OracleModel itself failed (its needs_user("oracle: ...") reply: target not in the observation,
    ambiguous, script exhausted) never counts, even where the verifier would pass it (pause tasks).
    A replay whose prompts differ from the recorded ones is "diverged" (environment or page-reader
    drift): the trajectory is reported, never labelled. Prompts are compared like pairs.prompt_key
    (exact text except the factory instance counter and MiniWoB's awos_ep episode id); --prompt-check observation compares only the
    goal line + the <observation> block (tolerates history-text changes, still pins control numbers).
  * the FIRST WRONG STEP is the smallest k with recoverable(s_{k-1}) and not recoverable(s_k).
    Shortcut: if the agent's action at step k equals the oracle's action from s_{k-1} in a
    successful check, that check already proves s_k recoverable (no new episode).
    Terminal / guard-ended steps (done, needs_user, step cap, MicroAgent's repeat or invalid stop)
    end the replay, so they are wrong whenever the state before them was still recoverable.
  * oracle continuations ("plans") = every suffix o_j..o_m, plus, on popup tasks, the popup dismissal
    o_1 followed by each later suffix. "Recoverable" therefore means recoverable BY THE SCRIPTED ORACLE:
    a detour that a human could undo with Back but the script cannot (wrong page, opened dialog) counts
    as wrong, and its correction is the oracle's move from the state before the detour.
  * correction = the oracle's reply at step k in the successful check from s_{k-1}: it is resolved
    against the same observation the agent saw (the replay prompt at step k is checked equal to the
    recorded one), so it is in the agent's exact text action language (GBNF form, no reason line).
    If MicroAgent rejected that reply (e.g. it repeats a fill the agent already made), another plan
    with an accepted action at step k is searched; none -> verdict no_valid_correction.
Category of the first wrong step (first rule that applies):
  unsafe          a check from s_k carries an unsafe code the s_{k-1} check did not (trap, checkpoint,
                  injection, side effect)
  invalid_action  the agent's reply did not parse / was rejected by MicroAgent
  gave_up_early   needs_user(...) on an act/answer task, or done(...) while the oracle still had work
  misread_goal    done(...) with a wrong answer where the oracle would also answer now, or fill/select
                  on the oracle's control with a different value
  loop            the same action on the same observation earlier in the run, or s_k is recoverable
                  once the step cap is lifted (the budget ran out; one extra check)
  wrong_control   anything else: acted on another control and the goal became unreachable
Verdicts per trajectory: labelled | diverged | no_oracle | oracle_fails_at_start | no_wrong_step
| no_valid_correction
(every state recoverable: nondeterminism or a time cap) | check_budget | skipped_<why>.

Outputs in --out:
  train.jsonl / valid.jsonl  correction rows, EXACTLY dataset.py's row schema
      {"messages": [system, user, assistant=correct reply], "prompt", "completion", "meta"}
      (meta = dataset.py's keys + correction fields), split by TASK ID with dataset.task_is_val
      (same --val-frac/--seed as the SFT build, so a val task never lands in train via --extra-rows)
  pairs.jsonl     the same steps as pairs.py rows (prompt / chosen / rejected / completion / messages /
                  meta, rule="first_wrong") for the DPO run
  verdicts.jsonl  one line per failed trajectory (verdict, step, category, checks used, reason)
  summary.json / summary.md   counts by verdict, category, family, task source; checks per episode
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from scaffold.rl.contract import TaskSpec
from scaffold.rl.train.chat import render_completion, render_prompt
from scaffold.rl.train.dataset import (EVAL_SPLITS, EvalSplitError, Example, check_split, episode_drop_reason,
                                       latest_records, parse_input, task_is_val)
from scaffold.rl.train.pairs import FAILED_REASONS, action_of, prompt_key

CATEGORIES = ("wrong_control", "gave_up_early", "loop", "misread_goal", "unsafe", "invalid_action")
EXTENDED_STEPS = 40                        # step cap for the "budget ran out?" check only

_STEP_LINE = re.compile(r"^Step \d+ of \d+\.$", re.M)
_OBS = re.compile(r"<observation[^>]*>.*?</observation>", re.S)
_IID = re.compile(r"/t/i\d+\b")
_EP = re.compile(r"awos_ep=[0-9a-f]+")         # MiniWoB's per-reset episode id in the start URL


# ------------------------------------------------------------------ replay --

@dataclass
class Replay:
    """One check episode: agent prefix then oracle suffix, as the environment played it."""
    steps: List[dict]                      # contract.Step dicts (system, user, reply, action, valid ...)
    success: bool
    unsafe: List[str] = field(default_factory=list)
    outcome: str = ""
    error: str = ""                        # infra failure: the check is unusable


class Env:
    """The environment a trajectory is replayed in. ``check`` resets the task, plays ``replies``
    verbatim, then the oracle ``suffix``; it must be deterministic for a seeded task."""

    def check(self, task: TaskSpec, replies: Sequence[str], suffix: Sequence[dict], *, guards: str,
              extended: bool = False) -> Replay:
        raise NotImplementedError


class PrefixThenOracle:
    """ActionModel: the recorded replies first (exact text), then harness.models.OracleModel."""
    output_format = "gbnf"

    def __init__(self, replies: Sequence[str], suffix: Sequence[dict]):
        from scaffold.rl.harness.models import OracleModel
        self.replies = list(replies)
        self.oracle = OracleModel(suffix)
        self.n = 0

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        self.n += 1
        if self.n <= len(self.replies):
            return self.replies[self.n - 1]
        return self.oracle.decide(system, user, grammar)


@contextlib.contextmanager
def _step_cap(n: Optional[int]):
    from scaffold.agent.desktop import micro_agent as MA
    if n is None:
        yield
        return
    old = MA.MAX_STEPS
    MA.MAX_STEPS = n
    try:
        yield
    finally:
        MA.MAX_STEPS = old


class HarnessEnv(Env):
    """The real thing: a contract TaskSource + headless Chromium through harness.episode.run_episode."""

    def __init__(self, source: Any, chromium: Any = None):
        self.source = source
        self.chromium = chromium
        self.episodes = 0
        self.seconds = 0.0

    def check(self, task, replies, suffix, *, guards, extended=False):
        from scaffold.rl.harness.episode import run_episode
        t = TaskSpec.from_dict(json.loads(json.dumps(task.to_dict())))
        if extended:
            t.max_steps = EXTENDED_STEPS
        t0 = time.monotonic()
        with _step_cap(EXTENDED_STEPS if extended else None):
            traj, sc = run_episode(self.source, t, PrefixThenOracle(replies, suffix), model_label="first_wrong",
                                   guards=guards, chromium=self.chromium)
        self.episodes += 1
        self.seconds += time.monotonic() - t0
        return Replay(steps=[asdict(s) for s in traj.steps], success=bool(sc.success), unsafe=list(sc.unsafe),
                      outcome=traj.outcome, error=traj.error)


# ------------------------------------------------------------- comparison --

def state_key(step: dict, mode: str = "strict", extended: bool = False) -> Tuple[str, str]:
    """What must be equal for two prompts to be the same state (see the module docstring)."""
    sysm, usr = prompt_key(step)
    usr = _EP.sub("awos_ep=*", usr)
    if extended:
        usr = _STEP_LINE.sub("Step N.", usr)
    if mode == "observation":
        goal = usr.splitlines()[0] if usr else ""
        m = _OBS.search(usr)
        return (sysm, goal + "\n" + (m.group(0) if m else usr))
    if mode != "strict":
        raise ValueError(f"prompt-check must be strict or observation, not {mode!r}")
    return (sysm, usr)


def _obs_of(step: dict) -> str:
    m = _OBS.search(_EP.sub("awos_ep=*", _IID.sub("/t/i*", step.get("user") or "")))
    return m.group(0) if m else ""


_ORACLE_FAIL = re.compile(r'^\s*needs_user\(\s*"oracle:')


def oracle_failed(reply: Optional[str]) -> bool:
    """True for harness.models.OracleModel's failure reply (control not found / ambiguous / script exhausted)."""
    return bool(_ORACLE_FAIL.match((reply or "").strip().splitlines()[-1] if (reply or "").strip() else ""))


def _verb(action: str) -> str:
    return action.split("(", 1)[0]


def _target(action: str) -> Optional[int]:
    m = re.match(r"\w+\((\d+)", action)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------- the finder --

@dataclass
class Verdict:
    task_id: str
    sample: int
    label: str                             # source label (--policy LABEL)
    verdict: str
    step: int = 0                          # 1-based step index of the first wrong step
    category: str = ""
    rule: str = ""
    wrong_action: str = ""
    correct_action: str = ""
    correct_reply: str = ""
    oracle_j: int = -1                     # index into plans_of() of the plan that succeeded from s_{k-1}
    checks: int = 0
    reason: str = ""


def plans_of(oracle: List[dict], task: TaskSpec) -> List[List[dict]]:
    """Oracle continuations tried from a state: every suffix o_j..o_m, then (popup tasks, whose first oracle
    step dismisses the banner) the dismissal followed by each later suffix, since an agent that acted
    first still has the popup to close."""
    plans = [oracle[j:] for j in range(len(oracle))]
    if "popup" in (task.tags or []) and len(oracle) > 2:
        plans += [oracle[:1] + oracle[j:] for j in range(2, len(oracle))]
    return plans


def _order(m: int, hint: int) -> List[int]:
    hint = max(0, min(hint, m - 1))
    return list(range(hint, m)) + list(range(hint - 1, -1, -1))


def find_first_wrong(rec: dict, env: Env, oracle: List[dict], *, label: str = "policy",
                     prompt_check: str = "strict", max_checks: int = 60) -> Verdict:
    """Locate the first wrong step of one failed record (runner.py row). Never trains on anything:
    the caller decides what to emit from the Verdict."""
    task = TaskSpec.from_dict(rec["task"])
    steps = rec["trajectory"]["steps"]
    n = len(steps)
    guards = next((e.get("guards") for e in reversed(rec["trajectory"].get("events") or [])
                   if e.get("event") == "harness" and e.get("guards")), "product")
    v = Verdict(task_id=task.id, sample=int(rec.get("sample", 0)), label=label, verdict="")
    if not oracle:
        v.verdict, v.reason = "no_oracle", "the source has no scripted oracle for this task"
        return v
    replies = [s.get("reply") or "" for s in steps]
    plans = plans_of(oracle, task)
    m = len(plans)

    def passed(rp: Replay, k: int) -> bool:
        """dataset.episode_drop_reason semantics: verified, safe, and pause <=> ended in needs_user. A check
        whose oracle-played steps (after the k agent replies) include OracleModel's own failure reply
        (needs_user("oracle: ...": target missing / script exhausted) never counts: on a pause task the
        verifier would accept it (state unchanged + needs_user), turning an oracle miss into a fake recovery."""
        if any(oracle_failed(s.get("reply")) for s in rp.steps[k:]):
            return False
        return rp.success and not rp.unsafe and ((rp.outcome == "needs_user") == (task.kind == "pause"))

    def diverged(rp: Replay, k: int, extended: bool = False) -> str:
        """'' if the replay of k agent replies reproduced the recorded prompts (steps 1..k+1)."""
        if rp.error:
            return "infra: " + rp.error[:200]
        upto = min(k + 1, n)
        if len(rp.steps) < min(k, n):
            return f"replay ended after {len(rp.steps)} steps, the recording continued to {n}"
        for i in range(min(upto, len(rp.steps))):
            if state_key(rp.steps[i], prompt_check, extended) != state_key(steps[i], prompt_check, extended):
                return f"prompt differs at step {i + 1}"
        return ""

    def recoverable(k: int, hint: int, extended: bool = False) -> Tuple[Optional[int], Optional[Replay], List[Replay], str]:
        """(j, the successful check, every check made, divergence) for state s_k."""
        made: List[Replay] = []
        for j in _order(m, hint):
            if v.checks >= max_checks:
                return None, None, made, "check_budget"
            rp = env.check(task, replies[:k], plans[j], guards=guards, extended=extended)
            v.checks += 1
            made.append(rp)
            why = diverged(rp, k, extended)
            if why:
                return None, None, made, why
            if passed(rp, k):
                return j, rp, made, ""
        return None, None, made, ""

    j, ok, _, why = recoverable(0, 0)
    if why:
        v.verdict, v.reason = ("check_budget" if why == "check_budget" else "diverged"), why
        return v
    if ok is None:
        v.verdict, v.reason = "oracle_fails_at_start", "no oracle suffix succeeds from the start state"
        return v
    for k in range(1, n + 1):
        agent = action_of(steps[k - 1])
        oracle_step = ok.steps[k - 1] if len(ok.steps) >= k else None
        if (oracle_step is not None and steps[k - 1].get("valid", True) and k < n
                and action_of(oracle_step) == agent and len(ok.steps) > k
                and state_key(ok.steps[k], prompt_check) == state_key(steps[k], prompt_check)):
            continue                       # the oracle did exactly this from s_{k-1}, and its next state is s_k
        j2, ok2, made, why = recoverable(k, j)
        if why:
            v.verdict, v.reason = ("check_budget" if why == "check_budget" else "diverged"), why
            return v
        if ok2 is not None:
            j, ok = j2, ok2
            continue
        # s_{k-1} recoverable, s_k not: step k is the first wrong step
        if oracle_step is None:
            v.verdict, v.reason = "diverged", f"successful check from s_{k - 1} has no step {k}"
            return v
        if not oracle_step.get("valid", True):
            # MicroAgent rejected the oracle's own move here (e.g. a repeat it skips): look for a plan
            # that succeeds from s_{k-1} with an accepted action at step k
            oracle_step = None
            for j3 in [x for x in _order(m, j) if x != j]:
                if v.checks >= max_checks:
                    break
                rp = env.check(task, replies[:k - 1], plans[j3], guards=guards)
                v.checks += 1
                if (not diverged(rp, k - 1) and passed(rp, k - 1) and len(rp.steps) >= k
                        and rp.steps[k - 1].get("valid", True)):
                    j, ok, oracle_step = j3, rp, rp.steps[k - 1]
                    break
            if oracle_step is None:
                v.verdict, v.step, v.wrong_action = "no_valid_correction", k, agent
                v.reason = f"step {k}: no oracle plan from s_{k - 1} has an accepted action there"
                return v
        v.step, v.wrong_action, v.oracle_j = k, agent, j
        v.correct_reply = (oracle_step.get("reply") or "").strip()
        v.correct_action = action_of(oracle_step)
        if oracle_failed(v.correct_reply):     # unreachable after passed(); never train on the oracle's debug text
            v.verdict, v.reason = "no_valid_correction", f"step {k}: the oracle's reply is its own failure message"
            return v
        if v.correct_action == agent:
            v.verdict, v.reason = "no_wrong_step", f"step {k}: the oracle's action equals the agent's (nondeterminism?)"
            return v
        v.category, v.rule = categorize(task, steps, k, oracle_step, ok, made)
        if not v.category:                 # budget? one extra check with the step cap lifted
            if v.checks < max_checks:
                _, okx, _, whyx = recoverable(k, j, extended=True)
                if okx is not None and not whyx:
                    v.category, v.rule = "loop", "budget: recoverable without the step cap"
            if not v.category:
                v.category, v.rule = "wrong_control", "goal unreachable after acting on another control"
        v.verdict = "labelled"
        return v
    v.verdict, v.reason = "no_wrong_step", "every replayed state stays recoverable (nondeterminism or a time cap)"
    return v


def categorize(task: TaskSpec, steps: List[dict], k: int, oracle_step: dict, ok_prev: Replay,
               made: List[Replay]) -> Tuple[str, str]:
    """(category, rule) by the docstring's rules; ('', '') = decide by the budget check."""
    s = steps[k - 1]
    agent, correct = action_of(s), action_of(oracle_step)
    new_unsafe = sorted({c for rp in made for c in rp.unsafe} - set(ok_prev.unsafe))
    if new_unsafe:
        return "unsafe", "unsafe after the step: " + ",".join(new_unsafe)
    if agent.startswith("INVALID:") or not s.get("valid", True):
        return "invalid_action", "reply did not parse or was rejected"
    va, vc = _verb(agent), _verb(correct)
    if va == "needs_user" and task.kind != "pause":
        return "gave_up_early", "needs_user on a task the oracle completes"
    if va == "done":
        if vc == "done":
            return "misread_goal", "done() with the wrong answer/summary where the oracle also finishes"
        return "gave_up_early", f"done() while the oracle still does {correct}"
    if va in ("fill", "select") and va == vc and _target(agent) == _target(correct):
        return "misread_goal", "right control, wrong value"
    obs = _obs_of(s)
    for i in range(k - 1):
        if action_of(steps[i]) == agent and _obs_of(steps[i]) == obs:
            return "loop", f"same action on the same observation as step {i + 1}"
    return "", ""


# ------------------------------------------------------- integrity + rows --

class TaskLookup:
    """Live split lookup: the record's own split field is not trusted alone."""

    def __init__(self, factory_splits: Optional[Dict[str, str]] = None, miniwob_holdout: Optional[Iterable[str]] = None):
        self.factory = factory_splits
        self.mw_holdout = set(miniwob_holdout) if miniwob_holdout is not None else None

    @staticmethod
    def default(sources: Iterable[str]) -> "TaskLookup":
        fs = hold = None
        if "factory" in sources:
            from scaffold.rl.factory.catalog import Catalog
            fs = {tid: e.task.split for tid, e in Catalog().entries.items()}
        if "miniwob" in sources:
            from scaffold.rl.miniwob import catalog as mwc
            hold = mwc.frozen_holdout()
        return TaskLookup(fs, hold)

    def check(self, task: dict) -> None:
        check_split(task)                                  # the recorded split (hard error)
        src, tid = task.get("source"), task.get("id", "")
        if src == "factory":
            if self.factory is None:
                raise EvalSplitError(f"{tid!r}: no factory catalog loaded to confirm its split")
            live = self.factory.get(tid)
            if live != "train":
                raise EvalSplitError(f"{tid!r}: the live factory catalog says split={live!r}")
        elif src == "miniwob":
            if self.mw_holdout is None:
                raise EvalSplitError(f"{tid!r}: no MiniWoB holdout list loaded to confirm its split")
            typ = (task.get("meta") or {}).get("type") or tid.split("/")[1]
            if typ in self.mw_holdout or tid.split("/")[1] in self.mw_holdout:
                raise EvalSplitError(f"{tid!r}: MiniWoB type {typ!r} is in the frozen holdout")
        else:
            raise EvalSplitError(f"{tid!r}: unknown task source {src!r} (fail closed)")


def correction_row(rec: dict, v: Verdict) -> dict:
    """dataset.py's row for (recorded prompt at step k) -> the oracle's reply."""
    s = rec["trajectory"]["steps"][v.step - 1]
    task = rec["task"]
    ex = Example(system=s["system"], user=s["user"], reply=v.correct_reply, meta={
        "src": f"first_wrong:{v.label}", "task_id": task["id"], "task_source": task.get("source"),
        "family": task.get("family"), "kind": task.get("kind"), "split": task.get("split"),
        "sample": int(rec.get("sample", 0)), "step": v.step, "model": rec["trajectory"].get("model"),
        "action": v.correct_action, "correction": True, "category": v.category, "rule": v.rule,
        "wrong_action": v.wrong_action, "wrong_valid": bool(s.get("valid", True)), "oracle_j": v.oracle_j})
    return ex.to_json()


def pair_row(rec: dict, v: Verdict) -> dict:
    s = rec["trajectory"]["steps"][v.step - 1]
    task = rec["task"]
    return {"messages": [{"role": "system", "content": s["system"]}, {"role": "user", "content": s["user"]},
                         {"role": "assistant", "content": v.correct_reply}],
            "prompt": render_prompt(s["system"], s["user"]), "chosen": render_completion(v.correct_reply),
            "rejected": render_completion(s.get("reply") or ""), "completion": render_completion(v.correct_reply),
            "meta": {"task_id": task["id"], "task_source": task.get("source"), "family": task.get("family"),
                     "kind": task.get("kind"), "split": task.get("split"), "step": v.step,
                     "safety": v.category == "unsafe", "rule": "first_wrong", "category": v.category,
                     "unsafe": list((rec.get("score") or {}).get("unsafe") or []), "chosen_src": "oracle",
                     "rejected_src": v.label, "chosen_action": v.correct_action, "rejected_action": v.wrong_action,
                     "rejected_valid": bool(s.get("valid", True)), "rejected_model": rec["trajectory"].get("model"),
                     "sample": int(rec.get("sample", 0))}}


def assert_train_only(rows: Iterable[dict], lookup: TaskLookup) -> None:
    """Last gate before anything is written: every row's task is train in the record AND live."""
    for r in rows:
        m = r["meta"]
        if m.get("split") in EVAL_SPLITS or m.get("split") != "train":
            raise EvalSplitError(f"row for {m.get('task_id')!r} has split={m.get('split')!r}")
        lookup.check({"id": m["task_id"], "source": m.get("task_source"), "split": m.get("split"),
                      "meta": {"type": m["task_id"].split("/")[1] if m.get("task_source") == "miniwob" else None}})


# ------------------------------------------------------------------- build --

def failed_records(sources, lookup: TaskLookup, st: Dict[str, Any]) -> List[Tuple[str, dict]]:
    out = []
    for src in sources:
        for path in src.paths:
            for rec in latest_records(path):
                lookup.check(rec["task"])                     # hard error on any eval task
                why = episode_drop_reason(rec)
                if why in FAILED_REASONS and rec["trajectory"].get("steps"):
                    out.append((src.label, rec))
                else:
                    st["skipped"]["success" if why is None else why] += 1
    return out


def build(sources, env_for: Callable[[str], Env], oracle_for: Callable[[dict], List[dict]], *,
          lookup: TaskLookup, val_frac: float = 0.1, seed: int = 0, prompt_check: str = "strict",
          max_checks: int = 60, max_episodes: int = 0, only_source: Optional[str] = None,
          log=print) -> Tuple[List[dict], List[dict], List[dict], List[Verdict], Dict[str, Any]]:
    """-> (train rows, valid rows, pair rows, verdicts, stats)."""
    st: Dict[str, Any] = {"skipped": Counter()}
    todo = failed_records(sources, lookup, st)
    if only_source:
        st["skipped"]["other_source"] += sum(r["task"].get("source") != only_source for _, r in todo)
        todo = [(lab, r) for lab, r in todo if r["task"].get("source") == only_source]
    if max_episodes:
        st["skipped"]["over_max_episodes"] += max(0, len(todo) - max_episodes)
        todo = todo[:max_episodes]
    verdicts: List[Verdict] = []
    train, valid, pairs = [], [], []
    seen = set()
    t0 = time.monotonic()
    for i, (label, rec) in enumerate(todo, 1):
        env = env_for(rec["task"]["source"])
        try:
            oracle = oracle_for(rec["task"])
        except Exception as e:
            oracle = []
            log(f"  oracle unavailable for {rec['task']['id']}: {type(e).__name__}: {e}")
        v = find_first_wrong(rec, env, oracle, label=label, prompt_check=prompt_check, max_checks=max_checks)
        verdicts.append(v)
        log(f"[{i}/{len(todo)}] {v.task_id}#{v.sample} {v.verdict}"
            + (f" step={v.step} {v.category}: {v.wrong_action} -> {v.correct_action}" if v.verdict == "labelled"
               else f" ({v.reason})") + f" checks={v.checks}")
        if v.verdict != "labelled":
            continue
        s = rec["trajectory"]["steps"][v.step - 1]
        key = (prompt_key(s), v.correct_action, v.wrong_action)
        if key in seen:
            st["skipped"]["duplicate_row"] += 1
            continue
        seen.add(key)
        row = correction_row(rec, v)
        (valid if task_is_val(v.task_id, val_frac, seed) else train).append(row)
        pairs.append(pair_row(rec, v))
    assert_train_only(train + valid + pairs, lookup)
    assert not ({r["meta"]["task_id"] for r in train} & {r["meta"]["task_id"] for r in valid})
    st.update(summarize(verdicts, todo))
    st.update({"train_rows": len(train), "valid_rows": len(valid), "pair_rows": len(pairs),
               "val_frac": val_frac, "seed": seed, "prompt_check": prompt_check, "max_checks": max_checks,
               "seconds": round(time.monotonic() - t0, 1), "skipped": dict(st["skipped"])})
    return train, valid, pairs, verdicts, st


def summarize(verdicts: List[Verdict], todo: List[Tuple[str, dict]]) -> Dict[str, Any]:
    lab = [v for v in verdicts if v.verdict == "labelled"]
    fam = {(r["task"]["id"], int(r.get("sample", 0))): f"{r['task'].get('source')}/{r['task'].get('family')}"
           for _, r in todo}
    by_fam: Dict[str, Counter] = defaultdict(Counter)
    for v in lab:
        by_fam[fam.get((v.task_id, v.sample), "?")][v.category] += 1
    steps = sorted(v.step for v in lab)
    return {"failed_episodes": len(verdicts), "labelled": len(lab),
            "by_verdict": dict(Counter(v.verdict for v in verdicts).most_common()),
            "by_category": {c: sum(v.category == c for v in lab) for c in CATEGORIES},
            "by_rule": dict(Counter(v.rule.split(":")[0] for v in lab).most_common()),
            "by_family": {f: dict(c) for f, c in sorted(by_fam.items())},
            "first_wrong_step": {"min": steps[0], "p50": steps[len(steps) // 2], "max": steps[-1]} if steps else {},
            "checks_total": sum(v.checks for v in verdicts),
            "checks_per_episode": round(sum(v.checks for v in verdicts) / len(verdicts), 2) if verdicts else 0.0}


def to_markdown(st: Dict[str, Any]) -> str:
    lab = st.get("labelled", 0)
    lines = ["# First wrong step: summary", "",
             f"failed episodes: {st.get('failed_episodes', 0)}, labelled: {lab}, rows: train {st.get('train_rows', 0)}"
             f" / valid {st.get('valid_rows', 0)}, checks: {st.get('checks_total', 0)} "
             f"({st.get('checks_per_episode', 0)} per episode), {st.get('seconds', 0)} s", "",
             "| category | count | share of labelled |", "|---|---|---|"]
    for c, k in (st.get("by_category") or {}).items():
        lines.append(f"| {c} | {k} | {k / lab:.0%} |" if lab else f"| {c} | {k} | - |")
    lines += ["", "| verdict | count |", "|---|---|"]
    lines += [f"| {k} | {n} |" for k, n in (st.get("by_verdict") or {}).items()]
    fams = st.get("by_family") or {}
    if fams:
        lines += ["", "| family | " + " | ".join(CATEGORIES) + " |", "|---|" + "---|" * len(CATEGORIES)]
        lines += [f"| {f} | " + " | ".join(str(c.get(x, 0)) for x in CATEGORIES) + " |" for f, c in fams.items()]
    if st.get("skipped"):
        lines += ["", "skipped records: " + json.dumps(st["skipped"])]
    return "\n".join(lines) + "\n"


def write(out: Path, train, valid, pairs, verdicts: List[Verdict], st: Dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("valid", valid), ("pairs", pairs)):
        with open(out / f"{name}.jsonl", "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "verdicts.jsonl", "w") as fh:
        for v in verdicts:
            fh.write(json.dumps(asdict(v), ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(json.dumps(st, indent=2) + "\n")
    (out / "summary.md").write_text(to_markdown(st))


# --------------------------------------------------------------------- CLI --

def _miniwob_source(recs: List[dict]):
    from scaffold.rl.miniwob.source import MiniWobSource
    types = sorted({r["task"]["meta"]["type"] for r in recs})
    seeds = sorted({int(r["task"]["meta"]["seed"]) for r in recs})
    return MiniWobSource(types=types, seeds=seeds)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.first_wrong", description=__doc__.split("\n\n")[0])
    ap.add_argument("--policy", action="append", required=True, help="LABEL=PATH: policy rollouts (repeatable)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--source", choices=["factory", "miniwob"], default=None, help="only this task source")
    ap.add_argument("--max-episodes", type=int, default=0, help="first N failed episodes (0 = all)")
    ap.add_argument("--max-checks", type=int, default=60, help="replay episodes per trajectory (cap)")
    ap.add_argument("--prompt-check", choices=["strict", "observation"], default="strict")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="count the work; start no browser")
    a = ap.parse_args(argv)
    sources = [parse_input(s) for s in a.policy]
    kinds = {r["task"].get("source") for s in sources for p in s.paths for r in latest_records(p)}
    try:
        lookup = TaskLookup.default(kinds)
        st0: Dict[str, Any] = {"skipped": Counter()}
        todo = failed_records(sources, lookup, st0)
    except EvalSplitError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2
    if a.source:
        todo = [x for x in todo if x[1]["task"].get("source") == a.source]
    if a.dry_run:
        n = len(todo[: a.max_episodes or None])
        steps = sum(len(r["trajectory"]["steps"]) for _, r in todo[: a.max_episodes or None])
        print(json.dumps({"failed_episodes": n, "agent_steps": steps, "skipped": dict(st0["skipped"]),
                          "by_source": dict(Counter(r["task"]["source"] for _, r in todo)),
                          "worst_case_checks": min(n * a.max_checks, steps * 3 + n * 2), "cost_usd": 0.0}, indent=1))
        return 0
    from scaffold.rl.harness.chromium import Chromium
    from scaffold.rl.harness.runner import load_source
    envs: Dict[str, HarnessEnv] = {}
    srcs: Dict[str, Any] = {}
    ch = Chromium()

    def env_for(name: str) -> Env:
        if name not in envs:
            src = _miniwob_source([r for _, r in todo if r["task"]["source"] == "miniwob"]) if name == "miniwob" \
                else load_source(name)
            src.start()
            srcs[name] = src
            envs[name] = HarnessEnv(src, ch)
        return envs[name]

    def oracle_for(task: dict) -> List[dict]:
        env_for(task["source"])
        return srcs[task["source"]].oracle(TaskSpec.from_dict(task))

    try:
        train, valid, pairs, verdicts, st = build(
            sources, env_for, oracle_for, lookup=lookup, val_frac=a.val_frac, seed=a.seed,
            prompt_check=a.prompt_check, max_checks=a.max_checks, max_episodes=a.max_episodes, only_source=a.source)
    except EvalSplitError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2
    finally:
        for s in srcs.values():
            with contextlib.suppress(Exception):
                s.stop()
        with contextlib.suppress(Exception):
            ch.close()
    st["episodes_run"] = sum(e.episodes for e in envs.values())
    st["episode_seconds"] = round(sum(e.seconds for e in envs.values()), 1)
    write(a.out.expanduser(), train, valid, pairs, verdicts, st)
    print(to_markdown(st))
    print(f"-> {a.out}/train.jsonl ({len(train)}), valid.jsonl ({len(valid)}), pairs.jsonl ({len(pairs)}), "
          "verdicts.jsonl, summary.json, summary.md")
    return 0 if (train or valid) else 1


if __name__ == "__main__":
    sys.exit(main())
