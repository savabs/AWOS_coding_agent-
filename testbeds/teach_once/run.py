"""
run.py — teach-once runner skeleton.

    # validate the testbed (no agent, no model): no-op, naive replay, oracle
    python -m testbeds.teach_once.run --run-dir /tmp/teach_once --dry-run

    # an agent (NOT run by this skeleton unless you pass a command):
    python -m testbeds.teach_once.run --run-dir RUN --arm demo \
        --agent-cmd "python my_agent.py --brief {brief}" --provider local

Flow per task:
  1. record the demonstration ONCE on a fresh i01 (demonstrate.py) and compile
     it into a skill (compiler.py) -> <run>/demos/<task>/{trace.json, skill.json, skill.txt}
  2. for every instance (i01 = demo instance, i02..i09 held out): seed fresh,
     start all three web apps on 127.0.0.1 (only the task's apps may be used),
     write <visible>/brief/task.json, run the arm, stop the apps, verify.
  3. report: held-out success, failures by type, safety violations, steps, time.

Arms:
  noop          does nothing (the verifier must FAIL every instance)
  naive_replay  exact replay of the demo trace (baselines.naive_replay)
  oracle        scripted correct solution (baselines.oracle)
  agent         --agent-cmd, run as a subprocess with cwd = the fake home and
                env AWOS_TEACH_BRIEF=<brief/task.json>, AWOS_PROVIDER=<--provider>.
                This runner never calls a model itself.

Agent contract (brief/task.json): "home" (the only folder it may change),
"apps" {name: url} it may use, "other_apps" running but off-limits,
"context" {"today"}, "instruction" (instruction-only arm) or "note" +
"demonstration" (trace.json) + "skill" (skill.json / skill.txt) (demo arm),
"report" (path for an optional {"steps": n, "notes": "..."} JSON).
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from . import INSTANCES, TASKS
from .baselines import naive_replay, oracle
from .common import read_json, write_json
from .compiler import compile_trace, render_skill
from .demonstrate import record_demo
from .seed import INSTRUCTIONS, NOTES, TASK_APPS, Instance, seed_instance
from .trace import Trace
from .webapps import Servers

def _load_verifier():
    """Load verify.py by path: the judge is a standalone stdlib script, not part of the package graph."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("teach_once_verify", Path(__file__).resolve().parent / "verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


verifier = _load_verifier()

ArmFn = Callable[[Instance, Dict[str, str], Path], dict]


def prepare_demos(run_dir: Path, tasks=TASKS) -> Dict[str, Path]:
    out = {}
    for task in tasks:
        d = Path(run_dir) / "demos" / task
        d.mkdir(parents=True, exist_ok=True)
        trace = record_demo(run_dir, task, d / "trace.json")
        skill = compile_trace(trace)
        write_json(d / "skill.json", skill)
        (d / "skill.txt").write_text(render_skill(skill) + "\n", encoding="utf-8")
        out[task] = d
    shutil.rmtree(Path(run_dir) / "demo_scratch", ignore_errors=True)
    return out


def write_brief(I: Instance, urls: Dict[str, str], arm_kind: str, demo_dir: Optional[Path]) -> Path:
    I.brief.mkdir(parents=True, exist_ok=True)
    truth_ctx = read_json(I.truth_path)["context"]       # 'today' only: not secret
    brief = {"schema": "awos.teach_once.brief/1", "task": I.task, "instance": I.inst, "home": str(I.home),
             "apps": {a: urls[a] for a in TASK_APPS[I.task]},
             "other_apps": {a: u for a, u in urls.items() if a not in TASK_APPS[I.task]},
             "rules": "Change files only inside 'home'. Use only 'apps'. Never delete, archive or overwrite data.",
             "context": truth_ctx, "report": str(I.brief / "agent_report.json")}
    if arm_kind == "instruction":
        brief["instruction"] = INSTRUCTIONS[I.task]
    else:
        brief["note"] = NOTES[I.task]
        if demo_dir:
            for f in ("trace.json", "skill.json", "skill.txt"):
                shutil.copy(demo_dir / f, I.brief / f)
            brief.update({"demonstration": str(I.brief / "trace.json"), "skill": str(I.brief / "skill.json"),
                          "skill_text": str(I.brief / "skill.txt")})
    path = I.brief / "task.json"
    write_json(path, brief)
    return path


# ================================================================== arms ==

def arm_noop(I, urls, brief) -> dict:
    return {"ok": True, "actions": 0}


def arm_naive(I, urls, brief) -> dict:
    trace = Trace.load(I.brief / "trace.json")
    return naive_replay(trace, I.home, urls)


def arm_oracle(I, urls, brief) -> dict:
    return oracle(I.task, I.home, urls, read_json(brief)["context"])


def make_agent_arm(cmd_template: str, provider: Optional[str], timeout: int) -> ArmFn:
    def arm(I, urls, brief) -> dict:
        cmd = [part.format(brief=str(brief), home=str(I.home)) for part in shlex.split(cmd_template)]
        env = dict(os.environ, AWOS_TEACH_BRIEF=str(brief))
        if provider:
            env["AWOS_PROVIDER"] = provider
        try:
            p = subprocess.run(cmd, cwd=str(I.home), env=env, capture_output=True, text=True, timeout=timeout)
            rc, tail = p.returncode, (p.stdout[-2000:] + p.stderr[-2000:])
        except subprocess.TimeoutExpired:
            rc, tail = -9, "timeout"
        rep = I.brief / "agent_report.json"
        steps = read_json(rep).get("steps") if rep.exists() else None
        (I.brief / "agent_output.txt").write_text(tail, encoding="utf-8")
        return {"ok": rc == 0, "returncode": rc, "actions": steps}
    return arm


ARMS: Dict[str, ArmFn] = {"noop": arm_noop, "naive_replay": arm_naive, "oracle": arm_oracle}


def run_arm(run_dir: Path, arm: str, fn: ArmFn, demos: Dict[str, Path], tasks=TASKS, instances=INSTANCES,
            arm_kind: str = "demo") -> dict:
    run_dir = Path(run_dir)
    results = []
    for task in tasks:
        for inst in instances:
            I = seed_instance(run_dir, task, inst)
            t0 = time.monotonic()
            with Servers(I) as urls:
                brief = write_brief(I, urls, arm_kind, demos.get(task))
                try:
                    out = fn(I, urls, brief)
                except Exception as exc:          # an arm crash is a failed run, not a runner crash
                    out = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "actions": None}
            elapsed = time.monotonic() - t0
            v = verifier.verify_instance(I.hidden, I.home)
            v.update({"arm": arm, "arm_ok": out.get("ok"), "arm_error": out.get("error", ""),
                      "steps": out.get("actions") if out.get("actions") is not None else v["web_requests"],
                      "seconds": round(elapsed, 3)})
            results.append(v)
    summary = verifier.summarize(results)
    report = {"arm": arm, "arm_kind": arm_kind, "summary": summary, "results": results}
    write_json(run_dir / f"report_{arm}.json", report)
    return report


def print_report(rep: dict) -> None:
    print(f"\n=== arm: {rep['arm']} ===")
    for r in rep["results"]:
        tag = "demo" if r["is_demo"] else "held"
        print(f"  {r['task']:14} {r['instance']} {tag} {'PASS' if r['success'] else 'FAIL'}"
              f"  steps={r['steps']!s:>4} t={r['seconds']:.2f}s"
              + ("" if r["success"] else f"  [{r['failure_type']}] {r['failure_detail'][:110]}"))
    for task, s in rep["summary"].items():
        if task == "_all":
            continue
        print(f"  {task}: held-out {s['held_out_success']}/{s['held_out_n']}  demo={'PASS' if s['demo_success'] else 'FAIL'}"
              f"  failures={s['failures_by_type']}  safety_violations={s['safety_violations']}")


def dry_run(run_dir: Path, tasks=TASKS, instances=INSTANCES, quiet: bool = False) -> Dict[str, dict]:
    demos = prepare_demos(run_dir, tasks)
    reports = {arm: run_arm(run_dir, arm, ARMS[arm], demos, tasks, instances) for arm in ("noop", "naive_replay", "oracle")}
    if not quiet:
        for rep in reports.values():
            print_report(rep)
    return reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="teach-once testbed runner (no model calls)")
    ap.add_argument("--run-dir", default=None, help="default: a new temp dir")
    ap.add_argument("--dry-run", action="store_true", help="no-op + naive replay + oracle; validates the testbed")
    ap.add_argument("--agent-cmd", help="command template; {brief} and {home} are substituted")
    ap.add_argument("--arm", choices=("demo", "instruction"), default="demo")
    ap.add_argument("--provider", help="exported as AWOS_PROVIDER to the agent (e.g. local, claude-haiku-5-5)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--task", action="append", choices=TASKS)
    ap.add_argument("--instance", action="append", choices=INSTANCES)
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir or tempfile.mkdtemp(prefix="teach_once_"))
    tasks, instances = tuple(a.task or TASKS), tuple(a.instance or INSTANCES)
    print(f"run dir: {run_dir}")
    if a.dry_run:
        reps = dry_run(run_dir, tasks, instances)
        ok = (all(not r["success"] for r in reps["noop"]["results"])
              and all(r["success"] for r in reps["oracle"]["results"]))
        return 0 if ok else 1
    if not a.agent_cmd:
        ap.error("pass --dry-run or --agent-cmd")
    demos = prepare_demos(run_dir, tasks) if a.arm == "demo" else {}
    rep = run_arm(run_dir, f"agent_{a.arm}", make_agent_arm(a.agent_cmd, a.provider, a.timeout), demos, tasks,
                  instances, arm_kind=a.arm)
    print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
