"""
job_host.py — The always-on host: a queue of jobs, run one at a time, that
survives crashes and restarts (milestone M1 of docs/product/agent_computer.md).

A person hands the agent's computer a job and walks away. That only works if
the job outlives the terminal it was typed into: it is written to disk the
moment it is submitted, a separate long-lived process picks it up, and a crash
or kill anywhere leaves a state the next start can repair.

    job = goal + ability + verifier

Nothing here assumes a job is coding. The host only knows a goal, a workspace
directory and the name of an ability; what "doing" and "verified" mean belongs
to the ability, looked up in ABILITIES. Coding is the only one today — an
unknown ability fails the job with a clear error, which is the plug-in point.

Crash safety comes from two choices:

- Each job runs in a CHILD process. A job that crashes or hangs cannot take the
  host down, and a per-job timeout can kill it cleanly.
- Every record carries the child's pid and the host's pid, each with its
  start time. If either is gone while the record still says "running", the
  work was interrupted: recover() kills any leftover child and puts the job
  back in the queue, up to AWOS_JOB_MAX_ATTEMPTS times. The start time is what
  makes the pid safe to act on: after a reboot a recorded pid often belongs
  to some unrelated process, which must be neither killed nor mistaken for a
  live job.

Records are JSON files under .awos/jobs/, written atomically (temp + replace),
so a kill mid-write never leaves a half-written job.

Usage:
    store = JobStore()
    job = store.submit("Fix the pagination bug", root="/path/to/project")
    JobHost(store).serve(once=True)
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from .sandbox import HOST_GIT_OPTS
except ImportError:  # run as a script: `python job_host.py _child ...`
    from sandbox import HOST_GIT_OPTS

REPO = Path(__file__).resolve().parents[2]
DEFAULT_JOBS_DIR = REPO / ".awos" / "jobs"

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
FINAL_STATUSES = (DONE, FAILED, CANCELLED)


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _max_attempts() -> int:
    return int(os.getenv("AWOS_JOB_MAX_ATTEMPTS", "3"))


def _timeout_sec() -> float:
    return float(os.getenv("AWOS_JOB_TIMEOUT_MIN", "60")) * 60


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def process_start(pid: Optional[int]) -> Optional[str]:
    """When pid started, as ps prints it; None if there is no such process
    (or ps could not tell). Together with the pid it names one process."""
    if not pid:
        return None
    try:
        proc = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (proc.stdout.strip() or None) if proc.returncode == 0 else None


def process_matches(pid: Optional[int], started: Optional[str]) -> Optional[bool]:
    """
    Is the process recorded as (pid, started) still running?

    True: yes. False: it is gone, even if its pid now names another process.
    None: the pid is alive but cannot be checked (a record written before
    start times were kept, or ps failed), so it must not be killed.
    """
    if not pid_alive(pid):
        return False
    if not started:
        return None
    now = process_start(pid)
    if now is None:
        return None if pid_alive(pid) else False
    return now == started


def write_json_atomic(path: Path, data: Any) -> None:
    """Write via a temp file and os.replace, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ── Job record and store ──────────────────────────────────────────────────────


@dataclass
class Job:
    id: str
    goal: str
    root: str
    ability: str = "coding"
    status: str = QUEUED
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    #: The attempt number: 1 on submit, +1 each time recover() re-queues it.
    attempts: int = 1
    pid: Optional[int] = None
    host_pid: Optional[int] = None
    #: Start times (process_start) matching pid and host_pid.
    pid_started: Optional[str] = None
    host_started: Optional[str] = None
    result: dict = field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class JobStore:
    """Job records as one JSON file each, under .awos/jobs/."""

    def __init__(self, jobs_dir: str | Path | None = None):
        self.dir = Path(jobs_dir) if jobs_dir else DEFAULT_JOBS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    def report_path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.report.md"

    def result_path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.result.json"

    def log_path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.log"

    def save(self, job: Job) -> Job:
        write_json_atomic(self.path(job.id), asdict(job))
        return job

    def submit(self, goal: str, root: str | Path, ability: str = "coding") -> Job:
        if not goal or not goal.strip():
            raise ValueError("a job needs a goal")
        job = Job(id=f"j_{uuid.uuid4().hex[:12]}", goal=goal.strip(),
                  root=str(Path(root).resolve()), ability=ability)
        return self.save(job)

    def get(self, job_id: str) -> Optional[Job]:
        try:
            return Job.from_dict(json.loads(self.path(job_id).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def list(self) -> list[Job]:
        jobs = []
        for p in self.dir.glob("j_*.json"):
            if p.name.endswith(".result.json"):
                continue
            job = self.get(p.stem)
            if job:
                jobs.append(job)
        return sorted(jobs, key=lambda j: (j.created_at, j.id))

    def next_queued(self) -> Optional[Job]:
        return next((j for j in self.list() if j.status == QUEUED), None)

    # Transitions — each reads nothing but the job it is given, and saves it.

    def mark_running(self, job: Job, pid: Optional[int] = None) -> Job:
        job.status, job.started_at, job.finished_at = RUNNING, _now(), None
        job.pid, job.pid_started = pid, process_start(pid)
        job.host_pid, job.host_started = os.getpid(), process_start(os.getpid())
        job.error = None
        return self.save(job)

    def mark_done(self, job: Job, result: dict) -> Job:
        job.status, job.finished_at, job.result, job.error = DONE, _now(), result, None
        return self.save(job)

    def mark_failed(self, job: Job, error: str, result: Optional[dict] = None) -> Job:
        job.status, job.finished_at, job.error = FAILED, _now(), error
        if result is not None:
            job.result = result
        return self.save(job)

    def mark_cancelled(self, job: Job) -> Job:
        job.status, job.finished_at = CANCELLED, _now()
        return self.save(job)

    def recover(self) -> list[Job]:
        """
        Re-queue jobs whose run was interrupted; fail those out of attempts.

        "Interrupted" = marked running, but its child or its host is gone. A
        live child whose host died is killed first, so two attempts never edit
        the same workspace at once. Only a pid whose start time still matches
        is killed; one that cannot be checked is left alone, and the child's
        own parent-death watcher ends it within a second of the host dying.
        """
        touched = []
        for job in self.list():
            if job.status != RUNNING:
                continue
            host_dead = bool(job.host_pid) and process_matches(job.host_pid, job.host_started) is False
            child = process_matches(job.pid, job.pid_started)
            if child is not False and not host_dead:
                continue
            if child is True:
                _kill_group(job.pid)
            job.attempts += 1
            job.pid = job.host_pid = None
            job.pid_started = job.host_started = None
            if job.attempts > _max_attempts():
                self.mark_failed(job, f"interrupted {job.attempts - 1} times "
                                      f"(AWOS_JOB_MAX_ATTEMPTS={_max_attempts()})")
                write_report(self, job)
            else:
                job.status, job.started_at = QUEUED, None
                self.save(job)
            touched.append(job)
        return touched


def _kill_group(pid: int) -> None:
    """Kill a child and whatever it spawned (it leads its own process group)."""
    for kill in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
        try:
            kill()
            return
        except (ProcessLookupError, PermissionError):
            continue


# ── Report (the hand-off seed for M3) ─────────────────────────────────────────


def _changed_files(root: str) -> list[str]:
    # The job's code may have written this tree; see HOST_GIT_OPTS.
    try:
        proc = subprocess.run(["git", *HOST_GIT_OPTS, "-C", root, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln for ln in proc.stdout.splitlines() if ln.strip()] if proc.returncode == 0 else []


def _duration(job: Job) -> str:
    try:
        secs = (datetime.fromisoformat(job.finished_at) - datetime.fromisoformat(job.started_at)).total_seconds()
    except (TypeError, ValueError):
        return "-"
    return f"{secs:.0f}s" if secs < 120 else f"{secs / 60:.1f} min"


def write_report(store: JobStore, job: Job) -> Path:
    changed = _changed_files(job.root)
    lines = [
        f"# Job {job.id}", "",
        f"- **Goal:** {job.goal}",
        f"- **Ability:** {job.ability}",
        f"- **Workspace:** {job.root}",
        f"- **Status:** {job.status}",
        f"- **Attempts:** {job.attempts}",
        f"- **Duration:** {_duration(job)}", "",
        "## Result", "",
    ]
    lines += [f"- {k}: {v}" for k, v in job.result.items()] or ["- (none)"]
    if job.error:
        lines += ["", "## Error", "", f"    {job.error}"]
    lines += ["", "## Changed files", ""]
    lines += [f"    {ln}" for ln in changed] or ["- (none, or the workspace is not a git repo)"]
    path = store.report_path(job.id)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── Abilities (run inside the child process) ──────────────────────────────────


def _ledger() -> list:
    try:
        return json.loads((REPO / ".awos" / "budget.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _run_coding(job: Job) -> dict:
    """App #1: hand the goal to a fresh Orchestrator, planner path."""
    from scaffold.agent.orchestrator import Orchestrator

    ledger_before = len(_ledger())
    report = Orchestrator().execute_feature(
        goal=job.goal,
        codebase_root=job.root,
        pre_planned_tasks=None,
        auto_approve_plan=True,
    )
    result = {k: report.get(k) for k in ("success", "tasks_completed", "tasks_failed")}
    if not report.get("success"):
        # Why it failed belongs in the report, not only in the child's log.
        reasons = [str(e) for e in report.get("errors") or []] or [report.get("goal_check_reasoning") or ""]
        result["error"] = "; ".join(r for r in reasons if r)[:500] or None
    # execute_feature does not always report its cost; the spend ledger does.
    # Jobs run one at a time, so the entries added meanwhile are this job's.
    cost = report.get("total_cost")
    if not cost:
        cost = sum(float(e.get("cost", 0)) for e in _ledger()[ledger_before:])
    result["cost_usd"] = round(float(cost or 0), 6)
    return result


#: ability name -> function(job) -> result dict with at least "success".
ABILITIES: dict[str, Callable[[Job], dict]] = {"coding": _run_coding}

CHILD_ENV = {
    "AWOS_EXECUTOR": "agent_loop",
    "AWOS_SAFE_TO_RUN_TESTS": "1",
    "AWOS_USE_WORKTREE": "false",
    "AWOS_ENABLE_CLARIFICATION": "false",
    "AWOS_ENABLE_PLAN_REVIEW": "false",
}


def _die_with_parent(parent_pid: int) -> None:
    """Exit if the host goes away, so a killed host leaves a dead pid behind."""
    def watch() -> None:
        while os.getppid() == parent_pid:
            time.sleep(1)
        # The child leads its own process group: take pytest and any script
        # it started down with it, not just this process.
        try:
            os.killpg(0, signal.SIGKILL)
        finally:
            os._exit(70)
    threading.Thread(target=watch, daemon=True).start()


def child_main(job_path: str, result_path: str, parent_pid: int) -> int:
    _die_with_parent(parent_pid)
    sys.path[:0] = [str(REPO), str(REPO / "scaffold"), str(REPO / "scaffold" / "agent")]
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    os.environ.update(CHILD_ENV)
    job = Job.from_dict(json.loads(Path(job_path).read_text(encoding="utf-8")))
    result = ABILITIES[job.ability](job)
    write_json_atomic(Path(result_path), result)
    return 0


def run_in_child(store: JobStore, job: Job, on_start: Callable[[int], None]) -> dict:
    """
    Run one job in a child process; return its result dict.

    Raises RuntimeError with a readable reason when the child times out or
    exits without writing a result.
    """
    result_path = store.result_path(job.id)
    result_path.unlink(missing_ok=True)
    with open(store.log_path(job.id), "a", encoding="utf-8") as log:
        log.write(f"\n===== attempt {job.attempts} at {_now()} =====\n")
        log.flush()
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_child",
             str(store.path(job.id)), str(result_path), str(os.getpid())],
            cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=True,  # its own group, so a timeout kills pytest too
        )
        on_start(proc.pid)
        try:
            code = proc.wait(timeout=_timeout_sec())
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid)
            proc.wait()
            raise RuntimeError(f"timed out after {_timeout_sec() / 60:.0f} min (AWOS_JOB_TIMEOUT_MIN)")
    if not result_path.exists():
        raise RuntimeError(f"child exited with code {code} and no result; see {store.log_path(job.id)}")
    return json.loads(result_path.read_text(encoding="utf-8"))


# ── Host loop ─────────────────────────────────────────────────────────────────


class JobHost:
    """Takes queued jobs one at a time and runs each to a final status."""

    def __init__(self, store: Optional[JobStore] = None,
                 runner: Optional[Callable[[JobStore, Job, Callable[[int], None]], dict]] = None,
                 log: Callable[[str], None] = print):
        self.store = store or JobStore()
        self.runner = runner or run_in_child
        self.log = log
        self._stop = False

    def _on_signal(self, signum, _frame) -> None:
        if self._stop:
            raise KeyboardInterrupt  # second signal: stop now
        self._stop = True
        self.log(f"[host] signal {signum}: finishing the current job, taking no new ones")

    def _lock(self):
        """One host per jobs dir; flock is released by the OS even on kill -9."""
        handle = open(self.store.dir / "host.lock", "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError(f"another job host is already serving {self.store.dir}")
        handle.write(str(os.getpid()))
        handle.flush()
        return handle

    def run_job(self, job: Job) -> Job:
        if job.ability not in ABILITIES:
            job = self.store.mark_failed(
                job, f"unknown ability {job.ability!r}; available: {', '.join(sorted(ABILITIES))}")
            write_report(self.store, job)
            return job
        self.store.mark_running(job)
        self.log(f"[host] {job.id} running (attempt {job.attempts}): {job.goal[:70]}")

        def on_start(pid: int) -> None:
            # recover() needs the child's pid and start time to kill an
            # orphan (and only it) if this host dies mid-job.
            job.pid, job.pid_started = pid, process_start(pid)
            self.store.save(job)

        try:
            result = self.runner(self.store, job, on_start)
        except Exception as exc:  # the job failed; the host carries on
            job = self.store.mark_failed(job, str(exc) or type(exc).__name__)
        else:
            if result.get("success"):
                job = self.store.mark_done(job, result)
            else:
                job = self.store.mark_failed(
                    job, result.get("error") or "the ability reported failure", result)
        write_report(self.store, job)
        self.log(f"[host] {job.id} {job.status}" + (f": {job.error}" if job.error else ""))
        return job

    def serve(self, poll_sec: float = 2, once: bool = False) -> None:
        lock = self._lock()
        previous = {s: signal.signal(s, self._on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
        self.log(f"[host] serving {self.store.dir} (pid {os.getpid()})")
        try:
            while not self._stop:
                for job in self.store.recover():
                    self.log(f"[host] {job.id} was interrupted -> {job.status} (attempt {job.attempts})")
                job = self.store.next_queued()
                if job is None:
                    if once:
                        break
                    time.sleep(poll_sec)
                    continue
                self.run_job(job)
        finally:
            for s, handler in previous.items():
                signal.signal(s, handler)
            lock.close()
        self.log("[host] stopped")


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "_child":
    sys.exit(child_main(sys.argv[2], sys.argv[3], int(sys.argv[4])))
