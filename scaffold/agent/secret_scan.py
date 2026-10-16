"""Secret scan over the ADDED lines of a unified diff (Droid Shield style).

Used as a fail-closed gate before AWOS hands off a review branch
(``host/handoff.py``) and before ``awos fix`` pushes one (``app/fix.py``).

Pure: no I/O, no git, no network. ``scan_diff(diff_text)`` returns
``Finding(path, line, rule, preview)`` records. A finding NEVER carries the
secret value: ``preview`` is at most the first 4 characters plus an ellipsis.

Rules (specific first; one finding per span):
    aws_access_key, anthropic_api_key, openai_api_key, github_token,
    github_pat, slack_token, stripe_live_key, google_api_key, private_key,
    generic_secret_assignment (``password|secret|token|api_key = "<value>"``
    with an entropy threshold).

Suppression:
    * obvious placeholders ("xxxx", "<your-key>", "example", "dummy", ...)
    * the generic rule is skipped for files under a ``tests/`` directory
    * an inline ``awos:allow-secret`` pragma on the same line waives the
      GENERIC rule only. The pragma is written by whoever authored the diff
      (possibly the model), so it never waives a known key format (AWS,
      Anthropic, GitHub, ...): those block whatever the line says. Callers
      should surface waived findings to the reviewer (``include_allowed``).

Emergency escape hatch: ``AWOS_SECRET_SCAN=0`` (see ``scan_enabled``) turns
the gate off. It is for emergencies only (a false positive blocking an urgent
hand-off); the bypass is recorded in the job's warnings.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Pattern, Tuple

PRAGMA = "awos:allow-secret"
ENV_SWITCH = "AWOS_SECRET_SCAN"
PREVIEW_CHARS = 4

GENERIC_RULE = "generic_secret_assignment"
GENERIC_MIN_LEN = 16
GENERIC_MIN_ENTROPY = 3.5   # bits per char; random base62 is ~5.9 max, ~4+ typical at 20+ chars


@dataclass(frozen=True)
class Finding:
    path: str
    line: int          # line number in the NEW file
    rule: str
    preview: str       # <= 4 chars of the match + "…"; never the value
    allowed: bool = False   # suppressed by the inline pragma (reported, not blocking)

    def __str__(self) -> str:
        tag = " [allowed by pragma]" if self.allowed else ""
        return f"{self.path}:{self.line}: {self.rule} ({self.preview}){tag}"


# (rule, pattern, group holding the secret). Order matters: earlier rules
# claim a span first, so ``sk-ant-...`` is reported as Anthropic, not OpenAI.
RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"\bxox[abposre]-[A-Za-z0-9\-]{10,}")),
    ("stripe_live_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
)

_GENERIC = re.compile(
    r"""(?ix)
    (?P<name>[A-Za-z0-9_.\-]*
        (?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|auth[_\-]?key
          |private[_\-]?key|client[_\-]?secret|credential)
     [A-Za-z0-9_.\-]*)
    ["']?\s*(?::=|=>|==?|:)\s*
    (?P<q>["'`])(?P<val>[^"'`\s]+)(?P=q)
    """
)

# Lower-cased substrings that mark a value as a placeholder for every rule.
_PLACEHOLDERS = ("example", "xxxx", "dummy", "placeholder", "your_", "your-", "yourkey",
                 "<", ">", "redacted", "changeme", "insert", "replace_me", "replaceme")
# Extra substrings only for the generic rule (values there are free text).
_GENERIC_PLACEHOLDERS = ("fake", "test", "sample", "${", "{{", "%(", "...", "****",
                         "none", "null", "secret", "password", "token")


def scan_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """False only when ``AWOS_SECRET_SCAN=0`` (emergencies only)."""
    import os
    src = os.environ if env is None else env
    return str(src.get(ENV_SWITCH, "")).strip() != "0"


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def redact_preview(value: str) -> str:
    return value[:PREVIEW_CHARS] + "…"


def _is_placeholder(token: str, generic: bool = False) -> bool:
    low = token.lower()
    if any(p in low for p in _PLACEHOLDERS):
        return True
    if generic and any(p in low for p in _GENERIC_PLACEHOLDERS):
        return True
    # "sk-aaaaaaaaaaaaaaaaaaaaaaaa", "AKIA0000000000000000": not a real key.
    body = token[4:] if len(token) > 8 else token
    return len(set(body.lower())) < 5


def _in_tests_dir(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return "tests" in parts[:-1]


def scan_line(path: str, lineno: int, text: str,
              include_allowed: bool = False) -> List[Finding]:
    """Findings for one added line."""
    # The pragma waives only the generic rule (see the module docstring).
    allowed = PRAGMA in text.lower()
    found: List[Finding] = []
    claimed: List[Tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in claimed)

    for rule, pat in RULES:
        for m in pat.finditer(text):
            tok = m.group(0)
            if not free(m.start(), m.end()):
                continue
            if rule != "private_key" and _is_placeholder(tok):
                continue
            claimed.append((m.start(), m.end()))
            found.append(Finding(path, lineno, rule, redact_preview(tok)))
    if not _in_tests_dir(path):
        for m in _GENERIC.finditer(text):
            val = m.group("val")
            a, b = m.start("val"), m.end("val")
            if not free(a, b):
                continue
            if len(val) < GENERIC_MIN_LEN or shannon_entropy(val) < GENERIC_MIN_ENTROPY:
                continue
            if _is_placeholder(val, generic=True):
                continue
            # Must look random: letters and digits mixed (not a sentence or path).
            if not (re.search(r"[A-Za-z]", val) and re.search(r"\d", val)):
                continue
            if "/" in val and not re.search(r"[+=]", val):
                continue  # URL / file path, not a key
            claimed.append((a, b))
            if allowed and not include_allowed:
                continue
            found.append(Finding(path, lineno, GENERIC_RULE, redact_preview(val), allowed))
    found.sort(key=lambda f: f.rule)
    return found


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _diff_path(header: str) -> Optional[str]:
    """Path from a ``+++ b/path`` line; None for /dev/null."""
    p = header[4:].rstrip("\n").split("\t", 1)[0]
    if p.startswith('"') and p.endswith('"') and len(p) >= 2:
        p = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape", "replace")
        try:
            p = p.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    if p == "/dev/null":
        return None
    return p[2:] if p.startswith(("b/", "w/", "i/")) else p


def added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield ``(path, new_line_number, text)`` for every added line.

    Hunk line counts are tracked, so an added line whose content starts with
    ``++`` is never mistaken for a ``+++`` file header."""
    path: Optional[str] = None
    old_rem = new_rem = 0
    new_line = 0
    for raw in diff_text.splitlines():
        if old_rem > 0 or new_rem > 0:
            tag, body = raw[:1], raw[1:]
            if tag == "+":
                new_rem -= 1
                if path is not None:
                    yield path, new_line, body
                new_line += 1
            elif tag == "-":
                old_rem -= 1
            elif tag == "\\":
                pass  # "\ No newline at end of file"
            else:  # context (" " or an empty line from a stripped trailing space)
                old_rem -= 1
                new_rem -= 1
                new_line += 1
            continue
        if raw.startswith("+++ "):
            path = _diff_path(raw)
        elif raw.startswith("diff --git "):
            path = None
        else:
            m = _HUNK.match(raw)
            if m:
                old_rem = int(m.group(2)) if m.group(2) is not None else 1
                new_rem = int(m.group(4)) if m.group(4) is not None else 1
                new_line = int(m.group(3))


def scan_diff(diff_text: str, include_allowed: bool = False) -> List[Finding]:
    """Scan the added lines of a unified diff. Blocking findings only, unless
    ``include_allowed`` (then pragma-suppressed ones come back with
    ``allowed=True``)."""
    out: List[Finding] = []
    for path, lineno, text in added_lines(diff_text):
        out.extend(scan_line(path, lineno, text, include_allowed))
    return out


def blocking(findings: Iterable[Finding]) -> List[Finding]:
    return [f for f in findings if not f.allowed]


def describe(findings: List[Finding], limit: int = 10) -> str:
    """One line for a job report: ``path:line rule (prev…)``, capped."""
    shown = "; ".join(f"{f.path}:{f.line} {f.rule} ({f.preview})" for f in findings[:limit])
    more = len(findings) - limit
    return shown + (f"; +{more} more" if more > 0 else "")
