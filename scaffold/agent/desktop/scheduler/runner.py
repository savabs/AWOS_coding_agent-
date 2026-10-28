"""runner.py: run scheduled jobs safely on the owner's Mac.

One run = one job x one schedule slot, identified by ``run_id`` =
``<job>@<slot UTC, YYYYmmddTHHMMZ>`` (manual runs: ``<job>@manual-<UTC>``).

Per run, in order:

  1. idempotency   a run_id with a final result.json is never run again
                   (DUPLICATE, nothing touched); a run_id with a checkpoint but no
                   result resumes from the checkpoint.
  2. admission     quiet hours, AC power, user idle time, unlocked screen (UI jobs:
                   synthetic input does not reach a locked session), and for model
                   jobs: no live task lease held by another AWOS task, and the
                   llama-server memory plan (local_runtime.plan). A refusal is a
                   DEFERRED record; the slot stays due, so the next run-due retries.
  3. model lock    a non-blocking flock on <home>/model.lock: never two model jobs
                   at once (DEFERRED "model_busy" for the second).
  4. model server  started through scripts/local_model.sh when down, held with a
                   local_runtime task lease while the job runs (the idle watchdog
                   never unloads it mid-job), stopped afterwards if this run
                   started it.
  5. execute       email_triage in-process (FakeMailbox / ImapGmail adapter,
                   triage_cascade); skill / micro_task as a subprocess with a
                   timeout, its last JSON stdout line parsed for the outcome.
                   Checkpoints after every chunk; SIGTERM (launchd at shutdown)
                   and max_runtime stop at the next chunk boundary -> INTERRUPTED,
                   resumed by the next run-due. max_runtime uses the monotonic
                   clock, which does not advance while the Mac sleeps, so a
                   closed lid does not eat the budget.
  6. record        result.json in the run dir + one line in <home>/runs.jsonl.
                   Everything written (records, logs, triage output, approvals)
                   goes through the WP7 SecretBroker redaction first.

Writes follow the job's approvals policy: ``queue`` (default) never writes and
queues each proposed action for the morning digest; ``auto_bulk`` archives
only mail the deterministic layers (L1 learned/user rules, L2 list rules) put
in a bulk class, never protected, suspicious or review-queue mail, and queues
the rest; ``deny`` neither writes nor queues. Each write is logged as an intent
before it is made and confirmed after, so a kill between the two is reconciled
by reading the message back, never by repeating the write.

No network except 127.0.0.1 (llama-server) and, for an ``imap:`` mailbox, the
mail server the owner configured.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .. import secrets as secrets_mod
from ... import local_runtime
from .jobs import Config, Job, due_slot, in_window

REPO = Path(__file__).resolve().parents[4]
BULK = ("newsletter", "promo", "social")


class Outcome(str, Enum):
    VERIFIED = "VERIFIED"                        # done, every write read back as intended
    COMPLETED_UNVERIFIED = "COMPLETED_UNVERIFIED"  # done, nothing could be checked
    HALTED = "HALTED"                            # stopped on an unexpected state (skill halt)
    NEEDS_USER = "NEEDS_USER"                    # waiting on the owner (sign-in, approval, MFA)
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"  # a write may not have landed as intended
    INTERRUPTED = "INTERRUPTED"                  # killed / SIGTERM / max_runtime: resumes next time
    DEFERRED = "DEFERRED"                        # admission refused; not started
    FAILED = "FAILED"                            # crashed; see the redacted log
    DUPLICATE = "DUPLICATE"                      # this run_id already finished

    @property
    def final(self) -> bool:
        return self not in (Outcome.INTERRUPTED, Outcome.DEFERRED, Outcome.DUPLICATE)


def _mono() -> float:
    """Monotonic seconds; on macOS this clock stops while the Mac sleeps."""
    return time.monotonic()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_id_for(job: Job, slot: Optional[datetime], manual_at: Optional[datetime] = None) -> str:
    if slot is not None:
        return f"{job.name}@{slot.astimezone(timezone.utc):%Y%m%dT%H%MZ}"
    return f"{job.name}@manual-{(manual_at or utcnow()).astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"


def _safe(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@-]", "_", run_id)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def read_jsonl(path: Path) -> List[dict]:
    out = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        fcntl.flock(fh, fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: Path, blocking: bool = True) -> Iterator[bool]:
    """flock on ``path``; yields False (without waiting) when ``blocking`` is
    off and someone else holds it. Released when the process dies, so a crash
    or kill -9 never leaves a stale lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}\n")
            fh.flush()
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


# ── system probes (injectable for tests) ────────────────────────────────────
class Probe:
    """Reads the machine state. Every method fails toward "do not run"."""

    def __init__(self, env: Optional[dict] = None, run=subprocess.run):
        self.env = dict(os.environ if env is None else env)
        self.run = run

    def power(self) -> local_runtime.PowerState:
        return local_runtime.power_state(self.run)

    def idle_seconds(self) -> Optional[float]:
        """Seconds since the last keyboard/mouse input (IOHIDSystem HIDIdleTime)."""
        try:
            out = self.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True,
                           text=True, timeout=5).stdout or ""
        except Exception:
            return None
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        return int(m.group(1)) / 1e9 if m else None

    def screen_locked(self) -> Optional[bool]:
        try:
            out = self.run(["ioreg", "-n", "Root", "-d1"], capture_output=True, text=True,
                           timeout=5).stdout or ""
        except Exception:
            return None
        if not out:
            return None
        return bool(re.search(r'"CGSSessionScreenIsLocked"\s*=\s*Yes', out))

    def leases(self) -> List[dict]:
        return local_runtime.live_leases(self.env)

    def memory(self, power: local_runtime.PowerState) -> Tuple[bool, str]:
        try:
            p = local_runtime.plan(self.env, power)
        except local_runtime.PlanError as e:
            return False, str(e)
        return True, p.admission.message

    def server_up(self) -> bool:
        return local_runtime.server_state(local_runtime.server_base(self.env))["state"] in (
            "up", "sleeping", "loading")

    def model_script(self, action: str) -> bool:
        r = self.run(["bash", str(REPO / "scripts" / "local_model.sh"), action], env=self.env,
                     capture_output=True, text=True, timeout=300)
        return r.returncode == 0

    def lease(self, run_id: str):
        return local_runtime.task_lease(run_id, self.env)


# ── run context ─────────────────────────────────────────────────────────────
class Stop(Exception):
    """Raised inside an executor at a chunk boundary when the run must pause."""


@dataclass
class Hooks:
    """Test hooks. ``after_chunk(n)`` may raise to simulate a kill mid-run."""
    after_chunk: Optional[Callable[[int], None]] = None
    after_action: Optional[Callable[[str], None]] = None


@dataclass
class ExecResult:
    outcome: Outcome
    reason: str = ""
    counts: Dict[str, Any] = field(default_factory=dict)
    halt: Optional[dict] = None


class RunContext:
    def __init__(self, sched: "Scheduler", job: Job, run_id: str, run_dir: Path,
                 deadline: float, stop_flag: threading.Event):
        self.sched, self.job, self.run_id, self.run_dir = sched, job, run_id, run_dir
        self.deadline, self.stop_flag = deadline, stop_flag
        self.cp_path = run_dir / "checkpoint.json"
        self.checkpoint: dict = read_json(self.cp_path, None) or {}
        self.log_path = run_dir / "log.txt"
        # an earlier attempt of this run id started (its log exists), whether or
        # not it got as far as a checkpoint
        self.resumed = bool(self.checkpoint) or self.log_path.exists()
        self.hooks = sched.hooks

    @property
    def broker(self):
        return self.sched.broker

    def log(self, msg: str) -> None:
        line = f"{iso(utcnow())} {self.sched.broker.redact(msg)}"
        with open(self.log_path, "a") as fh:
            fh.write(line + "\n")

    def save_checkpoint(self) -> None:
        write_json_atomic(self.cp_path, self.sched.broker.redact_obj(self.checkpoint))

    def check_stop(self) -> None:
        if self.stop_flag.is_set():
            raise Stop("stopped by signal (SIGTERM/SIGINT)")
        if _mono() > self.deadline:
            raise Stop(f"max_runtime {self.job.constraints.max_runtime_s}s reached")


# ── scheduler ───────────────────────────────────────────────────────────────
class Scheduler:
    def __init__(self, cfg: Config, probe: Optional[Probe] = None,
                 broker: Optional[secrets_mod.SecretBroker] = None,
                 hooks: Optional[Hooks] = None,
                 adapter_factory: Optional[Callable[[Job], Any]] = None,
                 clock: Callable[[], datetime] = utcnow):
        self.cfg = cfg
        self.home = Path(cfg.home)
        self.probe = probe or Probe()
        self.broker = broker or secrets_mod.SecretBroker(env=dict(os.environ))
        self.hooks = hooks or Hooks()
        self.adapter_factory = adapter_factory or default_adapter
        self.clock = clock

    # paths
    @property
    def state_path(self) -> Path:
        return self.home / "state.json"

    @property
    def runs_path(self) -> Path:
        return self.home / "runs.jsonl"

    @property
    def approvals_path(self) -> Path:
        return self.home / "approvals.jsonl"

    def run_dir(self, run_id: str) -> Path:
        return self.home / "runs" / _safe(run_id)

    # state
    def state(self) -> dict:
        return read_json(self.state_path, None) or {"jobs": {}, "digest": {}}

    def update_state(self, fn: Callable[[dict], None]) -> dict:
        with file_lock(self.home / "state.lock"):
            st = self.state()
            st.setdefault("jobs", {})
            st.setdefault("digest", {})
            fn(st)
            write_json_atomic(self.state_path, st)
            return st

    def _job_state(self, st: dict, name: str) -> dict:
        return st["jobs"].setdefault(name, {})

    # due
    def due(self, now: Optional[datetime] = None) -> List[Tuple[Job, datetime, int]]:
        now = now or self.clock()
        out = []
        st = self.state()
        for job in self.cfg.jobs:
            js = st.get("jobs", {}).get(job.name, {})
            first = parse_iso(js.get("first_seen")) or now
            d = due_slot(job, now, self.cfg.tz, parse_iso(js.get("last_slot")), first,
                         self.cfg.catchup_s)
            if d:
                out.append((job, d[0], d[1]))
        return out

    def note_jobs(self, now: Optional[datetime] = None) -> None:
        """Record first_seen for new jobs (their past slots never fire)."""
        now = now or self.clock()

        def fn(st):
            for job in self.cfg.jobs:
                self._job_state(st, job.name).setdefault("first_seen", iso(now))
        self.update_state(fn)

    # admission
    def admit(self, job: Job, now: datetime) -> Tuple[bool, str]:
        c = job.constraints
        local = now.astimezone(self.cfg.tz)
        if in_window(c.quiet_hours, local):
            return False, "quiet_hours"
        power = self.probe.power()
        if c.ac_power and power.source != "ac":
            return False, f"on_battery ({power.source}" + (
                f", {power.percent}%)" if power.percent is not None else ")")
        if c.idle_s:
            idle = self.probe.idle_seconds()
            if idle is None or idle < c.idle_s:
                return False, f"not_idle ({'unknown' if idle is None else int(idle)}s < {c.idle_s}s)"
        if c.unlocked:
            locked = self.probe.screen_locked()
            if locked is None or locked:
                return False, "screen_locked" if locked else "screen_state_unknown"
        if job.uses_model:
            # checked before this run takes its own lease, so any live lease is
            # another task (an AWOS coding run, a manual job) using the model
            others = self.probe.leases()
            if others:
                return False, f"model_busy (lease held by {others[0].get('task') or 'another task'})"
            if not self.probe.server_up():
                ok, msg = self.probe.memory(power)
                if not ok:
                    return False, f"memory ({msg})"
        return True, "ok"

    # run
    def run_due(self, now: Optional[datetime] = None) -> List[dict]:
        """Resume interrupted runs first, then start each due job once."""
        now = now or self.clock()
        with file_lock(self.home / "run-due.lock", blocking=False) as got:
            if not got:
                return [{"outcome": Outcome.DEFERRED.value, "reason": "another run-due is active"}]
            self.note_jobs(now)
            recs = []
            st = self.state()
            names = {j.name for j in self.cfg.jobs}
            for name, js in sorted(st.get("jobs", {}).items()):
                if js.get("pending") and name in names:
                    job = self.cfg.job(name)
                    recs.append(self.run_job(job, slot=parse_iso(js.get("pending_slot")),
                                             run_id=js["pending"], now=now))
            busy = {r.get("job") for r in recs}
            for job, slot, missed in self.due(now):
                if job.name in busy:
                    continue
                recs.append(self.run_job(job, slot=slot, now=now, missed=missed))
            return recs

    def run_job(self, job: Job, slot: Optional[datetime] = None, run_id: Optional[str] = None,
                now: Optional[datetime] = None, missed: int = 0, force: bool = False) -> dict:
        now = now or self.clock()
        run_id = run_id or run_id_for(job, slot, now)
        rdir = self.run_dir(run_id)
        done = read_json(rdir / "result.json", None)
        if done:
            return {**done, "outcome": Outcome.DUPLICATE.value,
                    "reason": f"run {run_id} already finished as {done.get('outcome')}"}
        base = {"run_id": run_id, "job": job.name, "kind": job.kind, "slot": iso(slot),
                "missed_slots": missed, "uses_model": job.uses_model}
        if not force:
            ok, why = self.admit(job, now)
            if not ok:
                return self._record({**base, "outcome": Outcome.DEFERRED.value, "reason": why,
                                     "started": iso(now), "ended": iso(now)}, rdir, final=False)
        with contextlib.ExitStack() as stack:
            if job.uses_model:
                got = stack.enter_context(file_lock(self.home / "model.lock", blocking=False))
                if not got:
                    return self._record({**base, "outcome": Outcome.DEFERRED.value,
                                         "reason": "model_busy (another model job is running)",
                                         "started": iso(now), "ended": iso(now)}, rdir, final=False)
            rdir.mkdir(parents=True, exist_ok=True)

            def mark(st):
                js = self._job_state(st, job.name)
                js["pending"], js["pending_slot"] = run_id, iso(slot)
            self.update_state(mark)
            started_server = False
            stop_flag = threading.Event()
            ctx = RunContext(self, job, run_id, rdir,
                             _mono() + job.constraints.max_runtime_s, stop_flag)
            ctx.log(f"start {run_id} kind={job.kind} resumed={ctx.resumed}")
            stack.enter_context(_signals(stop_flag))
            try:
                if job.uses_model:
                    if not self.probe.server_up():
                        ctx.log("model server down: starting it (scripts/local_model.sh start)")
                        if not self.probe.model_script("start"):
                            raise RuntimeError("scripts/local_model.sh start failed")
                        started_server = True
                    stack.enter_context(self.probe.lease(run_id))
                res = EXECUTORS[job.kind](job, ctx)
            except Stop as e:
                res = ExecResult(Outcome.INTERRUPTED, str(e))
            except Exception as e:  # crash: keep the checkpoint, record a redacted reason
                ctx.log(f"crash: {type(e).__name__}: {e}")
                res = ExecResult(Outcome.FAILED, f"{type(e).__name__}: {e}")
            finally:
                if started_server and job.stop_model_after:
                    ok = self.probe.model_script("stop")
                    ctx.log(f"model server stop: {'ok' if ok else 'failed'}")
            ctx.log(f"end {res.outcome.value} {res.reason}")
            rec = {**base, "outcome": res.outcome.value, "reason": res.reason,
                   "counts": res.counts, "resumed": ctx.resumed, "started": iso(now),
                   "ended": iso(self.clock()), "log": str(ctx.log_path),
                   "model_started_by_run": started_server}
            if res.halt:
                rec["halt"] = res.halt
            return self._record(rec, rdir, final=res.outcome.final, slot=slot)

    def _record(self, rec: dict, rdir: Path, final: bool, slot: Optional[datetime] = None) -> dict:
        rec = self.broker.redact_obj(rec)
        append_jsonl(self.runs_path, rec)
        name = rec["job"]
        outcome = rec["outcome"]

        def fn(st):
            js = self._job_state(st, name)
            js["last_outcome"], js["last_run_id"], js["last_attempt"] = outcome, rec["run_id"], rec["ended"]
            if outcome == Outcome.DEFERRED.value:
                js["last_deferred_reason"] = rec.get("reason")
                return
            if final:
                js.pop("pending", None)
                js.pop("pending_slot", None)
                if slot is not None:
                    js["last_slot"] = iso(slot)
        if final:
            write_json_atomic(rdir / "result.json", rec)
        self.update_state(fn)
        return rec

    # approvals
    def queue_approval(self, item: dict) -> bool:
        """Append a pending approval unless one with this id exists (idempotent)."""
        with file_lock(self.home / "approvals.lock"):
            if any(a.get("id") == item["id"] for a in read_jsonl(self.approvals_path)):
                return False
            append_jsonl(self.approvals_path, self.broker.redact_obj(
                {**item, "status": "pending", "created": iso(self.clock())}))
            return True

    def pending_approvals(self) -> List[dict]:
        latest: Dict[str, dict] = {}
        for a in read_jsonl(self.approvals_path):
            latest[a.get("id")] = a
        return [a for a in latest.values() if a.get("status") == "pending"]

    def status(self) -> dict:
        st = self.state()
        now = self.clock()
        due = {j.name: iso(s) for j, s, _ in self.due(now)}
        return {"home": str(self.home), "now": iso(now),
                "jobs": [{**j.summary(), "state": st.get("jobs", {}).get(j.name, {}),
                          "due_now": due.get(j.name)} for j in self.cfg.jobs],
                "pending_approvals": len(self.pending_approvals()),
                "digest": st.get("digest", {}).get("last_at"),
                "recent_runs": read_jsonl(self.runs_path)[-10:]}


@contextlib.contextmanager
def _signals(flag: threading.Event):
    """SIGTERM/SIGINT -> stop at the next chunk boundary (main thread only)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    def h(signum, frame):
        flag.set()
    for s in old:
        signal.signal(s, h)
    try:
        yield
    finally:
        for s, o in old.items():
            signal.signal(s, o)


# ── email_triage executor ───────────────────────────────────────────────────
def default_adapter(job: Job):
    from ..mail_adapter import FakeMailbox
    src, where = job.mailbox_source
    rw = job.writes == "auto_bulk"
    if src == "fake":
        return FakeMailbox(where, read_only=not rw)
    from ..mail_imap import ImapGmail
    return ImapGmail(where, read_only=not rw)


def scripted_keyword_batch(batch: List[dict]):
    """The model-free L3 stand-in (testbeds/email_triage baselines.keyword_predict);
    mail with no keyword signal is labelled NONE, so it abstains to the review queue."""
    from testbeds.email_triage.baselines import FYI_WORDS, keyword_predict
    from ..triage_cascade import NONE_LABEL
    out = {}
    for m in batch:
        p = keyword_predict(m)
        noreply = any(x in m.get("from_addr", "") for x in ("noreply", "no-reply", "notifications",
                                                            "notify"))
        fallback = (p["importance"] == 1 and p["category"] == "fyi" and not noreply
                    and not any(w in m.get("subject", "").lower() for w in FYI_WORDS))
        if fallback:
            p = {**p, "category": NONE_LABEL}
        out[m["id"]] = {k: p.get(k) for k in ("importance", "category", "needs_reply", "suspicious")}
    return out, {}


def local_model_batch(profile: dict, prefs: dict):
    from .. import email_triage_agent as eta
    from ..triage_cascade import Thresholds
    ctx = eta._triage_context_xml(profile, prefs)
    th = Thresholds.from_env()
    return lambda b: eta.model_batch(b, ctx, th.allow_none, prefs=prefs, xml=True)


def why_of(t: dict) -> Tuple[str, str]:
    """(source, sentence) for one cascade trace item: guard | rule | model."""
    guards = t.get("guards") or []
    layer = t.get("layer")
    if t.get("lookalike"):
        return "guard", f"look-alike sender ({t['lookalike']})"
    if t.get("l0_phish"):
        return "guard", f"phishing guard ({t['l0_phish']})"
    if layer == "L1":
        return "rule", "your rule(s): " + ", ".join(str(r) for r in t.get("rules") or [])
    if layer == "L2":
        return "rule", "list mail with no protective signal (static rule)"
    bits = []
    if t.get("abstain"):
        bits.append(f"model unsure ({t.get('abstain_reason')})")
    else:
        bits.append("model label")
    if t.get("urgency_cap"):
        bits.append(f"urgency check: {t['urgency_cap']}")
    if t.get("phish_guard"):
        bits.append(f"phishing sanity check: {t['phish_guard']}")
    if t.get("rules"):
        bits.append("adjusted by rule(s) " + ", ".join(str(r) for r in t["rules"]))
    src = "model"
    if "injection" in guards:
        bits.insert(0, "text addressed to an AI assistant (prompt injection)")
        src = "guard"
    return src, "; ".join(bits)


def _readback_archived(adapter, mid: str) -> Optional[bool]:
    try:
        return "INBOX" not in (adapter.get_message(mid).get("labels") or [])
    except Exception:
        return None


def run_email_triage(job: Job, ctx: RunContext) -> ExecResult:
    from testbeds.email_triage import prefs as P
    from ..mail_imap import MailAuthError, MailCredentialError
    from ..triage_cascade import Thresholds, build_context, run_cascade
    try:
        adapter = ctx.sched.adapter_factory(job)
        msgs = adapter.list_messages(limit=job.limit)
    except (MailCredentialError, MailAuthError) as e:
        return ExecResult(Outcome.NEEDS_USER, f"mailbox sign-in: {e}")
    src, where = job.mailbox_source
    profile = dict(getattr(adapter, "account", None) or {})
    if not profile.get("email") and src == "imap":
        profile["email"] = where
    prefs_path = Path(job.prefs) if job.prefs else None
    prefs = P.load(prefs_path) if prefs_path and prefs_path.exists() else P.empty_prefs()
    tctx = build_context(profile, prefs, msgs, Path(where) if src == "fake" else None)
    model = local_model_batch(profile, prefs) if job.agent == "local_model" else scripted_keyword_batch
    calls = [0]

    def mb(batch):
        calls[0] += 1
        return model(batch)

    cp = ctx.checkpoint
    cp.setdefault("run_id", ctx.run_id)
    items: Dict[str, dict] = cp.setdefault("items", {})
    ledger_path = ctx.run_dir / "actions.jsonl"
    th = Thresholds.from_env()
    todo = [m for m in msgs if m["id"] not in items]
    if ctx.resumed:
        ctx.log(f"resume: {len(items)} item(s) already done, {len(todo)} to go")
    layers = cp.setdefault("layers", {"L0": 0, "L1": 0, "L2": 0, "L3": 0})
    n_chunk = cp.get("chunks", 0)
    for i in range(0, len(todo), job.chunk):
        ctx.check_stop()
        chunk = todo[i:i + job.chunk]
        res, trace = run_cascade(chunk, prefs, tctx, mb, batch_size=5, th=th)
        for it in res:
            m = next(x for x in chunk if x["id"] == it["id"])
            t = trace["items"][it["id"]]
            layers[t["layer"]] = layers.get(t["layer"], 0) + 1
            src_why, why = why_of(t)
            rec = {"id": it["id"], "thread_id": m.get("thread_id"), "date": m.get("date"),
                   "from_name": m.get("from_name"), "from_addr": m.get("from_addr"),
                   "subject": m.get("subject"), "importance": it["importance"],
                   "category": it["category"], "needs_reply": bool(it.get("needs_reply")),
                   "suspicious": bool(it.get("suspicious")), "layer": t["layer"],
                   "why_source": src_why, "why": why, "guards": t.get("guards") or [],
                   "review": bool(t.get("abstain")),
                   "review_reason": t.get("abstain_reason") if t.get("abstain") else None,
                   "disposition": t.get("disposition"), "action": None}
            rec["action"] = _act(job, ctx, adapter, rec, ledger_path)
            items[it["id"]] = rec
        n_chunk += 1
        cp["chunks"] = n_chunk
        cp["model_calls"] = cp.get("model_calls", 0) + calls[0]
        calls[0] = 0
        ctx.save_checkpoint()
        ctx.log(f"chunk {n_chunk}: {len(chunk)} message(s), {len(items)} done")
        if ctx.hooks.after_chunk:
            ctx.hooks.after_chunk(n_chunk)
    # verify every write by reading it back
    applied = _ledger_done(ledger_path)
    bad = [mid for mid in applied if _readback_archived(adapter, mid) is False]
    unknown = [mid for mid in applied if _readback_archived(adapter, mid) is None]
    ordered = [items[m["id"]] for m in msgs if m["id"] in items]
    counts = {"messages": len(ordered), "layers": layers,
              "model_calls": cp.get("model_calls", 0),
              "urgent": sum(1 for r in ordered if r["importance"] == 3),
              "needs_reply": sum(1 for r in ordered if r["needs_reply"]),
              "review": sum(1 for r in ordered if r["review"]),
              "archived": len(applied),
              "queued_for_approval": sum(1 for r in ordered if (r["action"] or {}).get("status") == "queued"),
              "flagged": sum(1 for r in ordered if r["suspicious"] or r["category"] == "spam_phishing")}
    write_json_atomic(ctx.run_dir / "triage.json", ctx.broker.redact_obj({
        "version": 1, "run_id": ctx.run_id, "job": job.name, "account": profile.get("email"),
        "agent": job.agent, "writes": job.writes, "items": ordered, "counts": counts}))
    if bad:
        return ExecResult(Outcome.RECONCILIATION_REQUIRED,
                          f"{len(bad)} archive(s) did not read back as archived", counts)
    if unknown:
        return ExecResult(Outcome.COMPLETED_UNVERIFIED, f"{len(unknown)} write(s) could not be read back",
                          counts)
    return ExecResult(Outcome.VERIFIED, f"{len(ordered)} message(s) triaged", counts)


def _ledger(ledger_path: Path) -> Dict[str, set]:
    st: Dict[str, set] = {"intent": set(), "done": set()}
    for r in read_jsonl(ledger_path):
        st.setdefault(r.get("phase", ""), set()).add(r.get("id"))
    return st


def _ledger_done(ledger_path: Path) -> List[str]:
    seen, out = set(), []
    for r in read_jsonl(ledger_path):
        if r.get("phase") == "done" and r.get("id") not in seen:
            seen.add(r["id"])
            out.append(r["id"])
    return out


def _act(job: Job, ctx: RunContext, adapter, rec: dict, ledger_path: Path) -> Optional[dict]:
    """Decide and (policy allowing) perform the archive for one triaged message."""
    if rec["importance"] != 0 or rec["review"] or rec["suspicious"] \
            or rec["category"] == "spam_phishing":
        return None
    if any(g in rec["guards"] for g in ("correspondent", "same_domain", "family", "protected")):
        return None
    undo = {"op": "move", "id": rec["id"], "to": "INBOX"}
    if job.writes == "deny":
        return {"op": "archive", "status": "denied_by_policy"}
    auto = (job.writes == "auto_bulk" and rec["layer"] in ("L1", "L2") and rec["category"] in BULK)
    if not auto:
        ctx.sched.queue_approval({
            "id": f"{ctx.run_id}:archive:{rec['id']}", "run_id": ctx.run_id, "job": job.name,
            "op": "archive", "target": rec["id"],
            "summary": f"archive '{rec.get('subject')}' from {rec.get('from_addr')} ({rec['why']})"})
        return {"op": "archive", "status": "queued"}
    led = _ledger(ledger_path)
    if rec["id"] in led["done"]:
        return {"op": "archive", "status": "done", "undo": undo, "resumed": True}
    if rec["id"] in led["intent"]:
        # a previous attempt was interrupted between the write and its confirmation:
        # reconcile by reading the message back, never by writing again blindly
        if _readback_archived(adapter, rec["id"]):
            append_jsonl(ledger_path, {"phase": "done", "id": rec["id"], "op": "archive",
                                       "undo": undo, "reconciled": True, "at": iso(utcnow())})
            return {"op": "archive", "status": "done", "undo": undo, "reconciled": True}
    append_jsonl(ledger_path, {"phase": "intent", "id": rec["id"], "op": "archive",
                               "at": iso(utcnow())})
    adapter.move(rec["id"], "Archive")
    if ctx.hooks.after_action:
        ctx.hooks.after_action(rec["id"])
    append_jsonl(ledger_path, {"phase": "done", "id": rec["id"], "op": "archive", "undo": undo,
                               "at": iso(utcnow())})
    return {"op": "archive", "status": "done", "undo": undo}


# ── skill / micro_task executor ─────────────────────────────────────────────
_OUTCOME_MAP = {
    "verified": Outcome.VERIFIED, "completed_unverified": Outcome.COMPLETED_UNVERIFIED,
    "halted": Outcome.HALTED, "halted_before_effect": Outcome.HALTED,
    "needs_user": Outcome.NEEDS_USER, "denied": Outcome.NEEDS_USER,
    "reconciliation_required": Outcome.RECONCILIATION_REQUIRED,
}


def parse_command_result(stdout: str) -> Optional[dict]:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and ("outcome" in d or "status" in d):
                return d
    return None


def run_command(job: Job, ctx: RunContext) -> ExecResult:
    argv = list(job.command)
    if argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update({"AWOS_RUN_ID": ctx.run_id, "AWOS_RUN_DIR": str(ctx.run_dir),
                "AWOS_APPROVALS": job.writes, "AWOS_RESUME": "1" if ctx.resumed else "0",
                "NO_PROXY": "127.0.0.1,localhost"})
    ctx.checkpoint["attempts"] = ctx.checkpoint.get("attempts", 0) + 1
    ctx.save_checkpoint()
    remaining = max(5.0, ctx.deadline - _mono())
    try:
        r = subprocess.run(argv, cwd=str(REPO), env=env, capture_output=True, text=True,
                           timeout=remaining)
    except subprocess.TimeoutExpired as e:
        ctx.log("timeout; output tail: " + str((e.stdout or "")[-2000:]))
        return ExecResult(Outcome.INTERRUPTED, f"max_runtime {job.constraints.max_runtime_s}s reached")
    ctx.log(f"exit {r.returncode}\nstdout tail:\n{r.stdout[-4000:]}\nstderr tail:\n{r.stderr[-2000:]}")
    d = parse_command_result(r.stdout)
    if d is None:
        if r.returncode == 0:
            return ExecResult(Outcome.COMPLETED_UNVERIFIED, "exit 0, no result line")
        return ExecResult(Outcome.FAILED, f"exit {r.returncode}, no result line")
    raw = str(d.get("outcome") or d.get("status") or "").strip().lower()
    out = _OUTCOME_MAP.get(raw, Outcome.FAILED if r.returncode else Outcome.COMPLETED_UNVERIFIED)
    halt = d.get("halt") if isinstance(d.get("halt"), dict) else None
    reason = str(d.get("reason") or (halt or {}).get("reason") or raw)
    if out in (Outcome.HALTED, Outcome.NEEDS_USER):
        halt = {**(halt or {}), "reason": reason, "kind": job.kind}
        if d.get("approval"):
            ctx.sched.queue_approval({"id": f"{ctx.run_id}:approval", "run_id": ctx.run_id,
                                      "job": job.name, "op": job.kind,
                                      "target": str(d.get("approval")), "summary": reason})
    return ExecResult(out, reason, {"returncode": r.returncode}, ctx.broker.redact_obj(halt))


EXECUTORS: Dict[str, Callable[[Job, RunContext], ExecResult]] = {
    "email_triage": run_email_triage, "skill": run_command, "micro_task": run_command,
}
