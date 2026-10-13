"""
jobs.py — the app's view of host-queue jobs for one repo.

Submitting goes through the existing host queue (JobQueue.add); a follow-up
job additionally gets <job_dir>/base_ref so the worker starts it from the
previous job's review branch. The app's own review bookkeeping (merged /
rejected) lives in <job_dir>/app_review.json; the queue is never altered.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scaffold.agent.host.models import (
    BLOCKED,
    CANCELLED,
    DONE,
    FAILED,
    PAUSED,
    QUEUED,
    RUNNING,
    JobSpec,
    now_iso,
)
from scaffold.agent.host.queue import JobQueue

REVIEW_FILE = "app_review.json"
BASE_REF_FILE = "base_ref"   # read by host.worker.prepare_workspace
FOLLOW_UP_FILE = "follow_up_of"
ACTIVE = (QUEUED, RUNNING, PAUSED)
FINISHED = (DONE, FAILED, CANCELLED, BLOCKED)


def default_budget() -> float:
    try:
        return float(os.getenv("AWOS_APP_BUDGET", "1.0"))
    except ValueError:
        return 1.0


def short(job_id: str) -> str:
    return job_id[:8]


def repo_jobs(queue: JobQueue, repo: Path) -> list[JobSpec]:
    repo_s = str(Path(repo).resolve())
    return [j for j in queue.list() if str(Path(j.repo_path).resolve()) == repo_s]


def find_job(queue: JobQueue, repo: Path, ref: str) -> Optional[JobSpec]:
    """A job of this repo by id or unique id prefix (also 'awos/<id8>')."""
    ref = ref.strip()
    if ref.startswith("awos/"):
        ref = ref[5:]
    if not ref:
        return None
    matches = [j for j in repo_jobs(queue, repo) if j.id.startswith(ref)]
    return matches[0] if len(matches) == 1 else None


def submit(queue: JobQueue, repo: Path, goal: str, budget: Optional[float] = None) -> JobSpec:
    return queue.submit(goal, repo, budget_usd=default_budget() if budget is None else budget)


def follow_up_goal(prev: JobSpec, text: str, branch: Optional[str] = None) -> str:
    prev_goal = prev.goal.strip().splitlines()[0][:200] if prev.goal.strip() else short(prev.id)
    if branch:
        where = f"The code already contains that job's changes (branch {branch}); build on them."
    else:
        where = "That job left no branch to build on; start from the current code."
    return (f"{text.strip()}\n\n"
            f"(Follow-up to AWOS job {short(prev.id)}, whose goal was: \"{prev_goal}\". {where})")


def submit_follow_up(queue: JobQueue, repo: Path, prev: JobSpec, text: str,
                     branch: Optional[str], budget: Optional[float] = None) -> JobSpec:
    """
    A continuation job. With `branch` (the previous job's review branch) the
    worker starts it from that branch; base_ref is written before the job is
    queued, so the worker can never claim it without it.
    """
    job = JobSpec.new(follow_up_goal(prev, text, branch), repo,
                      budget_usd=default_budget() if budget is None else budget)
    jdir = queue.job_dir(job.id)
    jdir.mkdir(parents=True, exist_ok=True)
    if branch:
        (jdir / BASE_REF_FILE).write_text(branch + "\n", encoding="utf-8")
    (jdir / FOLLOW_UP_FILE).write_text(prev.id + "\n", encoding="utf-8")
    return queue.add(job)


# ── review bookkeeping ───────────────────────────────────────────────────────

def review_state(queue: JobQueue, job: JobSpec) -> dict:
    try:
        return json.loads((queue.job_dir(job.id) / REVIEW_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_reviewed(queue: JobQueue, job: JobSpec, action: str, **extra) -> None:
    path = queue.job_dir(job.id) / REVIEW_FILE
    data = {"action": action, "at": now_iso(), **extra}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


# ── one finished job, summarised ─────────────────────────────────────────────

@dataclass
class Outcome:
    state: str
    tests: str            # "2 passed" / "1 passed, 1 failed" / "no tests run"
    tests_ok: Optional[bool]
    cost: float
    seconds: Optional[float]
    branch: Optional[str]
    handoff_status: Optional[str]
    error: Optional[str]
    report: Optional[str]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def elapsed(job: JobSpec) -> Optional[float]:
    start = _parse_iso(job.started_at) or _parse_iso(job.created_at)
    end = _parse_iso(job.finished_at) or datetime.now(timezone.utc)
    return (end - start).total_seconds() if start else None


def outcome(queue: JobQueue, job: JobSpec) -> Outcome:
    result = job.result or {}
    tests = result.get("tests") if isinstance(result.get("tests"), dict) else None
    if tests:
        passed, failed = int(tests.get("passed") or 0), int(tests.get("failed") or 0)
        text = f"{passed} passed" + (f", {failed} failed" if failed else "")
        if tests.get("status") == "timeout":
            text = "tests timed out"
        ok = tests.get("status") == "passed"
    else:
        text, ok = "no tests run", None
    try:
        cost = float(result.get("cost_usd") or 0)
    except (TypeError, ValueError):
        cost = 0.0
    try:
        from scaffold.agent.host.autopilot import spent_so_far
        cost = max(cost, spent_so_far(queue.job_dir(job.id)))
    except Exception:
        pass
    handoff = result.get("handoff") if isinstance(result.get("handoff"), dict) else {}
    if not handoff:
        try:
            handoff = json.loads((queue.job_dir(job.id) / "handoff.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            handoff = {}
    report = handoff.get("report_path") or handoff.get("report")
    if not report:
        for name in ("report.md", "blocked.md"):
            p = queue.job_dir(job.id) / name
            if p.exists():
                report = str(p)
                break
    return Outcome(state=job.state, tests=text, tests_ok=ok, cost=cost, seconds=elapsed(job),
                   branch=handoff.get("branch"), handoff_status=handoff.get("status"),
                   error=job.error, report=report)


def fmt_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "-"
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def goal_line(job: JobSpec, n: int = 60) -> str:
    text = job.goal.strip().splitlines()[0] if job.goal.strip() else ""
    return text if len(text) <= n else text[: n - 1] + "…"
