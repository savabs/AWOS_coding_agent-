"""Host watchdog: detect the failures that killed this week's long runs.

Spec: docs/specs/host_ops.md

Failure            Detection                                  Action
-----------------  -----------------------------------------  ------------------------------
laptop sleep/crash heartbeat file older than max_age          report STALE + restart advice
revoked key        backend error classified "auth" (401/403)  write PAUSED flag + notify
out of credit      backend error classified "billing" (402)   write PAUSED flag + notify
DNS / network down resolver/connect failure or network error  exponential backoff, no pause
battery            `pmset -g batt` on battery / low percent   warn; pause below critical

Nothing here talks to a model or a paid API. Every probe (clock, pmset output,
DNS resolver) is injectable so the tests and the live proof run hermetically.

State lives under the host root (AWOS_HOST_DIR, default ~/.awos/host):
  heartbeat            JSON {ts, pid, note}; written by the worker loop
  PAUSED               JSON {reason, kind, ts, reasons[]}; present => queue must not
                       start jobs. A new pause never erases an earlier reason: the
                       most severe one stays primary, all are kept in `reasons`.
  network_backoff.json JSON {failures, next_retry_at, delay_s, source}
  notify_state.json    last watchdog notification signature (dedupe/rate limit)
  notifications.jsonl  one JSON line per notification
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

DEFAULT_HEARTBEAT_MAX_AGE_S = 300.0
BACKOFF_BASE_S = 30.0
BACKOFF_CAP_S = 1800.0
BATTERY_WARN_PCT = 30
BATTERY_CRITICAL_PCT = 15
# `check --notify` repeats an unchanged alert at most this often.
NOTIFY_REPEAT_S = 6 * 3600.0
# Higher = more severe. A new pause only becomes primary if strictly more severe.
PAUSE_SEVERITY = {"auth": 3, "billing": 3, "manual": 2, "battery": 1}


# --------------------------------------------------------------------------- paths

def host_root(root: Optional[str | os.PathLike] = None) -> Path:
    """Storage root: explicit arg > AWOS_HOST_DIR > ~/.awos/host."""
    if root:
        return Path(root).expanduser()
    env = os.environ.get("AWOS_HOST_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".awos" / "host"


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True))
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@contextlib.contextmanager
def _locked(path: Path):
    """Exclusive advisory lock for a read-modify-write shared by the watchdog
    and worker processes (no-op where fcntl is unavailable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    with open(path.with_name(path.name + ".lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@dataclass
class Finding:
    check: str            # heartbeat | backend | network | power | pause
    status: str           # ok | warn | stale | missing | paused | backoff | error
    detail: str
    action: str = ""
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# --------------------------------------------------------------------------- heartbeat

def write_heartbeat(root=None, note: str = "", now: Optional[float] = None,
                    pid: Optional[int] = None) -> Path:
    """Called by the worker loop on every tick (and between job steps)."""
    path = host_root(root) / "heartbeat"
    _write_json_atomic(path, {
        "ts": time.time() if now is None else now,
        "pid": os.getpid() if pid is None else pid,
        "note": note,
    })
    return path


def check_heartbeat(root=None, max_age_s: float = DEFAULT_HEARTBEAT_MAX_AGE_S,
                    now: Optional[float] = None) -> Finding:
    path = host_root(root) / "heartbeat"
    now = time.time() if now is None else now
    if not path.exists():
        return Finding("heartbeat", "missing", f"no heartbeat at {path}",
                       action=_restart_advice())
    hb = _read_json(path)
    if hb is None or not isinstance(hb.get("ts"), (int, float)):
        return Finding("heartbeat", "stale", f"unreadable heartbeat at {path}",
                       action=_restart_advice())
    age = now - float(hb["ts"])
    data = {"age_s": round(age, 1), "pid": hb.get("pid"), "note": hb.get("note", "")}
    if age > max_age_s:
        return Finding(
            "heartbeat", "stale",
            f"heartbeat is {age:.0f}s old (max {max_age_s:.0f}s); last note: "
            f"{hb.get('note', '') or '-'} — likely sleep, crash or hang",
            action=_restart_advice(), data=data)
    return Finding("heartbeat", "ok", f"heartbeat {age:.0f}s old", data=data)


def _restart_advice() -> str:
    uid = os.getuid() if hasattr(os, "getuid") else "$UID"
    return (f"launchctl kickstart -k gui/{uid}/ai.awos.host   "
            "# restarts the worker; running jobs are re-queued from their journal")


# --------------------------------------------------------------------------- backend errors

_AUTH = ("error code: 401", "status 401", "http 401", "401 unauthorized",
         "unauthorized", "error code: 403", "forbidden", "incorrect api key",
         "invalid api key", "invalid x-api-key", "no auth credentials",
         "user not found", "key revoked", "api key revoked")
_BILLING = ("error code: 402", "payment required", "credit balance is too low",
            "insufficient_quota", "insufficient balance", "insufficient credits")
_NETWORK = ("nodename nor servname", "name or service not known",
            "temporary failure in name resolution", "getaddrinfo failed",
            "failed to resolve", "connection refused", "connection reset",
            "network is unreachable", "no route to host", "connecterror",
            "apiconnectionerror", "connection error", "timed out", "read timeout",
            "remotedisconnected")
_RATE = ("error code: 429", "rate limit", "too many requests")
# OpenRouter (and others) answer 403 when moderation flags one request's input.
# That is a problem with the job's content, not the key: never pause the queue.
_CONTENT = ("moderation", "flagged", "content policy", "content_policy",
            "content management policy", "safety system")


def classify_backend_error(error: object, status: Optional[int] = None) -> str:
    """Map a backend error (exception, text or HTTP status) to a failure class.

    Returns one of: auth, billing, network, rate_limit, content, other.
    A 403 whose text says moderation/flagged is "content" (job-level), not auth.
    """
    if status == 401:
        return "auth"
    if status == 402:
        return "billing"
    if status == 429:
        return "rate_limit"
    if isinstance(error, (socket.gaierror, ConnectionError, TimeoutError)):
        return "network"
    text = f" {type(error).__name__}: {error} ".lower() if isinstance(error, BaseException) \
        else f" {error} ".lower()
    if any(p in text for p in _CONTENT):
        return "content"
    if status == 403:
        return "auth"
    for cls, pats in (("auth", _AUTH), ("billing", _BILLING),
                      ("rate_limit", _RATE), ("network", _NETWORK)):
        if any(p in text for p in pats):
            return cls
    return "other"


def handle_backend_error(error: object, root=None, status: Optional[int] = None,
                         job_id: str = "", now: Optional[float] = None) -> Finding:
    """Decide what the host does about one backend failure.

    auth/billing -> pause the whole queue (every further job would fail the
    same way and burn a retry) and notify. network -> backoff. Others are left
    to the job's own retry policy.
    """
    cls = classify_backend_error(error, status)
    msg = _redact(str(error))[:300]
    if cls in ("auth", "billing"):
        reason = ("backend rejected the API key (revoked/invalid)" if cls == "auth"
                  else "backend reports no credit")
        pause_queue(root, reason=f"{reason}: {msg}", kind=cls, job_id=job_id, now=now)
        notify(root, title=f"AWOS host paused ({cls})",
               message=f"{reason}. Fix the key/credit, then run: "
                       f"python -m scaffold.agent.host.watchdog resume", now=now)
        return Finding("backend", "paused", f"{cls}: {msg}",
                       action="queue paused; fix credentials then `watchdog resume`",
                       data={"class": cls, "job_id": job_id})
    if cls == "network":
        st = record_network_failure(root, now=now)
        return Finding("backend", "backoff", f"network: {msg}",
                       action=f"retry after {st['delay_s']:.0f}s",
                       data={"class": cls, **st})
    if cls == "content":
        return Finding("backend", "error", f"content rejected: {msg}",
                       action="job-level failure (moderation); queue keeps running",
                       data={"class": cls, "job_id": job_id})
    if cls == "rate_limit":
        return Finding("backend", "warn", f"rate limited: {msg}",
                       action="job-level retry with provider backoff",
                       data={"class": cls})
    return Finding("backend", "error", msg, action="job-level failure handling",
                   data={"class": cls})


_REDACTIONS = (
    # Authorization: Bearer <token>  (any token shape)
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1…"),
    # JWTs
    (re.compile(r"eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "eyJ…"),
    # OpenAI / OpenRouter / Anthropic style
    (re.compile(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+"), r"\1…"),
    # Google API keys, HuggingFace, GitHub, Groq, xAI
    (re.compile(r"(AIza)[A-Za-z0-9_\-]{20,}"), r"\1…"),
    (re.compile(r"(hf_)[A-Za-z0-9]{10,}"), r"\1…"),
    (re.compile(r"(gh[pousr]_)[A-Za-z0-9]{10,}"), r"\1…"),
    (re.compile(r"(gsk_|xai-)[A-Za-z0-9]{10,}"), r"\1…"),
    # key=..., api_key: "...", x-api-key: ..., token=..., secret=...
    (re.compile(r"(?i)((?:api[_-]?key|x-api-key|token|secret)[\"']?\s*[:=]\s*[\"']?)"
                r"[A-Za-z0-9._~+/\-]{8,}"), r"\1…"),
)


def _redact(text: str) -> str:
    """Never echo anything that looks like a key into logs/notifications."""
    for pat, sub in _REDACTIONS:
        text = pat.sub(sub, text)
    return text


# --------------------------------------------------------------------------- pause flag

def pause_queue(root=None, reason: str = "", kind: str = "manual", job_id: str = "",
                now: Optional[float] = None) -> Path:
    """Write/extend the PAUSED flag. Never erases an earlier reason.

    The most severe reason (auth/billing > manual > battery; ties keep the
    earlier one) stays primary in reason/kind/job_id; every distinct kind is
    kept in `reasons`, so `resume` after plugging in still shows a revoked key.
    """
    path = host_root(root) / "PAUSED"
    new = {"reason": reason, "kind": kind, "job_id": job_id,
           "ts": time.time() if now is None else now}
    keys = ("reason", "kind", "job_id", "ts")
    with _locked(path):
        old = _read_json(path) if path.exists() else None
        if not old:
            _write_json_atomic(path, {**new, "reasons": [dict(new)]})
            return path
        reasons = old.get("reasons")
        if not isinstance(reasons, list):
            reasons = [{k: old.get(k) for k in keys}]
        if any(isinstance(r, dict) and r.get("kind") == kind for r in reasons):
            return path                  # same cause already recorded
        reasons.append(dict(new))

        def sev(k):
            return PAUSE_SEVERITY.get(str(k), 2)
        primary = new if sev(kind) > sev(old.get("kind")) else \
            {k: old.get(k) for k in keys}
        _write_json_atomic(path, {**primary, "reasons": reasons})
    return path


def is_paused(root=None) -> Optional[dict]:
    path = host_root(root) / "PAUSED"
    if not path.exists():
        return None
    return _read_json(path) or {"reason": "unreadable PAUSED file", "kind": "unknown"}


def describe_pause(p: dict) -> str:
    """'kind: reason' for the primary cause, plus any other recorded causes."""
    text = f"{p.get('kind')}: {p.get('reason')}"
    others = [r for r in p.get("reasons", []) if isinstance(r, dict)
              and r.get("kind") != p.get("kind")]
    if others:
        text += " (also: " + "; ".join(f"{r.get('kind')}: {r.get('reason')}"
                                       for r in others) + ")"
    return text


def resume_queue(root=None) -> bool:
    path = host_root(root) / "PAUSED"
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


# --------------------------------------------------------------------------- notify

def notify(root=None, title: str = "", message: str = "",
           now: Optional[float] = None) -> dict:
    """Append to notifications.jsonl. Desktop banner only if AWOS_HOST_NOTIFY=osascript."""
    rec = {"ts": time.time() if now is None else now, "title": title,
           "message": _redact(message)}
    path = host_root(root) / "notifications.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    if os.environ.get("AWOS_HOST_NOTIFY") == "osascript" and sys.platform == "darwin":
        script = f'display notification {json.dumps(rec["message"])} with title {json.dumps(title)}'
        try:
            subprocess.run(["osascript", "-e", script], timeout=5, check=False,
                           capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass
    return rec


def notify_once(root=None, key: str = "", title: str = "", message: str = "",
                now: Optional[float] = None,
                repeat_s: float = NOTIFY_REPEAT_S) -> Optional[dict]:
    """notify() unless the same `key` was already sent less than repeat_s ago.

    Used by `check --notify` (runs every 5 min) so an unchanged PAUSED or
    missing-heartbeat state yields one notification, not one per run.
    """
    now = time.time() if now is None else now
    path = host_root(root) / "notify_state.json"
    with _locked(path):
        st = _read_json(path) or {}
        if st.get("key") == key and now - float(st.get("ts", 0)) < repeat_s:
            return None
        _write_json_atomic(path, {"key": key, "ts": now})
    return notify(root, title=title, message=message, now=now)


def clear_notify_state(root=None) -> None:
    try:
        (host_root(root) / "notify_state.json").unlink()
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- network

def backoff_delay(failures: int, base: float = BACKOFF_BASE_S,
                  cap: float = BACKOFF_CAP_S) -> float:
    if failures <= 0:
        return 0.0
    return min(cap, base * (2 ** (failures - 1)))


def record_network_failure(root=None, now: Optional[float] = None,
                           source: str = "api") -> dict:
    """Bump the shared backoff (locked: watchdog and worker both write it).

    `source` is "api" for a real backend connect/timeout/reset failure and
    "dns" for the watchdog's resolve-only probe. Once any API failure is
    recorded the window stays "api" until the API itself succeeds.
    """
    path = host_root(root) / "network_backoff.json"
    now = time.time() if now is None else now
    with _locked(path):
        st = _read_json(path) or {}
        failures = int(st.get("failures", 0)) + 1
        delay = backoff_delay(failures)
        src = "api" if "api" in (source, st.get("source")) else source
        st = {"failures": failures, "delay_s": delay, "next_retry_at": now + delay,
              "source": src}
        _write_json_atomic(path, st)
    return st


def record_network_ok(root=None, source: Optional[str] = None) -> None:
    """Clear the backoff. With source="dns" (the watchdog probe) only a
    DNS-recorded backoff is cleared: a resolving hostname proves nothing about
    an API connect/timeout failure the worker recorded."""
    path = host_root(root) / "network_backoff.json"
    with _locked(path):
        if source is not None:
            st = _read_json(path)
            if st and st.get("source", "api") != source:
                return
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def network_ready(root=None, now: Optional[float] = None) -> bool:
    """False while inside a backoff window — the worker should not start a job."""
    st = _read_json(host_root(root) / "network_backoff.json")
    if not st:
        return True
    now = time.time() if now is None else now
    return now >= float(st.get("next_retry_at", 0))


def check_network(root=None, host: str = "openrouter.ai", port: int = 443,
                  resolver: Callable = socket.getaddrinfo,
                  now: Optional[float] = None) -> Finding:
    """DNS resolve only (no request to the API, no key used)."""
    try:
        resolver(host, port)
    except (OSError, socket.gaierror) as exc:
        st = record_network_failure(root, now=now, source="dns")
        return Finding("network", "backoff", f"cannot resolve {host}: {exc}",
                       action=f"next retry in {st['delay_s']:.0f}s", data=st)
    record_network_ok(root, source="dns")
    if not network_ready(root, now=now):
        st = _read_json(host_root(root) / "network_backoff.json") or {}
        return Finding("network", "backoff",
                       f"{host} resolves, but the API backoff window is still open",
                       action=f"retry at {float(st.get('next_retry_at', 0)):.0f}",
                       data=st)
    return Finding("network", "ok", f"{host} resolves")


# --------------------------------------------------------------------------- power

def parse_pmset_batt(text: str) -> dict:
    """Parse `pmset -g batt` output.

    Example:
      Now drawing from 'Battery Power'
       -InternalBattery-0 (id=1)	54%; discharging; 3:12 remaining present: true
    """
    import re
    out: dict = {"source": "unknown", "percent": None, "state": None}
    m = re.search(r"drawing from '([^']+)'", text)
    if m:
        src = m.group(1)
        out["source"] = "AC" if "AC" in src else ("Battery" if "Battery" in src else src)
    m = re.search(r"(\d+)%;\s*([a-zA-Z ]+?);", text)
    if m:
        out["percent"] = int(m.group(1))
        out["state"] = m.group(2).strip()
    return out


def check_power(pmset_text: Optional[str] = None, root=None,
                runner: Optional[Callable[[], str]] = None,
                pause_on_critical: bool = True) -> Finding:
    if pmset_text is None:
        runner = runner or _run_pmset_batt
        try:
            pmset_text = runner()
        except (OSError, subprocess.SubprocessError) as exc:
            return Finding("power", "ok", f"pmset unavailable ({exc}); assuming AC")
    st = parse_pmset_batt(pmset_text)
    pct = st["percent"]
    if st["source"] != "Battery":
        return Finding("power", "ok", f"on {st['source']} power"
                       + (f" ({pct}%)" if pct is not None else ""), data=st)
    if pct is not None and pct <= BATTERY_CRITICAL_PCT:
        if pause_on_critical:
            prev = is_paused(root) or {}
            already = any(isinstance(r, dict) and r.get("kind") == "battery"
                          for r in prev.get("reasons", [prev]))
            pause_queue(root, reason=f"battery critical ({pct}%)", kind="battery")
            if not already:
                notify(root, "AWOS host paused (battery)",
                       f"On battery at {pct}%. Plug in, then `watchdog resume`.")
        return Finding("power", "paused" if pause_on_critical else "warn",
                       f"on battery at {pct}%", action="plug in AC power", data=st)
    return Finding("power", "warn", f"on battery at {pct}%",
                   action="plug in AC power; overnight runs die on battery", data=st)


def _run_pmset_batt() -> str:
    return subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                          timeout=5, check=False).stdout


# --------------------------------------------------------------------------- all checks

def run_checks(root=None, max_age_s: float = DEFAULT_HEARTBEAT_MAX_AGE_S,
               now: Optional[float] = None, pmset_text: Optional[str] = None,
               resolver: Callable = socket.getaddrinfo,
               skip: Iterable[str] = ()) -> list[Finding]:
    skip = set(skip)
    out: list[Finding] = []
    if "heartbeat" not in skip:
        out.append(check_heartbeat(root, max_age_s=max_age_s, now=now))
    p = is_paused(root)
    if p:
        out.append(Finding("pause", "paused", describe_pause(p),
                           action="fix cause, then `watchdog resume`", data=p))
    if "network" not in skip:
        out.append(check_network(root, resolver=resolver, now=now))
    if "power" not in skip:
        out.append(check_power(pmset_text, root=root))
    return out


def _print(findings: list[Finding], as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
        return
    for f in findings:
        print(f"[{f.status.upper():7}] {f.check:9} {f.detail}")
        if f.action:
            print(f"          -> {f.action}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.agent.host.watchdog",
                                 description="AWOS host watchdog (no model/API calls).")
    ap.add_argument("--root", help="host dir (default: $AWOS_HOST_DIR or ~/.awos/host)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="run all checks; exit 1 if any is not ok")
    c.add_argument("--max-age", type=float, default=DEFAULT_HEARTBEAT_MAX_AGE_S)
    c.add_argument("--json", action="store_true")
    c.add_argument("--skip", action="append", default=[],
                   choices=["heartbeat", "network", "power"])
    c.add_argument("--notify", action="store_true",
                   help="write a notification when something is not ok")
    b = sub.add_parser("beat", help="write a heartbeat now")
    b.add_argument("--note", default="")
    e = sub.add_parser("classify", help="classify a backend error string")
    e.add_argument("text")
    e.add_argument("--status", type=int)
    e.add_argument("--apply", action="store_true",
                   help="also act on it (pause/backoff) — used for simulation")
    sub.add_parser("resume", help="clear the PAUSED flag")
    sub.add_parser("status", help="print pause/backoff state")
    a = ap.parse_args(argv)
    root = a.root

    if a.cmd == "beat":
        print(write_heartbeat(root, note=a.note))
        return 0
    if a.cmd == "resume":
        p = is_paused(root)
        if p:
            print(f"clearing pause: {describe_pause(p)}")
        print("resumed" if resume_queue(root) else "was not paused")
        clear_notify_state(root)
        return 0
    if a.cmd == "status":
        print(json.dumps({"paused": is_paused(root), "network_ready": network_ready(root)},
                         indent=2))
        return 0
    if a.cmd == "classify":
        if a.apply:
            f = handle_backend_error(a.text, root=root, status=a.status)
            _print([f], False)
            return 0 if f.ok else 1
        print(classify_backend_error(a.text, a.status))
        return 0
    findings = run_checks(root, max_age_s=a.max_age, skip=a.skip)
    _print(findings, a.json)
    bad = [f for f in findings if f.status not in ("ok", "warn")]
    if bad and a.notify:
        # dedupe on (check, status) so a changing age/percent is still "the same
        # alert"; repeat at most every NOTIFY_REPEAT_S while it persists.
        key = "|".join(sorted(f"{f.check}:{f.status}" for f in bad))
        notify_once(root, key, "AWOS host watchdog",
                    "; ".join(f"{f.check}: {f.detail}" for f in bad))
    elif not bad:
        clear_notify_state(root)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
