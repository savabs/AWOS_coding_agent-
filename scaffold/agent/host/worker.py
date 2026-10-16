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

from . import autopilot
from . import workspace as _workspace
from .models import BLOCKED, CANCELLED, DONE, FAILED, JobSpec, now_iso
from .queue import JobQueue, max_attempts

from .. import paths as _paths

#: State root: the repo in a checkout, ~/.awos/runtime when installed. The job
#: child runs here, so the relative .awos/budget.json it writes is LEDGER_PATH.
REPO = _paths.state_root()

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


def _free_tier_gate(job: JobSpec):
    """licence.gate_job_start for this job (None if the module is unavailable)."""
    try:
        from scaffold.agent import licence, usage_ping
        return licence.gate_job_start(job.id, usage_ping.provider_kind(privacy=job.privacy))
    except Exception:  # noqa: BLE001 - the gate never breaks the worker
        return None


def _usage_ping_job_finished(job: JobSpec, state: str, result: dict, spent) -> None:
    """Opt-in anonymous count (scaffold/agent/usage_ping.py); never raises."""
    try:
        from scaffold.agent import usage_ping
        usage_ping.job_finished(job, state, result, spent)
    except Exception:  # noqa: BLE001
        pass


def local_only_blanked_keys(env: dict, dotenv_path: Optional[Path] = None) -> set[str]:
    """
    Every credential a local_only child must not hold: CLOUD_KEYS plus any
    *_API_KEY in `env` or in the repo .env (the child loads it with
    override=False, so a name missing from env would be refilled), minus
    LOCAL_KEYS. Fails closed for providers added later.
    """
    names = set(CLOUD_KEYS) | {k for k in env if k.endswith("_API_KEY")}
    path = dotenv_path or _paths.dotenv_path()
    try:
        names |= set(_KEY_NAME.findall(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        pass
    return names - set(LOCAL_KEYS)


def verify_enabled() -> bool:
    """AWOS_HOST_VERIFY_TESTS (default on): the host runs the repo's tests itself."""
    return os.getenv("AWOS_HOST_VERIFY_TESTS", "1").strip().lower() not in ("0", "false", "no")


#: UNSAFE opt-in: when no sandbox backend is available, run the host's own
#: post-run test verification directly on this machine (scrubbed env, but
#: model-written conftest.py / test code then runs with the owner's file
#: access). Off by default: without a sandbox, verification reports an error.
UNSAFE_HOST_VERIFY_ENV = "AWOS_HOST_VERIFY_UNSANDBOXED"


def unsafe_host_verify() -> bool:
    """AWOS_HOST_VERIFY_UNSANDBOXED=1 (UNSAFE, default off)."""
    return os.getenv(UNSAFE_HOST_VERIFY_ENV, "").strip().lower() in ("1", "true", "yes")


def verify_isolation_available() -> bool:
    """True when verify_tests can run: a sandbox backend for AWOS_SANDBOX
    exists, or the owner opted into unsandboxed verification. A cheap probe
    (no container is started)."""
    if unsafe_host_verify():
        return True
    try:
        from scaffold.agent import sandbox as _sb
    except ImportError:  # pragma: no cover
        return False
    choice = (os.environ.get("AWOS_SANDBOX") or "auto").strip().lower()
    if choice == "docker":
        return _sb.docker_available()
    if choice == "seatbelt":
        return _sb.seatbelt_available()
    if choice == "auto":
        return _sb.seatbelt_available() or _sb.docker_available()
    return False


def _verify_sandbox(workspace: str):
    """(sandbox, None) | (None, None) when the unsafe opt-in allows a host run
    | (None, reason) when verification must not run."""
    try:
        from scaffold.agent.sandbox import make_sandbox
        sb = make_sandbox(workspace)
    except Exception as exc:  # noqa: BLE001 - treated as "no backend"
        sb = None
        print(f"[worker] sandbox for verify_tests failed: {exc}")
    if sb is not None:
        return sb, None
    if unsafe_host_verify():
        print(f"[worker] WARNING: no sandbox backend; {UNSAFE_HOST_VERIFY_ENV}=1, so the "
              "job's tests run on this machine (scrubbed env)")
        return None, None
    return None, ("no sandbox backend for the host's test run (AWOS_SANDBOX: docker or "
                  f"seatbelt); not run on the bare host. {UNSAFE_HOST_VERIFY_ENV}=1 runs "
                  "them unsandboxed (unsafe)")


def goal_check_skip_enabled() -> bool:
    """AWOS_HOST_SKIP_GOAL_CHECK (default OFF): skip the LLM goal check when the
    repo has tests. Off by default because existing tests rarely cover a new
    multi-part goal: live proof9 (2026-10-11) came back `done` with only one of
    its parts fixed. Turn on only for jobs whose tests define the goal."""
    return os.getenv("AWOS_HOST_SKIP_GOAL_CHECK", "0").strip().lower() in ("1", "true", "yes")


def has_tests(workspace: str) -> bool:
    """A pytest-style test file anywhere in the workspace."""
    ws = Path(workspace)
    return (next(ws.rglob("test_*.py"), None) is not None
            or next(ws.rglob("*_test.py"), None) is not None)


def child_env(job: JobSpec, budget_left: Optional[float] = None,
              skip_goal_check: bool = False) -> dict:
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
    if skip_goal_check:
        # The host's own test run (verify_tests) is the evidence; the LLM goal
        # check was ~40% of a small job's cost (live run 2026-10-11).
        env["AWOS_GOAL_CHECK"] = "0"
    if job.privacy == "local_only":
        # Present-but-empty: dotenv will not refill them, cloud calls cannot authenticate.
        for key in local_only_blanked_keys(env):
            env[key] = ""
    return env


# ── Spend across attempts (default runner) ───────────────────────────────────


def _ledger_len_and_cost(start: int = 0, pids: Optional[list] = None) -> tuple[int, float]:
    """(ledger length, cost of entries from `start`). With `pids`, only entries
    those processes wrote count (pid-less entries from older writers too):
    the ledger is shared, so another AWOS process must not bill this job."""
    try:
        entries = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0.0
    if not isinstance(entries, list):
        return 0, 0.0
    cost = 0.0
    for i, e in enumerate(entries):
        try:
            pid = e.get("pid")
            if pids and pid in pids:
                # Count by pid wherever it sits: the ledger drops last month's
                # entries at month rollover, so an index taken before the child
                # started can point past the end (live run 2026-10-11: $0.001
                # spent, $0 reported).
                pass
            elif i < start or (pids and pid is not None):
                continue
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

    def set_pid(self, pid: int) -> None:
        """Bill the running attempt only for ledger entries this child writes."""
        data = self._load()
        if data.get("open"):
            data["open"]["pids"] = [pid]
            self._save(data)

    def snapshot(self) -> float:
        data = self._load()
        open_ = data.get("open")
        if not open_:
            return 0.0
        _, spent = _ledger_len_and_cost(int(open_.get("ledger_start", 0)), open_.get("pids"))
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
        result = _run_child(job, ctx, job_path, result_path, left, spend)
    finally:
        total = spend.close()
        ctx.step("spend", total_usd=round(total, 6))
    if isinstance(result, dict):
        # The host's per-pid ledger count is the authority; the child's own
        # figure misses spend when the ledger rotates mid-run.
        try:
            child = float(result.get("cost_usd") or 0)
        except (TypeError, ValueError):
            child = 0.0
        result["cost_usd"] = round(max(child, total - prior), 6)
    return result


WORKSPACE_DIR = "workspace"
#: The untouched checkout of the base commit that pre-existing test failures
#: are checked against (AWOS_BASELINE_WORKSPACE in the child).
BASELINE_DIR = "baseline"
BASE_COMMIT_FILE = "base_commit"
#: Optional: the git ref a follow-up job starts from instead of the repo HEAD.
BASE_REF_FILE = "base_ref"
#: Optional review-branch name for the hand-off (written by `awos fix`).
BRANCH_NAME_FILE = "branch_name"
#: Rollbacks the child recorded instead of applying (git_manager AWOS_DEFER_ROLLBACK).
DEFERRED_ROLLBACK = "deferred_rollback.jsonl"


#: The loop executor's per-turn checkpoint (loop_checkpoint.py) in the job dir.
LOOP_CHECKPOINT_FILE = "loop_checkpoint.jsonl"


def loop_resume_note_path(checkpoint: Path | str) -> Path:
    """<checkpoint>.note: the auto-continue NOTE for a resumed conversation
    (plain UTF-8 text; absent when this attempt has none)."""
    return Path(str(checkpoint) + ".note")


def loop_checkpoint_env(job: JobSpec, job_dir: Path, note: Optional[str]) -> dict:
    """
    The child's loop-checkpoint env, and the resume note on disk:

    - AWOS_LOOP_CHECKPOINT_PATH = <job_dir>/loop_checkpoint.jsonl
    - AWOS_LOOP_CHECKPOINT_GOAL = job.goal, the ORIGINAL goal: the
      orchestrator keys the checkpoint on it, so an auto-continue (which
      appends "NOTE: ..." to the goal the child gets) resumes the paid
      conversation instead of starting over and overwriting it;
    - <checkpoint>.note holds this attempt's NOTE (removed when there is
      none), for the loop executor to append as one extra user message
      after a resume. On a fresh start the NOTE is already in the goal.
    """
    ckpt = Path(job_dir) / LOOP_CHECKPOINT_FILE
    note_path = loop_resume_note_path(ckpt)
    if note:
        note_path.write_text(note, encoding="utf-8")
    else:
        note_path.unlink(missing_ok=True)   # never one from an earlier attempt
    return {"AWOS_LOOP_CHECKPOINT_PATH": str(ckpt), "AWOS_LOOP_CHECKPOINT_GOAL": job.goal}


def settle_rollback(job_dir: Path, keep: bool) -> int:
    """Apply (keep=False) or drop (keep=True) the child's deferred rollback."""
    path = job_dir / DEFERRED_ROLLBACK
    if not path.exists():
        return 0
    if keep:
        path.unlink(missing_ok=True)
        return 0
    from scaffold.agent.git_manager import apply_deferred
    return apply_deferred(str(path))


def prepare_workspace(job: JobSpec, job_dir: Path) -> tuple[str, Optional[str]]:
    """
    The job's own checkout: a detached git worktree of job.repo_path at its HEAD
    (the base commit), under <job_dir>/workspace, reused by later attempts. The
    user's checkout is never edited; hand-off diffs this workspace instead.
    Returns (workspace, base_commit).
    With <job_dir>/base_ref the workspace starts at that ref instead of HEAD.

    A root that is not a git repository with a commit is REFUSED (NonGitRoot):
    a queued job would otherwise edit the user's directory in place with no
    baseline to diff, review or undo. The owner runs `git init && git commit`
    first (the simplest safe option; no silent copy is made).
    """
    ws, base_file = job_dir / WORKSPACE_DIR, job_dir / BASE_COMMIT_FILE
    if ws.is_dir() and base_file.exists():
        base = base_file.read_text(encoding="utf-8").strip()
        _workspace.claim(job.id, job.repo_path, ws, base)   # a retry: this worker owns it now
        return str(ws), base
    # A follow-up job starts from an earlier job's review branch: the submitter
    # writes <job_dir>/base_ref (e.g. "awos/1a2b3c4d") before queueing the job.
    ref_file = job_dir / BASE_REF_FILE
    ref = ref_file.read_text(encoding="utf-8").strip() if ref_file.exists() else ""
    if ref:
        if ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref):
            raise RuntimeError(f"invalid base_ref {ref!r}")
        head = _workspace._git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                               cwd=job.repo_path)
        if head.returncode != 0:
            raise RuntimeError(f"the branch this follow-up starts from ({ref}) no longer exists")
    else:
        head = _workspace._git(["rev-parse", "--verify", "HEAD"], cwd=job.repo_path)
    if head.returncode != 0:
        raise NonGitRoot(non_git_message(job.repo_path))
    base = head.stdout.strip()
    _workspace.add_worktree(job.repo_path, ws, base)
    base_file.write_text(base + "\n", encoding="utf-8")
    _workspace.claim(job.id, job.repo_path, ws, base)
    return str(ws), base


class NonGitRoot(RuntimeError):
    """The job's repo_path is not a git repository with at least one commit."""


def non_git_message(repo_path: str) -> str:
    return (f"refused: {repo_path} is not a git repository with a commit, so the job "
            "cannot get its own worktree and would edit your files in place. Run "
            "`git init && git add -A && git commit -m base` there, then resubmit.")


def prepare_baseline(job: JobSpec, job_dir: Path, base: Optional[str]) -> Optional[str]:
    """
    A second, untouched checkout of the base commit (<job_dir>/baseline): a
    test that fails there too was failing before the job, so it never counts
    against the job's edits (baseline_tests.py). None when not possible.
    """
    if not base or os.getenv("AWOS_HOST_BASELINE", "1").strip().lower() in ("0", "false", "no"):
        return None
    bdir = job_dir / BASELINE_DIR
    if (bdir / ".git").exists():
        return str(bdir)
    try:
        _workspace.add_worktree(job.repo_path, bdir, base)
    except RuntimeError:
        return None
    return str(bdir)


def _remove_worktree(job: JobSpec, path: Path) -> None:
    # Snapshots its state to refs/awos/snapshots/<job>/<ts> first; kept if that fails.
    res = _workspace.remove_worktree(path, job.id, job.repo_path)
    if res.get("error"):
        raise RuntimeError(res["error"])


def remove_workspace(job: JobSpec, job_dir: Path) -> None:
    """Drop the job's worktrees now (each snapshotted to a hidden ref first)."""
    _remove_worktree(job, job_dir / BASELINE_DIR)
    _remove_worktree(job, job_dir / WORKSPACE_DIR)


def retire_workspace(job: JobSpec, job_dir: Path, queue: Optional[JobQueue] = None,
                     log: Optional[Callable[[str], None]] = None) -> dict:
    """The job is final: drop its baseline, keep its workspace for inspection and
    prune the least recently used finished worktrees beyond AWOS_MAX_WORKTREES
    (docs/specs/host_workspace.md)."""
    def is_final(jid: str) -> Optional[bool]:
        got = queue.get(jid) if queue is not None else None
        return None if got is None else got.state in (DONE, FAILED, CANCELLED)
    return _workspace.retire(job.id, job.repo_path, job_dir, is_final=is_final, log=log)


def _run_child(job: JobSpec, ctx: RunContext, job_path: Path, result_path: Path,
               left: float, spend: "SpendTracker") -> dict:
    from scaffold.agent import job_host

    workspace, base = prepare_workspace(job, ctx.job_dir)
    ctx.step("workspace", path=workspace, base_commit=base)
    autopilot.record_attempt_start(ctx.job_dir, workspace, base, job.attempts)
    goal = job.goal
    note = autopilot.take_continue_note(ctx.job_dir)
    if note:   # an auto-continue attempt: same workspace, told where it stopped
        goal = f"{job.goal}\n\nNOTE: {note}"
        ctx.step("auto_continue_note", note=note)
    job_host.write_json_atomic(job_path, {"id": job.id, "goal": goal, "root": workspace,
                                          "ability": job.kind, "attempts": job.attempts})
    timeout = job_host._timeout_sec()
    env = child_env(job, budget_left=left,
                    skip_goal_check=goal_check_skip_enabled() and bool(base)
                    and verify_enabled() and has_tests(workspace)
                    # no sandbox -> the host's tests will not run: keep the goal check
                    and verify_isolation_available())
    # Tests run in the project's env (project_env): its venv sits in the
    # user's checkout, not in the job's worktree.
    env["AWOS_PROJECT_ROOT"] = job.repo_path
    if base:
        # The job's own throwaway worktree: the engine may restore files here
        # (e.g. undo a failed one-shot attempt), never in the user's checkout.
        env["AWOS_JOB_WORKSPACE"] = workspace
        baseline = prepare_baseline(job, ctx.job_dir, base)
        if baseline:
            env["AWOS_BASELINE_WORKSPACE"] = baseline
            ctx.step("baseline", path=baseline)
    deferred = ctx.job_dir / DEFERRED_ROLLBACK
    deferred.unlink(missing_ok=True)
    # The child's green whole-suite run on its final tree, if any: verify_tests
    # reuses it instead of running the suite a second time (tree hash + command
    # must match). Never one from an earlier attempt.
    marker = ctx.job_dir / FULL_SUITE_MARKER
    marker.unlink(missing_ok=True)
    env["AWOS_FULL_SUITE_MARKER"] = str(marker)
    if base:
        # The loop executor saves each finished turn here (loop_checkpoint.py):
        # an attempt after a kill continues the same conversation on the same
        # worktree instead of paying for the turns again. Keyed by goal, model
        # and task, so an attempt with another goal or model starts fresh.
        env.update(loop_checkpoint_env(job, ctx.job_dir, note))
    if base:
        # v0.2.2 atomic outcome: the child never reverts single files in the
        # workspace. Its rollbacks are recorded; the worker applies them only
        # when the job succeeds (a failed sub-task inside a passing job). A
        # failed job keeps everything it did and is handed off, labelled, as
        # awos/<id>-failed: never a half-reverted branch.
        env["AWOS_DEFER_ROLLBACK"] = str(deferred)
    with open(ctx.job_dir / "worker.log", "a", encoding="utf-8") as log:
        log.write(f"\n===== child, attempt {job.attempts} at {now_iso()} =====\n")
        log.flush()
        proc = subprocess.Popen(
            [sys.executable, str(Path(job_host.__file__).resolve()), "_child",
             str(job_path), str(result_path), str(os.getpid())],
            cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
            env=env, start_new_session=True)
    ctx.on_child(proc.pid)
    spend.set_pid(proc.pid)
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
        if "tests" not in result and base:   # only an isolated workspace, never the user's checkout
            tests = verify_tests(workspace, base_commit=base, project_root=job.repo_path,
                                 baseline=env.get("AWOS_BASELINE_WORKSPACE"),
                                 marker=marker)
            if tests:
                result["tests"] = tests
                ctx.step("verify_tests", **tests)
        autopilot.record_tests(ctx.job_dir, result.get("tests"))
    return result


#: Wall-clock cap for the worker's own test run (AWOS_HOST_VERIFY_TIMEOUT_SEC).
VERIFY_TIMEOUT_SEC = 300.0


def changed_paths(workspace: str, base: Optional[str]) -> list[str]:
    """Files the job changed in its workspace (tracked diff vs base + new files)."""
    if not base:
        return []
    out: list[str] = []
    for args in (["diff", "--name-only", "--no-ext-diff", base],
                 ["ls-files", "--others", "--exclude-standard"]):
        try:
            proc = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", workspace, *args],
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            out += [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return list(dict.fromkeys(out))


def _run_summary(res) -> dict:
    """One TestRunner result as report data (only NEW failures count as failed)."""
    if res.timed_out:
        status = "timeout"
    elif res.infra_error or (res.no_tests_found and not res.passed):
        status = "error" if res.infra_error else "no_tests"
    elif res.failed or res.errors:
        status = "failed"
    else:
        status = "passed"
    out = {"status": status, "passed": res.passed, "failed": res.failed + res.errors,
           "preexisting": len(res.preexisting), "baseline_checked": res.baseline_checked}
    if res.infra_error:
        out["reason"] = res.infra_reason[:300]
    return out


def _fmt_run(r: dict) -> str:
    if r["status"] == "timeout":
        return "timed out"
    if r["status"] == "error":
        return "could not run (" + (r.get("reason") or "pytest crashed") + ")"
    if r["status"] == "no_tests":
        return "no tests collected"
    text = f"{r['passed']} passed, {r['failed']} new failure{'s' if r['failed'] != 1 else ''}"
    if r.get("preexisting"):
        text += f"; pre-existing failures: {r['preexisting']} (unchanged)"
    return text


#: In the job dir: the child's green whole-suite run (test_runner marker).
FULL_SUITE_MARKER = "full_suite_green.json"


def reused_full_suite(marker: Optional[Path], workspace: str, runner) -> Optional[dict]:
    """
    The child's full-suite result as a verify_tests summary, when the child
    ran the whole suite green on exactly this tree (same tree hash of tracked
    + untracked files, same pytest command); else None and the host runs it.
    """
    if marker is None:
        return None
    try:
        data = json.loads(Path(marker).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        from scaffold.agent.test_runner import workspace_tree_hash
        if data.get("command") != runner.full_suite_key():
            print("[worker] child full-suite result not reused: different test command")
            return None
        if data.get("tree") != workspace_tree_hash(workspace):
            print("[worker] child full-suite result not reused: the tree changed since")
            return None
    except Exception as exc:  # noqa: BLE001 - fall back to running it
        print(f"[worker] child full-suite result not reused: {exc}")
        return None
    passed = int(data.get("passed") or 0)
    pre = data.get("preexisting") or []
    print(f"[worker] reused child full-suite result ({passed} passed on the same tree); "
          "full suite not run again")
    return {"status": "passed", "passed": passed, "failed": 0, "preexisting": len(pre),
            "baseline_checked": bool(data.get("baseline_checked")), "seconds": 0.0,
            "reused": True}


def verify_tests(workspace: str, base_commit: Optional[str] = None,
                 project_root: Optional[str] = None,
                 baseline: Optional[str] = None,
                 marker: Optional[Path] = None) -> Optional[dict]:
    """
    The host's own test evidence after the child exits (v0.2.2):

    - in the PROJECT's environment (project_env: its .venv/uv/poetry env), with
      the job's workspace first on PYTHONPATH;
    - targeted first: the test files mapped to the files the job changed (and
      the test files it changed), then the full suite with a time cap;
    - only NEW failures count: a failing test that also fails in the untouched
      baseline checkout is reported as pre-existing.

    `line` is the honest one-line summary the report and notifications show.
    None when there are no tests or AWOS_HOST_VERIFY_TESTS=0.

    The job's tests are model-written code (conftest.py included): they run
    inside a sandbox on the workspace (make_sandbox, like the loop's gate),
    never on the bare host. With no backend the result is status "error"
    (reason "no_sandbox") unless AWOS_HOST_VERIFY_UNSANDBOXED=1 (unsafe);
    a host run always gets a scrubbed env (test_runner.scrub_env).
    """
    if not verify_enabled() or not has_tests(workspace):
        return None
    from scaffold.agent import project_env
    from scaffold.agent.test_runner import TestRunner

    sandbox, refused = _verify_sandbox(workspace)
    if refused:
        return {"status": "error", "passed": 0, "failed": 0, "preexisting": 0,
                "command": "", "reason": "no_sandbox", "sandboxed": False,
                "line": f"tests not run: {refused}", "full": None, "targeted": None}
    timeout = float(os.getenv("AWOS_HOST_VERIFY_TIMEOUT_SEC", VERIFY_TIMEOUT_SEC))
    tenv = project_env.detect(workspace, project_root)
    runner = TestRunner(project_root=workspace, timeout_sec=int(timeout), test_env=tenv,
                        sandbox=sandbox)
    saved = os.environ.get("AWOS_BASELINE_WORKSPACE")
    if baseline:
        os.environ["AWOS_BASELINE_WORKSPACE"] = baseline
    try:
        targets = runner.map_tests(changed_paths(workspace, base_commit))
        targeted = None
        if targets:
            targeted = _run_summary(runner.run_targets(targets, timeout_sec=int(timeout)))
            targeted["files"] = targets
        started = time.monotonic()
        full = reused_full_suite(marker, workspace, runner)
        if full is None:
            # A module that cannot be imported (an optional extra such as torch)
            # must not stop the whole suite: its collection error is one id, and
            # the baseline tells whether it was already broken.
            full = _run_summary(runner.run_targets(runner._full_suite_targets(),
                                                   timeout_sec=int(timeout),
                                                   extra_args=["--continue-on-collection-errors"]))
            full["seconds"] = round(time.monotonic() - started, 1)
    finally:
        if sandbox is not None:
            try:
                sandbox.close()   # a docker sandbox holds a container until closed
            except Exception:  # noqa: BLE001 - cleanup only
                pass
        if baseline:
            if saved is None:
                os.environ.pop("AWOS_BASELINE_WORKSPACE", None)
            else:
                os.environ["AWOS_BASELINE_WORKSPACE"] = saved

    shown = tenv.command_str()
    tgt_text = ""
    if targeted:
        names = ", ".join(targeted["files"][:4]) + (" ..." if len(targeted["files"]) > 4 else "")
        tgt_text = f"targeted tests ({names}): {_fmt_run(targeted)}"
    if full["status"] in ("passed", "failed"):
        main = full
        line = f"full suite: {_fmt_run(full)}"
        if full.get("reused"):
            line += " (reused child full-suite result on the same tree)"
        if targeted:
            line += f"; {tgt_text}"
    elif targeted:
        main = targeted
        why = (f"full suite timed out after {timeout:.0f}s" if full["status"] == "timeout"
               else f"full suite {_fmt_run(full)}")
        line = f"{why}; {tgt_text}"
    else:
        main = full
        line = (f"full suite timed out after {timeout:.0f}s; no targeted tests matched the changed files"
                if full["status"] == "timeout" else f"full suite: {_fmt_run(full)}")
    line += f" — via {tenv.describe() if tenv.is_fallback else tenv.short() + ' (' + tenv.source + ')'}"
    out = {"status": main["status"] if main["status"] != "no_tests" else "error",
           "passed": main["passed"], "failed": main["failed"],
           "preexisting": main.get("preexisting", 0), "command": shown,
           "env": tenv.describe(), "env_source": tenv.source, "line": line,
           "full": full, "targeted": targeted,
           "sandboxed": sandbox is not None,
           "sandbox": getattr(sandbox, "backend", None) if sandbox is not None else None}
    if sandbox is None:
        out["line"] += f" (UNSANDBOXED: {UNSAFE_HOST_VERIFY_ENV}=1)"
    notes = [n for n in tenv.notes if n]
    if notes:
        out["notes"] = notes[:4]
    return out


def _final_summary(job: JobSpec, state: str, result: dict, error: Optional[str]) -> str:
    """One line for the owner: state, goal, tests, cost, branch (or the error)."""
    from .handoff import commit_subject
    goal = commit_subject(job.goal, limit=60) if job.goal.strip() else job.id[:8]
    parts = [f"{state}: {goal}"]
    tests = result.get("tests")
    if isinstance(tests, dict):
        parts.append(f"tests {tests.get('status')} {tests.get('passed', 0)}p/{tests.get('failed', 0)}f")
    try:
        parts.append(f"${float(result.get('cost_usd') or 0):.4f}")
    except (TypeError, ValueError):
        pass
    branch = (result.get("handoff") or {}).get("branch")
    if branch and state != DONE:
        # A failed job's branch holds everything it did, unverified: say so.
        parts.append(f"branch {branch} ({state}: everything the job did, for salvage only)")
    elif branch:
        parts.append(f"branch {branch}")
    if error:
        parts.append(error[:120])
    return " | ".join(parts)


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
                 log: Callable[[str], None] = print,
                 key_checker: Optional[Callable[[], list]] = None,
                 notifier: Optional[Callable[..., Any]] = None):
        self.queue = queue or JobQueue()
        self.runner = runner or subprocess_runner
        self._print = log
        self._stop = False
        self._handoff = _load_handoff()
        # Key checks hit the network (free endpoints): wired by default only for
        # the real runner; tests inject a fake checker. AWOS_HOST_KEY_CHECK=0
        # skips the check before each job; an auth error is still diagnosed.
        if key_checker is None and self.runner is subprocess_runner:
            from scaffold.agent.key_health import check_all
            key_checker = check_all
        self.key_checker = key_checker
        self.precheck = os.getenv("AWOS_HOST_KEY_CHECK", "1").strip().lower() not in ("0", "false", "no")
        if notifier is None:
            from scaffold.agent.notify import notify as notifier
        self.notifier = notifier
        self._last_unblock_check = float("-inf")

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
                name_file = job_dir / BRANCH_NAME_FILE
                if name_file.exists():
                    spec["branch_name"] = name_file.read_text(encoding="utf-8").strip()
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
        problem = self._credentials_problem(job) if self.precheck else None
        if problem:
            return self._block(job, problem, "before the run")
        gate = _free_tier_gate(job)
        if gate is not None and not gate.allowed:
            return self._block_free_tier(job, gate.message)
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
            return self._after_failure(job, {"success": False}, str(exc) or type(exc).__name__)
        result = dict(result or {})
        if result.get("success"):
            self._apply_rollback(job, result)
            return self._finish(job, DONE, result, None)
        return self._after_failure(job, result, result.get("error") or "the runner reported failure")

    def _apply_rollback(self, job: JobSpec, result: dict) -> None:
        """Apply the child's deferred rollback (the pre-autopilot behaviour); the
        host's test evidence is re-taken if it changed the workspace."""
        jdir = self.queue.job_dir(job.id)
        try:
            touched = settle_rollback(jdir, keep=False)
        except Exception as exc:
            self.log(f"[worker] {job.id[:8]} deferred rollback failed: {exc}", job.id)
            return
        if touched:
            self.log(f"[worker] {job.id[:8]} applied deferred rollback ({touched} file(s))", job.id)
            ws = result.get("workspace")
            if result.get("tests") and ws and result.get("base_commit"):
                bdir = jdir / BASELINE_DIR
                tests = verify_tests(ws, base_commit=result.get("base_commit"),
                                     project_root=job.repo_path,
                                     baseline=str(bdir) if bdir.is_dir() else None)
                if tests:
                    result["tests"] = tests

    # ── supervision autopilot (docs/specs/supervision_autopilot.md) ──────────

    def _credentials_problem(self, job: JobSpec) -> Optional[dict]:
        if self.key_checker is None:
            return None
        from scaffold.agent.key_health import credentials_problem
        try:
            checks = self.key_checker()
        except Exception as exc:  # a broken check must not block work
            self.log(f"[worker] {job.id[:8]} key check failed: {exc}", job.id or None)
            return None
        problem = credentials_problem(checks, job.privacy)
        if problem:
            problem["checks"] = [c.to_dict() if hasattr(c, "to_dict") else c for c in checks]
        return problem

    def _after_failure(self, job: JobSpec, result: dict, error: str) -> JobSpec:
        """A failed attempt: credentials -> blocked; limit + progress -> one continuation; else failed."""
        jdir = self.queue.job_dir(job.id)
        kind = autopilot.classify_stop(error, result, autopilot.log_tail(jdir))
        if kind == autopilot.CREDENTIALS:
            problem = self._credentials_problem(job) or {
                "provider": "unknown", "status": "auth_error",
                "action": "check the provider API key in .env (run `awos keys`)",
                "summary": f"the provider rejected the credentials: {error[:120]}"}
            # Blocked jobs resume on the same workspace: keep it whole, never half-reverted.
            settle_rollback(jdir, keep=True)
            return self._block(job, problem, "after an auth error")
        ws = result.get("workspace") or (str(jdir / WORKSPACE_DIR) if (jdir / WORKSPACE_DIR).is_dir() else "")
        base = result.get("base_commit")
        if not base and (jdir / BASE_COMMIT_FILE).exists():
            base = (jdir / BASE_COMMIT_FILE).read_text(encoding="utf-8").strip()
        go, why = autopilot.continuation_decision(job, jdir, kind, error, ws, base, result.get("tests"))
        if go:
            settle_rollback(jdir, keep=True)   # the next attempt builds on these edits
            summary = autopilot.stop_summary(error, result, autopilot.log_tail(jdir), ws, base)
            data = autopilot.queue_continuation(jdir, summary, why)
            if self.queue.requeue(job.id, error=f"auto-continue {data['used']}: {error[:200]}"):
                self.log(f"[worker] {job.id[:8]} {kind} stop with progress ({why}) -> "
                         f"auto-continue {data['used']}/{autopilot.max_continuations()}", job.id)
                self._notify("continued", job,
                             f"auto-continue {data['used']}: {why}; stopped on: {error[:100]}",
                             str(jdir / "worker.log"))
                return self.queue.get(job.id)
        # v0.2.2 atomic outcome: a failed job keeps EVERYTHING it did (no
        # per-file rollback) and is handed off as awos/<id>-failed, clearly
        # labelled, so the human can salvage it. Never a half-reverted branch.
        settle_rollback(jdir, keep=True)
        result = {**result, "autocontinue": {"kind": kind, "continued": False, "reason": why}}
        if kind == autopilot.LIMIT:
            error = f"{error} (no auto-continue: {why})"
        return self._finish(job, FAILED, result, error)

    def _block(self, job: JobSpec, problem: dict, when: str) -> JobSpec:
        jdir = self.queue.job_dir(job.id)
        action = problem.get("action") or "run `awos keys`"
        reason = f"blocked: credentials ({problem.get('provider')} {problem.get('status')})"
        report = jdir / "blocked.md"
        try:
            report.write_text("\n".join([
                f"# Job {job.id} — blocked: credentials", "",
                f"- **Goal:** {job.goal}", f"- **When:** {when}",
                f"- **Problem:** {problem.get('summary')}", f"- **Do:** {action}",
                "", "The job keeps its workspace. Once `awos keys` shows ok, the worker",
                f"re-queues it by itself (or run `awos unblock {job.id[:8]}`).", ""]),
                encoding="utf-8")
        except OSError:
            pass
        dedupe = f"blocked|credentials|{problem.get('provider')}|{problem.get('status')}"
        result = {"success": False, "blocked": "credentials", "action": action,
                  "problem": {k: v for k, v in problem.items() if k != "checks"},
                  "keys": problem.get("checks"), "dedupe_key": dedupe}
        self.queue.block(job.id, reason, result=result)
        self.log(f"[worker] {job.id[:8]} {reason} {when} -> {action}", job.id)
        # One notification per credential problem, however many jobs it parks.
        self._notify("blocked", job, f"{reason}: {action}", str(report),
                     dedupe_key=dedupe)
        self._write_digest()
        return self.queue.get(job.id)

    def unblock_if_keys_ok(self, min_interval: float = 60.0) -> list[str]:
        """Re-queue credential-blocked jobs once the keys check out again."""
        if self.key_checker is None:
            return []
        blocked = [j for j in self.queue.list((BLOCKED,))
                   if (j.result or {}).get("blocked") == "credentials"]
        if not blocked or time.monotonic() - self._last_unblock_check < min_interval:
            return []
        self._last_unblock_check = time.monotonic()
        freed = []
        for job in blocked:
            if self._credentials_problem(job) is None and self.queue.unblock(job.id):
                freed.append(job.id)
                self.log(f"[worker] {job.id[:8]} credentials ok again -> queued", job.id)
                # resolved: the next credentials problem must notify again
                from scaffold.agent.notify import forget
                forget((job.result or {}).get("dedupe_key") or "", root=self.queue.root)
        if freed:
            self._notify("unblocked", JobSpec(id="", goal="", repo_path=""),
                         f"credentials ok again; {len(freed)} job(s) re-queued", "",
                         dedupe_key=f"unblocked|{now_iso()}")
        return freed

    def _notify(self, event: str, job: JobSpec, summary: str, report: str,
                dedupe_key: Optional[str] = None) -> None:
        try:
            self.notifier(event, job.id, summary, report, root=self.queue.root,
                          dedupe_key=dedupe_key)
        except Exception as exc:
            self.log(f"[worker] notify failed: {exc}")

    def _write_digest(self) -> None:
        try:
            autopilot.write_digest(self.queue)
        except Exception as exc:
            self.log(f"[worker] digest failed: {exc}")

    def _finish(self, job: JobSpec, state: str, result: dict, error: Optional[str]) -> JobSpec:
        # hand-off reads tests/cost_usd/minutes/summary at the top level.
        outcome = {**{k: v for k, v in result.items() if k != "handoff"},
                   "state": state, "result": result, "error": error}
        if error and not outcome.get("summary"):
            outcome["summary"] = error
        handoff = self._hand_off(job, outcome)
        if handoff is not None:
            result = {**result, "handoff": handoff}
        try:
            retire_workspace(job, self.queue.job_dir(job.id), self.queue,
                             lambda m: self.log(m, job.id))
        except Exception as exc:  # cleanup never changes the outcome
            self.log(f"[worker] {job.id[:8]} workspace cleanup failed: {exc}", job.id)
        self.queue.finish(job.id, state, result=result, error=error)
        self.log(f"[worker] {job.id[:8]} {state}" + (f": {error}" if error else ""), job.id)
        spent = autopilot.spent_so_far(self.queue.job_dir(job.id))
        try:
            shown = {**result, "cost_usd": max(float(result.get("cost_usd") or 0), spent)}
        except (TypeError, ValueError):
            shown = result
        self._notify(state, job, _final_summary(job, state, shown, error),
                     (handoff or {}).get("report_path") or (handoff or {}).get("report")
                     or str(self.queue.job_dir(job.id) / "report.md"))
        self._write_digest()
        _usage_ping_job_finished(self.queue.get(job.id) or job, state, result, spent)
        return self.queue.get(job.id)

    def _block_free_tier(self, job: JobSpec, message: str) -> JobSpec:
        """Free-tier limit reached: park the job unstarted (scaffold/agent/licence.py)."""
        action = f"get AWOS Pro, `awos activate <key>`, then `awos unblock {job.id[:8]}`"
        result = {"success": False, "blocked": "free_tier", "action": action,
                  "summary": message, "dedupe_key": f"blocked|free_tier|{job.id}"}
        self.queue.block(job.id, "blocked: free tier limit", result=result)
        self.log(f"[worker] {job.id[:8]} blocked: free tier limit -> {action}", job.id)
        self._notify("blocked", job, message, str(self.queue.job_dir(job.id)),
                     dedupe_key=result["dedupe_key"])
        self._write_digest()
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
                self.unblock_if_keys_ok()
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
