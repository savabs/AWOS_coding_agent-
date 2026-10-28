"""
usage_ping.py — opt-in, anonymous usage counts (off unless the user says yes).

What is sent (and nothing else; see build_payload and docs at /telemetry/):

    {"v": 1, "event": "install" | "first_run" | "job_finished",
     "install_id": "<random uuid4, made when you opt in>",
     "awos_version": "0.2.2", "os": "darwin" | "linux" | "windows" | "other",
     # job_finished (and provider on first_run):
     "outcome": "solved" | "failed" | "cancelled",
     "duration": "lt1m" | "1to5m" | "5to15m" | "15to60m" | "gt60m",
     "cost": "zero" | "lt1c" | "1to10c" | "10cto1" | "gt1",
     "provider": "anthropic" | "local" | "other"}

Never sent: code, prompts, goals, repo names, paths, branch names, keys,
hostnames, user names. The install id is uuid4 (random), not derived from
anything on the machine.

Consent lives in ~/.awos/telemetry.json (AWOS_HOME moves it):
    {"enabled": bool, "asked_at": iso, "install_id": uuid, "first_run_sent": bool}

Off when: no recorded "yes"; AWOS_TELEMETRY=0/false/off/no; DO_NOT_TRACK=1;
running under pytest (unless a test sets ALLOW_IN_TESTS). Sending is
fire-and-forget on a daemon thread with a 2 s timeout; nothing here raises
or blocks the caller.

    awos telemetry on | off | status
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    from scaffold.agent import paths as _paths
except ImportError:  # pragma: no cover - flat import when run from scaffold/agent
    import paths as _paths  # type: ignore

SCHEMA_VERSION = 1
TIMEOUT_S = 2.0
EVENTS = ("install", "first_run", "job_finished")
OUTCOMES = {"done": "solved", "solved": "solved", "failed": "failed",
            "cancelled": "cancelled", "canceled": "cancelled"}
PROVIDERS = ("anthropic", "local", "other")
OS_NAMES = ("darwin", "linux", "windows", "other")
DURATION_BUCKETS = ("lt1m", "1to5m", "5to15m", "15to60m", "gt60m")
COST_BUCKETS = ("zero", "lt1c", "1to10c", "10cto1", "gt1")
#: The only keys a payload may carry; build_payload drops everything else.
ALLOWED_FIELDS = ("v", "event", "install_id", "awos_version", "os",
                  "outcome", "duration", "cost", "provider")

#: Tests flip this to exercise the real code path.
ALLOW_IN_TESTS = False

_FALSY = ("0", "false", "no", "off")
_TRUTHY = ("1", "true", "yes", "on")


# ── where things live ────────────────────────────────────────────────────────


def consent_path() -> Path:
    return _paths.awos_home() / "telemetry.json"


def endpoint() -> str:
    return (os.getenv("AWOS_TELEMETRY_URL", "").strip() or _paths.site_url("api/ping"))


def _load() -> dict:
    try:
        data = json.loads(consent_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ── switches ─────────────────────────────────────────────────────────────────


def env_disabled(env: Optional[dict] = None) -> Optional[str]:
    """The reason the environment turns telemetry off, or None."""
    env = os.environ if env is None else env
    if (env.get("AWOS_TELEMETRY") or "").strip().lower() in _FALSY:
        return "AWOS_TELEMETRY=0"
    if (env.get("DO_NOT_TRACK") or "").strip().lower() in _TRUTHY:
        return "DO_NOT_TRACK=1"
    return None


def under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def _blocked() -> Optional[str]:
    reason = env_disabled()
    if reason:
        return reason
    if under_pytest() and not ALLOW_IN_TESTS:
        return "running under pytest"
    return None


def consent() -> Optional[bool]:
    """True/False once the user answered; None when never asked."""
    val = _load().get("enabled")
    return val if isinstance(val, bool) else None


def enabled() -> bool:
    return _blocked() is None and consent() is True


def set_consent(on: bool) -> dict:
    data = _load()
    data["enabled"] = bool(on)
    data["asked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if on and not _valid_uuid4(data.get("install_id")):
        data["install_id"] = str(uuid.uuid4())
    if not on:
        # Opting out forgets the id; opting in again makes a new one.
        data.pop("install_id", None)
    _save(data)
    return data


def status() -> dict:
    data = _load()
    blocked = env_disabled()
    return {"consent": consent(), "enabled": enabled(), "env_override": blocked,
            "install_id": data.get("install_id") if consent() else None,
            "endpoint": endpoint(), "file": str(consent_path())}


# ── payload ──────────────────────────────────────────────────────────────────


def _valid_uuid4(val) -> bool:
    try:
        return isinstance(val, str) and uuid.UUID(val).version == 4 and str(uuid.UUID(val)) == val
    except (ValueError, AttributeError):
        return False


def awos_version() -> str:
    try:
        from importlib.metadata import version
        return version("awos")[:24]
    except Exception:  # noqa: BLE001 - checkout without an installed dist
        pass
    try:
        text = (_paths.CODE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')[:24]
    except OSError:
        pass
    return "unknown"


def os_name() -> str:
    s = platform.system().lower()
    return s if s in ("darwin", "linux", "windows") else "other"


def duration_bucket(seconds: Optional[float]) -> Optional[str]:
    if seconds is None or seconds < 0:
        return None
    m = seconds / 60.0
    if m < 1:
        return "lt1m"
    if m < 5:
        return "1to5m"
    if m < 15:
        return "5to15m"
    if m < 60:
        return "15to60m"
    return "gt60m"


def cost_bucket(usd: Optional[float]) -> Optional[str]:
    try:
        usd = float(usd) if usd is not None else None
    except (TypeError, ValueError):
        return None
    if usd is None or usd < 0:
        return None
    if usd == 0:
        return "zero"
    if usd < 0.01:
        return "lt1c"
    if usd < 0.10:
        return "1to10c"
    if usd <= 1.0:
        return "10cto1"
    return "gt1"


def provider_kind(env: Optional[dict] = None, privacy: Optional[str] = None) -> str:
    """anthropic | local | other, from the configuration only (no network)."""
    env = os.environ if env is None else env
    if privacy == "local_only":
        return "local"
    if (env.get("AWOS_PROVIDER") or "").strip().lower() == "local" \
            or (env.get("AWOS_AGENT_MODEL") or "").strip().lower().startswith("local/"):
        return "local"
    model = (env.get("AWOS_AGENT_MODEL") or "").strip().lower()
    if model.startswith(("claude", "anthropic/")):
        return "anthropic"
    if not model and (env.get("ANTHROPIC_API_KEY") or "").strip():
        return "anthropic"
    return "other"


def build_payload(event: str, install_id: str, **fields) -> Optional[dict]:
    """The exact dict sent, or None when the event is not one we send.
    Only whitelisted keys with whitelisted values survive."""
    if event not in EVENTS or not _valid_uuid4(install_id):
        return None
    out = {"v": SCHEMA_VERSION, "event": event, "install_id": install_id,
           "awos_version": str(fields.get("awos_version") or awos_version())[:24],
           "os": fields.get("os") if fields.get("os") in OS_NAMES else os_name()}
    if event in ("job_finished", "first_run"):
        prov = fields.get("provider")
        if prov in PROVIDERS:
            out["provider"] = prov
    if event == "job_finished":
        outcome = OUTCOMES.get(str(fields.get("outcome") or ""))
        if outcome:
            out["outcome"] = outcome
        if fields.get("duration") in DURATION_BUCKETS:
            out["duration"] = fields["duration"]
        if fields.get("cost") in COST_BUCKETS:
            out["cost"] = fields["cost"]
    assert set(out) <= set(ALLOWED_FIELDS)
    return out


# ── sending ──────────────────────────────────────────────────────────────────


def _post(url: str, payload: dict, timeout: float = TIMEOUT_S) -> bool:
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "awos-ping/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoint
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 - telemetry never raises
        return False


#: Swappable transport (tests replace it): (url, payload, timeout) -> bool.
transport: Callable[[str, dict, float], bool] = _post


def send(event: str, wait: bool = False, **fields) -> bool:
    """Queue one ping. Returns True when a ping was handed to the transport
    (or, with wait=True, when it was accepted). Never raises."""
    try:
        if not enabled():
            return False
        payload = build_payload(event, _load().get("install_id"), **fields)
        if payload is None:
            return False
        url = endpoint()
        if wait:
            return bool(transport(url, payload, TIMEOUT_S))
        threading.Thread(target=transport, args=(url, payload, TIMEOUT_S),
                         name="awos-usage-ping", daemon=True).start()
        return True
    except Exception:  # noqa: BLE001
        return False


def _seconds_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    try:
        a = datetime.fromisoformat(start) if start else None
        b = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        if a is None:
            return None
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        if b.tzinfo is None:
            b = b.replace(tzinfo=timezone.utc)
        return (b - a).total_seconds()
    except (TypeError, ValueError):
        return None


def job_finished(job, state: str, result: Optional[dict] = None,
                 spent: Optional[float] = None) -> bool:
    """Hook for the host worker: one ping per finished job (no job content)."""
    try:
        if not enabled():
            return False
        result = result or {}
        try:
            cost = max(float(result.get("cost_usd") or 0), float(spent or 0))
        except (TypeError, ValueError):
            cost = None
        prov = provider_kind(privacy=getattr(job, "privacy", None))
        secs = _seconds_between(getattr(job, "started_at", None) or getattr(job, "created_at", None),
                                getattr(job, "finished_at", None))
        data = _load()
        if not data.get("first_run_sent"):
            data["first_run_sent"] = True
            _save(data)
            send("first_run", provider=prov)
        return send("job_finished", outcome=state, duration=duration_bucket(secs),
                    cost=cost_bucket(cost), provider=prov)
    except Exception:  # noqa: BLE001
        return False


# ── consent prompt (onboarding) ──────────────────────────────────────────────


def maybe_ask_consent(ask: Callable[[str], str], say: Callable[[str], None] = print,
                      interactive: bool = True) -> Optional[bool]:
    """Ask once. Non-interactive or env-disabled: never asks, stays off."""
    try:
        current = consent()
        if current is not None or not interactive or _blocked():
            return current
        say("\nHelp improve AWOS? Send anonymous counts (job solved/failed, rough duration "
            "and cost bucket, AWOS version, OS). Never code, prompts, goals, repo names or paths.")
        say(f"  Details: {_paths.site_url('telemetry/')}  (change any time: awos telemetry on|off)")
        try:
            answer = (ask("Send anonymous usage counts? [y/N] ") or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            say("")
            return None
        on = answer in ("y", "yes")
        set_consent(on)
        say("Thanks, usage counts are on." if on else "OK, nothing will be sent.")
        if on:
            send("install")
        return on
    except Exception:  # noqa: BLE001 - never breaks onboarding
        return None


def cli(action: str) -> int:
    """awos telemetry on|off|status"""
    if action == "on":
        set_consent(True)
        st = status()
        print("Anonymous usage counts: on.")
        if st["env_override"]:
            print(f"  (but {st['env_override']} is set, so nothing is sent)")
        else:
            send("install")
        print(f"  What is sent: {_paths.site_url('telemetry/')}")
        return 0
    if action == "off":
        set_consent(False)
        print("Anonymous usage counts: off. Nothing will be sent.")
        return 0
    st = status()
    word = {True: "on", False: "off", None: "off (never asked)"}[st["consent"]]
    print(f"Anonymous usage counts: {word}")
    if st["env_override"]:
        print(f"  disabled by the environment: {st['env_override']}")
    if st["install_id"]:
        print(f"  install id: {st['install_id']} (random)")
    print(f"  endpoint:   {st['endpoint']}")
    print(f"  settings:   {st['file']}")
    print(f"  details:    {_paths.site_url('telemetry/')}")
    return 0
