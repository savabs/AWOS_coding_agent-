"""
source.py — Online-Mind2Web (300 tasks, 136 real websites) as a contract.TaskSource.

    src = OnlineMind2WebSource()                  # AWOS_OM2W_JSON, the cache, or a download
    src = OnlineMind2WebSource(path="tasks.json") # any file in the Online_Mind2Web.json shape
    tasks = list(src.tasks("dev"))                # "all" (300) | "dev" (the fixed 100)

Data: HF dataset osunlp/Online-Mind2Web (gated, auto-approve; CC-BY-4.0), file
Online_Mind2Web.json, pinned to DATASET_REVISION. Downloaded with the HF_TOKEN env var (sent
only to huggingface.co, never printed or written anywhere) into
$AWOS_RL_CACHE/online-mind2web/<revision>/Online_Mind2Web.json (default ~/.cache/awos-rl).

One TaskSpec per entry: id = task_id, goal = confirmed_task, start = website (absolute URL),
family = the site's host, tags = [level], meta = {level, reference_length, website}.

Splits:
    all   every task in the file
    dev   a fixed 100-task subset, stratified by level (easy / medium / hard in proportion,
          largest remainder), seeded (DEV_SEED). The ids are committed in data/dev_ids.json
          (``python -m scaffold.rl.web freeze-dev`` writes it); when that file is still empty
          the same deterministic selection is computed from the pinned dataset and the run
          records ``dev_ids_source = "computed"``.

There is no executable verifier on the live web: ``outcome`` returns success=False with
notes "unjudged"; success comes from the WebJudge pass over the judge layout (see
scaffold/rl/web/episode.py). ``oracle`` only exists for local test tasks (meta["oracle"]).
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from scaffold.rl.contract import Score, TaskSpec, Trajectory

DATASET = "osunlp/Online-Mind2Web"
DATASET_FILE = "Online_Mind2Web.json"
DATASET_REVISION = "eacad896a84dc5b65e29b0b06e4699ab0544d701"     # HF api sha, 2026-10-25
DEV_SIZE = 100
DEV_SEED = "online-mind2web-dev-v1"
LEVELS = ("easy", "medium", "hard")
DATA_DIR = Path(__file__).resolve().parent / "data"
DEV_IDS = DATA_DIR / "dev_ids.json"
MAX_STEPS = 25


def cache_path(revision: str = DATASET_REVISION) -> Path:
    root = Path(os.environ.get("AWOS_RL_CACHE", "~/.cache/awos-rl")).expanduser()
    return root / "online-mind2web" / revision / DATASET_FILE


def download(dest: Optional[Path] = None, revision: str = DATASET_REVISION, token: Optional[str] = None,
             timeout: float = 60.0) -> Path:
    """Fetch Online_Mind2Web.json from the gated HF dataset with HF_TOKEN. The token goes in an
    unredirected Authorization header (never forwarded to the CDN the resolve URL redirects to)."""
    dest = Path(dest) if dest else cache_path(revision)
    token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        raise RuntimeError(f"{DATASET} is gated: set HF_TOKEN (auto-approved access) or pass a local "
                           f"--tasks-json / AWOS_OM2W_JSON file")
    url = f"https://huggingface.co/datasets/{DATASET}/resolve/{revision}/{DATASET_FILE}"
    req = urllib.request.Request(url)
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception as e:                        # the message never contains the token
        raise RuntimeError(f"download of {DATASET}/{DATASET_FILE}@{revision[:12]} failed: "
                           f"{type(e).__name__}: {getattr(e, 'code', '') or e}"[:300]) from None
    json.loads(raw)                               # must be JSON before it is cached
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(raw)
    tmp.replace(dest)
    return dest


def resolve_path(path: Optional[str] = None, fetch: bool = True) -> Path:
    for cand in (path, os.environ.get("AWOS_OM2W_JSON")):
        if cand:
            p = Path(cand).expanduser()
            if not p.is_file():
                raise FileNotFoundError(f"task file not found: {p}")
            return p
    p = cache_path()
    if p.is_file():
        return p
    if not fetch:
        raise FileNotFoundError(f"{p} missing (set HF_TOKEN to download, or pass a task file)")
    return download(p)


def _start_url(website: str) -> str:
    w = (website or "").strip()
    if w and "://" not in w:
        w = "https://" + w
    return w


def load_entries(path: Path) -> List[dict]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):                    # tolerate {"data": [...]} / {"tasks": [...]}
        data = data.get("data") or data.get("tasks") or list(data.values())
    out = []
    for d in data:
        tid = str(d.get("task_id") or d.get("id") or "").strip()
        goal = str(d.get("confirmed_task") or d.get("task") or "").strip()
        if not tid or not goal:
            continue
        out.append(d)
    return out


def to_spec(d: dict, split: str) -> TaskSpec:
    level = str(d.get("level") or "").strip().lower() or "unknown"
    website = _start_url(str(d.get("website") or d.get("url") or ""))
    host = urllib.parse.urlsplit(website).hostname or "?"
    meta = {"level": level, "website": website}
    if d.get("reference_length") is not None:
        try:
            meta["reference_length"] = int(d["reference_length"])
        except (TypeError, ValueError):
            pass
    for k in ("oracle", "script"):                # local test tasks only
        if k in d:
            meta[k] = d[k]
    return TaskSpec(id=str(d.get("task_id") or d.get("id")).strip(), source="online-mind2web",
                    family=host[4:] if host.startswith("www.") else host, split=split,
                    goal=str(d.get("confirmed_task") or d.get("task")).strip(), start=website, kind="act",
                    tags=[level], allow_writes=True, max_steps=MAX_STEPS, meta=meta)


def select_dev(entries: List[dict], size: int = DEV_SIZE, seed: str = DEV_SEED) -> List[str]:
    """Deterministic stratified sample: per level, a quota proportional to its share
    (largest remainder), drawn with random.Random(f"{seed}:{level}") from the sorted ids.
    Returned sorted, so the set (not the order of the source file) defines the split."""
    by_level: Dict[str, List[str]] = {}
    for d in entries:
        lv = str(d.get("level") or "").strip().lower() or "unknown"
        by_level.setdefault(lv, []).append(str(d.get("task_id") or d.get("id")).strip())
    total = sum(len(v) for v in by_level.values())
    size = min(size, total)
    if not total:
        return []
    exact = {lv: size * len(ids) / total for lv, ids in by_level.items()}
    quota = {lv: int(x) for lv, x in exact.items()}
    left = size - sum(quota.values())
    for lv in sorted(exact, key=lambda k: (-(exact[k] - quota[k]), k))[:left]:
        quota[lv] += 1
    out: List[str] = []
    for lv in sorted(by_level):
        ids = sorted(set(by_level[lv]))
        out += random.Random(f"{seed}:{lv}").sample(ids, min(quota[lv], len(ids)))
    return sorted(out)


def committed_dev_ids(path: Path = DEV_IDS) -> List[str]:
    if not path.is_file():
        return []
    return list(json.loads(path.read_text()).get("ids") or [])


def freeze_dev(entries: List[dict], path: Path = DEV_IDS, source_file: str = "") -> dict:
    ids = select_dev(entries)
    levels = {str(d.get("task_id") or d.get("id")).strip(): str(d.get("level") or "").strip().lower()
              for d in entries}
    doc = {"dataset": DATASET, "file": DATASET_FILE, "revision": DATASET_REVISION, "seed": DEV_SEED,
           "size": len(ids), "by_level": dict(sorted(Counter(levels.get(i, "?") for i in ids).items())),
           "source_file": source_file, "ids": ids}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


class OnlineMind2WebSource:
    """contract.TaskSource over the live web (no loopback server: ``start`` returns "")."""

    name = "online-mind2web"

    def __init__(self, path: Optional[str] = None, fetch: bool = True, dev_ids_path: Optional[str] = None):
        self.path = resolve_path(path, fetch=fetch)
        self.entries = load_entries(self.path)
        self.dev_ids_path = Path(dev_ids_path) if dev_ids_path else DEV_IDS
        self.dev_ids_source = ""

    def dev_ids(self) -> List[str]:
        ids = committed_dev_ids(self.dev_ids_path)
        if ids:
            known = {str(d.get("task_id") or d.get("id")).strip() for d in self.entries}
            missing = [i for i in ids if i not in known]
            if missing:
                raise ValueError(f"{len(missing)} committed dev ids are not in {self.path.name} "
                                 f"(e.g. {missing[0]}): wrong dataset revision?")
            self.dev_ids_source = f"committed:{self.dev_ids_path.name}"
            return ids
        self.dev_ids_source = "computed"
        print(f"note: {self.dev_ids_path} has no ids yet; using the deterministic selection "
              f"(seed {DEV_SEED}); run `python -m scaffold.rl.web freeze-dev` and commit it", file=sys.stderr)
        return select_dev(self.entries)

    def tasks(self, split: Optional[str] = None) -> Iterable[TaskSpec]:
        split = split or "all"
        if split == "all":
            return [to_spec(d, "all") for d in self.entries]
        if split == "dev":
            keep = set(self.dev_ids())
            return [to_spec(d, "dev") for d in self.entries
                    if str(d.get("task_id") or d.get("id")).strip() in keep]
        raise ValueError(f"unknown split {split!r} (all | dev)")

    def start(self) -> str:
        return ""

    def stop(self) -> None:
        pass

    def reset(self, task: TaskSpec) -> str:
        return task.start

    def outcome(self, task: TaskSpec, traj: Trajectory) -> Score:
        h = next((e for e in reversed(traj.events) if e.get("event") == "harness"), {})
        b = h.get("blocked")
        return Score(success=False, notes=f"blocked:{b['kind']}" if b else "unjudged (WebJudge scores offline)")

    def oracle(self, task: TaskSpec) -> List[dict]:
        if "oracle" in task.meta:
            return list(task.meta["oracle"])
        raise NotImplementedError("Online-Mind2Web has no scripted oracle (live websites)")
