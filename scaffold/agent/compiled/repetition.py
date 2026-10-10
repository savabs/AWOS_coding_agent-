"""
repetition.py — the repetition meter (compiled tools, spec §11.1, step 1).

Every finished task gets one cheap intent fingerprint appended to
.awos/repetition/log.jsonl. Three parts:

  goal template   the goal, lower-cased, with paths, versions, numbers, quoted
                  strings, hex ids and code spans replaced by typed slots
                  ("bump version to 0.9.4" -> "bump version to <semver>")
  file pattern    each touched file as <dir>/*.<ext>, root-normalised, sorted, unique
  action shape    the sequence of tool names with consecutive repeats collapsed

  family          a short slug of the template's first content words (or given)
  intent_key      sha(template)[:12]                -> "same kind of request"
  structure_key   sha(template | files | actions)[:12] -> "same request, same shape"

The raw goal is never stored: only its template and a sha. Secrets are slotted
out before templating (key-like tokens such as sk-/ghp_/xox*-/AKIA ids, values
after NAME_KEY=/TOKEN=/SECRET=/PASSWORD= style assignments or after the word
"password", and long mixed letter+digit strings) -> <secret>. No LLM, no network.
Writing never raises (advisory).

scripts/repetition_report.py folds the log into a repeat rate per family: the
share of entries whose key already appeared in the trailing window (28 days).
The E7 prerequisite is an overall rate >= 20%.

Env:
  AWOS_REPETITION_LOG   log path (default .awos/repetition/log.jsonl, cwd-relative);
                        "0"/"off" disables. Under pytest it is off unless this
                        names a path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

LOG_ENV = "AWOS_REPETITION_LOG"
DEFAULT_LOG = Path(".awos") / "repetition" / "log.jsonl"
ROOT_TOKEN = "<PROJECT_ROOT>"
_OFF = {"0", "off", "false", "no", ""}
SCHEMA_VERSION = 1

# Secrets are slotted on the original-case text, before anything else, so no
# credential reaches goal_template (which is written to the log).
_SECRET = "<secret>"
_SECRET_ASSIGN = re.compile(
    r"(?i)(\b[\w.-]*(?:key|token|secret|passw(?:or)?d|pwd|passphrase|credentials?)\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|\S+)")
_SECRET_WORD = re.compile(r"(?i)(\b(?:password|passwd|passphrase)\s+(?:is\s+)?)(\S+)")
_SECRET_TOKEN = re.compile(
    r"\b(?:sk-[\w-]{8,}|gh[pousr]_\w{16,}|github_pat_\w{16,}|xox[abprs]-[\w-]{8,}"
    r"|AKIA[0-9A-Z]{12,}|AIza[\w-]{20,}|glpat-[\w-]{16,})")
# Long mixed letter+digit runs (base64/hex-ish keys). Pure hex <= 40 chars is a
# commit id and keeps its <hex> slot; '/' is excluded so paths stay paths.
_SECRET_LONG = re.compile(r"(?<![\w/.-])(?=[\w+=-]*\d)(?=[\w+=-]*[A-Za-z])[\w+=-]{24,}(?![\w/.-])")


def _redact_secrets(text: str) -> str:
    text = _SECRET_TOKEN.sub(_SECRET, text)
    text = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)} {_SECRET} ", text)
    text = _SECRET_WORD.sub(lambda m: f"{m.group(1)}{_SECRET}", text)

    def _long(m: re.Match) -> str:
        tok = m.group(0)
        if len(tok) <= 40 and re.fullmatch(r"[0-9a-fA-F]+", tok):
            return tok
        return _SECRET
    return _SECRET_LONG.sub(_long, text)


# Order matters: the most specific patterns first.
_SLOTS: list[tuple[str, re.Pattern]] = [
    ("<code>", re.compile(r"`[^`]*`")),
    ("<str>", re.compile(r"\"[^\"]*\"|(?<!\w)'[^'\s][^']*'(?!\w)")),
    ("<url>", re.compile(r"https?://\S+")),
    ("<path>", re.compile(r"(?:[\w.~-]+/)+[\w.-]+|\b[\w-]+\.(?:py|toml|md|json|jsonl|txt|yaml|yml|cfg|ini|js|ts|tsx|sh|lock)\b")),
    ("<semver>", re.compile(r"\bv?\d+\.\d+\.\d+(?:[-+][\w.]+)?\b")),
    ("<hex>", re.compile(r"\b[0-9a-f]{7,40}\b")),
    ("<num>", re.compile(r"\b\d+(?:\.\d+)?\b")),
]
_STOP = {
    "a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "with", "this",
    "that", "it", "please", "can", "you", "we", "our", "my", "is", "be", "so",
    "from", "into", "at", "by", "as", "all", "some", "then", "now",
}


def _sha(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def goal_template(goal: str) -> str:
    """Typed-slot template of a goal: the same request with other values maps equal."""
    text = _redact_secrets((goal or "").strip()).lower()
    for slot, pat in _SLOTS:
        text = pat.sub(f" {slot} ", text)
    text = re.sub(r"[^\w<>\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def family_of(template: str, n_words: int = 3) -> str:
    """A short slug from the template's first content words ("bump_version")."""
    words = [w for w in template.split()
             if w not in _STOP and re.fullmatch(r"[a-z][a-z0-9_]*", w)]
    return "_".join(words[:n_words]) or "unknown"


def _normalise_path(path: str, root: Optional[str]) -> str:
    p = str(path).replace("\\", "/")
    if root:
        r = str(root).replace("\\", "/").rstrip("/")
        if not r:  # root "/" (or "\\"): every absolute path is under it
            p = p.lstrip("/")
        else:
            if p.startswith(r + "/"):
                p = p[len(r) + 1:]
            p = p.replace(r, ROOT_TOKEN)
    return p.removeprefix("./")


def file_pattern(files: Iterable[str], root: Optional[str] = None) -> list[str]:
    """Each touched file as <dir>/*.<ext> (root-normalised), sorted and unique."""
    out = set()
    for f in files or []:
        p = _normalise_path(f, root)
        d, _, name = p.rpartition("/")
        ext = name.rsplit(".", 1)[1] if "." in name.strip(".") else ""
        out.add(f"{d + '/' if d else ''}*{'.' + ext if ext else ''}")
    return sorted(out)


def action_shape(actions: Iterable[Any]) -> list[str]:
    """Tool names in order, consecutive repeats collapsed. Accepts names or {tool: ...}."""
    shape: list[str] = []
    for a in actions or []:
        name = a.get("tool") or a.get("name") if isinstance(a, dict) else a
        name = str(name or "?")
        if not shape or shape[-1] != name:
            shape.append(name)
    return shape


def fingerprint(goal: str, files: Iterable[str] = (), actions: Iterable[Any] = (),
                root: Optional[str] = None, family: Optional[str] = None) -> dict:
    """The intent fingerprint of one finished task (no raw goal inside)."""
    tpl = goal_template(goal)
    fpat = file_pattern(files, root)
    shape = action_shape(actions)
    return {
        "family": family or family_of(tpl),
        "goal_template": tpl,
        "goal_sha": _sha(goal or "", 16),
        "file_pattern": fpat,
        "action_shape": shape,
        "intent_key": _sha(tpl),
        "structure_key": _sha(json.dumps([tpl, fpat, shape])),
    }


def log_path() -> Optional[Path]:
    value = os.environ.get(LOG_ENV)
    if value is None:
        if "PYTEST_CURRENT_TEST" in os.environ:
            return None
        return DEFAULT_LOG
    if value.strip().lower() in _OFF:
        return None
    return Path(value)


def log_task(goal: str, *, files: Iterable[str] = (), actions: Iterable[Any] = (),
             root: Optional[str] = None, outcome: Optional[str] = None,
             evidence_level: Optional[str] = None, family: Optional[str] = None,
             ts: Optional[float] = None, path: Optional[Path] = None) -> Optional[dict]:
    """
    Append one fingerprint line for a finished task. Returns the entry, or None
    when the meter is off or the write failed. Never raises.
    """
    try:
        target = path if path is not None else log_path()
        if target is None:
            return None
        entry = {"v": SCHEMA_VERSION, "ts": float(ts if ts is not None else time.time())}
        entry.update(fingerprint(goal, files, actions, root, family))
        entry["outcome"] = outcome
        entry["evidence_level"] = evidence_level
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry
    except Exception as exc:  # noqa: BLE001 — the meter is advisory
        logger.warning("[REPETITION] log failed: %s", exc)
        return None


def read_log(path: Path) -> list[dict]:
    """Entries in file order; malformed lines skipped."""
    out: list[dict] = []
    try:
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and "ts" in rec:
                    out.append(rec)
    except FileNotFoundError:
        return []
    return out


def repeat_rates(entries: list[dict], *, window_days: float = 28.0,
                 key: str = "structure_key", since: Optional[float] = None) -> dict:
    """
    Repeat rate: an entry is a repeat when an earlier entry (within the trailing
    window) has the same `key`. Entries are sorted by ts. Entries before `since`
    seed the window but are not counted. Returns
    {"overall": {n, repeats, rate}, "families": {family: {n, repeats, rate, keys}}}.
    """
    window = window_days * 86400.0
    seen: dict[str, float] = {}
    fams: dict[str, dict] = {}
    n = rep = 0
    for e in sorted(entries, key=lambda e: float(e.get("ts", 0))):
        k_val = e.get(key)
        ts = float(e.get("ts", 0))
        is_rep = k_val is not None and k_val in seen and ts - seen[k_val] <= window
        if k_val is not None:
            seen[k_val] = ts
        if since is not None and ts < since:
            continue
        fam = e.get("family") or "unknown"
        f = fams.setdefault(fam, {"n": 0, "repeats": 0, "keys": set()})
        f["n"] += 1
        f["repeats"] += int(is_rep)
        f["keys"].add(k_val)
        n += 1
        rep += int(is_rep)
    for f in fams.values():
        f["rate"] = f["repeats"] / f["n"] if f["n"] else 0.0
        f["keys"] = len(f["keys"])
    return {"overall": {"n": n, "repeats": rep, "rate": rep / n if n else 0.0},
            "families": fams}
