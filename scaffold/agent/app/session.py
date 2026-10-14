"""
session.py — the interactive AWOS app (`awos` with no args) and the one-shot
form (`awos "fix X"`).

Outside it looks like a chat: type a task, watch friendly progress, review the
result. Underneath every task is an ordinary host-queue job run by the
background worker (`awos serve --worker`), so closing the terminal never stops
work and reopening `awos` picks the results up again.

Spec: docs/specs/awos_app.md.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from scaffold.agent.host.models import (
    BLOCKED,
    CANCELLED,
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    JobSpec,
)
from scaffold.agent.host.queue import JobQueue

from . import gitops, workerctl
from . import jobs as J
from .detect import detect_test_command
from .progress import JobTail
from .ui import Term

HELP = """\
Type a task in plain English and press Enter; it is queued and run in the
background. Several tasks can wait in the queue.

Review the latest finished job with a single letter (or add a job id):
  d  diff        m  merge into your current branch     r  reject (delete branch)
  f  follow-up   o  open the report

Commands:
  /status              queue and recent results for this repo
  /diff [id]           show a job's changes          /merge [id]    merge a job's branch
  /reject [id]         delete a job's branch          /follow [id] <task>  continue a job
  /open [id]           show a job's report            /cancel <id>   cancel a job
  /watch               follow running jobs live (Ctrl-C to stop watching)
  /keys                check provider keys (no spend) /help          this help
  /quit                leave (background jobs keep running; Ctrl-C works too)"""


def _setup_log(root: Path) -> Path:
    path = root / "app.log"
    logger = logging.getLogger("awos.app")
    if not logger.handlers:
        root.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return path


class App:
    def __init__(self, repo: Path, term: Optional[Term] = None, queue: Optional[JobQueue] = None,
                 poll_sec: Optional[float] = None, auto_worker: bool = True):
        self.repo = Path(repo).resolve()
        self.term = term or Term()
        self.queue = queue or JobQueue()
        self.root = self.queue.root
        self.log_path = _setup_log(self.root)
        self.log = logging.getLogger("awos.app")
        self.poll = poll_sec if poll_sec is not None else float(os.getenv("AWOS_APP_POLL", "0.5"))
        self.auto_worker = auto_worker and os.getenv("AWOS_APP_NO_WORKER", "") not in ("1", "true")
        self.tails: dict[str, JobTail] = {}
        self.states: dict[str, str] = {}
        self.current: Optional[str] = None       # latest finished job, for d/m/r/f/o
        self.ready: dict = {}
        self._stop = threading.Event()
        self._monitor: Optional[threading.Thread] = None
        self._events: dict[str, threading.Event] = {}
        self.compact_cards = False                # one-shot shows its own review keys
        # While jobs wait, re-check the worker every N seconds (it can die mid-session).
        self.worker_check_sec = float(os.getenv("AWOS_APP_WORKER_CHECK_SEC", "15"))
        self._last_worker_check = time.monotonic()
        self._worker_lock = threading.Lock()   # one restart attempt at a time per app

    # ── plumbing ─────────────────────────────────────────────────────────
    def fail(self, what: str, exc: BaseException) -> None:
        """No raw tracebacks for the user: one line here, the details in app.log."""
        self.log.error("%s failed:\n%s", what, "".join(traceback.format_exception(exc)))
        msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        self.term.line(self.term.s(f"  {what} failed: {msg[:160]}", "red"))
        self.term.line(self.term.s(f"  (details in {self.log_path})", "grey"))

    def onboarding(self) -> None:
        try:
            from scaffold.agent.onboarding import ensure_ready  # provided by the installer work
        except ImportError:
            return
        try:
            self.ready = ensure_ready(self.repo, interactive=self.term.tty) or {}
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as exc:
            self.fail("first-run setup", exc)

    def ensure_worker(self) -> str:
        if not self.auto_worker:
            return "running" if workerctl.worker_running(self.root) else "off"
        try:
            state = workerctl.ensure_worker(self.root)
        except Exception as exc:
            self.fail("starting the background worker", exc)
            return "error"
        if state == "failed":
            self.term.line(self.term.s(
                "  The background worker stopped right after starting. "
                f"See {self.root / workerctl.DAEMON_LOG}", "red"))
            from scaffold.agent import paths
            if not paths.provider_configured():
                self.term.line("  " + paths.no_provider_message())
        return state

    def check_worker(self, jobs: Optional[list[JobSpec]] = None, force: bool = False) -> Optional[str]:
        """
        Periodic liveness check while the session is open: when jobs are queued
        (or marked running) and no worker holds the lock, start one again through
        workerctl.ensure_worker, whose spawn lock keeps it to a single instance
        even when several app windows check at once. Returns the ensure_worker
        state when a restart was attempted, else None.
        """
        if not self.auto_worker:
            return None
        now = time.monotonic()
        if not force and now - self._last_worker_check < self.worker_check_sec:
            return None
        if not self._worker_lock.acquire(blocking=False):
            return None   # a check is already in progress on another thread
        try:
            self._last_worker_check = now
            js = jobs if jobs is not None else self.repo_jobs()
            if not any(j.state in (QUEUED, RUNNING) for j in js):
                return None
            if workerctl.worker_running(self.root):
                return None
            age = workerctl.heartbeat_age(self.root)
            self.log.warning("background worker not running (last heartbeat %s ago) with jobs "
                             "waiting; restarting it",
                             "never" if age is None else f"{age:.0f}s")
            state = self.ensure_worker()
            if state in ("started", "spawned"):
                self.term.line(self.term.s("  The background worker had stopped; started it again.",
                                           "yellow"))
            self.log.info("worker restart: %s", state)
            return state
        finally:
            self._worker_lock.release()

    def repo_jobs(self) -> list[JobSpec]:
        return J.repo_jobs(self.queue, self.repo)

    def resolve(self, ref: str, finished_only: bool = False) -> Optional[JobSpec]:
        ref = ref.strip()
        if ref:
            job = J.find_job(self.queue, self.repo, ref)
            if job is None:
                self.term.line(f"  No job matching '{ref}' in this repo. Try /status.")
            return job
        if self.current:
            return self.queue.get(self.current)
        done = [j for j in self.repo_jobs() if j.state in J.FINISHED]
        if done:
            return done[-1]
        self.term.line("  No finished job yet. Type a task first, or /status.")
        return None

    # ── header / status ──────────────────────────────────────────────────
    def header(self, worker_state: str) -> None:
        t = self.term
        branch = gitops.current_branch(self.repo) or "detached"
        test_cmd = (self.ready.get("test_env") or self.ready.get("test_command")
                    or detect_test_command(self.repo) or "none found")
        model = (self.ready.get("model") or os.getenv("AWOS_AGENT_MODEL")
                 or os.getenv("AWOS_PROVIDER") or "default")
        js = self.repo_jobs()
        running = sum(j.state == RUNNING for j in js)
        queued = sum(j.state == QUEUED for j in js)
        home = str(Path.home())
        where = str(self.repo).replace(home, "~", 1) if str(self.repo).startswith(home) else str(self.repo)
        worker = {"running": "worker on", "started": "worker started", "spawned": "worker starting",
                  "off": "worker off", "failed": "worker failed", "error": "worker ?"}.get(worker_state, worker_state)
        t.line(t.s("AWOS", "bold", "cyan") + t.s("  ·  ", "grey") + t.s(f"{where} ({branch})", "bold")
               + t.s("  ·  ", "grey") + f"tests: {test_cmd}" + t.s("  ·  ", "grey") + f"model: {model}")
        t.line(t.s(f"queue: {running} running, {queued} queued  ·  {worker}", "grey"))

    def show_status(self, recent: int = 5) -> None:
        t = self.term
        js = self.repo_jobs()
        active = [j for j in js if j.state in J.ACTIVE]
        finished = [j for j in js if j.state in J.FINISHED]
        if not js:
            t.line(t.s("  No jobs for this repo yet.", "grey"))
            return
        for j in active:
            tail = self.tails.get(j.id)
            last = tail.last_update if tail and tail.last_update else ""
            label = "running" if j.state == RUNNING else j.state
            t.line(f"  {t.s('…', 'yellow')} {t.s(J.short(j.id), 'bold')}  {label:<8} {J.goal_line(j, 50)}"
                   + (t.s(f"  — {last}", "grey") if last else ""))
        for j in finished[-recent:]:
            self.print_card(j, compact=True)

    def print_card(self, job: JobSpec, compact: bool = False) -> None:
        t = self.term
        o = J.outcome(self.queue, job)
        review = J.review_state(self.queue, job).get("action")
        if job.state == DONE and o.handoff_status == "no_changes":
            mark, word, style = "✓", "done (no changes needed)", "green"
        elif job.state == DONE:
            mark, word, style = "✓", "done", "green"
        elif job.state == BLOCKED:
            mark, word, style = "!", "blocked", "yellow"
        elif job.state == CANCELLED:
            mark, word, style = "-", "cancelled", "grey"
        else:
            mark, word, style = "✗", "failed", "red"
        tag = ""
        if review:
            tag = t.s(f"  [{review}]", "grey")
        elif job.state == DONE and o.branch:
            tag = t.s("  [needs review]", "yellow")
        t.line(f"  {t.s(mark + ' ' + word, style, 'bold')}  {t.s(J.short(job.id), 'bold')}  "
               f"{J.goal_line(job, 56)}{tag}")
        tests = t.s(o.tests, "green" if o.tests_ok else ("red" if o.tests_ok is False else "grey"))
        bits = [f"tests {tests}", f"${o.cost:.4f}", J.fmt_seconds(o.seconds)]
        if o.branch and job.state != DONE:
            # Everything the failed job did, kept whole for salvage; not verified.
            bits.append(f"branch {o.branch} (all of its work, unverified)")
        elif o.branch:
            bits.append(f"branch {o.branch}")
        t.line("      " + t.s(" · ", "grey").join(bits))
        if job.state in (FAILED, BLOCKED) and o.error:
            t.line("      " + t.s(o.error.strip().splitlines()[0][:140], "red" if job.state == FAILED else "yellow"))
            if job.state == BLOCKED:
                action = (job.result or {}).get("action")
                if action:
                    t.line("      " + t.s(f"to fix: {action}", "yellow"))
        if not compact:
            opts = []
            if o.branch and not review:
                opts += ["[d]iff", "[m]erge", "[r]eject"]
            opts += ["[f]ollow-up", "[o]pen report"]
            t.line("      " + t.s("  ".join(opts), "cyan"))

    # ── background monitor ───────────────────────────────────────────────
    def _snapshot(self) -> None:
        """Remember current states; attach to running jobs without replaying old lines."""
        for j in self.repo_jobs():
            self.states[j.id] = j.state
            if j.state == RUNNING:
                tail = self.tails.setdefault(j.id, JobTail(self.queue.job_dir(j.id)))
                tail.poll()   # catch up silently; last_update keeps the latest line

    def start_monitor(self) -> None:
        self._snapshot()
        self._monitor = threading.Thread(target=self._run_monitor, name="awos-app-monitor", daemon=True)
        self._monitor.start()

    def stop_monitor(self) -> None:
        self._stop.set()
        if self._monitor:
            self._monitor.join(timeout=2)

    def _run_monitor(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:   # keep monitoring; tell the log
                self.log.error("monitor tick failed: %s", "".join(traceback.format_exception(exc)))
            self._stop.wait(self.poll)

    def tick(self) -> None:
        """One monitor pass: progress lines for running jobs, a card for finished ones."""
        t = self.term
        changed = False
        jobs = self.repo_jobs()
        self.check_worker(jobs)
        for j in jobs:
            prev = self.states.get(j.id)
            just_ended = prev in (RUNNING, QUEUED) and j.state in J.FINISHED
            if j.state == RUNNING or just_ended:
                tail = self.tails.setdefault(j.id, JobTail(self.queue.job_dir(j.id)))
                if prev != RUNNING and j.state == RUNNING:
                    t.line(f"  {t.s('▶', 'cyan')} {t.s(J.short(j.id), 'bold')} started: {J.goal_line(j, 60)}")
                for msg in tail.poll():
                    if msg == "started":
                        continue
                    t.line(f"    {t.s(t.spin(), 'cyan')} {t.s(J.short(j.id), 'grey')}  {msg}")
            if prev != j.state:
                self.states[j.id] = j.state
                if j.state in J.FINISHED and prev is not None:
                    self.current = j.id
                    self.print_card(j, compact=self.compact_cards)
                    ev = self._events.get(j.id)
                    if ev:
                        ev.set()
                changed = True
        if changed:
            t.set_prompt(self._prompt())   # refresh the "[n working]" count

    def wait_for(self, job_id: str) -> JobSpec:
        """Watch mode: animate a status line until the job finishes (Ctrl-C stops watching)."""
        ev = self._events.setdefault(job_id, threading.Event())
        t = self.term
        try:
            while not ev.is_set():
                job = self.queue.get(job_id)
                if job is None:
                    break
                if job.state in J.FINISHED and self.states.get(job_id) == job.state:
                    break
                tail = self.tails.get(job_id)
                what = (tail.last_update if tail and tail.last_update else
                        ("waiting in the queue" if job.state == QUEUED else "starting"))
                t.status(f"  {t.s(t.spin(), 'cyan')} {J.short(job_id)}  {what}  "
                         + t.s(f"({J.fmt_seconds(J.elapsed(job))}, Ctrl-C to stop watching)", "grey"))
                ev.wait(0.12 if t.tty else self.poll)
        finally:
            t.clear_status()
        return self.queue.get(job_id)

    def watch_all(self) -> None:
        running = [j for j in self.repo_jobs() if j.state in J.ACTIVE]
        if not running:
            self.term.line("  Nothing is running.")
            return
        try:
            for j in running:
                self.wait_for(j.id)
        except KeyboardInterrupt:
            self.term.line(self.term.s("  Stopped watching; the jobs keep running.", "grey"))

    # ── actions ──────────────────────────────────────────────────────────
    def submit(self, goal: str) -> JobSpec:
        job = J.submit(self.queue, self.repo, goal)
        self.states[job.id] = job.state
        ahead = sum(j.state in (RUNNING, QUEUED) for j in self.repo_jobs()) - 1
        where = "starting now" if ahead == 0 else f"{ahead} ahead of it"
        self.term.line(f"  {self.term.s('+', 'green')} queued {self.term.s(J.short(job.id), 'bold')} ({where})")
        self.ensure_worker()
        return job

    def _branch_for(self, job: JobSpec) -> Optional[str]:
        o = J.outcome(self.queue, job)
        if o.branch and gitops.branch_exists(self.repo, o.branch):
            return o.branch
        return None

    def do_diff(self, job: JobSpec) -> None:
        branch = self._branch_for(job)
        if not branch:
            self.term.line(f"  {J.short(job.id)} has no review branch (nothing to diff).")
            return
        text = gitops.diff_text(self.repo, branch, color=self.term.color)
        if not text.strip():
            self.term.line(f"  {branch} has no changes compared to your current branch (already merged?).")
            return
        self.term.page(text)

    def do_merge(self, job: JobSpec) -> bool:
        branch = self._branch_for(job)
        if not branch:
            self.term.line(f"  {J.short(job.id)} has no review branch to merge.")
            return False
        if job.state != DONE:
            if not self.term.confirm(f"  {J.short(job.id)} is {job.state}, not done. Merge anyway?"):
                return False
        res = gitops.merge_branch(self.repo, branch, job.goal)
        if res.ok:
            self.term.line(f"  {self.term.s('✓', 'green')} {res.message}")
            J.mark_reviewed(self.queue, job, "merged", branch=branch,
                            into=gitops.current_branch(self.repo))
        else:
            self.term.line(f"  {self.term.s('Not merged:', 'yellow')} {res.message}")
        return res.ok

    def do_reject(self, job: JobSpec) -> bool:
        branch = self._branch_for(job)
        if not branch:
            self.term.line(f"  {J.short(job.id)} has no review branch to delete.")
            return False
        if not self.term.confirm(f"  Delete branch {branch}? Its changes are discarded."):
            self.term.line("  Kept.")
            return False
        ok, msg = gitops.delete_branch(self.repo, branch)
        self.term.line(f"  {msg}")
        if ok:
            J.mark_reviewed(self.queue, job, "rejected", branch=branch)
        return ok

    def do_follow(self, job: JobSpec, text: str) -> Optional[JobSpec]:
        if not text.strip():
            try:
                text = self.term.ask(f"  follow-up to {J.short(job.id)} › ")
            except (EOFError, KeyboardInterrupt):
                self.term.line("")
                return None
        if not text.strip():
            return None
        branch = self._branch_for(job)
        new = J.submit_follow_up(self.queue, self.repo, job, text, branch)
        self.states[new.id] = new.state
        base = f"starts from {branch}" if branch else "starts from your current HEAD"
        self.term.line(f"  {self.term.s('+', 'green')} queued follow-up "
                       f"{self.term.s(J.short(new.id), 'bold')} ({base})")
        self.ensure_worker()
        return new

    def do_open(self, job: JobSpec) -> None:
        o = J.outcome(self.queue, job)
        if not o.report or not Path(o.report).exists():
            log = self.queue.job_dir(job.id) / "worker.log"
            self.term.line(f"  No report yet for {J.short(job.id)}. Raw log: {log}")
            return
        self.term.page(Path(o.report).read_text(encoding="utf-8", errors="replace"))
        self.term.line(self.term.s(f"  ({o.report})", "grey"))

    def do_cancel(self, ref: str) -> None:
        job = self.resolve(ref) if ref.strip() else None
        if job is None:
            if not ref.strip():
                self.term.line("  Usage: /cancel <id>")
            return
        outcome = self.queue.cancel(job.id)
        msg = {"cancelled": "cancelled", "requested": "stopping (the worker stops it at its next step)",
               "final": f"already {job.state}", "missing": "not found"}[outcome]
        self.term.line(f"  {J.short(job.id)}: {msg}")

    def do_keys(self) -> None:
        try:
            from scaffold.agent.key_health import check_all
            for c in check_all():
                d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
                status = d.get("status", "?")
                style = "green" if status == "ok" else ("grey" if status in ("missing", "skipped") else "yellow")
                self.term.line(f"  {d.get('provider', '?'):<12} {self.term.s(status, style)}  "
                               f"{self.term.s(str(d.get('detail') or d.get('message') or ''), 'grey')}")
        except Exception as exc:
            self.fail("key check", exc)

    # ── command dispatch ─────────────────────────────────────────────────
    _REVIEW = {"d": "diff", "m": "merge", "r": "reject", "f": "follow", "o": "open"}

    def handle(self, line: str) -> bool:
        """Run one input line. Returns False when the session should end."""
        text = line.strip()
        if not text:
            return True
        m = re.fullmatch(r"([dmrfo])(?:\s+([0-9a-f]{4,32}))?", text)
        if m:
            return self._review(self._REVIEW[m.group(1)], m.group(2) or "", "")
        if text.startswith("/"):
            cmd, _, rest = text[1:].partition(" ")
            cmd, rest = cmd.lower(), rest.strip()
            if cmd in ("quit", "exit", "q"):
                return False
            if cmd in ("help", "h", "?"):
                self.term.line(HELP)
            elif cmd == "status":
                self.show_status(recent=10)
            elif cmd in ("diff", "merge", "reject", "open"):
                return self._review(cmd, rest, "")
            elif cmd in ("follow", "followup", "follow-up"):
                ref, _, task = rest.partition(" ")
                if ref and not re.fullmatch(r"[0-9a-f]{4,32}", ref):
                    ref, task = "", rest
                return self._review("follow", ref, task)
            elif cmd == "cancel":
                self.do_cancel(rest)
            elif cmd == "keys":
                self.do_keys()
            elif cmd == "watch":
                self.watch_all()
            else:
                self.term.line(f"  Unknown command /{cmd}. Type /help.")
            return True
        self.submit(text)
        return True

    def _review(self, action: str, ref: str, task: str) -> bool:
        job = self.resolve(ref)
        if job is None:
            return True
        if action == "diff":
            self.do_diff(job)
        elif action == "merge":
            self.do_merge(job)
        elif action == "reject":
            self.do_reject(job)
        elif action == "follow":
            self.do_follow(job, task)
        elif action == "open":
            self.do_open(job)
        return True

    # ── entry points ─────────────────────────────────────────────────────
    def _prompt(self) -> str:
        js = self.repo_jobs()
        n = sum(j.state in (RUNNING, QUEUED) for j in js)
        busy = self.term.s(f"[{n} working] ", "yellow") if n else ""
        return busy + self.term.s("awos", "cyan", "bold") + self.term.s(" › ", "grey")

    def interactive(self) -> int:
        t = self.term
        self.onboarding()
        worker_state = self.ensure_worker()
        self.header(worker_state)
        self.show_status()
        t.line(t.s("Type a task in plain English. /help for commands; Ctrl-C leaves "
                   "(background jobs keep running).", "grey"))
        self.start_monitor()
        try:
            while True:
                try:
                    line = t.ask(self._prompt())
                except EOFError:
                    break
                try:
                    if not self.handle(line):
                        break
                except KeyboardInterrupt:
                    t.line(t.s("  (interrupted)", "grey"))
                except Exception as exc:
                    self.fail("that command", exc)
        except KeyboardInterrupt:
            t.line("")
        finally:
            self.stop_monitor()
            self.goodbye()
        return 0

    def goodbye(self) -> None:
        js = self.repo_jobs()
        n = sum(j.state in (RUNNING, QUEUED) for j in js)
        if n:
            self.term.line(self.term.s(f"Leaving. {n} job(s) keep running in the background; "
                                       "run `awos` here to check on them.", "grey"))
        else:
            self.term.line(self.term.s("Bye.", "grey"))

    def oneshot(self, goal: str) -> int:
        t = self.term
        self.onboarding()
        self.compact_cards = True
        self.start_monitor()
        try:
            job = self.submit(goal)
            try:
                job = self.wait_for(job.id)
            except KeyboardInterrupt:
                t.line(t.s(f"  Stopped watching. {J.short(job.id)} keeps running in the background; "
                           "run `awos` here to review it.", "grey"))
                return 0
            return self.review_loop(job)
        finally:
            self.stop_monitor()

    def review_loop(self, job: JobSpec) -> int:
        """Single-key review after a one-shot job."""
        t = self.term
        while True:
            branch = self._branch_for(job)
            keys = ("dmr" if branch else "") + "foq"
            opts = (["[d]iff", "[m]erge", "[r]eject"] if branch else []) + ["[f]ollow-up", "[o]pen report", "[q]uit"]
            try:
                ch = t.key("  " + t.s("  ".join(opts), "cyan") + " › ", keys)
            except (EOFError, KeyboardInterrupt):
                ch = "q"
            if ch in ("q", ""):
                if branch:
                    t.line(t.s(f"  Left {branch} for later: `awos` here, then d/m/r.", "grey"))
                return 0 if job.state == DONE else 1
            try:
                if ch == "d":
                    self.do_diff(job)
                elif ch == "o":
                    self.do_open(job)
                elif ch == "m":
                    if self.do_merge(job):
                        return 0
                elif ch == "r":
                    if self.do_reject(job):
                        return 0
                elif ch == "f":
                    new = self.do_follow(job, "")
                    if new is not None:
                        try:
                            job = self.wait_for(new.id)
                        except KeyboardInterrupt:
                            t.line(t.s("  Stopped watching; it keeps running.", "grey"))
                            return 0
            except Exception as exc:
                self.fail("that action", exc)


def _repo_or_none(start: Path) -> Optional[Path]:
    return gitops.repo_root(start)


def run_session(start: Optional[Path] = None) -> int:
    """`awos` with no arguments. Returns an exit code."""
    repo = _repo_or_none(Path(start or Path.cwd()))
    if repo is None:
        return 2
    app = App(repo)
    try:
        with app.term.quiet():
            return app.interactive()
    except Exception as exc:   # last line of defence: no traceback on screen
        app.fail("awos", exc)
        return 1


def run_oneshot(goal: str, start: Optional[Path] = None) -> int:
    repo = _repo_or_none(Path(start or Path.cwd()))
    if repo is None:
        print("awos: run this inside a git repository (the result comes back as a branch).")
        return 2
    app = App(repo)
    try:
        with app.term.quiet():
            return app.oneshot(goal)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        app.fail("awos", exc)
        return 1
