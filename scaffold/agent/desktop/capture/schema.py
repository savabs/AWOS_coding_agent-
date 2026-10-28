# SPDX-License-Identifier: MIT
# Portions adapted from openadapt-capture, openadapt_capture/structural.py
# Copyright (c) 2025-2026 OpenAdapt.AI. MIT License: see ./NOTICE.
# AWOS changes: pydantic models replaced by plain dicts + stdlib validation
# (no pydantic dependency); trimmed to the fields the demo trace needs; added
# sanitize_element(), the ordinal field and the native-event record schema.
"""
schema.py — the structural AX element schema and secure-value omission.

Every element that leaves the capture layer is a plain dict:

    {"role": "AXTextField", "subrole": "AXSecureTextField", "name": "Password",
     "identifier": "pw", "ordinal": 1, "value": "..."}      # value only if not secure

Rules (enforced by sanitize_element, applied at the source AND again in the
adapter before anything is logged or fed to trace.Recorder):
  * a secure / password element never carries a value (the keys "value",
    "value_length" and "selected_text" are dropped);
  * text fields are capped at MAX_TEXT_LENGTH characters;
  * absent attributes stay absent (never guessed from pixels or neighbours).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

SCHEMA_VERSION = "awos.capture.element/1"
EVENTS_SCHEMA = "awos.capture_events/1"
MAX_TEXT_LENGTH = 512
MAX_ANCESTRY_DEPTH = 32
MAX_TREE_NODES = 1024
ELEMENT_KEYS = ("role", "subrole", "name", "identifier", "ordinal", "value")
EDITABLE_ROLES = frozenset({"AXTextField", "AXTextArea", "AXSecureTextField", "AXComboBox", "AXSearchField"})
VALUE_KEYS = ("value", "value_length", "selected_text")

# --- verbatim from openadapt_capture/structural.py (MIT) -------------------
_SECRET_VALUE_ROLE_KEYS = frozenset(
    {
        "password",
        "passwordbox",
        "passwordtext",
        "password text",
        "securetextfield",
        "axsecuretextfield",
        "secureedit",
    }
)


def omit_tree_value(*roles: Optional[str], is_password: bool = False) -> bool:
    """Return True when a password or secure-field value must not be retained."""

    if is_password:
        return True
    for role in roles:
        if not isinstance(role, str):
            continue
        collapsed = " ".join(role.replace("_", " ").casefold().split())
        compact = collapsed.replace(" ", "")
        if collapsed in _SECRET_VALUE_ROLE_KEYS or compact in _SECRET_VALUE_ROLE_KEYS:
            return True
    return False
# --- end verbatim ------------------------------------------------------------


def clip_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> Optional[str]:
    """A provider string, stripped and capped; None when absent or not text."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return value[:limit]


def is_secure(el: Optional[dict]) -> bool:
    if not isinstance(el, dict):
        return False
    return omit_tree_value(el.get("role"), el.get("subrole"), is_password=bool(el.get("secure")))


def is_editable(el: Optional[dict]) -> bool:
    return isinstance(el, dict) and (el.get("role") in EDITABLE_ROLES or el.get("subrole") in EDITABLE_ROLES)


def sanitize_element(el: Optional[dict], *, keep_value: bool = True) -> Dict[str, Any]:
    """A clean element dict: known keys only, capped text, and NO value on a secure field."""
    if not isinstance(el, dict):
        return {}
    secure = is_secure(el)
    out: Dict[str, Any] = {}
    for k in ("role", "subrole", "name", "identifier"):
        v = clip_text(el.get(k))
        if v is not None:
            out[k] = v
    if isinstance(el.get("ordinal"), int) and not isinstance(el.get("ordinal"), bool) and el["ordinal"] >= 0:
        out["ordinal"] = el["ordinal"]
    if keep_value and not secure and "value" in el and not isinstance(el["value"], bool):
        v = el["value"]
        out["value"] = "" if v is None else str(v)[:20000]
    return out


def secret_names(obj: Any) -> set:
    """Names of every secure element mentioned anywhere in a nested structure (for audits)."""
    found = set()
    if isinstance(obj, dict):
        if is_secure(obj) and obj.get("name"):
            found.add(obj["name"])
        for v in obj.values():
            found |= secret_names(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= secret_names(v)
    return found
