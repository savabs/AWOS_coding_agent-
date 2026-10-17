"""
licence.py — AWOS Pro licence (Paddle subscription) and the free-tier gate.

    awos activate <key>      store a licence key and check it with the server
    awos licence status      plan, last check, jobs used this month

Free tier: FREE_JOBS_PER_MONTH cloud-model jobs per calendar month (UTC),
counted on this machine (AWOS_FREE_JOBS_PER_MONTH overrides). Local-model
jobs are unlimited and never counted. Pro is unlimited.

The licence lives in ~/.awos/licence.json (AWOS_HOME moves it, mode 0600):
    {"key", "active", "plan", "current_period_end", "grace_until",
     "checked_at", "last_ok_at"}
It is revalidated against <site>/api/license/check at most once a day. When the
server cannot be reached, a licence the server last called active keeps working
for OFFLINE_GRACE_DAYS after that last successful check.

The gate is ONE function, gate_job_start(), called by the host worker when a
job starts. It is disabled under pytest unless a test sets ENABLED_IN_TESTS.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    from scaffold.agent import paths as _paths
    from scaffold.agent import usage_ping as _ping
except ImportError:  # pragma: no cover - flat import when run from scaffold/agent
    import paths as _paths  # type: ignore
    import usage_ping as _ping  # type: ignore

FREE_JOBS_PER_MONTH = 25
REVALIDATE_SECONDS = 24 * 3600
OFFLINE_GRACE_DAYS = 14
CHECK_TIMEOUT_S = 5.0
KEY_RE = re.compile(r"^awos_[A-Z2-7]{32}$")

#: Tests flip this to exercise the gate.
ENABLED_IN_TESTS = False


def pro_url() -> str:
    return _paths.site_url("pro/")


def check_url() -> str:
    return os.getenv("AWOS_LICENCE_URL", "").strip() or _paths.site_url("api/license/check")


def licence_path() -> Path:
    return _paths.awos_home() / "licence.json"


def usage_path() -> Path:
    return _paths.awos_home() / "usage.json"


def free_jobs_per_month() -> int:
    raw = os.getenv("AWOS_FREE_JOBS_PER_MONTH", "").strip()
    try:
        return max(0, int(raw)) if raw else FREE_JOBS_PER_MONTH
    except ValueError:
        return FREE_JOBS_PER_MONTH


def normalize_key(key: str) -> str:
    key = (key or "").strip()
    if key.lower().startswith("awos_"):
        key = "awos_" + key[5:].upper()
    return key


def valid_key(key: str) -> bool:
    return bool(KEY_RE.match(key or ""))


def mask(key: Optional[str]) -> str:
    return f"{key[:9]}…{key[-4:]}" if key and len(key) > 13 else "-"


# ── files ────────────────────────────────────────────────────────────────────


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


# ── server check ─────────────────────────────────────────────────────────────

#: (url, payload, timeout) -> (http status or None, json dict or None).
HttpPost = Callable[[str, dict, float], "tuple[Optional[int], Optional[dict]]"]


def _http_post(url: str, payload: dict, timeout: float) -> "tuple[Optional[int], Optional[dict]]":
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "awos-licence/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoint
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except (ValueError, OSError):
            return exc.code, None
    except Exception:  # noqa: BLE001 - offline, DNS, TLS, timeout: all "unreachable"
        return None, None


def _install_id() -> Optional[str]:
    # Only the telemetry id if the user opted in; otherwise nothing identifying.
    return _ping.status().get("install_id")


def _ts(value) -> Optional[float]:
    """ISO-8601 or epoch seconds -> epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        v = str(value).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def revalidate(key: Optional[str] = None, *, http: Optional[HttpPost] = None,
               now: Optional[float] = None) -> dict:
    """Ask the server about the stored (or given) key; update the cache.
    Returns the cache dict plus "reachable": bool."""
    http = http or _http_post
    now = time.time() if now is None else now
    data = _read(licence_path())
    key = key or data.get("key")
    if not key:
        return {**data, "reachable": False}
    status, body = http(check_url(), {"key": key, "install_id": _install_id()}, CHECK_TIMEOUT_S)
    if status == 200 and isinstance(body, dict) and "active" in body:
        data = {"key": key, "active": bool(body.get("active")), "plan": body.get("plan"),
                "current_period_end": body.get("current_period_end"),
                "grace_until": body.get("grace_until"), "checked_at": now,
                "last_ok_at": now}
        _write(licence_path(), data)
        return {**data, "reachable": True}
    # Unreachable or odd answer: keep the last good state, retry in an hour.
    data = {**data, "key": key, "checked_at": now - REVALIDATE_SECONDS + 3600}
    _write(licence_path(), data)
    return {**data, "reachable": False}


def is_pro(*, http: Optional[HttpPost] = None, now: Optional[float] = None) -> bool:
    """Pro access right now. Revalidates at most daily; offline grace 14 days."""
    now = time.time() if now is None else now
    data = _read(licence_path())
    if not data.get("key"):
        return False
    checked = float(data.get("checked_at") or 0)
    if now - checked >= REVALIDATE_SECONDS:
        data = revalidate(data["key"], http=http, now=now)
    if not data.get("active"):
        return False
    last_ok = float(data.get("last_ok_at") or 0)
    return now - last_ok <= OFFLINE_GRACE_DAYS * 86400


# ── free-tier counter ────────────────────────────────────────────────────────


def _month(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m")


def jobs_used(now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    data = _read(usage_path())
    return len(data.get("jobs") or []) if data.get("month") == _month(now) else 0


def _record_job(job_id: str, now: float) -> int:
    data = _read(usage_path())
    month = _month(now)
    if data.get("month") != month:
        data = {"month": month, "jobs": []}
    jobs = list(data.get("jobs") or [])
    if job_id not in jobs:
        jobs.append(job_id)
    data["jobs"] = jobs
    _write(usage_path(), data)
    return len(jobs)


def gate_enabled() -> bool:
    under_pytest = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
    return ENABLED_IN_TESTS or not under_pytest


@dataclass
class Gate:
    allowed: bool
    message: str = ""
    used: int = 0
    limit: int = 0
    pro: bool = False
    counted: bool = False


def limit_message(used: int, limit: int, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    month = datetime.fromtimestamp(now, timezone.utc).strftime("%B")
    return (f"You have used your {limit} free cloud-model jobs for {month} ({used}/{limit}). "
            "Local-model jobs stay free and unlimited (AWOS_PROVIDER=local). "
            f"For unlimited cloud jobs, get AWOS Pro at {pro_url()} and run "
            "`awos activate <key>`, then `awos unblock <job id>`.")


def gate_job_start(job_id: str, provider: str, *, http: Optional[HttpPost] = None,
                   now: Optional[float] = None) -> Gate:
    """THE free-tier gate. Call once when a job starts (retries of the same
    job id are not counted twice). Never raises; on any internal error the
    job is allowed."""
    try:
        if not gate_enabled():
            return Gate(True)
        now = time.time() if now is None else now
        limit = free_jobs_per_month()
        if provider == "local":
            return Gate(True, used=jobs_used(now), limit=limit)
        data = _read(usage_path())
        if data.get("month") == _month(now) and job_id in (data.get("jobs") or []):
            return Gate(True, used=len(data["jobs"]), limit=limit)
        if is_pro(http=http, now=now):
            return Gate(True, used=jobs_used(now), limit=limit, pro=True)
        used = jobs_used(now)
        if used >= limit:
            return Gate(False, limit_message(used, limit, now), used=used, limit=limit)
        return Gate(True, used=_record_job(job_id, now), limit=limit, counted=True)
    except Exception:  # noqa: BLE001 - the gate must never break the worker
        return Gate(True)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _fmt_time(value) -> str:
    t = _ts(value)
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d") if t else "-"


def activate(key: str, *, http: Optional[HttpPost] = None) -> int:
    key = normalize_key(key)
    if not valid_key(key):
        print("That does not look like an AWOS licence key (awos_ followed by 32 letters/digits).")
        print(f"Your key is on the page you saw after checkout, or get one at {pro_url()}")
        return 2
    res = revalidate(key, http=http)
    if not res.get("reachable"):
        print("Saved the key, but could not reach the licence server to check it.")
        print("AWOS will check again on the next job. `awos licence status` shows the state.")
        return 1
    if res.get("active"):
        print(f"AWOS Pro is active ({res.get('plan') or 'pro'}). Cloud-model jobs are unlimited.")
        if res.get("current_period_end"):
            print(f"  renews/ends: {_fmt_time(res.get('current_period_end'))}")
        return 0
    print("The licence server does not know this key as active.")
    print(f"If you just paid, wait a minute and run this again. Help: {pro_url()}")
    return 1


def status_cli(*, http: Optional[HttpPost] = None) -> int:
    data = _read(licence_path())
    limit = free_jobs_per_month()
    if data.get("key"):
        pro = is_pro(http=http)
        data = _read(licence_path())
        print(f"Plan:        {'AWOS Pro' if pro else 'Free (licence not active)'}")
        print(f"Key:         {mask(data.get('key'))}")
        print(f"Period end:  {_fmt_time(data.get('current_period_end'))}")
        if data.get("grace_until"):
            print(f"Grace until: {_fmt_time(data.get('grace_until'))}")
        print(f"Last check:  {_fmt_time(data.get('last_ok_at'))} (offline grace {OFFLINE_GRACE_DAYS} days)")
        if pro:
            return 0
    else:
        print("Plan:        Free")
    print(f"Cloud jobs:  {jobs_used()}/{limit} this month (local-model jobs are unlimited)")
    print(f"Upgrade:     {pro_url()}")
    return 0
