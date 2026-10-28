# Adapted from OpenAdapt Flow openadapt_flow/runtime/effects/compensation.py
# (reconcile-or-escalate, ReconciliationTask with digests only, "only a
# duplicate has a safe automatic undo"), Copyright (c) 2026 OpenAdapt.AI
# (MLDSAI Inc.), MIT License -- see LICENSE-OpenAdapt in this directory.
# Rewritten without pydantic; FileRestore (an exact-pre-image undo hook) is AWOS's own.
"""Reconcile-or-escalate after a refuted effect, plus undo hooks.

When a verifier does not CONFIRM a consequential write we never just carry
on. Either we reconcile -- run a compensator, then RE-verify, and only a
CONFIRMED re-verification lets the run proceed -- or we escalate: a durable
halt with a ``ReconciliationTask`` for a human.

Automatic compensation is allowed for exactly two cases:
  * a duplicate (observed > expected): remove the extras, then re-verify;
  * our own write, undone from its exact pre-image (``FileRestore``), and
    only while the file still holds exactly the bytes we wrote (nobody else
    touched it since). The effect is then gone, so the outcome is still
    RECONCILIATION_REQUIRED (the owner learns it was rolled back), but the
    file is provably back to its pre-state.
Missing, partial, collateral-loss or INDETERMINATE verdicts with no exact
pre-image always escalate: reconciliation must never invent state.

A task carries digests and counts only, never field values.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, runtime_checkable

from .outcome import EffectVerdict, Outcome, Verdict, digest


@dataclass
class ReconciliationTask:
    kind: str                 # effect_refuted | effect_indeterminate | compensation_failed | uncertain_write
    substrate: str
    reason: str
    verdict: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""
    task_id: str = field(default_factory=lambda: f"recon-{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_record(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "created_at": self.created_at, "kind": self.kind,
                "substrate": self.substrate, "verdict": self.verdict, "reason": self.reason,
                "evidence": dict(self.evidence), "suggested_action": self.suggested_action}


def record_digest(record: Dict[str, Any]) -> str:
    payload = json.dumps({k: str(v) for k, v in sorted(record.items())}, sort_keys=True,
                         separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_reconciliation_task(verdict: EffectVerdict, *, kind: Optional[str] = None) -> ReconciliationTask:
    if kind is None:
        kind = "effect_indeterminate" if verdict.verdict is Verdict.INDETERMINATE else "effect_refuted"
    if verdict.verdict is Verdict.INDETERMINATE:
        suggested = ("restore the verifier's read access, then re-verify before resuming "
                     "(never assume the write landed)")
    elif verdict.duplicated:
        suggested = ("inspect the matched records and remove the duplicate(s) this action created, "
                     "then re-verify exactly the intended record remains")
    else:
        suggested = ("inspect the target against the evidence, repair the missing/partial/collateral "
                     "state by hand, then re-verify before resuming")
    ev = {"observed_count": verdict.observed_count, "expected_count": verdict.expected_count}
    ev.update({k: v for k, v in verdict.evidence.items()
               if isinstance(v, (bool, int, float)) or (isinstance(v, str) and v.startswith("sha256:"))})
    return ReconciliationTask(kind=kind, substrate=verdict.substrate, reason=verdict.reason,
                              verdict=verdict.verdict.value, evidence=ev, suggested_action=suggested)


class CompensationOutcome(str, Enum):
    NOOP = "noop"               # already confirmed
    RECONCILED = "reconciled"   # compensated and re-verified CONFIRMED -> proceed
    ROLLED_BACK = "rolled_back" # our own write undone from its exact pre-image
    ESCALATED = "escalated"     # durable halt for a human


@dataclass
class CompensationResult:
    outcome: CompensationOutcome
    reason: str
    proceed: bool
    final_verdict: Optional[EffectVerdict] = None
    actions_taken: int = 0
    task: Optional[ReconciliationTask] = None

    @property
    def run_outcome(self) -> Outcome:
        return Outcome.VERIFIED if self.proceed else Outcome.RECONCILIATION_REQUIRED


@runtime_checkable
class Compensator(Protocol):
    """Undo hook. Must report failure by returning False, never raise."""

    def undo(self, verdict: EffectVerdict) -> bool: ...


@dataclass
class FileRestore:
    """Undo one file write from its exact pre-image, hash-guarded.

    Restores only while the file still has ``written_sha256`` (the bytes our
    write produced); if anyone changed it since, it refuses (returns False).
    ``pre_bytes=None`` means the file did not exist before: undo removes it.
    """
    path: Path
    pre_bytes: Optional[bytes]
    pre_mode: int
    written_sha256: str

    def undo(self, verdict: Optional[EffectVerdict] = None) -> bool:
        p = Path(self.path)
        try:
            if not p.exists() or digest(p.read_bytes()) != self.written_sha256:
                return False
            if self.pre_bytes is None:
                p.unlink()
                return True
            fd, tmp = tempfile.mkstemp(prefix=".awos-undo-", dir=str(p.parent))
            os.fchmod(fd, stat.S_IMODE(self.pre_mode) or 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(self.pre_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            return True
        except OSError:
            return False


def reconcile_or_escalate(verdict: EffectVerdict, *, reverify: Callable[[], EffectVerdict],
                          compensator: Optional[Compensator] = None,
                          own_write_undo: Optional[FileRestore] = None) -> CompensationResult:
    """Reconcile a non-confirmed effect, or escalate.

    ``reverify`` re-runs the verifier against the ORIGINAL pre-state.
    ``compensator`` is tried only for duplicates. ``own_write_undo`` rolls
    back our own write (any REFUTED verdict) when the file is untouched since.
    """
    if verdict.confirmed:
        return CompensationResult(CompensationOutcome.NOOP, "effect already confirmed", True, verdict)
    if verdict.verdict is Verdict.INDETERMINATE:
        return _escalate(verdict, "verifier could not read the effect; cannot compensate blindly")
    if verdict.duplicated and compensator is not None:
        if not _safe_undo(compensator, verdict):
            return _escalate(verdict, "compensation failed", kind="compensation_failed", actions=1)
        again = reverify()
        if again.confirmed:
            return CompensationResult(CompensationOutcome.RECONCILED,
                                      "removed duplicate(s); re-verification confirms the intended state",
                                      True, again, actions_taken=1)
        return _escalate(again, "compensation did not restore the intended state",
                         kind="compensation_failed", actions=1)
    if own_write_undo is not None:
        if own_write_undo.undo(verdict):
            res = _escalate(verdict, "own write rolled back from its exact pre-image; owner must review",
                            actions=1)
            res.outcome = CompensationOutcome.ROLLED_BACK
            return res
        return _escalate(verdict, "rollback refused (file changed since our write)",
                         kind="compensation_failed")
    return _escalate(verdict, "refuted effect has no safe automatic compensation")


def _safe_undo(c: Compensator, v: EffectVerdict) -> bool:
    try:
        return bool(c.undo(v))
    except Exception:
        return False


def _escalate(verdict: EffectVerdict, reason: str, *, kind: Optional[str] = None,
              actions: int = 0) -> CompensationResult:
    return CompensationResult(CompensationOutcome.ESCALATED, reason, False, verdict, actions,
                              build_reconciliation_task(verdict, kind=kind))
