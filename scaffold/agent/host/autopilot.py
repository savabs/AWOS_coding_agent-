"""
autopilot.py — the host's supervision autopilot: the parts of babysitting an
agent that the owner should never have to type.

  * classify_stop()     why an attempt ended: "credentials" | "limit" | "hard"
  * progress tracking   did the attempt change the workspace or pass more tests?
  * auto-continue       one bounded continuation (AWOS_HOST_AUTOCONTINUE, default 1)
                        when a limit stop made progress, reusing the workspace
  * digest              `awos status --digest` / <AWOS_HOST_DIR>/digest.md

The worker (worker.py) calls into this module; it holds no queue logic itself.
Spec: docs/specs/supervision_autopilot.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import BLOCKED, DONE, FAILED, QUEUED, RUNNING, CANCELLED, JobSpec

CREDENTIALS = "credentials"
LIMIT = "limit"
HARD = "hard"

PROGRESS_FILE = "progress.json"
CONTINUE_FILE = "continue.json"
DIGEST_FILE = "digest.md"
DIGEST_STATE = "digest_state.json"

#: Stops worth continuing from: the attempt ran out of room, not out of ideas.
LIMIT_RE = re.compile(
    r"max_turns|ran out of turns|wall_budget|timed out after|repeated_tool_call"
    r"|\btruncated\b|turn limit|time limit", re.I)


# ── stop classification ──────────────────────────────────────────────────────


def log_tail(job_dir: Path, max_chars: int = 20000) -> str:
    """The last child attempt's section of worker.log (bounded)."""
    try:
        text = (job_dir / "worker.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    marker = text.rfind("===== child, attempt")
    if marker >= 0:
        text = text[marker:]
    return text[-max_chars:]


def classify_stop(error: str = "", result: Optional[dict] = None, log: str = "") -> str:
    """credentials (a provider rejected the key) > limit (turns/time) > hard."""
    from scaffold.agent.key_health import looks_like_auth_error
    result = result or {}
    text = " ".join(str(x) for x in (error, result.get("error"), result.get("stop_reason")) if x)
    if looks_like_auth_error(text) or looks_like_auth_error(log):
        return CREDENTIALS
    if LIMIT_RE.search(text):
        return LIMIT
    return HARD


# ── progress evidence ────────────────────────────────────────────────────────


def diff_fingerprint(workspace: str, base: Optional[str]) -> str:
    """
    sha256 of the workspace's change against `base` (tracked diff + untracked
    files), or "" when nothing changed. Non-git workspaces give "".
    """
    if not base or not workspace or not Path(workspace).is_dir():
        return ""
    try:
        diff = subprocess.run(["git", "-C", workspace, "diff", base, "--", "."],
                              capture_output=True, text=True, timeout=60)
        untracked = subprocess.run(
            ["git", "-C", workspace, "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if diff.returncode != 0:
        return ""
    h = hashlib.sha256(diff.stdout.encode())
    names = [n for n in untracked.stdout.splitlines()
             if n and "__pycache__" not in n and not n.endswith(".pyc")]
    for name in sorted(names):
        h.update(name.encode())
        try:
            h.update((Path(workspace) / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest() if (diff.stdout.strip() or names) else ""


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def record_attempt_start(job_dir: Path, workspace: str, base: Optional[str],
                         attempt: int) -> dict:
    """Snapshot the workspace diff and the last known test pass count."""
    data = _load(job_dir / PROGRESS_FILE)
    start = {"attempt": attempt, "diff": diff_fingerprint(workspace, base),
             "passed": data.get("last_passed")}
    data["start"] = start
    _save(job_dir / PROGRESS_FILE, data)
    return start


def record_tests(job_dir: Path, tests: Optional[dict]) -> None:
    if not tests:
        return
    data = _load(job_dir / PROGRESS_FILE)
    data["last_passed"] = int(tests.get("passed") or 0)
    _save(job_dir / PROGRESS_FILE, data)


def progress(job_dir: Path, workspace: str, base: Optional[str],
             tests: Optional[dict]) -> tuple[bool, str]:
    """(made progress?, why) for the attempt that just ended."""
    start = _load(job_dir / PROGRESS_FILE).get("start") or {}
    now = diff_fingerprint(workspace, base)
    if now and now != start.get("diff"):
        return True, "the workspace diff changed during the attempt"
    if tests and start.get("passed") is not None and int(tests.get("passed") or 0) > int(start["passed"]):
        return True, f"passing tests rose {start['passed']} -> {tests.get('passed')}"
    if not now:
        return False, "no change in the workspace"
    return False, "the workspace diff did not change during the attempt"


# ── auto-continue ────────────────────────────────────────────────────────────


def max_continuations() -> int:
    try:
        return max(0, int(os.getenv("AWOS_HOST_AUTOCONTINUE", "1")))
    except ValueError:
        return 1


def continuations_used(job_dir: Path) -> int:
    return int(_load(job_dir / CONTINUE_FILE).get("used", 0) or 0)


def spent_so_far(job_dir: Path) -> float:
    data = _load(job_dir / "spend.json")
    total = float(data.get("prior_usd", 0) or 0)
    if data.get("open"):
        total += float(data["open"].get("spent_usd", 0) or 0)
    return total


def continuation_decision(job: JobSpec, job_dir: Path, kind: str, error: str,
                          workspace: str, base: Optional[str],
                          tests: Optional[dict]) -> tuple[bool, str]:
    """(continue?, reason). Only a limit stop with progress, within count and budget."""
    if kind != LIMIT:
        return False, f"not a limit stop ({kind})"
    used, cap = continuations_used(job_dir), max_continuations()
    if used >= cap:
        return False, f"auto-continue used {used}/{cap} (AWOS_HOST_AUTOCONTINUE)"
    if job.budget_usd > 0:
        left = job.budget_usd - spent_so_far(job_dir)
        if left <= 0.0005:
            return False, f"budget ${job.budget_usd:g} used up"
    if not base or not Path(workspace or "").is_dir():
        return False, "no isolated workspace to continue in"
    moved, why = progress(job_dir, workspace, base, tests)
    if not moved:
        return False, f"no progress: {why}"
    return True, why


def changed_files(workspace: str, base: Optional[str]) -> list[str]:
    if not base or not workspace or not Path(workspace).is_dir():
        return []
    try:
        tracked = subprocess.run(["git", "-C", workspace, "diff", "--name-only", base, "--", "."],
                                 capture_output=True, text=True, timeout=60).stdout.split()
        new = subprocess.run(["git", "-C", workspace, "ls-files", "--others", "--exclude-standard"],
                             capture_output=True, text=True, timeout=60).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted({n for n in tracked + new if "__pycache__" not in n and not n.endswith(".pyc")})


def stop_summary(error: str, result: dict, log: str, workspace: str, base: Optional[str]) -> str:
    """'<why it stopped>; files changed so far: ...; last said: ...' for the continue note."""
    parts = [str(result.get("summary") or error or "it hit its limit")[:160]]
    files = changed_files(workspace, base)
    if files:
        parts.append("files changed so far: " + ", ".join(files[:12]))
    said = re.findall(r"\[agent\] t\d+ says: (.+)", log or "")
    if said:
        parts.append("last said: " + said[-1].strip()[:160])
    return "; ".join(parts)


def queue_continuation(job_dir: Path, summary: str, reason: str) -> dict:
    data = _load(job_dir / CONTINUE_FILE)
    data["used"] = int(data.get("used", 0) or 0) + 1
    data["note"] = continue_note(summary)
    data["reason"] = reason
    data["pending"] = True
    _save(job_dir / CONTINUE_FILE, data)
    return data


def continue_note(summary: str) -> str:
    summary = " ".join(str(summary or "").split())[:600]
    return ("continue from where you stopped: " + (summary or "the previous attempt hit its limit")
            + ". The workspace already holds your earlier edits; do not start over.")


def take_continue_note(job_dir: Path) -> Optional[str]:
    """The pending note for this attempt's child (consumed once)."""
    path = job_dir / CONTINUE_FILE
    data = _load(path)
    if not data.get("pending"):
        return None
    data["pending"] = False
    _save(path, data)
    return data.get("note")


# ── digest ───────────────────────────────────────────────────────────────────


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _elapsed(iso: Optional[str]) -> str:
    dt = _parse(iso)
    if not dt:
        return "?"
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    return f"{secs // 3600}h{secs % 3600 // 60:02d}m" if secs >= 3600 else f"{secs // 60}m{secs % 60:02d}s"


def last_journal_step(job_dir: Path) -> str:
    try:
        lines = (job_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "-"
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        name = rec.get("key") or rec.get("step") or rec.get("kind")
        return str(name)
    return "-"


def ledger_spend_today(ledger: Optional[Path] = None) -> float:
    if ledger is None:
        from .worker import LEDGER_PATH as ledger
    try:
        entries = json.loads(Path(ledger).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    today = datetime.now().strftime("%Y-%m-%d")
    total = 0.0
    for e in entries if isinstance(entries, list) else []:
        try:
            if str(e.get("timestamp", "")).startswith(today):
                total += float(e.get("cost", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            pass
    return total


def _job_cost(job: JobSpec, job_dir: Path) -> float:
    res = job.result or {}
    try:
        cost = float(res.get("cost_usd") or 0)
    except (TypeError, ValueError):
        cost = 0.0
    return max(cost, spent_so_far(job_dir))


def _tests_str(res: dict) -> str:
    t = res.get("tests")
    if isinstance(t, dict):
        return f"{t.get('status')} {t.get('passed', 0)}p/{t.get('failed', 0)}f"
    return "n/a"


def build_digest(queue, since: Optional[str] = None, ledger: Optional[Path] = None) -> str:
    jobs = queue.list()
    now = datetime.now(timezone.utc)
    today = datetime.now().date()
    out = [f"# AWOS digest ({datetime.now().strftime('%Y-%m-%d %H:%M')})", ""]

    running = [j for j in jobs if j.state == RUNNING]
    if running:
        for j in running:
            out.append(f"- **Running:** {j.id[:8]} {_elapsed(j.started_at)} "
                       f"(attempt {j.attempts}), last step `{last_journal_step(queue.job_dir(j.id))}` "
                       f"— {j.goal[:60]}")
    else:
        out.append("- **Running:** nothing")
    out.append(f"- **Queued:** {sum(1 for j in jobs if j.state == QUEUED)}")

    since_dt = _parse(since)
    finished = [j for j in jobs if j.state in (DONE, FAILED, CANCELLED)
                and (since_dt is None or (_parse(j.finished_at) or now) > since_dt)]
    label = "since last digest" if since_dt else "all time"
    out += ["", f"## Finished ({label}): {len(finished)}"]
    for j in finished[-10:]:
        res = j.result or {}
        branch = (res.get("handoff") or {}).get("branch") or "-"
        out.append(f"- {j.id[:8]} **{j.state}** tests {_tests_str(res)}, "
                   f"${_job_cost(j, queue.job_dir(j.id)):.4f}, branch `{branch}` — {j.goal[:50]}")

    blocked = [j for j in jobs if j.state == BLOCKED]
    out += ["", f"## Blocked: {len(blocked)}"]
    for j in blocked:
        action = (j.result or {}).get("action") or j.error or "see the job report"
        out.append(f"- {j.id[:8]} {j.error or 'blocked'} -> **do:** {action}")

    host_today = sum(_job_cost(j, queue.job_dir(j.id)) for j in jobs
                     if (_parse(j.finished_at) or _parse(j.started_at))
                     and (_parse(j.finished_at) or _parse(j.started_at)).astimezone().date() == today)
    out += ["", f"**Spend today:** host jobs ${host_today:.4f}; all AWOS (ledger) "
                f"${ledger_spend_today(ledger):.4f}", ""]
    return "\n".join(out)


def write_digest(queue, ledger: Optional[Path] = None) -> Path:
    """Write <root>/digest.md (since the owner's last `status --digest`)."""
    since = _load(queue.root / DIGEST_STATE).get("last_digest_at")
    path = queue.root / DIGEST_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(build_digest(queue, since, ledger), encoding="utf-8")
    os.replace(tmp, path)
    return path


def digest_for_owner(queue, ledger: Optional[Path] = None) -> str:
    """`awos status --digest`: the digest since the last one, then advance the marker."""
    state_path = queue.root / DIGEST_STATE
    since = _load(state_path).get("last_digest_at")
    text = build_digest(queue, since, ledger)
    _save(state_path, {"last_digest_at": datetime.now(timezone.utc).isoformat()})
    try:
        (queue.root / DIGEST_FILE).write_text(text, encoding="utf-8")
    except OSError:
        pass
    return text
