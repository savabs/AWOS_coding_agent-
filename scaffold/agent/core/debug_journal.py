"""Append-only persistence for native AWOS debugger bundles."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

from .debug_report import RerunOutcome
from .debug_schema import DebugEvidence, DebugFinding


Record = Union[DebugEvidence, DebugFinding, RerunOutcome]


class DebugJournal:
    """Persist one debugger session as an inspectable append-only bundle.

    Bundle layout::

        .awos/debug/<session_id>/
        ├── manifest.json
        ├── evidence.jsonl
        ├── findings.jsonl
        └── reruns.jsonl

    Existing journal records are never rewritten.  Consumers can use the JSONL
    files as an audit trail and rebuild reports from them later.
    """

    MANIFEST_NAME = "manifest.json"
    EVIDENCE_NAME = "evidence.jsonl"
    FINDINGS_NAME = "findings.jsonl"
    RERUNS_NAME = "reruns.jsonl"
    SCHEMA_VERSION = "1"

    def __init__(self, root: Path | str = ".awos/debug", session_id: str = "") -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        if Path(session_id).name != session_id or session_id in {".", ".."}:
            raise ValueError("session_id must be a single safe path component")

        self.root = Path(root)
        self.session_id = session_id
        self.bundle_dir = self.root / session_id
        self.bundle_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.bundle_dir / self.MANIFEST_NAME
        self.evidence_path = self.bundle_dir / self.EVIDENCE_NAME
        self.findings_path = self.bundle_dir / self.FINDINGS_NAME
        self.reruns_path = self.bundle_dir / self.RERUNS_NAME
        self._ensure_manifest()

    def _ensure_manifest(self) -> None:
        """Create the manifest once; never replace an existing one."""
        if self.manifest_path.exists():
            return
        manifest = {
            "schema": "awos.debug.bundle",
            "schema_version": self.SCHEMA_VERSION,
            "session_id": self.session_id,
            "created_at": time.time(),
            "files": {
                "evidence": self.EVIDENCE_NAME,
                "findings": self.FINDINGS_NAME,
                "reruns": self.RERUNS_NAME,
            },
        }
        self.manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _append(self, path: Path, record: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def append_evidence(self, evidence: DebugEvidence) -> None:
        self._append(self.evidence_path, evidence.to_dict())

    def append_finding(self, finding: DebugFinding) -> None:
        self._append(self.findings_path, finding.to_dict())

    def append_rerun(self, outcome: RerunOutcome) -> None:
        self._append(self.reruns_path, outcome.to_dict())

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL record at {path}:{line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"JSONL record at {path}:{line_number} must be an object"
                )
            records.append(value)
        return records

    def read_evidence(self) -> List[DebugEvidence]:
        return [
            DebugEvidence.from_dict(record)
            for record in self._read_jsonl(self.evidence_path)
        ]

    def read_findings(self) -> List[DebugFinding]:
        return [
            DebugFinding.from_dict(record)
            for record in self._read_jsonl(self.findings_path)
        ]

    def read_reruns(self) -> List[RerunOutcome]:
        return [
            RerunOutcome.from_dict(record)
            for record in self._read_jsonl(self.reruns_path)
        ]

    def read_manifest(self) -> Dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))
