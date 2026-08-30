"""
observer_control.py — Local observer control helper.

Provides token generation/validation and audit logging for the local control API.
Non-destructive by design.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def generate_token(path: str = ".awos/observer_token", ttl_seconds: int = 3600) -> Dict[str, Any]:
    p = Path(path)
    _ensure_dir(p)
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    obj = {"token": token, "expires_at": expires}
    p.write_text(json.dumps(obj), encoding="utf-8")
    return obj


def load_token(path: str = ".awos/observer_token") -> Optional[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def validate_token(header_token: str | None, path: str = ".awos/observer_token") -> bool:
    if not header_token:
        return False
    obj = load_token(path)
    if not obj:
        return False
    try:
        if obj.get("token") != header_token:
            return False
        expires = datetime.fromisoformat(obj.get("expires_at"))
        return datetime.now(timezone.utc) < expires
    except Exception:
        return False


def append_audit(entry: Dict[str, Any], log_dir: str = ".awos/control_log") -> str:
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    audit_path = d / f"control_{ts}.jsonl"
    audit_path.write_text(json.dumps(entry, default=str) + "\n", encoding="utf-8")
    return str(audit_path)


def read_audit_lines(log_dir: str = ".awos/control_log", limit: int = 100) -> list[Dict[str, Any]]:
    d = Path(log_dir)
    if not d.exists():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:limit]:
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    out.append(json.loads(line))
        except Exception:
            continue
    return out
