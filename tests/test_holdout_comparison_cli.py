from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scaffold.agent.evaluation_corpus import (
    HoldoutCorpusManifest,
    HoldoutReportBundle,
    sha256_file,
)
from scaffold.agent.evaluation_v1 import EvaluationReport, TrialResult, TrialStatus

SCRIPT = Path("scripts/verify_holdout_comparison.py")
SHA = "a" * 64


def _report(task_id: str, passed: bool) -> EvaluationReport:
    trial = TrialResult(
        task_id=task_id,
        trial_id="trial_1",
        status=TrialStatus.PASSED if passed else TrialStatus.FAILED,
        acceptance_passed=passed,
        safety_passed=True,
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
        safety_failure_rate=0.0,
        mean_latency_sec=1.0,
        mean_cost_usd=0.01,
        mean_retries=0.0,
        trials=[trial],
    )


def _inputs(tmp_path: Path, *, baseline_passed=False, candidate_passed=True):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    for path, passed in ((baseline_path, baseline_passed), (candidate_path, candidate_passed)):
        path.write_text(
            HoldoutReportBundle(
                corpus_id="cli-corpus",
                environment_identity="test-environment",
                reports=[_report("task-1", passed)],
            ).to_json(),
            encoding="utf-8",
        )
    manifest = HoldoutCorpusManifest.from_dict({
        "corpus_id": "cli-corpus",
        "environment_identity": "test-environment",
        "tasks": [{
            "task_id": "task-1",
            "family": "basic_edit",
            "external_reference": "vault://task-1",
            "fixture_sha256": SHA,
            "repository_sha256": "b" * 64,
        }],
        "baseline": {"artifact_reference": "vault://baseline", "report_sha256": sha256_file(str(baseline_path))},
        "candidate": {"artifact_reference": "vault://candidate", "report_sha256": sha256_file(str(candidate_path))},
        "split": "holdout",
    })
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    return manifest_path, baseline_path, candidate_path


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
    )


def test_cli_success_prints_and_writes_deterministic_json(tmp_path):
    manifest, baseline, candidate = _inputs(tmp_path)
    output_path = tmp_path / "comparison.json"
    result = _run("--manifest", manifest, "--baseline", baseline, "--candidate", candidate, "--output", output_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(output_path.read_text(encoding="utf-8"))
    assert json.loads(result.stdout)["regressed"] is False


def test_cli_returns_nonzero_for_regression(tmp_path):
    manifest, baseline, candidate = _inputs(tmp_path, baseline_passed=True, candidate_passed=False)
    result = _run("--manifest", manifest, "--baseline", baseline, "--candidate", candidate)
    assert result.returncode == 1
    assert json.loads(result.stdout)["correctness_regression"] is True


def test_cli_rejects_tampered_candidate(tmp_path):
    manifest, baseline, candidate = _inputs(tmp_path)
    candidate.write_text(candidate.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = _run("--manifest", manifest, "--baseline", baseline, "--candidate", candidate)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "digest" in result.stderr


def test_cli_requires_all_artifact_arguments():
    result = _run("--manifest", "manifest.json")
    assert result.returncode == 2
    assert "--baseline" in result.stderr


def test_cli_does_not_overwrite_input_artifact(tmp_path):
    manifest, baseline, candidate = _inputs(tmp_path)
    result = _run("--manifest", manifest, "--baseline", baseline, "--candidate", candidate, "--output", candidate)
    assert result.returncode == 1
    assert "output path" in result.stderr
