#!/usr/bin/env python3
"""
capture.py — build EngineBench's FIXED, versioned workload of real model calls (docs/specs/enginebench.md).

    python scripts/engine/capture.py --exam DIR [--web DIR] [--out bench/engine]
    python scripts/engine/capture.py --exam DIR --check bench/engine/workload-v1.jsonl.gz   # digest unchanged?

Three kinds of call, each exactly as the product sends it (messages, GBNF grammar, max_tokens):

  agent   multi-step episodes, one call per step, in step order (so prefix reuse across steps is
          measurable). Source: trajectories.jsonl of the factory exam (rl-eval run 40090089187, run A
          final on the frozen ood split) and, optionally, the live-web dev run (40209008622; public
          sites, untrusted text: written to bench/engine/local/, which is gitignored).
          expected = the recorded action when the episode succeeded (a path known to verify); for a
          failed episode only ``reference`` (what the CI model said) is kept.
  repair  the shortlist prompts of scripts/repair_bench.py (TRAIN-split factory families, test split;
          exam families are never rendered): only the cases the rule tier hands to the model.
          expected = "PICK <n>" (truth in the shortlist) or "NOT_FOUND".
  verify  scaffold/rl/train/verifier_data.py exam rows (build_eval on the same exam run).
          expected = VERIFIED | NOT_DONE | UNSAFE.

File format (gzip JSON lines): line 1 {"header": {...}}; then one line per episode
{"id", "kind", "source", "calls": [{"id", "step", "messages", "grammar", "max_tokens", "expected",
"reference", "meta"}]}. Episodes are in a fixed, seeded order per kind, so a budget subset
(bench.py --budget) is always the same calls. ``header.digest`` hashes every call's request and answer.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "enginebench-v1"
AGENT_MAX_TOKENS = 200          # micro_agent.LocalGBNFModel
REPAIR_MAX_TOKENS = 64          # repair.model.LocalPicker
VERIFY_MAX_TOKENS = 64          # scripts/rl/eval_verifier.py default
EXAM_RUN = "40090089187"
WEB_RUN = "40209008622"
DEFAULT_LIMITS = {"agent": 36, "web_episodes": 24, "repair": 120, "verify": 60}


# ------------------------------------------------------------------ helpers --

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _order(ids: Iterable[str], seed: str) -> List[str]:
    """A fixed pseudo-random order (sha of seed + id): stable across Python versions and machines."""
    return sorted(ids, key=lambda i: _sha(f"{seed}:{i}"))


def norm_action(text: str) -> Optional[str]:
    """Agent reply -> canonical action (MicroAction.show(): reason line dropped), None if unparseable."""
    from scaffold.agent.desktop.micro_agent import ParseError, parse_text_action
    try:
        return parse_text_action(text or "").show()
    except ParseError:
        return None


def read_jsonl(path: Path) -> List[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def latest(paths: Iterable[Path]) -> List[dict]:
    """Latest row per (task id, sample) across files (runner.summarize semantics)."""
    rows: Dict[tuple, dict] = {}
    for p in paths:
        for r in read_jsonl(p):
            rows[(r["task"]["id"], int(r.get("sample", 0)))] = r
    return list(rows.values())


# ------------------------------------------------------------------- agent --

def agent_episodes(src_dir: Path, source: str, limit: Optional[int]) -> List[dict]:
    recs = latest(sorted(Path(src_dir).rglob("trajectories.jsonl")))
    eps = {}
    for r in recs:
        traj, score, task = r.get("trajectory") or {}, r.get("score") or {}, r["task"]
        steps = traj.get("steps") or []
        if not steps or traj.get("error"):
            continue
        ok = bool(score.get("success")) and not score.get("unsafe")
        eid = f"agent/{source}/{task['id']}#{int(r.get('sample', 0))}"
        calls = []
        for s in steps:
            ref = norm_action(s.get("reply") or "")
            calls.append({
                "id": f"{eid}/s{int(s['i']):02d}", "step": int(s["i"]),
                "messages": [{"role": "system", "content": s["system"]}, {"role": "user", "content": s["user"]}],
                "grammar": s.get("grammar") or None, "max_tokens": AGENT_MAX_TOKENS,
                "expected": ref if ok else None, "reference": ref,
                "meta": {"ci_ms": s.get("ms"), "valid": s.get("valid")}})
        eps[eid] = {"id": eid, "kind": "agent", "source": source, "calls": calls,
                    "meta": {"task_id": task["id"], "family": task.get("family"), "split": task.get("split"),
                             "success": ok, "outcome": traj.get("outcome"), "steps": len(calls)}}
    order = _order(eps, f"{VERSION}:{source}")
    return [eps[i] for i in order[:limit]]


# ------------------------------------------------------------------ repair --

def repair_episodes(limit: Optional[int], seed: int = 0, families: Optional[tuple] = None,
                    max_tasks: Optional[int] = None) -> List[dict]:
    """The shortlist prompts the rule tier hands to the model in scripts/repair_bench.py (test split)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import repair_bench as RB
    from scaffold.agent.desktop.repair import mutate as M
    from scaffold.agent.desktop.repair import model as RM
    from scaffold.agent.desktop.repair.core import Repairer
    from scaffold.rl.factory.htmldriver import parse

    cases = RB.collect(families or RB.TEST, max_tasks=max_tasks)
    calls_by_task: Dict[str, List[dict]] = {}
    box: Dict[str, Any] = {}

    def rec(fp, short):
        gt = box["gt"]
        idx = next((i for i, c in enumerate(short) if gt is not None and (c.ref is gt.ref or c is gt)), None)
        box["call"] = {"user": RM.prompt(fp, short), "expected": f"PICK {idx + 1}" if idx is not None else "NOT_FOUND",
                       "n": len(short)}
        return RM.Pick(idx, "capture", calls=1)

    rp = Repairer(rec)
    for case in cases:
        v0 = parse(case["html"])
        fp = M.ctl_fingerprint(v0, case["index"])
        for kind in M.MUTATIONS:
            rng = random.Random(f"{seed}:{case['task']}:{case['step']}:{case['index']}:{kind}")
            m = M.mutate(case["html"], case["index"], kind, rng)
            if m is None:
                continue
            cands = M.view_candidates(m.view)
            box["gt"], box["call"] = (cands[m.gt] if m.gt is not None else None), None
            if RB.strict_pick(fp, m.view) is not None:
                continue
            rp.refind(fp, cands)
            if box["call"]:
                c = box["call"]
                calls_by_task.setdefault(case["task"], []).append({
                    "step": case["step"], "mutation": kind, "user": c["user"], "expected": c["expected"],
                    "shortlist": c["n"]})
    flat = [(t, i) for t, cs in calls_by_task.items() for i in range(len(cs))]
    keep = set(_order([f"{t}|{i}" for t, i in flat], f"{VERSION}:repair")[:limit])
    eps = []
    for t in _order(calls_by_task, f"{VERSION}:repair-ep"):
        calls = []
        for i, c in enumerate(calls_by_task[t]):
            if f"{t}|{i}" not in keep:
                continue
            calls.append({"id": f"repair/{t}/{len(calls):02d}", "step": len(calls) + 1,
                          "messages": [{"role": "system", "content": RM.SYSTEM}, {"role": "user", "content": c["user"]}],
                          "grammar": RM.GRAMMAR, "max_tokens": REPAIR_MAX_TOKENS, "expected": c["expected"],
                          "reference": None, "meta": {"mutation": c["mutation"], "shortlist": c["shortlist"],
                                                      "case_step": c["step"]}})
        if calls:
            eps.append({"id": f"repair/{t}", "kind": "repair", "source": "repair_bench-test", "calls": calls,
                        "meta": {"task_id": t}})
    return eps


# ------------------------------------------------------------------ verify --

def _verify_grammar() -> str:
    spec = importlib.util.spec_from_file_location("eval_verifier", ROOT / "scripts" / "rl" / "eval_verifier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                      # type: ignore[union-attr]
    return mod.GRAMMAR


def verify_episodes(exam_dir: Path, limit: Optional[int]) -> List[dict]:
    from scaffold.rl.train.dataset import parse_input
    from scaffold.rl.train.verifier_data import build_eval
    exs, _st = build_eval([parse_input(f"a-final={exam_dir}")])
    gram = _verify_grammar()
    by_id = {}
    for e in exs:
        vid = f"verify/{e.meta.get('task_id')}#{e.meta.get('sample', 0)}"
        by_id[vid] = {"id": vid, "kind": "verify", "source": "verifier_data-exam",
                      "calls": [{"id": vid, "step": 1,
                                 "messages": [{"role": "system", "content": e.system}, {"role": "user", "content": e.user}],
                                 "grammar": gram, "max_tokens": VERIFY_MAX_TOKENS, "expected": e.meta["label"],
                                 "reference": None, "meta": {"family": e.meta.get("family"),
                                                             "hard": bool(e.meta.get("hard"))}}],
                      "meta": {"task_id": e.meta.get("task_id"), "label": e.meta["label"]}}
    # stratified: all failures first in the fixed order, capped so the mix keeps both classes
    order = _order(by_id, f"{VERSION}:verify")
    fails = [i for i in order if by_id[i]["meta"]["label"] != "VERIFIED"]
    oks = [i for i in order if by_id[i]["meta"]["label"] == "VERIFIED"]
    if limit is None:
        pick = order
    else:
        nf = min(len(fails), limit // 2)
        pick = fails[:nf] + oks[:limit - nf]
        pick = [i for i in order if i in set(pick)]
    return [by_id[i] for i in pick]


# --------------------------------------------------------------------- io --

def digest(episodes: List[dict]) -> str:
    h = hashlib.sha256()
    for ep in episodes:
        for c in ep["calls"]:
            h.update(json.dumps([c["id"], c["messages"], c["grammar"], c["max_tokens"], c["expected"]],
                                sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


def header(episodes: List[dict], recipe: dict) -> dict:
    kinds: Dict[str, Dict[str, int]] = {}
    for ep in episodes:
        k = kinds.setdefault(f"{ep['kind']}:{ep['source']}", {"episodes": 0, "calls": 0, "with_expected": 0})
        k["episodes"] += 1
        k["calls"] += len(ep["calls"])
        k["with_expected"] += sum(1 for c in ep["calls"] if c["expected"] is not None)
    return {"version": VERSION, "digest": digest(episodes), "calls": sum(len(e["calls"]) for e in episodes),
            "episodes": len(episodes), "by_source": kinds, "recipe": recipe}


def write(path: Path, episodes: List[dict], recipe: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    hd = header(episodes, recipe)
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8") as f:                 # mtime=0: byte-identical rebuilds
        f.write(json.dumps({"header": hd}, ensure_ascii=False) + "\n")
        for ep in episodes:
            f.write(json.dumps(ep, ensure_ascii=False) + "\n")
    return hd


def load(paths: Iterable[Path]) -> tuple:
    """-> (headers, episodes) from one or more workload files (.jsonl or .jsonl.gz)."""
    headers, eps = [], []
    for p in paths:
        p = Path(p)
        op = gzip.open if p.suffix == ".gz" else open
        with op(p, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if not line.strip():
                    continue
                d = json.loads(line)
                if i == 0 and "header" in d:
                    if d["header"].get("version") != VERSION:
                        raise ValueError(f"{p}: workload version {d['header'].get('version')!r} != {VERSION!r}")
                    headers.append(d["header"])
                    continue
                for k in ("id", "kind", "calls"):
                    if k not in d:
                        raise ValueError(f"{p}:{i + 1}: episode without {k!r}")
                eps.append(d)
    if headers:
        got = digest(eps) if len(headers) == 1 else None
        if got is not None and got != headers[0]["digest"]:
            raise ValueError(f"workload digest mismatch: file says {headers[0]['digest'][:12]}, content {got[:12]}")
    return headers, eps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--exam", type=Path, required=True,
                    help=f"dir with the factory-ood trajectories.jsonl of rl-eval run {EXAM_RUN} (gh run download)")
    ap.add_argument("--web", type=Path, help=f"dir with web-dev trajectories.jsonl of run {WEB_RUN} (untrusted)")
    ap.add_argument("--out", type=Path, default=ROOT / "bench" / "engine")
    for k, v in DEFAULT_LIMITS.items():
        ap.add_argument(f"--{k.replace('_', '-')}", type=int, default=v, help=f"max {k} (default {v})")
    a = ap.parse_args(argv)
    recipe = {"exam_run": EXAM_RUN, "exam_artifacts": "rl-a-final-factory-ood-s{1..4}of4",
              "repair": "scripts/repair_bench.py collect(TEST), rule tier ambiguous -> shortlist prompt",
              "verify": "verifier_data.build_eval on the exam run", "limits": {
                  k: getattr(a, k) for k in DEFAULT_LIMITS}, "command": "python scripts/engine/capture.py " +
              " ".join(sys.argv[1:] if argv is None else argv)}
    eps = agent_episodes(a.exam, "factory-ood", a.agent) + repair_episodes(a.repair) + verify_episodes(a.exam, a.verify)
    hd = write(a.out / "workload-v1.jsonl.gz", eps, recipe)
    print(json.dumps({k: hd[k] for k in ("version", "digest", "calls", "episodes", "by_source")}, indent=1))
    if a.web:
        web = agent_episodes(a.web, "web-dev", a.web_episodes)
        wr = dict(recipe, exam_run=None, web_run=WEB_RUN, note="public-site pages; local only, never committed")
        hw = write(a.out / "local" / "workload-v1-web.jsonl.gz", web, wr)
        print(json.dumps({k: hw[k] for k in ("digest", "calls", "episodes")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
