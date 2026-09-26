#!/usr/bin/env python3
"""
job_series.py — does AWOS get better at one project's work over a series of jobs?

Contract: docs/specs/compounding_proof_spec.md (piece C). A series lives in
tests/job_series/<series>/:

    base/                   the project at the start (package + tests/)
    jobs/NN_<slug>/
        task.json           {"id","goal","max_turns","max_cost_usd","timeout_min"}
        hidden_tests/       test_*.py, copied into the project's tests/ to judge
        reference/          files that, copied over the project, solve the job

Job N starts from base + the references of jobs 1..N-1 (cumulative).

    python3 scripts/job_series.py validate [--series ordertool]
    python3 scripts/job_series.py run [--series ordertool] [--arms off,on] [--jobs 1-12]
    python3 scripts/job_series.py run --dry-run      # everything but the model calls

`run` gives every arm its own empty state dir (the child's cwd, so every .awos
store starts empty and is private to that arm) and one persistent git project
(so the project id is stable across jobs). Arms interleave job by job
(off j1, on j1, off j2, ...), so a key dying mid-run leaves a paired prefix.
The only difference between arms is AWOS_NOTEBOOK=0|1; the model is pinned to
DeepSeek V4 Flash in both (see _pin_model).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERIES_ROOT = REPO / "tests" / "job_series"
PY = sys.executable

PINNED_MODEL = "deepseek/deepseek-v4-flash"
# Escalation ladder ids hidden from the router inside the child, so every
# decision (heuristic, retry escalation, LinUCB, performance veto) lands on
# Flash. MODELS_UNAVAILABLE is the ladder's own "never pick this" filter.
BLOCKED_LADDER_IDS = ("deepseek-v4-pro",)

# Both arms get exactly these. AWOS_NOTEBOOK is the only per-arm variable.
PIN_ENV = {
    "AWOS_AGENT_MODEL": PINNED_MODEL,       # fallback id for build_client_from_env / check_backend
    "AWOS_NOTEBOOK_MODEL": PINNED_MODEL,    # the notebook's update call
    "AWOS_GOAL_CHECK": "0",                 # hidden tests judge; saves ~$0.13/job
}


# ── Series discovery and staging ──────────────────────────────────────────────


def series_dir(series: str, root: Path | None = None) -> Path:
    return (root or SERIES_ROOT) / series


def discover_jobs(sdir: Path) -> list[tuple[int, Path]]:
    """[(number, job_dir)] ordered by the NN_ prefix."""
    jobs = []
    for task in (sdir / "jobs").glob("*/task.json"):
        m = re.match(r"(\d+)", task.parent.name)
        if m:
            jobs.append((int(m.group(1)), task.parent))
    return sorted(jobs)


def load(job_dir: Path) -> dict:
    return json.loads((job_dir / "task.json").read_text(encoding="utf-8"))


def parse_jobs(spec: str | None, available: list[int]) -> list[int]:
    """'1-6,9' -> [1..6, 9], restricted to jobs that exist."""
    if not spec:
        return list(available)
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            wanted.update(range(int(lo), int(hi) + 1))
        else:
            wanted.add(int(part))
    return [n for n in available if n in wanted]


def overlay(src: Path, dest: Path) -> None:
    for f in src.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            target = dest / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)


def materialize(sdir: Path, jobs: list[tuple[int, Path]], number: int, dest: Path,
                with_own_reference: bool = False) -> Path:
    """Write job `number`'s start state (base + references 1..N-1) into dest.

    dest may already hold a checkout: everything except .git is removed first.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for child in dest.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    overlay(sdir / "base", dest)
    for n, job_dir in jobs:
        if n < number or (with_own_reference and n == number):
            overlay(job_dir / "reference", dest)
    return dest


def add_hidden_tests(job_dir: Path, project: Path) -> list[str]:
    """Copy hidden_tests/ into project/tests/; return the copied test paths."""
    tests = project / "tests"
    tests.mkdir(exist_ok=True)
    copied = []
    src_root = job_dir / "hidden_tests"
    for f in sorted(src_root.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            rel = f.relative_to(src_root)
            target = tests / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            if f.name.startswith("test_") and f.suffix == ".py":
                copied.append(str(Path("tests") / rel))
    return copied


_FAIL_LINE = re.compile(r"^(FAILED|ERROR) (\S+)")


def pytest(project: Path, *paths: str, timeout: int = 600) -> dict:
    """Run pytest -rf; return counts and the failing test ids."""
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", *paths],
            cwd=project, capture_output=True, text=True, timeout=timeout,
        )
        out = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "ok": False,
                "failing": [], "summary": "pytest timed out"}
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)
    counts = {k: 0 for k in ("passed", "failed", "errors")}
    for num, word in re.findall(r"(\d+) (passed|failed|errors?)", out):
        counts["errors" if word.startswith("error") else word] += int(num)
    failing = []
    for ln in out.splitlines():
        m = _FAIL_LINE.match(ln.strip())
        if m and m.group(2) not in failing:
            failing.append(m.group(2))
    summary = next((ln for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
    return {**counts, "ok": code == 0, "failing": failing, "summary": summary[:160]}


def judge(job_dir: Path, project: Path) -> tuple[dict, dict]:
    """(hidden, visible). Visible runs first, before the hidden tests exist."""
    visible = (pytest(project, "tests") if (project / "tests").is_dir()
               else {"ok": True, "passed": 0, "failed": 0, "errors": 0, "failing": [], "summary": "-"})
    hidden_paths = add_hidden_tests(job_dir, project)
    hidden = pytest(project, *hidden_paths) if hidden_paths else {
        "ok": False, "passed": 0, "failed": 0, "errors": 0, "failing": [], "summary": "no hidden tests"}
    return hidden, visible


# ── validate ──────────────────────────────────────────────────────────────────

TASK_KEYS = ("id", "goal", "max_turns", "max_cost_usd", "timeout_min")


def validate(series: str, root: Path | None = None) -> int:
    sdir = series_dir(series, root)
    if not (sdir / "base").is_dir():
        print(f"[job_series] no series at {sdir} (missing base/)")
        return 1
    jobs = discover_jobs(sdir)
    if not jobs:
        print(f"[job_series] {sdir}/jobs has no jobs")
        return 1
    bad = invalid = 0
    numbers = [n for n, _ in jobs]
    if numbers != list(range(1, len(jobs) + 1)):
        print(f"  jobs are not numbered 1..{len(jobs)}: {numbers}")
        bad += 1
    for n, job_dir in jobs:
        problems = []
        missing = [p for p in ("hidden_tests", "reference") if not (job_dir / p).is_dir()]
        if missing:
            problems.append(f"missing {missing}")
        try:
            spec = load(job_dir)
            absent = [k for k in TASK_KEYS if k not in spec]
            if absent:
                problems.append(f"task.json lacks {absent}")
        except ValueError as exc:
            problems.append(f"task.json unreadable: {exc}")
        if missing:
            bad += 1
            invalid += 1
            print(f"  {n:>2} {job_dir.name:<30} INVALID")
            for p in problems:
                print(f"      - {p}")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            start = materialize(sdir, jobs, n, Path(tmp) / "start")
            h_start, v_start = judge(job_dir, start)
            solved = materialize(sdir, jobs, n, Path(tmp) / "solved", with_own_reference=True)
            h_solved, v_solved = judge(job_dir, solved)
        if h_start["ok"]:
            problems.append("hidden tests PASS on the start state (measures nothing)")
        if not v_start["ok"]:
            problems.append(f"visible tests fail on the start state ({v_start['summary']})")
        if not h_solved["ok"]:
            problems.append(f"hidden tests fail with the reference ({h_solved['summary']}; "
                            f"failing {h_solved['failing'][:5]})")
        if not v_solved["ok"]:
            problems.append(f"reference breaks the visible tests ({v_solved['summary']})")
        bad += bool(problems)
        invalid += bool(problems)
        print(f"  {n:>2} {job_dir.name:<30} {'ok' if not problems else 'INVALID':<8} "
              f"start: {h_start['summary']} | reference: {h_solved['summary']}", flush=True)
        for p in problems:
            print(f"      - {p}")
    print(f"[job_series] {len(jobs) - invalid}/{len(jobs)} jobs valid")
    return 1 if bad else 0


# ── the child (runs with cwd = the arm's state dir) ───────────────────────────


def _pin_model() -> list[str]:
    """Block every ladder rung but Flash in whichever copies of the module load."""
    import importlib

    allowed: list[str] = []
    for name in ("scaffold.agent.escalation_engine", "escalation_engine"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        mod.MODELS_UNAVAILABLE.update(BLOCKED_LADDER_IDS)
        allowed = [s.model_id for s in mod.LADDER if s.model_id not in mod.MODELS_UNAVAILABLE]
    return allowed


def run_child(job_dir: Path, project: Path) -> None:
    """Hand the goal to a fresh Orchestrator, as scripts/long_tasks.py does."""
    # cwd is the arm's state dir, so the repo is put on the path explicitly.
    sys.path[:0] = [str(REPO), str(REPO / "scaffold"), str(REPO / "scaffold" / "agent")]
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")   # the arm's copy; never overrides the pins
    spec = load(job_dir)
    os.environ.update({
        "AWOS_EXECUTOR": "agent_loop",
        "AWOS_SAFE_TO_RUN_TESTS": "1",
        "AWOS_USE_WORKTREE": "false",
        "AWOS_ENABLE_CLARIFICATION": "false",
        "AWOS_ENABLE_PLAN_REVIEW": "false",
        "AWOS_MAX_RUN_COST": str(spec.get("max_cost_usd", 1.0)),
        "AWOS_AGENT_MAX_TURNS": str(spec.get("max_turns", 150)),
        "AWOS_SANDBOX": os.environ.get("AWOS_SANDBOX", "auto"),
        **PIN_ENV,
    })
    allowed = _pin_model()
    from scaffold.agent.orchestrator import Orchestrator

    allowed = _pin_model() or allowed  # again, in case the fallback import path loaded
    print(f"[job_series] child cwd={Path.cwd()} notebook={os.environ.get('AWOS_NOTEBOOK')} "
          f"goal_check={os.environ.get('AWOS_GOAL_CHECK')} ladder={allowed}", flush=True)
    report = Orchestrator().execute_feature(
        goal=spec["goal"],
        codebase_root=str(project),
        pre_planned_tasks=None,
        auto_approve_plan=True,
    )
    (Path.cwd() / "report.json").write_text(json.dumps({
        k: report.get(k) for k in ("success", "tasks_completed", "tasks_failed")
    }), encoding="utf-8")


def run_dry_child(job_dir: Path, project: Path) -> None:
    """--dry-run stand-in for the orchestrator: touches nothing, calls nothing."""
    print(f"[job_series] dry-run child cwd={Path.cwd()} project={project} "
          f"notebook={os.environ.get('AWOS_NOTEBOOK')} goal_check={os.environ.get('AWOS_GOAL_CHECK')} "
          f"model={os.environ.get('AWOS_AGENT_MODEL')}", flush=True)
    (Path.cwd() / "report.json").write_text(json.dumps({"success": None}), encoding="utf-8")


# ── run ───────────────────────────────────────────────────────────────────────


def find_env_file(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    candidates = [REPO / ".env"]
    try:
        common = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            candidates.append((REPO / common).resolve().parent / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    return next((c for c in candidates if c.is_file()), None)


def read_ledger(state: Path) -> list:
    try:
        data = json.loads((state / ".awos" / "budget.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def read_spans(state: Path) -> list:
    """Per-task spans; attempt_count is the agent loop's turns for that task."""
    try:
        lines = (state / ".awos" / "spans.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    spans = []
    for line in lines:
        try:
            spans.append(json.loads(line))
        except ValueError:
            continue
    return spans


def ledger_metrics(entries: list) -> dict:
    return {
        "llm_calls": len(entries),
        "input_tokens": sum(int(e.get("input_tokens", 0) or 0) for e in entries),
        "output_tokens": sum(int(e.get("output_tokens", 0) or 0) for e in entries),
        "cost_usd": round(sum(float(e.get("cost", 0) or 0) for e in entries), 6),
        "notebook_cost_usd": round(sum(float(e.get("cost", 0) or 0) for e in entries
                                       if e.get("request_type") == "notebook"), 6),
        "models": sorted({str(e.get("model")) for e in entries if e.get("model")}),
    }


def notebook_chars(state: Path) -> int:
    return sum(len(p.read_text(encoding="utf-8", errors="replace"))
               for p in (state / ".awos" / "projects").glob("*/notebook.md"))


def backend_ok() -> bool:
    """One tiny model call on the pinned model; a dead key is not an agent failure."""
    proc = subprocess.run(
        [PY, str(REPO / "scripts" / "check_backend.py")],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONUNBUFFERED": "1", **PIN_ENV},
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
        print(f"[job_series] backend check failed:\n{tail}", flush=True)
        return False
    return True


def git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=bench@awos", "-c", "user.name=bench", *args],
        capture_output=True, text=True, check=check,
    )


def stream_child(cmd: list[str], cwd: Path, env: dict, log_path: Path, prefix: str,
                 timeout_s: float) -> bool:
    """Run cmd, teeing each line to log_path and stdout with prefix. True on timeout."""
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== {prefix} {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        log.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                errors="replace", start_new_session=True)

        started = time.monotonic()

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                # Elapsed time on every line shows where a job's minutes go.
                secs = int(time.monotonic() - started)
                line = f"+{secs // 60:02d}:{secs % 60:02d} {line}"
                log.write(line)
                log.flush()
                sys.stdout.write(f"{prefix} {line}")
                sys.stdout.flush()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
        reader.join(timeout=10)
        if timed_out:
            msg = f"[job_series] hit the {timeout_s / 60:.0f}-minute limit\n"
            log.write(msg)
            sys.stdout.write(f"{prefix} {msg}")
    return timed_out


def run_job(arm: str, number: int, job_dir: Path, sdir: Path, jobs: list, arm_dir: Path,
            dry_run: bool) -> dict:
    spec = load(job_dir)
    state, project = arm_dir / "state", arm_dir / "project"
    prefix = f"[{arm} j{number:02d}]"
    materialize(sdir, jobs, number, project)
    git(project, "add", "-A")
    git(project, "commit", "-q", "--allow-empty", "-m", f"job {number} start")
    report_path = state / "report.json"
    report_path.unlink(missing_ok=True)

    ledger_before = len(read_ledger(state))
    spans_before = len(read_spans(state))
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "AWOS_NOTEBOOK": "1" if arm == "on" else "0",
           **PIN_ENV}
    started = time.monotonic()
    timed_out = stream_child(
        [PY, str(Path(__file__).resolve()), "_dry_child" if dry_run else "_child",
         str(job_dir), str(project)],
        cwd=state, env=env, log_path=arm_dir / "run.log", prefix=prefix,
        timeout_s=float(spec.get("timeout_min", 30)) * 60,
    )
    minutes = round((time.monotonic() - started) / 60, 2)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    changed = git(project, "status", "--porcelain", check=False).stdout
    hidden, visible = judge(job_dir, project)
    usage = ledger_metrics(read_ledger(state)[ledger_before:])
    # The ledger holds one entry per agent-loop task, not per model call.
    usage["turns"] = sum(int(s.get("attempt_count", 0) or 0)
                         for s in read_spans(state)[spans_before:])
    result = {
        "arm": arm, "job": number, "id": spec.get("id", job_dir.name), "dir": job_dir.name,
        "solved": bool(hidden["ok"] and visible["ok"]),
        "hidden_passed": hidden["passed"], "hidden_failed": hidden["failed"] + hidden["errors"],
        "failing_tests": hidden["failing"], "hidden_summary": hidden["summary"],
        "visible_tests_ok": visible["ok"], "visible_failing": visible["failing"],
        "orchestrator_success": report.get("success"),
        "files_changed": len([ln for ln in changed.splitlines() if ln.strip()]),
        **usage,
        "minutes": minutes, "timed_out": timed_out,
        "notebook_chars": notebook_chars(state),
    }
    print(f"{prefix} {'SOLVED' if result['solved'] else 'not solved'} — hidden "
          f"{hidden['passed']}/{hidden['passed'] + result['hidden_failed']} · own tests "
          f"{'ok' if visible['ok'] else 'BROKEN'} · {usage['turns']} turns · "
          f"${usage['cost_usd']:.4f} · {minutes} min · notebook {result['notebook_chars']} chars",
          flush=True)
    return result


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def summarize(results: list[dict], arms: list[str]) -> dict:
    def block(rows: list[dict]) -> dict:
        return {
            "jobs": len(rows),
            "solved": sum(r["solved"] for r in rows),
            "solve_rate": _mean([float(r["solved"]) for r in rows]),
            "mean_cost_usd": _mean([r["cost_usd"] for r in rows]),
            "mean_turns": _mean([float(r.get("turns", 0)) for r in rows]),
            "mean_minutes": _mean([r["minutes"] for r in rows]),
        }

    out: dict = {}
    for arm in arms:
        rows = [r for r in results if r["arm"] == arm]
        out[arm] = {
            "all": block(rows),
            "jobs_1_6": block([r for r in rows if r["job"] <= 6]),
            "jobs_7_12": block([r for r in rows if 7 <= r["job"] <= 12]),
        }
    return out


def _fmt(v, money=False) -> str:
    if v is None:
        return "-"
    return f"${v:.4f}" if money else (f"{v:.2f}" if isinstance(v, float) else str(v))


def print_report(results: list[dict], arms: list[str], summary: dict) -> None:
    by = {(r["arm"], r["job"]): r for r in results}
    numbers = sorted({r["job"] for r in results})
    width = 10 + 34 * len(arms)
    print("\n" + "=" * width)
    print("  job     " + "".join(f"{arm + ': verdict  turns  cost  min':<34}" for arm in arms))
    print("  " + "-" * (width - 2))
    for n in numbers:
        line = f"  {n:>3}     "
        for arm in arms:
            r = by.get((arm, n))
            cell = ("-" if r is None else
                    f"{'SOLVED' if r['solved'] else 'no':<8} {r.get('turns', 0):>5}  "
                    f"${r['cost_usd']:.3f} {r['minutes']:>5}")
            line += f"{cell:<34}"
        print(line)
    print("  " + "-" * (width - 2))
    for arm in arms:
        for label, key in (("all", "all"), ("jobs 1-6", "jobs_1_6"), ("jobs 7-12", "jobs_7_12")):
            b = summary[arm][key]
            if not b["jobs"]:
                continue
            print(f"  {arm:<4} {label:<10} solved {b['solved']}/{b['jobs']} "
                  f"(rate {_fmt(b['solve_rate'])})  mean cost {_fmt(b['mean_cost_usd'], True)}  "
                  f"mean turns {_fmt(b['mean_turns'])}")
    print("=" * width)


def run(series: str, arms: list[str], job_spec: str | None, dry_run: bool,
        root: Path | None = None, out_root: Path | None = None,
        env_file: str | None = None) -> int:
    sdir = series_dir(series, root)
    jobs = discover_jobs(sdir)
    numbers = parse_jobs(job_spec, [n for n, _ in jobs])
    if not numbers:
        print(f"[job_series] no jobs selected in {sdir}")
        return 1
    job_by_n = dict(jobs)
    out_root = out_root or (REPO / ".awos")
    ts = time.strftime("%Y%m%dT%H%M%S")
    run_root = out_root / "job_series" / ts

    env_src = find_env_file(env_file)
    arm_dirs: dict[str, Path] = {}
    for arm in arms:
        arm_dir = run_root / arm
        (arm_dir / "state").mkdir(parents=True, exist_ok=True)
        (arm_dir / "project").mkdir(parents=True, exist_ok=True)
        if env_src:
            shutil.copy2(env_src, arm_dir / "state" / ".env")
        git(arm_dir / "project", "init", "-q")
        (arm_dir / "run.log").touch()
        arm_dirs[arm] = arm_dir

    print(f"[job_series] series={series} jobs={numbers} arms={arms} dry_run={dry_run}")
    print(f"[job_series] model pinned: {PINNED_MODEL} (ladder blocks {list(BLOCKED_LADDER_IDS)}; "
          f"env {sorted(PIN_ENV)})")
    print(f"[job_series] .env copied from: {env_src if env_src else 'none found'}")
    for arm, arm_dir in arm_dirs.items():
        print(f"[job_series] {arm} log:  tail -f {arm_dir / 'run.log'}")
    sys.stdout.flush()

    results: list[dict] = []
    stopped = None
    for i, n in enumerate(numbers):
        for arm in arms:
            # Before every job: a key dying mid-run otherwise reads as the agent failing.
            if not dry_run and not backend_ok():
                stopped = f"backend check failed before {arm} job {n}"
                break
            spec = load(job_by_n[n])
            print(f"\n########## [{arm}] job {n} ({i + 1}/{len(numbers)}): {spec.get('id')} ##########")
            print(f"GOAL: {spec['goal']}\n", flush=True)
            results.append(run_job(arm, n, job_by_n[n], sdir, jobs, arm_dirs[arm], dry_run))
        if stopped:
            print(f"[job_series] stopping: {stopped}", flush=True)
            break

    summary = summarize(results, arms)
    print_report(results, arms, summary)
    out = out_root / f"job_series_{ts}.json"
    out.write_text(json.dumps({
        "series": series, "timestamp": ts, "arms": arms, "jobs": numbers, "dry_run": dry_run,
        "model_pin": {"model": PINNED_MODEL, "blocked_ladder_ids": list(BLOCKED_LADDER_IDS),
                      "env": PIN_ENV},
        "run_dir": str(run_root), "stopped": stopped,
        "results": results, "summary": summary,
    }, indent=2), encoding="utf-8")
    print(f"  Saved {out}")
    return 2 if stopped and not results else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_child"]:
        run_child(Path(argv[1]), Path(argv[2]))
        return 0
    if argv[:1] == ["_dry_child"]:
        run_dry_child(Path(argv[1]), Path(argv[2]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--series", default="ordertool")
    parser.add_argument("--arms", default="off,on", help="comma list of off|on (default off,on)")
    parser.add_argument("--jobs", default=None, help="e.g. 1-12 or 1-3,7 (default all)")
    parser.add_argument("--dry-run", action="store_true", help="no-op child instead of the orchestrator")
    parser.add_argument("--series-root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--out-root", default=None, help="where results/state go (default .awos/)")
    parser.add_argument("--env-file", default=None, help=".env to copy into each arm (default: repo's)")
    args = parser.parse_args(argv)
    root = Path(args.series_root) if args.series_root else None
    if args.command == "validate":
        return validate(args.series, root)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if not arms or any(a not in ("off", "on") for a in arms):
        parser.error("--arms takes off and/or on")
    return run(args.series, arms, args.jobs, args.dry_run, root,
               Path(args.out_root) if args.out_root else None, args.env_file)


if __name__ == "__main__":
    sys.exit(main())
