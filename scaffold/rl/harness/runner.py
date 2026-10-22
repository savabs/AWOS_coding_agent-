"""
runner.py — rollouts at scale, resumable, plus the summary.

    python -m scaffold.rl.harness run --source demo --model oracle --out runs/rl/demo-oracle/
    python -m scaffold.rl.harness run --source factory --split ood --model local --samples 4 \\
        --temperature 0.7 --workers 2 --out runs/rl/f-ood-4b/
    python -m scaffold.rl.harness summarize runs/rl/f-ood-4b/
    python -m scaffold.rl.harness admit --source factory --split train --out runs/rl/admit/

``trajectories.jsonl``: one JSON object per (task, sample):
    {"task": TaskSpec, "sample": j, "trajectory": Trajectory, "score": Score (+reward), "ts": ...}
Resumable: (task_id, sample) pairs already written without an infra error are skipped; rows
with traj.error are retried and the latest row per pair wins in the summary.

Sources are looked up lazily by name, so a missing package never breaks import:
    factory -> scaffold.rl.factory:FactorySource    miniwob -> scaffold.rl.miniwob:MiniWobSource
    demo    -> scaffold.rl.harness.demo:DemoSource  or any "package.module:Class"
Models: oracle (source.oracle), scripted (oracle steps through micro_agent.ScriptedModel, or
task.meta["script"]), local (llama-server at AWOS_LOCAL_BASE_URL via SampledLocalModel;
--temperature, --top-p, --seed; sample j uses seed+j).
Workers are processes; each owns one source instance (its own server) and one Chromium.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scaffold.rl.contract import Score, Step, TaskSpec, Trajectory

SOURCES = {"factory": "scaffold.rl.factory:FactorySource", "miniwob": "scaffold.rl.miniwob:MiniWobSource",
           "demo": "scaffold.rl.harness.demo:DemoSource"}


def load_source(name: str, kwargs: Optional[dict] = None) -> Any:
    ref = SOURCES.get(name, name)
    if ":" not in ref:
        raise SystemExit(f"unknown source {name!r} (known: {', '.join(SOURCES)} or module:Class)")
    mod, cls = ref.split(":", 1)
    try:
        m = importlib.import_module(mod)
    except ImportError as e:
        raise SystemExit(f"source {name!r} is not available ({mod}: {e})") from e
    return getattr(m, cls)(**(kwargs or {}))


def make_model(kind: str, source: Any, task: TaskSpec, sample: int, args: dict) -> Tuple[Any, str]:
    from scaffold.agent.desktop.micro_agent import ScriptedModel
    from .models import OracleModel, SampledLocalModel, oracle_to_script
    if kind == "oracle":
        return OracleModel(source.oracle(task)), args.get("model_label") or "oracle"
    if kind == "scripted":
        script = task.meta.get("script") or oracle_to_script(source.oracle(task))
        return ScriptedModel(script), args.get("model_label") or "scripted"
    if kind == "local":
        seed = args.get("seed")
        m = SampledLocalModel(temperature=float(args.get("temperature") or 0.0), top_p=args.get("top_p"),
                              seed=(None if seed is None else int(seed) + sample))
        return m, args.get("model_label") or m.model
    raise SystemExit(f"unknown model {kind!r}")


# --------------------------------------------------------------- records --

def record(task: TaskSpec, sample: int, traj: Trajectory, score: Score) -> dict:
    return {"task": task.to_dict(), "sample": sample, "trajectory": traj.to_dict(), "score": score.to_dict(),
            "ts": round(time.time(), 3)}


def traj_from_dict(d: dict) -> Trajectory:
    d = dict(d)
    d["steps"] = [Step(**s) for s in d.get("steps") or []]
    return Trajectory(**d)


def score_from_dict(d: dict) -> Score:
    return Score(success=bool(d.get("success")), unsafe=list(d.get("unsafe") or []),
                 progress=float(d.get("progress") or 0.0), notes=str(d.get("notes") or ""))


def read_records(path: Path) -> List[dict]:
    out = []
    if not path.exists():
        return out
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue                                   # a torn last line from an interrupted run
    return out


def done_keys(path: Path) -> set:
    return {(r["task"]["id"], int(r.get("sample", 0))) for r in read_records(path)
            if not (r.get("trajectory") or {}).get("error")}


# ----------------------------------------------------------------- workers --

_W: Dict[str, Any] = {}


def _worker_init(source_name: str, source_kw: dict, args: dict) -> None:
    from .chromium import Chromium
    _W["source"] = load_source(source_name, source_kw)
    _W["source"].start()
    _W["chromium"] = Chromium()
    _W["args"] = args


def _worker_run(job: Tuple[dict, int]) -> dict:
    from .episode import run_episode
    td, sample = job
    task = TaskSpec.from_dict(td)
    a = _W["args"]
    try:
        model, label = make_model(a["model"], _W["source"], task, sample, a)
    except SystemExit:
        raise
    except Exception as e:
        traj = Trajectory(task_id=task.id, model=a.get("model_label") or a["model"],
                          error=f"model setup: {type(e).__name__}: {e}"[:500])
        return record(task, sample, traj, Score(success=False, notes="not run"))
    traj, score = run_episode(_W["source"], task, model, model_label=label, guards=a.get("guards", "product"),
                              chromium=_W["chromium"])
    return record(task, sample, traj, score)


def _worker_close() -> None:
    for k in ("chromium", "source"):
        try:
            obj = _W.pop(k, None)
            if obj is not None:
                (obj.close if k == "chromium" else obj.stop)()
        except Exception:
            pass


def run(source_name: str, out: Path, *, model: str = "oracle", split: Optional[str] = None, n: int = 0,
        samples: int = 1, workers: int = 1, guards: str = "product", source_kw: Optional[dict] = None,
        temperature: float = 0.0, top_p: Optional[float] = None, seed: Optional[int] = None,
        model_label: Optional[str] = None, task_ids: Optional[List[str]] = None,
        shard: Optional[str] = None, log=print) -> Dict[str, Any]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "trajectories.jsonl"
    src = load_source(source_name, source_kw)
    tasks = list(src.tasks(split))
    if task_ids:
        tasks = [t for t in tasks if t.id in set(task_ids)]
    if n:
        tasks = tasks[:n]
    if shard:                                   # "K/N", 1-based, round-robin over the selected tasks
        k, m = (int(x) for x in shard.split("/"))
        if not 1 <= k <= m:
            raise ValueError(f"bad shard {shard!r}")
        tasks = tasks[k - 1::m]
    have = done_keys(path)
    jobs = [(t.to_dict(), j) for t in tasks for j in range(max(1, samples)) if (t.id, j) not in have]
    args = {"model": model, "guards": guards, "temperature": temperature, "top_p": top_p, "seed": seed,
            "model_label": model_label}
    meta = {"source": source_name, "split": split, "shard": shard, "model": model, "model_label": model_label, "samples": samples,
            "guards": guards, "temperature": temperature, "top_p": top_p, "seed": seed, "tasks": len(tasks)}
    (out / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    log(f"{len(tasks)} tasks x {samples} samples: {len(jobs)} to run ({len(have)} already done) -> {path}")
    t0 = time.monotonic()
    ran = 0
    with path.open("a") as fh:
        if workers <= 1:
            _worker_init(source_name, source_kw or {}, args)
            try:
                for job in jobs:
                    rec = _worker_run(job)
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
                    ran += 1
                    _progress(log, rec, ran, len(jobs))
            finally:
                _worker_close()
        else:
            ctx = mp.get_context("spawn")
            with ctx.Pool(workers, initializer=_worker_init, initargs=(source_name, source_kw or {}, args)) as pool:
                for rec in pool.imap_unordered(_worker_run, jobs):
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
                    ran += 1
                    _progress(log, rec, ran, len(jobs))
    try:
        src.stop()
    except Exception:
        pass
    summ = summarize(out)
    summ["run_seconds"] = round(time.monotonic() - t0, 2)
    return summ


def _progress(log, rec: dict, i: int, total: int) -> None:
    tr, sc = rec["trajectory"], rec["score"]
    tag = "ERR" if tr.get("error") else ("PASS" if sc["success"] and not sc["unsafe"] else "fail")
    log(f"[{i}/{total}] {rec['task']['id']}#{rec['sample']} {tag} outcome={tr.get('outcome')} "
        f"steps={len(tr.get('steps') or [])} unsafe={sc['unsafe']} {tr.get('seconds')}s"
        + (f" error={tr['error'][:120]}" if tr.get("error") else ""))


# ----------------------------------------------------------------- summary --

def _comb(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k (Chen et al. 2021): P(at least one of k samples passes)."""
    if n - c < k:
        return 1.0
    return 1.0 - _comb(n - c, k) / _comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench): P(all k samples pass), estimated without replacement."""
    return _comb(c, k) / _comb(n, k) if n >= k else float("nan")


def _harness(tr: dict) -> dict:
    return next((e for e in reversed(tr.get("events") or []) if e.get("event") == "harness"), {})


def _tokens(tr: dict) -> int:
    h = _harness(tr)
    u = h.get("usage") or {}
    t = int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
    return t or int(h.get("est_tokens") or 0)


def summarize(out: Path) -> Dict[str, Any]:
    out = Path(out)
    recs = read_records(out / "trajectories.jsonl")
    latest: Dict[Tuple[str, int], dict] = {}
    for r in recs:
        latest[(r["task"]["id"], int(r.get("sample", 0)))] = r
    errors = [r for r in latest.values() if r["trajectory"].get("error")]
    good = [r for r in latest.values() if not r["trajectory"].get("error")]

    def passed(r: dict) -> bool:
        return bool(r["score"]["success"]) and not r["score"]["unsafe"]

    by_task: Dict[str, List[dict]] = defaultdict(list)
    for r in good:
        by_task[r["task"]["id"]].append(r)
    ks = sorted({len(v) for v in by_task.values()})
    kmax = max(ks) if ks else 0

    def block(rs: List[dict]) -> Dict[str, Any]:
        if not rs:
            return {"episodes": 0}
        bt: Dict[str, List[dict]] = defaultdict(list)
        for r in rs:
            bt[r["task"]["id"]].append(r)
        p1 = sum(sum(passed(x) for x in v) / len(v) for v in bt.values()) / len(bt)
        d: Dict[str, Any] = {"tasks": len(bt), "episodes": len(rs), "pass@1": round(p1, 4),
                             "success_raw": round(sum(bool(r["score"]["success"]) for r in rs) / len(rs), 4)}
        if kmax > 1:
            full = {t: v for t, v in bt.items() if len(v) >= kmax}
            if full:
                d[f"pass@{kmax}"] = round(sum(pass_at_k(len(v), sum(map(passed, v)), kmax)
                                              for v in full.values()) / len(full), 4)
                d[f"pass^{kmax}"] = round(sum(pass_hat_k(len(v), sum(map(passed, v)), kmax)
                                              for v in full.values()) / len(full), 4)
        uc = Counter(code for r in rs for code in r["score"]["unsafe"])
        d["unsafe_episodes"] = sum(1 for r in rs if r["score"]["unsafe"])
        d["unsafe_by_code"] = dict(sorted(uc.items()))
        nu = [r for r in rs if r["trajectory"].get("outcome") == "needs_user"]
        d["needs_user_rate"] = round(len(nu) / len(rs), 4)
        d["needs_user_by"] = dict(Counter(_harness(r["trajectory"]).get("stop_by", "?") for r in nu))
        d["outcomes"] = dict(Counter(r["trajectory"].get("outcome") or "?" for r in rs))
        wins = [r for r in rs if passed(r)]
        if wins:
            d["steps_per_success"] = round(sum(len(r["trajectory"]["steps"]) for r in wins) / len(wins), 2)
            d["tokens_per_success"] = round(sum(_tokens(r["trajectory"]) for r in wins) / len(wins), 1)
        steps = [s for r in rs for s in r["trajectory"]["steps"]]
        if steps:
            d["invalid_action_rate"] = round(sum(not s["valid"] for s in steps) / len(steps), 4)
            d["model_ms_per_step"] = round(sum(s["ms"] for s in steps) / len(steps), 1)
        tm = [t for r in rs for t in (_harness(r["trajectory"]).get("timings") or [])]
        if tm:
            d["env_ms_per_op"] = round(sum(t["act_ms"] + t["settle_ms"] + t["observe_ms"] for t in tm) / len(tm), 1)
            d["observe_ms_per_op"] = round(sum(t["observe_ms"] for t in tm) / len(tm), 1)
        d["destructive_attempts"] = sum(len(_harness(r["trajectory"]).get("destructive_attempts") or []) for r in rs)
        return d

    def breakdown(key) -> Dict[str, Any]:
        groups: Dict[str, List[dict]] = defaultdict(list)
        for r in good:
            for g in key(r["task"]):
                groups[str(g)].append(r)
        return {g: {k: v for k, v in block(rs).items()
                    if k in ("tasks", "episodes", "pass@1", f"pass^{kmax}", "unsafe_episodes", "needs_user_rate")}
                for g, rs in sorted(groups.items())}

    summary: Dict[str, Any] = {"overall": block(good), "infra_errors": len(errors),
                               "infra_error_examples": [r["trajectory"]["error"][:200] for r in errors[:5]],
                               "samples_per_task": ks,
                               "by_family": breakdown(lambda t: [t.get("family") or "?"]),
                               "by_tag": breakdown(lambda t: t.get("tags") or ["(none)"]),
                               "by_aria": breakdown(lambda t: [(t.get("meta") or {}).get("aria")
                                                               or (t.get("meta") or {}).get("aria_level") or "?"]),
                               "by_kind": breakdown(lambda t: [t.get("kind") or "?"]),
                               "by_split": breakdown(lambda t: [t.get("split") or "?"])}
    run_meta = out / "run.json"
    if run_meta.exists():
        summary["run"] = json.loads(run_meta.read_text())
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "summary.md").write_text(to_markdown(summary))
    return summary


def to_markdown(s: Dict[str, Any]) -> str:
    o = s["overall"]
    run = s.get("run") or {}
    lines = [f"# Rollout summary: {run.get('source', '?')} / {run.get('split') or 'all'} / "
             f"{run.get('model_label') or run.get('model', '?')}", ""]
    if run:
        lines.append(f"guards={run.get('guards')} samples={run.get('samples')} temperature={run.get('temperature')}")
        lines.append("")
    lines += ["| metric | value |", "|---|---|"]
    for k, v in o.items():
        lines.append(f"| {k} | {json.dumps(v) if isinstance(v, (dict, list)) else v} |")
    lines.append(f"| infra_errors | {s['infra_errors']} |")
    for name in ("by_family", "by_tag", "by_aria", "by_kind", "by_split"):
        g = s.get(name) or {}
        if not g:
            continue
        cols = sorted({k for v in g.values() for k in v})
        lines += ["", f"## {name.replace('by_', 'by ')}", "", "| group | " + " | ".join(cols) + " |",
                  "|---|" + "---|" * len(cols)]
        for grp, v in g.items():
            lines.append(f"| {grp} | " + " | ".join(str(v.get(c, "")) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def run_admit(source_name: str, out: Optional[Path], split: Optional[str] = None, n: int = 0,
              source_kw: Optional[dict] = None, guards: str = "product", log=print) -> Dict[str, Any]:
    from .chromium import Chromium
    from .episode import admit
    src = load_source(source_name, source_kw)
    src.start()
    tasks = list(src.tasks(split))[: n or None]
    try:
        with Chromium() as ch:
            ok, bad = admit(src, tasks, chromium=ch, guards=guards)
    finally:
        src.stop()
    res = {"admitted": [t.id for t in ok], "rejected": [{"id": t.id, "reason": r} for t, r in bad]}
    log(f"admitted {len(ok)}/{len(tasks)}")
    for r in res["rejected"]:
        log(f"  REJECT {r['id']}: {r['reason']}")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "admission.json").write_text(json.dumps(res, indent=2) + "\n")
    return res


def _kv(items: Iterable[str]) -> dict:
    d = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            d[k] = json.loads(v)
        except ValueError:
            d[k] = v
    return d


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="roll out a model on a task source")
    r.add_argument("--source", required=True)
    r.add_argument("--source-arg", action="append", default=[], help="key=value passed to the source constructor")
    r.add_argument("--split", default=None)
    r.add_argument("--model", default="oracle", choices=["oracle", "scripted", "local"])
    r.add_argument("--model-label", default=None)
    r.add_argument("--n", type=int, default=0, help="first N tasks (0 = all)")
    r.add_argument("--task", action="append", default=None, help="only this task id (repeatable)")
    r.add_argument("--shard", default=None, help="K/N: every N-th selected task starting at K (1-based)")
    r.add_argument("--samples", type=int, default=1, help="samples per task (k for pass^k / RFT)")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--guards", default="product", choices=["product", "raw"])
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--top-p", type=float, default=None)
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--out", required=True)
    s = sub.add_parser("summarize", help="(re)write summary.md / summary.json for a run directory")
    s.add_argument("out")
    a_ = sub.add_parser("admit", help="run each task's oracle through the harness")
    a_.add_argument("--source", required=True)
    a_.add_argument("--source-arg", action="append", default=[])
    a_.add_argument("--split", default=None)
    a_.add_argument("--n", type=int, default=0)
    a_.add_argument("--guards", default="product", choices=["product", "raw"])
    a_.add_argument("--out", default=None)
    c = sub.add_parser("calibrate", help="dev_dashboard: Chromium AX path vs HTML path / FakeSafari")
    c.add_argument("--json", default=None)
    c.add_argument("--no-fake-safari", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        summ = run(a.source, Path(a.out), model=a.model, split=a.split, n=a.n, samples=a.samples,
                   workers=a.workers, guards=a.guards, source_kw=_kv(a.source_arg), temperature=a.temperature,
                   top_p=a.top_p, seed=a.seed, model_label=a.model_label, task_ids=a.task,
                   shard=a.shard)
        print(to_markdown(summ))
        return 0
    if a.cmd == "summarize":
        print(to_markdown(summarize(Path(a.out))))
        return 0
    if a.cmd == "admit":
        res = run_admit(a.source, Path(a.out) if a.out else None, a.split, a.n, _kv(a.source_arg), a.guards)
        return 0 if not res["rejected"] else 3
    if a.cmd == "calibrate":
        from .calibrate import main as cal_main
        return cal_main((["--json", a.json] if a.json else []) + (["--no-fake-safari"] if a.no_fake_safari else []))
    return 2


if __name__ == "__main__":
    sys.exit(main())
