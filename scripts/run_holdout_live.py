#!/usr/bin/env python3
"""Run one explicitly authorized AWOS holdout trial.

This command is intentionally small and fail-closed. It validates one private
fixture and repository snapshot, enforces a repository-size limit, then delegates
correctness and safety to evaluation_v1. Live execution is never the default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Iterable

# Load .env early
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.agent.evaluation_corpus import (  # noqa: E402
    CorpusSchemaError,
    HoldoutCorpusManifest,
    HoldoutReportBundle,
    ReportRef,
    sha256_file,
)
from scaffold.agent.evaluation_v1 import (  # noqa: E402
    EvaluationFixture,
    EvaluationRunner,
    aggregate_trials,
    make_orchestrator_agent,
)

DEFAULT_ROOT = REPO / ".awos" / "holdout_private"
DEFAULT_MAX_REPOSITORY_BYTES = 64 * 1024 * 1024
ORACLE_TOKEN = "__AWOS_HOLDOUT_ORACLE__"
UNSET_DIGEST = "0" * 64


class LiveHoldoutError(ValueError):
    """Raised when the one-task live-run contract is not satisfied."""


def _inside(root: Path, candidate: Path, label: str) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LiveHoldoutError(f"{label} must remain inside {root}") from exc
    return candidate


def _local_reference(root: Path, reference: str, label: str) -> Path:
    if not reference.startswith("local://"):
        raise LiveHoldoutError(f"{label} must use a local:// reference")
    relative = reference[len("local://") :]
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise LiveHoldoutError(f"{label} contains an unsafe relative path")
    return _inside(root, root / relative, label)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _tree_size(root: Path) -> int:
    root = root.resolve()
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            target = path.resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise LiveHoldoutError(f"repository symlink escapes workspace: {path}") from exc
            if not target.exists():
                raise LiveHoldoutError(f"repository contains a broken symlink: {path}")
            continue
        if path.is_file():
            total += path.stat().st_size
    return total


def _load_manifest(path: Path) -> HoldoutCorpusManifest:
    try:
        return HoldoutCorpusManifest.from_json(path.read_text(encoding="utf-8"))
    except (OSError, CorpusSchemaError) as exc:
        raise LiveHoldoutError(f"invalid holdout manifest: {path}") from exc


def load_single_fixture(
    root: Path,
    manifest_path: Path,
    *,
    max_repository_bytes: int = DEFAULT_MAX_REPOSITORY_BYTES,
    task_id: str | None = None,
) -> EvaluationFixture:
    """Load exactly one digest-bound private fixture and repository."""
    root = root.expanduser().resolve()
    manifest_path = _inside(root, manifest_path, "manifest")
    manifest = _load_manifest(manifest_path)
    if len(manifest.tasks) != 1:
        raise LiveHoldoutError(f"small live evaluation requires exactly one task, got {len(manifest.tasks)}")
    task_ref = manifest.tasks[0]
    if task_id is not None and task_ref.task_id != task_id:
        raise LiveHoldoutError(f"requested task does not match manifest: {task_id!r}")

    fixture_path = _local_reference(root, task_ref.external_reference, "fixture reference")
    if sha256_file(str(fixture_path)) != task_ref.fixture_sha256:
        raise LiveHoldoutError("fixture SHA-256 does not match manifest")
    try:
        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture = EvaluationFixture.from_dict(fixture_data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LiveHoldoutError("private fixture is invalid") from exc
    if fixture.task_id != task_ref.task_id or fixture.family != task_ref.family:
        raise LiveHoldoutError("fixture identity does not match manifest")

    repository = _inside(root, root / "repositories" / task_ref.task_id, "repository")
    if not repository.is_dir():
        raise LiveHoldoutError(f"repository snapshot is missing: {repository}")
    if _tree_size(repository) > max_repository_bytes:
        raise LiveHoldoutError(
            f"repository exceeds limit: {_tree_size(repository)} bytes > {max_repository_bytes} bytes"
        )
    if _tree_digest(repository) != task_ref.repository_sha256:
        raise LiveHoldoutError("repository SHA-256 does not match manifest")

    data = fixture.to_dict()
    data["repository_path"] = str(repository)
    bound_fixture = EvaluationFixture.from_dict(data)
    bound_data = bound_fixture.to_dict()
    uses_oracle_token = any(
        ORACLE_TOKEN in token
        for item in bound_data["acceptance"]
        for token in item["command"]
    )
    if uses_oracle_token:
        oracle = _inside(root, root / "oracle", "oracle")
        if not oracle.is_dir():
            raise LiveHoldoutError(f"hidden oracle directory is missing: {oracle}")
        for acceptance in bound_data["acceptance"]:
            command = acceptance["command"]
            acceptance["command"] = [token.replace(ORACLE_TOKEN, str(oracle)) for token in command]
    if any(ORACLE_TOKEN in token for item in bound_data["acceptance"] for token in item["command"]):
        raise LiveHoldoutError("acceptance command contains an unresolved oracle token")
    oracle_root = _inside(root, root / "oracle", "oracle")
    for item in bound_data["acceptance"]:
        for token in item["command"][1:]:
            candidate = Path(token)
            if candidate.is_absolute() and candidate == oracle_root:
                raise LiveHoldoutError(
                    "acceptance oracle reference must identify a file below the oracle directory"
                )
    for item in bound_data["acceptance"]:
        executable = Path(item["command"][0])
        if not executable.is_absolute():
            raise LiveHoldoutError(
                f"acceptance executable must be an absolute path: {executable}"
            )
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise LiveHoldoutError(
                f"acceptance executable is missing or not executable: {executable}"
            )
        for token in item["command"][1:]:
            path = Path(token)
            if path.is_absolute() and "holdout_private" in path.parts:
                _inside(root, path, "acceptance path")
    return EvaluationFixture.from_dict(bound_data)


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and temporary_name.exists():
            temporary_name.unlink()


def _finalize_candidate_manifest(
    manifest_path: Path,
    manifest: HoldoutCorpusManifest,
    candidate_path: Path,
) -> None:
    candidate_digest = sha256_file(str(candidate_path))
    finalized = HoldoutCorpusManifest(
        corpus_id=manifest.corpus_id,
        environment_identity=manifest.environment_identity,
        tasks=manifest.tasks,
        baseline=manifest.baseline,
        candidate=ReportRef(
            artifact_reference=manifest.candidate.artifact_reference,
            report_sha256=candidate_digest,
        ),
        split=manifest.split,
        schema_version=manifest.schema_version,
    )
    _write_atomic(manifest_path, finalized.to_json() + "\n")


def _validate_baseline_bundle(root: Path, manifest: HoldoutCorpusManifest) -> None:
    """Verify the immutable baseline belongs to this exact holdout manifest."""
    baseline_path = _local_reference(root, manifest.baseline.artifact_reference, "baseline report reference")
    if not baseline_path.is_file():
        raise LiveHoldoutError(f"baseline report is missing: {baseline_path}")
    if sha256_file(str(baseline_path)) != manifest.baseline.report_sha256:
        raise LiveHoldoutError("baseline report SHA-256 does not match manifest")
    try:
        bundle = HoldoutReportBundle.from_json(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise LiveHoldoutError("baseline report bundle is invalid") from exc
    if bundle.corpus_id != manifest.corpus_id:
        raise LiveHoldoutError("baseline report corpus_id does not match manifest")
    if bundle.environment_identity != manifest.environment_identity:
        raise LiveHoldoutError("baseline report environment_identity does not match manifest")
    expected_tasks = {task.task_id for task in manifest.tasks}
    actual_tasks = {report.task_id for report in bundle.reports}
    if actual_tasks != expected_tasks:
        raise LiveHoldoutError("baseline report task coverage does not match manifest")


def _required_env_names(value: str) -> list[str]:
    names = [item.strip() for item in value.split(",") if item.strip()]
    if not names or any(not name.replace("_", "").isalnum() for name in names):
        raise LiveHoldoutError("--credentials-env must contain comma-separated environment variable names")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise LiveHoldoutError(f"required credential environment variables are missing: {', '.join(missing)}")
    return names


def validate_live_authorization(args: argparse.Namespace) -> None:
    if not args.confirm_live:
        raise LiveHoldoutError("live execution requires --confirm-live")
    if not args.external_timeout_enforced:
        raise LiveHoldoutError("live execution requires an externally enforced timeout wrapper")
    if not args.provider.strip() or not args.model.strip():
        raise LiveHoldoutError("live execution requires --provider and --model")
    if args.budget_usd <= 0:
        raise LiveHoldoutError("--budget-usd must be positive")
    if args.wall_timeout_sec <= 0:
        raise LiveHoldoutError("--wall-timeout-sec must be positive")
    _required_env_names(args.credentials_env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one authorized AWOS private holdout trial")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--max-repository-bytes", type=int, default=DEFAULT_MAX_REPOSITORY_BYTES)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--credentials-env", default="")
    parser.add_argument("--budget-usd", type=float, default=0.0)
    parser.add_argument("--wall-timeout-sec", type=int, default=0)
    parser.add_argument("--network-policy", choices=("disabled", "restricted", "allowed"), default="disabled")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--external-timeout-enforced", action="store_true", help="operator confirms an outer timeout wrapper is active")
    parser.add_argument("--dry-run", action="store_true", help="validate the one-task input without calling a provider")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.max_repository_bytes <= 0:
            raise LiveHoldoutError("--max-repository-bytes must be positive")
        root = args.root.expanduser().resolve()
        manifest = (args.manifest or root / "manifest.json").expanduser().resolve()
        holdout_manifest = _load_manifest(manifest)
        _validate_baseline_bundle(root, holdout_manifest)
        if holdout_manifest.candidate.report_sha256 != UNSET_DIGEST:
            raise LiveHoldoutError("candidate report digest must be unset before a fresh trial")
        expected_report_path = _local_reference(
            root,
            holdout_manifest.candidate.artifact_reference,
            "candidate report reference",
        )
        fixture = load_single_fixture(
            root,
            manifest,
            max_repository_bytes=args.max_repository_bytes,
            task_id=args.task_id,
        )
        report_path = (args.report or expected_report_path).expanduser().resolve()
        _inside(root, report_path, "report")
        if report_path != expected_report_path:
            raise LiveHoldoutError("candidate report path must match the manifest reference")
        if report_path.exists():
            raise LiveHoldoutError(f"refusing to overwrite existing report: {report_path}")
        if args.dry_run:
            print(json.dumps({"task_id": fixture.task_id, "family": fixture.family, "dry_run": True}, sort_keys=True))
            return 0
        validate_live_authorization(args)

        from scaffold.agent.orchestrator import Orchestrator

        def _wall_timeout_handler(signum, frame):
            raise TimeoutError(f"holdout wall timeout exceeded: {args.wall_timeout_sec}s")

        previous_handler = signal.signal(signal.SIGALRM, _wall_timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, float(args.wall_timeout_sec))
        try:
            result = EvaluationRunner().run_trial(
                fixture,
                make_orchestrator_agent(
                    Orchestrator,
                    benchmark_mode=True,
                    budget_usd=args.budget_usd,
                    use_native_planner=True,
                ),
                trial_id="trial_1",
            )
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        report = aggregate_trials([result])
        report_bundle = HoldoutReportBundle(
            corpus_id=holdout_manifest.corpus_id,
            environment_identity=holdout_manifest.environment_identity,
            reports=[report],
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_bundle.to_json() + "\n", encoding="utf-8")
        _finalize_candidate_manifest(manifest, holdout_manifest, report_path)
        print(json.dumps({
            "task_id": fixture.task_id,
            "provider": args.provider,
            "model": args.model,
            "budget_usd": args.budget_usd,
            "wall_timeout_sec": args.wall_timeout_sec,
            "network_policy": args.network_policy,
            "report": str(report_path),
            "pass_at_1": report.pass_at_1,
            "safety_failure_rate": report.safety_failure_rate,
        }, sort_keys=True))
        return 0 if report.pass_at_1 == 1.0 else 1
    except (LiveHoldoutError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
