"""
queue.py — the host's durable job queue: one SQLite file, atomic transitions.

Every state change is a single BEGIN IMMEDIATE transaction guarded by
`WHERE state IN (...)`, and reports whether it happened. So a job is claimed
by at most one worker, finished at most once, and a crash between any two
statements leaves the previous consistent state.

Spec: docs/specs/host_queue.md.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .models import (CANCELLED, FAILED, FINAL_STATES, PAUSED, QUEUED, RUNNING,
                     JobSpec, host_root, job_dir, now_iso)

_COLUMNS = (
    "id", "goal", "repo_path", "kind", "budget_usd", "privacy", "created_at",
    "state", "attempts", "result", "started_at", "finished_at", "error",
    "cancel_requested", "worker_pid", "worker_started", "child_pid", "child_started",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    budget_usd REAL NOT NULL,
    privacy TEXT NOT NULL,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    worker_pid INTEGER,
    worker_started TEXT,
    child_pid INTEGER,
    child_started TEXT,
    seq INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, seq);
"""


def max_attempts() -> int:
    try:
        return max(1, int(os.getenv("AWOS_HOST_MAX_ATTEMPTS", "3")))
    except ValueError:
        return 3


def _row_to_job(row: sqlite3.Row) -> JobSpec:
    data = {k: row[k] for k in _COLUMNS}
    data["result"] = json.loads(data["result"]) if data["result"] else None
    data["cancel_requested"] = bool(data["cancel_requested"])
    return JobSpec.from_dict(data)


class JobQueue:
    """The jobs table in <root>/queue.sqlite."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser() if root else host_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "queue.sqlite"
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── plumbing ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction; the database lock is taken up front."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def job_dir(self, job_id: str) -> Path:
        return job_dir(job_id, self.root)

    def _transition(self, job_id: str, from_states: Iterable[str], **fields) -> bool:
        states = tuple(from_states)
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"], default=str)
        sets = ", ".join(f"{k} = ?" for k in fields)
        marks = ", ".join("?" for _ in states)
        with self._tx() as conn:
            cur = conn.execute(
                f"UPDATE jobs SET {sets} WHERE id = ? AND state IN ({marks})",
                (*fields.values(), job_id, *states))
            return cur.rowcount == 1

    # ── reads ─────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[JobSpec]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None and len(job_id) >= 4:  # accept an unambiguous prefix
                rows = conn.execute("SELECT * FROM jobs WHERE id LIKE ?", (job_id + "%",)).fetchall()
                row = rows[0] if len(rows) == 1 else None
        finally:
            conn.close()
        return _row_to_job(row) if row else None

    def list(self, states: Optional[Iterable[str]] = None) -> list[JobSpec]:
        conn = self._connect()
        try:
            if states:
                st = tuple(states)
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE state IN ({', '.join('?' for _ in st)}) ORDER BY seq",
                    st).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY seq").fetchall()
        finally:
            conn.close()
        return [_row_to_job(r) for r in rows]

    # ── transitions ───────────────────────────────────────────────────────

    def submit(self, goal: str, repo_path: str | Path, kind: str = "coding",
               budget_usd: float = 1.0, privacy: str = "cloud_ok") -> JobSpec:
        job = JobSpec.new(goal, repo_path, kind=kind, budget_usd=budget_usd, privacy=privacy)
        return self.add(job)

    def add(self, job: JobSpec) -> JobSpec:
        data = job.to_dict()
        data["result"] = json.dumps(data["result"]) if data["result"] is not None else None
        data["cancel_requested"] = int(bool(data["cancel_requested"]))
        cols = ", ".join(_COLUMNS)
        marks = ", ".join("?" for _ in _COLUMNS)
        with self._tx() as conn:
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM jobs").fetchone()[0]
            conn.execute(f"INSERT INTO jobs ({cols}, seq) VALUES ({marks}, ?)",
                         (*[data[c] for c in _COLUMNS], seq))
        self.job_dir(job.id).mkdir(parents=True, exist_ok=True)
        return job

    def claim_next(self, worker_pid: Optional[int] = None,
                   worker_started: Optional[str] = None) -> Optional[JobSpec]:
        """Oldest queued job -> running (attempts + 1), atomically; None if empty."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE state = ? ORDER BY seq LIMIT 1", (QUEUED,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET state = ?, attempts = attempts + 1, started_at = ?, "
                "finished_at = NULL, error = NULL, worker_pid = ?, worker_started = ?, "
                "child_pid = NULL, child_started = NULL WHERE id = ? AND state = ?",
                (RUNNING, now_iso(), worker_pid, worker_started, row["id"], QUEUED))
            job_id = row["id"]
        return self.get(job_id)

    def set_child(self, job_id: str, pid: Optional[int], started: Optional[str]) -> bool:
        return self._transition(job_id, (RUNNING,), child_pid=pid, child_started=started)

    def finish(self, job_id: str, state: str, result: Optional[dict] = None,
               error: Optional[str] = None) -> bool:
        """running -> done | failed | cancelled."""
        if state not in FINAL_STATES:
            raise ValueError(f"not a final state: {state}")
        return self._transition(job_id, (RUNNING,), state=state, result=result, error=error,
                                finished_at=now_iso(), child_pid=None, child_started=None)

    def requeue(self, job_id: str, refund_attempt: bool = False,
                error: Optional[str] = None) -> bool:
        """running/paused -> queued. A clean stop refunds the attempt it used."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state = ?, started_at = NULL, worker_pid = NULL, "
                "worker_started = NULL, child_pid = NULL, child_started = NULL, error = ?, "
                f"attempts = MAX(0, attempts - {1 if refund_attempt else 0}) "
                "WHERE id = ? AND state IN (?, ?)",
                (QUEUED, error, job_id, RUNNING, PAUSED))
            return cur.rowcount == 1

    def cancel(self, job_id: str) -> str:
        """
        Cancel a job. Returns what happened: "cancelled" (it was queued or
        paused), "requested" (running; the worker stops it at its next step),
        "final" (already finished) or "missing".
        """
        job = self.get(job_id)
        if job is None:
            return "missing"
        if self._transition(job.id, (QUEUED, PAUSED), state=CANCELLED, finished_at=now_iso(),
                            error="cancelled before it ran"):
            return "cancelled"
        if self._transition(job.id, (RUNNING,), cancel_requested=1):
            return "requested"
        return "final"

    def cancel_requested(self, job_id: str) -> bool:
        job = self.get(job_id)
        return bool(job and job.cancel_requested)

    def recover(self, kill_child=None) -> list[JobSpec]:
        """
        Called by a worker that holds the worker lock: every job still marked
        running was interrupted. Its child is killed (via kill_child, which
        must only act on a pid whose start time still matches), then the job
        is re-queued, or failed once it has used AWOS_HOST_MAX_ATTEMPTS.
        """
        touched = []
        for job in self.list((RUNNING,)):
            if kill_child and job.child_pid:
                kill_child(job.child_pid, job.child_started)
            if job.cancel_requested:
                self._transition(job.id, (RUNNING,), state=CANCELLED, finished_at=now_iso(),
                                 error="cancelled (worker was interrupted)",
                                 child_pid=None, child_started=None)
            elif job.attempts >= max_attempts():
                self._transition(job.id, (RUNNING,), state=FAILED, finished_at=now_iso(),
                                 error=f"interrupted {job.attempts} times "
                                       f"(AWOS_HOST_MAX_ATTEMPTS={max_attempts()})",
                                 child_pid=None, child_started=None)
            else:
                self.requeue(job.id, error=f"interrupted during attempt {job.attempts}")
            touched.append(self.get(job.id))
        return touched
