"""
fileops.py — plan / dry-run / execute / undo for sandboxed file chores
(docs/specs/computer_use_first_demo.md). No LLM here.

A plan is a list of ops:
    {"op": "mkdir",      "path": "receipts/2026-01"}
    {"op": "move",       "src": "receipts/scan0001.txt", "dst": "receipts/2026-01/2026-01-14_starbucks_12.40.txt"}
    {"op": "write_file", "path": "receipts/summary.csv", "content": "vendor,count,total\\n..."}
Paths are relative to the sandbox root (absolute paths are accepted and
checked). Every op is

1. confined: realpath must stay inside the sandbox (a hard tool-level check,
   independent of the policy), no overwrite, no symlink source;
2. checked by the sink policy through the Enforcer (an ask or deny blocks);
3. dry-run first: simulate() runs 1–2 on a virtual view of the tree and
   returns every rejected op; nothing is touched;
4. executed with a write-ahead undo record, then a deterministic
   postcondition (probes-style getter + comparator). A failed postcondition
   raises and the caller rolls back.

revert(undo_log) replays the log backwards (move back with a hash check,
rmdir if empty, remove a file the run itself wrote if its hash still matches).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .enforce import Enforcer
from .policy import Action, ActionClass, Provenance


class OpError(Exception):
    pass


class PostconditionFailed(OpError):
    pass


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha(p: Path) -> str:
    return _sha_bytes(p.read_bytes())


@dataclass
class OpResult:
    index: int
    op: dict
    ok: bool
    message: str
    blocked_by: str = ""          # "", "confinement", "policy", "precondition"

    def to_dict(self) -> dict:
        return {"index": self.index, "op": _short(self.op), "ok": self.ok,
                "message": self.message, "blocked_by": self.blocked_by}


def _short(op: dict) -> dict:
    d = dict(op)
    if isinstance(d.get("content"), str) and len(d["content"]) > 120:
        d["content"] = d["content"][:120] + f"... ({len(op['content'])} chars)"
    return d


class SandboxFS:
    def __init__(self, root: Path, enforcer: Enforcer,
                 initiator: Provenance = Provenance.UNTRUSTED):
        self.root = Path(os.path.realpath(Path(root).expanduser()))
        self.enforcer = enforcer
        # The plan is derived from untrusted documents (receipt text), so by
        # default ops carry an untrusted initiator: sandbox writes pass R2,
        # anything outside is denied by R3.
        self.initiator = initiator

    # -- paths --------------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel or "\x00" in rel:
            raise OpError(f"bad path {rel!r}")
        p = Path(rel).expanduser()
        if not p.is_absolute():
            p = self.root / p
        # realpath of the deepest existing ancestor + the rest
        real = Path(os.path.realpath(p))
        if real != self.root and self.root not in real.parents:
            raise OpError(f"path {rel!r} resolves outside the sandbox")
        return real

    def rel(self, p: Path) -> str:
        return os.path.relpath(p, self.root)

    # -- policy -------------------------------------------------------------
    def _policy(self, cls: ActionClass, target: Path | str, desc: str) -> Optional[str]:
        res = self.enforcer.check_action(Action(
            cls, str(target), self.initiator, [Provenance.UNTRUSTED], desc))
        return None if res.allowed else res.message

    # -- dry run ------------------------------------------------------------
    def simulate(self, plan: List[dict]) -> List[OpResult]:
        """Check every op against a virtual view; touch nothing."""
        files: Dict[str, str] = {}      # real path -> "file"/"dir"
        for dp, dns, fns in os.walk(self.root):
            for d in dns:
                files[os.path.join(dp, d)] = "dir"
            for f in fns:
                files[os.path.join(dp, f)] = "file"
        files[str(self.root)] = "dir"
        results = []
        for i, op in enumerate(plan):
            results.append(self._check(i, op, files, apply=True))
        return results

    def _check(self, i: int, op: dict, view: Dict[str, str], apply: bool) -> OpResult:
        kind = op.get("op") if isinstance(op, dict) else None
        try:
            if kind == "mkdir":
                p = self.resolve(op.get("path", ""))
                msg = self._policy(ActionClass.WRITE_SANDBOX, p, f"mkdir {op.get('path')}")
                if msg:
                    return OpResult(i, op, False, msg, "policy")
                if view.get(str(p)) == "dir":
                    return OpResult(i, op, True, "already exists (no-op)")
                if str(p) in view:
                    return OpResult(i, op, False, "a file exists at that path", "precondition")
                if view.get(str(p.parent)) != "dir":
                    return OpResult(i, op, False, "parent directory does not exist", "precondition")
                if apply:
                    view[str(p)] = "dir"
                return OpResult(i, op, True, "ok")
            if kind == "move":
                src = Path(op.get("src", "")).expanduser()
                src_abs = src if src.is_absolute() else self.root / src
                if os.path.islink(src_abs):
                    return OpResult(i, op, False, "source is a symlink", "confinement")
                s = self.resolve(op.get("src", ""))
                d = self.resolve(op.get("dst", ""))
                msg = (self._policy(ActionClass.WRITE_SANDBOX, s, f"move-from {op.get('src')}")
                       or self._policy(ActionClass.WRITE_SANDBOX, d, f"move-to {op.get('dst')}"))
                if msg:
                    return OpResult(i, op, False, msg, "policy")
                if view.get(str(s)) != "file":
                    return OpResult(i, op, False, "source file does not exist", "precondition")
                if str(d) in view:
                    return OpResult(i, op, False, "destination already exists (no overwrite)", "precondition")
                if view.get(str(d.parent)) != "dir":
                    return OpResult(i, op, False, "destination folder does not exist", "precondition")
                if apply:
                    del view[str(s)]
                    view[str(d)] = "file"
                return OpResult(i, op, True, "ok")
            if kind == "write_file":
                p = self.resolve(op.get("path", ""))
                content = op.get("content")
                if not isinstance(content, str):
                    return OpResult(i, op, False, "content must be a string", "precondition")
                msg = self._policy(ActionClass.WRITE_SANDBOX, p, f"write {op.get('path')}")
                if msg:
                    return OpResult(i, op, False, msg, "policy")
                if str(p) in view:
                    return OpResult(i, op, False, "file already exists (no overwrite)", "precondition")
                if view.get(str(p.parent)) != "dir":
                    return OpResult(i, op, False, "parent directory does not exist", "precondition")
                if apply:
                    view[str(p)] = "file"
                return OpResult(i, op, True, "ok")
            return OpResult(i, op, False, f"unknown op {kind!r} (allowed: mkdir, move, write_file)",
                            "precondition")
        except OpError as e:
            # A path outside the sandbox is also shown to the policy so the
            # audit log records the attempt (it is denied/blocked there too).
            target = op.get("dst") or op.get("path") or op.get("src") or "" if isinstance(op, dict) else ""
            self._policy(ActionClass.WRITE_SANDBOX, str(Path(str(target)).expanduser()),
                         f"{kind} {target} (confinement)")
            return OpResult(i, op, False, str(e), "confinement")

    # -- execute ------------------------------------------------------------
    def execute(self, plan: List[dict], undo_path: Path) -> List[OpResult]:
        """Dry-run the whole plan again, refuse if any op fails, then run each
        op with a write-ahead undo record and a postcondition."""
        pre = self.simulate(plan)
        bad = [r for r in pre if not r.ok]
        if bad:
            raise OpError(f"plan rejected in dry run: {len(bad)} op(s) failed")
        undo_path = Path(undo_path)
        undo_path.parent.mkdir(parents=True, exist_ok=True)
        results = []
        for i, op in enumerate(plan):
            results.append(self._run(i, op, undo_path))
        return results

    def _log(self, undo_path: Path, rec: dict) -> None:
        rec = dict(rec, ts=round(time.time(), 3))
        with open(undo_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _run(self, i: int, op: dict, undo_path: Path) -> OpResult:
        kind = op["op"]
        if kind == "mkdir":
            p = self.resolve(op["path"])
            if p.is_dir():
                return OpResult(i, op, True, "already exists (no-op)")
            self._log(undo_path, {"op": "mkdir", "path": str(p), "state": "intent"})
            p.mkdir()
            if not p.is_dir():
                raise PostconditionFailed(f"mkdir {p}: not a directory afterwards")
            self._log(undo_path, {"op": "mkdir", "path": str(p), "state": "done"})
            return OpResult(i, op, True, "ok; postcondition is_dir holds")
        if kind == "move":
            s, d = self.resolve(op["src"]), self.resolve(op["dst"])
            h = _sha(s)
            self._log(undo_path, {"op": "move", "src": str(s), "dst": str(d), "sha256": h,
                                  "state": "intent"})
            if d.exists():
                raise PostconditionFailed(f"destination {d} appeared before move")
            os.rename(s, d)
            if s.exists() or not d.is_file() or _sha(d) != h:
                raise PostconditionFailed(f"move {s} -> {d}: postcondition failed")
            self._log(undo_path, {"op": "move", "src": str(s), "dst": str(d), "sha256": h,
                                  "state": "done"})
            return OpResult(i, op, True, "ok; postcondition src gone, dst sha256 equal")
        if kind == "write_file":
            p = self.resolve(op["path"])
            data = op["content"].encode("utf-8")
            h = _sha_bytes(data)
            self._log(undo_path, {"op": "write_file", "path": str(p), "sha256": h, "state": "intent"})
            with open(p, "xb") as f:          # exclusive: never overwrite
                f.write(data)
            if _sha(p) != h:
                raise PostconditionFailed(f"write {p}: content hash mismatch")
            self._log(undo_path, {"op": "write_file", "path": str(p), "sha256": h, "state": "done"})
            return OpResult(i, op, True, "ok; postcondition content sha256 equal")
        raise OpError(f"unknown op {kind}")


def revert(undo_path: Path, root: Path) -> List[str]:
    """Undo every op in the log, newest first. Intent records whose op did
    not complete are undone when the world shows the op happened."""
    root = Path(os.path.realpath(Path(root).expanduser()))
    undo_path = Path(undo_path)
    if not undo_path.exists():
        return []
    recs = [json.loads(l) for l in undo_path.read_text().splitlines() if l.strip()]
    # one entry per op: keep the last state for each (op, key)
    seen: Set[tuple] = set()
    ops = []
    for r in reversed(recs):
        key = (r["op"], r.get("path"), r.get("src"), r.get("dst"))
        if key in seen:
            continue
        seen.add(key)
        ops.append(r)
    notes = []

    def inside(p: str) -> bool:
        rp = Path(os.path.realpath(p))
        return rp == root or root in rp.parents

    for r in ops:
        if r["op"] == "move":
            s, d = r["src"], r["dst"]
            if not (inside(s) and inside(d)):
                notes.append(f"skip move outside sandbox {d}")
                continue
            if os.path.isfile(d) and not os.path.exists(s) and _sha(Path(d)) == r["sha256"]:
                os.rename(d, s)
                notes.append(f"moved back {os.path.relpath(d, root)} -> {os.path.relpath(s, root)}")
            elif os.path.isfile(s):
                notes.append(f"already in place {os.path.relpath(s, root)}")
            else:
                notes.append(f"CANNOT undo move {d} (missing or changed)")
        elif r["op"] == "write_file":
            p = r["path"]
            if inside(p) and os.path.isfile(p) and _sha(Path(p)) == r["sha256"]:
                os.remove(p)
                notes.append(f"removed run-written {os.path.relpath(p, root)}")
            elif os.path.exists(p):
                notes.append(f"CANNOT undo write {p} (changed since)")
        elif r["op"] == "mkdir":
            p = r["path"]
            if inside(p) and os.path.isdir(p):
                try:
                    os.rmdir(p)
                    notes.append(f"rmdir {os.path.relpath(p, root)}")
                except OSError:
                    notes.append(f"CANNOT rmdir {p} (not empty)")
    return notes


# -- reading documents ------------------------------------------------------

_PDF_STR = re.compile(rb"\((?:\\.|[^\\)])*\)\s*Tj")


def pdf_text(data: bytes) -> str:
    """Text from uncompressed PDF text-show operators (enough for simple
    generated PDFs; returns '' for anything else)."""
    out = []
    for m in _PDF_STR.finditer(data):
        s = m.group(0)
        s = s[1:s.rindex(b")")]
        s = re.sub(rb"\\([\\()])", rb"\1", s)
        out.append(s.decode("latin-1"))
    return "\n".join(out)


def read_text(p: Path, limit: int = 8000) -> str:
    data = p.read_bytes()
    if data[:5] == b"%PDF-":
        t = pdf_text(data)
        return ("[PDF text extracted]\n" + t)[:limit] if t else "[PDF: no extractable text]"
    try:
        return data.decode("utf-8")[:limit]
    except UnicodeDecodeError:
        return f"[binary file, {len(data)} bytes]"
