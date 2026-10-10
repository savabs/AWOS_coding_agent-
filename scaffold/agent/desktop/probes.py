"""
probes.py — deterministic end-state postconditions for computer-use chores.

See docs/specs/computer_use_foundations.md (T10). No LLM anywhere in here.

A Probe is a getter (reads one fact from the world) plus a comparator (checks
that fact against an expected value). This is the OSWorld / macOSWorld /
AndroidWorld checker design: the verdict comes from the end state, never from
the agent's own account of what it did.

Probe spec (JSON):

    {"id": "port_changed",
     "getter":     {"type": "ini_value", "path": "settings.ini",
                    "section": "server", "key": "port"},
     "comparator": {"op": "equals", "expected": "9090"}}

Relative paths resolve against the state root passed to ``holds(root)``.
A getter that raises (missing file, bad JSON, timeout) makes the probe fail;
it never makes it pass.

Validity rule (same as acceptance V1, must-fail-first): a probe is valid for
a chore only if it FAILS on the start state and PASSES on the verified end
state. ``validate_probe`` enforces it.
"""
from __future__ import annotations

import configparser
import fnmatch
import glob as _glob
import hashlib
import json
import math
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SUBPROCESS_TIMEOUT_S = 8


class ProbeError(Exception):
    """A getter could not read its fact. The probe fails."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve(root: Path, rel: str) -> Path:
    p = Path(os.path.expanduser(rel))
    return p if p.is_absolute() else (Path(root) / p)


def _walk_key(obj: Any, key: str) -> Any:
    """Dotted key path; integer segments index lists ("rows.2.email")."""
    if key in ("", None):
        return obj
    for seg in str(key).split("."):
        if isinstance(obj, list):
            try:
                obj = obj[int(seg)]
            except (ValueError, IndexError) as e:
                raise ProbeError(f"bad list index {seg!r}") from e
        elif isinstance(obj, dict):
            if seg not in obj:
                raise ProbeError(f"missing key {seg!r}")
            obj = obj[seg]
        else:
            raise ProbeError(f"cannot index {type(obj).__name__} with {seg!r}")
    return obj


def _run(cmd: List[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=SUBPROCESS_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError(f"{cmd[0]}: {e}") from e
    if out.returncode != 0:
        raise ProbeError(f"{cmd[0]} exit {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout.rstrip("\n")


def _applescript_str(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# getters: (spec, root) -> value
# ---------------------------------------------------------------------------

def g_file_exists(spec: dict, root: Path) -> bool:
    return _resolve(root, spec["path"]).exists()


def g_file_content(spec: dict, root: Path) -> str:
    p = _resolve(root, spec["path"])
    try:
        text = p.read_text(encoding=spec.get("encoding", "utf-8"))
    except OSError as e:
        raise ProbeError(str(e)) from e
    return text.strip() if spec.get("strip", True) else text


def g_file_hash(spec: dict, root: Path) -> str:
    p = _resolve(root, spec["path"])
    try:
        return hashlib.new(spec.get("algo", "sha256"), p.read_bytes()).hexdigest()
    except OSError as e:
        raise ProbeError(str(e)) from e


def g_glob(spec: dict, root: Path) -> List[str]:
    """Sorted paths (relative to root) matching a pattern."""
    base = Path(root)
    hits = _glob.glob(str(base / spec["pattern"]), recursive=True)
    return sorted(os.path.relpath(h, base) for h in hits)


def g_json_value(spec: dict, root: Path) -> Any:
    try:
        data = json.loads(_resolve(root, spec["path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProbeError(str(e)) from e
    return _walk_key(data, spec.get("key", ""))


def g_plist_value(spec: dict, root: Path) -> Any:
    try:
        with open(_resolve(root, spec["path"]), "rb") as f:
            data = plistlib.load(f)
    except Exception as e:  # plistlib raises several types
        raise ProbeError(str(e)) from e
    return _walk_key(data, spec.get("key", ""))


def g_ini_value(spec: dict, root: Path) -> str:
    cp = configparser.ConfigParser(interpolation=None)
    p = _resolve(root, spec["path"])
    try:
        if not cp.read(p, encoding="utf-8"):
            raise ProbeError(f"cannot read {p}")
        return cp.get(spec["section"], spec["key"])
    except (configparser.Error, OSError) as e:
        raise ProbeError(str(e)) from e


def g_sqlite_query(spec: dict, root: Path) -> Any:
    """Read-only query. ``mode``: scalar (default) | row | rows."""
    p = _resolve(root, spec["path"])
    if not p.exists():
        raise ProbeError(f"no database {p}")
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = con.execute(spec["sql"], spec.get("params", [])).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise ProbeError(str(e)) from e
    mode = spec.get("mode", "scalar")
    if mode == "rows":
        return [list(r) for r in rows]
    if not rows:
        return None
    return list(rows[0]) if mode == "row" else rows[0][0]


def g_defaults_read(spec: dict, root: Path) -> str:
    """macOS ``defaults read <domain> <key>``. A domain that looks like a path
    resolves against root (defaults takes a plist path without .plist)."""
    domain = spec["domain"]
    if "/" in domain or domain.endswith(".plist"):
        domain = str(_resolve(root, domain))
        if domain.endswith(".plist"):
            domain = domain[: -len(".plist")]
    return _run(["defaults", "read", domain, spec["key"]])


def g_process_running(spec: dict, root: Path) -> bool:
    try:
        r = subprocess.run(["pgrep", "-x", spec["name"]], capture_output=True,
                           timeout=SUBPROCESS_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError(str(e)) from e
    return r.returncode == 0


def g_ax_attribute(spec: dict, root: Path) -> str:
    """AX attribute via System Events (needs Accessibility permission for the
    calling process). ``element`` is an AppleScript element path relative to
    the process, default ``window 1``."""
    if sys.platform != "darwin":
        raise ProbeError("ax_attribute needs macOS")
    element = spec.get("element", "window 1")
    if not re.fullmatch(r"[A-Za-z0-9 _]+", element):
        raise ProbeError("element path must be plain AppleScript words")
    script = (
        'tell application "System Events" to tell process '
        f'{_applescript_str(spec["process"])} to get value of attribute '
        f'{_applescript_str(spec["attribute"])} of {element}'
    )
    return _run(["osascript", "-e", script])


def g_frontmost_app(spec: dict, root: Path) -> str:
    if sys.platform != "darwin":
        raise ProbeError("frontmost_app needs macOS")
    return _run(["osascript", "-e", 'tell application "System Events" to get '
                 'name of first process whose frontmost is true'])


def g_mdls(spec: dict, root: Path) -> str:
    """Spotlight metadata attribute (raw value)."""
    return _run(["mdls", "-raw", "-name", spec["attribute"],
                 str(_resolve(root, spec["path"]))])


class _FormValues(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: Dict[str, Any] = {}
        self._ta: Optional[str] = None
        self._select: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "option":
            if "selected" in a and self._select:
                self.values[self._select] = a.get("value", "")
            return
        key = a.get("id") or a.get("name")
        if not key:
            return
        if tag == "input":
            if a.get("type") in ("checkbox", "radio"):
                self.values[key] = "checked" in a
            else:
                self.values[key] = a.get("value", "")
        elif tag == "textarea":
            self._ta = key
            self.values[key] = ""
        elif tag == "select":
            self.values.setdefault(key, None)
            self._select = key

    def handle_data(self, data):
        if self._ta:
            self.values[self._ta] += data

    def handle_endtag(self, tag):
        if tag == "textarea":
            self._ta = None
        elif tag == "select":
            self._select = None


def g_html_field(spec: dict, root: Path) -> Any:
    """Value of a form control (by id or name) in a saved HTML/DOM file."""
    try:
        html = _resolve(root, spec["path"]).read_text(encoding="utf-8")
    except OSError as e:
        raise ProbeError(str(e)) from e
    parser = _FormValues()
    parser.feed(html)
    if spec["field"] not in parser.values:
        raise ProbeError(f"no field {spec['field']!r}")
    return parser.values[spec["field"]]


GETTERS: Dict[str, Callable[[dict, Path], Any]] = {
    "file_exists": g_file_exists,
    "file_content": g_file_content,
    "file_hash": g_file_hash,
    "glob": g_glob,
    "json_value": g_json_value,
    "plist_value": g_plist_value,
    "ini_value": g_ini_value,
    "sqlite_query": g_sqlite_query,
    "defaults_read": g_defaults_read,
    "process_running": g_process_running,
    "ax_attribute": g_ax_attribute,
    "frontmost_app": g_frontmost_app,
    "mdls": g_mdls,
    "html_field": g_html_field,
}

# Getters that touch the live desktop rather than files under the root.
LIVE_GETTERS = {"process_running", "ax_attribute", "frontmost_app"}


# ---------------------------------------------------------------------------
# comparators: (actual, spec) -> bool
# ---------------------------------------------------------------------------

def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError) as e:
        raise ProbeError(f"not a number: {x!r}") from e


def c_equals(actual, spec):
    return actual == spec["expected"]


def c_not_equals(actual, spec):
    return actual != spec["expected"]


def c_contains(actual, spec):
    try:
        return spec["expected"] in actual
    except TypeError:
        return False


def c_regex(actual, spec):
    flags = re.MULTILINE | (re.IGNORECASE if spec.get("ignore_case") else 0)
    return isinstance(actual, str) and re.search(spec["expected"], actual, flags) is not None


def c_approx(actual, spec):
    tol = float(spec.get("tolerance", 1e-6))
    return math.isclose(_num(actual), _num(spec["expected"]), abs_tol=tol, rel_tol=0)


def c_set_equals(actual, spec):
    try:
        return set(map(_hashable, actual)) == set(map(_hashable, spec["expected"]))
    except TypeError:
        return False


def c_length(actual, spec):
    try:
        return len(actual) == int(spec["expected"])
    except TypeError:
        return False


def c_truthy(actual, spec):
    return bool(actual) is bool(spec.get("expected", True))


def c_glob_match(actual, spec):
    return isinstance(actual, str) and fnmatch.fnmatchcase(actual, spec["expected"])


def _hashable(x):
    return json.dumps(x, sort_keys=True) if isinstance(x, (list, dict)) else x


COMPARATORS: Dict[str, Callable[[Any, dict], bool]] = {
    "equals": c_equals,
    "not_equals": c_not_equals,
    "contains": c_contains,
    "regex": c_regex,
    "approx": c_approx,
    "set_equals": c_set_equals,
    "length": c_length,
    "truthy": c_truthy,
    "glob_match": c_glob_match,
}


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    probe_id: str
    passed: bool
    actual: Any = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {"probe_id": self.probe_id, "passed": self.passed,
                "actual": _jsonable(self.actual), "error": self.error}


def _jsonable(x):
    try:
        json.dumps(x)
        return x
    except TypeError:
        return repr(x)


@dataclass
class Probe:
    id: str
    getter: dict
    comparator: dict
    description: str = ""

    def __post_init__(self):
        if self.getter.get("type") not in GETTERS:
            raise ValueError(f"unknown getter {self.getter.get('type')!r}")
        if self.comparator.get("op") not in COMPARATORS:
            raise ValueError(f"unknown comparator {self.comparator.get('op')!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "Probe":
        return cls(id=d["id"], getter=dict(d["getter"]),
                   comparator=dict(d["comparator"]),
                   description=d.get("description", ""))

    def to_dict(self) -> dict:
        d = {"id": self.id, "getter": self.getter, "comparator": self.comparator}
        if self.description:
            d["description"] = self.description
        return d

    @property
    def is_live(self) -> bool:
        return self.getter["type"] in LIVE_GETTERS

    def evaluate(self, root: Path | str = ".") -> ProbeResult:
        try:
            actual = GETTERS[self.getter["type"]](self.getter, Path(root))
        except (ProbeError, KeyError) as e:
            return ProbeResult(self.id, False, None, f"getter: {e}")
        try:
            ok = bool(COMPARATORS[self.comparator["op"]](actual, self.comparator))
        except (ProbeError, KeyError) as e:
            return ProbeResult(self.id, False, actual, f"comparator: {e}")
        return ProbeResult(self.id, ok, actual)

    def holds(self, root: Path | str = ".") -> bool:
        return self.evaluate(root).passed


@dataclass
class ProbeSet:
    """All-of conjunction. A chore is done only when every probe holds."""
    probes: List[Probe] = field(default_factory=list)

    @classmethod
    def from_list(cls, items: List[dict]) -> "ProbeSet":
        return cls([Probe.from_dict(d) for d in items])

    def evaluate(self, root) -> List[ProbeResult]:
        return [p.evaluate(root) for p in self.probes]

    def holds(self, root) -> bool:
        return all(r.passed for r in self.evaluate(root))


@dataclass
class ProbeValidity:
    probe_id: str
    fails_on_start: bool
    passes_on_end: bool
    start: ProbeResult
    end: ProbeResult

    @property
    def valid(self) -> bool:
        return self.fails_on_start and self.passes_on_end


def validate_probe(probe: Probe, start_root, end_root) -> ProbeValidity:
    """V1 rule: valid only if it FAILS on start and PASSES on the end state."""
    s = probe.evaluate(start_root)
    e = probe.evaluate(end_root)
    return ProbeValidity(probe.id, not s.passed, e.passed, s, e)


def load_probe(path_or_json) -> Probe:
    if isinstance(path_or_json, dict):
        return Probe.from_dict(path_or_json)
    text = Path(path_or_json).read_text(encoding="utf-8") \
        if not str(path_or_json).lstrip().startswith("{") else path_or_json
    return Probe.from_dict(json.loads(text))
