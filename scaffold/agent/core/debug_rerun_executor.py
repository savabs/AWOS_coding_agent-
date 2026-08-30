"""Safe local execution for evidence-first debugger reruns.

This module deliberately executes no shell and never calls a model.  It turns a
validated local command into append-only rerun evidence and an explicit outcome;
callers may then pass that outcome to ``resolve_finding_after_rerun``.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from .debug_journal import DebugJournal
from .debug_report import RerunOutcome, RerunStatus
from .debug_rerun import RerunRequest
from .debug_rules import apply_proof_rules
from .debug_schema import DebugEvidence, EvidenceKind

_OUTPUT_LIMIT = 12_000
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 300
_ALLOWED_EXECUTABLES = {"python", "python3", "pytest"}
_FORBIDDEN_PYTHON_FLAGS = {"-c", "--command", "--eval", "-m=sh", "-m= bash"}


def default_environment_ref() -> str:
    """Return a stable, non-sensitive identity for the local runtime."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return f"python-{version}:{platform.system().lower()}"


def _bounded(value: str) -> str:
    if len(value) <= _OUTPUT_LIMIT:
        return value
    return value[:_OUTPUT_LIMIT] + "\n[output truncated]"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class ControlledRerunExecutor:
    """Execute a narrow, reproducible local command under recorded conditions."""

    def __init__(
        self,
        root: Path | str = ".",
        *,
        environment_ref: Optional[str] = None,
        timeout_sec: int = _DEFAULT_TIMEOUT,
        allowed_executables: Iterable[str] = _ALLOWED_EXECUTABLES,
    ) -> None:
        self.root = Path(root).resolve()
        self.environment_ref = environment_ref or default_environment_ref()
        self.timeout_sec = max(1, min(int(timeout_sec), _MAX_TIMEOUT))
        self.allowed_executables = {Path(name).name for name in allowed_executables}

    def _command_from_request(self, request: RerunRequest) -> list[str]:
        command = request.metadata.get("command")
        if not isinstance(command, (list, tuple)) or not command:
            raise ValueError("rerun metadata.command must be a non-empty argument list")
        if not all(isinstance(arg, str) and arg for arg in command):
            raise ValueError("rerun metadata.command arguments must be non-empty strings")
        return list(command)

    def _validate_command(self, command: Sequence[str]) -> None:
        executable = Path(command[0]).name
        if executable not in self.allowed_executables:
            raise ValueError(f"rerun executable is not allowlisted: {executable}")
        if any(flag in _FORBIDDEN_PYTHON_FLAGS for flag in command[1:]):
            raise ValueError("inline or shell-like Python execution is not allowed")
        if executable in {"python", "python3"}:
            if len(command) < 3 or command[1:3] != ["-m", "pytest"]:
                raise ValueError("Python reruns must use: python -m pytest ...")
        if any(token in {"&&", "||", ";", "|", ">", "<"} for token in command):
            raise ValueError("shell operators are not allowed in rerun commands")

    def _workspace(self, request: RerunRequest) -> Path:
        workspace_ref = request.workspace_ref or str(self.root)
        candidate = Path(workspace_ref)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        workspace = candidate.resolve()
        if not workspace.is_dir():
            raise ValueError(f"rerun workspace is not a directory: {workspace}")
        if not _inside(workspace, self.root):
            raise ValueError("rerun workspace must remain inside executor root")
        return workspace

    def _outcome(
        self,
        request: RerunRequest,
        status: RerunStatus,
        *,
        evidence_ids: Optional[list[str]] = None,
        started_at: Optional[float] = None,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
        journal: Optional[DebugJournal] = None,
        evidence: Optional[DebugEvidence] = None,
    ) -> RerunOutcome:
        outcome = RerunOutcome(
            rerun_id=f"rerun:{request.request_id}",
            status=status,
            source_finding_id=(request.attach_to_finding_ids[0]
                               if request.attach_to_finding_ids else None),
            evidence_ids=evidence_ids or [],
            started_at=started_at or time.time(),
            completed_at=time.time(),
            summary=summary,
            metadata={
                "source_session_id": request.source_session_id,
                "boundary_id": request.boundary_id,
                **(metadata or {}),
            },
        )
        if journal is not None:
            if journal.session_id != request.source_session_id:
                raise ValueError("journal session does not match rerun source session")
            if evidence is not None:
                journal.append_evidence(evidence)
                for finding in apply_proof_rules([evidence]):
                    journal.append_finding(finding)
            journal.append_rerun(outcome)
        return outcome

    def execute(
        self,
        request: RerunRequest,
        *,
        journal: Optional[DebugJournal] = None,
    ) -> RerunOutcome:
        """Run one request and return a persisted or standalone outcome."""
        started_at = time.time()
        rerun_id = f"rerun:{request.request_id}"

        def rejected(status: RerunStatus, message: str) -> RerunOutcome:
            evidence = DebugEvidence(
                evidence_id=f"evidence:{rerun_id}",
                kind=EvidenceKind.EVIDENCE_GAP,
                source="controlled_rerun_executor",
                observed_at=time.time(),
                source_ref=rerun_id,
                event_id=rerun_id,
                payload={
                    "normalized_event_type": "rerun_result",
                    "rerun_id": rerun_id,
                    "status": status.value,
                    "error": message,
                },
                valid=False,
            )
            return self._outcome(
                request,
                status,
                evidence_ids=[evidence.evidence_id],
                started_at=started_at,
                summary=message,
                metadata={"validation_error": message},
                journal=journal,
                evidence=evidence,
            )

        try:
            command = self._command_from_request(request)
            self._validate_command(command)
            workspace = self._workspace(request)
        except (TypeError, ValueError) as exc:
            return rejected(RerunStatus.ERROR, str(exc))

        if request.environment_ref and request.environment_ref != self.environment_ref:
            return rejected(
                RerunStatus.DIVERGENT,
                "requested environment does not match the executor environment",
            )
        if any(not isinstance(ref, str) or not ref.strip() for ref in request.frozen_input_refs):
            return rejected(RerunStatus.ERROR, "frozen_input_refs must contain non-empty references")

        expected = request.metadata.get("expected_exit_code")
        if expected is not None and (
            isinstance(expected, bool) or not isinstance(expected, int)
        ):
            return rejected(RerunStatus.ERROR, "expected_exit_code must be an integer")

        timeout = request.metadata.get("timeout_sec", self.timeout_sec)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            return rejected(RerunStatus.ERROR, "timeout_sec must be numeric")
        timeout = max(1, min(float(timeout), _MAX_TIMEOUT))

        try:
            completed = subprocess.run(
                command,
                cwd=str(workspace),
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
            returncode = completed.returncode
            stdout = _bounded(completed.stdout or "")
            stderr = _bounded(completed.stderr or "")
            status = (
                RerunStatus.INCONCLUSIVE
                if expected is None
                else (
                    RerunStatus.REPRODUCED
                    if returncode == expected
                    else RerunStatus.NOT_REPRODUCED
                )
            )
            summary = (
                f"Rerun exited with code {returncode}; expected {expected}."
                if expected is not None
                else "Rerun completed without an expected exit code; reproduction is inconclusive."
            )
            metadata = {
                "command": command,
                "workspace": str(workspace),
                "environment_ref": self.environment_ref,
                "expected_exit_code": expected,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "duration_sec": time.time() - started_at,
            }
            evidence = DebugEvidence(
                evidence_id=f"evidence:{rerun_id}",
                kind=EvidenceKind.DIRECT_OBSERVATION,
                source="controlled_rerun_executor",
                observed_at=time.time(),
                source_ref=rerun_id,
                event_id=rerun_id,
                payload={"normalized_event_type": "rerun_result", **metadata},
            )
            return self._outcome(
                request,
                status,
                evidence_ids=[evidence.evidence_id],
                started_at=started_at,
                summary=summary,
                metadata=metadata,
                journal=journal,
                evidence=evidence,
            )
        except subprocess.TimeoutExpired:
            return rejected(
                RerunStatus.INCONCLUSIVE,
                f"rerun timed out after {timeout:g}s",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return rejected(RerunStatus.ERROR, f"rerun execution failed: {exc}")
