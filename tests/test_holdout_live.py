from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from scaffold.agent.evaluation_corpus import HoldoutReportBundle, sha256_file
from scaffold.agent.evaluation_v1 import EvaluationReport, TrialResult, TrialStatus
from scripts.run_holdout_live import (
    LiveHoldoutError,
    ORACLE_TOKEN,
    _tree_digest,
    _finalize_candidate_manifest,
    _load_manifest,
    load_single_fixture,
    main,
    validate_live_authorization,
)


def _write_private_case(root: Path, *, task_count: int = 1, content: str = "value = 1\n") -> Path:
    (root / "tasks" / "task_one").mkdir(parents=True)
    (root / "repositories" / "task_one").mkdir(parents=True)
    (root / "oracle").mkdir(parents=True)
    (root / "oracle" / "grader.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "repositories" / "task_one" / "module.py").write_text(content, encoding="utf-8")
    fixture = {
        "fixture_version": "evaluation_v1",
        "task_id": "task_one",
        "family": "tiny",
        "repository_path": "unused-by-loader",
        "request": "Make the tiny task correct.",
        "acceptance": [{"name": "smoke", "command": [sys.executable, "-c", "print('ok')"]}],
        "allowed_paths": ["module.py"],
        "forbidden_paths": [".git", ".env"],
    }
    fixture_path = root / "tasks" / "task_one" / "fixture.json"
    fixture_path.write_text(json.dumps(fixture, sort_keys=True) + "\n", encoding="utf-8")
    task = {
        "task_id": "task_one",
        "family": "tiny",
        "external_reference": "local://tasks/task_one/fixture.json",
        "fixture_sha256": sha256_file(str(fixture_path)),
        "repository_sha256": _tree_digest(root / "repositories" / "task_one"),
    }
    tasks = [task]
    if task_count == 2:
        tasks.append({**task, "task_id": "task_two", "external_reference": "local://tasks/task_one/fixture.json"})
    baseline_path = root / "reports" / "baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    trial = TrialResult(
        task_id="task_one",
        trial_id="trial_1",
        status=TrialStatus.FAILED,
        acceptance_passed=False,
        safety_passed=True,
        truthful_report=True,
        diagnostic_reward=0.0,
        latency_sec=1.0,
        cost_usd=0.01,
    )
    baseline_path.write_text(
        HoldoutReportBundle(
            corpus_id="tiny-private",
            environment_identity="test-python",
            reports=[EvaluationReport(
                task_id="task_one",
                trial_count=1,
                pass_at_1=0.0,
                all_trials_passed=False,
                safety_failure_rate=0.0,
                mean_latency_sec=1.0,
                mean_cost_usd=0.01,
                mean_retries=0.0,
                trials=[trial],
            )],
        ).to_json(),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "holdout_corpus_v1",
        "corpus_id": "tiny-private",
        "environment_identity": "test-python",
        "split": "holdout",
        "tasks": tasks,
        "baseline": {"artifact_reference": "local://reports/baseline.json", "report_sha256": sha256_file(str(baseline_path))},
        "candidate": {"artifact_reference": "local://reports/candidate.json", "report_sha256": "0" * 64},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def test_load_single_fixture_verifies_private_digests(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)

    fixture = load_single_fixture(tmp_path, manifest)

    assert fixture.task_id == "task_one"
    assert fixture.repository_path == str((tmp_path / "repositories" / "task_one").resolve())


def test_binds_private_oracle_token_to_selected_root(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    fixture_path = tmp_path / "tasks" / "task_one" / "fixture.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    data["acceptance"][0]["command"] = [sys.executable, ORACLE_TOKEN + "/grader.py"]
    fixture_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["tasks"][0]["fixture_sha256"] = sha256_file(str(fixture_path))
    manifest.write_text(json.dumps(manifest_data, sort_keys=True) + "\n", encoding="utf-8")

    fixture = load_single_fixture(tmp_path, manifest)

    assert str(tmp_path / "oracle" / "grader.py") in fixture.acceptance[0].command
    assert str(tmp_path / "_clean") not in fixture.acceptance[0].command


def test_rejects_oracle_directory_as_command_argument(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    fixture_path = tmp_path / "tasks" / "task_one" / "fixture.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    data["acceptance"][0]["command"] = [sys.executable, ORACLE_TOKEN]
    fixture_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["tasks"][0]["fixture_sha256"] = sha256_file(str(fixture_path))
    manifest.write_text(json.dumps(manifest_data, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(LiveHoldoutError, match="oracle reference must identify a file"):
        load_single_fixture(tmp_path, manifest)


def test_rejects_stale_absolute_holdout_path(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    fixture_path = tmp_path / "tasks" / "task_one" / "fixture.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    data["acceptance"][0]["command"] = [
        sys.executable,
        "/tmp/awos/holdout_private/old/grader/test.py",
    ]
    fixture_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["tasks"][0]["fixture_sha256"] = sha256_file(str(fixture_path))
    manifest.write_text(json.dumps(manifest_data, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(LiveHoldoutError, match="acceptance path must remain inside"):
        load_single_fixture(tmp_path, manifest)


def test_rejects_more_than_one_task(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path, task_count=2)

    with pytest.raises(LiveHoldoutError, match="exactly one task"):
        load_single_fixture(tmp_path, manifest)


def test_rejects_repository_over_size_limit(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path, content="x" * 128)

    with pytest.raises(LiveHoldoutError, match="exceeds limit"):
        load_single_fixture(tmp_path, manifest, max_repository_bytes=32)


def test_allows_internal_repository_symlink(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    repository = tmp_path / "repositories" / "task_one"
    (repository / "internal_link").symlink_to("module.py")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["tasks"][0]["repository_sha256"] = _tree_digest(repository)
    manifest.write_text(json.dumps(manifest_data, sort_keys=True) + "\n", encoding="utf-8")

    assert load_single_fixture(tmp_path, manifest).task_id == "task_one"


def test_rejects_repository_symlink_escape(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    repository = tmp_path / "repositories" / "task_one"
    (repository / "outside_link").symlink_to("/etc/passwd")

    with pytest.raises(LiveHoldoutError, match="escapes workspace"):
        load_single_fixture(tmp_path, manifest)



def test_dry_run_validates_without_provider_call(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _write_private_case(tmp_path)

    assert main(["--root", str(tmp_path), "--manifest", str(manifest), "--dry-run"]) == 0
    assert '"dry_run": true' in capsys.readouterr().out


def test_dry_run_rejects_relative_acceptance_executable(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    fixture_path = tmp_path / "tasks" / "task_one" / "fixture.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    data["acceptance"][0]["command"][0] = "venv/bin/python"
    fixture_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["tasks"][0]["fixture_sha256"] = sha256_file(str(fixture_path))
    manifest.write_text(json.dumps(manifest_data, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["--root", str(tmp_path), "--manifest", str(manifest), "--dry-run"]) == 1


def test_dry_run_rejects_missing_acceptance_executable(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    fixture_path = tmp_path / "tasks" / "task_one" / "fixture.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    data["acceptance"][0]["command"][0] = str(tmp_path / "missing-python")
    fixture_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["tasks"][0]["fixture_sha256"] = sha256_file(str(fixture_path))
    manifest.write_text(json.dumps(manifest_data, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["--root", str(tmp_path), "--manifest", str(manifest), "--dry-run"]) == 1


def test_dry_run_rejects_baseline_corpus_mismatch(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    baseline_path = tmp_path / "reports" / "baseline.json"
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    data["corpus_id"] = "different-trial"
    baseline_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["--root", str(tmp_path), "--manifest", str(manifest), "--dry-run"]) == 1


def test_dry_run_rejects_stale_candidate_digest(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["candidate"]["report_sha256"] = "f" * 64
    manifest.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["--root", str(tmp_path), "--manifest", str(manifest), "--dry-run"]) == 1


def test_finalizing_candidate_manifest_binds_new_report_digest(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    candidate = tmp_path / "reports" / "candidate.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("candidate report\n", encoding="utf-8")

    _finalize_candidate_manifest(manifest, _load_manifest(manifest), candidate)

    finalized = json.loads(manifest.read_text(encoding="utf-8"))
    assert finalized["candidate"]["report_sha256"] == sha256_file(str(candidate))


def test_live_authorization_requires_explicit_confirmation() -> None:
    args = Namespace(
        confirm_live=False,
        provider="provider",
        model="model",
        credentials_env="API_KEY",
        budget_usd=1.0,
        wall_timeout_sec=60,
    )

    with pytest.raises(LiveHoldoutError, match="confirm-live"):
        validate_live_authorization(args)


def test_public_fixture_reference_is_rejected(tmp_path: Path) -> None:
    manifest = _write_private_case(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["tasks"][0]["external_reference"] = "local://tests/fixtures/evaluation_v1/fixture.json"
    manifest.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(LiveHoldoutError, match="invalid holdout manifest"):
        load_single_fixture(tmp_path, manifest)
