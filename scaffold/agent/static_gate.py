"""
static_gate.py — V0+ static gate: reject a Python edit before it reaches disk
when it would not compile or would introduce a NEW undefined name.

Spec: docs/specs/static_gate.md (trick T3, docs/research/trick_book_2026-10.md).
Off unless AWOS_STATIC_GATE=1.

Design (SWE-agent ACI linter-on-edit): the edit is checked against the file's
new text; on failure the file is left unchanged and the messages are returned
to the model. Only *new* problems block (before/after comparison), so a file
that already had an undefined name stays editable. After MAX_CONSECUTIVE
rejections on the same file the gate lets the write through with a warning —
loops on lint errors are a known failure mode (SWE-agent: 23.4% of failures).

Checks (.py only; every other file passes):
  (a) the new text compiles (compile(), the same as py_compile)
  (b) ruff F821/F822 (undefined name / undefined name in __all__) — only names
      whose count grew vs the old text. ruff runs --isolated so the target
      repo's config cannot switch rules on or off. Without ruff, (b) is skipped.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

ENV_VAR = "AWOS_STATIC_GATE"
MAX_CONSECUTIVE = 2
RUFF_RULES = "F821,F822"

#: consecutive gate rejections per absolute path; reset when that file passes
_consecutive: dict[str, int] = {}
#: total rejections in this process (orchestrator snapshots it per task)
_total_rejections = 0

_ruff_path: Optional[str] = None
_ruff_looked = False


def enabled() -> bool:
    return os.getenv(ENV_VAR, "0").strip() == "1"


#: V0 "tests are read-only" in the agent loop. Its own knob, default off:
#: offline, 7 of 73 solved job_series runs edited an existing test file in a
#: feature task (docs/specs/static_gate.md §Offline check), so it is measured
#: separately rather than bundled into the lint gate.
TESTS_ENV_VAR = "AWOS_STATIC_GATE_TESTS"


def tests_read_only() -> bool:
    return os.getenv(TESTS_ENV_VAR, "0").strip() == "1"


def total_rejections() -> int:
    return _total_rejections


def new_task() -> None:
    """Start the per-file cap afresh (the cap is per task); keeps the total."""
    _consecutive.clear()


def reset() -> None:
    """Forget per-file counters and the total (tests; a new task)."""
    global _total_rejections
    _consecutive.clear()
    _total_rejections = 0


def _ruff() -> Optional[str]:
    global _ruff_path, _ruff_looked
    if not _ruff_looked:
        _ruff_looked = True
        cand = Path(sys.executable).parent / "ruff"
        _ruff_path = str(cand) if cand.exists() else shutil.which("ruff")
    return _ruff_path


def _compile_errors(path: str, text: str) -> list[str]:
    try:
        compile(text, path, "exec", dont_inherit=True)
    except SyntaxError as e:
        line = (e.text or "").rstrip("\n")
        return [f"SyntaxError at line {e.lineno}: {e.msg}" + (f"\n    {line}" if line else "")]
    except (ValueError, TypeError) as e:  # e.g. null bytes
        return [f"compile error: {e}"]
    return []


def _undefined(path: str, text: str) -> Optional[list[tuple[str, int, str]]]:
    """[(name, line, message)] from ruff, or None when ruff is unavailable/fails."""
    ruff = _ruff()
    if not ruff:
        return None
    try:
        proc = subprocess.run(
            [ruff, "check", "--isolated", "--no-cache", "--select", RUFF_RULES,
             "--output-format", "json", "--stdin-filename", path, "-"],
            input=text, capture_output=True, text=True, timeout=20,
        )
        items = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    out = []
    for it in items:
        msg = it.get("message", "")
        # "Undefined name `foo`" / "Undefined name `foo` in `__all__`"
        name = msg.split("`")[1] if msg.count("`") >= 2 else msg
        row = (it.get("location") or {}).get("row", 0)
        out.append((name, row, f"{it.get('code', 'F821')} {msg}"))
    return out


def problems(path: str, new_text: str, old_text: Optional[str] = "") -> list[str]:
    """The NEW problems new_text has vs old_text (no counters, no logging)."""
    if not str(path).endswith(".py"):
        return []
    errs = _compile_errors(str(path), new_text)
    if errs:
        return errs
    after = _undefined(str(path), new_text)
    if not after:
        return []
    before = _undefined(str(path), old_text or "") if old_text else []
    before_counts = Counter(n for n, _, _ in (before or []))
    after_counts = Counter(n for n, _, _ in after)
    new_names = {n for n, c in after_counts.items() if c > before_counts.get(n, 0)}
    return [f"line {row}: {msg}" for n, row, msg in after if n in new_names]


def check(path: str, new_text: str, old_text: Optional[str] = "") -> tuple[bool, list[str]]:
    """
    Gate one write. (True, []) to proceed; (False, messages) to reject it.
    Applies the consecutive-rejection cap and logs [STATIC-GATE] lines.
    Callers decide whether the gate is on (enabled()).
    """
    global _total_rejections
    key = os.path.abspath(str(path))
    msgs = problems(path, new_text, old_text)
    if not msgs:
        _consecutive.pop(key, None)
        return True, []
    n = _consecutive.get(key, 0)
    if n >= MAX_CONSECUTIVE:
        print(f"[STATIC-GATE] cap reached ({n} rejections) for {path}; "
              f"allowing the write: {msgs[0]}")
        _consecutive.pop(key, None)
        return True, msgs
    _consecutive[key] = n + 1
    _total_rejections += 1
    print(f"[STATIC-GATE] rejected {path}: {msgs[0]}")
    return False, msgs


def rejection_message(path: str, msgs: list[str]) -> str:
    """The text a model sees, SWE-agent style: why, and that nothing changed."""
    shown = "\n".join(f"  - {m}" for m in msgs[:8])
    more = f"\n  (+{len(msgs) - 8} more)" if len(msgs) > 8 else ""
    return (
        f"Your edit to {path} was NOT applied (the file is unchanged): it introduces "
        f"these errors:\n{shown}{more}\n"
        "Fix the edit (define or import the name, fix the syntax) and try again."
    )
