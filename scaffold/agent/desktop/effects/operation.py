"""Idempotent operation state machine: never blind-retry a write.

The design follows the operation model in OpenAdapt's effect kit and
BLUEPRINT.md section 2 (payload hash; ``uncertain`` -> verify before any
retry). AWOS implementation, no OpenAdapt code.

    ledger = OperationLedger(".awos/operations.jsonl")    # or OperationLedger() in memory
    res = run_once(ledger, "create_key", {"project": "storefront-web", "scope": "read",
                                          "label": "awos-storefront-web"},
                   execute=lambda: create_on_dashboard(),
                   verify=lambda: key_effect_verdict())   # "is it there now?"

State per op id (= sha256 of kind + canonical payload):

    (none) --record STARTED--> execute()
        raises BeforeEffect -> HALTED            outcome HALTED_BEFORE_EFFECT
        raises anything else -> UNCERTAIN, then verify():
                 CONFIRMED -> SUCCEEDED          outcome VERIFIED
                 otherwise -> UNCERTAIN          outcome RECONCILIATION_REQUIRED
        returns -> verify():
                 CONFIRMED -> SUCCEEDED          outcome VERIFIED
                 no verify -> UNVERIFIED         outcome COMPLETED_UNVERIFIED
                 otherwise -> RECONCILE          outcome RECONCILIATION_REQUIRED

A second call with the same payload:
    SUCCEEDED / UNVERIFIED       -> not executed again (returns the record)
    HALTED                       -> runs again (nothing happened last time)
    STARTED / UNCERTAIN / RECONCILE  (a crash mid-write, or an unproven one)
                                 -> verify() FIRST:
                 CONFIRMED                         -> SUCCEEDED, not executed
                 REFUTED with absent=True          -> provably not done: execute
                 anything else / no verify         -> RECONCILIATION_REQUIRED, not executed

Payloads must carry handles (``{{secret:name}}``), never secret values: a
payload with a recognisable key format is refused (``ValueError``), so the
ledger file never stores a credential.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .outcome import EffectVerdict, Outcome


class OpState(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    UNVERIFIED = "completed_unverified"
    HALTED = "halted_before_effect"
    UNCERTAIN = "uncertain"
    RECONCILE = "reconciliation_required"


DONE = {OpState.SUCCEEDED, OpState.UNVERIFIED}
VERIFY_FIRST = {OpState.STARTED, OpState.UNCERTAIN, OpState.RECONCILE}


class BeforeEffect(Exception):
    """Raise from an executor to say: stopped, and provably nothing changed."""


def _canonical(kind: str, payload: Any) -> str:
    return json.dumps({"kind": kind, "payload": payload}, sort_keys=True, separators=(",", ":"),
                      default=str)


def payload_hash(kind: str, payload: Any) -> str:
    raw = _canonical(kind, payload)
    try:
        from ...secret_scan import key_spans
    except ImportError:          # pragma: no cover - package layout
        key_spans = None
    if key_spans is not None and key_spans(raw, placeholders=False):
        raise ValueError("operation payload contains a credential; pass a {{secret:name}} handle")
    return "op_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass
class OpResult:
    op_id: str
    outcome: Outcome
    state: OpState
    executed: bool
    reason: str
    verdict: Optional[EffectVerdict] = None
    result: Any = None

    def to_record(self) -> Dict[str, Any]:
        return {"op_id": self.op_id, "outcome": self.outcome.value, "state": self.state.value,
                "executed": self.executed, "reason": self.reason,
                "verdict": self.verdict.to_record() if self.verdict else None}


class OperationLedger:
    """Latest state per op id; optional append-only JSONL persistence (resume)."""

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path else None
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                    self._state[rec["op_id"]] = rec
                except (ValueError, KeyError):
                    continue

    def get(self, op_id: str) -> Optional[OpState]:
        rec = self._state.get(op_id)
        return OpState(rec["state"]) if rec else None

    def record(self, op_id: str, kind: str, state: OpState, reason: str = "") -> None:
        rec = {"ts": round(time.time(), 3), "op_id": op_id, "kind": kind, "state": state.value,
               "reason": reason[:300]}
        with self._lock:
            self._state[op_id] = rec
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, sort_keys=True) + "\n")


def _safe_verify(verify: Optional[Callable[[], EffectVerdict]]) -> Optional[EffectVerdict]:
    if verify is None:
        return None
    try:
        return verify()
    except Exception as ex:
        from .outcome import indeterminate
        return indeterminate("operation", f"verifier raised {type(ex).__name__}")


def run_once(ledger: OperationLedger, kind: str, payload: Any, *, execute: Callable[[], Any],
             verify: Optional[Callable[[], EffectVerdict]] = None) -> OpResult:
    op_id = payload_hash(kind, payload)
    prior = ledger.get(op_id)

    if prior in DONE:
        out = Outcome.VERIFIED if prior is OpState.SUCCEEDED else Outcome.COMPLETED_UNVERIFIED
        return OpResult(op_id, out, prior, False, "already done; not executed again")

    if prior in VERIFY_FIRST:
        v = _safe_verify(verify)
        if v is not None and v.confirmed:
            ledger.record(op_id, kind, OpState.SUCCEEDED, "verified after an uncertain attempt")
            return OpResult(op_id, Outcome.VERIFIED, OpState.SUCCEEDED, False,
                            "previous attempt verified present; not executed again", v)
        if v is None or not v.absent:
            ledger.record(op_id, kind, OpState.RECONCILE, "previous attempt unproven; retry refused")
            return OpResult(op_id, Outcome.RECONCILIATION_REQUIRED, OpState.RECONCILE, False,
                            "previous attempt may have taken effect and cannot be proven either way; "
                            "a human must reconcile (no blind retry)", v)
        # provably absent -> safe to execute

    ledger.record(op_id, kind, OpState.STARTED)
    try:
        result = execute()
    except BeforeEffect as ex:
        ledger.record(op_id, kind, OpState.HALTED, str(ex))
        return OpResult(op_id, Outcome.HALTED_BEFORE_EFFECT, OpState.HALTED, True, str(ex) or "halted")
    except Exception as ex:
        ledger.record(op_id, kind, OpState.UNCERTAIN, f"executor raised {type(ex).__name__}")
        v = _safe_verify(verify)
        if v is not None and v.confirmed:
            ledger.record(op_id, kind, OpState.SUCCEEDED, "executor raised but the effect is verified")
            return OpResult(op_id, Outcome.VERIFIED, OpState.SUCCEEDED, True,
                            "executor raised, effect verified present", v)
        return OpResult(op_id, Outcome.RECONCILIATION_REQUIRED, OpState.UNCERTAIN, True,
                        f"executor raised {type(ex).__name__}; effect unproven", v)

    v = _safe_verify(verify)
    if v is None:
        ledger.record(op_id, kind, OpState.UNVERIFIED)
        return OpResult(op_id, Outcome.COMPLETED_UNVERIFIED, OpState.UNVERIFIED, True,
                        "ran; no verifier", None, result)
    if v.confirmed:
        ledger.record(op_id, kind, OpState.SUCCEEDED)
        return OpResult(op_id, Outcome.VERIFIED, OpState.SUCCEEDED, True, v.reason, v, result)
    ledger.record(op_id, kind, OpState.RECONCILE, v.reason)
    return OpResult(op_id, Outcome.RECONCILIATION_REQUIRED, OpState.RECONCILE, True, v.reason, v, result)
