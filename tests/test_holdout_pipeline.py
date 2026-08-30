from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scaffold.agent.evaluation_corpus import HoldoutReportBundle, sha256_file
from scaffold.agent.evaluation_v1 import EvaluationReport, TrialResult, TrialStatus

SCRIPT = Path("scripts/run_holdout_pipeline.py")


def _report(passed: bool, safe: bool = True) -> EvaluationReport:
    trial = TrialResult(
        task_id="task-1",
        trial_id="trial-1",
        status=TrialStatus.PASSED if passed else TrialStatus.FAILED,
        acceptance_passed=passed,
        safety_passed=safe,
        truthful_report=True,
        diagnostic_reward=1.0 if passed else 0.0,
        latency_sec=1.0,
        cost_usd=0.01,
    )
    return EvaluationReport(
        task_id="task-1",
        trial_count=1,
        pass_at_1=1.0 if passed else 0.0,
        all_trials_passed=passed,
        safety_failure_rate=0.0 if safe else 1.0,
        mean_latency_sec=1.0,
        mean_cost_usd=0.01,
        mean_retries=0.0,
        trials=[trial],
    )


def _workspace(tmp_path: Path, *, baseline_passed: bool, candidate_passed: bool) -> Path:
    root = tmp_path / "private"
    (root / "reports" / "baseline").mkdir(parents=True)
    (root / "reports" / "candidate").mkdir(parents=True)
    (root / "comparisons").mkdir()
    for name, passed in (("baseline", baseline_passed), ("candidate", candidate_passed)):
        bundle = HoldoutReportBundle(
            corpus_id="pipeline-corpus",
            environment_identity="pipeline-env",
            reports=[_report(passed)],
        )
        (root / "reports" / name / f"{name}.json").write_text(bundle.to_json(), encoding="utf-8")
    manifest = {
        "schema_version": "holdout_corpus_v1",
        "corpus_id": "pipeline-corpus",
        "environment_identity": "pipeline-env",
        "split": "holdout",
        "tasks": [{
            "task_id": "task-1",
            "family": "basic_edit",
            "external_reference": "vault://task-1",
            "fixture_sha256": "a" * 64,
            "repository_sha256": "b" * 64,
        }],
        "baseline": {
            "artifact_reference": "vault://baseline",
            "report_sha256": sha256_file(str(root / "reports" / "baseline" / "baseline.json")),
        },
        "candidate": {
            "artifact_reference": "vault://candidate",
            "report_sha256": sha256_file(str(root / "reports" / "candidate" / "candidate.json")),
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return root


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_pipeline_success_writes_comparison(tmp_path):
    root = _workspace(tmp_path, baseline_passed=False, candidate_passed=True)
    result = _run("--root", root)
    assert result.returncode == 0, result.stderr
    comparison = json.loads((root / "comparisons" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["regressed"] is False


def test_pipeline_returns_nonzero_for_regression(tmp_path):
    root = _workspace(tmp_path, baseline_passed=True, candidate_passed=False)
    result = _run("--root", root)
    assert result.returncode == 1
    assert json.loads((root / "comparisons" / "comparison.json").read_text(encoding="utf-8"))["regressed"] is True


def test_pipeline_rejects_path_outside_private_root(tmp_path):
    root = _workspace(tmp_path, baseline_passed=False, candidate_passed=True)
    result = _run("--root", root, "--candidate", tmp_path / "public.json")
    assert result.returncode == 1
    assert "inside private holdout root" in result.stderr


def test_pipeline_rejects_missing_artifact(tmp_path):
    root = _workspace(tmp_path, baseline_passed=False, candidate_passed=True)
    (root / "reports" / "candidate" / "candidate.json").unlink()
    result = _run("--root", root)
    assert result.returncode == 1
    assert "candidate" in result.stderr


def test_pipeline_propagates_tamper_failure(tmp_path):
    root = _workspace(tmp_path, baseline_passed=False, candidate_passed=True)
    candidate = root / "reports" / "candidate" / "candidate.json"
    candidate.write_text(candidate.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = _run("--root", root)
    assert result.returncode == 1
    assert "digest" in result.stderr
