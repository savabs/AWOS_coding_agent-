from __future__ import annotations

import json

import pytest

from scaffold.agent.evaluation_corpus import (
    CORPUS_SCHEMA_VERSION,
    CorpusSchemaError,
    HoldoutCorpusManifest,
    HoldoutReportBundle,
    HoldoutTaskRef,
    compare_holdout_report_bundles,
    sha256_file,
)
from scaffold.agent.evaluation_v1 import EvaluationReport, TrialResult, TrialStatus

SHA = "a" * 64


def _task(**overrides):
    data = {
        "task_id": "private_001",
        "family": "multi_file",
        "external_reference": "vault://holdout/private_001",
        "fixture_sha256": SHA,
        "repository_sha256": "b" * 64,
    }
    data.update(overrides)
    return HoldoutTaskRef.from_dict(data)


def _manifest(**overrides):
    data = {
        "corpus_id": "awos-private-2026-07",
        "environment_identity": "python3.12-pytest8.3-linux-x86_64",
        "tasks": [_task().__dict__],
        "baseline": {"artifact_reference": "vault://reports/baseline.json", "report_sha256": SHA},
        "candidate": {"artifact_reference": "vault://reports/candidate.json", "report_sha256": "c" * 64},
        "split": "holdout",
        "schema_version": CORPUS_SCHEMA_VERSION,
    }
    data.update(overrides)
    return HoldoutCorpusManifest.from_dict(data)


def test_manifest_round_trip_is_deterministic():
    manifest = _manifest()
    encoded = manifest.to_json()
    assert encoded == manifest.to_json()
    assert HoldoutCorpusManifest.from_json(encoded) == manifest


def test_manifest_rejects_public_fixture_reference():
    with pytest.raises(CorpusSchemaError, match="public fixtures"):
        _task(external_reference="tests/fixtures/evaluation_v1/basic_edit/fixture.json")


def test_manifest_rejects_traversal_reference():
    with pytest.raises(CorpusSchemaError, match="traversal"):
        _task(external_reference="vault://holdout/../public/task.json")


def test_manifest_requires_holdout_split():
    with pytest.raises(CorpusSchemaError, match="split"):
        _manifest(split="public")


def test_manifest_requires_hashes():
    with pytest.raises(CorpusSchemaError, match="SHA-256"):
        _task(fixture_sha256="not-a-digest")


def test_manifest_requires_environment_identity():
    with pytest.raises(CorpusSchemaError, match="environment_identity"):
        _manifest(environment_identity="")


def test_manifest_requires_immutable_report_references():
    with pytest.raises(CorpusSchemaError, match="SHA-256"):
        _manifest(candidate={"artifact_reference": "latest", "report_sha256": ""})


def test_manifest_rejects_duplicate_task_ids():
    duplicate = _task(task_id="private_001").__dict__
    with pytest.raises(CorpusSchemaError, match="unique"):
        _manifest(tasks=[_task().__dict__, duplicate])


def test_example_contract_has_no_public_fixture_path():
    data = json.loads(open("tests/fixtures/evaluation_v1/holdout_manifest.example.json", encoding="utf-8").read())
    manifest = HoldoutCorpusManifest.from_dict(data)
    assert manifest.split == "holdout"
    assert "tests/fixtures/evaluation_v1" not in manifest.to_json()


def _report(task_id: str, *, passed: bool, safe: bool = True) -> EvaluationReport:
    trial = TrialResult(
        task_id=task_id,
        trial_id="trial_1",
        status=TrialStatus.PASSED if passed else TrialStatus.FAILED,
        acceptance_passed=passed,
        safety_passed=safe,
        truthful_report=True,
        diagnostic_reward=1.0 if passed else 0.0,
        latency_sec=1.0,
        cost_usd=0.01,
    )
    return EvaluationReport(
        task_id=task_id,
        trial_count=1,
        pass_at_1=1.0 if passed else 0.0,
        all_trials_passed=passed,
        safety_failure_rate=0.0 if safe else 1.0,
        mean_latency_sec=1.0,
        mean_cost_usd=0.01,
        mean_retries=0.0,
        trials=[trial],
    )


def _report_files(tmp_path, *, candidate_passed=True, candidate_environment=None):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline = HoldoutReportBundle(
        corpus_id="awos-private-2026-07",
        environment_identity="python3.12-pytest8.3-linux-x86_64",
        reports=[_report("private_001", passed=False)],
    )
    candidate = HoldoutReportBundle(
        corpus_id="awos-private-2026-07",
        environment_identity=candidate_environment or "python3.12-pytest8.3-linux-x86_64",
        reports=[_report("private_001", passed=candidate_passed)],
    )
    baseline_path.write_text(baseline.to_json(), encoding="utf-8")
    candidate_path.write_text(candidate.to_json(), encoding="utf-8")
    manifest = _manifest(
        baseline={"artifact_reference": "vault://reports/baseline.json", "report_sha256": sha256_file(str(baseline_path))},
        candidate={"artifact_reference": "vault://reports/candidate.json", "report_sha256": sha256_file(str(candidate_path))},
    )
    return manifest, baseline_path, candidate_path


def test_report_bundle_round_trip_is_deterministic(tmp_path):
    manifest, baseline_path, _ = _report_files(tmp_path)
    bundle = HoldoutReportBundle.from_json(baseline_path.read_text(encoding="utf-8"))
    assert bundle.to_json() == HoldoutReportBundle.from_json(bundle.to_json()).to_json()
    assert bundle.corpus_id == manifest.corpus_id


def test_comparison_reports_improvement_without_regression(tmp_path):
    manifest, baseline_path, candidate_path = _report_files(tmp_path)
    result = compare_holdout_report_bundles(manifest, str(baseline_path), str(candidate_path))
    assert result.regressed is False
    assert result.task_comparisons[0].delta_pass_at_1 == 1.0


def test_comparison_rejects_tampered_artifact(tmp_path):
    manifest, baseline_path, candidate_path = _report_files(tmp_path)
    candidate_path.write_text(candidate_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CorpusSchemaError, match="digest"):
        compare_holdout_report_bundles(manifest, str(baseline_path), str(candidate_path))


def test_comparison_rejects_identity_mismatch(tmp_path):
    manifest, baseline_path, candidate_path = _report_files(
        tmp_path, candidate_environment="different-environment"
    )
    with pytest.raises(CorpusSchemaError, match="environment_identity"):
        compare_holdout_report_bundles(manifest, str(baseline_path), str(candidate_path))


def test_comparison_rejects_incomplete_task_coverage(tmp_path):
    manifest, baseline_path, candidate_path = _report_files(tmp_path)
    extra = HoldoutReportBundle(
        corpus_id=manifest.corpus_id,
        environment_identity=manifest.environment_identity,
        reports=[_report("unregistered", passed=True)],
    )
    candidate_path.write_text(extra.to_json(), encoding="utf-8")
    manifest_data = manifest.to_dict()
    manifest_data["candidate"]["report_sha256"] = sha256_file(str(candidate_path))
    manifest = HoldoutCorpusManifest.from_dict(manifest_data)
    with pytest.raises(CorpusSchemaError, match="coverage"):
        compare_holdout_report_bundles(manifest, str(baseline_path), str(candidate_path))
