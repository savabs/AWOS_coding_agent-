"""jobs.py: job definitions (``~/.awos/jobs.toml``) and due-time calculation.

File format (TOML; every key except name/kind/schedule is optional)::

    [scheduler]
    home = "~/.awos/scheduler"     # state, run records, checkpoints, digests
    tz = "Australia/Hobart"        # IANA zone for schedules (default: the Mac's zone)
    catchup_hours = 24             # a missed slot older than this is skipped, not run

    [digest]
    at = "07:30"                   # run-due also writes the morning digest at this time
    notify = false                 # macOS notification (osascript) when a digest is written
    quiet_hours = "22:00-07:00"    # no notification inside this window

    [[job]]
    name = "inbox-overnight"
    kind = "email_triage"          # email_triage | skill | micro_task
    schedule = "daily 02:00"       # daily HH:MM | weekdays HH:MM | mon,wed,fri HH:MM | every N[m|h]
    mailbox = "fake:/path/to/mailbox"   # or "imap:you@gmail.com" (read-only unless writes=auto_bulk)
    agent = "keyword_prefs"        # email_triage: keyword_prefs (scripted, no model) | local_model
    prefs = "~/.awos/prefs.json"   # the WP2 preference store
    limit = 200                    # newest N messages per run
    [job.constraints]
    ac_power = true                # only on AC power
    idle = 300                     # only after N s of no keyboard/mouse input (true = 300)
    max_runtime = "20m"            # wall clock while awake; checkpoint + resume past it
    quiet_hours = "09:00-17:00"    # never start inside this window
    unlocked = false               # need an unlocked screen (default true for skill/micro_task)
    [job.approvals]
    writes = "queue"               # queue (default): every write waits for the morning digest
                                   # auto_bulk: archive L1/L2 bulk mail, queue the rest
                                   # deny: never write, do not queue

    [[job]]
    name = "weekly-report"
    kind = "skill"
    schedule = "mon 06:00"
    command = ["python", "-m", "testbeds.teach_once.skill_runner", "--run-dir", "/tmp/x"]
    uses_model = false

Due calculation (``latest_slot`` / ``due``): slots are wall-clock times in the
configured zone, so a "daily 02:00" job stays at 02:00 across DST changes. A
wall time that does not exist (spring forward) runs at the first instant after
the gap; an ambiguous one (fall back) runs once, at its first occurrence.
"every N" slots are fixed UTC intervals from midnight UTC, unaffected by DST.
Missed runs (the Mac was asleep or off) are caught up ONCE: only the most
recent missed slot runs, and the record counts what was skipped.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

KINDS = ("email_triage", "skill", "micro_task")
WRITE_POLICIES = ("queue", "auto_bulk", "deny")
EMAIL_AGENTS = ("keyword_prefs", "local_model")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_JOBS_FILE = "~/.awos/jobs.toml"
DEFAULT_HOME = "~/.awos/scheduler"
DEFAULT_IDLE_S = 300
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class JobError(ValueError):
    """A jobs.toml problem, with a message the owner can act on."""


# ── schedule ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Schedule:
    kind: str                         # "calendar" | "every"
    at: Optional[dtime] = None        # calendar: wall-clock time
    days: Tuple[int, ...] = ()        # calendar: weekday numbers 0=Mon..6=Sun
    every_s: int = 0                  # every: interval seconds
    text: str = ""

    def __str__(self) -> str:
        return self.text


def parse_hhmm(s: str) -> dtime:
    m = _HHMM.match(str(s).strip())
    if not m:
        raise JobError(f"{s!r} is not a HH:MM time")
    return dtime(int(m.group(1)), int(m.group(2)))


def parse_duration(v: Any) -> int:
    """'90s' / '20m' / '2h' / 1200 -> seconds."""
    if isinstance(v, bool):
        raise JobError(f"{v!r} is not a duration")
    if isinstance(v, (int, float)):
        return int(v)
    m = re.match(r"^\s*(\d+)\s*([smh]?)\s*$", str(v))
    if not m:
        raise JobError(f"{v!r} is not a duration (use e.g. 90s, 20m, 2h)")
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def parse_schedule(text: str) -> Schedule:
    t = " ".join(str(text).strip().lower().split())
    m = re.match(r"^every (\d+)\s*(m|min|mins|minutes|h|hours?)?$", t)
    if m:
        n = int(m.group(1))
        unit = (m.group(2) or "m")[0]
        s = n * (3600 if unit == "h" else 60)
        if s < 300:
            raise JobError(f"schedule {text!r}: the shortest interval is 5 minutes")
        if s > 86400:
            raise JobError(f"schedule {text!r}: use daily/weekdays for intervals over a day")
        return Schedule("every", every_s=s, text=t)
    parts = t.split(" ")
    if len(parts) != 2:
        raise JobError(f"schedule {text!r}: use 'daily HH:MM', 'weekdays HH:MM', "
                       "'mon,wed HH:MM' or 'every N[m|h]'")
    head, hhmm = parts
    at = parse_hhmm(hhmm)
    if head == "daily":
        days = tuple(range(7))
    elif head == "weekdays":
        days = (0, 1, 2, 3, 4)
    elif head == "weekends":
        days = (5, 6)
    else:
        try:
            days = tuple(sorted({DAY_NAMES.index(d[:3]) for d in head.split(",") if d}))
        except ValueError:
            raise JobError(f"schedule {text!r}: unknown day in {head!r} (use {','.join(DAY_NAMES)})")
        if not days:
            raise JobError(f"schedule {text!r}: no days")
    return Schedule("calendar", at=at, days=days, text=t)


def parse_window(s: Optional[str]) -> Optional[Tuple[dtime, dtime]]:
    """'22:00-07:00' -> (22:00, 07:00); the window may wrap midnight."""
    if not s:
        return None
    m = re.match(r"^\s*(\S+)\s*-\s*(\S+)\s*$", str(s))
    if not m:
        raise JobError(f"quiet_hours {s!r}: use HH:MM-HH:MM")
    a, b = parse_hhmm(m.group(1)), parse_hhmm(m.group(2))
    if a == b:
        raise JobError(f"quiet_hours {s!r}: empty window")
    return a, b


def in_window(win: Optional[Tuple[dtime, dtime]], local: datetime) -> bool:
    if not win:
        return False
    a, b, t = win[0], win[1], local.time().replace(second=0, microsecond=0)
    return a <= t < b if a < b else (t >= a or t < b)


def slot_on(day: date, at: dtime, tz) -> datetime:
    """The UTC instant of wall-clock ``at`` on ``day`` in ``tz``.

    Nonexistent (spring-forward gap): fold=0 resolves with the pre-gap offset,
    i.e. the first instant after the gap (02:30 -> 03:30 local). Ambiguous
    (fall-back): fold=0 is the first occurrence."""
    local = datetime.combine(day, at).replace(tzinfo=tz, fold=0)
    return local.astimezone(timezone.utc)


def latest_slot(sched: Schedule, now: datetime, tz) -> Optional[datetime]:
    """The most recent slot at or before ``now`` (UTC), within the last 8 days."""
    now = now.astimezone(timezone.utc)
    if sched.kind == "every":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        k = int((now - midnight).total_seconds()) // sched.every_s
        return midnight + timedelta(seconds=k * sched.every_s)
    today = now.astimezone(tz).date()
    for back in range(0, 9):
        d = today - timedelta(days=back)
        if d.weekday() not in sched.days:
            continue
        s = slot_on(d, sched.at, tz)
        if s <= now:
            return s
    return None


def slots_between(sched: Schedule, after: datetime, upto: datetime, tz) -> List[datetime]:
    """All slots in (after, upto] (UTC), oldest first. Used to count missed runs."""
    after, upto = after.astimezone(timezone.utc), upto.astimezone(timezone.utc)
    out: List[datetime] = []
    if upto <= after:
        return out
    if sched.kind == "every":
        s = latest_slot(sched, upto, tz)
        while s is not None and s > after and len(out) < 10000:
            out.append(s)
            s = s - timedelta(seconds=sched.every_s)
        return sorted(out)
    d = after.astimezone(tz).date() - timedelta(days=1)
    end = upto.astimezone(tz).date() + timedelta(days=1)
    while d <= end and len(out) < 10000:
        if d.weekday() in sched.days:
            s = slot_on(d, sched.at, tz)
            if after < s <= upto:
                out.append(s)
        d += timedelta(days=1)
    return out


# ── job ─────────────────────────────────────────────────────────────────────
@dataclass
class Constraints:
    ac_power: bool = False
    idle_s: int = 0                       # 0 = no idle requirement
    max_runtime_s: int = 1800
    quiet_hours: Optional[Tuple[dtime, dtime]] = None
    unlocked: bool = False


@dataclass
class Job:
    name: str
    kind: str
    schedule: Schedule
    constraints: Constraints = field(default_factory=Constraints)
    writes: str = "queue"
    mailbox: str = ""                     # "fake:<dir>" | "imap:<user>"
    agent: str = "keyword_prefs"
    prefs: str = ""
    limit: int = 200
    chunk: int = 20
    command: List[str] = field(default_factory=list)
    uses_model_flag: Optional[bool] = None
    stop_model_after: bool = True
    enabled: bool = True

    @property
    def uses_model(self) -> bool:
        if self.uses_model_flag is not None:
            return self.uses_model_flag
        if self.kind == "email_triage":
            return self.agent == "local_model"
        return self.kind == "micro_task"

    @property
    def mailbox_source(self) -> Tuple[str, str]:
        kind, _, rest = self.mailbox.partition(":")
        return kind, rest

    def summary(self) -> Dict[str, Any]:
        c = self.constraints
        return {"name": self.name, "kind": self.kind, "schedule": str(self.schedule),
                "uses_model": self.uses_model, "writes": self.writes, "enabled": self.enabled,
                "constraints": {"ac_power": c.ac_power, "idle_s": c.idle_s,
                                "max_runtime_s": c.max_runtime_s, "unlocked": c.unlocked,
                                "quiet_hours": (f"{c.quiet_hours[0]:%H:%M}-{c.quiet_hours[1]:%H:%M}"
                                                if c.quiet_hours else None)},
                **({"mailbox": self.mailbox} if self.mailbox else {}),
                **({"command": self.command} if self.command else {})}


@dataclass
class Config:
    jobs: List[Job]
    home: Path
    tz: Any
    catchup_s: int = 24 * 3600
    digest_at: Optional[dtime] = None
    digest_notify: bool = False
    digest_quiet: Optional[Tuple[dtime, dtime]] = None
    path: Optional[Path] = None

    def job(self, name: str) -> Job:
        for j in self.jobs:
            if j.name == name:
                return j
        raise JobError(f"no job named {name!r} (have: {', '.join(j.name for j in self.jobs) or 'none'})")


def _expand(p: str) -> str:
    return os.path.expanduser(os.path.expandvars(str(p)))


def local_tz():
    name = os.environ.get("TZ", "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return ZoneInfo(link.split("zoneinfo/", 1)[1])
    except (OSError, ValueError):
        pass
    return datetime.now().astimezone().tzinfo


def _job(raw: Dict[str, Any], i: int) -> Job:
    where = f"job #{i + 1}"
    if not isinstance(raw, dict):
        raise JobError(f"{where}: must be a table")
    name = str(raw.get("name") or "")
    if not NAME_RE.match(name):
        raise JobError(f"{where}: name {name!r} must be 1-64 of letters, digits, _ . - ")
    where = f"job {name!r}"
    kind = raw.get("kind")
    if kind not in KINDS:
        raise JobError(f"{where}: kind must be one of {', '.join(KINDS)}")
    if "schedule" not in raw:
        raise JobError(f"{where}: schedule is required")
    try:
        sched = parse_schedule(raw["schedule"])
        rc = raw.get("constraints") or {}
        idle = rc.get("idle", 0)
        cons = Constraints(
            ac_power=bool(rc.get("ac_power", False)),
            idle_s=(DEFAULT_IDLE_S if idle is True else 0 if idle is False else parse_duration(idle)),
            max_runtime_s=parse_duration(rc.get("max_runtime", 1800)),
            quiet_hours=parse_window(rc.get("quiet_hours")),
            unlocked=bool(rc.get("unlocked", kind in ("skill", "micro_task"))))
    except JobError as e:
        raise JobError(f"{where}: {e}") from None
    if cons.max_runtime_s < 10:
        raise JobError(f"{where}: max_runtime must be at least 10s")
    writes = str((raw.get("approvals") or {}).get("writes", "queue"))
    if writes not in WRITE_POLICIES:
        raise JobError(f"{where}: approvals.writes must be one of {', '.join(WRITE_POLICIES)}")
    job = Job(name=name, kind=kind, schedule=sched, constraints=cons, writes=writes,
              enabled=bool(raw.get("enabled", True)),
              stop_model_after=bool(raw.get("stop_model_after", True)))
    if "uses_model" in raw:
        job.uses_model_flag = bool(raw["uses_model"])
    if kind == "email_triage":
        mb = str(raw.get("mailbox") or "")
        src, _, rest = mb.partition(":")
        if src not in ("fake", "imap") or not rest:
            raise JobError(f"{where}: mailbox must be 'fake:<mailbox dir>' or 'imap:<user>'")
        job.mailbox = f"fake:{_expand(rest)}" if src == "fake" else mb
        job.agent = str(raw.get("agent", "keyword_prefs"))
        if job.agent not in EMAIL_AGENTS:
            raise JobError(f"{where}: agent must be one of {', '.join(EMAIL_AGENTS)}")
        job.prefs = _expand(raw["prefs"]) if raw.get("prefs") else ""
        job.limit = int(raw.get("limit", 200))
        job.chunk = max(1, int(raw.get("chunk", 20)))
    else:
        cmd = raw.get("command")
        if not (isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd)):
            raise JobError(f"{where}: command must be a non-empty list of strings (no shell)")
        job.command = [_expand(x) if x.startswith(("~", "$")) else x for x in cmd]
    return job


def parse_config(data: Dict[str, Any], path: Optional[Path] = None,
                 home: Optional[str] = None) -> Config:
    sch = data.get("scheduler") or {}
    tzname = sch.get("tz")
    try:
        tz = ZoneInfo(tzname) if tzname else local_tz()
    except Exception:
        raise JobError(f"scheduler.tz {tzname!r} is not an IANA time zone")
    raw_jobs = data.get("job") or data.get("jobs") or []
    if isinstance(raw_jobs, dict):
        raw_jobs = [raw_jobs]
    jobs = [_job(r, i) for i, r in enumerate(raw_jobs)]
    seen = set()
    for j in jobs:
        if j.name in seen:
            raise JobError(f"duplicate job name {j.name!r}")
        seen.add(j.name)
    dg = data.get("digest") or {}
    try:
        digest_at = parse_hhmm(dg["at"]) if dg.get("at") else None
        dq = parse_window(dg.get("quiet_hours"))
    except JobError as e:
        raise JobError(f"digest: {e}") from None
    h = home or os.environ.get("AWOS_SCHEDULER_HOME") or sch.get("home") or DEFAULT_HOME
    return Config(jobs=jobs, home=Path(_expand(h)), tz=tz,
                  catchup_s=int(float(sch.get("catchup_hours", 24)) * 3600),
                  digest_at=digest_at, digest_notify=bool(dg.get("notify", False)),
                  digest_quiet=dq, path=path)


def jobs_path(path: Optional[str] = None) -> Path:
    return Path(_expand(path or os.environ.get("AWOS_JOBS_FILE") or DEFAULT_JOBS_FILE))


def load_config(path: Optional[str] = None, home: Optional[str] = None) -> Config:
    p = jobs_path(path)
    if not p.exists():
        return parse_config({}, p, home)
    try:
        data = tomllib.loads(p.read_text())
    except tomllib.TOMLDecodeError as e:
        raise JobError(f"{p}: {e}") from None
    return parse_config(data, p, home)


def due_slot(job: Job, now: datetime, tz, last_slot: Optional[datetime],
             first_seen: Optional[datetime], catchup_s: int) -> Optional[Tuple[datetime, int]]:
    """(slot, missed) when ``job`` should run now, else None.

    ``last_slot``: the slot of the last finished (non-deferred) run.
    ``first_seen``: when the scheduler first saw the job; slots before it never
    run (a new job does not fire for yesterday). Only the latest missed slot runs;
    ``missed`` counts the older ones that were skipped. A slot older than
    ``catchup_s`` is not caught up at all."""
    if not job.enabled:
        return None
    now = now.astimezone(timezone.utc)
    s = latest_slot(job.schedule, now, tz)
    if s is None:
        return None
    floor = last_slot or first_seen
    if floor is not None and s <= floor:
        return None
    if (now - s).total_seconds() > catchup_s:
        return None
    missed = len(slots_between(job.schedule, floor, s, tz)) - 1 if floor else 0
    return s, max(missed, 0)
