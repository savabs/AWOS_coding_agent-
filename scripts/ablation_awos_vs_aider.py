#!/usr/bin/env python3
"""Experiment 1 driver: AWOS (arm `off`) vs Aider (arm `aider`) on the same model.

Pre-registration: docs/specs/ablation_awos_vs_aider_haiku.md.

    AWOS_SERIES_MODEL=claude-haiku-5-5 caffeinate -i python scripts/ablation_awos_vs_aider.py \
        run --series-root ~/.awos-harness/fresh_series --out ~/.awos-harness/b2_ablation --cap 3.0
    python scripts/ablation_awos_vs_aider.py combine --out ~/.awos-harness/b2_ablation
    python scripts/stats_audit.py ~/.awos-harness/b2_ablation/combined.json --a aider --b off --cost-budget 0.5

Every issue directory under the series root is its own one-job series. `run` calls
scripts/job_series.py once per (repeat, issue) with both arms, so the two arms of an
issue run back to back on the same inputs (job_series checks that). The arm order
alternates by issue index (even: off first, odd: aider first).

Resumable: a (repeat, issue) whose results file exists with a row for each arm is
skipped. Spend cap (--cap, total USD across everything this out dir ever ran):
- spend counts every attempt: each row's spent_usd (the scored attempt, the
  harness's retried empty-response attempts and job_series' failed attempts),
  summed over every results file of a (repeat, issue), including interrupted ones;
- before each issue the driver stops if spent + the costliest issue-run so far
  (at least the running mean; first issue: $0.05) would pass --cap;
- hard stop: job_series gets AWOS_SERIES_BUDGET_USD = cap - spent, lowers each
  job's cost cap to what is left, scores a job cut short by it INVALID
  (budget_cap) and stops; the driver then stops too.

Speed (benchmark overhead only; scoring unchanged): job_series reuses the
series' `validate` verdict from --preflight-cache (default
<series-root>/_tools/validate.json) instead of re-running the preflight pytest
runs. --parallel N runs up to N issues at once (each its own job_series, arms
still sequential in arm_order, output in <out>/r<rep>/<issue>/driver.log); an
issue launches only while spent + (in_flight + 1) * step <= --cap, each child
gets (cap - spent) / N as its series budget, and a child stopped on the budget
stops new launches (in-flight issues finish).

Gates (refuse to start): the series root must hold a READY file (Track A's signal
that the set validated) and at least --min-issues issue dirs.

`combine` merges every per-issue result into one job_series-shaped file. job_series
numbers each one-job series as job 1, so rows are re-keyed: job = issue index
(sorted order), `issue` = the issue dir name. stats_audit pools by `job`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARMS = ("off", "aider")
FIRST_ISSUE_GUESS_USD = 0.05


def discover(series_root: Path) -> list[str]:
    return sorted(p.name for p in series_root.iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "jobs").is_dir())


def gate(series_root: Path, min_issues: int, ready_required: bool = True) -> tuple[list[str], str | None]:
    if not series_root.is_dir():
        return [], f"series root {series_root} does not exist"
    issues = discover(series_root)
    if ready_required and not (series_root / "READY").is_file():
        return issues, f"no READY file in {series_root} (fresh set not validated)"
    if len(issues) < min_issues:
        return issues, f"{len(issues)} issue(s) < {min_issues} required"
    return issues, None


def arm_order(index: int) -> list[str]:
    return list(ARMS) if index % 2 == 0 else list(reversed(ARMS))


def result_files(out: Path, rep: int, issue: str) -> list[Path]:
    return sorted((out / f"r{rep}" / issue).glob("job_series_*.json"))


def result_file(out: Path, rep: int, issue: str) -> Path | None:
    files = result_files(out, rep, issue)
    return files[-1] if files else None


def load_rows(path: Path | None) -> list[dict]:
    if not path:
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("results") or []
    except (OSError, ValueError):
        return []


def done(rows: list[dict]) -> bool:
    return {r.get("arm") for r in rows} >= set(ARMS)


def row_cost(r: dict) -> float:
    """Every dollar a row's job cost: spent_usd when job_series recorded it
    (all attempts), else the billed/ledger cost plus failed attempts'."""
    billed = r.get("billed_usd")
    if billed is not None:
        return float(billed)
    if r.get("spent_usd") is not None:
        return float(r["spent_usd"])
    return float(r.get("cost_usd") or 0) + sum(float(f.get("cost_usd") or 0)
                                               for f in r.get("failed_attempts") or [])


def issue_spend(out: Path, rep: int, issue: str) -> float:
    """Spend of one (repeat, issue) over every results file it has (a rerun
    after an interruption writes a new file; the old one's spend still counts)."""
    return sum(row_cost(r) for f in result_files(out, rep, issue) for r in load_rows(f))


def spend(out: Path, issues: list[str], repeats: int) -> tuple[float, int]:
    total, n = 0.0, 0
    for rep in range(1, repeats + 1):
        for issue in issues:
            if result_files(out, rep, issue):
                total += issue_spend(out, rep, issue)
                n += 1
    return total, n


def max_issue_spend(out: Path, issues: list[str], repeats: int) -> float:
    return max([issue_spend(out, rep, issue) for rep in range(1, repeats + 1)
                for issue in issues if result_files(out, rep, issue)] or [0.0])


def projected_next(out: Path, issues: list[str], repeats: int) -> tuple[float, float, int]:
    """(spent, spent + the costliest issue-run so far (>= the mean), issue-runs)."""
    total, n = spend(out, issues, repeats)
    step = max(total / n, max_issue_spend(out, issues, repeats)) if n else FIRST_ISSUE_GUESS_USD
    return total, total + step, n


def budget_stopped(path: Path | None) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return False
    return "budget" in str(data.get("stopped") or "")


def preflight_cache_for(a: argparse.Namespace, root: Path) -> Path | None:
    """--preflight-cache, else <series-root>/_tools/validate.json when it exists."""
    if getattr(a, "preflight_cache", None):
        return Path(a.preflight_cache).expanduser().resolve()
    default = root / "_tools" / "validate.json"
    return default.resolve() if default.is_file() else None


def issue_cmd(a: argparse.Namespace, root: Path, out: Path, rep: int, i: int, issue: str,
              cache: Path | None) -> list[str]:
    cmd = [sys.executable, str(REPO / "scripts" / "job_series.py"), "run",
           "--series", issue, "--series-root", str(root),
           "--arms", ",".join(arm_order(i)),
           "--out-root", str(out / f"r{rep}" / issue)]
    if cache:
        cmd += ["--preflight-cache", str(cache)]
    if a.dry_run:
        cmd.append("--dry-run")
    return cmd


def cmd_run(a: argparse.Namespace) -> int:
    root, out = Path(a.series_root).expanduser(), Path(a.out).expanduser()
    issues, why = gate(root, a.min_issues, not a.no_ready_check)
    if why:
        print(f"[ablation] BLOCKED: {why}", flush=True)
        return 4
    if os.environ.get("AWOS_SERIES_MODEL", "").strip() != "claude-haiku-5-5" and not a.dry_run:
        print("[ablation] AWOS_SERIES_MODEL must be claude-haiku-5-5 (pre-registered)", flush=True)
        return 4
    if a.parallel < 1:
        print("[ablation] --parallel must be >= 1", flush=True)
        return 4
    out.mkdir(parents=True, exist_ok=True)
    cache = preflight_cache_for(a, root)
    if cache:
        print(f"[ablation] preflight cache: {cache}", flush=True)
    if a.parallel > 1:
        return run_parallel(a, root, out, issues, cache)
    for rep in range(1, a.repeat + 1):
        for i, issue in enumerate(issues):
            if done(load_rows(result_file(out, rep, issue))):
                continue
            total, projected, n = projected_next(out, issues, a.repeat)
            if projected > a.cap:
                print(f"[ablation] cap: spent ${total:.4f} over {n} issue-run(s); next would "
                      f"project ${projected:.4f} > ${a.cap:.2f}. Stopping.", flush=True)
                return 0
            cmd = issue_cmd(a, root, out, rep, i, issue, cache)
            print(f"[ablation] r{rep} {i + 1}/{len(issues)} {issue} arms={arm_order(i)} "
                  f"spent=${total:.4f} budget_left=${a.cap - total:.4f}", flush=True)
            env = {**os.environ, "AWOS_SERIES_BUDGET_USD": f"{a.cap - total:.6f}"}
            subprocess.run(cmd, cwd=REPO, check=False, env=env)
            if budget_stopped(result_file(out, rep, issue)):
                total, _ = spend(out, issues, a.repeat)
                print(f"[ablation] cap: job_series stopped on the series budget; spent "
                      f"${total:.4f} of ${a.cap:.2f}. Stopping.", flush=True)
                return 0
    total, n = spend(out, issues, a.repeat)
    print(f"[ablation] finished: {n} issue-run(s), ${total:.4f}", flush=True)
    return 0


POLL_S = 2.0


def run_parallel(a: argparse.Namespace, root: Path, out: Path, issues: list[str],
                 cache: Path | None) -> int:
    """Up to a.parallel issues at once, each its own job_series process (its arms
    stay sequential, in arm_order; output to <out>/r<rep>/<issue>/driver.log).
    Budget: an issue launches only while spent + (in_flight + 1) * step <= cap
    (step: projected_next's), and its child gets AWOS_SERIES_BUDGET_USD =
    (cap - spent) / a.parallel. A child stopped on the budget ends the run:
    nothing new launches, the in-flight ones finish, then the driver stops."""
    pending = [(rep, i, issue) for rep in range(1, a.repeat + 1)
               for i, issue in enumerate(issues)]
    running: dict[tuple[int, str], tuple[subprocess.Popen, object]] = {}
    stop: str | None = None
    cap_note = ""
    try:
        while True:
            for (rep, issue), (proc, log) in list(running.items()):
                if proc.poll() is None:
                    continue
                log.close()
                del running[(rep, issue)]
                print(f"[ablation] r{rep} {issue} finished (exit {proc.returncode}) "
                      f"spend=${issue_spend(out, rep, issue):.4f} in_flight={len(running)}",
                      flush=True)
                if stop is None and budget_stopped(result_file(out, rep, issue)):
                    stop = f"job_series stopped on the series budget ({issue})"
                    print(f"[ablation] cap: {stop}; launching nothing new, waiting for "
                          f"{len(running)} in-flight issue(s)", flush=True)
            while stop is None and pending and len(running) < a.parallel:
                rep, i, issue = pending[0]
                if done(load_rows(result_file(out, rep, issue))):
                    pending.pop(0)
                    continue
                total, projected, n = projected_next(out, issues, a.repeat)
                need = total + (len(running) + 1) * (projected - total)
                if need > a.cap:
                    cap_note = (f"spent ${total:.4f} over {n} issue-run(s); {len(running)} in "
                                f"flight + next would project ${need:.4f} > ${a.cap:.2f}")
                    break
                pending.pop(0)
                d = out / f"r{rep}" / issue
                d.mkdir(parents=True, exist_ok=True)
                log = open(d / "driver.log", "a", encoding="utf-8")
                child_budget = (a.cap - total) / a.parallel
                # AWOS_SERIES_CONCURRENT: one key's usage delta can't be split
                # between concurrent jobs, so job_series skips the billed figure.
                env = {**os.environ, "AWOS_SERIES_BUDGET_USD": f"{child_budget:.6f}",
                       "AWOS_SERIES_CONCURRENT": "1", "PYTHONUNBUFFERED": "1"}
                proc = subprocess.Popen(issue_cmd(a, root, out, rep, i, issue, cache), cwd=REPO,
                                        env=env, stdout=log, stderr=subprocess.STDOUT)
                running[(rep, issue)] = (proc, log)
                print(f"[ablation] r{rep} {i + 1}/{len(issues)} {issue} started "
                      f"arms={arm_order(i)} spent=${total:.4f} child_budget=${child_budget:.4f} "
                      f"in_flight={len(running)} log={d / 'driver.log'}", flush=True)
            if not running:
                if stop:
                    total, _ = spend(out, issues, a.repeat)
                    print(f"[ablation] cap: stopped on the series budget; spent "
                          f"${total:.4f} of ${a.cap:.2f}. Stopping.", flush=True)
                    return 0
                if pending:   # nothing in flight and the next issue still doesn't fit
                    print(f"[ablation] cap: {cap_note}. Stopping.", flush=True)
                    return 0
                break
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        for proc, _ in running.values():
            if proc.poll() is None:
                proc.terminate()
        for proc, log in running.values():
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
        raise
    total, n = spend(out, issues, a.repeat)
    print(f"[ablation] finished: {n} issue-run(s), ${total:.4f}", flush=True)
    return 0


def combine(out: Path, issues: list[str], repeats: int) -> dict:
    results = []
    for rep in range(1, repeats + 1):
        for idx, issue in enumerate(issues, start=1):
            for r in load_rows(result_file(out, rep, issue)):
                if r.get("arm") in ARMS:
                    results.append({**r, "job": idx, "issue": issue, "repeat": rep})
    return {"series": "ablation_awos_vs_aider", "arms": list(ARMS), "repeat": repeats,
            "issues": issues, "results": results}


def cmd_combine(a: argparse.Namespace) -> int:
    out = Path(a.out).expanduser()
    issues = sorted({p.name for p in out.glob("r*/*") if p.is_dir()})
    repeats = max([int(p.name[1:]) for p in out.glob("r*") if p.name[1:].isdigit()] or [1])
    data = combine(out, issues, repeats)
    (out / "combined.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"[ablation] combined {len(data['results'])} row(s) over {len(issues)} issue(s) "
          f"-> {out / 'combined.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--series-root", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--cap", type=float, default=3.0)
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--min-issues", type=int, default=15)
    r.add_argument("--no-ready-check", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--preflight-cache", default=None,
                   help="job_series validate cache (default <series-root>/_tools/validate.json "
                        "when it exists)")
    r.add_argument("--parallel", type=int, default=1,
                   help="issues run at once, each its own job_series (default 1: sequential)")
    c = sub.add_parser("combine")
    c.add_argument("--out", required=True)
    for s in (r, c):
        # Two job_series arms to pair; default AWOS vs Aider. The harness-lift
        # ladder uses e.g. --arms off,mini (docs/specs/harness_lift_ladder.md).
        s.add_argument("--arms", default=",".join(ARMS))
    a = p.parse_args(argv)
    arms = tuple(x.strip() for x in a.arms.split(",") if x.strip())
    if len(arms) != 2:
        p.error("--arms takes exactly two arms, e.g. off,mini")
    globals()["ARMS"] = arms
    return cmd_run(a) if a.command == "run" else cmd_combine(a)


if __name__ == "__main__":
    sys.exit(main())
