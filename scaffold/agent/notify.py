"""
notify.py — tell the owner when a job reaches a final (or blocked) state,
so they never have to ask "what's the status?".

Every event goes to:
  (a) <AWOS_HOST_DIR>/inbox.jsonl — append-only, one JSON line per event, always;
  (b) a macOS notification (osascript `display notification`) when on darwin
      and AWOS_NOTIFY_MACOS != 0. Bounded by a timeout; a failure never
      raises. Each issued command is logged to <AWOS_HOST_DIR>/notify.log.

Identical events (same dedupe key) within AWOS_NOTIFY_DEDUPE_SEC (default 6 h)
are delivered once. Text is scrubbed of anything secret-shaped and of the
values of *_API_KEY / *_TOKEN env vars before it leaves the process.

Spec: docs/specs/supervision_autopilot.md.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

INBOX = "inbox.jsonl"
STATE = "notify_state.json"
LOG = "notify.log"
OSASCRIPT_TIMEOUT_SEC = 5.0

_SECRET_SHAPES = re.compile(
    r"sk-ant-[A-Za-z0-9_\-]{8,}|sk-or-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{20,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._\-]{12,}")


def _root(root=None) -> Path:
    if root:
        return Path(root).expanduser()
    from scaffold.agent.host.models import host_root
    return host_root()


def scrub(text: str, env: Optional[dict] = None) -> str:
    """Remove secret-shaped strings and the value of any credential env var."""
    text = str(text or "")
    env = os.environ if env is None else env
    for name, val in env.items():
        if val and len(val) >= 8 and re.search(r"(_API_KEY|_TOKEN|_SECRET|PASSWORD)$", name):
            text = text.replace(val, "[redacted]")
    return _SECRET_SHAPES.sub("[redacted]", text)


def _one_line(text: str, limit: int = 200) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def applescript_quote(text: str) -> str:
    """A safe AppleScript string literal: backslashes and quotes escaped, no newlines."""
    text = _one_line(text, 240).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def macos_enabled() -> bool:
    return sys.platform == "darwin" and os.getenv("AWOS_NOTIFY_MACOS", "1").strip() not in ("0", "false", "no")


def _dedupe_sec() -> float:
    try:
        return float(os.getenv("AWOS_NOTIFY_DEDUPE_SEC", str(6 * 3600)))
    except ValueError:
        return 6 * 3600.0


@contextmanager
def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "notify.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _log(root: Path, line: str) -> None:
    try:
        with open(root / LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
    except OSError:
        pass


def send_macos(title: str, message: str, root: Path, run=subprocess.run) -> bool:
    script = (f"display notification {applescript_quote(message)} "
              f"with title {applescript_quote(title)}")
    cmd = ["osascript", "-e", script]
    _log(root, "osascript -e " + script)
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=OSASCRIPT_TIMEOUT_SEC)
        ok = getattr(proc, "returncode", 1) == 0
        if not ok:
            _log(root, f"osascript failed rc={getattr(proc, 'returncode', '?')}")
        return ok
    except subprocess.TimeoutExpired:
        _log(root, "osascript timed out")
    except (OSError, ValueError) as exc:
        _log(root, f"osascript error: {type(exc).__name__}")
    return False


def notify(event: str, job_id: str = "", summary: str = "", report: str = "",
           *, root=None, dedupe_key: Optional[str] = None, extra: Optional[dict] = None,
           run=subprocess.run) -> Optional[dict]:
    """
    Deliver one event ("done" | "failed" | "blocked" | "cancelled" | "continued" | ...).
    Returns the inbox record, or None when it was a duplicate. Never raises.
    """
    try:
        root = _root(root)
        summary = _one_line(scrub(summary))
        report = scrub(report)
        key = dedupe_key or f"{event}|{job_id}|{summary}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        now = time.time()
        with _locked(root):
            state_path = root / STATE
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    state = {}
            except (OSError, ValueError):
                state = {}
            window = _dedupe_sec()
            state = {k: t for k, t in state.items() if now - float(t) < window}
            if digest in state:
                _log(root, f"dedupe: {event} {job_id[:8]} (key {digest})")
                return None
            state[digest] = now
            record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event,
                      "job_id": job_id, "summary": summary, "report": report,
                      "key": digest}
            if extra:
                record.update({k: json.loads(scrub(json.dumps(v, default=str)))
                               for k, v in extra.items()})
            with open(root / INBOX, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, state_path)
        if macos_enabled():
            title = f"AWOS job {event}" + (f" ({job_id[:8]})" if job_id else "")
            record["macos"] = send_macos(title, summary or event, root, run=run)
        return record
    except Exception as exc:  # notification is best effort
        try:
            _log(_root(root), f"notify error: {type(exc).__name__}")
        except Exception:
            pass
        return None


def forget(dedupe_key: str, root=None) -> None:
    """Drop a dedupe key, so the next identical event is delivered again
    (e.g. once a credentials block is resolved, a new one must notify)."""
    try:
        root = _root(root)
        digest = hashlib.sha256(dedupe_key.encode()).hexdigest()[:16]
        with _locked(root):
            path = root / STATE
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.pop(digest, None) is not None:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(state), encoding="utf-8")
                os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def read_inbox(root=None, since: Optional[str] = None) -> list[dict]:
    path = _root(root) / INBOX
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if since is None or str(rec.get("at", "")) > since:
                out.append(rec)
    except OSError:
        pass
    return out
