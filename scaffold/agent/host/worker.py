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
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .models import CANCELLED, DONE, FAILED, JobSpec, now_iso
from .queue import JobQueue, max_attempts

REPO = Path(__file__).resolve().parents[3]

#: Cloud credentials blanked in the child env of a local_only job (fail closed).
#: Not exhaustive on purpose: every *_API_KEY in the env or the repo .env is
#: blanked too, except LOCAL_KEYS (see local_only_blanked_keys).
CLOUD_KEYS = (
    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENCODE_GO_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "LLM_API_KEY",
    "TAVILY_API_KEY", "SERP_API_KEY",
)
#: Keys a local_only job keeps: they authenticate the local model server only.
LOCAL_KEYS = ("AWOS_LOCAL_API_KEY", "LOCAL_API_KEY")
_KEY_NAME = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*_API_KEY)\s*=", re.M)

#: The spend ledger every AWOS process appends to (job_host._run_coding diffs it).
LEDGER_PATH = REPO / ".awos" / "budget.json"
#: The legacy `awos host` lock. The default runner holds it too, so the two
#: hosts never run jobs at once and mix their spend in the shared ledger.
LEGACY_HOST_LOCK = REPO / ".awos" / "jobs" / "host.lock"
#: Journal kind for the worker's own lifecycle steps (attempt start, child pid, spend).
WORKER_STEP = "worker_step"
#: How often (seconds) the default runner snapshots a running attempt's spend.
SPEND_SNAPSHOT_SEC = 5.0


class StopRequested(Exception):
    """Raised by a runner at a step boundary when ctx.should_stop() is true."""


class RetryableError(Exception):
    """
    Raised by a runner for a failure worth another attempt (the child was
    killed by a signal, e.g. OOM, before it wrote a result). The job is
    re-queued while attempts remain (AWOS_HOST_MAX_ATTEMPTS), else it fails.
    Any other exception, a timeout included, fails the job at once.
    """


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
        # The journal keys records by (key, kind); "step"/"idempotency_key" stay
        # for done_steps() and older journals.
        entry = {"kind": WORKER_STEP, "key": name, "status": "done",
                 "step": name, "idempotency_key": name, "attempt": self.job.attempts,
                 "at": now_iso(), "payload": data}
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


def local_only_blanked_keys(env: dict, dotenv_path: Optional[Path] = None) -> set[str]:
    """
    Every credential a local_only child must not hold: CLOUD_KEYS plus any
    *_API_KEY in `env` or in the repo .env (the child loads it with
    override=False, so a name missing from env would be refilled), minus
    LOCAL_KEYS. Fails closed for providers added later.
    """
    names = set(CLOUD_KEYS) | {k for k in env if k.endswith("_API_KEY")}
    path = dotenv_path or (REPO / ".env")
    try:
        names |= set(_KEY_NAME.findall(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        pass
    return names - set(LOCAL_KEYS)


def child_env(job: JobSpec, budget_left: Optional[float] = None) -> dict:
    """
    The orchestrator child's env. budget_usd bounds the whole job: it sets the
    goal cap (AWOS_GOAL_BUDGET_USD) and the per-task cap (AWOS_MAX_RUN_COST)
    to what is left of it (`budget_left`, after earlier attempts' spend).
    budget_usd == 0 means no cap, as AWOS_GOAL_BUDGET_USD=0 does.
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1",
           "AWOS_JOB_PRIVACY": job.privacy, "AWOS_HOST_JOB_ID": job.id,
           # the job's workspace is a throwaway worktree; hand-off makes the branch
           "AWOS_GIT_BRANCH": "0"}
    if job.budget_usd > 0:
        left = job.budget_usd if budget_left is None else max(0.0, budget_left)
        cap = f"{round(left, 6):g}"
        env["AWOS_GOAL_BUDGET_USD"] = cap
        env["AWOS_MAX_RUN_COST"] = cap
    else:
        env["AWOS_GOAL_BUDGET_USD"] = "0"
        env.pop("AWOS_MAX_RUN_COST", None)
    if job.privacy == "local_only":
        # Present-but-empty: dotenv will not refill them, cloud calls cannot authenticate.
        for key in local_only_blanked_keys(env):
            env[key] = ""
    return env


# ── Spend across attempts (default runner) ───────────────────────────────────


def _ledger_len_and_cost(start: int = 0) -> tuple[int, float]:
    try:
        entries = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0.0
    if not isinstance(entries, list):
        return 0, 0.0
    cost = 0.0
    for e in entries[start:]:
        try:
            cost += float(e.get("cost", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            pass
    return len(entries), cost


class SpendTracker:
    """
    <job_dir>/spend.json: what earlier attempts of a job spent, so a retry
    gets only the rest of budget_usd. The running attempt's spend (ledger
    entries since it started) is snapshotted every SPEND_SNAPSHOT_SEC; an
    attempt killed with the worker counts its last snapshot.
    """

    def __init__(self, job_dir: Path):
        self.path = job_dir / "spend.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        from scaffold.agent.job_host import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, data)

    def prior(self) -> float:
        """Spend of finished and interrupted attempts (folds an interrupted one in)."""
        data = self._load()
        prior = float(data.get("prior_usd", 0) or 0)
        open_ = data.get("open")
        if open_:
            prior += float(open_.get("spent_usd", 0) or 0)
            self._save({"prior_usd": prior, "open": None})
        return prior

    def start(self, attempt: int) -> None:
        start, _ = _ledger_len_and_cost(0)
        data = self._load()
        data["open"] = {"attempt": attempt, "ledger_start": start, "spent_usd": 0.0}
        self._save(data)

    def snapshot(self) -> float:
        data = self._load()
        open_ = data.get("open")
        if not open_:
            return 0.0
        _, spent = _ledger_len_and_cost(int(open_.get("ledger_start", 0)))
        open_["spent_usd"] = max(spent, float(open_.get("spent_usd", 0) or 0))
        self._save(data)
        return open_["spent_usd"]

    def close(self) -> float:
        """Fold the running attempt into prior_usd; returns the new total."""
        self.snapshot()
        return self.prior()


def _legacy_host_lock():
    """Hold the legacy `awos host` lock (non-blocking) or raise."""
    LEGACY_HOST_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LEGACY_HOST_LOCK, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(
            f"the legacy `awos host` is running ({LEGACY_HOST_LOCK}); the default runner "
            "shares its spend ledger, so it must not run jobs at the same time")
    return handle


def subprocess_runner(job: JobSpec, ctx: RunContext) -> dict:
    """
    Default runner: job_host.py's child process (orchestrator, planner path)
    on job.repo_path, output appended to <job_dir>/worker.log.
    """
    from scaffold.agent import job_host

    job_path = ctx.job_dir / "job_host_job.json"
    result_path = ctx.job_dir / "child_result.json"
    result_path.unlink(missing_ok=True)
    spend = SpendTracker(ctx.job_dir)
    prior = spend.prior()
    left = job.budget_usd - prior
    if job.budget_usd > 0 and left <= 0:
        raise RuntimeError(f"budget ${job.budget_usd:g} used up by earlier attempts "
                           f"(spent ${prior:.4f})")
    spend.start(job.attempts)
    try:
        return _run_child(job, ctx, job_path, result_path, left, spend)
    finally:
        total = spend.close()
        ctx.step("spend", total_usd=round(total, 6))


WORKSPACE_DIR = "workspace"
BASE_COMMIT_FILE = "base_commit"


def prepare_workspace(job: JobSpec, job_dir: Path) -> tuple[str, Optional[str]]:
    """
    The job's own checkout: a detached git worktree of job.repo_path at its HEAD
    (the base commit), under <job_dir>/workspace, reused by later attempts. The
    user's checkout is never edited; hand-off diffs this workspace instead.
    Returns (workspace, base_commit). A non-git root runs in place (base None).
    """
    ws, base_file = job_dir / WORKSPACE_DIR, job_dir / BASE_COMMIT_FILE
    if ws.is_dir() and base_file.exists():
        return str(ws), base_file.read_text(encoding="utf-8").strip()
    head = subprocess.run(["git", "-C", job.repo_path, "rev-parse", "--verify", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode != 0:
        return job.repo_path, None
    base = head.stdout.strip()
    add = subprocess.run(["git", "-C", job.repo_path, "worktree", "add", "--detach", str(ws), base],
                         capture_output=True, text=True)
    if add.returncode != 0:
        raise RuntimeError(f"could not create the job workspace: {add.stderr.strip()}")
    base_file.write_text(base + "\n", encoding="utf-8")
    return str(ws), base


def remove_workspace(job: JobSpec, job_dir: Path) -> None:
    """Drop the job's worktree once the job is final; the review branch holds the result."""
    ws = job_dir / WORKSPACE_DIR
    if not ws.exists():
        return
    rm = subprocess.run(["git", "-C", job.repo_path, "worktree", "remove", "--force", str(ws)],
                        capture_output=True, text=True)
    if rm.returncode != 0:
        shutil.rmtree(ws, ignore_errors=True)
        subprocess.run(["git", "-C", job.repo_path, "worktree", "prune"], capture_output=True)


def _run_child(job: JobSpec, ctx: RunContext, job_path: Path, result_path: Path,
               left: float, spend: "SpendTracker") -> dict:
    from scaffold.agent import job_host

    workspace, base = prepare_workspace(job, ctx.job_dir)
    ctx.step("workspace", path=workspace, base_commit=base)
    job_host.write_json_atomic(job_path, {"id": job.id, "goal": job.goal, "root": workspace,
                                          "ability": job.kind, "attempts": job.attempts})
    timeout = job_host._timeout_sec()
    with open(ctx.job_dir / "worker.log", "a", encoding="utf-8") as log:
        log.write(f"\n===== child, attempt {job.attempts} at {now_iso()} =====\n")
        log.flush()
        proc = subprocess.Popen(
            [sys.executable, str(Path(job_host.__file__).resolve()), "_child",
             str(job_path), str(result_path), str(os.getpid())],
            cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
            env=child_env(job, budget_left=left), start_new_session=True)
    ctx.on_child(proc.pid)
    ctx.step("child_started", pid=proc.pid)
    started = last_snap = time.monotonic()
    while proc.poll() is None:
        if time.monotonic() - last_snap >= SPEND_SNAPSHOT_SEC:
            spend.snapshot()
            last_snap = time.monotonic()
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
        if proc.returncode is not None and proc.returncode < 0:
            raise RetryableError(f"child killed by signal {-proc.returncode} before it wrote "
                                 f"a result; see {ctx.job_dir / 'worker.log'}")
        raise RuntimeError(f"child exited with code {proc.returncode} and no result; "
                           f"see {ctx.job_dir / 'worker.log'}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if isinstance(result, dict):
        result.setdefault("workspace", workspace)
        if base:
            result.setdefault("base_commit", base)
    return result


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
        # "a+", not "w": a worker that loses the race must not wipe the
        # live worker's pid. Truncate only once the lock is ours.
        handle = open(self.queue.root / "worker.lock", "a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError(f"another worker is already serving {self.queue.root}")
        handle.seek(0)
        handle.truncate()
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
                # Diff the job's own workspace, never the user's checkout (which
                # may hold the user's uncommitted work).
                ws = job_dir / WORKSPACE_DIR
                workspace = str(ws) if ws.is_dir() else job.repo_path
                spec = job.to_dict()
                base_file = job_dir / BASE_COMMIT_FILE
                if base_file.exists() and not spec.get("base_commit"):
                    spec["base_commit"] = base_file.read_text(encoding="utf-8").strip()
                return self._handoff(spec, workspace, outcome)
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
        except RetryableError as exc:
            if job.attempts < max_attempts():
                self.queue.requeue(jid, error=f"attempt {job.attempts}: {exc}")
                self.log(f"[worker] {jid[:8]} {exc} -> queued (attempts {job.attempts})", jid)
                return self.queue.get(jid)
            return self._finish(job, FAILED, {"success": False},
                                f"{exc} (attempt {job.attempts} of {max_attempts()})")
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
        try:
            remove_workspace(job, self.queue.job_dir(job.id))
        except Exception as exc:  # cleanup never changes the outcome
            self.log(f"[worker] {job.id[:8]} workspace cleanup failed: {exc}", job.id)
        self.queue.finish(job.id, state, result=result, error=error)
        self.log(f"[worker] {job.id[:8]} {state}" + (f": {error}" if error else ""), job.id)
        return self.queue.get(job.id)

    def serve(self, poll_sec: float = 2.0, once: bool = False) -> None:
        from scaffold.agent.job_host import process_start

        lock = self._lock()
        legacy = None
        if self.runner is subprocess_runner:
            try:
                legacy = _legacy_host_lock()
            except RuntimeError:
                lock.close()
                raise
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
            if legacy is not None:
                legacy.close()
            lock.close()
        self.log("[worker] stopped")
