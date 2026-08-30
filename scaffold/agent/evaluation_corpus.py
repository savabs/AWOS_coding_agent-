"""Fail-closed metadata contracts for private held-out evaluations.

This module deliberately stores references and digests, not private task contents.
It prevents public-fixture leakage and binds evaluation comparisons to immutable
fixture, repository, environment, and report identities.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .evaluation_v1 import EvaluationReport, compare_reports

CORPUS_SCHEMA_VERSION = "holdout_corpus_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_FIXTURE_MARKER = "tests/fixtures/evaluation_v1"
REPORT_BUNDLE_SCHEMA_VERSION = "holdout_report_bundle_v1"


class CorpusSchemaError(ValueError):
    """Raised when a holdout manifest is incomplete or unsafe."""


def _digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CorpusSchemaError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorpusSchemaError(f"{field} must not be empty")
    return value.strip()


def _external_reference(value: str) -> str:
    value = _nonempty(value, "external_reference")
    normalized = value.replace("\\", "/")
    if _PUBLIC_FIXTURE_MARKER in normalized:
        raise CorpusSchemaError("holdout reference must not point at public fixtures")
    if any(part == ".." for part in normalized.split("/")):
        raise CorpusSchemaError("holdout reference must not contain traversal")
    return value


@dataclass(frozen=True)
class HoldoutTaskRef:
    task_id: str
    family: str
    external_reference: str
    fixture_sha256: str
    repository_sha256: str

    def __post_init__(self) -> None:
        _nonempty(self.task_id, "task_id")
        _nonempty(self.family, "family")
        _external_reference(self.external_reference)
        _digest(self.fixture_sha256, "fixture_sha256")
        _digest(self.repository_sha256, "repository_sha256")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HoldoutTaskRef":
        if not isinstance(data, dict):
            raise CorpusSchemaError("holdout task reference must be an object")
        return cls(
            task_id=str(data.get("task_id", "")),
            family=str(data.get("family", "")),
            external_reference=str(data.get("external_reference", "")),
            fixture_sha256=str(data.get("fixture_sha256", "")),
            repository_sha256=str(data.get("repository_sha256", "")),
        )


@dataclass(frozen=True)
class ReportRef:
    artifact_reference: str
    report_sha256: str

    def __post_init__(self) -> None:
        _external_reference(self.artifact_reference)
        _digest(self.report_sha256, "report_sha256")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReportRef":
        if not isinstance(data, dict):
            raise CorpusSchemaError("report reference must be an object")
        return cls(
            artifact_reference=str(data.get("artifact_reference", "")),
            report_sha256=str(data.get("report_sha256", "")),
        )


@dataclass(frozen=True)
class HoldoutCorpusManifest:
    corpus_id: str
    environment_identity: str
    tasks: List[HoldoutTaskRef]
    baseline: ReportRef
    candidate: ReportRef
    split: str = "holdout"
    schema_version: str = CORPUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CORPUS_SCHEMA_VERSION:
            raise CorpusSchemaError(f"unsupported corpus schema: {self.schema_version!r}")
        _nonempty(self.corpus_id, "corpus_id")
        _nonempty(self.environment_identity, "environment_identity")
        if self.split != "holdout":
            raise CorpusSchemaError("corpus split must be 'holdout'")
        if not self.tasks:
            raise CorpusSchemaError("holdout corpus requires at least one task")
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise CorpusSchemaError("holdout task IDs must be unique")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HoldoutCorpusManifest":
        if not isinstance(data, dict):
            raise CorpusSchemaError("holdout corpus manifest must be an object")
        return cls(
            corpus_id=str(data.get("corpus_id", "")),
            environment_identity=str(data.get("environment_identity", "")),
            tasks=[HoldoutTaskRef.from_dict(item) for item in data.get("tasks", [])],
            baseline=ReportRef.from_dict(data.get("baseline", {})),
            candidate=ReportRef.from_dict(data.get("candidate", {})),
            split=str(data.get("split", "")),
            schema_version=str(data.get("schema_version", CORPUS_SCHEMA_VERSION)),
        )

    @classmethod
    def from_json(cls, value: str) -> "HoldoutCorpusManifest":
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CorpusSchemaError("holdout corpus JSON is invalid") from exc
        return cls.from_dict(data)


def sha256_file(path: str) -> str:
    """Return the SHA-256 digest of a local artifact for manifest creation."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class HoldoutReportBundle:
    """A complete set of evaluator-owned reports for one holdout corpus."""

    corpus_id: str
    environment_identity: str
    reports: List[EvaluationReport]
    schema_version: str = REPORT_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REPORT_BUNDLE_SCHEMA_VERSION:
            raise CorpusSchemaError(f"unsupported report bundle schema: {self.schema_version!r}")
        _nonempty(self.corpus_id, "corpus_id")
        _nonempty(self.environment_identity, "environment_identity")
        if not self.reports:
            raise CorpusSchemaError("report bundle requires at least one report")
        task_ids = [report.task_id for report in self.reports]
        if len(task_ids) != len(set(task_ids)):
            raise CorpusSchemaError("report bundle task IDs must be unique")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "environment_identity": self.environment_identity,
            "reports": [report.to_dict() for report in sorted(self.reports, key=lambda item: item.task_id)],
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HoldoutReportBundle":
        if not isinstance(data, dict):
            raise CorpusSchemaError("holdout report bundle must be an object")
        try:
            reports = [EvaluationReport.from_dict(item) for item in data.get("reports", [])]
        except (TypeError, KeyError, ValueError) as exc:
            raise CorpusSchemaError("holdout report bundle contains an invalid report") from exc
        return cls(
            corpus_id=str(data.get("corpus_id", "")),
            environment_identity=str(data.get("environment_identity", "")),
            reports=reports,
            schema_version=str(data.get("schema_version", REPORT_BUNDLE_SCHEMA_VERSION)),
        )

    @classmethod
    def from_json(cls, value: str) -> "HoldoutReportBundle":
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CorpusSchemaError("holdout report bundle JSON is invalid") from exc
        return cls.from_dict(data)


@dataclass(frozen=True)
class HoldoutTaskComparison:
    task_id: str
    baseline_pass_at_1: float
    candidate_pass_at_1: float
    delta_pass_at_1: float
    correctness_regression: bool
    safety_regression: bool

    @property
    def regressed(self) -> bool:
        return self.correctness_regression or self.safety_regression

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self) | {"regressed": self.regressed}


@dataclass(frozen=True)
class HoldoutComparison:
    corpus_id: str
    environment_identity: str
    baseline_report_sha256: str
    candidate_report_sha256: str
    task_comparisons: List[HoldoutTaskComparison]

    @property
    def correctness_regression(self) -> bool:
        return any(item.correctness_regression for item in self.task_comparisons)

    @property
    def safety_regression(self) -> bool:
        return any(item.safety_regression for item in self.task_comparisons)

    @property
    def regressed(self) -> bool:
        return self.correctness_regression or self.safety_regression

    def to_dict(self) -> Dict[str, Any]:
        return {
            "baseline_report_sha256": self.baseline_report_sha256,
            "candidate_report_sha256": self.candidate_report_sha256,
            "corpus_id": self.corpus_id,
            "correctness_regression": self.correctness_regression,
            "environment_identity": self.environment_identity,
            "regressed": self.regressed,
            "safety_regression": self.safety_regression,
            "task_comparisons": [item.to_dict() for item in sorted(self.task_comparisons, key=lambda value: value.task_id)],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _verified_bundle(
    manifest: HoldoutCorpusManifest,
    reference: ReportRef,
    artifact_path: str,
    label: str,
) -> HoldoutReportBundle:
    try:
        actual_digest = sha256_file(artifact_path)
    except OSError as exc:
        raise CorpusSchemaError(f"{label} report artifact cannot be read") from exc
    if actual_digest != reference.report_sha256:
        raise CorpusSchemaError(f"{label} report artifact digest does not match manifest")
    try:
        bundle = HoldoutReportBundle.from_json(open(artifact_path, encoding="utf-8").read())
    except OSError as exc:
        raise CorpusSchemaError(f"{label} report artifact cannot be read") from exc
    if bundle.corpus_id != manifest.corpus_id:
        raise CorpusSchemaError(f"{label} report corpus_id does not match manifest")
    if bundle.environment_identity != manifest.environment_identity:
        raise CorpusSchemaError(f"{label} report environment_identity does not match manifest")
    expected = {task.task_id for task in manifest.tasks}
    actual = {report.task_id for report in bundle.reports}
    if actual != expected:
        raise CorpusSchemaError(f"{label} report task coverage does not match manifest")
    return bundle


def compare_holdout_report_bundles(
    manifest: HoldoutCorpusManifest,
    baseline_path: str,
    candidate_path: str,
) -> HoldoutComparison:
    """Verify and compare two local, digest-bound bundles without executing agents."""
    baseline_digest = sha256_file(baseline_path)
    candidate_digest = sha256_file(candidate_path)
    if baseline_digest == candidate_digest:
        raise CorpusSchemaError("baseline and candidate report artifacts must differ")
    baseline = _verified_bundle(manifest, manifest.baseline, baseline_path, "baseline")
    candidate = _verified_bundle(manifest, manifest.candidate, candidate_path, "candidate")
    baseline_by_id = {report.task_id: report for report in baseline.reports}
    candidate_by_id = {report.task_id: report for report in candidate.reports}
    comparisons: List[HoldoutTaskComparison] = []
    for task_id in sorted(baseline_by_id):
        result = compare_reports(baseline_by_id[task_id], candidate_by_id[task_id])
        comparisons.append(HoldoutTaskComparison(
            task_id=task_id,
            baseline_pass_at_1=result.baseline_pass_at_1,
            candidate_pass_at_1=result.candidate_pass_at_1,
            delta_pass_at_1=result.delta_pass_at_1,
            correctness_regression=result.correctness_regression,
            safety_regression=result.safety_regression,
        ))
    return HoldoutComparison(
        corpus_id=manifest.corpus_id,
        environment_identity=manifest.environment_identity,
        baseline_report_sha256=baseline_digest,
        candidate_report_sha256=candidate_digest,
        task_comparisons=comparisons,
    )
