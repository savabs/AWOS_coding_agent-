"""
layout.py — read Online-Mind2Web judge-layout episodes (the official submission layout, docs/specs/web_judge.md).

One episode = one directory:

    <task_id>/
        result.json      v1: {"task_id", "task", "final_result_response", "action_history": [str], "thoughts": [str]}
                         v2: {"schema_version": "online-mind2web-v2", "task_id", "task", "agent_final_answer",
                              "action_history": [{"step", "screenshot", "action", "thought", ...}]}
        trajectory/      screenshots. v1 order = first integer in the file name (upstream run.py);
                         v2 order = action_history[*].screenshot

AWOS additions, all optional (ignored by the official judge): "level" (easy|medium|hard), "harness_status"
(ok|blocked|infra_error) and "harness_note". find_episodes() walks a run directory recursively, because CI
artifacts nest the task folders under artifact names.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

V2 = "online-mind2web-v2"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
LEVELS = ("easy", "medium", "hard")

# harness_status values -> one of ok | blocked | infra_error. Unknown values are a layout error.
STATUS_ALIASES = {
    "ok": "ok", "done": "ok", "completed": "ok", "": "ok",
    "blocked": "blocked", "captcha": "blocked", "bot_wall": "blocked", "login_wall": "blocked",
    "infra_error": "infra_error", "infra": "infra_error", "infra_failed": "infra_error",
    "error": "infra_error", "crash": "infra_error", "timeout_infra": "infra_error",
}


class LayoutError(ValueError):
    pass


@dataclass
class Episode:
    task_id: str
    task: str
    path: Path
    schema: str                                  # "v1" | "v2"
    action_history: List[str]
    screenshots: List[Path]
    thoughts: Optional[List[Optional[str]]] = None
    final_result_response: Optional[str] = None
    level: Optional[str] = None
    harness_status: str = "ok"
    harness_note: str = ""
    extra: Dict = field(default_factory=dict)

    @property
    def judgeable(self) -> bool:
        return self.harness_status == "ok"


def _first_int(name: str) -> int:
    m = re.findall(r"\d+", name)
    if not m:
        raise LayoutError(f"screenshot {name!r} has no number in its name (v1 orders by the first integer)")
    return int(m[0])


def v1_screenshots(traj: Path) -> List[Path]:
    """Upstream run.py: sorted(os.listdir(dir), key=first integer). Non-image files are skipped (deviation 3)."""
    if not traj.is_dir():
        return []
    files = [p for p in traj.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]
    return sorted(files, key=lambda p: _first_int(p.name))


def normalize_status(v) -> str:
    s = str(v if v is not None else "").strip().lower()
    if s not in STATUS_ALIASES:
        raise LayoutError(f"unknown harness_status {v!r} (want ok | blocked | infra_error)")
    return STATUS_ALIASES[s]


def load_episode(d: Path, levels: Optional[Dict[str, str]] = None) -> Episode:
    d = Path(d)
    rp = d / "result.json"
    try:
        result = json.loads(rp.read_text())
    except (OSError, ValueError) as e:
        raise LayoutError(f"{rp}: {e}") from None
    if not isinstance(result, dict) or not isinstance(result.get("task"), str):
        raise LayoutError(f"{rp}: needs a 'task' string")
    task_id = str(result.get("task_id") or d.name)
    status = normalize_status(result.get("harness_status", "ok"))
    traj = d / "trajectory"
    if result.get("schema_version") == V2:
        steps = result.get("action_history") or []
        if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
            raise LayoutError(f"{rp}: v2 action_history must be a list of step objects")
        actions = [str(s.get("action", "")) for s in steps]
        thoughts = [s.get("thought") for s in steps]
        shots = [traj / s["screenshot"] for s in steps if s.get("screenshot")]   # null -> skipped (deviation 3)
        final = result.get("agent_final_answer")
        schema = "v2"
    else:
        actions = result.get("action_history") or []
        if not isinstance(actions, list):
            raise LayoutError(f"{rp}: v1 action_history must be a list of strings")
        actions = [str(a) for a in actions]
        thoughts = result.get("thoughts")
        final = result.get("final_result_response")
        shots = v1_screenshots(traj)
        schema = "v1"
    missing = [p.name for p in shots if not p.is_file()]
    if missing and status == "ok":
        raise LayoutError(f"{d}: screenshots listed but missing: {missing[:5]}")
    shots = [p for p in shots if p.is_file()]
    level = result.get("level") or (levels or {}).get(task_id)
    level = str(level).lower() if level else None
    known = {"task_id", "task", "action_history", "thoughts", "final_result_response", "schema_version",
             "agent_final_answer", "level", "harness_status", "harness_note"}
    return Episode(task_id=task_id, task=result["task"], path=d, schema=schema, action_history=actions,
                   screenshots=shots, thoughts=thoughts, final_result_response=final, level=level,
                   harness_status=status, harness_note=str(result.get("harness_note") or ""),
                   extra={k: v for k, v in result.items() if k not in known})


def find_episodes(root: Path, levels: Optional[Dict[str, str]] = None) -> List[Episode]:
    """Every directory under root (root included) holding a result.json with a 'task'. Sorted by task_id,
    as upstream sorts task dirs. Duplicate task_ids inside one run are an error."""
    root = Path(root)
    if not root.is_dir():
        raise LayoutError(f"{root}: not a directory")
    eps: List[Episode] = []
    for rp in sorted(root.rglob("result.json")):
        if "trajectory" in rp.parent.parts[len(root.parts):]:
            continue
        eps.append(load_episode(rp.parent, levels))
    seen: Dict[str, Path] = {}
    for e in eps:
        if e.task_id in seen:
            raise LayoutError(f"task {e.task_id} appears twice: {seen[e.task_id]} and {e.path}")
        seen[e.task_id] = e.path
    return sorted(eps, key=lambda e: e.task_id)


def load_levels(path: Optional[Path]) -> Dict[str, str]:
    """task_id -> level from the Online-Mind2Web task file (a list of {"task_id", "level", ...}) or a
    {task_id: level} map."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        return {str(k): str(v).lower() for k, v in data.items()}
    return {str(r["task_id"]): str(r["level"]).lower() for r in data if r.get("task_id") and r.get("level")}


def image_size(path: Path):
    """(width, height) of a PNG / JPEG / WebP without Pillow, or None. Used only for the cost estimate."""
    try:
        with open(path, "rb") as fh:
            return image_size_fh(fh)
    except OSError:
        return None


def image_size_bytes(data: bytes):
    import io
    return image_size_fh(io.BytesIO(data))


def image_size_fh(fh):
    head = fh.read(32)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return _webp_size(head, fh)
    if head[:2] == b"\xff\xd8":
        return _jpeg_size(fh)
    return None


def _webp_size(head: bytes, fh):
    fh.seek(12)
    chunk = fh.read(30)
    kind = chunk[:4]
    if kind == b"VP8X":
        return 1 + int.from_bytes(chunk[12:15], "little"), 1 + int.from_bytes(chunk[15:18], "little")
    if kind == b"VP8 ":
        return int.from_bytes(chunk[14:16], "little") & 0x3FFF, int.from_bytes(chunk[16:18], "little") & 0x3FFF
    if kind == b"VP8L":
        b = int.from_bytes(chunk[9:13], "little")
        return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    return None


def _jpeg_size(fh):
    fh.seek(2)
    while True:
        b = fh.read(1)
        while b and b != b"\xff":
            b = fh.read(1)
        while b == b"\xff":
            b = fh.read(1)
        if not b:
            return None
        marker = b[0]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        seg = fh.read(2)
        if len(seg) < 2:
            return None
        n = int.from_bytes(seg, "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            data = fh.read(5)
            return int.from_bytes(data[3:5], "big"), int.from_bytes(data[1:3], "big")
        fh.seek(n - 2, 1)
