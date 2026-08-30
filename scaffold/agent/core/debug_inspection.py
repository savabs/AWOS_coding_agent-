"""Read-only inspection API for saved AWOS debugger bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .debug_report import DebugReport, ReportScope, RerunOutcome
from .debug_schema import DebugEvidence, DebugFinding


def _safe_bundle_path(root: Path | str, session_id: str) -> Path:
    if not session_id or Path(session_id).name != session_id:
        raise ValueError("session_id must be a single safe path component")
    path = Path(root) / session_id
    if not path.is_dir():
        raise FileNotFoundError(f"debug bundle not found: {path}")
    return path


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL record at {path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record at {path}:{line_number} must be an object")
        records.append(record)
    return records


def inspect_debug_bundle(root: Path | str, session_id: str) -> DebugReport:
    """Load a saved bundle without creating or modifying any files."""
    bundle = _safe_bundle_path(root, session_id)
    manifest_path = bundle / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"debug bundle manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    findings = [
        DebugFinding.from_dict(record)
        for record in _read_jsonl(bundle / "findings.jsonl")
    ]
    reruns = [
        RerunOutcome.from_dict(record)
        for record in _read_jsonl(bundle / "reruns.jsonl")
    ]
    evidence = [
        DebugEvidence.from_dict(record)
        for record in _read_jsonl(bundle / "evidence.jsonl")
    ]
    return DebugReport(
        report_id=f"report:{session_id}",
        scope=ReportScope.SESSION,
        session_id=session_id,
        created_at=float(manifest.get("created_at", 0.0)),
        findings=findings,
        artifact_refs=[str(path.name) for path in bundle.iterdir() if path.is_file()],
        rerun_outcomes=reruns,
        metadata={
            "manifest": manifest,
            "evidence_count": len(evidence),
            "bundle_path": str(bundle),
        },
    )
