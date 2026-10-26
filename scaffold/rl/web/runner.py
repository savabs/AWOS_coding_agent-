"""
runner.py — Online-Mind2Web rollouts on the live web, resumable and shardable, plus the summary.

    python -m scaffold.rl.web run --model local --split dev --shard 1/12 --out runs/web/qwen4b-dev-s1/
    python -m scaffold.rl.web run --model cloud --split dev --n 5 --out runs/web/haiku-dev/
    python -m scaffold.rl.web summarize runs/web/qwen4b-dev-s1/
    python -m scaffold.rl.web freeze-dev            # write data/dev_ids.json (needs the dataset)
    python -m scaffold.rl.web validate runs/web/x/judge/    # check the judge layout (schema v2 rules)

Output directory:
    trajectories.jsonl   one row per task: {"task", "sample", "trajectory", "score", "ts"} (harness format)
    judge/<task_id>/     result.json + trajectory/NNNN.png: what the official WebJudge reads
                         (python src/run.py --mode WebJudge_Online_Mind2Web_eval --trajectories_dir judge/)
    run.json             settings;  summary.json / summary.md: counts, blocked-by-kind, steps, tokens

Models: local (llama-server at AWOS_LOCAL_BASE_URL, default http://127.0.0.1:8080/v1, temperature
0; --base-url URL, repeatable, gives each worker process one of the URLs round-robin, e.g. one
llama-server per GPU), cloud (CloudTextModel; AWOS_CLOUD_MAX_USD spend cap, split across workers), scripted (tests:
task.meta["script"] through micro_agent.ScriptedModel). There is no oracle on the live web.
Resumable: tasks already written without an infra error (traj.error) are skipped.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scaffold.rl.contract import Score, TaskSpec, Trajectory
from scaffold.rl.harness.runner import read_records, record

from .episode import MAX_SECONDS, run_web_episode, validate_judge_task
from .source import MAX_STEPS, OnlineMind2WebSource, freeze_dev, load_entries, resolve_path


def make_model(kind: str, task: TaskSpec, args: dict) -> Tuple[Any, str]:
    if kind == "local":
        from scaffold.rl.harness.models import SampledLocalModel
        m = SampledLocalModel(temperature=0.0)
        return m, args.get("model_label") or m.model
    if kind == "cloud":
        from scaffold.rl.harness.models import CloudTextModel
        workers = max(1, int(args.get("workers") or 1))
        total = float(os.environ.get("AWOS_CLOUD_MAX_USD") or 5.0)
        m = CloudTextModel(max_usd=total / workers)        # each worker process gets its share of the cap
        return m, args.get("model_label") or m.model
    if kind == "scripted":
        from scaffold.agent.desktop.micro_agent import ScriptedModel
        script = task.meta.get("script")
        if not script:
            raise ValueError("--model scripted needs task.meta['script']")
        return ScriptedModel(script), args.get("model_label") or "scripted"
    raise SystemExit(f"unknown model {kind!r} (local | cloud | scripted)")


# ----------------------------------------------------------------- workers --

_W: Dict[str, Any] = {}


def worker_base_url(urls: List[str], index: int) -> str:
    """The llama-server URL of the index-th worker (0-based): round-robin over --base-url."""
    return urls[index % len(urls)]


def _worker_init(args: dict, counter=None) -> None:
    urls = args.get("base_urls") or []
    if urls:                                    # before the model is built: LocalGBNFModel reads the env
        idx = 0
        if counter is not None:
            with counter.get_lock():
                idx, counter.value = counter.value, counter.value + 1
        os.environ["AWOS_LOCAL_BASE_URL"] = worker_base_url(urls, idx)
    from scaffold.rl.harness.chromium import Chromium
    # AWOS_WEB_HEADFUL=1: an ordinary visible browser window (the residential-IP run in the sandbox VM; point
    # AWOS_RL_CHROMIUM at a full Chromium, not chrome-headless-shell). Nothing is disguised: no stealth, no
    # fingerprint or user-agent changes; it is the same browser a person would open.
    _W["chromium"] = Chromium(headless=os.environ.get("AWOS_WEB_HEADFUL") != "1", web=True)
    _W["args"] = args


def _worker_run(td: dict) -> dict:
    task = TaskSpec.from_dict(td)
    a = _W["args"]
    try:
        model, label = make_model(a["model"], task, a)
    except SystemExit:
        raise
    except Exception as e:
        traj = Trajectory(task_id=task.id, model=a.get("model_label") or a["model"],
                          error=f"model setup: {type(e).__name__}: {e}"[:500])
        return record(task, 0, traj, Score(success=False, notes="not run"))
    traj, score = run_web_episode(task, model, model_label=label, chromium=_W["chromium"],
                                  judge_root=Path(a["out"]) / "judge", max_steps=a["max_steps"],
                                  max_seconds=a["max_seconds"], guards=a.get("guards", "product"),
                                  allow_private=bool(a.get("allow_private")))
    return record(task, 0, traj, score)


def _worker_close() -> None:
    ch = _W.pop("chromium", None)
    if ch is not None:
        try:
            ch.close()
        except Exception:
            pass


def select_tasks(src: OnlineMind2WebSource, split: str, n: int = 0, task_ids: Optional[List[str]] = None,
                 shard: Optional[str] = None) -> List[TaskSpec]:
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
    return tasks


def run(out: Path, *, model: str = "local", split: str = "dev", shard: Optional[str] = None, n: int = 0,
        task_ids: Optional[List[str]] = None, tasks_json: Optional[str] = None, max_steps: int = MAX_STEPS,
        max_seconds: float = MAX_SECONDS, workers: int = 1, guards: str = "product",
        model_label: Optional[str] = None, allow_private: bool = False, dev_ids_path: Optional[str] = None,
        base_urls: Optional[List[str]] = None, log=print) -> Dict[str, Any]:
    out = Path(out)
    (out / "judge").mkdir(parents=True, exist_ok=True)
    path = out / "trajectories.jsonl"
    src = OnlineMind2WebSource(tasks_json, dev_ids_path=dev_ids_path)
    tasks = select_tasks(src, split, n, task_ids, shard)
    have = {r["task"]["id"] for r in read_records(path) if not (r.get("trajectory") or {}).get("error")}
    jobs = [t.to_dict() for t in tasks if t.id not in have]
    args = {"model": model, "guards": guards, "model_label": model_label, "workers": workers, "out": str(out),
            "max_steps": int(max_steps), "max_seconds": float(max_seconds), "allow_private": allow_private,
            "base_urls": list(base_urls or [])}
    meta = {"source": "online-mind2web", "dataset_file": src.path.name, "split": split, "shard": shard,
            "model": model, "model_label": model_label, "guards": guards, "max_steps": max_steps,
            "max_seconds": max_seconds, "tasks": len(tasks), "dev_ids_source": src.dev_ids_source or None}
    (out / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    log(f"{len(tasks)} tasks: {len(jobs)} to run ({len(tasks) - len(jobs)} already done) -> {path}")
    t0 = time.monotonic()
    with path.open("a") as fh:
        def emit(rec: dict, i: int) -> None:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            _progress(log, rec, i, len(jobs))
        if workers <= 1:
            _worker_init(args)
            try:
                for i, job in enumerate(jobs, 1):
                    emit(_worker_run(job), i)
            finally:
                _worker_close()
        else:
            ctx = mp.get_context("spawn")
            with ctx.Pool(workers, initializer=_worker_init, initargs=(args, ctx.Value("i", 0))) as pool:
                for i, rec in enumerate(pool.imap_unordered(_worker_run, jobs), 1):
                    emit(rec, i)
    summ = summarize(out)
    summ["run_seconds"] = round(time.monotonic() - t0, 2)
    return summ


def _harness(tr: dict) -> dict:
    return next((e for e in reversed(tr.get("events") or []) if e.get("event") == "harness"), {})


def _progress(log, rec: dict, i: int, total: int) -> None:
    tr = rec["trajectory"]
    h = _harness(tr)
    tag = "ERR" if tr.get("error") else ("BLOCKED:" + h["blocked"]["kind"] if h.get("blocked")
                                         else ("ANSWERED" if h.get("answer") is not None else "stopped"))
    log(f"[{i}/{total}] {rec['task']['id']} {tag} outcome={tr.get('outcome')} stop_by={h.get('stop_by')} "
        f"steps={len(tr.get('steps') or [])} {tr.get('seconds')}s"
        + (f" error={tr['error'][:120]}" if tr.get("error") else ""))


# ----------------------------------------------------------------- summary --

def summarize(out: Path) -> Dict[str, Any]:
    """Counts only: success on the live web is WebJudge's call (judge/), not ours. Blocked episodes
    count as failures in any score but are broken out so infrastructure loss is visible."""
    out = Path(out)
    latest: Dict[str, dict] = {}
    for r in read_records(out / "trajectories.jsonl"):
        latest[r["task"]["id"]] = r
    errors = [r for r in latest.values() if r["trajectory"].get("error")]
    good = [r for r in latest.values() if not r["trajectory"].get("error")]

    def block(rs: List[dict]) -> Dict[str, Any]:
        if not rs:
            return {"episodes": 0}
        hs = [_harness(r["trajectory"]) for r in rs]
        blocked = [h for h in hs if h.get("blocked")]
        d: Dict[str, Any] = {"episodes": len(rs),
                             "answered": sum(h.get("answer") is not None for h in hs),
                             "blocked": len(blocked),
                             "blocked_rate": round(len(blocked) / len(rs), 4),
                             "blocked_by_kind": dict(sorted(Counter(h["blocked"]["kind"] for h in blocked).items())),
                             "outcomes": dict(Counter(r["trajectory"].get("outcome") or "?" for r in rs)),
                             "stop_by": dict(Counter(h.get("stop_by", "?") for h in hs))}
        steps = [s for r in rs for s in r["trajectory"]["steps"]]
        if steps:
            d["model_steps"] = len(steps)
            d["steps_per_episode"] = round(len(steps) / len(rs), 2)
            d["invalid_action_rate"] = round(sum(not s["valid"] for s in steps) / len(steps), 4)
            d["model_ms_per_step"] = round(sum(s["ms"] for s in steps) / len(steps), 1)
        d["max_prompt_tokens_est"] = max([h.get("max_prompt_tokens_est") or 0 for h in hs] or [0])
        toks = [int((h.get("usage") or {}).get("prompt_tokens") or 0) + int((h.get("usage") or {}).get(
            "completion_tokens") or 0) or int(h.get("est_tokens") or 0) for h in hs]
        d["tokens_per_episode"] = round(sum(toks) / len(rs), 1)
        usd = sum(float((h.get("usage") or {}).get("usd") or 0.0) for h in hs)
        if usd:
            d["usd"] = round(usd, 4)
        d["seconds_per_episode"] = round(sum(float(r["trajectory"].get("seconds") or 0) for r in rs) / len(rs), 1)
        return d

    by_level: Dict[str, List[dict]] = defaultdict(list)
    for r in good:
        by_level[str((r["task"].get("meta") or {}).get("level") or "?")].append(r)
    summary: Dict[str, Any] = {
        "overall": block(good), "infra_errors": len(errors),
        "infra_error_examples": [r["trajectory"]["error"][:200] for r in errors[:5]],
        "by_level": {k: {kk: vv for kk, vv in block(v).items()
                         if kk in ("episodes", "answered", "blocked", "steps_per_episode")}
                     for k, v in sorted(by_level.items())},
        "blocked_tasks": [{"id": r["task"]["id"], "site": r["task"].get("family"),
                           **{k: _harness(r["trajectory"])["blocked"].get(k) for k in ("kind", "evidence")}}
                          for r in good if _harness(r["trajectory"]).get("blocked")],
        "judge_dir": str(out / "judge"),
        "note": "success is not scored here: run the official WebJudge over judge/ (blocked = failure)",
    }
    run_meta = out / "run.json"
    if run_meta.exists():
        summary["run"] = json.loads(run_meta.read_text())
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "summary.md").write_text(to_markdown(summary))
    return summary


def to_markdown(s: Dict[str, Any]) -> str:
    run = s.get("run") or {}
    lines = [f"# Online-Mind2Web rollouts: {run.get('split', '?')} / {run.get('model_label') or run.get('model', '?')}"
             + (f" / shard {run['shard']}" if run.get("shard") else ""), "",
             f"{s['note']}.", "", "| metric | value |", "|---|---|"]
    for k, v in s["overall"].items():
        lines.append(f"| {k} | {json.dumps(v) if isinstance(v, (dict, list)) else v} |")
    lines.append(f"| infra_errors | {s['infra_errors']} |")
    if s.get("by_level"):
        lines += ["", "## by level", "", "| level | episodes | answered | blocked | steps/episode |", "|---|---|---|---|---|"]
        for lv, v in s["by_level"].items():
            lines.append(f"| {lv} | {v.get('episodes', 0)} | {v.get('answered', '')} | {v.get('blocked', '')} | "
                         f"{v.get('steps_per_episode', '')} |")
    if s.get("blocked_tasks"):
        lines += ["", "## blocked (infrastructure)", "", "| task | site | kind | evidence |", "|---|---|---|---|"]
        for b in s["blocked_tasks"]:
            lines.append(f"| {b['id']} | {b.get('site')} | {b.get('kind')} | {str(b.get('evidence') or '')[:80]} |")
    return "\n".join(lines) + "\n"


def validate_dir(judge: Path) -> Dict[str, List[str]]:
    return {d.name: validate_judge_task(d) for d in sorted(Path(judge).iterdir()) if d.is_dir()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.web")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="roll out a model on Online-Mind2Web (live websites)")
    r.add_argument("--model", required=True, choices=["local", "cloud", "scripted"])
    r.add_argument("--model-label", default=None)
    r.add_argument("--split", default="dev", choices=["dev", "all"])
    r.add_argument("--shard", default=None, help="K/N: every N-th selected task starting at K (1-based)")
    r.add_argument("--n", type=int, default=0, help="first N selected tasks (0 = all)")
    r.add_argument("--task", action="append", default=None, help="only this task id (repeatable)")
    r.add_argument("--tasks-json", default=None, help="local Online_Mind2Web.json (default: AWOS_OM2W_JSON, "
                                                      "the cache, or a download with HF_TOKEN)")
    r.add_argument("--dev-ids", default=None, help="dev id file (default: scaffold/rl/web/data/dev_ids.json)")
    r.add_argument("--max-steps", type=int, default=MAX_STEPS)
    r.add_argument("--max-seconds", type=float, default=MAX_SECONDS, help="wall-clock cap per task")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--guards", default="product", choices=["product", "raw"])
    r.add_argument("--allow-private", action="store_true", help="tests only: allow loopback / private hosts")
    r.add_argument("--base-url", action="append", default=None,
                   help="llama-server URL for --model local (repeatable: worker i uses URL i mod n; "
                        "default AWOS_LOCAL_BASE_URL)")
    r.add_argument("--out", required=True)
    s = sub.add_parser("summarize", help="(re)write summary.md / summary.json for a run directory")
    s.add_argument("out")
    f = sub.add_parser("freeze-dev", help="write the fixed dev split ids from the dataset file")
    f.add_argument("--tasks-json", default=None)
    f.add_argument("--dest", default=None)
    v = sub.add_parser("validate", help="check a judge/ directory against the schema v2 rules")
    v.add_argument("judge")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        summ = run(Path(a.out), model=a.model, split=a.split, shard=a.shard, n=a.n, task_ids=a.task,
                   tasks_json=a.tasks_json, max_steps=a.max_steps, max_seconds=a.max_seconds, workers=a.workers,
                   guards=a.guards, model_label=a.model_label, allow_private=a.allow_private,
                   dev_ids_path=a.dev_ids, base_urls=a.base_url)
        print(to_markdown(summ))
        return 0
    if a.cmd == "summarize":
        print(to_markdown(summarize(Path(a.out))))
        return 0
    if a.cmd == "freeze-dev":
        from .source import DEV_IDS
        p = resolve_path(a.tasks_json)
        doc = freeze_dev(load_entries(p), Path(a.dest) if a.dest else DEV_IDS, source_file=p.name)
        print(f"dev split: {doc['size']} ids {doc['by_level']} -> {a.dest or DEV_IDS}")
        return 0
    if a.cmd == "validate":
        res = validate_dir(Path(a.judge))
        bad = {k: v for k, v in res.items() if v}
        print(f"{len(res)} task folders, {len(bad)} invalid")
        for k, v in bad.items():
            print(f"  {k}: {'; '.join(v)}")
        return 0 if not bad else 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
