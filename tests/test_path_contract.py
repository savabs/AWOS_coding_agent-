"""TDD contract tests for safe planner workspace paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from scaffold.agent.core.path_contract import (
    PathContractError,
    canonical_workspace_path,
    require_grounded_path,
)


def test_canonical_workspace_path_returns_root_relative_posix_path(tmp_path: Path) -> None:
    target = tmp_path / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")

    assert canonical_workspace_path(target, tmp_path) == "src/module.py"
    assert canonical_workspace_path("src\\module.py", tmp_path) == "src/module.py"


def test_canonical_workspace_path_rejects_traversal_and_outside_paths(tmp_path: Path) -> None:
    with pytest.raises(PathContractError, match="outside workspace"):
        canonical_workspace_path("../secret.txt", tmp_path)

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    with pytest.raises(PathContractError, match="outside workspace"):
        canonical_workspace_path(outside, tmp_path)


def test_canonical_workspace_path_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    link.symlink_to(outside)

    with pytest.raises(PathContractError, match="outside workspace"):
        canonical_workspace_path(link, tmp_path)


def test_require_grounded_path_accepts_snapshot_evidence(tmp_path: Path) -> None:
    target = tmp_path / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    snapshot = {
        "candidate_files": ["src/module.py"],
        "hit_files": [],
        "test_files": [],
    }

    assert require_grounded_path(target, tmp_path, snapshot) == "src/module.py"


def test_require_grounded_path_rejects_existing_but_unevidenced_file(tmp_path: Path) -> None:
    target = tmp_path / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(PathContractError, match="not grounded"):
        require_grounded_path(
            target,
            tmp_path,
            {"candidate_files": [], "hit_files": [], "test_files": []},
        )


def test_require_grounded_path_allows_explicit_create_target(tmp_path: Path) -> None:
    assert require_grounded_path(
        "src/new_module.py",
        tmp_path,
        {"candidate_files": [], "hit_files": [], "test_files": []},
        allow_create=True,
    ) == "src/new_module.py"


def test_require_grounded_path_rejects_unsafe_create_target(tmp_path: Path) -> None:
    with pytest.raises(PathContractError, match="outside workspace"):
        require_grounded_path("../new_module.py", tmp_path, {}, allow_create=True)


# The contract must not silently accept a path with a different normalization.
def test_require_grounded_path_uses_canonical_snapshot_membership(tmp_path: Path) -> None:
    target = tmp_path / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")

    assert require_grounded_path(
        "src\\module.py",
        tmp_path,
        {"candidate_files": ["src/module.py"]},
    ) == "src/module.py"

    with pytest.raises(PathContractError, match="not grounded"):
        require_grounded_path(
            "src/module.py",
            tmp_path,
            {"candidate_files": ["src/other.py"]},
        )


def test_canonical_workspace_path_rejects_empty_and_nul_paths(tmp_path: Path) -> None:
    with pytest.raises(PathContractError, match="non-empty"):
        canonical_workspace_path("", tmp_path)
    with pytest.raises(PathContractError, match="invalid"):
        canonical_workspace_path("src\x00module.py", tmp_path)


def test_strict_plan_validation_uses_safe_grounding_for_absolute_paths(tmp_path: Path) -> None:
    from scaffold.agent.orchestrator import validate_plan_contract

    target = tmp_path / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    snapshot = {
        "snapshot_id": "snap-1",
        "candidate_files": ["src/module.py"],
        "hit_files": [],
        "test_files": [],
    }
    valid, errors = validate_plan_contract(
        [{
            "task_id": 1,
            "file": str(target),
            "action": "Update module",
            "acceptance_check": "pytest -q",
            "evidence_ref": "snapshot:snap-1",
        }],
        str(tmp_path),
        snapshot,
        strict=True,
    )

    assert valid is True
    assert errors == []


def test_strict_plan_validation_rejects_existing_unevidenced_absolute_path(tmp_path: Path) -> None:
    from scaffold.agent.orchestrator import validate_plan_contract

    target = tmp_path / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    valid, errors = validate_plan_contract(
        [{
            "task_id": 1,
            "file": str(target),
            "action": "Update module",
            "acceptance_check": "pytest -q",
            "evidence_ref": "snapshot:snap-1",
        }],
        str(tmp_path),
        {"snapshot_id": "snap-1", "candidate_files": []},
        strict=True,
    )

    assert valid is False
    assert any("not grounded" in error for error in errors)
