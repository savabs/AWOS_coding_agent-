"""Run MacArena tasks directly on a disposable macOS VM (a GitHub-hosted macOS runner) — AWOS code, MIT.

    python scripts/macarena/run_tasks.py --macarena /tmp/MacArena --tasks scripts/macarena/pilot.json \
        --shard 0/10 --agent noop --out out/

Per task: preflight (apps present) -> reset (quit the task's apps) -> screen recording on ->
official pre_command -> before_action delay -> agent -> before_grading delay -> official
evaluator (ma_tasks.evaluate) -> screenshot -> recording off -> cleanup -> result JSON.

Guard: this executes the benchmark's own setup / grading shell scripts and (for awos-micro)
drives the UI, so it refuses unless kern.hv_vmm_present=1 AND (GITHUB_ACTIONS=true or
AWOS_MACARENA_ALLOW_GUEST=1). The owner's host Mac is never touched. ``--dry-run`` only prints.

Differences from MacArena's own runner (recorded, deliberate):
  * no snapshot revert: each workflow job is a fresh runner VM; tasks sharing a shard share it;
  * pre_command is waited for (bounded, 120 s) instead of fire-and-forget Popen;
  * /Users/admin (MacArena VM user) is rewritten to $HOME (GitHub runner user is ``runner``);
  * before_grading delays are capped (AWOS_MACARENA_MAX_GRADING_DELAY, default 30 s).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ma_agents as A          # noqa: E402
import ma_inventory as INV     # noqa: E402
import ma_tasks as T           # noqa: E402

LICENCE = {"macarena": "MIT (MacPaw)", "macosworld": "CC BY-NC 4.0 (showlab) - non-commercial",
           "osworld": "Apache-2.0 (XLANG)"}
NEVER_QUIT = {"Finder", "System Events", "Dock", "SystemUIServer", "Control Center", "Spotlight"}


def in_vm() -> bool:
    try:
        return subprocess.run(["sysctl", "-n", "kern.hv_vmm_present"], capture_output=True, text=True,
                              timeout=5).stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def guard() -> Optional[str]:
    if platform.system() != "Darwin":
        return "not macOS"
    if not in_vm():
        return "kern.hv_vmm_present != 1 (bare-metal Mac): refusing to run benchmark scripts / UI control"
    if os.environ.get("GITHUB_ACTIONS") != "true" and os.environ.get("AWOS_MACARENA_ALLOW_GUEST") != "1":
        return "not a GitHub runner; set AWOS_MACARENA_ALLOW_GUEST=1 inside a disposable guest VM"
    return None


def select_shard(ids: List[str], shard: str) -> List[str]:
    i, n = (int(x) for x in shard.split("/"))
    if not 0 <= i < n:
        raise ValueError(f"bad shard {shard}")
    return [t for k, t in enumerate(ids) if k % n == i]


def app_present(app: str) -> bool:
    try:
        return subprocess.run(["open", "-Ra", app], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def task_apps(task: T.Task, hints: Optional[dict] = None) -> List[str]:
    apps = set(INV.apps_of(task.raw))
    h = (hints or {}).get(task.id)
    if h and not h.startswith("("):
        apps.add(h)
    return sorted(a for a in apps if a not in NEVER_QUIT and a != "Terminal" and a != "Spotlight")


def quit_apps(apps: List[str]) -> None:
    for a in apps:
        if a in NEVER_QUIT:
            continue
        try:
            subprocess.run(["osascript", "-e", f'tell application "{a}" to quit'], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            subprocess.run(["pkill", "-x", a], capture_output=True)


class Recorder:
    def __init__(self, path: Path, max_s: int):
        self.path, self.proc = path, None
        self.logf = open(path.with_suffix(".log"), "w")
        try:
            # screencapture finalises the movie from a SIGINT dispatch source, which only works when
            # SIGINT is ignored (as for a bash background job); with the default action it just dies
            # (rc=-2, no file; run 2).
            self.proc = subprocess.Popen(["screencapture", "-v", "-V", str(max_s), "-x", str(path)],
                                         stdin=subprocess.DEVNULL, stdout=self.logf, stderr=subprocess.STDOUT,
                                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
            time.sleep(2.0)
        except OSError as e:
            self.logf.write(f"spawn failed: {e!r}\n")
            self.proc = None

    def stop(self) -> Optional[str]:
        if self.proc is None:
            return None
        early = self.proc.poll()
        if early is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.logf.write("killed after 60 s\n")
        else:
            self.logf.write(f"screencapture had already exited rc={early}\n")
        self.logf.write(f"rc={self.proc.returncode}\n")
        self.logf.close()
        return self.path.name if self.path.exists() else None


def run_one(task: T.Task, agent, out: Path, *, budget_s: float, max_grading_delay: float,
            record: bool, hints: Optional[dict] = None) -> dict:
    tdir = out / task.id
    tdir.mkdir(parents=True, exist_ok=True)
    res = {"task_id": task.id, "family": task.family, "category": task.category,
           "licence": LICENCE.get(task.family, "?"), "agent": agent.name, "status": "", "passed": False,
           "score": 0.0, "steps": 0, "phases": {}}
    t0 = time.time()
    apps = task_apps(task, hints)
    task.apps = apps                       # what awos-micro may act in
    res["apps"] = apps
    missing = [a for a in apps if not app_present(a)]
    res["missing_apps"] = missing
    if not task.list_evaluator:
        res["status"] = "skipped_osworld_evaluator"
        return res
    if missing:
        res["status"] = "skipped_app_missing"
        return res
    if agent.name == "awos-scripted-oracle" and A.reference_solution(task) is None:
        res["status"] = "skipped_no_reference"
        return res
    quit_apps(apps)
    rec = Recorder(tdir / "video.mov", int(budget_s + 120)) if record else None
    ph = res["phases"]
    s = time.time()
    res["pre"] = T.run_pre_command(task.pre_command)
    ph["pre_s"] = round(time.time() - s, 2)
    time.sleep(min(task.before_action_delay, 20))
    s = time.time()
    ctx = A.Ctx(out_dir=tdir, deadline=time.time() + budget_s)
    try:
        ar = agent.run(task, ctx)
    except Exception as e:                                   # the harness must outlive the agent
        ar = {"status": "agent_exception", "steps": 0, "error": repr(e)[:400]}
    ph["agent_s"] = round(time.time() - s, 2)
    res["agent_result"] = ar
    res["steps"] = int(ar.get("steps") or 0)
    time.sleep(min(task.before_grading_delay, max_grading_delay))
    s = time.time()
    ev = T.evaluate(task.evaluator)
    ph["eval_s"] = round(time.time() - s, 2)
    res["eval"] = ev
    res["score"] = ev["score"]
    res["passed"] = ev["score"] == 1.0
    res["status"] = "eval_error" if ev.get("error") else ("pass" if res["passed"] else "fail")
    subprocess.run(["screencapture", "-x", str(tdir / "final.png")], capture_output=True, timeout=30)
    if rec:
        res["video"] = rec.stop()
    quit_apps(apps)
    res["wall_s"] = round(time.time() - t0, 2)
    return res


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--macarena", type=Path, required=True, help="MacArena checkout")
    ap.add_argument("--tasks", type=Path, default=HERE / "pilot.json")
    ap.add_argument("--ids", help="comma-separated task ids (overrides --tasks)")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--agent", default=os.environ.get("AWOS_MACARENA_AGENT", "noop"))
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--budget", type=float, default=float(os.environ.get("AWOS_MACARENA_TASK_SECONDS", "600")))
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    root = a.macarena / "evaluation_examples" if (a.macarena / "evaluation_examples").is_dir() else a.macarena
    listed = json.loads(a.tasks.read_text())["tasks"]
    hints = {t["id"]: t.get("app", "") for t in listed}
    ids = a.ids.split(",") if a.ids else [t["id"] for t in listed]
    mine = select_shard(ids, a.shard)
    agents = [x.strip() for x in a.agent.split(",") if x.strip()]
    print(f"shard {a.shard}: {len(mine)} task(s) x agents {agents}")
    if a.dry_run:
        for tid in mine:
            t = T.load_task(root, tid)
            print(f"  {t.family}/{t.category}/{t.id} apps={task_apps(t, hints)}")
        return 0
    why = guard()
    if why:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    max_gd = float(os.environ.get("AWOS_MACARENA_MAX_GRADING_DELAY", "30"))
    rc = 0
    for name in agents:
        agent = A.make_agent(name)
        for tid in mine:
            try:
                task = T.load_task(root, tid)
                res = run_one(task, agent, a.out / name, budget_s=a.budget, max_grading_delay=max_gd,
                              record=not a.no_record, hints=hints)
            except Exception as e:
                res = {"task_id": tid, "agent": name, "status": "harness_error", "error": repr(e)[:400],
                       "passed": False}
                rc = 1
            res["shard"] = a.shard
            res["runner"] = {"os": os.environ.get("ImageOS", platform.mac_ver()[0]),
                             "image": os.environ.get("ImageVersion", "")}
            p = a.out / name / tid / "result.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(res, indent=1))
            print(f"[{name}] {tid[:8]} {res.get('status'):<22} score={res.get('score')} steps={res.get('steps')} "
                  f"wall={res.get('wall_s')}s missing={res.get('missing_apps')}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
