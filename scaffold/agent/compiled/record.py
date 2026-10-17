"""
record.py — the Tool/Routine record format (compiled tools spec §4, step 2).

  Record            one compiled Tool or Routine (dataclass; to_dict / from_dict)
  RECORD_SCHEMA     the JSON schema of a serialised record (subset validator below)
  validate()        a small, dependency-free JSON-schema subset validator:
                    type, required, properties, additionalProperties, pattern,
                    enum, maxLength, minLength, minimum, maximum, items
  taint_fields()    params fields marked "x-taint" (free text that may not reach a sink)
  compute_fingerprint / check_fingerprint
                    the preconditions fingerprint: sha256 of the files the record
                    reads or writes, plus the lockfile. A mismatch suspends (§7),
                    it is not a failure.

No LLM, no network. Writes elsewhere are atomic (see store helpers below).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

KINDS = ("tool", "routine")
STATES = ("candidate", "admitted", "promoted", "suspended", "demoted", "retired", "rejected")
TAINT_KEY = "x-taint"
LOCKFILES = ("uv.lock", "requirements.txt", "poetry.lock", "package-lock.json", "Pipfile.lock")


# ── JSON-schema subset validator ──────────────────────────────────────────────

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def _type_ok(value: Any, t: str) -> bool:
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[t])


def _same(a: Any, b: Any) -> bool:
    """JSON equality: unlike Python ==, a bool never equals a number (0 != False)."""
    return isinstance(a, bool) == isinstance(b, bool) and a == b


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Errors for `value` against a JSON-schema subset; [] means valid."""
    errs: list[str] = []
    if not isinstance(schema, dict):
        return errs
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in types):
            return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        errs.append(f"{path}: {value!r} not in enum")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing required {k!r}")
        for k, v in value.items():
            if k in props:
                errs.extend(validate(v, props[k], f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected property {k!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs.extend(validate(v, schema["additionalProperties"], f"{path}.{k}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value):
            errs.extend(validate(v, schema["items"], f"{path}[{i}]"))
    return errs


def taint_fields(params_schema: dict) -> dict[str, str]:
    """{field: taint} for params marked x-taint (e.g. "data": may not reach a sink)."""
    return {k: v[TAINT_KEY] for k, v in (params_schema.get("properties") or {}).items()
            if isinstance(v, dict) and TAINT_KEY in v}


def validate_params(params_schema: dict, params: Any) -> list[str]:
    """Params check before run() sees them. A params schema must be closed."""
    errs = []
    if params_schema.get("additionalProperties") is not False:
        errs.append("params_schema must set additionalProperties: false")
    return errs + validate(params, params_schema, "params")


# ── Preconditions fingerprint ─────────────────────────────────────────────────

def file_sha(path: Path) -> Optional[str]:
    try:
        return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:  # missing, a directory, unreadable (PermissionError), ...
        return None


def _safe_rel(f: str) -> bool:
    """A fingerprint path must be repo-relative: no absolute paths, no '..'."""
    p = str(f).replace("\\", "/")
    return bool(p) and not p.startswith("/") and not re.match(r"^[A-Za-z]:", p) \
        and ".." not in p.split("/")


def compute_fingerprint(root: Path, files: list[str], toolchain: Optional[dict] = None) -> dict:
    """Fingerprint of `files` (repo-relative) and the first lockfile found."""
    root = Path(root)
    lock = next((n for n in LOCKFILES if (root / n).is_file()), None)
    return {
        "files": {f: file_sha(root / f) for f in sorted(files)},
        "lockfile": {lock: file_sha(root / lock)} if lock else None,
        "toolchain": dict(toolchain or {}),
    }


def check_fingerprint(root: Path, fp: dict) -> list[str]:
    """Mismatches between `fp` and the tree at `root`; [] means it matches."""
    root = Path(root)
    out = []
    for f, want in (fp.get("files") or {}).items():
        if not _safe_rel(f):
            out.append(f"{f}: unsafe path (must be repo-relative)")
            continue
        got = file_sha(root / f)
        if got != want:
            out.append(f"{f}: {'missing' if got is None else 'changed'}")
    for f, want in (fp.get("lockfile") or {}).items():
        if not _safe_rel(f):
            out.append(f"lockfile {f}: unsafe path (must be repo-relative)")
            continue
        if file_sha(root / f) != want:
            out.append(f"lockfile {f}: changed")
    return out


# ── Record dataclasses ────────────────────────────────────────────────────────

@dataclass
class Intent:
    summary: str
    triggers: list[str] = field(default_factory=list)
    anti_triggers: list[str] = field(default_factory=list)
    embedding_key: Optional[str] = None     # reserved; fuzzy matching is a trap (APC)


@dataclass
class Preconditions:
    repo_key: str = ""
    fingerprint: dict = field(default_factory=dict)
    state_predicates: list[str] = field(default_factory=list)
    drift_policy: str = "suspend"


@dataclass
class Probe:
    entry: str
    kinds: list[str] = field(default_factory=list)
    fails_on_start: Optional[bool] = None   # set by admission (A1)


@dataclass
class NearMiss:
    generator: str
    examples: list[str] = field(default_factory=list)


@dataclass
class Evidence:
    source_traces: list[str] = field(default_factory=list)
    admission: dict = field(default_factory=dict)
    s: int = 0
    f: int = 0
    s_live: int = 0
    f_live: int = 0
    s_indep: int = 0                        # synthetic successes from the independent generator
    consecutive_ok: int = 0                 # since the last failure (demoted -> usable)
    fail_ts: list[float] = field(default_factory=list)
    last_fail: Optional[dict] = None
    last_used_ts: Optional[float] = None
    lb95: float = 0.0


@dataclass
class Record:
    kind: str
    family: str
    intent: Intent
    params_schema: dict
    preconditions: Preconditions
    body: dict
    probe: Probe
    near_miss: NearMiss
    evidence: Evidence = field(default_factory=Evidence)
    state: str = "candidate"
    history: list[dict] = field(default_factory=list)
    maker: dict = field(default_factory=dict)
    owner_approved_sinks: bool = False
    version: int = 1
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = record_id(self.kind, self.family, self.body)

    def taint(self) -> dict[str, str]:
        return taint_fields(self.params_schema)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        d = dict(d)
        return cls(
            kind=d["kind"], family=d["family"], intent=Intent(**d["intent"]),
            params_schema=d["params_schema"],
            preconditions=Preconditions(**d["preconditions"]),
            body=d["body"], probe=Probe(**d["probe"]),
            near_miss=NearMiss(**d["near_miss"]),
            evidence=Evidence(**d.get("evidence", {})),
            state=d.get("state", "candidate"), history=d.get("history", []),
            maker=d.get("maker", {}),
            owner_approved_sinks=d.get("owner_approved_sinks", False),
            version=d.get("version", 1), id=d.get("id", ""),
        )


def record_id(kind: str, family: str, body: dict) -> str:
    blob = json.dumps([kind, family, body], sort_keys=True)
    return "rt_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


_STR_LIST = {"type": "array", "items": {"type": "string"}}
RECORD_SCHEMA: dict = {
    "type": "object",
    "required": ["id", "version", "kind", "family", "intent", "params_schema",
                 "preconditions", "body", "probe", "near_miss", "evidence", "state"],
    "properties": {
        "id": {"type": "string", "pattern": r"^rt_[0-9a-f]{12}$"},
        "version": {"type": "integer", "minimum": 1},
        "kind": {"type": "string", "enum": list(KINDS)},
        "family": {"type": "string", "pattern": r"^[a-z0-9_]+$"},
        "intent": {"type": "object", "required": ["summary", "triggers", "anti_triggers"],
                   "properties": {"summary": {"type": "string"}, "triggers": _STR_LIST,
                                  "anti_triggers": _STR_LIST,
                                  "embedding_key": {"type": ["string", "null"]}}},
        "params_schema": {"type": "object",
                          "required": ["type", "properties", "additionalProperties"],
                          "properties": {"type": {"enum": ["object"]},
                                         "additionalProperties": {"enum": [False]}}},
        "preconditions": {"type": "object", "required": ["fingerprint", "drift_policy"],
                          "properties": {"repo_key": {"type": "string"},
                                         "fingerprint": {"type": "object"},
                                         "state_predicates": _STR_LIST,
                                         "drift_policy": {"enum": ["suspend"]}}},
        "body": {"type": "object"},
        "probe": {"type": "object", "required": ["entry"],
                  "properties": {"entry": {"type": "string"}, "kinds": _STR_LIST,
                                 "fails_on_start": {"type": ["boolean", "null"]}}},
        "near_miss": {"type": "object", "required": ["generator"],
                      "properties": {"generator": {"type": "string"}, "examples": _STR_LIST}},
        "evidence": {"type": "object", "required": ["s", "f", "s_live", "f_live"],
                     "properties": {k: {"type": "integer", "minimum": 0}
                                    for k in ("s", "f", "s_live", "f_live", "s_indep",
                                              "consecutive_ok")}},
        "state": {"type": "string", "enum": list(STATES)},
        "history": {"type": "array", "items": {"type": "object"}},
        "maker": {"type": "object"},
        "owner_approved_sinks": {"type": "boolean"},
    },
}


def validate_record(d: dict) -> list[str]:
    errs = validate(d, RECORD_SCHEMA, "record")
    if not errs and d["kind"] == "routine" and not d["body"].get("routine"):
        errs.append("record.body: a routine needs body.routine steps")
    if not errs and d["kind"] == "tool" and not (d["body"].get("tool") or {}).get("entry"):
        errs.append("record.body: a tool needs body.tool.entry")
    return errs


# ── Store helpers (atomic, one live record per family) ────────────────────────

def write_json_atomic(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save_record(store_dir: Path, rec: Record) -> Path:
    """Write <store>/<id>.v<version>.json and point INDEX[family] at it (top-1 by construction)."""
    store_dir = Path(store_dir)
    errs = validate_record(rec.to_dict())
    if errs:
        raise ValueError("; ".join(errs))
    path = store_dir / f"{rec.id}.v{rec.version}.json"
    write_json_atomic(path, rec.to_dict())
    idx_path = store_dir / "INDEX.json"
    with _IndexLock(store_dir):
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            idx = {}
        idx[rec.family] = path.name
        write_json_atomic(idx_path, idx)
    return path


_THREAD_LOCK = threading.Lock()


class _IndexLock:
    """INDEX.json read-modify-write lock: a thread lock plus an flock across processes."""

    def __init__(self, store_dir: Path):
        self.path = Path(store_dir) / "INDEX.lock"
        self.fh = None

    def __enter__(self):
        _THREAD_LOCK.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(self.path, "a")
            try:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - non-POSIX: thread lock only
                pass
        except BaseException:
            if self.fh:
                self.fh.close()
            _THREAD_LOCK.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.fh.close()  # releases the flock
        finally:
            _THREAD_LOCK.release()
        return False


def load_record(path: Path) -> Record:
    return Record.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
