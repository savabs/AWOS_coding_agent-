"""
worker.py — the single long-lived host worker (M1 of docs/product/agent_computer.md).

Loop: recover interrupted jobs -> claim the oldest queued job -> run it via a
pluggable runner -> hand off -> mark done/failed. Spec: docs/specs/host_queue.md.

    runner(job: JobSpec, ctx: RunContext) -> dict   # at least {"success": bool}

The default runner reuses job_host.py's child process (the orchestrator entry
point behind `awos run`), so a crashed or hung job never takes the worker down.
`sleep_runner` is a model-free runner for demos and tests.

Restart safety:
- one worker per storage root (flock on <root>/worker.lock; the OS drops it
  even on kill -9), so on start every "running" job is an orphan: recover()
  kills its child (only if the pid still names that process) and re-queues it;
- SIGTERM/SIGINT: the current step finishes, the job goes back to "queued"
  without using up an attempt, the worker exits. A second signal exits at once.
"""

from __future__ import annotations

import fcntl
import importlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .models import CANCELLED, DONE, FAILED, JobSpec, now_iso
from .queue import JobQueue

REPO = Path(__file__).resolve().parents[3]

#: Cloud credentials blanked in the child env of a local_only job (fail closed).
CLOUD_KEYS = (
    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENCODE_GO_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
)


class StopRequested(Exception):
    """Raised by a runner at a step boundary when ctx.should_stop() is true."""


# ── Journal / handoff: optional collaborators ─────────────────────────────────


class _NullJournal:
    """Stand-in when scaffold.agent.host.journal is not available."""

    def __init__(self, job_dir: Any = None):
        self._steps: list[dict] = []

    def append(self, step: dict) -> dict:
        self._steps.append(dict(step))
        return step

    def steps(self) -> list[dict]:
        return list(self._steps)

    def last_checkpoint(self) -> Optional[dict]:
        return None

    def replay_plan(self) -> Optional[dict]:
        return None


def open_journal(job_dir: Path):
    try:
        from .journal import Journal  # type: ignore
    except ImportError:
        return _NullJournal(job_dir)
    try:
        return Journal(job_dir)
    except Exception:  # a broken journal must not stop the job
        return _NullJournal(job_dir)


def _load_handoff() -> Optional[Callable[..., dict]]:
    try:
        from .handoff import finish  # type: ignore
    except ImportError:
        return None
    return finish


def write_minimal_report(job: JobSpec, job_dir: Path, outcome: dict) -> dict:
    """Fallback hand-off when handoff.py is not available: report.md only."""
    lines = [
        f"# Job {job.id}", "",
        f"- **Goal:** {job.goal}",
        f"- **Repo:** {job.repo_path}",
        f"- **State:** {outcome.get('state')}",
        f"- **Attempts:** {job.attempts}",
        f"- **Budget:** ${job.budget_usd:.2f} ({job.privacy})", "",
        "## Result", "",
    ]
    result = outcome.get("result") or {}
    lines += [f"- {k}: {v}" for k, v in result.items()] or ["- (none)"]
    if outcome.get("error"):
        lines += ["", "## Error", "", f"    {outcome['error']}"]
    path = job_dir / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(path), "branch": None, "summary": f"{outcome.get('state')}: {job.goal[:80]}"}


# ── Run context handed to runners ─────────────────────────────────────────────


class RunContext:
    def __init__(self, job: JobSpec, queue: JobQueue, journal, stop_flag: Callable[[], bool],
                 log: Callable[[str], None]):
        self.job = job
        self.queue = queue
        self.journal = journal
        self.job_dir = queue.job_dir(job.id)
        self._stop_flag = stop_flag
        self._log = log
        self.replay = None
        if job.attempts > 1:
            try:
                self.replay = journal.replay_plan()
            except Exception as exc:
                log(f"[worker] {job.id[:8]} replay_plan failed: {exc}")

    def log(self, msg: str) -> None:
        self._log(msg)

    def step(self, name: str, **data) -> None:
        """Journal one finished step; a journal failure never fails the job."""
        entry = {"step": name, "idempotency_key": name, "attempt": self.job.attempts,
                 "at": now_iso(), **data}
        try:
            self.journal.append(entry)
        except Exception as exc:
            self._log(f"[worker] {self.job.id[:8]} journal append failed: {exc}")

    def done_steps(self) -> set[str]:
        """Idempotency keys the journal already holds (from earlier attempts)."""
        try:
            steps = self.journal.steps() or []
        except Exception:
            return set()
        keys = set()
        for s in steps:
            if isinstance(s, dict):
                key = s.get("idempotency_key") or s.get("key") or s.get("step")
                if key:
                    keys.add(str(key))
        return keys

    def cancel_requested(self) -> bool:
        try:
            return self.queue.cancel_requested(self.job.id)
        except Exception:
            return False

    def should_stop(self) -> bool:
        return self._stop_flag() or self.cancel_requested()

    def on_child(self, pid: int) -> None:
        from scaffold.agent.job_host import process_start
        self.queue.set_child(self.job.id, pid, process_start(pid))


# ── Runners ───────────────────────────────────────────────────────────────────


def sleep_runner(job: JobSpec, ctx: RunContext) -> dict:
    """
    Model-free runner for demos and tests: AWOS_HOST_FAKE_STEPS steps
    (default 3) of AWOS_HOST_FAKE_STEP_SEC seconds (default 1). Steps the
    journal already holds are skipped; a stop request is honoured between steps.
    """
    steps = int(os.getenv("AWOS_HOST_FAKE_STEPS", "3"))
    step_sec = float(os.getenv("AWOS_HOST_FAKE_STEP_SEC", "1"))
    done = ctx.done_steps()
    ran = skipped = 0
    for i in range(1, steps + 1):
        name = f"fake_step_{i}"
        if name in done:
            skipped += 1
            continue
        if ctx.should_stop():
            raise StopRequested(f"stopped before {name}")
        ctx.log(f"[runner] {job.id[:8]} {name}/{steps} (attempt {job.attempts})")
        time.sleep(step_sec)
        ctx.step(name)
        ran += 1
    return {"success": True, "steps_run": ran, "steps_skipped": skipped, "cost_usd": 0.0}


def _stop_grace_sec() -> float:
    try:
        return float(os.getenv("AWOS_HOST_STOP_GRACE_SEC", "30"))
    except ValueError:
        return 30.0


def child_env(job: JobSpec) -> dict:
    env = {**os.environ, "PYTHONUNBUFFERED": "1",
           "AWOS_MAX_RUN_COST": f"{job.budget_usd:g}",
           "AWOS_JOB_PRIVACY": job.privacy, "AWOS_HOST_JOB_ID": job.id}
    if job.privacy == "local_only":
        # Present-but-empty: dotenv will not refill them, cloud calls cannot authenticate.
        for key in CLOUD_KEYS:
            env[key] = ""
    return env


def subprocess_runner(job: JobSpec, ctx: RunContext) -> dict:
    """
    Default runner: job_host.py's child process (orchestrator, planner path)
    on job.repo_path, output appended to <job_dir>/worker.log.
    """
    from scaffold.agent import job_host

    job_path = ctx.job_dir / "job_host_job.json"
    result_path = ctx.job_dir / "child_result.json"
    result_path.unlink(missing_ok=True)
    job_host.write_json_atomic(job_path, {"id": job.id, "goal": job.goal, "root": job.repo_path,
                                          "ability": job.kind, "attempts": job.attempts})
    timeout = job_host._timeout_sec()
    with open(ctx.job_dir / "worker.log", "a", encoding="utf-8") as log:
        log.write(f"\n===== child, attempt {job.attempts} at {now_iso()} =====\n")
        log.flush()
        proc = subprocess.Popen(
            [sys.executable, str(Path(job_host.__file__).resolve()), "_child",
             str(job_path), str(result_path), str(os.getpid())],
            cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
            env=child_env(job), start_new_session=True)
    ctx.on_child(proc.pid)
    ctx.step("child_started", pid=proc.pid)
    started = time.monotonic()
    while proc.poll() is None:
        if ctx.should_stop():
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=_stop_grace_sec())
            except subprocess.TimeoutExpired:
                job_host._kill_group(proc.pid)
                proc.wait()
            raise StopRequested("stopped while the child was running")
        if time.monotonic() - started > timeout:
            job_host._kill_group(proc.pid)
            proc.wait()
            raise RuntimeError(f"timed out after {timeout / 60:.0f} min (AWOS_JOB_TIMEOUT_MIN)")
        time.sleep(0.5)
    ctx.step("child_exited", code=proc.returncode)
    if not result_path.exists():
        raise RuntimeError(f"child exited with code {proc.returncode} and no result; "
                           f"see {ctx.job_dir / 'worker.log'}")
    return json.loads(result_path.read_text(encoding="utf-8"))


RUNNERS = {"default": subprocess_runner, "subprocess": subprocess_runner, "sleep": sleep_runner}


def resolve_runner(spec: Optional[str]) -> Callable[[JobSpec, RunContext], dict]:
    """'sleep' | 'default' | 'package.module:callable' (AWOS_HOST_RUNNER)."""
    spec = (spec or os.getenv("AWOS_HOST_RUNNER") or "default").strip()
    if spec in RUNNERS:
        return RUNNERS[spec]
    module, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"runner must be one of {sorted(RUNNERS)} or 'module:callable', got {spec!r}")
    return getattr(importlib.import_module(module), attr)


def _kill_orphan(pid: Optional[int], started: Optional[str]) -> None:
    from scaffold.agent.job_host import _kill_group, process_matches
    if process_matches(pid, started) is True:
        _kill_group(pid)


# ── Worker ────────────────────────────────────────────────────────────────────


class HostWorker:
    def __init__(self, queue: Optional[JobQueue] = None,
                 runner: Optional[Callable[[JobSpec, RunContext], dict]] = None,
                 log: Callable[[str], None] = print):
        self.queue = queue or JobQueue()
        self.runner = runner or subprocess_runner
        self._print = log
        self._stop = False
        self._handoff = _load_handoff()

    # logging: stdout + the job's worker.log
    def log(self, msg: str, job_id: Optional[str] = None) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self._print(line)
        if job_id:
            try:
                path = self.queue.job_dir(job_id) / "worker.log"
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def request_stop(self) -> None:
        self._stop = True

    def _on_signal(self, signum, _frame) -> None:
        if self._stop:
            raise KeyboardInterrupt  # second signal: stop now
        self._stop = True
        self.log(f"[worker] signal {signum}: finishing the current step, then exiting")

    def _lock(self):
        handle = open(self.queue.root / "worker.lock", "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError(f"another worker is already serving {self.queue.root}")
        handle.write(str(os.getpid()))
        handle.flush()
        return handle

    def heartbeat(self) -> None:
        try:
            (self.queue.root / "heartbeat").write_text(f"{time.time():.0f} {os.getpid()}\n")
        except OSError:
            pass

    def _hand_off(self, job: JobSpec, outcome: dict) -> Optional[dict]:
        job_dir = self.queue.job_dir(job.id)
        try:
            if self._handoff is not None:
                return self._handoff(job.to_dict(), job.repo_path, outcome)
            return write_minimal_report(job, job_dir, outcome)
        except Exception as exc:  # the job's outcome stands either way
            self.log(f"[worker] {job.id[:8]} handoff failed: {exc}", job.id)
            return {"error": str(exc)}

    def run_job(self, job: JobSpec) -> JobSpec:
        jid = job.id
        self.log(f"[worker] {jid[:8]} running (attempt {job.attempts}): {job.goal[:70]}", jid)
        journal = open_journal(self.queue.job_dir(jid))
        ctx = RunContext(job, self.queue, journal, lambda: self._stop,
                         lambda m: self.log(m, jid))
        ctx.step(f"attempt_{job.attempts}_start")
        try:
            result = self.runner(job, ctx)
        except StopRequested as exc:
            if ctx.cancel_requested():
                return self._finish(job, CANCELLED, {"success": False}, f"cancelled: {exc}")
            self.queue.requeue(jid, refund_attempt=True, error=f"stopped: {exc}")
            self.log(f"[worker] {jid[:8]} stopped cleanly -> queued", jid)
            return self.queue.get(jid)
        except Exception as exc:  # the job failed; the worker carries on
            return self._finish(job, FAILED, {"success": False}, str(exc) or type(exc).__name__)
        result = dict(result or {})
        if result.get("success"):
            return self._finish(job, DONE, result, None)
        return self._finish(job, FAILED, result, result.get("error") or "the runner reported failure")

    def _finish(self, job: JobSpec, state: str, result: dict, error: Optional[str]) -> JobSpec:
        handoff = self._hand_off(job, {"state": state, "result": result, "error": error})
        if handoff is not None:
            result = {**result, "handoff": handoff}
        self.queue.finish(job.id, state, result=result, error=error)
        self.log(f"[worker] {job.id[:8]} {state}" + (f": {error}" if error else ""), job.id)
        return self.queue.get(job.id)

    def serve(self, poll_sec: float = 2.0, once: bool = False) -> None:
        from scaffold.agent.job_host import process_start

        lock = self._lock()
        previous = {}
        if threading.current_thread() is threading.main_thread():
            previous = {s: signal.signal(s, self._on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
        beat_stop = threading.Event()

        def beat() -> None:
            while not beat_stop.is_set():
                self.heartbeat()
                beat_stop.wait(10)

        threading.Thread(target=beat, daemon=True).start()
        me, me_started = os.getpid(), process_start(os.getpid())
        self.log(f"[worker] serving {self.queue.root} (pid {me})")
        try:
            for job in self.queue.recover(kill_child=_kill_orphan):
                self.log(f"[worker] {job.id[:8]} was interrupted -> {job.state} "
                         f"(attempts {job.attempts})", job.id)
            while not self._stop:
                job = self.queue.claim_next(me, me_started)
                if job is None:
                    if once:
                        break
                    time.sleep(poll_sec)
                    continue
                self.run_job(job)
        finally:
            beat_stop.set()
            for s, handler in previous.items():
                signal.signal(s, handler)
            lock.close()
        self.log("[worker] stopped")
