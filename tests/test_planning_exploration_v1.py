"""Deterministic planning/exploration contract tests.

Governing task: tasks/active/planning_exploration_v1.html
Governing spec: docs/specs/planning_exploration_v1_spec.html

These tests do not call a model. They verify the repository evidence contract
that a planner will receive.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_goal_references_keep_explicit_paths_and_symbols_separate() -> None:
    from scaffold.agent.exploration_phase import normalize_goal_references

    refs = normalize_goal_references(
        "Update `src/service.py` and call ServiceRegistry.register_handler() for the auth flow"
    )

    assert refs["explicit_paths"] == ["src/service.py"]
    assert "ServiceRegistry" in refs["explicit_symbols"]
    assert "register_handler" in refs["explicit_symbols"]
    assert "auth" in [term.lower() for term in refs["keywords"]]
    assert "src/service.py" not in refs["keywords"]


def test_exploration_snapshot_is_bounded_and_replayable(tmp_path: Path) -> None:
    from scaffold.agent.exploration_phase import build_exploration_snapshot

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        "class ServiceRegistry:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text(
        "def test_service():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (tmp_path / ".awos").mkdir()
    (tmp_path / ".awos" / "ignored.py").write_text("ignored = True\n", encoding="utf-8")

    first = build_exploration_snapshot(
        "Update `src/service.py` and call ServiceRegistry", str(tmp_path), max_grep_hits=4
    )
    second = build_exploration_snapshot(
        "Update `src/service.py` and call ServiceRegistry", str(tmp_path), max_grep_hits=4
    )

    assert first == second
    assert first["snapshot_id"]
    assert first["explicit_paths"] == ["src/service.py"]
    assert "src/service.py" in first["candidate_files"]
    assert "tests/test_service.py" in first["test_files"]
    assert "pyproject.toml" in first["config_files"]
    assert all(".awos" not in path for path in first["candidate_files"])
    assert first["bounds"]["max_grep_hits"] == 4
    assert first["cost"]["grep_hits"] <= 4
    assert first["negative_evidence"]


def test_snapshot_fields_are_preserved_in_planner_context(tmp_path: Path) -> None:
    from scaffold.agent.exploration_phase import build_exploration_snapshot, enrich_codebase_context

    (tmp_path / "service.py").write_text("class ServiceRegistry:\n    pass\n", encoding="utf-8")
    snapshot = build_exploration_snapshot("Update `service.py` ServiceRegistry", str(tmp_path))
    context = enrich_codebase_context({}, snapshot)

    assert context["exploration_snapshot_id"] == snapshot["snapshot_id"]
    assert context["exploration_explicit_paths"] == ["service.py"]
    assert "ServiceRegistry" in context["exploration_explicit_symbols"]
    assert context["exploration_cost"] == snapshot["cost"]


def test_plan_validation_rejects_unresolved_and_non_atomic_tasks(tmp_path: Path) -> None:
    from scaffold.agent.orchestrator import validate_plan_contract

    (tmp_path / "service.py").write_text("class ServiceRegistry:\n    pass\n", encoding="utf-8")
    snapshot = {
        "snapshot_id": "abc123",
        "candidate_files": ["service.py"],
        "hit_files": ["service.py"],
    }
    valid, errors = validate_plan_contract(
        [{
            "task_id": 1,
            "file": "service.py",
            "action": "Update the registry",
            "acceptance_check": "python -m pytest",
            "evidence_ref": "snapshot:abc123",
        }], str(tmp_path), snapshot, strict=True
    )
    assert valid is True
    assert errors == []

    invalid, errors = validate_plan_contract(
        [{
            "task_id": 1,
            "file": "missing.py",
            "files": ["service.py", "other.py"],
            "action": "Update multiple files",
        }], str(tmp_path), snapshot, strict=True
    )
    assert invalid is False
    assert any("does not exist" in error for error in errors)
    assert any("one primary file" in error for error in errors)
    assert any("acceptance_check" in error for error in errors)


def test_offline_fixture_eval_scores_grounded_exploration(tmp_path: Path) -> None:
    manifest_path = Path(__file__).parent / "fixtures" / "planning_exploration_v1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    from scaffold.agent.exploration_phase import build_exploration_snapshot

    for fixture in manifest["fixtures"]:
        for relative in fixture["target_files"] + fixture["dependency_files"]:
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def placeholder():\n    return True\n", encoding="utf-8")
        snapshot = build_exploration_snapshot(fixture["goal"], str(tmp_path))
        candidates = set(snapshot["candidate_files"])
        targets = set(fixture["target_files"])
        dependencies = set(fixture["dependency_files"])
        target_recall = len(targets & candidates) / len(targets)
        dependency_recall = len(dependencies & candidates) / len(dependencies) if dependencies else 1.0
        forbidden_hits = set(fixture["forbidden_files"]) & candidates

        assert target_recall == 1.0, fixture["id"]
        assert dependency_recall == 1.0, fixture["id"]
        assert not forbidden_hits, fixture["id"]
        assert snapshot["snapshot_id"]
        assert snapshot["bounds"]["max_files"] > 0
        assert snapshot["cost"]["files_listed"] >= len(candidates)


def test_invalid_root_has_explicit_negative_evidence(tmp_path: Path) -> None:
    from scaffold.agent.exploration_phase import build_exploration_snapshot

    snapshot = build_exploration_snapshot("fix service", str(tmp_path / "missing"))

    assert snapshot["candidate_files"] == []
    assert snapshot["grep_hits"] == []
    assert snapshot["negative_evidence"]
    assert "invalid project root" in snapshot["negative_evidence"][0]


def test_nested_awos_workspace_keeps_repository_files_grounded(tmp_path: Path) -> None:
    from scaffold.agent.exploration_phase import build_exploration_snapshot

    repository = tmp_path / ".awos" / "holdout" / "repository"
    target = repository / "src" / "sqlfluff" / "rules" / "L060.py"
    target.parent.mkdir(parents=True)
    target.write_text("class Rule_L060: pass\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")

    snapshot = build_exploration_snapshot(
        "Fix `src/sqlfluff/rules/L060.py` and preserve tests",
        str(repository),
    )

    assert "src/sqlfluff/rules/L060.py" in snapshot["candidate_files"]
    assert "pyproject.toml" in snapshot["config_files"]


def test_explicit_target_is_grounded_when_outside_bounded_sample(tmp_path: Path) -> None:
    from scaffold.agent.exploration_phase import build_exploration_snapshot
    from scaffold.agent.orchestrator import validate_plan_contract

    repository = tmp_path / ".awos" / "holdout" / "repository"
    target = repository / "src" / "sqlfluff" / "rules" / "L060.py"
    target.parent.mkdir(parents=True)
    target.write_text("class Rule_L060: pass\n", encoding="utf-8")
    for index in range(45):
        filler = repository / "docs" / f"file_{index:02d}.py"
        filler.parent.mkdir(parents=True, exist_ok=True)
        filler.write_text("value = True\n", encoding="utf-8")

    snapshot = build_exploration_snapshot(
        "Fix `src/sqlfluff/rules/L060.py`", str(repository), max_files=4
    )
    valid, errors = validate_plan_contract(
        [{
            "task_id": 1,
            "file": "src/sqlfluff/rules/L060.py",
            "action": "Fix the rule",
            "acceptance_check": "pytest -q",
            "evidence_ref": f"snapshot:{snapshot['snapshot_id']}",
        }], str(repository), snapshot, strict=True
    )

    assert "src/sqlfluff/rules/L060.py" in snapshot["candidate_files"]
    assert valid is True
    assert errors == []


def test_inner_awos_directory_remains_excluded(tmp_path: Path) -> None:
    from scaffold.agent.exploration_phase import build_exploration_snapshot

    repository = tmp_path / ".awos" / "holdout" / "repository"
    visible = repository / "src" / "visible.py"
    hidden = repository / ".awos" / "hidden.py"
    visible.parent.mkdir(parents=True)
    hidden.parent.mkdir(parents=True)
    visible.write_text("visible = True\n", encoding="utf-8")
    hidden.write_text("hidden = True\n", encoding="utf-8")

    snapshot = build_exploration_snapshot("Inspect visible.py", str(repository))

    assert "src/visible.py" in snapshot["candidate_files"]
    assert all(".awos" not in path for path in snapshot["candidate_files"])


def test_yaml_test_fixture_is_grounded_for_strict_plan_validation(tmp_path: Path) -> None:
    from scaffold.agent.exploration_phase import build_exploration_snapshot
    from scaffold.agent.orchestrator import validate_plan_contract

    fixture = tmp_path / "test" / "fixtures" / "rules" / "std_rule_cases" / "L060.yml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("rule: L060\n", encoding="utf-8")
    snapshot = build_exploration_snapshot("Fix L060", str(tmp_path))

    valid, errors = validate_plan_contract(
        [{
            "task_id": 2,
            "file": "test/fixtures/rules/std_rule_cases/L060.yml",
            "action": "Update expected fixture",
            "acceptance_check": "pytest -q",
            "evidence_ref": f"snapshot:{snapshot['snapshot_id']}",
        }], str(tmp_path), snapshot, strict=True
    )

    assert valid is True
    assert errors == []
