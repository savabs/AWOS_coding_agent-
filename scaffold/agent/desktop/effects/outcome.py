# Portions adapted from OpenAdapt Flow (openadapt_flow/runtime/effects/effect.py,
# openadapt_flow/verification.py, openadapt_types/check_level.py and
# openadapt_types/execute.py), Copyright (c) 2026 OpenAdapt.AI (MLDSAI Inc.),
# MIT License -- see LICENSE-OpenAdapt in this directory.
"""Verdicts, check levels and the closed run-outcome enum for effect checks.

An effect verifier answers one question after a side-effecting action: "is
the intended change actually there, exactly once, with nothing else
damaged?" -- read from the system of record (the file, the dashboard's key
list), not from what the agent believes it did.

Verdict (per effect):
    CONFIRMED      the effect is present and correct -> proceed
    REFUTED        the system of record contradicts it (missing, duplicated,
                   wrong value, collateral loss) -> halt
    INDETERMINATE  the system of record could not be read -> halt (never
                   assume success)

Outcome (per run / operation; a CLOSED set, nothing else is ever reported):
    VERIFIED                 the effect happened and a verifier confirmed it
    HALTED_BEFORE_EFFECT     stopped before anything changed (policy block,
                             owner said no, precondition failed)
    RECONCILIATION_REQUIRED  something may have changed and it is not proven
                             correct (refuted, unreadable, uncertain write);
                             a human must look; never retried blindly
    COMPLETED_UNVERIFIED     the action ran but no verifier strong enough was
                             available; reported honestly, never as VERIFIED

Verdicts never carry secret values: evidence holds counts, booleans and
one-way digests only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    INDETERMINATE = "indeterminate"


class CheckLevel(IntEnum):
    """How strongly a save was checked (higher is stronger)."""
    UNCHECKED = 0
    SAME_SCREEN = 1          # the acting screen still shows the value
    REOPENED = 2             # the acting session re-navigated and re-read
    SEPARATE_SESSION = 3     # a fresh, separately signed-in read-only session
    SYSTEM_OF_RECORD = 4     # the store itself (the file, an API, a DB)


class Outcome(str, Enum):
    VERIFIED = "verified"
    HALTED_BEFORE_EFFECT = "halted_before_effect"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    COMPLETED_UNVERIFIED = "completed_unverified"

    @property
    def may_have_changed(self) -> bool:
        return self is not Outcome.HALTED_BEFORE_EFFECT


@dataclass(frozen=True)
class EffectVerdict:
    verdict: Verdict
    substrate: str
    reason: str
    check_level: CheckLevel = CheckLevel.UNCHECKED
    observed_count: Optional[int] = None
    expected_count: Optional[int] = None
    #: True only when the verifier affirmatively proved the effect is ABSENT
    #: and nothing else changed: the one state in which a retry is safe.
    absent: bool = False
    #: digests / counts / booleans only -- never a field value
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def confirmed(self) -> bool:
        return self.verdict is Verdict.CONFIRMED

    @property
    def should_halt(self) -> bool:
        return self.verdict is not Verdict.CONFIRMED

    @property
    def duplicated(self) -> bool:
        return (self.verdict is Verdict.REFUTED and self.observed_count is not None
                and self.expected_count is not None and self.observed_count > self.expected_count)

    def to_record(self) -> Dict[str, Any]:
        return {"verdict": self.verdict.value, "substrate": self.substrate, "reason": self.reason,
                "check_level": self.check_level.name.lower(), "observed_count": self.observed_count,
                "expected_count": self.expected_count, "absent": self.absent,
                "evidence": dict(self.evidence)}


def confirmed(substrate: str, reason: str, level: CheckLevel, **kw) -> EffectVerdict:
    return EffectVerdict(Verdict.CONFIRMED, substrate, reason, level, **kw)


def refuted(substrate: str, reason: str, level: CheckLevel, **kw) -> EffectVerdict:
    return EffectVerdict(Verdict.REFUTED, substrate, reason, level, **kw)


def indeterminate(substrate: str, reason: str, level: CheckLevel = CheckLevel.UNCHECKED,
                  **kw) -> EffectVerdict:
    return EffectVerdict(Verdict.INDETERMINATE, substrate,
                         reason + " -- cannot certify the effect; HALT (never assume success)",
                         level, **kw)


@runtime_checkable
class Effect(Protocol):
    """An effect contract: snapshot before the action, judge after it."""
    substrate: str

    def capture(self) -> Any: ...

    def verify(self, before: Any) -> EffectVerdict: ...


def digest(value: str | bytes) -> str:
    """One-way sha256 of a value (the only form a value takes in evidence)."""
    b = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(b).hexdigest()


def outcome_for(verdict: Optional[EffectVerdict], *, attempted: bool,
                min_level: CheckLevel = CheckLevel.SAME_SCREEN) -> Outcome:
    """Map one action's verdict to the closed outcome enum.

    not attempted                  -> HALTED_BEFORE_EFFECT
    attempted, no verifier          -> COMPLETED_UNVERIFIED
    CONFIRMED at >= min_level       -> VERIFIED
    CONFIRMED but weaker than that  -> COMPLETED_UNVERIFIED
    REFUTED / INDETERMINATE         -> RECONCILIATION_REQUIRED
    """
    if not attempted:
        return Outcome.HALTED_BEFORE_EFFECT
    if verdict is None:
        return Outcome.COMPLETED_UNVERIFIED
    if verdict.confirmed:
        return Outcome.VERIFIED if verdict.check_level >= min_level else Outcome.COMPLETED_UNVERIFIED
    return Outcome.RECONCILIATION_REQUIRED


def worst(outcomes) -> Outcome:
    """Aggregate several outcomes into one run outcome (worst wins)."""
    order = [Outcome.RECONCILIATION_REQUIRED, Outcome.COMPLETED_UNVERIFIED,
             Outcome.HALTED_BEFORE_EFFECT, Outcome.VERIFIED]
    seen = set(outcomes)
    if not seen:
        return Outcome.COMPLETED_UNVERIFIED
    return next(o for o in order if o in seen)
