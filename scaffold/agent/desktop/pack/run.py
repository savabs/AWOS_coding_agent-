"""run.py — collect one month's pack, source by source, resumable.

    state = run_month(cfg, "2026-10", runner=my_runner, check=verify.check)

For each enabled source that is due this month:
  1. files the owner dropped into Pack/<M>/<id>/ by hand are adopted first;
  2. the source's taught skill runs through the injected ``runner(source, period, ctx)``
     (the live macOS runner in production, fakes in tests). The start URL must pass the
     policy first. A login / OTP / captcha wall -> the runner raises NeedsYou (or returns
     RunnerResult(status="needs_you")) and the source is marked needs_you;
  3. new downloads are collected (files.collect) and each filed document goes through
     ``check(doc_path, source, period) -> Verdict`` (default: stub, UNVERIFIED);
  4. the outcome is recorded in Pack/<M>/.pack_state.json:

     collected_verified    all expected files present, every one VERIFIED
     collected_unverified  all expected files present, at least one could not be confirmed
     missing               fewer good files than expected (reason says why)
     needs_you             stopped at a checkpoint only the owner can pass (login/OTP/captcha)
     failed                runner crashed / halted / policy refused (reason says why)
     not_due               quarterly source outside a quarter-end month

Idempotent per (month, source): a re-run skips collected_* sources (unless force) and
retries missing / failed / needs_you. Resume: the state is written before the runner
starts (status "running" + the Downloads snapshot), so after a crash the next run first
collects whatever finished downloading, and only re-runs the skill if that is not enough.
A per-month lock file stops two runs on the same month.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import (MISMATCH, RUN_FAILED, RUN_NEEDS_YOU, RUN_OK, SUSPICIOUS, UNVERIFIED, VERIFIED, Checkpoint,
               NeedsYou, Period, RunContext, RunnerResult, Verdict, raise_needs_you, stub_check)
from .config import QUARTER_END_MONTHS, PackConfig, Source, url_problem
from .files import Collected, FiledDoc, Ledger, adopt_manual, collect, snapshot

STATE_NAME = ".pack_state.json"
LOCK_NAME = ".pack.lock"
STATE_VERSION = 1

COLLECTED_VERIFIED = "collected_verified"
COLLECTED_UNVERIFIED = "collected_unverified"
MISSING = "missing"
NEEDS_YOU = "needs_you"
FAILED = "failed"
NOT_DUE = "not_due"
RUNNING = "running"
STATUSES = (COLLECTED_VERIFIED, COLLECTED_UNVERIFIED, MISSING, NEEDS_YOU, FAILED, NOT_DUE)
DONE = (COLLECTED_VERIFIED, COLLECTED_UNVERIFIED)
BAD_VERDICTS = (MISMATCH, SUSPICIOUS)
DENIED_ACTIONS = frozenset({"pay", "purchase", "transfer", "delete", "submit_payment", "cancel_subscription"})
MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class PackBusy(RuntimeError):
    """Another run holds this month's lock."""


# ── periods ─────────────────────────────────────────────────────────────────
def parse_month(month: str) -> str:
    m = MONTH_RE.match(str(month).strip())
    if not m:
        raise ValueError(f"month must look like 2026-10, got {month!r}")
    return m.group(0)


def previous_month(today: Optional[date] = None) -> str:
    t = today or date.today()
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return f"{y:04d}-{m:02d}"


def is_due(source: Source, month: str) -> bool:
    return source.cadence == "monthly" or int(month[5:7]) in QUARTER_END_MONTHS


def period_for(source: Source, month: str) -> Period:
    y, m = int(month[:4]), int(month[5:7])
    end = date(y, m, calendar.monthrange(y, m)[1])
    if source.cadence == "quarterly":
        qs = m - (m - 1) % 3
        return Period(month, date(y, qs, 1), end, "quarterly")
    return Period(month, date(y, m, 1), end, "monthly")


# ── policy ──────────────────────────────────────────────────────────────────
def domain_policy(source: Source) -> Callable[[str, str], bool]:
    """Default ``allowed(url, action)``: https host in the source's allowed_domains, and never a
    money-moving / destructive action. pack/policy.py may layer a stricter one on top."""
    def allowed(url: str, action: str = "navigate") -> bool:
        return url_problem(url, source.allowed_domains) is None and str(action).lower() not in DENIED_ACTIONS
    return allowed


def default_policy_factory() -> Callable[[Source], Callable[[str, str], bool]]:
    """Use pack.policy if it is installed: ``for_source(source)`` or a module-level
    ``allowed(url, action)`` (ANDed with the domain policy). Otherwise the domain policy."""
    try:
        from . import policy as pol  # type: ignore[attr-defined]
    except ImportError:
        return domain_policy
    if hasattr(pol, "for_source"):
        return pol.for_source
    if hasattr(pol, "SourceConfig") and hasattr(pol, "allowed"):
        # pack/policy.py: allowed(url, action, SourceConfig) -> Decision. Exact hosts from the
        # config become its domains (subdomain-matching there, exact-host here: both must pass);
        # loopback test hosts are handled by its allow_loopback, not listed as domains.
        def factory(source: Source) -> Callable[[str, str], bool]:
            base = domain_policy(source)
            hosts = [h[2:] if h.startswith("*.") else h for h in source.allowed_domains
                     if h not in ("127.0.0.1", "localhost", "::1")]
            try:
                cfg = pol.SourceConfig(name=source.name, domains=tuple(hosts), allow_loopback=True)
            except Exception:
                return lambda url, action="navigate": False       # unusable config: refuse all
            return lambda url, action="navigate": (base(url, action)
                                                   and bool(pol.allowed(url, action, cfg).ok))
        return factory
    if hasattr(pol, "allowed"):
        def factory(source: Source) -> Callable[[str, str], bool]:
            base = domain_policy(source)
            return lambda url, action="navigate": base(url, action) and bool(pol.allowed(url, action))
        return factory
    return domain_policy


def default_check() -> Callable[[Path, Source, Period], Any]:
    try:
        from . import verify  # type: ignore[attr-defined]
    except ImportError:
        return stub_check
    vcheck = getattr(verify, "check", None)
    if vcheck is None:
        return stub_check
    # pack/verify.py returns its own Verdict (lower-case statuses, `fields`); map it onto ours.
    status_map = {"verified": VERIFIED, "unverified": UNVERIFIED, "locked": UNVERIFIED,
                  "wrong_period": MISMATCH, "duplicate": MISMATCH,
                  "corrupt": SUSPICIOUS, "suspicious": SUSPICIOUS}

    def check(path: Path, source: Source, period: Period) -> Verdict:
        spec = {"name": source.name, "doc_type": source.kind, "vendor": source.name}
        v = vcheck(path, spec, getattr(period, "label", str(period)))
        status = getattr(v, "status", "unverified")
        facts = {k: (str(x) if x is not None else None)
                 for k, x in (getattr(v, "fields", None) or {}).items()}
        return Verdict(status_map.get(str(status), UNVERIFIED), list(getattr(v, "reasons", []) or []), facts)
    return check


def unwired_runner(source: Source, period: Period, ctx: RunContext) -> RunnerResult:
    return RunnerResult(RUN_FAILED, "no skill runner is wired in (pass --runner module:function); "
                                    "drop the file into the pack folder by hand meanwhile")


# ── redaction ───────────────────────────────────────────────────────────────
_SECRETISH = re.compile(r"(?i)\b(password|passwd|pwd|pin|otp|passcode|token|secret|api[_-]?key)\b(\s*[:=]\s*|\s+is\s+)\S+")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s]*")


def scrub(text: Any, limit: int = 400) -> str:
    """Reasons go into reports and a zip: drop anything that looks like a secret or a URL query."""
    s = str(text or "")
    s = _SECRETISH.sub(lambda m: f"{m.group(1)}=[REDACTED]", s)
    s = _URL_QUERY.sub(r"\1?[…]", s)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ── state ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path(root: Path, month: str) -> Path:
    return Path(root) / month / STATE_NAME


def load_state(root: Path, month: str) -> Dict[str, Any]:
    p = state_path(root, month)
    if p.is_file():
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(st, dict) and isinstance(st.get("sources"), dict):
                return st
        except (OSError, json.JSONDecodeError):
            bad = p.with_suffix(f".corrupt-{int(time.time())}")
            os.replace(p, bad)            # keep it for a human, start clean; the ledger still dedupes
    return {"version": STATE_VERSION, "month": month, "sources": {}, "runs": []}


def save_state(root: Path, month: str, st: Dict[str, Any]) -> None:
    p = state_path(root, month)
    p.parent.mkdir(parents=True, exist_ok=True)
    st["updated"] = _now()
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


class MonthLock:
    def __init__(self, root: Path, month: str):
        self.path = Path(root) / month / LOCK_NAME

    def __enter__(self) -> "MonthLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise PackBusy(f"another pack run is working on {self.path.parent.name} ({self.path})")
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            return self
        raise PackBusy(f"could not take {self.path}")

    def _stale(self) -> bool:
        try:
            pid = int(self.path.read_text().strip() or "0")
        except (OSError, ValueError):
            return True
        if pid <= 0 or pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def __exit__(self, *exc) -> None:
        self.path.unlink(missing_ok=True)


# ── one source ──────────────────────────────────────────────────────────────
def _verify(doc: FiledDoc, root: Path, source: Source, period: Period, check: Callable) -> Dict[str, Any]:
    try:
        v = Verdict.coerce(check(Path(root) / doc.path, source, period))
    except Exception as e:                       # a verifier bug never loses a document
        v = Verdict(UNVERIFIED, [f"verifier error: {type(e).__name__}: {scrub(e, 160)}"])
    reasons = [scrub(r, 200) for r in v.reasons]
    if not doc.type_ok:
        reasons.append(f"unexpected file type .{Path(doc.path).suffix.lstrip('.')} "
                       f"(expected {', '.join(source.expected.types)})")
    rec = doc.to_dict()
    rec["verdict"] = {"status": v.status, "reasons": reasons, "facts": v.facts}
    rec["flagged"] = v.status in BAD_VERDICTS
    return rec


def _good(files: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [f for f in files if f.get("type_ok") and not f.get("flagged")]


def _decide(entry: Dict[str, Any], source: Source, res: RunnerResult, got: Optional[Collected]) -> None:
    good = _good(entry["files"])
    need = source.expected.count
    entry["checkpoint"] = None
    if len(good) >= need:
        entry["status"] = (COLLECTED_VERIFIED if all(f["verdict"]["status"] == VERIFIED for f in good)
                           else COLLECTED_UNVERIFIED)
        entry["reason"] = "" if entry["status"] == COLLECTED_VERIFIED else \
            "; ".join(sorted({r for f in good if f["verdict"]["status"] != VERIFIED for r in f["verdict"]["reasons"]})) \
            or "could not be checked automatically"
        return
    if res.status == RUN_NEEDS_YOU:
        cp = res.checkpoint or Checkpoint("unknown")
        entry["status"] = NEEDS_YOU
        entry["checkpoint"] = {"kind": cp.kind, "message": scrub(cp.message, 200), "url": scrub(cp.url, 200)}
        entry["reason"] = f"stopped at a {cp.kind} step" + (f": {scrub(cp.message, 200)}" if cp.message else "")
        return
    if res.status == RUN_FAILED:
        entry["status"] = FAILED
        entry["reason"] = scrub(res.reason) or "the skill run failed"
        return
    entry["status"] = MISSING
    why: List[str] = [f"have {len(good)} of {need} expected file(s)"]
    if got is not None:
        old = [d for d in got.duplicates if not d.get("same_slot")]
        if old:
            why.append("the portal gave a document already filed for " +
                       ", ".join(sorted({f"{d.get('month')}/{d.get('source')}" for d in old})) +
                       " (probably the new one is not out yet)")
        if got.pending:
            why.append(f"download did not finish: {', '.join(got.pending[:3])}")
        if not got.filed and not got.duplicates and not got.pending:
            why.append("the run finished but no new file appeared in Downloads")
    flagged = [f for f in entry["files"] if f.get("flagged") or not f.get("type_ok")]
    if flagged:
        why.append(f"{len(flagged)} file(s) set aside after checks")
    entry["reason"] = "; ".join(why)


def run_source(cfg: PackConfig, month: str, source: Source, st: Dict[str, Any], *, runner: Callable,
               check: Callable, policy_factory: Callable, ledger: Ledger, force: bool = False,
               sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
               log: Callable[[str], None] = lambda s: None) -> Dict[str, Any]:
    root = cfg.root
    entry = st["sources"].setdefault(source.id, {"status": None, "files": [], "attempts": 0, "history": []})
    entry.setdefault("files", [])
    entry.setdefault("history", [])
    period = period_for(source, month)
    save = lambda: save_state(root, month, st)    # noqa: E731

    if not is_due(source, month):
        entry.update(status=NOT_DUE, reason=f"quarterly; due in {', '.join(map(str, QUARTER_END_MONTHS))}",
                     checkpoint=None)
        save()
        return entry
    if entry.get("status") in DONE and not force:
        log(f"{source.id}: already {entry['status']}, skipping")
        return entry

    def absorb(got: Collected) -> None:
        for d in got.filed:
            entry["files"].append(_verify(d, root, source, period, check))
        if got.duplicates:
            entry.setdefault("duplicates", []).extend(got.duplicates)

    gone = [f for f in entry["files"] if not (Path(root) / f["path"]).is_file()]
    if gone:                                      # the owner deleted / moved a filed document
        entry["files"] = [f for f in entry["files"] if f not in gone]
        entry["history"].append({"ts": _now(), "event": "files_gone", "paths": [f["path"] for f in gone]})

    # 1. resume: an interrupted attempt may have left finished downloads behind
    if entry.get("status") == RUNNING and entry.get("before") is not None:
        log(f"{source.id}: resuming an interrupted attempt")
        got = collect(cfg.downloads, entry["before"], root, month, source, ledger, already=len(entry["files"]),
                      settle_s=cfg.settle_s, wait_s=cfg.wait_s, sleep=sleep, clock=clock)
        absorb(got)
        entry["history"].append({"ts": _now(), "event": "resumed", "filed": len(got.filed)})
    # 2. files dropped in by hand
    absorb(adopt_manual(root, month, source, ledger))
    if len(_good(entry["files"])) >= source.expected.count and not force:
        _decide(entry, source, RunnerResult(RUN_OK), None)
        entry.pop("before", None)
        save()
        return entry
    # 3. run the taught skill
    if not source.skill:
        _decide(entry, source, RunnerResult(RUN_OK), None)
        entry["reason"] = "no skill taught for this source yet; " + entry["reason"]
        entry.pop("before", None)
        save()
        return entry
    allowed = policy_factory(source)
    if not allowed(source.start_url, "navigate"):
        entry.update(status=FAILED, reason=f"policy refused the start URL {scrub(source.start_url, 200)}",
                     checkpoint=None)
        entry.pop("before", None)
        save()
        return entry

    before = snapshot(cfg.downloads)
    entry.update(status=RUNNING, before=before, started=_now(), attempts=int(entry.get("attempts", 0)) + 1)
    save()
    ctx = RunContext(downloads=Path(cfg.downloads), allowed=allowed, checkpoint=raise_needs_you,
                     password_item=source.password_hint, log=log)
    try:
        res = runner(source, period, ctx) or RunnerResult(RUN_OK)
        if not isinstance(res, RunnerResult):
            res = RunnerResult(str(getattr(res, "status", RUN_OK)), str(getattr(res, "reason", "")),
                               getattr(res, "checkpoint", None), dict(getattr(res, "detail", {}) or {}))
    except NeedsYou as e:
        res = RunnerResult(RUN_NEEDS_YOU, str(e), e.checkpoint)
    except Exception as e:
        log(traceback.format_exc())
        res = RunnerResult(RUN_FAILED, f"{type(e).__name__}: {e}")
    if res.status not in (RUN_OK, RUN_NEEDS_YOU, RUN_FAILED):
        res = RunnerResult(RUN_FAILED, f"runner returned unknown status {res.status!r}")
    # 4. collect whatever arrived, even after a checkpoint or a failure
    got = collect(cfg.downloads, before, root, month, source, ledger, already=len(entry["files"]),
                  settle_s=cfg.settle_s, wait_s=cfg.wait_s, sleep=sleep, clock=clock)
    absorb(got)
    _decide(entry, source, res, got)
    entry.pop("before", None)
    entry["finished"] = _now()
    entry["history"].append({"ts": entry["finished"], "event": "run", "runner": res.status,
                             "filed": len(got.filed), "duplicates": len(got.duplicates),
                             "pending": len(got.pending), "status": entry["status"]})
    entry["history"] = entry["history"][-20:]
    save()
    log(f"{source.id}: {entry['status']} {entry.get('reason', '')}")
    return entry


# ── one month ───────────────────────────────────────────────────────────────
def run_month(cfg: PackConfig, month: str, *, runner: Optional[Callable] = None, check: Optional[Callable] = None,
              only: Optional[str] = None, force: bool = False, policy_factory: Optional[Callable] = None,
              sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
              log: Callable[[str], None] = lambda s: None) -> Dict[str, Any]:
    month = parse_month(month)
    sources = [cfg.source(only)] if only else [s for s in cfg.sources if s.enabled]
    runner = runner or unwired_runner
    check = check or default_check()
    policy_factory = policy_factory or default_policy_factory()
    ledger = Ledger(cfg.root)
    with MonthLock(cfg.root, month):
        st = load_state(cfg.root, month)
        st.setdefault("runs", []).append({"ts": _now(), "only": only, "force": force})
        st["runs"] = st["runs"][-50:]
        for s in sources:
            try:
                run_source(cfg, month, s, st, runner=runner, check=check, policy_factory=policy_factory,
                           ledger=ledger, force=force, sleep=sleep, clock=clock, log=log)
            except PackBusy:
                raise
            except Exception as e:              # one broken source never stops the month
                e_entry = st["sources"].setdefault(s.id, {"files": [], "history": []})
                e_entry.update(status=FAILED, reason=scrub(f"internal error: {type(e).__name__}: {e}"),
                               checkpoint=None)
                e_entry.pop("before", None)
                save_state(cfg.root, month, st)
                log(traceback.format_exc())
        save_state(cfg.root, month, st)
    return st


def totals(st: Dict[str, Any]) -> Dict[str, int]:
    out = {k: 0 for k in STATUSES}
    for e in st.get("sources", {}).values():
        k = e.get("status") or MISSING
        out[k] = out.get(k, 0) + 1
    return out
