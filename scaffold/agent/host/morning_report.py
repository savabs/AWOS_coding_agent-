"""Morning report: what the host did overnight, in one page.

Spec: docs/specs/host_ops.md

Reads <root>/queue.sqlite (owned by the queue builder) defensively — the table
and column names are discovered with PRAGMA, never assumed — plus each job's
<root>/jobs/<id>/handoff.json and report.md (owned by the handoff builder).
Writes <root>/morning_report.md. Read-only on the queue DB; no model calls.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:  # same package; keep the report usable even if watchdog is missing
    from scaffold.agent.host import watchdog as _wd
except ImportError:  # pragma: no cover
    _wd = None


def _root(root=None) -> Path:
    if _wd is not None:
        return _wd.host_root(root)
    import os
    return Path(root or os.environ.get("AWOS_HOST_DIR") or Path.home() / ".awos" / "host")


@dataclass
class JobRow:
    id: str
    goal: str = ""
    state: str = "?"
    cost_usd: Optional[float] = None
    budget_usd: Optional[float] = None
    minutes: Optional[float] = None
    branch: str = ""
    tests: str = ""
    created_at: str = ""
    attempts: Optional[int] = None
    summary: str = ""
    report_path: str = ""
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- reading

def _find_jobs_table(con: sqlite3.Connection) -> Optional[str]:
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    best, best_score = None, 0
    for t in tables:
        cols = {r[1].lower() for r in con.execute(f'PRAGMA table_info("{t}")')}
        score = len(cols & {"id", "goal", "state", "status", "result", "budget_usd"})
        if t.lower() in ("jobs", "job", "queue"):
            score += 2
        if "goal" in cols and score > best_score:
            best, best_score = t, score
    return best


def _loads(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, (str, bytes)) and v:
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}
    return {}


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _epoch(x: float) -> Optional[datetime]:
    if x > 1e11:          # epoch milliseconds
        x /= 1000.0
    try:
        return datetime.fromtimestamp(x, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_ts(v: Any) -> Optional[datetime]:
    """ISO-8601 text, or epoch seconds/ms as a number *or* numeric text
    (SQLite REAL/INTEGER columns, or a str() of one)."""
    if v in (None, "") or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return _epoch(float(v))
    t = str(v).strip()
    try:
        return _epoch(float(t))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_MIN_TS = datetime.min.replace(tzinfo=timezone.utc)


def _sort_key(j: "JobRow") -> tuple:
    """Chronological across mixed ISO / epoch created_at; undated jobs last."""
    ts = _parse_ts(j.created_at)
    return (ts is None, ts or _MIN_TS, j.created_at)


def _tests_status(d: dict) -> str:
    v = _first(d, "tests", "test_status", "tests_status", "verify", "verification")
    if isinstance(v, dict):
        p, f = v.get("passed"), v.get("failed")
        if p is not None or f is not None:
            return f"{p or 0} passed / {f or 0} failed"
        return str(_first(v, "status", "result", default=""))
    if isinstance(v, bool):
        return "pass" if v else "fail"
    if v is not None:
        return str(v)
    if "tests_passed" in d:
        return "pass" if d["tests_passed"] else "fail"
    return ""


def _job_from_row(row: dict, jobs_dir: Path) -> JobRow:
    lower = {k.lower(): v for k, v in row.items()}
    result = _loads(lower.get("result"))
    jid = str(_first(lower, "id", "job_id", default="?"))
    handoff = _loads(_read_text(jobs_dir / jid / "handoff.json"))
    merged = {**result, **{k: v for k, v in handoff.items() if v not in (None, "")}}

    cost = _first(merged, "cost_usd", "spent_usd", "usd", "cost")
    if cost is None:
        cost = _first(lower, "cost_usd", "spent_usd")
    minutes = _first(merged, "minutes")
    if minutes is None:
        secs = _first(merged, "duration_s", "elapsed_s", "wall_s", "seconds")
        if secs is None:
            secs = _first(lower, "duration_s", "elapsed_s")
        if secs is not None:
            try:
                minutes = float(secs) / 60.0
            except (TypeError, ValueError):
                minutes = None
    if minutes is None:
        a = _parse_ts(_first(lower, "started_at", "start_at"))
        b = _parse_ts(_first(lower, "finished_at", "ended_at", "completed_at", "updated_at"))
        if a and b and b >= a:
            minutes = (b - a).total_seconds() / 60.0

    report = jobs_dir / jid / "report.md"
    rp = str(_first(handoff, "report", "report_path", default="")) or (
        str(report) if report.exists() else "")
    summary = str(_first(merged, "summary", "error", default=""))
    if not summary and report.exists():
        summary = _first_line(_read_text(report))

    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    attempts = lower.get("attempts")
    return JobRow(
        id=jid,
        goal=str(_first(lower, "goal", default="")),
        state=str(_first(lower, "state", "status", default="?")),
        cost_usd=_num(cost),
        budget_usd=_num(lower.get("budget_usd")),
        minutes=_num(minutes),
        branch=str(_first(merged, "branch", "branch_name", default="")),
        tests=_tests_status(merged),
        created_at=str(lower.get("created_at") or ""),
        attempts=int(attempts) if isinstance(attempts, (int, float)) else None,
        summary=summary,
        report_path=rp,
    )


def _read_text(p: Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s
    return ""


def load_jobs(root=None, since_hours: Optional[float] = None,
              now: Optional[datetime] = None) -> tuple[list[JobRow], list[str]]:
    """Return (jobs, warnings). Never raises on a missing/odd DB."""
    root = _root(root)
    db = root / "queue.sqlite"
    warnings: list[str] = []
    if not db.exists():
        return [], [f"no queue DB at {db}"]
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return [], [f"cannot open {db}: {exc}"]
    try:
        con.row_factory = sqlite3.Row
        table = _find_jobs_table(con)
        if not table:
            return [], [f"no jobs table (with a 'goal' column) in {db}"]
        rows = [dict(r) for r in con.execute(f'SELECT * FROM "{table}"')]
    except sqlite3.Error as exc:
        return [], [f"cannot read {db}: {exc}"]
    finally:
        con.close()

    jobs = [_job_from_row(r, root / "jobs") for r in rows]
    if since_hours is not None:
        now = now or datetime.now(timezone.utc)
        cut = now - timedelta(hours=since_hours)
        kept = []
        for j in jobs:
            ts = _parse_ts(j.created_at)
            # keep undated jobs and anything still active
            if ts is None or ts >= cut or j.state in ("queued", "running", "paused"):
                kept.append(j)
        jobs = kept
    jobs.sort(key=_sort_key)
    return jobs, warnings


# --------------------------------------------------------------------------- rendering

def _cell(s: str, n: int = 70) -> str:
    s = " ".join(str(s).split()).replace("|", "\\|")
    return s if len(s) <= n else s[: n - 1] + "…"


def render(jobs: list[JobRow], warnings: list[str], root: Path,
           now: Optional[datetime] = None, health: Optional[list] = None) -> str:
    now = now or datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.state] = counts.get(j.state, 0) + 1
    spent = sum(j.cost_usd or 0.0 for j in jobs)
    mins = sum(j.minutes or 0.0 for j in jobs)
    branches = [j for j in jobs if j.branch]

    out = [f"# AWOS morning report — {now.strftime('%Y-%m-%d %H:%M UTC')}", ""]
    state_line = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "no jobs"
    out += [f"**{len(jobs)} jobs** ({state_line}) · **${spent:.2f}** spent · "
            f"**{mins:.0f} min** of work · **{len(branches)} branch{'es' if len(branches) != 1 else ''}** ready for review",
            ""]
    if health:
        out += ["## Host health", ""]
        for f in health:
            out.append(f"- **{f.check}**: {f.status} — {_cell(f.detail, 160)}"
                       + (f" → `{f.action}`" if f.action and not f.ok else ""))
        out.append("")
    if warnings:
        out += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]
    if jobs:
        out += ["## Jobs", "",
                "| id | state | goal | $ (budget) | min | branch | tests |",
                "|---|---|---|---|---|---|---|"]
        for j in jobs:
            usd = "-" if j.cost_usd is None else f"{j.cost_usd:.2f}"
            if j.budget_usd is not None:
                usd += f" ({j.budget_usd:.2f})"
            mn = "-" if j.minutes is None else f"{j.minutes:.1f}"
            out.append(f"| `{j.id[:8]}` | {j.state} | {_cell(j.goal)} | {usd} | {mn} | "
                       f"{('`' + j.branch + '`') if j.branch else '-'} | {_cell(j.tests, 30) or '-'} |")
        out.append("")
        attention = [j for j in jobs if j.state in ("failed", "paused", "cancelled")]
        if attention:
            out += ["## Needs attention", ""]
            for j in attention:
                out.append(f"- `{j.id[:8]}` **{j.state}** — {_cell(j.goal, 80)}"
                           + (f": {_cell(j.summary, 160)}" if j.summary else ""))
            out.append("")
        if branches:
            out += ["## Review", ""]
            for j in branches:
                line = f"- `{j.branch}` — {_cell(j.goal, 80)}"
                if j.report_path:
                    line += f" ([report]({j.report_path}))"
                out.append(line)
            out += ["", "Branches are local only; nothing was pushed.", ""]
    out.append(f"_Source: {root / 'queue.sqlite'}_")
    return "\n".join(out) + "\n"


def build(root=None, since_hours: Optional[float] = 24.0, write: bool = True,
          with_health: bool = True, now: Optional[datetime] = None) -> tuple[str, Path]:
    r = _root(root)
    jobs, warnings = load_jobs(r, since_hours=since_hours, now=now)
    health = None
    if with_health and _wd is not None:
        # heartbeat + pause only: the report must not block on DNS or pmset
        health = [_wd.check_heartbeat(r)]
        p = _wd.is_paused(r)
        if p:
            health.append(_wd.Finding("pause", "paused",
                                      _wd.describe_pause(p),
                                      action="python -m scaffold.agent.host.watchdog resume"))
    text = render(jobs, warnings, r, now=now, health=health)
    path = r / "morning_report.md"
    if write:
        r.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return text, path


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.agent.host.morning_report")
    ap.add_argument("--root", help="host dir (default: $AWOS_HOST_DIR or ~/.awos/host)")
    ap.add_argument("--since-hours", type=float, default=24.0)
    ap.add_argument("--all", action="store_true", help="include every job")
    ap.add_argument("--no-health", action="store_true")
    ap.add_argument("--stdout", action="store_true", help="print, do not write the file")
    a = ap.parse_args(argv)
    text, path = build(a.root, since_hours=None if a.all else a.since_hours,
                       write=not a.stdout, with_health=not a.no_health)
    print(text if a.stdout else f"{text}\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
