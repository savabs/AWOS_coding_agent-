"""files.py — downloads watcher, dedupe, filing and the collected-files ledger.

    before = snapshot(downloads)                 # just before a source's skill runs
    ... skill clicks "Download" ...
    got = collect(downloads, before, root, period, source, ledger)
    got.filed        # FiledDoc list, moved to Pack/<YYYY-MM>/<source-id>/<rendered name>
    got.duplicates   # same bytes already filed (this month or an earlier one) -- not filed again
    got.pending      # partial downloads that never finished within wait_s

Rules:
  * only entries that are new since ``before`` (or whose size/mtime changed) count;
  * partial downloads (.crdownload, .download, .part, ...) and hidden files are ignored,
    and collection waits until no partial is left and every new file kept its size for
    ``settle_s`` (or ``wait_s`` runs out -> the unfinished ones are reported as pending);
  * identity is the sha256 of the bytes; a sha already in the ledger is never filed twice;
  * a file is MOVED into the pack and never overwrites anything (`` (2)`` suffixes);
  * the ledger (``Pack/ledger.jsonl``) is append-only, one JSON object per line.
Files that are not filed (duplicates, wrong type is still filed) stay in Downloads: the
pack never deletes the owner's files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

PARTIAL_SUFFIXES = (".crdownload", ".download", ".part", ".partial", ".opdownload", ".tmp")
LEDGER_NAME = "ledger.jsonl"
MAX_NAME = 150
_UNSAFE = re.compile(r"[^A-Za-z0-9._()+ -]+")

Snapshot = Dict[str, List[float]]    # name -> [size, mtime]   (JSON-serialisable; stored in run state)


def is_partial(name: str) -> bool:
    return name.lower().endswith(PARTIAL_SUFFIXES)


def is_ignored(name: str) -> bool:
    return name.startswith(".") or name.startswith("~$") or is_partial(name)


def snapshot(downloads: Path) -> Snapshot:
    """Every top-level entry (files AND partial bundles) with its size and mtime."""
    out: Snapshot = {}
    d = Path(downloads)
    if not d.is_dir():
        return out
    for p in d.iterdir():
        try:
            st = p.stat()
        except OSError:
            continue
        out[p.name] = [st.st_size if p.is_file() else -1, round(st.st_mtime, 3)]
    return out


def _new_entries(downloads: Path, before: Snapshot) -> Tuple[Dict[str, Tuple[int, float]], List[str]]:
    files: Dict[str, Tuple[int, float]] = {}
    partials: List[str] = []
    for name, (size, mtime) in snapshot(downloads).items():
        if before.get(name) == [size, mtime]:
            continue
        if is_partial(name):
            partials.append(name)
        elif is_ignored(name) or size < 0:
            continue
        else:
            files[name] = (int(size), mtime)
    return files, sorted(partials)


def wait_for_downloads(downloads: Path, before: Snapshot, *, settle_s: float = 2.0, wait_s: float = 120.0,
                       sleep: Callable[[float], None] = time.sleep,
                       clock: Callable[[], float] = time.monotonic) -> Tuple[List[Path], List[str]]:
    """(finished new files oldest first, names still unfinished when wait_s ran out)."""
    deadline = clock() + max(0.0, wait_s)
    step = max(0.05, settle_s)
    prev: Optional[Dict[str, Tuple[int, float]]] = None
    while True:
        cur, partials = _new_entries(downloads, before)
        if not partials and (settle_s <= 0 or prev == cur):
            break
        if clock() >= deadline:
            stable = {n: v for n, v in cur.items() if prev is not None and prev.get(n) == v}
            pending = partials + sorted(n for n in cur if n not in stable)
            return _order(downloads, stable), pending
        prev = cur
        sleep(step)
    return _order(downloads, cur), []


def _order(downloads: Path, entries: Dict[str, Tuple[int, float]]) -> List[Path]:
    return [Path(downloads) / n for n, _ in sorted(entries.items(), key=lambda kv: (kv[1][1], kv[0]))]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    name = _UNSAFE.sub("_", name.replace("/", "_").replace("\\", "_")).strip(" ._") or "document"
    while ".." in name:
        name = name.replace("..", ".")
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:MAX_NAME - len(ext) - 1] + "." + ext) if dot and len(ext) <= 8 else name[:MAX_NAME]
    return name


def file_ext(path: Path) -> str:
    return Path(path).suffix.lower().lstrip(".") or "bin"


def render_name(template: str, *, source_id: str, kind: str, period: str, original: str, index: int,
                expected_count: int, sha: str) -> str:
    """Fields: {source} {kind} {period} {yyyy} {mm} {name} {ext} {n} {sha8}.
    {n} is "" for the only expected file, else "_1", "_2", ..."""
    o = Path(original)
    vals = {"source": source_id, "kind": kind, "period": period, "yyyy": period[:4], "mm": period[5:7],
            "name": o.stem, "ext": file_ext(o), "sha8": sha[:8],
            "n": "" if expected_count == 1 and index == 1 else f"_{index}"}
    out = re.sub(r"\{([a-z0-9]+)\}", lambda m: str(vals.get(m.group(1), m.group(0))), template)
    return safe_name(out)


def unique_path(dest_dir: Path, name: str) -> Path:
    p = dest_dir / name
    if not p.exists():
        return p
    stem, suffix = Path(name).stem, Path(name).suffix
    k = 2
    while (dest_dir / f"{stem} ({k}){suffix}").exists():
        k += 1
    return dest_dir / f"{stem} ({k}){suffix}"


def place(src: Path, dest_dir: Path, name: str) -> Path:
    """Move ``src`` to ``dest_dir/name`` without ever overwriting (race-safe: link / O_EXCL)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(1000):
        dest = unique_path(dest_dir, name)
        try:
            os.link(src, dest)                      # fails if dest appeared meanwhile
        except FileExistsError:
            continue
        except OSError:                             # other volume / no hard links: exclusive copy
            try:
                with open(src, "rb") as fi, open(dest, "xb") as fo:
                    shutil.copyfileobj(fi, fo)
                shutil.copystat(src, dest)
            except FileExistsError:
                continue
        os.unlink(src)
        return dest
    raise OSError(f"could not find a free name for {name} in {dest_dir}")


# ── ledger ──────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    """Append-only JSONL of every file the pack collected (``Pack/ledger.jsonl``).

    Record: {ts, event: filed|adopted|duplicate, month, source, sha256, size, original, path}
    ``path`` is relative to the pack root."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / LEDGER_NAME

    def append(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        rec = {"ts": _now(), **rec}
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return rec

    def records(self) -> Iterator[Dict[str, Any]]:
        if not self.path.is_file():
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue                    # a torn last line after a crash: skip it

    def stored(self) -> Dict[str, Dict[str, Any]]:
        """sha256 -> the record of the file holding those bytes in the pack."""
        out: Dict[str, Dict[str, Any]] = {}
        for r in self.records():
            if r.get("event") in ("filed", "adopted") and r.get("sha256") not in out:
                out[r["sha256"]] = r
        return out

    def paths(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for r in self.records():
            if r.get("event") in ("filed", "adopted"):
                out[r["path"]] = r
        return out


# ── collection ──────────────────────────────────────────────────────────────
@dataclass
class FiledDoc:
    path: str              # relative to the pack root
    sha256: str
    size: int
    original: str
    type_ok: bool
    event: str = "filed"   # filed | adopted

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size, "original": self.original,
                "type_ok": self.type_ok, "event": self.event}


@dataclass
class Collected:
    filed: List[FiledDoc] = field(default_factory=list)
    duplicates: List[Dict[str, Any]] = field(default_factory=list)   # {original, sha256, of, month, source, same_slot}
    pending: List[str] = field(default_factory=list)


def month_dir(root: Path, month: str, source_id: str) -> Path:
    return Path(root) / month / source_id


def collect(downloads: Path, before: Snapshot, root: Path, month: str, source: Any, ledger: Ledger, *,
            already: int = 0, settle_s: float = 2.0, wait_s: float = 120.0,
            sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Collected:
    """File every finished new download for ``source`` into Pack/<month>/<source.id>/.

    ``already`` = files this (month, source) already holds, so {n} keeps counting."""
    out = Collected()
    new, out.pending = wait_for_downloads(downloads, before, settle_s=settle_s, wait_s=wait_s,
                                          sleep=sleep, clock=clock)
    stored = ledger.stored()
    dest_dir = month_dir(root, month, source.id)
    index = already
    for src in new:
        try:
            sha = sha256_file(src)
            size = src.stat().st_size
        except OSError:
            out.pending.append(src.name)
            continue
        if sha in stored:
            r = stored[sha]
            dup = {"original": src.name, "sha256": sha, "of": r.get("path"), "month": r.get("month"),
                   "source": r.get("source"), "same_slot": r.get("month") == month and r.get("source") == source.id}
            out.duplicates.append(dup)
            ledger.append({"event": "duplicate", "month": month, "source": source.id, "sha256": sha, "size": size,
                           "original": src.name, "path": r.get("path")})
            continue
        index += 1
        name = render_name(source.naming, source_id=source.id, kind=source.kind, period=month,
                           original=src.name, index=index, expected_count=source.expected.count, sha=sha)
        dest = place(src, dest_dir, name)
        rel = str(dest.relative_to(root))
        doc = FiledDoc(rel, sha, size, src.name, file_ext(src) in source.expected.types)
        ledger.append({"event": "filed", "month": month, "source": source.id, "sha256": sha, "size": size,
                       "original": src.name, "path": rel})
        stored[sha] = {"path": rel, "month": month, "source": source.id}
        out.filed.append(doc)
    return out


def adopt_manual(root: Path, month: str, source: Any, ledger: Ledger) -> Collected:
    """Files the owner dropped into Pack/<month>/<source>/ by hand join the pack (ledger: adopted)."""
    out = Collected()
    d = month_dir(root, month, source.id)
    if not d.is_dir():
        return out
    known = ledger.paths()
    stored = ledger.stored()
    for p in sorted(d.iterdir()):
        if not p.is_file() or is_ignored(p.name):
            continue
        rel = str(p.relative_to(root))
        if rel in known:
            continue
        sha = sha256_file(p)
        if sha in stored and stored[sha].get("path") != rel:
            r = stored[sha]
            out.duplicates.append({"original": p.name, "sha256": sha, "of": r.get("path"), "month": r.get("month"),
                                   "source": r.get("source"),
                                   "same_slot": r.get("month") == month and r.get("source") == source.id})
            continue
        size = p.stat().st_size
        ledger.append({"event": "adopted", "month": month, "source": source.id, "sha256": sha, "size": size,
                       "original": p.name, "path": rel})
        stored[sha] = {"path": rel, "month": month, "source": source.id}
        out.filed.append(FiledDoc(rel, sha, size, p.name, file_ext(p) in source.expected.types, "adopted"))
    return out
