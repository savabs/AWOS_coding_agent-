"""
placement_ledger.py — Append-only log of file placement outcomes for learning.

Tracks whether create_file tasks landed in the right folder per artifact kind.
Consumed offline or by future placement bandits.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(".awos") / "placement_ledger.jsonl"


@dataclass
class PlacementRecord:
    record_id: str
    task_id: str
    goal: str
    path: str
    artifact_kind: str
    expected_folder: str | None
    placement_ok: bool
    task_success: bool
    auto_fixed: bool = False
    fixed_from: str = ""
    issues: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlacementLedger:
    """Append-only placement outcome log."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        task_id: str,
        goal: str,
        path: str,
        artifact_kind: str,
        expected_folder: str | None,
        placement_ok: bool,
        task_success: bool,
        auto_fixed: bool = False,
        fixed_from: str = "",
        issues: list[str] | None = None,
    ) -> PlacementRecord:
        rec = PlacementRecord(
            record_id=str(uuid.uuid4()),
            task_id=str(task_id),
            goal=goal[:300],
            path=path,
            artifact_kind=artifact_kind,
            expected_folder=expected_folder,
            placement_ok=placement_ok,
            task_success=task_success,
            auto_fixed=auto_fixed,
            fixed_from=fixed_from,
            issues=list(issues or []),
        )
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict()) + "\n")
        logger.debug(
            "[placement_ledger] kind=%s path=%s ok=%s",
            artifact_kind, path, placement_ok,
        )
        return rec

    def get_recent(self, n: int = 100) -> list[PlacementRecord]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in lines[-n:]:
            try:
                d = json.loads(line)
                records.append(PlacementRecord(**{
                    k: v for k, v in d.items()
                    if k in PlacementRecord.__dataclass_fields__
                }))
            except Exception:
                continue
        return records

    def summary(self, window: int = 100) -> dict[str, Any]:
        """Per artifact_kind placement success rates."""
        recent = self.get_recent(window)
        if not recent:
            return {"total": 0, "by_kind": {}}

        by_kind: dict[str, dict[str, int]] = {}
        for r in recent:
            k = r.artifact_kind or "general"
            if k not in by_kind:
                by_kind[k] = {"count": 0, "placement_ok": 0, "task_success": 0, "auto_fixed": 0}
            by_kind[k]["count"] += 1
            by_kind[k]["placement_ok"] += int(r.placement_ok)
            by_kind[k]["task_success"] += int(r.task_success)
            by_kind[k]["auto_fixed"] += int(r.auto_fixed)

        result: dict[str, Any] = {"total": len(recent), "by_kind": {}}
        for kind, stats in sorted(by_kind.items()):
            n = stats["count"]
            result["by_kind"][kind] = {
                **stats,
                "placement_rate": round(stats["placement_ok"] / n, 3) if n else 0.0,
                "task_success_rate": round(stats["task_success"] / n, 3) if n else 0.0,
            }
        return result

    def path(self) -> Path:
        return self._path
