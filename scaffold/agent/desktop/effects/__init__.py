"""Effect verifiers (WP7): did the side effect really happen, exactly once, with
nothing else damaged? Ported in part from OpenAdapt Flow's effect kit (MIT; see
LICENSE-OpenAdapt and docs/third_party/wp7.md).

Import-light (stdlib only). Public API:

    outcome      Verdict, CheckLevel, Outcome, EffectVerdict, outcome_for, worst, digest
    file_field   FieldEquals (.env / TOML field set exactly once + collateral-loss
                 check), write_field / render_field (minimal-edit writer),
                 FieldWriteRefused
    file_arrival FileArrival
    readback     UIReadback, observation_text (+ readback_values.compare_value)
    compensation reconcile_or_escalate, FileRestore, Compensator, ReconciliationTask
    operation    OperationLedger, run_once, BeforeEffect, OpState, payload_hash
"""
from .compensation import (CompensationOutcome, CompensationResult, Compensator, FileRestore,
                           ReconciliationTask, build_reconciliation_task, reconcile_or_escalate)
from .file_arrival import FileArrival
from .file_field import FieldEquals, FieldWriteRefused, WriteResult, render_field, snapshot, write_field
from .operation import BeforeEffect, OperationLedger, OpResult, OpState, payload_hash, run_once
from .outcome import (CheckLevel, Effect, EffectVerdict, Outcome, Verdict, digest, outcome_for,
                      worst)
from .readback import UIReadback, observation_text
from .readback_values import compare_value

__all__ = [
    "BeforeEffect", "CheckLevel", "CompensationOutcome", "CompensationResult", "Compensator",
    "Effect", "EffectVerdict", "FieldEquals", "FieldWriteRefused", "FileArrival", "FileRestore",
    "OpResult", "OpState", "OperationLedger", "Outcome", "ReconciliationTask", "UIReadback",
    "Verdict", "WriteResult", "build_reconciliation_task", "compare_value", "digest",
    "observation_text", "outcome_for", "payload_hash", "reconcile_or_escalate", "render_field",
    "run_once", "snapshot", "worst", "write_field",
]
