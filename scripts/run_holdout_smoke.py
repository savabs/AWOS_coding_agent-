#!/usr/bin/env python3
"""Generate and run a three-task offline holdout smoke corpus.

This is a plumbing proof using deterministic callbacks, not a coding-agent
capability evaluation. It refuses to overwrite an existing corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
DEFAULT_ROOT = REPO / ".awos" / "holdout_private"

from scaffold.agent.evaluation_corpus import (  # noqa: E402
    HoldoutReportBundle,
    HoldoutCorpusManifest,
    sha256_file,
)
from scaffold.agent.evaluation_v1 import (  # noqa: E402
    AcceptanceCheck,
    EvaluationFixture,
    EvaluationRunner,
    aggregate_trials,
)


@dataclass(frozen=True)
class SmokeTask:
    task_id: str
    request: str
    source_files: Dict[str, str]
    test_file: str
    allowed_paths: List[str]
    fix: Callable[[Path], None]


def _fix_add(workspace: Path) -> None:
    (workspace / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")


def _fix_greet(workspace: Path) -> None:
    (workspace / "greet.py").write_text("def greet(name):\n    return f\"hello, {name}\"\n", encoding="utf-8")


def _fix_total(workspace: Path) -> None:
    (workspace / "aggregate.py").write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")


def _tasks() -> List[SmokeTask]:
    return [
        SmokeTask("smoke_add", "Fix add without changing the API.", {"calculator.py": "def add(a, b):\n    return a - b\n"}, "test_task.py", ["calculator.py"], _fix_add),
        SmokeTask("smoke_greet", "Fix greet while preserving its function name.", {"greet.py": "def greet(name):\n    return \"hello\"\n"}, "test_task.py", ["greet.py"], _fix_greet),
        SmokeTask("smoke_total", "Fix total to aggregate every value.", {"aggregate.py": "def total(values):\n    return max(values)\n"}, "test_task.py", ["aggregate.py"], _fix_total),
    ]


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _write_snapshot(root: Path, task: SmokeTask) -> tuple[Path, Path]:
    task_root = root / "tasks" / task.task_id
    repository = root / "repositories" / task.task_id
    task_root.mkdir(parents=True)
    repository.mkdir(parents=True)
    for relative, content in task.source_files.items():
        (repository / relative).write_text(content, encoding="utf-8")
    tests = {
        "smoke_add": "from calculator import add\n\ndef test_task():\n    assert add(2, 3) == 5\n",
        "smoke_greet": "from greet import greet\n\ndef test_task():\n    assert greet('AWOS') == 'hello, AWOS'\n",
        "smoke_total": "from aggregate import total\n\ndef test_task():\n    assert total([1, 2, 3]) == 6\n",
    }
    (repository / task.test_file).write_text(tests[task.task_id], encoding="utf-8")
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    task_json = {
        "task_id": task.task_id,
        "family": "smoke",
        "request": task.request,
        "repository_reference": f"local://repositories/{task.task_id}",
    }
    task_file = task_root / "task.json"
    task_file.write_text(json.dumps(task_json, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return task_file, repository


def _run_task(task: SmokeTask, repository: Path, candidate: bool):
    fixture = EvaluationFixture(
        task_id=task.task_id,
        family="smoke",
        repository_path=str(repository),
        request=task.request,
        acceptance=[AcceptanceCheck(command=["python3", "-m", "pytest", "-q"])],
        allowed_paths=task.allowed_paths,
    )

    def agent(_fixture, workspace, emit):
        emit("smoke_plan", {"candidate": candidate, "task_id": task.task_id})
        if candidate:
            task.fix(workspace)
        return {"success": candidate, "cost_usd": 0.0}

    return EvaluationRunner().run_trial(fixture, agent, trial_id="trial_1")


def _run(root: Path) -> int:
    if (root / "manifest.json").exists() or any((root / part).exists() for part in ("tasks", "repositories")):
        print("ERROR: smoke corpus target already contains data; refusing overwrite", file=sys.stderr)
        return 1
    for relative in ("tasks", "repositories", "reports/baseline", "reports/candidate", "comparisons"):
        (root / relative).mkdir(parents=True, exist_ok=False)
    tasks = _tasks()
    refs = []
    repositories = {}
    for task in tasks:
        task_file, repository = _write_snapshot(root, task)
        refs.append((task, task_file))
        repositories[task.task_id] = repository

    baseline_reports = [aggregate_trials([_run_task(task, repositories[task.task_id], False)]) for task, _ in refs]
    candidate_reports = [aggregate_trials([_run_task(task, repositories[task.task_id], True)]) for task, _ in refs]
    baseline_bundle = HoldoutReportBundle("awos-smoke-3", "offline-smoke-python", baseline_reports)
    candidate_bundle = HoldoutReportBundle("awos-smoke-3", "offline-smoke-python", candidate_reports)
    baseline_path = root / "reports" / "baseline" / "baseline.json"
    candidate_path = root / "reports" / "candidate" / "candidate.json"
    baseline_path.write_text(baseline_bundle.to_json() + "\n", encoding="utf-8")
    candidate_path.write_text(candidate_bundle.to_json() + "\n", encoding="utf-8")
    manifest = HoldoutCorpusManifest.from_dict({
        "schema_version": "holdout_corpus_v1",
        "corpus_id": "awos-smoke-3",
        "environment_identity": "offline-smoke-python",
        "split": "holdout",
        "tasks": [{
            "task_id": task.task_id,
            "family": "smoke",
            "external_reference": f"local://tasks/{task.task_id}/task.json",
            "fixture_sha256": sha256_file(str(task_file)),
            "repository_sha256": _hash_tree(repositories[task.task_id]),
        } for task, task_file in refs],
        "baseline": {"artifact_reference": "local://reports/baseline/baseline.json", "report_sha256": sha256_file(str(baseline_path))},
        "candidate": {"artifact_reference": "local://reports/candidate/candidate.json", "report_sha256": sha256_file(str(candidate_path))},
    })
    (root / "manifest.json").write_text(manifest.to_json() + "\n", encoding="utf-8")
    command = [sys.executable, str(REPO / "scripts" / "run_holdout_pipeline.py"), "--root", str(root)]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    print("SMOKE_ONLY deterministic callback proof; not agent capability evidence")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the three-task offline holdout smoke proof")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    try:
        return _run(args.root.expanduser().resolve())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
