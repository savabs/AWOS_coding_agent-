"""
journal.py — append-only step journal for host jobs, with checkpoint/resume.

A host job can run for hours. When its worker dies (SIGKILL, OOM, reboot) the
restarted worker reads this journal and:

  * skips steps already done (LLM replies are served from the journal, so a
    resumed run never pays for the same model call twice — cassette reuse);
  * re-verifies side-effect steps against the real world through a verifier
    callback, instead of blindly re-running them;
  * resumes at the first step that is not done.

Storage: <job_dir>/journal.jsonl, one JSON record per line, fsynced per write.
A crash mid-write leaves a truncated last line; it is repaired on open, and any
unparseable line is skipped rather than raised on.

Spec: docs/specs/host_journal.md. Live proof:
    python -m scaffold.agent.host.journal demo
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:  # fcntl is POSIX-only; the journal still works single-writer without it.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

JOURNAL_VERSION = 1
JOURNAL_NAME = "journal.jsonl"

LLM_CALL = "llm_call"
TOOL_CALL = "tool_call"
CHECKPOINT = "checkpoint"
SIDE_EFFECT_KINDS = frozenset({TOOL_CALL})

STARTED = "started"
DONE = "done"
FAILED = "failed"

Verifier = Callable[[dict], bool]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def derive_key(kind: str, payload: Any) -> str:
    """Idempotency key for a step that did not name one."""
    return f"{kind}:{hashlib.sha256(_canon(payload).encode()).hexdigest()[:16]}"


def workspace_fingerprint(path: str | os.PathLike) -> str:
    """sha256 over every file (relative path, size, content hash), skipping .git,
    plus git HEAD when the workspace is a repository."""
    root = Path(path)
    h = hashlib.sha256()
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in sorted(filenames):
                fp = Path(dirpath) / name
                try:
                    data = fp.read_bytes()
                except OSError:
                    continue
                rel = fp.relative_to(root).as_posix()
                h.update(f"{rel}\0{len(data)}\0".encode())
                h.update(hashlib.sha256(data).digest())
        if (root / ".git").exists():
            try:
                head = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                h.update(f"HEAD\0{head}".encode())
            except (OSError, subprocess.SubprocessError):
                pass
    return h.hexdigest()[:24]


@dataclass
class ReplayPlan:
    """What a resumed worker should do with the steps already journaled."""

    done: list[str] = field(default_factory=list)       # finished, skip
    to_verify: list[str] = field(default_factory=list)  # side effects to re-check
    verified: list[str] = field(default_factory=list)   # re-checked: effect present
    rerun: list[str] = field(default_factory=list)      # re-checked: effect absent
    incomplete: list[str] = field(default_factory=list) # started/failed, non side-effect
    resume_after: Optional[str] = None                  # last skippable key
    last_checkpoint: Optional[dict] = None
    workspace_drift: Optional[bool] = None              # None = not checked

    @property
    def skip(self) -> list[str]:
        return self.done + self.verified

    def as_dict(self) -> dict:
        return {
            "done": self.done, "to_verify": self.to_verify,
            "verified": self.verified, "rerun": self.rerun,
            "incomplete": self.incomplete, "resume_after": self.resume_after,
            "last_checkpoint": self.last_checkpoint,
            "workspace_drift": self.workspace_drift,
        }


@dataclass
class StepRun:
    """Outcome of Journal.run: executed | skipped | verified."""

    outcome: str
    result: Any
    record: dict


class Journal:
    """Append-only, fsynced, idempotent step log for one job directory."""

    def __init__(self, job_dir: str | os.PathLike, fsync: bool = True) -> None:
        self.job_dir = Path(job_dir)
        self.path = self.job_dir / JOURNAL_NAME
        self.fsync = fsync
        self.corrupt_lines = 0
        self.repaired_bytes = 0
        self._records: list[dict] = []
        self._latest: dict[str, dict] = {}
        self._order: list[str] = []
        self._seen: set[tuple] = set()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self._repair_tail()
        self._load()

    # -- storage -----------------------------------------------------------

    def _repair_tail(self) -> None:
        """Cut a partial last line (crash mid-write) back to the last newline."""
        if not self.path.exists():
            return
        data = self.path.read_bytes()
        if not data or data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1  # 0 when there is no newline at all
        tail = data[cut:]
        try:
            json.loads(tail.decode("utf-8"))
            with open(self.path, "ab") as f:  # complete record, newline lost
                f.write(b"\n")
                self._sync(f)
        except (ValueError, UnicodeDecodeError):
            os.truncate(self.path, cut)
            self.repaired_bytes = len(tail)

    def _load(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "rb") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self.corrupt_lines += 1
                    continue
                if not isinstance(rec, dict) or "key" not in rec or "kind" not in rec:
                    self.corrupt_lines += 1
                    continue
                self._index(rec)

    def _index(self, rec: dict) -> None:
        self._records.append(rec)
        key = rec["key"]
        if key not in self._latest:
            self._order.append(key)
        self._latest[key] = rec
        self._seen.add(self._ident(rec))

    @staticmethod
    def _ident(rec: dict) -> tuple:
        return (rec["key"], rec.get("status", DONE), rec.get("attempt", 1))

    def _sync(self, f) -> None:
        f.flush()
        if self.fsync:
            os.fsync(f.fileno())

    def _write(self, rec: dict) -> None:
        created = not self.path.exists()
        line = (json.dumps(rec, default=str, separators=(",", ":")) + "\n").encode()
        with open(self.path, "ab") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line)  # one write: a crash leaves at most a partial tail
                self._sync(f)
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if created and self.fsync:
            try:
                dfd = os.open(self.job_dir, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass

    # -- public API --------------------------------------------------------

    def append(self, step: dict) -> dict:
        """Append a step. Re-appending the same (key, status, attempt) is a no-op
        that returns the record already on disk."""
        if "kind" not in step:
            raise ValueError("journal step needs a 'kind'")
        rec = dict(step)
        rec.setdefault("key", derive_key(rec["kind"], rec.get("payload")))
        rec.setdefault("status", DONE)
        prev = self._latest.get(rec["key"])
        rec.setdefault("attempt", prev.get("attempt", 1) if prev else 1)
        ident = self._ident(rec)
        if ident in self._seen:
            for existing in reversed(self._records):
                if self._ident(existing) == ident:
                    return existing
        rec["v"] = JOURNAL_VERSION
        rec["seq"] = len(self._records) + 1
        rec.setdefault("ts", _now())
        self._write(rec)
        self._index(rec)
        return rec

    def records(self) -> list[dict]:
        return list(self._records)

    def steps(self) -> list[dict]:
        """Latest record per key, in the order keys were first journaled."""
        return [self._latest[k] for k in self._order]

    def get(self, key: str) -> Optional[dict]:
        return self._latest.get(key)

    def last_checkpoint(self) -> Optional[dict]:
        for rec in reversed(self._records):
            if rec["kind"] == CHECKPOINT and rec.get("status") == DONE:
                return rec
        return None

    def checkpoint(self, key: str, workspace: str | os.PathLike, **extra: Any) -> dict:
        payload = {"workspace": str(workspace),
                   "fingerprint": workspace_fingerprint(workspace), **extra}
        return self.append({"key": key, "kind": CHECKPOINT, "status": DONE,
                            "payload": payload})

    def replay_plan(
        self,
        verifier: Optional[Verifier] = None,
        workspace: Optional[str | os.PathLike] = None,
    ) -> ReplayPlan:
        """Classify journaled steps for a resumed run.

        Without a verifier, side-effect steps land in `to_verify`. With one,
        each is checked: present → `verified` (skip), absent → `rerun`. An
        in-doubt step (started, never finished) the verifier confirms is
        journaled as done so it is not checked again.
        """
        plan = ReplayPlan(last_checkpoint=self.last_checkpoint())
        blocked = False  # once a step must rerun, nothing later is "resume_after"
        for rec in self.steps():
            key, kind, status = rec["key"], rec["kind"], rec.get("status", DONE)
            if kind in SIDE_EFFECT_KINDS and status in (DONE, STARTED):
                plan.to_verify.append(key)
                if verifier is None:
                    blocked = blocked or status != DONE
                    if not blocked:
                        plan.resume_after = key
                    continue
                ok = _safe_verify(verifier, rec)
                if ok:
                    plan.verified.append(key)
                    if status == STARTED:
                        self.append({"key": key, "kind": kind, "status": DONE,
                                     "attempt": rec.get("attempt", 1),
                                     "payload": rec.get("payload"),
                                     "result": {"verified_in_doubt": True}})
                    if not blocked:
                        plan.resume_after = key
                else:
                    plan.rerun.append(key)
                    blocked = True
            elif status == DONE:
                plan.done.append(key)
                if not blocked:
                    plan.resume_after = key
            else:
                plan.incomplete.append(key)
                blocked = True
        if workspace is not None and plan.last_checkpoint is not None:
            want = (plan.last_checkpoint.get("payload") or {}).get("fingerprint")
            plan.workspace_drift = want != workspace_fingerprint(workspace)
        return plan

    def run(
        self,
        key: str,
        kind: str,
        fn: Callable[[], Any],
        verify: Optional[Verifier] = None,
        payload: Any = None,
    ) -> StepRun:
        """Execute a step at most once across crashes.

        done → cached result (side effects re-verified when `verify` given);
        started but in doubt → verify, else rerun; otherwise run and journal.
        """
        prev = self._latest.get(key)
        attempt = 1
        if prev is not None:
            status = prev.get("status", DONE)
            side_effect = kind in SIDE_EFFECT_KINDS
            if status == DONE:
                if not (side_effect and verify is not None):
                    return StepRun("skipped", prev.get("result"), prev)
                if _safe_verify(verify, prev):
                    return StepRun("verified", prev.get("result"), prev)
            elif status == STARTED and side_effect and verify is not None:
                if _safe_verify(verify, prev):
                    rec = self.append({"key": key, "kind": kind, "status": DONE,
                                       "attempt": prev.get("attempt", 1),
                                       "payload": prev.get("payload", payload),
                                       "result": {"verified_in_doubt": True}})
                    return StepRun("verified", rec.get("result"), rec)
            attempt = prev.get("attempt", 1) + 1
        self.append({"key": key, "kind": kind, "status": STARTED,
                     "attempt": attempt, "payload": payload})
        try:
            result = fn()
        except Exception as exc:
            self.append({"key": key, "kind": kind, "status": FAILED,
                         "attempt": attempt, "payload": payload,
                         "result": {"error": f"{type(exc).__name__}: {exc}"}})
            raise
        rec = self.append({"key": key, "kind": kind, "status": DONE,
                           "attempt": attempt, "payload": payload,
                           "result": result})
        return StepRun("executed", result, rec)


def _safe_verify(verifier: Verifier, rec: dict) -> bool:
    try:
        return bool(verifier(rec))
    except Exception:
        return False  # a verifier that cannot decide means "redo"


# -- LLM replay (reuses cassette.py fingerprints and reply codec) ------------


def _cassette():
    try:
        from .. import cassette
    except ImportError:  # pragma: no cover - flat import layout
        import cassette  # type: ignore[no-redef]
    return cassette


class JournalingClient:
    """Wraps an agent-loop ModelClient. Each reply is journaled as an llm_call
    keyed by the cassette conversation fingerprint; a resumed run gets the
    journaled reply back with no model call and no cost."""

    def __init__(self, inner: Any, journal: Journal, root: Optional[str] = None) -> None:
        self.inner = inner
        self.journal = journal
        self.root = root
        self.cache_hits = 0
        self.live_calls = 0
        self.last_was_cached = False
        self._occurrence: dict[str, int] = {}

    @property
    def model(self) -> Optional[str]:
        return getattr(self.inner, "model", None)

    def complete(self, system, messages, registry):
        c = _cassette()
        fp = c._fingerprint(system, messages, self.root)
        n = self._occurrence.get(fp, 0) + 1  # identical prompts asked twice
        self._occurrence[fp] = n
        key = f"llm:{fp}:{n}"
        prev = self.journal.get(key)
        if prev is not None and prev.get("status") == DONE and prev.get("result"):
            self.cache_hits += 1
            self.last_was_cached = True
            return c._reply_from_dict(prev["result"])
        reply = self.inner.complete(system, messages, registry)
        self.live_calls += 1
        self.last_was_cached = False
        self.journal.append({"key": key, "kind": LLM_CALL, "status": DONE,
                             "payload": {"fingerprint": fp, "model": self.model},
                             "result": c._reply_to_dict(reply)})
        return reply

    def format_assistant_turn(self, reply):
        return self.inner.format_assistant_turn(reply)

    def format_tool_results(self, calls, results):
        return self.inner.format_tool_results(calls, results)


# -- live proof --------------------------------------------------------------

DEMO_STEPS = [
    ("1-plan", LLM_CALL),
    ("2-write-a", TOOL_CALL),
    ("3-checkpoint", CHECKPOINT),
    ("4-write-b", TOOL_CALL),
    ("5-review", LLM_CALL),
    ("6-write-c", TOOL_CALL),
]


class _FakeModel:
    """Deterministic stand-in for a paid model; logs every real call."""

    model = "fake-demo"

    def __init__(self, log: Path) -> None:
        self.log = log

    def complete(self, system, messages, registry):
        c = _cassette()
        with open(self.log, "a") as f:
            f.write(messages[-1]["content"] + "\n")
        return c.ModelReply(text=f"ok: {messages[-1]['content']}",
                            input_tokens=10, output_tokens=5)


def _demo_child(job_dir: str, ws: str, crash_after: int) -> int:
    journal = Journal(job_dir)
    wsp = Path(ws)
    effects = wsp.parent / "effects.log"
    client = JournalingClient(_FakeModel(wsp.parent / "model_calls.log"), journal)

    def write(name: str):
        def fn():
            (wsp / name).write_text(f"content {name}\n")
            with open(effects, "a") as f:
                f.write(f"{name}\n")
            return {"path": name}
        return fn

    def exists(name: str) -> Verifier:
        return lambda rec: (wsp / name).read_text() == f"content {name}\n" \
            if (wsp / name).exists() else False

    plan = journal.replay_plan(verifier=lambda r: exists(r["payload"]["path"])(r),
                               workspace=ws)
    print(f"[journal] plan skip={plan.skip} rerun={plan.rerun} "
          f"resume_after={plan.resume_after} drift={plan.workspace_drift} "
          f"repaired_bytes={journal.repaired_bytes}", flush=True)

    for i, (key, kind) in enumerate(DEMO_STEPS, start=1):
        if kind == LLM_CALL:
            client.complete("demo system", [{"role": "user", "content": key}], None)
            outcome = "skip(cached reply)" if client.last_was_cached else "executed"
        elif kind == TOOL_CALL:
            name = key.split("-")[-1] + ".txt"
            r = journal.run(key, kind, write(name), verify=exists(name),
                            payload={"path": name})
            outcome = r.outcome
        else:
            if journal.get(key):
                outcome = "skipped"
            else:
                journal.checkpoint(key, ws)
                outcome = "executed"
        print(f"[journal] step {i} {key:<13} {outcome}", flush=True)
        if crash_after and i == crash_after:
            print(f"[journal] SIGKILL self after step {i}", flush=True)
            os.kill(os.getpid(), signal.SIGKILL)
    return 0


def demo(base: Optional[str] = None) -> int:
    root = Path(base or tempfile.mkdtemp(prefix="awos_journal_demo_"))
    job_dir, ws = root / "jobs" / "demo", root / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[3]
    cmd = [sys.executable, "-m", "scaffold.agent.host.journal", "_child",
           str(job_dir), str(ws)]
    print(f"[journal] demo root {root}")
    print("[journal] --- run 1 (crash after step 4) ---", flush=True)
    r1 = subprocess.run(cmd + ["4"], cwd=repo_root)
    print(f"[journal] run 1 exit code {r1.returncode}")
    # Simulate a torn write as well: a partial record at the tail.
    with open(job_dir / JOURNAL_NAME, "ab") as f:
        f.write(b'{"v":1,"seq":99,"key":"torn')
    print("[journal] --- run 2 (resume) ---", flush=True)
    r2 = subprocess.run(cmd + ["0"], cwd=repo_root)
    print(f"[journal] run 2 exit code {r2.returncode}")
    effects = (root / "effects.log").read_text().split()
    calls = (root / "model_calls.log").read_text().split()
    print(f"[journal] side effects: {effects}")
    print(f"[journal] model calls:  {calls}")
    ok = (r1.returncode == -signal.SIGKILL and r2.returncode == 0
          and sorted(effects) == ["a.txt", "b.txt", "c.txt"]
          and calls == ["1-plan", "5-review"])
    print(f"[journal] EXACTLY-ONCE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "_child":
        sys.exit(_demo_child(sys.argv[2], sys.argv[3], int(sys.argv[4])))
    if len(sys.argv) >= 2 and sys.argv[1] == "demo":
        sys.exit(demo(sys.argv[2] if len(sys.argv) > 2 else None))
    print("usage: python -m scaffold.agent.host.journal demo [dir]")
    sys.exit(2)
